"""__STAGE__ — __HINT__

입력  : 직전 스테이지 artifact (필요하면 artifact_path(cfg, "s2_...") 로 다른 스테이지 것도 읽는다)
출력  : outputs/<run_id>/  — 표(xlsx)·그림(png)·요약(md). 산출 보고물 파일명은
        deliverable_path(cfg, 제목, 확장자) → `제목(YYMMDD, NN, 저자).ext`
파라미터: configs params.__STAGE__ → deliverable_title, figures
"""
from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from src.common.config import stage_params
from src.common.io import deliverable_path, input_of, read_table, run_dir, write_json
from src.common.log import get_logger
from src.common.plot import setup_matplotlib
from src.common.report import add_figure, add_metrics

STAGE = "__STAGE__"


def run(cfg: dict) -> Path:
    log = get_logger(cfg)
    setup_matplotlib()
    p = stage_params(cfg, STAGE)
    src = input_of(cfg, STAGE)
    out_dir = run_dir(cfg)
    title = p.get("deliverable_title", "분석결과")

    df = read_table(src)
    log.info(f"{STAGE}: {len(df):,}행 ← {src.name}")

    # ── 표 ────────────────────────────────────────────────────────────────
    xlsx = deliverable_path(cfg, title, "xlsx", out_dir)
    with pd.ExcelWriter(xlsx) as xw:
        df.to_excel(xw, sheet_name="data", index=False)
        # 요약 시트는 여기서 추가: summary.to_excel(xw, sheet_name="summary", index=False)

    # ── 그림 (예시 — 실제 축·범례는 분석에 맞게) ──────────────────────────
    fig, ax = plt.subplots(figsize=(8, 4.5))
    if "year" in df.columns:
        df["year"].value_counts().sort_index().plot(kind="bar", ax=ax)
        ax.set_xlabel("year"), ax.set_ylabel("count")
    ax.set_title(title)
    fig.tight_layout()
    png = out_dir / f"{STAGE}_overview.png"
    fig.savefig(png, dpi=200)
    plt.close(fig)

    # ── 실행 메타 (어느 설정·데이터에서 나온 산출물인지) ───────────────────
    write_json({"run_id": cfg.get("run_id"), "config": cfg.get("_config_path"), "rows": len(df),
                "input": str(src), "params": p}, out_dir / f"{STAGE}_meta.json")
    # 스테이지 리포트(reports/__STAGE__.md)에는 보고물 목록이 자동으로 들어간다. 그림은 add_figure 로 함께 노출.
    add_metrics(cfg, STAGE, deliverables=2, deliverable_title=title)
    add_figure(cfg, STAGE, png, caption=f"{title} — 개요 그림")
    log.info(f"{STAGE}: → {xlsx.name}, {png.name}")
    return out_dir
