"""스테이지 리포트 — 실행할 때마다 스테이지별로 '사람이 읽는 한 장' 을 자동으로 남긴다.

왜 두는가: 중간 산출이 parquet 로만 있으면 검수하려면 노트북을 열어야 하고, 상류 변경이 하류를
조용히 틀어 놓아도(귀속률 90%→74% 같은) 다음 단계 결과를 보고서야 알게 된다. 리포트는
"지표를 고정 양식으로 매 실행 찍고, 직전 실행과 비교하고, 선언한 불변식에 걸리면 ⚠ 를 세운다".
정지점 보고는 이 파일을 그대로 쓴다.

산출: outputs/<run_id>/reports/<stage>.md (+ <stage>.report.json, <stage>_hist.png, index.md)
호출: run.py 가 각 스테이지 run() 직후 write_stage_report() 를 부른다. 스테이지 코드는 아무것도
      안 해도 ①~⑥ 의 자동 부분이 나온다. 스테이지가 '사람이 봐야 할 숫자' 를 더 넣고 싶으면:

    from src.common.report import add_metrics, add_table, add_samples, note, warn
    add_metrics(cfg, STAGE, parse_ok_rate=0.98, dropped=37)        # ③ 지표 표에 추가 (⑤ 비교·불변식 대상)
    add_table(cfg, STAGE, "탈락 사유", drop_reasons_df)              # ③ 에 소형 표 추가
    add_samples(cfg, STAGE, out, cols=["id", "problem", "solution"])  # ④ 표본 열 지정 (생략 시 자동)
    note(cfg, STAGE, "ko 요약은 2차 패스에서 채움")                     # ⑥ 메모
    warn(cfg, STAGE, "no_match 큐 41건 — 승인 필요")                   # ⑥ ⚠

불변식: schemas/<stage>.out.yaml 의 `invariants:` 에 선언 (metric 은 자동 지표명 또는 add_metrics 이름)
    invariants:
      - {metric: rows_out, min: 1000}
      - {metric: parse_ok_rate, min: 0.95}
      - {metric: attribution_rate, min: 0.85, max_rel_delta: 0.10}   # 직전 실행 대비 ±10% 초과 시 ⚠
자동 지표명: rows_in, rows_out, rows_delta, rows_delta_pct, cols_out, null_rate_max, llm_calls,
            llm_input_tokens, llm_output_tokens, elapsed_s

설정(configs: report:): enabled(true) · sample_n(5) · hist_max(3) · compare_previous(true) · max_cols(40)
재생성: python -m src.common.report s3_extract [--run-id 261026_101500] [-c configs/exp01.yaml]
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import traceback
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from .config import path_of, root_of, stage_params, stages
from .io import TABLE_EXTS, read_table, run_dir, write_json
from .schema import SchemaError, load_schema, validate

# (run_id, stage) → 스테이지가 add_* 로 넣은 내용. 디스크(<stage>.extras.json)에도 즉시 저장해
# 스테이지가 중간에 죽어도 그때까지의 지표는 리포트에 남는다.
_EXTRAS: dict[tuple[str, str], dict] = {}
_EMPTY = {"metrics": {}, "tables": {}, "samples": None, "figures": [], "notes": [], "warnings": []}

_AUTO_METRICS = ("rows_in", "rows_out", "rows_delta", "rows_delta_pct", "cols_out", "null_rate_max",
                 "llm_calls", "llm_input_tokens", "llm_output_tokens", "elapsed_s")


# ================================================================ 설정·경로
def settings(cfg: dict) -> dict:
    s = dict(cfg.get("report") or {})
    s.setdefault("enabled", True)
    s.setdefault("sample_n", 5)
    s.setdefault("hist_max", 3)
    s.setdefault("compare_previous", True)
    s.setdefault("max_cols", 40)
    s.setdefault("cat_max", 3)      # 범주형 분포 표 최대 개수
    s.setdefault("cat_card", 12)    # 범주형으로 볼 최대 고유값 수
    return s


def reports_dir(cfg: dict, run_id: str | None = None) -> Path:
    rid = run_id or cfg.get("run_id", "adhoc")
    d = path_of(cfg, "outputs") / rid / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ================================================================ 스테이지용 API
def _bucket(cfg: dict, stage: str) -> dict:
    key = (str(cfg.get("run_id", "adhoc")), stage)
    if key not in _EXTRAS:
        f = (path_of(cfg, "outputs") / str(cfg.get("run_id", "adhoc")) / "reports" / f"{stage}.extras.json")
        if f.exists():  # 같은 run_id 로 재진입(--run-id 재사용)한 경우
            try:
                _EXTRAS[key] = json.loads(f.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                _EXTRAS[key] = json.loads(json.dumps(_EMPTY))
        else:
            _EXTRAS[key] = json.loads(json.dumps(_EMPTY))
    return _EXTRAS[key]


def _flush(cfg: dict, stage: str) -> None:
    if not settings(cfg).get("enabled", True):  # --no-report / report.enabled=false 면 디스크에 아무것도 만들지 않는다
        return
    write_json(_bucket(cfg, stage), reports_dir(cfg) / f"{stage}.extras.json")


def add_metrics(cfg: dict, stage: str, **metrics) -> None:
    """③ 지표 표에 올릴 숫자(또는 짧은 문자열). 숫자는 ⑤ 직전 대비 비교·불변식 검사 대상이 된다."""
    b = _bucket(cfg, stage)
    for k, v in metrics.items():
        b["metrics"][k] = _py(v)
    _flush(cfg, stage)


def add_table(cfg: dict, stage: str, name: str, df: pd.DataFrame, max_rows: int = 20) -> None:
    """③ 에 들어갈 소형 표(탈락 사유별 건수, 유형별 분포 등). 큰 표는 넣지 않는다 — 산출 파일이 따로 있다."""
    b = _bucket(cfg, stage)
    b["tables"][name] = _records(df.head(max_rows))
    _flush(cfg, stage)


def add_samples(cfg: dict, stage: str, df: pd.DataFrame, cols: list[str] | None = None,
                n: int | None = None) -> None:
    """④ 표본을 스테이지가 직접 지정. 생략하면 산출 파일 첫 표에서 seed 고정 무작위 추출."""
    s = settings(cfg)
    n = int(n or s["sample_n"])
    seed = int((cfg.get("project") or {}).get("seed", 42))
    cols = [c for c in (cols or list(df.columns)[:6]) if c in df.columns]
    smp = df[cols].sample(n=min(n, len(df)), random_state=seed) if len(df) else df[cols]
    b = _bucket(cfg, stage)
    b["samples"] = {"cols": cols, "rows": _records(smp), "n": len(smp), "seed": seed}
    _flush(cfg, stage)


def add_figure(cfg: dict, stage: str, path: str | Path, caption: str = "") -> None:
    """③ 에 그림 추가. reports/ 바깥 경로면 상대경로로 링크한다."""
    b = _bucket(cfg, stage)
    p = Path(path)
    try:
        rel = p.resolve().relative_to(reports_dir(cfg).resolve())
    except ValueError:
        rel = Path("..") / p.resolve().relative_to(run_dir(cfg).resolve()) if _under(p, run_dir(cfg)) else p
    b["figures"].append({"path": str(rel).replace("\\", "/"), "caption": caption})
    _flush(cfg, stage)


def note(cfg: dict, stage: str, text: str) -> None:
    """⑥ 메모(결정·보류 사항). ⚠ 는 아니다."""
    _bucket(cfg, stage)["notes"].append(str(text))
    _flush(cfg, stage)


def warn(cfg: dict, stage: str, text: str) -> None:
    """⑥ ⚠ — 사람이 보고 결정해야 하는 것(승인 대기 행, 임계 근접 등)."""
    _bucket(cfg, stage)["warnings"].append(str(text))
    _flush(cfg, stage)


# ================================================================ run.py 가 부르는 진입점
def safe_write(cfg: dict, stage: str, artifact: Path | None, *, seconds: float | None = None,
               status: str = "ok", error: str | None = None) -> Path | None:
    """리포트 실패가 파이프라인을 죽이지 않도록 감싼 진입점. 실패하면 경고만 남긴다."""
    if not settings(cfg).get("enabled", True):
        return None
    try:
        return write_stage_report(cfg, stage, artifact, seconds=seconds, status=status, error=error)
    except Exception as e:  # noqa: BLE001
        warnings.warn(f"[report] {stage} 리포트 생성 실패: {e!r}\n{traceback.format_exc(limit=2)}", stacklevel=2)
        return None


def write_stage_report(cfg: dict, stage: str, artifact: Path | None, *, seconds: float | None = None,
                       status: str = "ok", error: str | None = None) -> Path:
    s = settings(cfg)
    rid = str(cfg.get("run_id", "adhoc"))
    rdir = reports_dir(cfg)
    extras = _bucket(cfg, stage)
    params = stage_params(cfg, stage)
    ts = _dt.datetime.now().isoformat(timespec="seconds")

    # ── 산출·입력 적재 ───────────────────────────────────────────────────
    # report 스테이지(artifact == outputs/<run_id>/)는 표가 아니라 '보고물 목록' 으로 요약한다
    is_run_dir = artifact is not None and Path(artifact).is_dir() and Path(artifact).resolve() == run_dir(cfg).resolve()
    tables = _load_tables(artifact) if (artifact is not None and not is_run_dir) else {}
    main_df = next(iter(tables.values()), None)
    deliverables = _list_deliverables(artifact) if (artifact is not None and not tables and Path(artifact).is_dir()) else []
    src = _input_path(cfg, stage)
    rows_in = _count_rows(src) if src else None

    # ── 자동 지표 ─────────────────────────────────────────────────────────
    auto: dict = {}
    if rows_in is not None:
        auto["rows_in"] = rows_in
    if main_df is not None:
        auto["rows_out"] = int(len(main_df))
        auto["cols_out"] = int(main_df.shape[1])
        if rows_in is not None:
            auto["rows_delta"] = int(len(main_df)) - rows_in
            auto["rows_delta_pct"] = round(100.0 * auto["rows_delta"] / rows_in, 2) if rows_in else None
        if len(main_df):
            nr = main_df.isna().mean()
            auto["null_rate_max"] = round(float(nr.max()), 4)
            auto["null_rate_max_col"] = str(nr.idxmax())
    cost = _stage_cost(cfg, rid, stage)
    if cost["calls"]:
        auto.update({"llm_calls": cost["calls"], "llm_input_tokens": cost["input_tokens"],
                     "llm_output_tokens": cost["output_tokens"]})
    if seconds is not None:
        auto["elapsed_s"] = round(float(seconds), 1)
    metrics = {**auto, **extras.get("metrics", {})}

    # ── 스키마·불변식·직전 실행 ───────────────────────────────────────────
    schema = load_schema(cfg, f"{stage}.out")
    schema_result = _schema_check(main_df, schema, stage)
    prev = _previous_report(cfg, stage, rid) if s["compare_previous"] else None
    issues = list(extras.get("warnings", []))
    issues += _check_invariants(schema, metrics, (prev or {}).get("metrics", {}))
    if schema_result["status"] == "fail":
        issues.append(f"스키마 위반 {schema_result['n']}건 — ② 참조")
    if status != "ok":
        issues.insert(0, f"스테이지 실패: {error}")

    # ── 그림(수치 분포) ───────────────────────────────────────────────────
    figures = list(extras.get("figures", []))
    if main_df is not None and len(main_df) and s["hist_max"] and status == "ok":
        png = _hist_png(main_df, rdir / f"{stage}_hist.png", int(s["hist_max"]))
        if png:
            figures.insert(0, {"path": png.name, "caption": "수치 열 분포 (자동)"})

    # ── 표본 ──────────────────────────────────────────────────────────────
    samples = extras.get("samples")
    if samples is None and main_df is not None and len(main_df):
        cols = (params.get("report") or {}).get("sample_cols") or list(main_df.columns)[:6]
        cols = [c for c in cols if c in main_df.columns]
        seed = int((cfg.get("project") or {}).get("seed", 42))
        smp = main_df[cols].sample(n=min(int(s["sample_n"]), len(main_df)), random_state=seed)
        samples = {"cols": cols, "rows": _records(smp), "n": len(smp), "seed": seed}

    # ── 기록(json) ────────────────────────────────────────────────────────
    record = {
        "stage": stage, "run_id": rid, "ts": ts, "status": status, "error": error,
        "config": Path(cfg.get("_config_path", "")).name, "params": _py(params),
        "input": _file_meta(src), "artifact": _file_meta(artifact) if artifact is not None else None,
        "metrics": metrics, "schema": schema_result, "issues": issues,
        "previous_run_id": (prev or {}).get("run_id"),
    }
    write_json(record, rdir / f"{stage}.report.json")

    # ── 마크다운 ──────────────────────────────────────────────────────────
    md = _render(cfg, stage, record, tables, deliverables, extras, figures, samples, prev)
    out = rdir / f"{stage}.md"
    out.write_text(md, encoding="utf-8")
    _update_index(cfg, rdir)
    _update_card(cfg, stage, record)
    return out


# ================================================================ 적재 도우미
def _input_path(cfg: dict, stage: str) -> Path | None:
    order = stages(cfg)
    if stage not in order or order.index(stage) == 0:
        return None
    prev_stage = order[order.index(stage) - 1]
    p = (cfg.get("artifacts") or {}).get(prev_stage)
    if not p:
        return None
    p = Path(p)
    return p if p.is_absolute() else root_of(cfg) / p


def _load_tables(artifact: Path, limit: int = 6) -> dict[str, pd.DataFrame]:
    p = Path(artifact)
    if p.is_file() and p.suffix.lower() in TABLE_EXTS:
        return {p.name: read_table(p)}
    if p.is_dir():
        out = {}
        for f in sorted(p.iterdir()):
            if f.is_file() and f.suffix.lower() in TABLE_EXTS and not f.name.startswith("."):
                try:
                    out[f.name] = read_table(f)
                except Exception:  # noqa: BLE001 — 읽을 수 없는 표는 건너뛴다
                    continue
                if len(out) >= limit:
                    break
        return out
    return {}


def _list_deliverables(d: Path) -> list[dict]:
    rows = []
    for f in sorted(Path(d).rglob("*")):
        if f.is_file() and "reports" not in f.relative_to(d).parts:
            rows.append({"파일": str(f.relative_to(d)).replace("\\", "/"), "크기": _human(f.stat().st_size)})
    return rows[:50]


def _count_rows(p: Path) -> int | None:
    p = Path(p)
    try:
        if p.is_file() and p.suffix.lower() == ".parquet":
            import pyarrow.parquet as pq
            return int(pq.ParquetFile(p).metadata.num_rows)
        if p.is_file() and p.suffix.lower() in TABLE_EXTS:
            return int(len(read_table(p)))
        if p.is_dir():
            t = _load_tables(p, limit=1)
            return int(len(next(iter(t.values())))) if t else None
    except Exception:  # noqa: BLE001
        return None
    return None


def _file_meta(p: Path | None) -> dict | None:
    if p is None:
        return None
    p = Path(p)
    if not p.exists():
        return {"path": str(p), "exists": False}
    meta = {"path": str(p), "exists": True,
            "mtime": _dt.datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")}
    if p.is_file():
        meta["bytes"] = p.stat().st_size
        meta["sha256_12"] = _sha12(p)
    return meta


def _sha12(p: Path, cap: int = 512 * 1024 * 1024) -> str | None:
    if p.stat().st_size > cap:
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def _stage_cost(cfg: dict, run_id: str, stage: str) -> dict:
    """logs/llm_cost.jsonl 에서 이 run·이 스테이지(tag 가 '<stage>:' 로 시작) 의 신규 호출만 집계."""
    f = path_of(cfg, "logs") / "llm_cost.jsonl"
    out = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
    if not f.exists():
        return out
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("run_id") != run_id or not str(rec.get("tag", "")).startswith(f"{stage}:"):
            continue
        u = rec.get("usage") or {}
        out["calls"] += 1
        out["input_tokens"] += int(u.get("input_tokens") or u.get("prompt_tokens") or 0)
        out["output_tokens"] += int(u.get("output_tokens") or u.get("completion_tokens") or 0)
    return out


def _schema_check(df: pd.DataFrame | None, schema: dict | None, stage: str) -> dict:
    if df is None:
        return {"status": "skip", "n": 0, "detail": "표 산출 없음"}
    if schema is None:
        return {"status": "none", "n": 0, "detail": "schemas/ 미선언"}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            validate(df, schema, where=f"{stage}:out")
        return {"status": "pass", "n": 0, "detail": schema.get("name", "")}
    except SchemaError as e:
        lines = [ln.strip("- ").strip() for ln in str(e).splitlines()[1:]]
        return {"status": "fail", "n": len(lines), "detail": lines[:10]}


def _previous_report(cfg: dict, stage: str, run_id: str) -> dict | None:
    """outputs/*/reports/<stage>.report.json 중 현재 run 이 아닌 가장 최근 것."""
    base = path_of(cfg, "outputs")
    best, best_ts = None, ""
    for f in base.glob(f"*/reports/{stage}.report.json"):
        if f.parent.parent.name == run_id:
            continue
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if rec.get("status") != "ok":
            continue
        if rec.get("ts", "") > best_ts:
            best, best_ts = rec, rec.get("ts", "")
    return best


