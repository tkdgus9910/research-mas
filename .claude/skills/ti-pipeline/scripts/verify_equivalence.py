#!/usr/bin/env python3
"""리팩터링 전후 출력 동일성 검증.

    python verify_equivalence.py <기준> <비교>  [--key patent_id] [--atol 1e-6]
                                 [--ignore-cols run_id,prompt_hash,llm_cached] [--map old.csv=new.parquet ...] [--col-map 0=weighted_degree]

<기준>/<비교> 는 파일 또는 디렉터리. 디렉터리면 확장자를 무시한 stem 으로 짝을 맞춘다
(results/extracted.csv ↔ data/interim/s3_extract.parquet 처럼 이름이 다르면 --map 으로 지정).

표 파일(csv/tsv/parquet/xlsx/jsonl/json)은 DataFrame 으로 읽어 비교한다:
  - 공통 컬럼만 값 비교(비교 쪽에만 있는 컬럼은 '추가됨' 으로 보고, 실패 아님)
  - --key 가 있으면 키로 정렬 후 비교(행 순서 차이 허용), 없으면 전체 컬럼으로 정렬
  - 실수는 atol 허용, 문자열은 strip 후 비교, NaN==NaN
그 외 파일은 sha256 비교(png 는 보통 다르게 나온다 — 그림은 눈으로 확인하고 --ignore 로 뺀다).

종료 코드: 0 동일 / 1 차이 있음 / 2 입력 오류.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import numpy as np
import pandas as pd

TABLE_EXTS = {".csv", ".tsv", ".parquet", ".xlsx", ".jsonl", ".json", ".feather", ".pkl"}


def read_any(p: Path) -> dict[str, pd.DataFrame]:
    """xlsx 는 시트별로 여러 표를 돌려준다."""
    ext = p.suffix.lower()
    if ext == ".xlsx":
        return {f"{p.stem}::{k}": v for k, v in pd.read_excel(p, sheet_name=None).items()}
    if ext == ".csv":
        return {p.stem: pd.read_csv(p, encoding="utf-8-sig")}
    if ext == ".tsv":
        return {p.stem: pd.read_csv(p, sep="\t")}
    if ext == ".parquet":
        return {p.stem: pd.read_parquet(p)}
    if ext == ".jsonl":
        return {p.stem: pd.read_json(p, lines=True)}
    if ext == ".json":
        return {p.stem: pd.read_json(p)}
    if ext == ".feather":
        return {p.stem: pd.read_feather(p)}
    if ext == ".pkl":
        return {p.stem: pd.read_pickle(p)}
    raise ValueError(ext)


def norm(df: pd.DataFrame, ignore: set[str], _unused: dict | None = None) -> pd.DataFrame:
    df = df.drop(columns=[c for c in df.columns if c in ignore or str(c).startswith("Unnamed:")], errors="ignore").copy()
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].map(lambda x: x.strip() if isinstance(x, str) else x)
            # 리스트 컬럼(parquet)은 문자열로 평탄화해 csv 쪽과 맞춘다
            df[c] = df[c].map(lambda x: str(list(x)) if isinstance(x, (list, tuple, np.ndarray)) else x)
    return df


def _sortable(df: pd.DataFrame, c) -> bool:
    return bool(df[c].map(lambda v: isinstance(v, (str, int, float, np.integer, np.floating, bool)) or v is None
                          or (isinstance(v, float) and np.isnan(v))).all())


def sort_pair(a: pd.DataFrame, b: pd.DataFrame, key: list[str] | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """양쪽을 '공통 컬럼' 기준으로 같은 순서로 정렬 — 한쪽에만 있는 컬럼이 정렬을 바꾸지 않게."""
    common = [c for c in a.columns if c in b.columns]
    cols = [k for k in (key or []) if k in common] or [c for c in common if _sortable(a, c) and _sortable(b, c)]
    if cols:
        a = a.sort_values(cols, kind="mergesort")
        b = b.sort_values(cols, kind="mergesort")
    return a.reset_index(drop=True), b.reset_index(drop=True)


def compare_tables(a: pd.DataFrame, b: pd.DataFrame, key: list[str] | None, atol: float, ignore: set[str], label: str,
                   col_map: dict[str, str] | None = None) -> list[str]:
    a, b = norm(a, ignore, {}), norm(b, ignore, {})
    # --col-map: 기준 컬럼이 비교 쪽에 없고 새 이름이 비교 쪽에 있을 때만 적용 (파일마다 같은 이름 '0' 이 다른 뜻일 수 있으므로)
    ren = {c: col_map[str(c)] for c in a.columns if col_map and str(c) in col_map and c not in b.columns and col_map[str(c)] in b.columns}
    a = a.rename(columns=ren)
    a, b = sort_pair(a, b, key)
    msgs = []
    common = [c for c in a.columns if c in b.columns]
    missing = [c for c in a.columns if c not in b.columns]
    added = [c for c in b.columns if c not in a.columns]
    if missing:
        msgs.append(f"기준에만 있는 컬럼(누락): {missing}")
    if added:
        msgs.append(f"비교에만 있는 컬럼(추가, 허용): {added}")
    if len(a) != len(b):
        msgs.append(f"행 수 다름: 기준 {len(a)} vs 비교 {len(b)}")
    n = min(len(a), len(b))
    for c in common:
        x, y = a[c].iloc[:n], b[c].iloc[:n]
        if pd.api.types.is_numeric_dtype(x) and pd.api.types.is_numeric_dtype(y):
            xf, yf = x.astype(float).to_numpy(), y.astype(float).to_numpy()
            bad = ~(np.isclose(xf, yf, atol=atol, rtol=0, equal_nan=True))
        else:
            xs, ys = x.astype(object).where(x.notna(), None), y.astype(object).where(y.notna(), None)
            bad = (xs.map(str) != ys.map(str)).to_numpy()
        if bad.any():
            i = int(np.argmax(bad))
            msgs.append(f"컬럼 '{c}': {int(bad.sum())}/{n} 행 불일치 (첫 예: 행 {i} 기준={_fmt(a[c].iloc[i])} 비교={_fmt(b[c].iloc[i])})")
    return [f"[{label}] {m}" for m in msgs if not m.startswith("비교에만")] + [f"[{label}] (참고) {m}" for m in msgs if m.startswith("비교에만")]


def _fmt(v) -> str:
    v = v.item() if hasattr(v, "item") else v
    return repr(v) if isinstance(v, str) else str(v)


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def pair_files(a: Path, b: Path, maps: list[str]) -> list[tuple[Path, Path]]:
    if a.is_file() and b.is_file():
        return [(a, b)]
    if not (a.is_dir() and b.is_dir()):
        sys.exit("둘 다 파일이거나 둘 다 디렉터리여야 함")
    explicit = {}
    for m in maps:
        k, _, v = m.partition("=")
        explicit[a / k] = b / v
    pairs = list(explicit.items())
    b_by_stem = {}
    for p in b.rglob("*"):
        if p.is_file():
            b_by_stem.setdefault(p.stem, []).append(p)
    for p in sorted(a.rglob("*")):
        if not p.is_file() or p in explicit:
            continue
        cands = b_by_stem.get(p.stem, [])
        same_ext = [c for c in cands if c.suffix.lower() == p.suffix.lower()]
        tables = [c for c in cands if c.suffix.lower() in TABLE_EXTS] if p.suffix.lower() in TABLE_EXTS else []
        pick = (same_ext or tables or cands)
        if pick:
            pairs.append((p, pick[0]))
        else:
            pairs.append((p, None))
    return pairs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("baseline")
    ap.add_argument("candidate")
    ap.add_argument("--key", help="행 정렬 키 (쉼표 구분)")
    ap.add_argument("--atol", type=float, default=1e-6)
    ap.add_argument("--ignore-cols", default="run_id,prompt_hash,llm_cached,llm_raw,ts,timestamp")
    ap.add_argument("--ignore", default="", help="건너뛸 파일명 또는 stem (쉼표 구분, 예: trend.png,network.png)")
    ap.add_argument("--map", action="append", default=[], metavar="OLD=NEW", help="이름이 다른 파일 짝 지정 (디렉터리 기준 상대경로)")
    ap.add_argument("--col-map", action="append", default=[], metavar="OLD=NEW", help="기준 쪽 컬럼명을 비교 쪽 이름으로 바꿔 비교 (예: 0=weighted_degree)")
    a = ap.parse_args()

    key = [k.strip() for k in a.key.split(",")] if a.key else None
    ignore_cols = {c.strip() for c in a.ignore_cols.split(",") if c.strip()}
    ignore_files = {c.strip() for c in a.ignore.split(",") if c.strip()}
    pairs = pair_files(Path(a.baseline), Path(a.candidate), a.map)
    col_map = dict(m.partition("=")[::2] for m in a.col_map)

    problems, ok, skipped = [], [], []
    for pa, pb in pairs:
        if pa.stem in ignore_files or pa.name in ignore_files:
            skipped.append(pa.name)
            continue
        if pb is None:
            problems.append(f"[{pa.name}] 비교 쪽에 대응 파일 없음 (--map 으로 지정하거나 --ignore)")
            continue
        if pa.suffix.lower() in TABLE_EXTS and pb.suffix.lower() in TABLE_EXTS:
            ta, tb = read_any(pa), read_any(pb)
            if len(ta) == 1 and len(tb) == 1:
                (na, da), (nb, db) = next(iter(ta.items())), next(iter(tb.items()))
                msgs = compare_tables(da, db, key, a.atol, ignore_cols, f"{pa.name} ↔ {pb.name}", col_map)
            else:  # xlsx 시트 매칭
                msgs = []
                for na, da in ta.items():
                    sheet = na.split("::")[-1]
                    match = next((db for nb, db in tb.items() if nb.split("::")[-1] == sheet), None)
                    if match is None:
                        msgs.append(f"[{pa.name}] 시트 '{sheet}' 비교 쪽에 없음")
                    else:
                        msgs += compare_tables(da, match, key, a.atol, ignore_cols, f"{pa.name}::{sheet}", col_map)
            hard = [m for m in msgs if "(참고)" not in m]
            (problems if hard else ok).append(f"{pa.name} ↔ {pb.name}")
            problems.extend(hard)
            for m in msgs:
                if "(참고)" in m:
                    print("  ·", m)
        else:
            if sha(pa) == sha(pb):
                ok.append(f"{pa.name} ↔ {pb.name} (sha 동일)")
            else:
                problems.append(f"[{pa.name}] 바이너리 해시 다름 — 그림이면 눈으로 비교하고 --ignore {pa.name}")

    print("\n== 동일 ==")
    for o in ok:
        print("  ✔", o)
    if skipped:
        print("== 건너뜀 ==\n  " + ", ".join(skipped))
    if problems:
        print("\n== 차이 ==")
        for p in problems:
            print("  ✘", p)
        print(f"\n결과: 차이 {len([p for p in problems if p.startswith('[')])}건")
        return 1
    print("\n결과: 모든 산출 동일")
    return 0


if __name__ == "__main__":
    sys.exit(main())
