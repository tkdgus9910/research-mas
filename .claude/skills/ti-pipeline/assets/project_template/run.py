#!/usr/bin/env python3
"""스테이지 선택 실행기.

    python run.py                          # configs/default.yaml 로 전체 실행
    python run.py --only s3_extract        # 한 스테이지만 (접두사 s3 도 됨)
    python run.py --from s2 --to s4        # 구간 실행
    python run.py -c configs/exp01.yaml --tag exp01
    python run.py --set params.s4_analyze.n_clusters=12 --only s4
    python run.py --list                   # 스테이지 목록·산출 경로·존재 여부
    python run.py --no-report              # 스테이지 리포트 생성 생략

각 스테이지는 src/<stage>/stage.py 의 run(cfg) -> Path 하나만 노출한다.
스테이지 간 전달은 파일(configs 의 artifacts)로만 한다.
스테이지가 끝날 때마다 outputs/<run_id>/reports/<stage>.md 리포트를 자동 생성한다(src/common/report.py).
"""
from __future__ import annotations

import argparse
import importlib
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.common import report as _report  # noqa: E402
from src.common.config import load_config, path_of, stages  # noqa: E402
from src.common.io import artifact_path  # noqa: E402
from src.common.log import get_logger, new_run_id, record_run  # noqa: E402


def _index(order: list[str], key: str | None) -> int | None:
    if key is None:
        return None
    for i, s in enumerate(order):
        if s == key or s.split("_", 1)[0] == key:
            return i
    raise SystemExit(f"알 수 없는 스테이지: {key} (가능: {', '.join(order)})")


def select(order: list[str], only: str | None, frm: str | None, to: str | None) -> list[str]:
    if only:
        return [order[_index(order, only)]]
    a = _index(order, frm) or 0
    b = _index(order, to)
    b = len(order) - 1 if b is None else b
    return order[a:b + 1]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-c", "--config", default="configs/default.yaml")
    ap.add_argument("--only")
    ap.add_argument("--from", dest="frm")
    ap.add_argument("--to")
    ap.add_argument("--tag", default="", help="run_id 접미사 (실험 이름)")
    ap.add_argument("--run-id", help="기존 run_id 재사용 (outputs/<run_id>/ 에 이어서 기록)")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VAL", help="설정 오버라이드 (점 표기)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--no-report", action="store_true", help="스테이지 리포트(outputs/<run_id>/reports/) 생성 생략")
    a = ap.parse_args(argv)

    cfg = load_config(a.config, sets=a.set)
    if a.no_report:
        cfg.setdefault("report", {})["enabled"] = False
    order = stages(cfg)
    if not order:
        raise SystemExit("configs 에 stages 목록이 없음")

    if a.list:
        for s in order:
            try:
                p = artifact_path(cfg, s)
                mark = "✔" if p.exists() else "·"
                print(f"  {mark} {s:<16} → {p.relative_to(cfg['_root'])}")
            except KeyError:
                print(f"  ? {s:<16} → (artifacts 미선언)")
        return 0

    cfg["run_id"] = a.run_id or new_run_id(a.tag)
    log = get_logger(cfg)
    todo = select(order, a.only, a.frm, a.to)
    log.info(f"run_id={cfg['run_id']} config={Path(cfg['_config_path']).name} stages={todo}")

    for name in todo:
        mod = importlib.import_module(f"src.{name}.stage")
        t0 = time.time()
        log.info(f"▶ {name}")
        try:
            out = mod.run(cfg)
        except Exception as e:  # 어느 스테이지에서 죽었는지 명확히 남긴다
            secs = round(time.time() - t0, 1)
            record_run(cfg, name, status="error", error=repr(e), seconds=secs)
            _report.safe_write(cfg, name, None, seconds=secs, status="error", error=repr(e))
            log.error(f"✘ {name}: {e!r}")
            raise
        secs = round(time.time() - t0, 1)
        record_run(cfg, name, status="ok", output=str(out), seconds=secs)
        rep = _report.safe_write(cfg, name, out, seconds=secs, status="ok")
        log.info(f"✔ {name} → {out} ({secs}s)" + (f" | 리포트 {rep.relative_to(cfg['_root'])}" if rep else ""))
    if not a.no_report:
        idx = path_of(cfg, "outputs") / cfg["run_id"] / "reports" / "index.md"
        if idx.exists():
            log.info(f"리포트 묶음 → {idx.relative_to(cfg['_root'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
