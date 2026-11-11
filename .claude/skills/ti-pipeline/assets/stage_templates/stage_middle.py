"""__STAGE__ — __HINT__

입력  : 직전 스테이지 artifact (configs artifacts 순서)
출력  : configs artifacts.__STAGE__
파라미터: configs params.__STAGE__
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.common.config import stage_params
from src.common.io import artifact_path, input_of, read_table, write_table
from src.common.log import get_logger
from src.common.report import add_metrics
from src.common.schema import load_schema, validate

STAGE = "__STAGE__"


def run(cfg: dict) -> Path:
    log = get_logger(cfg)
    p = stage_params(cfg, STAGE)
    src = input_of(cfg, STAGE)
    dst = artifact_path(cfg, STAGE)

    df = read_table(src)
    validate(df, load_schema(cfg, f"{STAGE}.in"), where=f"{STAGE}:in")  # 입력 스키마는 선택
    log.info(f"{STAGE}: 로드 {len(df):,}행 ← {src.name}")

    # ── 핵심 로직 (여기만 채운다) ───────────────────────────────────────────
    out = df
    # ──────────────────────────────────────────────────────────────────────

    validate(out, load_schema(cfg, f"{STAGE}.out"), where=f"{STAGE}:out")
    write_table(out, dst)
    # 리포트는 run.py 가 자동 생성. 여기서는 이 변환의 핵심 숫자(탈락 수·비율 등)만 올린다.
    # 분포 표가 필요하면 add_table(cfg, STAGE, "탈락 사유", reasons_df), 표본 열 지정은 add_samples(...)
    add_metrics(cfg, STAGE, dropped_rows=len(df) - len(out),
                dropped_pct=round(100 * (len(df) - len(out)) / len(df), 2) if len(df) else 0.0)
    log.info(f"{STAGE}: {len(out):,}행 → {dst.name}")
    return dst
