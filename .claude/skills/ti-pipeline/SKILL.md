---
name: "ti-pipeline"
description: 기술인텔리전스(특허·논문·정책보고서 등 데이터사이언스) 분석 코드를 스테이지 단위 모듈형 파이프라인으로 설계·구현하거나 기존 코드를 그 구조로 리팩터링한다. 인풋 data/ · 코드 src/ · 아웃풋 outputs/ 분리, YAML 설정 외부화, 스테이지 경계 스키마 검증, LLM 호출 캐시·비용 로그, 산출물 명명 규칙, 스테이지별 실행 리포트(입출력·지표·표본·직전 실행 대비·불변식 ⚠) 자동 생성을 scaffold 스크립트로 한 번에 깔고, 기존 노트북·스크립트는 seam_finder 로 분할 지점을 찾아 옮긴 뒤 verify_equivalence 로 출력 동일성을 검증한다. "모듈화해줘", "파이프라인(구조)으로 만들어줘", "인풋·코드·아웃풋 분리", "노트북 정리", "코드 구조 잡아줘", "재현 가능하게", "나중에 데이터만 바꿔 끼울 수 있게", "프로젝트 골격", "단계별 리포트/중간 결과를 보기 쉽게", 수집→전처리→LLM 추출→분석→시각화 흐름의 분석 코드를 새로 짜달라는 요청, .py/.ipynb 를 주며 정리·리팩터링을 요청할 때 반드시 사용. 단일 함수 버그 수정이나 한 줄 질문에는 쓰지 않는다.
---

# ti-pipeline — 스테이지형 분석 파이프라인

## 왜 이 구조인가

연구 분석 코드는 한 파일에서 시작해 "데이터만 바꿔서 다시" "군집 수만 바꿔서 다시" "LLM 프롬프트만 고쳐서 다시"를 반복하다 무너진다. 재실행 단위가 '파일 전체'뿐이기 때문이다. 이 스킬은 재실행 단위를 **스테이지**로 바꾼다. 스테이지 사이는 **파일**로만 통하므로, 어느 단계든 그 지점부터 다시 돌릴 수 있고 중간 산출을 열어볼 수 있다. 원칙은 다섯이다.

1. **스테이지 1개 = 폴더 1개 = `run(cfg) -> Path` 1개.** 스테이지 간 전달은 메모리 객체가 아니라 파일(`configs: artifacts`). 부분 재실행과 중간 검수가 가능해진다.
2. **상수·경로·모델명은 `configs/*.yaml`, 프롬프트는 `prompts/*.md`.** 실험 변형이 코드 diff 가 아니라 YAML 교체가 된다.
3. **스테이지 경계에서 스키마 검증.** 상류 컬럼명이 바뀌면 하류는 KeyError 가 아니라 NaN 으로 조용히 틀어진다. 경계에서 잡아야 디버깅 범위가 한 스테이지로 좁혀진다.
4. **LLM 호출은 `src/common/llm.py` 를 통해서만.** 키 = (모델, 프롬프트, 입력) 해시 → 하류를 고치려고 재실행할 때 재과금이 없고, 프롬프트를 바꾸면 자동으로 다시 호출된다.
5. **스테이지마다 리포트 1장이 자동으로 남는다.** `run.py` 가 스테이지가 끝날 때마다 `outputs/<run_id>/reports/<stage>.md` 를 쓴다 — ① 실행 메타 ② 입출력 요약 ③ 핵심 지표·분포 ④ 표본 ⑤ 직전 실행 대비 ⑥ 확인 필요(⚠). 사람은 parquet 을 열지 않고 이 리포트로 중간 산출을 검수하고, 스테이지가 지켜야 할 범위는 `schemas/*.out.yaml` 의 `invariants` 로 선언해 위반을 ⑥ 에서 받는다. 상류 변경이 하류 수치를 조용히 틀어 놓는 사고(귀속률 90%→74% 같은)를 다음 단계가 아니라 그 스테이지에서 잡는 장치이고, 정지점 보고는 이 파일을 그대로 쓴다.

오버엔지니어링은 금지다. 1인 연구코드에 Airflow·DVC·Docker 는 필요 없다. 파일 + YAML + `run.py` 로 충분하다.

