"""__STAGE__ — __HINT__

입력  : 직전 스테이지 artifact
출력  : configs artifacts.__STAGE__  (원문 응답 + 파싱 결과 컬럼)
파라미터: configs params.__STAGE__  → prompt(프롬프트 파일명), text_col, id_col, limit
LLM   : configs llm → provider/model/temperature/max_workers. 캐시: cache/llm/ (키 = 모델+프롬프트+입력)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

from src.common.config import stage_params
from src.common.io import artifact_path, input_of, read_table, write_table
from src.common.llm import cost_summary, get_provider, load_prompt, map_cached
from src.common.log import get_logger
from src.common.report import add_metrics, add_samples
from src.common.schema import load_schema, validate

STAGE = "__STAGE__"
_JSON = re.compile(r"\{.*\}", re.S)


def parse_response(text: str) -> dict:
    """응답에서 JSON 객체를 뽑는다. 실패하면 raw 만 남겨 하류가 결측으로 처리하게 한다."""
    m = _JSON.search(text or "")
    if not m:
        return {"parse_ok": False}
    try:
        d = json.loads(m.group(0))
        return {"parse_ok": True, **{k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v) for k, v in d.items()}}
    except json.JSONDecodeError:
        return {"parse_ok": False}


def run(cfg: dict) -> Path:
    log = get_logger(cfg)
    p = stage_params(cfg, STAGE)
    src = input_of(cfg, STAGE)
    dst = artifact_path(cfg, STAGE)

    df = read_table(src)
    validate(df, load_schema(cfg, f"{STAGE}.in"), where=f"{STAGE}:in")
    id_col, text_col = p.get("id_col", "id"), p.get("text_col", "text")
    if (lim := p.get("limit")):
        df = df.head(int(lim))  # 개발 중 소량 시험용 — 설정에서만 바꾼다
    log.info(f"{STAGE}: {len(df):,}건 ← {src.name}")

    template, phash = load_prompt(cfg, p["prompt"])
    call = get_provider(cfg)
    items = [{"id": r[id_col], "text": r[text_col]} for r in df[[id_col, text_col]].to_dict("records")]
    # 템플릿의 자리표시자({text} 등)와 items 의 키가 일치해야 한다
    results = map_cached(cfg, call, template, items, tag=f"{STAGE}:{p['prompt']}@{phash}",
                         max_workers=int((cfg.get("llm") or {}).get("max_workers", 4)))

    parsed = pd.DataFrame([{id_col: r["id"], "llm_raw": r["text"], "llm_cached": r["cached"], **parse_response(r["text"])}
                           for r in results])
    out = df.merge(parsed, on=id_col, how="left")
    out["prompt_hash"] = phash

    validate(out, load_schema(cfg, f"{STAGE}.out"), where=f"{STAGE}:out")
    write_table(out, dst)
    cs = cost_summary(cfg, cfg.get("run_id"))
    # 리포트 지표: LLM 스테이지는 파싱 성공률·캐시 적중률이 핵심. 비용은 run.py 가 llm_cost.jsonl 에서 자동 집계.
    add_metrics(cfg, STAGE, parse_ok_rate=round(float(out["parse_ok"].mean()), 4) if "parse_ok" in out else None,
                cache_hit=int(out["llm_cached"].sum()), prompt_hash=phash)
    add_samples(cfg, STAGE, out, cols=[id_col, text_col, "llm_raw"])  # 원문 ↔ 응답을 나란히 — 정성 점검용
    log.info(f"{STAGE}: {len(out):,}행 → {dst.name} | 신규 호출 {cs['calls']} (in {cs.get('input_tokens', 0):,} / out {cs.get('output_tokens', 0):,} tok)")
    return dst
