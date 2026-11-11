"""LLM 호출 캐시·비용 로그 — 같은 (모델, 프롬프트, 입력) 은 두 번 과금하지 않는다.

캐시를 두는 이유: 추출 스테이지는 파이프라인에서 가장 비싸고 느리다. 하류 스테이지를
고치려고 재실행할 때마다 수천 건을 다시 호출하면 실험 속도와 비용 둘 다 무너진다.
캐시 키에 프롬프트 해시가 들어가므로 프롬프트를 고치면 자동으로 다시 호출된다
(= 프롬프트 버전 관리가 공짜로 따라온다).

사용법 (stage.py 안에서):
    from src.common.llm import load_prompt, cached_call, map_cached, get_provider
    tmpl, _ = load_prompt(cfg, p["prompt"])            # prompts/problem_solution_v1.md
    call = get_provider(cfg)                           # cfg['llm']['provider'] → 호출 함수
    rows = map_cached(cfg, call, tmpl, items, max_workers=cfg["llm"].get("max_workers", 4))

응답 파싱(JSON 등)은 스테이지 코드가 한다 — 캐시는 원문(text)만 보관한다.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Iterable

from .config import path_of

CallFn = Callable[[str, str, str], dict]  # (model, system_or_template, user_text) -> {"text":..., "usage":{...}}
_lock = threading.Lock()


# ---------------------------------------------------------------- 프롬프트
def load_prompt(cfg: dict, name: str) -> tuple[str, str]:
    """prompts/<name> 을 읽고 (본문, sha256 앞 12자리) 반환. 템플릿은 str.format 자리표시자 사용."""
    p = path_of(cfg, "prompts") / name
    text = p.read_text(encoding="utf-8")
    return text, hashlib.sha256(text.encode()).hexdigest()[:12]


_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def render(template: str, **fields) -> str:
    """{abstract} 같은 자리표시자만 채운다. JSON 예시 등 다른 중괄호는 건드리지 않는다
    (str.format 을 쓰면 프롬프트 안의 {"problem": ...} 에서 KeyError 가 난다)."""
    return _PLACEHOLDER.sub(lambda m: str(fields[m.group(1)]) if m.group(1) in fields else m.group(0), template)


# ---------------------------------------------------------------- 캐시
def _key(model: str, prompt: str, payload) -> str:
    blob = json.dumps({"m": model, "p": prompt, "x": payload}, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def cached_call(cfg: dict, call: CallFn, prompt: str, payload, *, model: str | None = None,
                system: str | None = None, tag: str = "") -> dict:
    """캐시 조회 → 없으면 호출 → 저장. 반환: {"text", "usage", "cached", "key"}.

    payload: 입력 레코드(dict/str). 캐시 키·user 메시지에 쓰인다.
    prompt : 렌더링된 user 프롬프트 또는 템플릿(캐시 키에 포함).
    """
    llm = cfg.get("llm") or {}
    model = model or llm.get("model", "unknown")
    cache_dir = path_of(cfg, "cache") / "llm"
    cache_dir.mkdir(parents=True, exist_ok=True)
    k = _key(model, prompt + (system or ""), payload)
    f = cache_dir / f"{k}.json"
    if f.exists():
        rec = json.loads(f.read_text(encoding="utf-8"))
        rec["cached"] = True
        return rec

    t0 = time.time()
    resp = call(model, system or "", prompt)
    rec = {
        "key": k, "model": model, "tag": tag, "text": resp.get("text", ""),
        "usage": resp.get("usage", {}), "latency_s": round(time.time() - t0, 2),
        "ts": _dt.datetime.now().isoformat(timespec="seconds"), "cached": False,
    }
    f.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    _log_cost(cfg, rec)
    return rec


def _log_cost(cfg: dict, rec: dict) -> None:
    logs = path_of(cfg, "logs")
    logs.mkdir(parents=True, exist_ok=True)
    line = {k: rec.get(k) for k in ("ts", "run_id", "model", "tag", "usage", "latency_s", "key")}
    line["run_id"] = cfg.get("run_id")
    with _lock, open(logs / "llm_cost.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, ensure_ascii=False) + "\n")


def map_cached(cfg: dict, call: CallFn, template: str, items: Iterable[dict], *,
               id_field: str = "id", max_workers: int = 4, system: str | None = None,
               tag: str = "", progress: bool = True) -> list[dict]:
    """items 각각을 template 에 렌더링해 호출. 순서 보존. 각 결과에 id 와 cached 여부 포함.

    item 의 키가 템플릿 자리표시자와 일치해야 한다 (예: {"id": ..., "abstract": ...}).
    """
    items = list(items)
    out: list[dict | None] = [None] * len(items)

    def work(i: int, it: dict) -> tuple[int, dict]:
        prompt = render(template, **it)
        rec = cached_call(cfg, call, prompt, it, system=system, tag=tag)
        return i, {"id": it.get(id_field), "text": rec["text"], "cached": rec["cached"], "usage": rec.get("usage", {})}

    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [ex.submit(work, i, it) for i, it in enumerate(items)]
        for fu in as_completed(futs):
            i, r = fu.result()
            out[i] = r
            done += 1
            if progress and (done % 50 == 0 or done == len(items)):
                hit = sum(1 for o in out if o and o["cached"])
                print(f"  llm {done}/{len(items)} (cache hit {hit})", flush=True)
    return out  # type: ignore[return-value]


def cost_summary(cfg: dict, run_id: str | None = None) -> dict:
    """logs/llm_cost.jsonl 집계 — CARD.md 의 '비용' 란에 적는 숫자."""
    f = path_of(cfg, "logs") / "llm_cost.jsonl"
    if not f.exists():
        return {"calls": 0}
    calls, tin, tout = 0, 0, 0
    for line in f.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if run_id and rec.get("run_id") != run_id:
            continue
        calls += 1
        u = rec.get("usage") or {}
        tin += int(u.get("input_tokens") or u.get("prompt_tokens") or 0)
        tout += int(u.get("output_tokens") or u.get("completion_tokens") or 0)
    return {"calls": calls, "input_tokens": tin, "output_tokens": tout}


# ---------------------------------------------------------------- 제공자 어댑터
def get_provider(cfg: dict) -> CallFn:
    """cfg['llm']['provider'] 에 따라 호출 함수 반환. 키가 없거나 provider=mock 이면 mock."""
    llm = cfg.get("llm") or {}
    name = (llm.get("provider") or "mock").lower()
    temperature = llm.get("temperature", 0)
    max_tokens = llm.get("max_tokens", 1024)

    if name == "openai" and os.environ.get("OPENAI_API_KEY"):
        from openai import OpenAI  # 지연 import — 설치 안 돼 있어도 다른 provider 는 동작

        client = OpenAI()

        def call(model, system, user):
            msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}]
            r = client.chat.completions.create(model=model, messages=msgs, temperature=temperature, max_tokens=max_tokens)
            u = r.usage
            return {"text": r.choices[0].message.content or "",
                    "usage": {"input_tokens": u.prompt_tokens, "output_tokens": u.completion_tokens}}
        return call

    if name == "anthropic" and os.environ.get("ANTHROPIC_API_KEY"):
        import anthropic

        client = anthropic.Anthropic()

        def call(model, system, user):
            r = client.messages.create(model=model, max_tokens=max_tokens, temperature=temperature,
                                       system=system or anthropic.NOT_GIVEN,
                                       messages=[{"role": "user", "content": user}])
            return {"text": "".join(b.text for b in r.content if getattr(b, "type", "") == "text"),
                    "usage": {"input_tokens": r.usage.input_tokens, "output_tokens": r.usage.output_tokens}}
        return call

    if name not in ("mock", "openai", "anthropic"):
        raise ValueError(f"알 수 없는 llm.provider: {name}")
    if name != "mock":
        print(f"  [llm] {name.upper()}_API_KEY 없음 → mock provider 사용", flush=True)
    return mock_call


def mock_call(model: str, system: str, user: str) -> dict:
    """키 없이 파이프라인 배선을 검증하기 위한 결정적(deterministic) 가짜 응답.

    user 프롬프트 해시로 응답을 만들어, 같은 입력이면 항상 같은 출력 → 동일성 검증에 쓸 수 있다.
    실제 추출 스키마에 맞는 JSON 이 필요하면 스테이지에서 mock 결과를 후처리하거나 이 함수를 바꾼다.
    """
    h = hashlib.md5(user.encode()).hexdigest()[:8]
    text = json.dumps({"mock": True, "hash": h, "echo": user[-120:].strip()}, ensure_ascii=False)
    return {"text": text, "usage": {"input_tokens": len(user) // 4, "output_tokens": len(text) // 4}}