def _check_invariants(schema: dict | None, metrics: dict, prev_metrics: dict) -> list[str]:
    out = []
    for inv in (schema or {}).get("invariants") or []:
        if not isinstance(inv, dict) or "metric" not in inv:
            continue
        name = inv["metric"]
        cur = metrics.get(name)
        if cur is None:
            out.append(f"불변식 `{name}`: 지표가 없음 (add_metrics 로 넣었는지 확인)")
            continue
        if not isinstance(cur, (int, float)) or isinstance(cur, bool):
            continue
        if (lo := inv.get("min")) is not None and cur < lo:
            out.append(f"불변식 위반 `{name}` = {cur} < min {lo}" + (f" — {inv['note']}" if inv.get("note") else ""))
        if (hi := inv.get("max")) is not None and cur > hi:
            out.append(f"불변식 위반 `{name}` = {cur} > max {hi}" + (f" — {inv['note']}" if inv.get("note") else ""))
        pv = prev_metrics.get(name)
        if isinstance(pv, (int, float)) and not isinstance(pv, bool):
            if (d := inv.get("max_abs_delta")) is not None and abs(cur - pv) > d:
                out.append(f"불변식 위반 `{name}`: 직전 {pv} → {cur} (|Δ| {abs(cur - pv):.4g} > {d})")
            if (r := inv.get("max_rel_delta")) is not None and pv != 0 and abs(cur - pv) / abs(pv) > r:
                out.append(f"불변식 위반 `{name}`: 직전 {pv} → {cur} (Δ {100 * (cur - pv) / abs(pv):+.1f}% > ±{100 * r:.0f}%)")
    return out


