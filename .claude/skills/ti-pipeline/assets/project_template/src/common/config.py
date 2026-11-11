"""설정 로더 — configs/*.yaml 을 읽고 경로를 프로젝트 루트 기준 절대경로로 해석한다.

코드 안에 상수·경로를 두지 않는 이유: 실험 변형(데이터 교체, 파라미터 스윕)을
코드 diff 없이 YAML 교체만으로 하기 위해서다.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

_ENV = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


def project_root(start: Path | None = None) -> Path:
    """run.py 또는 configs/ 가 있는 가장 가까운 상위 디렉터리."""
    p = (start or Path.cwd()).resolve()
    for cand in [p, *p.parents]:
        if (cand / "run.py").exists() or (cand / "configs").is_dir():
            return cand
    return p


def _expand_env(v: Any) -> Any:
    if isinstance(v, str):
        return _ENV.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), v)
    if isinstance(v, dict):
        return {k: _expand_env(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_expand_env(x) for x in v]
    return v


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _apply_sets(cfg: dict, sets: list[str]) -> dict:
    """--set a.b.c=value 형태 오버라이드. 값은 YAML 로 파싱(숫자·리스트·bool 인식)."""
    for item in sets or []:
        key, _, raw = item.partition("=")
        node = cfg
        parts = key.split(".")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = yaml.safe_load(raw)
    return cfg


def load_config(path: str | Path = "configs/default.yaml", sets: list[str] | None = None) -> dict:
    """YAML 로드 → `extends:` 상속 병합 → 환경변수 치환 → --set 오버라이드 → 경로 절대화."""
    path = Path(path)
    root = project_root(path.parent if path.is_absolute() else None)
    if not path.is_absolute():
        path = root / path
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    if cfg.get("extends"):
        base = load_config(root / "configs" / cfg.pop("extends"))
        base.pop("_root", None)
        base.pop("_config_path", None)
        cfg = _deep_merge(base, cfg)
    cfg = _expand_env(cfg)
    cfg = _apply_sets(cfg, sets or [])
    cfg["_root"] = str(root)
    cfg["_config_path"] = str(path)
    return cfg


def root_of(cfg: dict) -> Path:
    return Path(cfg["_root"])


def path_of(cfg: dict, key: str) -> Path:
    """cfg['paths'][key] 를 프로젝트 루트 기준 Path 로. 없으면 관례 경로 사용."""
    defaults = {
        "raw": "data/raw", "interim": "data/interim", "processed": "data/processed",
        "outputs": "outputs", "cache": "cache", "logs": "logs",
        "prompts": "prompts", "schemas": "schemas",
    }
    rel = (cfg.get("paths") or {}).get(key, defaults.get(key, key))
    p = Path(rel)
    return p if p.is_absolute() else root_of(cfg) / p


def stage_params(cfg: dict, stage: str) -> dict:
    """params.<stage> 블록. 없으면 빈 dict — 스테이지 코드는 p.get('키', 기본값) 으로 읽는다."""
    return dict((cfg.get("params") or {}).get(stage) or {})


def stages(cfg: dict) -> list[str]:
    return list(cfg.get("stages") or [])
