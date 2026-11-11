#!/usr/bin/env python3
"""ti-pipeline 프로젝트 골격 생성.

    python scaffold.py <project_dir> [--stages collect,clean,extract,analyze,report]
                       [--author 박상현] [--name 프로젝트명] [--raw-file patents.csv] [--force]

스테이지 표기: `이름` 또는 `이름:종류`. 종류 = first | middle | llm | report (생략 시 자동 추정:
첫 스테이지=first, 마지막=report, extract/classify/label/summarize/llm 이름=llm, 나머지 middle).

    --stages collect,clean,extract:llm,analyze,report

생성물: configs/default.yaml, src/common/*, src/sN_<stage>/{stage.py,CARD.md}, schemas/*.yaml 초안,
        prompts/ (llm 스테이지가 있으면 예시 프롬프트), run.py, README.md, data/·outputs/·cache/·logs/·notebooks/·legacy/
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ASSETS = HERE.parent / "assets"
TEMPLATE = ASSETS / "project_template"
STAGE_T = ASSETS / "stage_templates"

LLM_NAMES = {"extract", "classify", "label", "summarize", "llm", "annotate", "tag", "score"}
REPORT_NAMES = {"report", "viz", "visualize", "figure", "figures", "plot", "deliver"}

HINTS = {
    "collect": "원천 데이터 수집·로드 (KIPRIS/OpenAlex/Scopus API 또는 다운로드 파일)",
    "clean": "중복 제거·정규화·필터링·컬럼 표준화",
    "extract": "LLM 기반 정보 추출 (기술 문제·해결·효과 등)",
    "analyze": "정량 분석 (군집·네트워크·시계열·지표 산출)",
    "report": "표·그림·산출 보고물 생성",
}


def parse_stages(spec: str) -> list[tuple[str, str]]:
    items = [s.strip() for s in spec.split(",") if s.strip()]
    out = []
    for i, it in enumerate(items):
        name, _, kind = it.partition(":")
        name = re.sub(r"[^a-z0-9_]", "_", name.lower())
        if not kind:
            if i == 0:
                kind = "first"
            elif i == len(items) - 1 and name in REPORT_NAMES:
                kind = "report"
            elif name in LLM_NAMES:
                kind = "llm"
            elif name in REPORT_NAMES:
                kind = "report"
            else:
                kind = "middle"
        if kind not in {"first", "middle", "llm", "report"}:
            sys.exit(f"알 수 없는 스테이지 종류: {kind}")
        out.append((f"s{i + 1}_{name}", kind))
    return out


def render(text: str, **kw) -> str:
    for k, v in kw.items():
        text = text.replace(f"__{k}__", v)
    return text


def artifact_for(i: int, n: int, stage: str, kind: str) -> str:
    if kind == "report":
        return "outputs"
    if i == n - 2 or (i == n - 1):
        return f"data/processed/{stage}.parquet"
    return f"data/interim/{stage}.parquet"


def write(p: Path, text: str, force: bool) -> None:
    if p.exists() and not force:
        print(f"  skip (exists) {p}")
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    print(f"  + {p}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project_dir")
    ap.add_argument("--stages", default="collect,clean,extract,analyze,report")
    ap.add_argument("--author", default="")
    ap.add_argument("--name", default="")
    ap.add_argument("--raw-file", default="CHANGE_ME.csv", help="첫 스테이지가 읽을 data/raw/ 파일명")
    ap.add_argument("--force", action="store_true", help="기존 파일 덮어쓰기")
    a = ap.parse_args()

    root = Path(a.project_dir).resolve()
    name = a.name or root.name
    stages = parse_stages(a.stages)
    n = len(stages)
    print(f"프로젝트: {root}  스테이지: {[s for s, _ in stages]}")

    # 디렉터리
    for d in ["configs", "prompts", "data/raw", "data/interim", "data/processed", "outputs",
              "cache/llm", "logs", "schemas", "notebooks", "legacy", "src"]:
        (root / d).mkdir(parents=True, exist_ok=True)
    for d in ["data/raw", "data/interim", "data/processed", "outputs"]:
        (root / d / ".gitkeep").touch()

    # 공통 템플릿 복사 (src/common, run.py, .gitignore)
    for src in TEMPLATE.rglob("*"):
        if src.is_dir() or src.name == "README.md":
            continue
        dst = root / src.relative_to(TEMPLATE)
        if dst.exists() and not a.force:
            print(f"  skip (exists) {dst}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        print(f"  + {dst}")

    # 스테이지
    rows = []
    for i, (stage, kind) in enumerate(stages):
        short = stage.split("_", 1)[1]
        hint = HINTS.get(short, f"{short} 단계")
        tmpl = (STAGE_T / f"stage_{kind}.py").read_text(encoding="utf-8")
        sdir = root / "src" / stage
        write(sdir / "__init__.py", "", a.force)
        write(sdir / "stage.py", render(tmpl, STAGE=stage, HINT=hint), a.force)
        write(sdir / "CARD.md", render((STAGE_T / "CARD.md").read_text(encoding="utf-8"), STAGE=stage, HINT=hint), a.force)
        if kind != "report":
            write(root / "schemas" / f"{stage}.out.yaml",
                  f"# {stage} 출력 계약 — 열을 선언하면 경계 검증이 켜진다. 초안 생성:\n"
                  f"#   python -m src.common.schema infer {artifact_for(i, n, stage, kind)} --name {stage}.out\n"
                  f"name: {stage}.out\ndescription: ''\nmin_rows: 1\ncolumns: {{}}\nextra_columns: allow\n"
                  f"# 리포트 불변식 — 지표(자동: rows_out·rows_delta_pct·null_rate_max·llm_calls… / add_metrics 이름)가\n"
                  f"# 범위를 벗어나거나 직전 실행 대비 너무 변하면 reports/{stage}.md ⑥ 에 ⚠. 예:\n"
                  f"#   - {{metric: rows_out, min: 100}}\n"
                  f"#   - {{metric: parse_ok_rate, min: 0.95, max_rel_delta: 0.10}}\n"
                  f"invariants: []\n", a.force)
        rows.append(f"| {stage} | {kind} | {hint} | `{artifact_for(i, n, stage, kind)}` |")

    # 프롬프트 예시
    if any(k == "llm" for _, k in stages):
        write(root / "prompts" / "extract_v1.md",
              "You are a patent analyst. From the text below, extract the technical problem the invention "
              "addresses and the core solution.\n\nReturn JSON only:\n"
              '{"problem": "<one sentence>", "solution": "<one sentence>", "confidence": <0-1>}\n\n'
              "Text (id={id}):\n{text}\n", a.force)

    # configs/default.yaml
    params_lines = []
    for i, (stage, kind) in enumerate(stages):
        if kind == "first":
            params_lines.append(f"  {stage}:\n    raw_file: {a.raw_file}")
        elif kind == "llm":
            params_lines.append(f"  {stage}:\n    prompt: extract_v1.md\n    id_col: id\n    text_col: text\n    limit: null   # 개발 중엔 20 처럼 소량으로")
        elif kind == "report":
            params_lines.append(f"  {stage}:\n    deliverable_title: {name} 분석결과")
        else:
            params_lines.append(f"  {stage}: {{}}")
    cfg = f"""# {name} — 기본 설정. 실험 변형은 이 파일을 extends 로 상속한 새 YAML 로 만든다.