def _hist_png(df: pd.DataFrame, path: Path, k: int) -> Path | None:
    num = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c])
           and df[c].nunique(dropna=True) > 1 and not c.endswith("_id") and c != "id"]
    num = num[:k]
    if not num:
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from .plot import setup_matplotlib
        setup_matplotlib(dpi=130)
        fig, axes = plt.subplots(1, len(num), figsize=(3.6 * len(num), 2.6))
        axes = np.atleast_1d(axes)
        for ax, c in zip(axes, num):
            s = df[c].dropna()
            if pd.api.types.is_integer_dtype(s) and s.nunique() <= 30:  # 연도·개수 같은 이산값은 값별 막대
                vc = s.value_counts().sort_index()
                ax.bar(vc.index.astype(str), vc.values, color="#4a4a4a")
                if len(vc) > 10:
                    ax.tick_params(axis="x", rotation=90)
            else:
                ax.hist(s.astype(float), bins=30, color="#4a4a4a")
            ax.set_title(c, fontsize=9)
            ax.tick_params(labelsize=7)
        fig.tight_layout()
        fig.savefig(path)
        plt.close(fig)
        return path
    except Exception:  # noqa: BLE001 — 그림은 부가물이다
        return None


# ================================================================ 렌더링
def _render(cfg, stage, rec, tables, deliverables, extras, figures, samples, prev) -> str:
    s = settings(cfg)
    m = rec["metrics"]
    ok = rec["status"] == "ok"
    L: list[str] = []
    L.append(f"# {stage} — 실행 리포트")
    L.append("")
    badge = "✔ ok" if ok else f"✘ {rec['status']}"
    L.append(f"`{rec['run_id']}` · {rec['config']} · {badge}"
             + (f" · {m['elapsed_s']}s" if "elapsed_s" in m else "") + f" · {rec['ts']}")
    if rec["issues"]:
        L.append("")
        L.append(f"> ⚠ 확인 필요 {len(rec['issues'])}건 — ⑥ 참조")
    L.append("")

    # ① 실행 메타
    L.append("## ① 실행 메타")
    rows = [("run_id", rec["run_id"]), ("config", rec["config"]),
            ("params", "`" + json.dumps(rec["params"], ensure_ascii=False, default=str) + "`" if rec["params"] else "(없음)")]
    if rec["input"]:
        rows.append(("입력", _meta_str(rec["input"])))
    if rec["artifact"]:
        rows.append(("출력", _meta_str(rec["artifact"])))
    if "elapsed_s" in m:
        rows.append(("소요", f"{m['elapsed_s']}s"))
    if "llm_calls" in m:
        rows.append(("LLM 신규 호출", f"{m['llm_calls']:,}회 · in {m['llm_input_tokens']:,} / out {m['llm_output_tokens']:,} tok"))
    ph = _prompt_hash(tables)
    if ph:
        rows.append(("프롬프트 해시", ph))
    if rec.get("previous_run_id"):
        rows.append(("직전 실행", rec["previous_run_id"]))
    L += _kv_table(rows)
    L.append("")

    # ② 입출력 요약
    L.append("## ② 입출력 요약")
    if tables:
        hdr = ["표", "행", "열", "스키마", "기본키"]
        body = []
        for i, (name, df) in enumerate(tables.items()):
            sch = _schema_badge(rec["schema"]) if i == 0 else "–"
            body.append([name, f"{len(df):,}", str(df.shape[1]), sch, _pk_badge(cfg, stage, df) if i == 0 else "–"])
        L += _table(hdr, body)
        if "rows_in" in m and "rows_out" in m:
            d = m.get("rows_delta", 0)
            L.append("")
            L.append(f"- 입력 {m['rows_in']:,}행 → 출력 {m['rows_out']:,}행 ({d:+,}"
                     + (f", {m['rows_delta_pct']:+.1f}%" if m.get("rows_delta_pct") is not None else "") + ")")
        if rec["schema"]["status"] == "fail":
            L.append("- 스키마 위반:")
            for ln in rec["schema"]["detail"]:
                L.append(f"  - {ln}")
        first = next(iter(tables.values()))
        nulls = first.isna().mean()
        nulls = nulls[nulls > 0].sort_values(ascending=False).head(5)
        if len(nulls):
            L.append("- 결측 상위: " + ", ".join(f"`{c}` {100 * v:.1f}%" for c, v in nulls.items()))
        L.append("")
        L.append("<details><summary>열 요약 (" + ("첫 표" if len(tables) > 1 else first_name(tables)) + ")</summary>")
        L.append("")
        L += _column_table(first, int(s["max_cols"]))
        L.append("")
        L.append("</details>")
    elif deliverables:
        L.append(f"산출 보고물 {len(deliverables)}개 (`outputs/{rec['run_id']}/`)")
        L.append("")
        L += _table(["파일", "크기"], [[d["파일"], d["크기"]] for d in deliverables])
    elif rec["input"] and not ok:
        L.append(f"- 입력 {m.get('rows_in', '?')}행 — 스테이지가 실패해 산출 없음")
    else:
        L.append("(표 산출 없음)")
    L.append("")

    # ③ 핵심 지표·분포
    L.append("## ③ 핵심 지표·분포")
    user_m = extras.get("metrics", {})
    auto_m = {k: v for k, v in m.items() if k not in user_m}
    if user_m:
        L.append("**스테이지 지표**")
        L.append("")
        L += _kv_table([(k, _fmt(v)) for k, v in user_m.items()])
        L.append("")
    L.append("**자동 지표**")
    L.append("")
    L += _kv_table([(k, _fmt(v)) for k, v in auto_m.items() if k != "null_rate_max_col"] or [("—", "지표 없음")])
    if tables:
        cats = _categorical_tables(next(iter(tables.values())), int(s["cat_max"]), int(s["cat_card"]))
        for name, (hdr, body) in cats.items():
            L.append("")
            L.append(f"**`{name}` 분포**")
            L.append("")
            L += _table(hdr, body)
    for name, recs in extras.get("tables", {}).items():
        L.append("")
        L.append(f"**{name}**")
        L.append("")
        L += _records_table(recs)
    for fg in figures:
        L.append("")
        L.append(f"![{fg.get('caption', '')}]({fg['path']})")
        if fg.get("caption"):
            L.append(f"*{fg['caption']}*")
    L.append("")

    # ④ 표본
    L.append("## ④ 표본")
    if samples and samples.get("rows"):
        L.append(f"n={samples['n']} · seed={samples['seed']} · 열 {', '.join(f'`{c}`' for c in samples['cols'])}")
        L.append("")
        L += _records_table(samples["rows"], cols=samples["cols"])
    else:
        L.append("(표본 없음)")
    L.append("")

    # ⑤ 직전 실행 대비
    L.append("## ⑤ 직전 실행 대비")
    if prev:
        L.append(f"직전 `{prev['run_id']}` ({prev.get('ts', '')}, {prev.get('config', '')})")
        L.append("")
        pm = prev.get("metrics", {})
        body = []
        for k, v in m.items():
            if k in ("elapsed_s", "null_rate_max_col") or not _isnum(v):
                continue
            pv = pm.get(k)
            if not _isnum(pv):
                body.append([f"`{k}`", "–", _fmt(v), "신규", ""])
                continue
            d = v - pv
            pct = f"{100 * d / abs(pv):+.1f}%" if pv else ""
            body.append([f"`{k}`", _fmt(pv), _fmt(v), _fmt(d, signed=True), pct])
        L += _table(["지표", "직전", "현재", "Δ", "Δ%"], body) if body else ["(비교 가능한 수치 지표 없음)"]
        changed = _param_diff(prev.get("params") or {}, rec["params"] or {})
        if changed:
            L.append("")
            L.append("변경된 파라미터: " + ", ".join(f"`{k}`: {a} → {b}" for k, a, b in changed))
        if prev.get("input") and rec["input"] and prev["input"].get("sha256_12") != rec["input"].get("sha256_12"):
            L.append("")
            L.append("입력 파일이 직전 실행과 다름 (해시 불일치) — 상류 변경이 있었음")
    else:
        L.append("(비교할 직전 실행 없음 — 첫 실행)")
    L.append("")

    # ⑥ 확인 필요
    L.append("## ⑥ 확인 필요")
    if rec["issues"]:
        for it in rec["issues"]:
            L.append(f"- ⚠ {it}")
    else:
        L.append("- 없음")
    for n in extras.get("notes", []):
        L.append(f"- 메모: {n}")
    L.append("")
    return "\n".join(L)