## 생성되는 구조

```
project/
├─ configs/default.yaml   # stages 순서, artifacts(스테이지별 산출 파일), paths, llm, params.<stage>
├─ prompts/               # LLM 프롬프트 .md (자리표시자 {text}, {id})
├─ schemas/               # <stage>.out.yaml — 열·dtype·required·unique + invariants(리포트 불변식)
├─ data/{raw,interim,processed}/   # [인풋] raw 는 읽기 전용
├─ src/
│  ├─ common/             # config · io · schema · llm · log · plot · report (복사되는 공통 라이브러리)
│  └─ s1_collect/ … s5_report/   # [코드] stage.py(run 함수) + CARD.md
├─ outputs/<run_id>/      # [아웃풋] 표·그림·보고물. 보고물 파일명 = 제목(YYMMDD, NN, 저자).ext
│  └─ reports/            #   스테이지별 실행 리포트 <stage>.md(+ .report.json, _hist.png) 와 index.md — 자동 생성
├─ cache/llm/  logs/  notebooks/  legacy/
└─ run.py                 # python run.py [--only s3] [--from s2 --to s4] [-c configs/exp01.yaml --tag exp01] [--no-report]
```

공통 라이브러리 API 는 짧다. 스테이지 코드에서 쓰는 것은 이 정도다:
`stage_params(cfg, STAGE)` · `input_of(cfg, STAGE)` · `artifact_path(cfg, STAGE)` · `read_table/write_table` · `validate(df, load_schema(cfg, f"{STAGE}.out"))` · `load_prompt / get_provider / map_cached` · `deliverable_path(cfg, 제목, "xlsx")` · `setup_matplotlib()` · 리포트에 올릴 것이 있을 때만 `add_metrics(cfg, STAGE, **지표)` / `add_table` / `add_samples` / `warn`. 상세는 [references/conventions.md](references/conventions.md).

## 모드 A — 새로 짤 때

1. **스테이지 확정 (사용자와 한 번).** 연구질문 → 데이터 소스 → 최종 산출(표·그림)을 듣고 스테이지 3–5개를 제안한다. 경계 기준은 '기능'이 아니라 **파일이 생기는 지점**이다(수집 결과·정제본·추출본·분석표·보고물). LLM 호출이 있으면 그 단계는 반드시 독립 스테이지로 둔다(가장 비싸고 느리므로).
2. **골격 생성.** `python <skill>/scripts/scaffold.py <project> --stages collect,clean,extract,analyze,report --author <이름> --raw-file <원본파일명>`. 스테이지 이름 뒤에 `:llm`/`:report` 로 템플릿 종류를 지정할 수 있다. 원본은 `data/raw/` 에 복사해 두고 이후 손대지 않는다.
3. **스테이지별 순차 구현 — 한 번에 전체를 쓰지 않는다.** 스테이지 하나의 `stage.py` 핵심 로직 블록을 채우고 `python run.py --only sN` 으로 돌린 뒤, 산출 parquet 대신 `outputs/<run_id>/reports/sN_xxx.md` 를 읽는다(②행 증감·③분포·④표본·⑥⚠). 사용자에게 중간 결과를 보고할 때도 이 리포트를 요약해 보여 준다. 다음 단계의 설계는 앞 단계의 실제 데이터 모양을 보고 하는 것이 맞기 때문이다. 개발 중 LLM 스테이지는 `params.<stage>.limit: 20` 으로 소량만 돌린다.
4. **스키마·불변식 선언.** 스테이지가 처음 성공하면 `python -m src.common.schema infer <산출파일> --name sN_xxx.out > schemas/sN_xxx.out.yaml` 로 초안을 뽑고 required/unique/allowed 를 다듬는다. 같은 파일의 `invariants:` 에 그 스테이지가 지켜야 할 범위를 1–3개 적는다(행 수 하한, 파싱 성공률, 귀속률, 직전 대비 변동폭). 이후 실행부터 경계 검증과 ⑥ 불변식 검사가 켜진다.
5. **지표 올리기.** 스테이지 고유의 숫자(탈락 사유별 건수, 병합률, 도달률 등)는 `add_metrics(cfg, STAGE, ...)` 한 줄로 ③ 에 올린다. 자동 지표(행 증감·결측·LLM 비용)로 충분하면 생략한다. 지표는 스테이지당 2–5개 — 리포트가 길어지면 읽지 않게 된다.
6. **CARD.md 갱신.** 목적·입력·출력·파라미터·비용·리포트 지표(왜 이 숫자를 보는지)를 적는다. `최근 실행` 행은 리포트가 자동으로 채운다. 이 문서가 논문 Method 절과 재현성 자료의 원재료다.
7. 끝나면 [references/checklist.md](references/checklist.md) 로 점검하고 `python run.py` 전체 실행으로 마무리한다. `reports/index.md` 의 '확인 필요' 가 0 인지 본다.

