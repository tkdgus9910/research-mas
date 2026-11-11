# ti-pipeline 규약 상세

SKILL.md 가 '왜'라면 이 문서는 '정확히 어떻게'다. 스테이지를 구현하면서 참조한다.

## 1. configs/default.yaml

```yaml
project: {name: hydrogen_storage, author: 박상현, seed: 42}

stages: [s1_collect, s2_clean, s3_extract, s4_analyze, s5_report]   # 실행 순서 = 계약 순서

paths:                      # 관례 경로. 보조 데이터는 여기에 추가 → path_of(cfg, "lexicon")
  raw: data/raw
  interim: data/interim
  processed: data/processed
  outputs: outputs
  cache: cache
  logs: logs
  prompts: prompts
  schemas: schemas
  lexicon: data/raw/ipc_lexicon.csv

artifacts:                  # 각 스테이지가 내놓는 파일. 다음 스테이지는 input_of() 로 이걸 읽는다
  s1_collect: data/interim/s1_collect.parquet
  s2_clean:   data/interim/s2_clean.parquet
  s3_extract: data/interim/s3_extract.parquet
  s4_analyze: data/processed/s4_analyze      # 표가 여러 개면 디렉터리 — 안에 clusters.parquet, network.parquet …
  s5_report:  outputs                        # report 는 outputs/<run_id>/ 에 쓴다

llm:
  provider: openai          # openai | anthropic | mock  — 키(OPENAI_API_KEY/ANTHROPIC_API_KEY) 없으면 자동 mock
  model: gpt-4o-mini
  temperature: 0
  max_tokens: 1024
  max_workers: 4

report:                     # 스테이지 리포트 (§9). 생략하면 아래 기본값
  enabled: true
  sample_n: 5
  hist_max: 3
  compare_previous: true

params:                     # 스테이지별 파라미터. 코드는 p = stage_params(cfg, STAGE); p.get("키", 기본값)
  s1_collect: {raw_file: patents_sample.csv}
  s2_clean:   {dedup_keys: [title, abstract], min_year: 2016, min_abstract_len: 20}
  s3_extract: {prompt: problem_solution_v1.md, id_col: patent_id, text_col: abstract, limit: null}
  s4_analyze: {n_clusters: 4, ngram_range: [1, 2], top_terms: 5}
  s5_report:  {deliverable_title: 수소저장 특허 문제-해결 분석}
```

실험 변형은 상속으로:

```yaml
# configs/k8.yaml
extends: default.yaml
params: {s4_analyze: {n_clusters: 8}}
```

`python run.py -c configs/k8.yaml --tag k8 --from s4` — s4 부터만 다시 돌고 outputs/<run_id>_k8/ 에 묶인다.
일회성 변경은 `--set params.s4_analyze.n_clusters=8`.

## 2. 스테이지 모듈 계약

```python
STAGE = "s4_analyze"

def run(cfg: dict) -> Path:
    log = get_logger(cfg)
    p   = stage_params(cfg, STAGE)
    src = input_of(cfg, STAGE)            # 직전 스테이지 artifact (첫 스테이지는 None)
    dst = artifact_path(cfg, STAGE)       # 이 스테이지가 내놓기로 한 파일/디렉터리
    df  = read_table(src)
    validate(df, load_schema(cfg, f"{STAGE}.in"), where=f"{STAGE}:in")    # 선택
    ...핵심 로직...
    validate(out, load_schema(cfg, f"{STAGE}.out"), where=f"{STAGE}:out")
    write_table(out, dst)
    return dst
```

