"""실행 ID·로거·실행 기록 — '이 그림이 어느 설정·어느 데이터에서 나왔나' 를 추적한다."""
from __future__ import annotations

import datetime as _dt
import json
import logging
import sys
from pathlib import Path

from .config import path_of

_loggers: dict[str, logging.Logger] = {}


def new_run_id(tag: str = "") -> str:
    """YYMMDD_HHMMSS[_tag] — outputs/·logs/ 의 묶음 단위 (초 단위: 연속 실행 충돌 방지)."""
    rid = _dt.datetime.now().strftime("%y%m%d_%H%M%S")
    return f"{rid}_{tag}" if tag else rid


def get_logger(cfg: dict, name: str = "pipeline") -> logging.Logger:
    """stdout + logs/<run_id>.log 동시 기록. 같은 run_id 면 같은 로거 재사용."""
    rid = cfg.get("run_id", "adhoc")
    key = f"{name}:{rid}"
    if key in _loggers:
        return _loggers[key]
    lg = logging.getLogger(key)
    lg.setLevel(logging.INFO)
    lg.propagate = False
    fmt = logging.Formatter("%(asctime)s %(levelname).1s %(message)s", "%H:%M:%S")
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    lg.addHandler(sh)
    logs = path_of(cfg, "logs")
    logs.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(logs / f"{rid}.log", encoding="utf-8")
    fh.setFormatter(fmt)
    lg.addHandler(fh)
    _loggers[key] = lg
    return lg


def record_run(cfg: dict, stage: str, **info) -> None:
    """logs/runs.jsonl 에 스테이지 실행 1줄 추가 (행 수·소요·출력 경로·파라미터 스냅샷)."""
    logs = path_of(cfg, "logs")
    logs.mkdir(parents=True, exist_ok=True)
    line = {
        "ts": _dt.datetime.now().isoformat(timespec="seconds"),
        "run_id": cfg.get("run_id"),
        "config": Path(cfg.get("_config_path", "")).name,
        "stage": stage,
        "params": (cfg.get("params") or {}).get(stage),
        **info,
    }
    with open(logs / "runs.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