def first_name(tables: dict) -> str:
    return next(iter(tables.keys()))


def _update_index(cfg: dict, rdir: Path) -> None:
    rid = cfg.get("run_id", "adhoc")
    order = stages(cfg)
    recs = {}
    for f in rdir.glob("*.report.json"):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
            recs[r["stage"]] = r
        except (json.JSONDecodeError, KeyError):
            continue
    names = [s for s in order if s in recs] + [s for s in recs if s not in order]
    L = [f"# 실행 리포트 — `{rid}`", "",
         f"config `{Path(cfg.get('_config_path', '')).name}` · 생성 {_dt.datetime.now().isoformat(timespec='seconds')}", ""]
    body = []
    total_issues = 0
    for s in names:
        r = recs[s]
        m = r.get("metrics", {})
        n_iss = len(r.get("issues", []))
        total_issues += n_iss
        body.append([f"[{s}]({s}.md)", "✔" if r["status"] == "ok" else "✘",
                     f"{m['rows_out']:,}" if "rows_out" in m else "–",
                     f"{m['elapsed_s']}s" if "elapsed_s" in m else "–",
                     f"{m['llm_calls']:,}" if "llm_calls" in m else "–",
                     f"⚠ {n_iss}" if n_iss else "–"])
    L += _table(["스테이지", "상태", "출력 행", "소요", "LLM 호출", "확인"], body)
    L.append("")
    L.append(f"확인 필요 합계: {total_issues}건" if total_issues else "확인 필요: 없음")
    L.append("")
    (rdir / "index.md").write_text("\n".join(L), encoding="utf-8")