## 모드 B — 기존 코드를 옮길 때

1. **기준 출력 확보.** 원본을 그대로 한 번 실행해 산출물을 `legacy/baseline_outputs/` 에 보관한다(이미 산출물이 있으면 그것을 복사). LLM 호출이 포함되어 있고 응답이 저장되어 있지 않다면 이 실행이 유일한 기준이므로, 응답 원문을 반드시 파일로 남긴다.
2. **분할 지점 탐지.** `python <skill>/scripts/seam_finder.py <file.py|ipynb> -o seams.md --json seams.json`. 보고서의 구간표(스테이지 후보·입출력 변수·파일 I/O)와 외부화 후보(상수·경로·프롬프트·하이퍼파라미터), 주의사항(시드 미지정, 전역 상수 참조, 셀 순서 의존)을 읽는다. 구간표는 제안이다 — 1–2문장짜리 구간은 이웃에 합치고, 최종 스테이지 목록을 사용자에게 한 번 확인받는다(사용자가 자리에 없으면 보고서 제안대로 간다).
3. **골격 생성 후 이동.** scaffold 로 구조를 만들고 구간별 코드를 해당 `stage.py` 의 핵심 로직 자리로 옮긴다. 옮기면서:
   - 구간의 '입력 변수' → `read_table(input_of(cfg, STAGE))`, '출력 변수' → `write_table(out, artifact_path(cfg, STAGE))`. 여러 변수가 다음 구간으로 넘어가면 한 표로 합치거나(열 추가) 표가 둘 이상 필요하면 `artifacts` 에 디렉터리를 지정하고 그 안에 여러 파일을 쓴다.
   - 상수·경로 → `configs/default.yaml` 의 `params.<stage>` / `paths`, 함수 안에서 전역 상수를 읽던 곳은 인자로 받게 바꾼다.
   - 프롬프트 문자열 → `prompts/<name>_v1.md`, 직접 API 호출 → `get_provider(cfg)` + `map_cached(...)`. 원본의 mock/규칙 기반 분기가 있으면 `llm.provider: mock` 으로 두되, 원본과 **같은 결과**를 내도록 원본의 mock 로직을 그대로 가져온다(캐시 레이어의 `mock_call` 은 배선 검증용이지 원본 대체가 아니다).
   - 그림·엑셀 → report 스테이지, `outputs/<run_id>/` 로. 보고물 파일명은 `deliverable_path()`.
   - 리스트 컬럼(예: 출원인 분해)은 parquet 이 그대로 보존하므로 문자열 join 으로 바꾸지 않는다.