- `run()` 은 cfg 하나만 받는다. 전역 변수·모듈 수준 상수를 읽지 않는다. 그래야 `run.py` 와 노트북 양쪽에서 같은 결과가 난다.
- 다른 스테이지의 산출이 추가로 필요하면 `read_table(artifact_path(cfg, "s2_clean"))`. 단 '직전' 이외의 의존은 CARD.md 에 적는다.
- 산출이 표 여러 개면 artifact 를 디렉터리로 선언하고 `dst / "clusters.parquet"` 식으로 쓴다. 다음 스테이지는 `input_of(cfg, STAGE) / "clusters.parquet"`.
- 결정성: 확률적 알고리즘(KMeans, UMAP, 샘플링)은 `random_state=cfg["project"]["seed"]`. dict/set 순회로 결과가 달라지는 코드는 정렬을 넣는다.
- 중간 포맷은 parquet. 리스트 컬럼(출원인 분해, 키워드)을 그대로 보존한다. 사람이 열어볼 최종본만 xlsx/csv.

## 3. 스키마 (schemas/<stage>.out.yaml)

```yaml
name: s3_extract.out
description: 특허별 문제·해결 추출 결과
primary_key: [patent_id]
min_rows: 1
columns:
  patent_id: {dtype: string, required: true, unique: true}
  problem:   {dtype: string, required: true}
  solution:  {dtype: string, required: true}
  confidence:{dtype: float,  min: 0, max: 1}
  parse_ok:  {dtype: bool,   required: true}
  source:    {dtype: string, allowed: [KIPRIS, USPTO, EPO]}
  ipc_main:  {dtype: string, pattern: "^[A-H]\\d{2}"}
extra_columns: allow        # allow | warn | forbid
```

dtype: `string | int | float | bool | datetime | list | any`. 초안은 `python -m src.common.schema infer <파일> --name <stage>.out` 로 뽑고 `required/unique` 를 손본다.
입력 스키마(`<stage>.in.yaml`)는 선택 — 상류 스테이지를 남이 만들었거나 외부 파일을 받을 때 둔다.

같은 파일에 리포트 불변식을 둔다(§9). `columns` 는 "모양" 을, `invariants` 는 "수치 범위" 를 지킨다:

```yaml
invariants:
  - {metric: rows_out, min: 1000, note: 결측 제거만으로 10% 이상 빠지면 원본 점검}
  - {metric: parse_ok_rate, min: 0.95}
  - {metric: attribution_rate, min: 0.85, max_rel_delta: 0.10}   # 직전 실행 대비 ±10% 초과도 ⚠
```

## 4. 프롬프트 (prompts/<name>_vN.md)

- 파일 하나 = 프롬프트 하나. 수정하면 `_v2` 로 새 파일을 만들고 config 의 `prompt:` 만 바꾼다. 캐시 키에 프롬프트 본문 해시가 들어가므로 v2 는 자동으로 전량 재호출된다(의도된 동작).
- 자리표시자는 `{text}`, `{id}` 처럼 item 의 키와 같은 이름. JSON 예시의 중괄호는 그대로 둬도 된다(`render()` 는 알려진 키만 치환).
- 응답 파싱은 스테이지 코드(`parse_response`)가 한다. 캐시는 원문만 보관한다 — 파싱 로직을 바꿔도 재호출이 없다.

## 5. LLM 호출

```python
template, phash = load_prompt(cfg, p["prompt"])
call = get_provider(cfg)                               # openai / anthropic / mock
items = [{"id": r["patent_id"], "text": r["abstract"]} for r in df.to_dict("records")]
results = map_cached(cfg, call, template, items, tag=f"{STAGE}:{p['prompt']}@{phash}")
# results[i] = {"id", "text"(응답 원문), "cached", "usage"}
```

- 캐시: `cache/llm/<sha256>.json`. 키 = 모델 + 프롬프트(렌더링 후) + 입력 item. 비용 로그: `logs/llm_cost.jsonl`, 집계 `cost_summary(cfg, run_id)`.
- 다른 provider 가 필요하면 `get_provider` 에 분기를 추가한다. 호출 함수 시그니처는 `(model, system, user) -> {"text", "usage"}`.
- 원본 코드에 규칙 기반 mock 이 있었다면: 그 함수를 `src/<stage>/mock.py` 로 옮기고 `provider: mock` 일 때 `mock_call` 대신 쓰도록 스테이지에서 교체한다. 동일성 검증은 원본 mock 과 비교해야 의미가 있다.

  ```python
  # src/s3_extract/mock.py — 공통 CallFn 시그니처 (model, system, user) -> {"text", "usage"}
  def rule_based_call(model, system, user):
      m = _RX.search(user)                      # user = 렌더링된 프롬프트 (끝에 원문)
      return {"text": json.dumps({...}), "usage": {"input_tokens": 0, "output_tokens": 0}}

  # stage.py
  from src.common.llm import get_provider, mock_call
  call = get_provider(cfg)
  if call is mock_call:                         # 키 없음/provider: mock → 원본 로직으로
      call = rule_based_call
  ```