_CARD_RX = re.compile(r"(\|\s*최근 실행\s*\|)(.*?)(\|\s*\n)")


def _update_card(cfg: dict, stage: str, rec: dict) -> None:
    """src/<stage>/CARD.md 의 '최근 실행' 행만 갱신. 파일이 없거나 행이 없으면 건드리지 않는다."""
    card = root_of(cfg) / "src" / stage / "CARD.md"
    if not card.exists():
        return
    m = rec["metrics"]
    cell = f" `{rec['run_id']}` · {m['rows_out']:,}행" if "rows_out" in m else f" `{rec['run_id']}`"
    cell += f" · {rec['status']}"
    if rec["issues"]:
        cell += f" · ⚠{len(rec['issues'])}"
    cell += f" · [리포트](../../outputs/{rec['run_id']}/reports/{stage}.md) "
    text = card.read_text(encoding="utf-8")
    new, n = _CARD_RX.subn(lambda mo: mo.group(1) + cell + mo.group(3), text, count=1)
    if n:
        card.write_text(new, encoding="utf-8")


# ================================================================ 표·서식 도우미
def _kv_table(rows: list[tuple[str, object]]) -> list[str]:
    return ["| 항목 | 값 |", "|---|---|"] + [f"| {k} | {_cell(v)} |" for k, v in rows]


