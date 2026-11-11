"""파일 입출력 — 스테이지 간 계약은 '파일'이다.

메모리 객체 대신 파일로 넘기는 이유: 스테이지 단위 재실행, 중간 산출 검수(노트북에서 열어보기),
실패 지점부터 재개가 가능해진다. 확장자로 포맷을 결정하므로 호출부는 경로만 다룬다.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path

import pandas as pd

from .config import path_of, root_of, stages

TABLE_EXTS = {".parquet", ".csv", ".tsv", ".jsonl", ".json", ".xlsx", ".feather", ".pkl"}


# ---------------------------------------------------------------- 표 읽기/쓰기
def read_table(path: str | Path, **kw) -> pd.DataFrame:
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".parquet":
        return pd.read_parquet(p, **kw)
    if ext == ".csv":
        return pd.read_csv(p, **({"encoding": "utf-8-sig"} | kw))
    if ext == ".tsv":
        return pd.read_csv(p, sep="\t", **kw)
    if ext == ".jsonl":
        return pd.read_json(p, lines=True, **kw)
    if ext == ".json":
        return pd.read_json(p, **kw)
    if ext == ".xlsx":
        return pd.read_excel(p, **kw)
    if ext == ".feather":
        return pd.read_feather(p, **kw)
    if ext == ".pkl":
        return pd.read_pickle(p, **kw)
    raise ValueError(f"지원하지 않는 표 포맷: {p}")


def write_table(df: pd.DataFrame, path: str | Path, **kw) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    ext = p.suffix.lower()
    if ext == ".parquet":
        df.to_parquet(p, index=False, **kw)
    elif ext == ".csv":
        df.to_csv(p, index=False, encoding="utf-8-sig", **kw)
    elif ext == ".tsv":
        df.to_csv(p, index=False, sep="\t", **kw)
    elif ext == ".jsonl":
        df.to_json(p, orient="records", lines=True, force_ascii=False, **kw)
    elif ext == ".json":
        df.to_json(p, orient="records", force_ascii=False, indent=2, **kw)
    elif ext == ".xlsx":
        df.to_excel(p, index=False, **kw)
    elif ext == ".feather":
        df.to_feather(p, **kw)
    elif ext == ".pkl":
        df.to_pickle(p, **kw)
    else:
        raise ValueError(f"지원하지 않는 표 포맷: {p}")
    return p


def read_json(path: str | Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(obj, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)
    return p


# ---------------------------------------------------------------- 스테이지 계약
def artifact_path(cfg: dict, stage: str) -> Path:
    """cfg['artifacts'][stage] — 이 스테이지가 '내놓기로 약속한' 파일(또는 디렉터리)."""
    arts = cfg.get("artifacts") or {}
    if stage not in arts:
        raise KeyError(f"configs 의 artifacts 에 '{stage}' 산출 경로가 없음")
    p = Path(arts[stage])
    return p if p.is_absolute() else root_of(cfg) / p


def input_of(cfg: dict, stage: str) -> Path | None:
    """직전 스테이지의 artifact. 첫 스테이지면 None (raw 에서 직접 읽는다)."""
    order = stages(cfg)
    i = order.index(stage)
    return artifact_path(cfg, order[i - 1]) if i > 0 else None


def run_dir(cfg: dict) -> Path:
    """outputs/<run_id>/ — 보고물은 실행 단위로 묶어 둔다(어느 설정에서 나온 그림인지 추적)."""
    d = path_of(cfg, "outputs") / cfg.get("run_id", "adhoc")
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------- 산출 보고물 명명
_VER = re.compile(r"\((\d{6}), (\d{2}), ([^)]+)\)$")


def deliverable_path(cfg: dict, title: str, ext: str, directory: str | Path | None = None) -> Path:
    """`제목(YYMMDD, NN, 저자).ext` — 같은 제목·날짜의 기존 파일이 있으면 NN 을 +1.

    예: 수소저장 특허 문제-해결 분석(261026, 01, 박상현).xlsx
    """
    author = (cfg.get("project") or {}).get("author", "")
    today = _dt.date.today().strftime("%y%m%d")
    d = Path(directory) if directory else run_dir(cfg)
    d.mkdir(parents=True, exist_ok=True)
    ext = ext if ext.startswith(".") else f".{ext}"
    existing = []
    for p in d.glob(f"{title}(*{ext}"):
        m = _VER.search(p.stem)
        if m and m.group(1) == today and m.group(3) == author:
            existing.append(int(m.group(2)))
    ver = (max(existing) + 1) if existing else 1
    return d / f"{title}({today}, {ver:02d}, {author}){ext}"