- 앙상블/다중 에이전트: 모델·프롬프트 조합마다 `map_cached` 를 따로 호출하고 결과 컬럼을 `problem_gpt`, `problem_claude` 처럼 접미사로 구분한다. 합의(voting) 로직은 별도 스테이지(`s4_consensus`)로 둔다.

## 6. 산출물·명명·로그

- `outputs/<run_id>/` 에 그 실행의 모든 보고물. `run_id = YYMMDD_HHMMSS[_tag]`.
- 사람에게 전달하는 파일은 `deliverable_path(cfg, "제목", "xlsx")` → `제목(YYMMDD, NN, 저자).xlsx`. 같은 날 같은 제목이면 NN 자동 증가.
- `logs/runs.jsonl`: 스테이지별 실행 기록(run_id, config, params 스냅샷, 행 수, 소요, 상태). `logs/<run_id>.log`: 콘솔 로그 사본.
- `<stage>_meta.json` 을 report 가 outputs 에 남긴다(어느 config·입력에서 나온 그림인지).

## 7. 노트북과의 관계

```python
# notebooks/explore.ipynb
import sys; sys.path.insert(0, "..")
from src.common.config import load_config
from src.common.io import read_table, artifact_path
cfg = load_config("../configs/default.yaml")
df = read_table(artifact_path(cfg, "s3_extract"))      # 중간 산출을 열어 탐색
```

노트북에서 새 분석을 시도하다가 쓸 만해지면 → 스테이지로 승격(새 폴더 + artifacts 한 줄 + stages 에 추가). 승격 기준은 "두 번째로 돌리고 싶어졌는가".

## 8. 레거시 보관

- 원본 스크립트/노트북 → `legacy/`. 기준 산출물 → `legacy/baseline_outputs/`. 삭제하지 않는다.
- README 의 '원본 → 스테이지' 대응표: `hydrogen_analysis.py L26–27 → s1_collect`, `L30–48 → s2_clean` … seam_finder 보고서의 구간표를 그대로 옮기면 된다.

## 9. 스테이지 리포트 (`src/common/report.py`)

`run.py` 가 스테이지 `run()` 이 끝날 때마다(실패했을 때도) `outputs/<run_id>/reports/<stage>.md` 를 쓰고 `index.md` 를 갱신한다. 스테이지 코드가 아무것도 안 해도 자동 부분이 나온다. 리포트 생성 실패는 경고로만 남고 파이프라인을 죽이지 않는다.

### 6블록 (양식 고정 — 실행 간 비교를 위해 순서·지표명을 바꾸지 않는다)

| 블록 | 자동으로 들어가는 것 | 스테이지가 보탤 수 있는 것 |
|---|---|---|
| ① 실행 메타 | run_id · config · params 스냅샷 · 입력/출력 파일(크기·sha256 앞 12자리·mtime) · 소요 · LLM 신규 호출·토큰(`logs/llm_cost.jsonl` 에서 이 스테이지 tag 만) · 프롬프트 해시 · 직전 run_id | — |
| ② 입출력 요약 | 표별 행·열 · 입력→출력 행 증감(%) · 스키마 검증 결과(위반 목록) · 기본키 중복 · 결측 상위 5열 · 열 요약(접힘) | — |
| ③ 핵심 지표·분포 | 자동 지표(아래) · 범주형 열(고유값 ≤12) 분포 표 최대 3개 · 수치 열 히스토그램 png | `add_metrics` 지표 · `add_table` 소형 표 · `add_figure` 그림 |
| ④ 표본 | 첫 표에서 `sample_n` 행, `random_state = project.seed`, 앞 6열 | `add_samples(df, cols)` 로 열 지정(원문 ↔ 산출 나란히) 또는 `params.<stage>.report.sample_cols` |
| ⑤ 직전 실행 대비 | 같은 스테이지의 직전 ok 리포트와 수치 지표 Δ·Δ% · 변경된 params · 입력 해시 변경 여부 | — |
| ⑥ 확인 필요 | 불변식 위반 · 스키마 위반 · 스테이지 실패 사유 | `warn()` 경고 · `note()` 메모 |

