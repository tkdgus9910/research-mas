"""__STAGE__ — __HINT__

입력  : data/raw/ (또는 외부 API) — 원본은 읽기만 한다
출력  : configs artifacts.__STAGE__
파라미터: configs params.__STAGE__  (raw_file, 수집 조건 등)
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.common.config import path_of, stage_params
from src.common.io import artifact_path, read_table, write_table
from src.common.log import get_logger
from src.common.report import add_metrics
from src.common.schema import load_schema, validate

STAGE = "__STAGE__"


def run(cfg: dict) -> Path:
    log = get_logger(cfg)
    p = stage_params(cfg, STAGE)
    dst = artifact_path(cfg, STAGE)

    # ── 로드: raw 파일 또는 API ────────────────────────────────────────────
    src = path_of(cfg, "raw") / p.get("raw_file", "CHANGE_ME.csv")
    df = read_table(src)
    log.info(f"{STAGE}: 로드 {len(df):,}행 ← {src.name}")

    # ── 핵심 로직 (여기만 채운다) ───────────────────────────────────────────
    out = df
    # ──────────────────────────────────────────────────────────────────────

    validate(out, load_schema(cfg, f"{STAGE}.out"), where=f"{STAGE}:out")
    write_table(out, dst)
    # 리포트(outputs/<run_id>/reports/__STAGE__.md)는 run.py 가 자동 생성한다. 이 스테이지에서
    # 사람이 봐야 할 숫자만 add_metrics 로 올린다 — 불변식(schemas/*.out.yaml invariants)의 대상이 된다.
    add_metrics(cfg, STAGE, raw_rows=len(df), raw_file=src.name)
    log.info(f"{STAGE}: {len(out):,}행 → {dst.name}")
    return dst