#   configs/exp01.yaml:  extends: default.yaml / params: {{ s4_analyze: {{ n_clusters: 12 }} }}
project:
  name: {name}
  author: {a.author}
  seed: 42

stages: [{', '.join(s for s, _ in stages)}]

paths:
  raw: data/raw
  interim: data/interim
  processed: data/processed
  outputs: outputs
  cache: cache
  logs: logs
  prompts: prompts
  schemas: schemas

# 스테이지 간 계약: 각 스테이지가 내놓는 파일. 다음 스테이지는 이 경로를 입력으로 읽는다.
artifacts:
""" + "\n".join(f"  {s}: {artifact_for(i, n, s, k)}" for i, (s, k) in enumerate(stages)) + f"""

llm:
  provider: mock        # openai | anthropic | mock  (키 환경변수 없으면 자동 mock)
  model: gpt-4o-mini
  temperature: 0
  max_tokens: 1024
  max_workers: 4

# 스테이지 리포트: 스테이지가 끝날 때마다 outputs/<run_id>/reports/<stage>.md (메타·입출력·지표·표본·직전 대비·확인 필요)
report:
  enabled: true
  sample_n: 5           # ④ 표본 행 수 (seed = project.seed)
  hist_max: 3           # ③ 수치 열 히스토그램 최대 개수 (0 이면 그림 생략)
  compare_previous: true

params:
""" + "\n".join(params_lines) + "\n"
    write(root / "configs" / "default.yaml", cfg, a.force)

    # README
    readme = (TEMPLATE / "README.md").read_text(encoding="utf-8")
    table = "| 스테이지 | 종류 | 역할 | 산출 |\n|---|---|---|---|\n" + "\n".join(rows)
    write(root / "README.md", render(readme, PROJECT=name, STAGE_TABLE=table), a.force)

    print("\n다음 단계:")
    print(f"  1) data/raw/ 에 원본 넣고 configs/default.yaml 의 params.{stages[0][0]}.raw_file 수정")
    print(f"  2) 스테이지 순서대로 stage.py 의 '핵심 로직' 채우기 → python run.py --only {stages[0][0]}")
    print("     → outputs/<run_id>/reports/<stage>.md 를 읽고(표본·분포·⚠) 다음 스테이지로")
    print("  3) 산출 확인 후 python -m src.common.schema infer <산출파일> --name <stage>.out > schemas/<stage>.out.yaml")
    print("     → 같은 파일의 invariants: 에 이 스테이지가 지켜야 할 범위(행 수·비율) 1–3개 선언")


if __name__ == "__main__":
    main()