def _table(hdr: list[str], body: list[list]) -> list[str]:
    out = ["| " + " | ".join(hdr) + " |", "|" + "---|" * len(hdr)]
    out += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in body]
    return out


def _records_table(recs: list[dict], cols: list[str] | None = None, width: int = 80) -> list[str]:
    if not recs:
        return ["(없음)"]
    cols = cols or list(recs[0].keys())
    return _table(cols, [[_cell(r.get(c), width) for c in cols] for r in recs])


def _column_table(df: pd.DataFrame, max_cols: int) -> list[str]:
    body = []
    for c in list(df.columns)[:max_cols]:
        s = df[c]
        null = f"{100 * s.isna().mean():.1f}%"
        try:
            nun = f"{s.nunique(dropna=True):,}"
        except TypeError:
            nun = "–"
        if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s) and s.notna().any():
            rng = f"{_num(s.min())} / {_num(s.median())} / {_num(s.max())}"
        elif s.notna().any() and s.dropna().map(lambda x: isinstance(x, (list, tuple, np.ndarray))).all():
            rng = f"list, 평균 길이 {s.dropna().map(len).mean():.1f}"
        elif s.notna().any() and pd.api.types.is_string_dtype(s.dropna()):
            rng = f"str, 평균 길이 {s.dropna().astype(str).str.len().mean():.0f}"
        else:
            rng = ""
        body.append([f"`{c}`", str(s.dtype), null, nun, rng])
    out = _table(["열", "dtype", "결측", "고유값", "min/중앙/max 또는 길이"], body)
    if df.shape[1] > max_cols:
        out.append(f"(외 {df.shape[1] - max_cols}열 생략)")
    return out