4. **동일성 검증.** `python run.py` 로 새 파이프라인을 돌리고 `python <skill>/scripts/verify_equivalence.py legacy/baseline_outputs <새 산출 디렉터리> --key <id컬럼> --map old.csv=../../data/interim/s3_extract.parquet --ignore trend.png,network.png`. 표는 행 순서·포맷·추가 컬럼을 무시하고 값으로 비교한다(중간 산출 parquet 도 `--map` 으로 원본 csv 와 짝지을 수 있다). 원본 산출에 무의미한 컬럼명(`0`, `Unnamed: 0` — `reset_index()` 흔적)이 있으면 새 코드에서는 제대로 이름을 붙이고 `--col-map 0=weighted_degree` 로 비교한다. 불일치가 나면 원인은 대개 넷 중 하나다: 시드, dict/set 순회 순서, 필터 순서(중복 제거 전후), LLM 비결정성(캐시된 응답으로만 비교). 그림(png)은 바이트가 달라지는 게 정상이므로 원본과 새 그림을 둘 다 열어 눈으로 비교하고 `--ignore` 로 뺀다. 불일치 지점을 좁힐 때는 스테이지 리포트 ②(행 증감)·⑤(직전 실행 대비)가 어느 스테이지에서 숫자가 갈라졌는지 먼저 알려 준다.
5. **원본 보관.** 원본 파일은 `legacy/` 로 옮기고 삭제하지 않는다. README 에 "원본 → 스테이지" 대응표를 한 줄씩 남긴다.
6. **불변식 이식.** 원본 코드·노트북에 흩어져 있던 "이 값이 이 범위여야 한다" 류의 assert·눈대중 점검(행 수, 비율, 귀속률 등)은 `schemas/<stage>.out.yaml` 의 `invariants` 로 옮긴다. 그래야 다음 실험부터 리포트 ⑥ 이 대신 본다.

## 작동 여부를 가르는 판단들

- **스테이지는 보통 3–5개.** 함수마다 스테이지를 만들면 config 와 artifacts 가 비대해지고, 2개면 재실행 단위가 너무 크다. 기준은 "이 파일을 열어 확인하고 싶은가"다.
- **노트북은 탐색·시각화 전용.** 로직은 `src/` 에 두고 노트북 셀은 `from src.s3_extract.stage import run` 한 줄이면 성공이다. 노트북을 그대로 "정리"하는 것은 이 스킬의 목표가 아니다.
- **`limit` 과 `provider: mock` 은 설정으로만.** 코드에 `if DEBUG:` 를 심지 않는다. 실험 설정은 `configs/exp01.yaml` 에 `extends: default.yaml` 로 상속해 차이만 적는다.
- **첫 스테이지만 raw 를 읽는다.** 중간 스테이지가 `data/raw/` 를 직접 읽으면 계약이 깨진다. 보조 데이터(사전, 룩업표)가 필요하면 `paths` 에 선언하고 `path_of(cfg, "lexicon")` 으로 읽는다.
- **마지막에 전체 실행 1회.** 스테이지별로 성공했어도 `python run.py` 로 끝까지 한 번 돌려 run_id 하나에 모든 산출이 묶이는지 본다.
- **리포트는 고정 양식, 지표는 적게.** 6블록 순서와 지표명은 실행 간에 바꾸지 않는다 — ⑤ 직전 대비는 이름이 같아야 비교된다. 리포트에 긴 표·전체 데이터를 넣지 않는다(산출 파일이 따로 있다). 사람이 매 실행 읽을 수 있는 분량이 상한이다.
- **⑥ 이 비어 있지 않으면 다음 스테이지로 가지 않는다.** ⚠ 는 "사람이 결정할 것" 이다 — 불변식 위반이면 데이터·규칙 중 어느 쪽이 틀렸는지 판단하고, 규칙이 틀렸으면 `invariants` 를 고치되 그 사유를 CARD.md 에 남긴다. 결과를 보고 임계를 슬쩍 늦추는 일을 막기 위해서다.

## 번들 자원

- `scripts/scaffold.py` — 프로젝트 골격 생성 (`--stages`, `--author`, `--raw-file`, `--force`)
- `scripts/seam_finder.py` — 기존 .py/.ipynb 분할 지점·외부화 후보·주의사항 보고서
- `scripts/verify_equivalence.py` — 리팩터링 전후 산출 동일성 비교 (표는 값 기준, 그 외 해시)
- `assets/project_template/` — 복사되는 `src/common/*`(config·io·schema·llm·log·plot·**report**), `run.py`, `.gitignore`, README 틀
- `assets/stage_templates/` — `stage_first/middle/llm/report.py`, `CARD.md`
- `references/conventions.md` — config/schema/prompt/artifact/리포트 규약과 common API 상세 (스테이지 구현 중 참조)
- `references/checklist.md` — 모듈화 완료 판정 기준 (마무리 전 1회)

의존 패키지: `pandas pyyaml pyarrow` (필수), `matplotlib openpyxl` (report), `openai`/`anthropic` (실제 LLM 호출 시). 없으면 `pip install` 후 진행한다.