자동 지표명: `rows_in` `rows_out` `rows_delta` `rows_delta_pct` `cols_out` `null_rate_max` `llm_calls` `llm_input_tokens` `llm_output_tokens` `elapsed_s`. 불변식의 `metric` 에 그대로 쓸 수 있다.

### 스테이지에서 쓰는 API

```python
from src.common.report import add_metrics, add_table, add_samples, add_figure, note, warn

add_metrics(cfg, STAGE, dropped_rows=37, attribution_rate=0.943)   # ③ 표 + ⑤ 비교 + 불변식 대상
add_table(cfg, STAGE, "탈락 사유", reasons_df)                      # ③ 소형 표 (20행 이내)
add_samples(cfg, STAGE, out, cols=["id", "text", "llm_raw"])       # ④ 표본 열 지정
add_figure(cfg, STAGE, png_path, caption="…")                      # ③ 그림 링크
warn(cfg, STAGE, "no_match 큐 41건 — 승인 필요")                     # ⑥ ⚠
note(cfg, STAGE, "정의 필드는 2차 패스에서 채움")                      # ⑥ 메모
```

- 호출 즉시 `reports/<stage>.extras.json` 에 저장되므로 스테이지가 중간에 죽어도 그때까지의 지표는 리포트에 남는다.
- `add_metrics` 값은 숫자 또는 짧은 문자열. 숫자만 ⑤ 비교·불변식 검사 대상이다. 지표명은 한 번 정하면 바꾸지 않는다.
- 지표는 스테이지당 2–5개. "이 숫자가 틀어지면 뒤 단계가 다 틀어진다" 는 것만 올린다. 분포 전체·긴 표는 산출 파일에 있으므로 넣지 않는다.
- 앙상블/다중 에이전트 스테이지는 **불일치율**(모델 간 판정 불일치 비율, 조정자 개입 건수)을 지표로 올린다 — 리포트에 안 보이면 에이전트를 늘린 효과를 알 수 없다.

### 불변식 (`schemas/<stage>.out.yaml` → `invariants:`)

| 키 | 뜻 |
|---|---|
| `metric` | 자동 지표명 또는 `add_metrics` 이름 (필수) |
| `min` / `max` | 절대 범위 |
| `max_abs_delta` / `max_rel_delta` | 직전 ok 실행 대비 허용 변동(절대값 / 비율 0–1) |
| `note` | 위반 시 ⑥ 에 함께 표시할 한 줄 (왜 이 범위인가) |

위반은 파이프라인을 멈추지 않고 ⑥ 에 ⚠ 로 올린다 — 멈춰야 하는 조건은 `validate()` 의 `columns`(min/max/allowed) 에 둔다. 결과를 보고 임계를 늦추지 않는다; 늦춰야 하면 사유를 CARD.md 에 적는다.

### 재생성·끄기

- 기존 산출로 리포트만 다시: `python -m src.common.report s3_extract --run-id 261026_101500` (노트북에서 수동 실행한 뒤, 또는 report.py 를 고친 뒤)
- 끄기: `python run.py --no-report` 또는 `report.enabled: false`. 꺼져 있으면 `add_*` 는 디스크에 아무것도 만들지 않는다.
- CARD.md 의 `| 최근 실행 | … |` 행은 리포트 생성 시 자동으로 채워진다(run_id · 행 수 · 상태 · ⚠ 수 · 리포트 링크). 그 행이 없으면 건드리지 않는다.