def _categorical_tables(df: pd.DataFrame, k: int, card: int) -> dict[str, tuple[list[str], list[list]]]:
    out = {}
    for c in df.columns:
        s = df[c]
        if pd.api.types.is_bool_dtype(s) or (s.dtype == object or pd.api.types.is_string_dtype(s) or isinstance(s.dtype, pd.CategoricalDtype)):
            try:
                nun = s.nunique(dropna=True)
            except TypeError:
                continue
            if 2 <= nun <= card:
                vc = s.value_counts(dropna=False)
                tot = int(vc.sum()) or 1
                out[c] = (["값", "건수", "비율"], [[_cell(i, 40), f"{int(v):,}", f"{100 * v / tot:.1f}%"] for i, v in vc.items()])
        if len(out) >= k:
            break
    return out


def _prompt_hash(tables: dict) -> str | None:
    for df in tables.values():
        if "prompt_hash" in df.columns and df["prompt_hash"].notna().any():
            vals = df["prompt_hash"].dropna().unique()
            return ", ".join(map(str, vals[:3])) + (" …" if len(vals) > 3 else "")
    return None


def _schema_badge(sr: dict) -> str:
    return {"pass": "✔ 통과", "fail": f"✘ 위반 {sr['n']}건", "none": "미선언", "skip": "–"}.get(sr["status"], sr["status"])


def _pk_badge(cfg: dict, stage: str, df: pd.DataFrame) -> str:
    sch = load_schema(cfg, f"{stage}.out") or {}
    pk = sch.get("primary_key")
    if not pk:
        pk = [c for c, spec in (sch.get("columns") or {}).items() if (spec or {}).get("unique")]
    pk = [pk] if isinstance(pk, str) else list(pk or [])
    pk = [c for c in pk if c in df.columns]
    if not pk:
        return "미선언"
    try:
        dup = int(df.duplicated(subset=pk).sum())
    except TypeError:
        return "확인 불가"
    return f"✔ {'+'.join(pk)}" if not dup else f"✘ 중복 {dup:,}"


def _param_diff(a: dict, b: dict) -> list[tuple[str, str, str]]:
    out = []
    for k in sorted(set(a) | set(b)):
        if a.get(k) != b.get(k):
            out.append((k, json.dumps(a.get(k), ensure_ascii=False, default=str), json.dumps(b.get(k), ensure_ascii=False, default=str)))
    return out


def _meta_str(meta: dict) -> str:
    if not meta.get("exists", True):
        return f"`{Path(meta['path']).name}` (없음)"
    s = f"`{Path(meta['path']).name}`"
    if meta.get("bytes") is not None:
        s += f" · {_human(meta['bytes'])}"
    if meta.get("sha256_12"):
        s += f" · sha256 {meta['sha256_12']}"
    if meta.get("mtime"):
        s += f" · {meta['mtime']}"
    return s


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def _isnum(v) -> bool:
    return isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, bool) and not (isinstance(v, float) and np.isnan(v))


def _fmt(v, signed: bool = False) -> str:
    if v is None:
        return "–"
    if isinstance(v, bool):
        return "✔" if v else "✘"
    if isinstance(v, (int, np.integer)):
        return f"{int(v):+,}" if signed else f"{int(v):,}"
    if isinstance(v, (float, np.floating)):
        if np.isnan(v):
            return "–"
        f = float(v)
        if abs(f) >= 1000:
            return f"{f:+,.0f}" if signed else f"{f:,.0f}"
        return f"{f:+.4g}" if signed else f"{f:.4g}"
    return str(v)


def _num(v) -> str:
    """데이터 값 표시용 — 연도·ID 같은 정수에 천단위 콤마를 넣지 않는다 (지표 표는 _fmt)."""
    if isinstance(v, bool):
        return "✔" if v else "✘"
    if isinstance(v, (int, np.integer)):
        return str(int(v))
    if isinstance(v, (float, np.floating)):
        return "–" if np.isnan(v) else f"{float(v):.4g}"
    return str(v)


def _cell(v, width: int = 120) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ""
    if isinstance(v, (int, float, np.integer, np.floating, bool)):
        return _num(v)
    s = str(v).replace("\n", " ").replace("|", "\\|").strip()
    return s if len(s) <= width else s[: width - 1] + "…"


def _records(df: pd.DataFrame) -> list[dict]:
    return [{str(k): _py(v) for k, v in r.items()} for r in df.to_dict("records")]


def _py(v):
    """numpy/pandas 값을 json 직렬화 가능한 파이썬 값으로."""
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return None if np.isnan(v) else float(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if isinstance(v, (np.ndarray, list, tuple)):
        return [_py(x) for x in list(v)]
    if isinstance(v, dict):
        return {str(k): _py(x) for k, x in v.items()}
    if isinstance(v, (pd.Timestamp, _dt.datetime, _dt.date)):
        return v.isoformat()
    if isinstance(v, float) and np.isnan(v):
        return None
    if isinstance(v, Path):
        return str(v)
    return v


def _under(p: Path, d: Path) -> bool:
    try:
        p.resolve().relative_to(d.resolve())
        return True
    except ValueError:
        return False


# ================================================================ CLI: 기존 산출로 리포트 재생성
if __name__ == "__main__":  # python -m src.common.report s3_extract [--run-id RID] [-c configs/x.yaml]
    import argparse

    from .config import load_config
    from .io import artifact_path

    ap = argparse.ArgumentParser(description="기존 artifact 로 스테이지 리포트를 (재)생성한다")
    ap.add_argument("stage")
    ap.add_argument("--run-id", default=None, help="기본: outputs/ 의 가장 최근 run_id, 없으면 'adhoc'")
    ap.add_argument("-c", "--config", default="configs/default.yaml")
    a = ap.parse_args()
    cfg = load_config(a.config)
    if a.run_id:
        cfg["run_id"] = a.run_id
    else:
        runs = sorted(d.name for d in path_of(cfg, "outputs").iterdir() if d.is_dir())
        cfg["run_id"] = runs[-1] if runs else "adhoc"
    out = write_stage_report(cfg, a.stage, artifact_path(cfg, a.stage))
    print(out)
