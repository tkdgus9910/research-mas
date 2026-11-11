# 모듈화 완료 판정 체크리스트

마무리 전에 한 번 훑는다. 하나라도 ✘면 아직 "모듈화됨"이 아니다. 각 항목은 확인 명령을 같이 둔다.

## 구조
- [ ] `python run.py --list` 가 모든 스테이지와 산출 경로를 보여주고, 전체 실행(`python run.py`) 이 끝까지 성공한다
- [ ] 스테이지 수 3–5개. 1–2문장짜리 스테이지나 20개 함수가 든 스테이지가 없다
- [ ] 각 `src/sN_*/stage.py` 에 `run(cfg) -> Path` 가 있고, 모듈 수준에 `STAGE` 외의 상수가 없다 — `grep -n "^[A-Z_]\+ = " src/s*/stage.py`
- [ ] 첫 스테이지만 `data/raw/` 를 읽는다 — `grep -rn "raw" src/s[2-9]*/`

## 계약
- [ ] `configs: artifacts` 에 모든 스테이지가 있고, 각 스테이지는 `input_of()`/`artifact_path()` 로만 입출력한다 — `grep -rn "to_csv\|to_parquet\|read_csv\|read_parquet" src/s*/stage.py` 가 `write_table/read_table` 외 직접 호출을 보이지 않는다
- [ ] 스테이지마다 `schemas/<stage>.out.yaml` 이 있고 `columns` 가 비어 있지 않다 (report 제외) — 실행 로그에 "스키마 미선언" 경고가 없다
- [ ] 중간 산출은 parquet, 리스트 컬럼이 문자열로 뭉개지지 않았다
- [ ] LLM 스테이지와 핵심 변환 스테이지의 `schemas/<stage>.out.yaml` 에 `invariants` 가 1개 이상 있다 (행 수 하한, 성공률, 귀속률 등 — 원본 코드의 assert·눈대중 점검을 옮긴 것)

## 설정 외부화
- [ ] 코드에 경로 리터럴이 없다 — `grep -rn "\.csv\|\.xlsx\|\.parquet\|results/\|data/" src/s*/stage.py` 가 config 키 참조 외에 안 나온다
- [ ] 하이퍼파라미터(n_clusters, threshold, min_year, model, temperature…)가 `params.<stage>` / `llm` 에 있고 코드는 `p.get()` 으로 읽는다
- [ ] 시드가 `project.seed` 한 곳에서 나온다
- [ ] 프롬프트가 `prompts/*.md` 에 있고 코드 안 문자열이 아니다

## LLM
- [ ] 모든 호출이 `map_cached`/`cached_call` 을 거친다. 두 번째 실행에서 `cache hit` 이 전량이다 — `python run.py --only s3` 두 번
- [ ] `limit`·`provider` 가 config 에만 있다(코드에 `if DEBUG` 없음)
- [ ] 원본이 mock/규칙 기반 분기를 갖고 있었다면 그 로직이 보존되어 동일성 검증이 원본 mock 기준으로 통과한다

## 산출
- [ ] 보고물이 `outputs/<run_id>/` 한 곳에 묶이고 `*_meta.json` 이 함께 있다
- [ ] 전달용 파일명이 `제목(YYMMDD, NN, 저자).ext`
- [ ] (모드 B) `verify_equivalence.py` 가 표 산출 전부 동일 판정. 그림은 눈으로 비교했고 차이 사유가 있으면 README 에 적었다

## 리포트
- [ ] 전체 실행 후 `outputs/<run_id>/reports/index.md` 에 모든 스테이지가 ✔ 이고 '확인 필요' 가 0 이다 (남은 ⚠ 는 사유를 CARD.md 에 적었다)
- [ ] 각 스테이지 리포트 ③ 에 그 스테이지 고유 지표가 2개 이상 보인다 (자동 지표만으로 충분한 단순 변환 스테이지는 예외) — 앙상블/에이전트 스테이지는 불일치율 포함
- [ ] ④ 표본이 "원문 ↔ 산출" 을 나란히 보여 준다 (LLM 스테이지는 `add_samples` 로 열 지정)
- [ ] 같은 설정으로 두 번 돌렸을 때 ⑤ 직전 대비가 전부 Δ 0 이다 (`cache_hit`·`llm_*`·`elapsed_s` 제외) — 아니면 비결정 요소(시드·순회 순서)가 남아 있다

## 문서
- [ ] 각 스테이지 `CARD.md` 의 목적·입력·출력·파라미터·리포트 지표가 채워졌다 (비용 란은 LLM 스테이지만). `최근 실행` 행이 자동 갱신되어 있다
- [ ] README 에 실행법과 (모드 B) '원본 → 스테이지' 대응표가 있다
- [ ] 원본은 `legacy/` 에 있고 삭제되지 않았다
- [ ] `notebooks/` 에 로직이 남아 있지 않다 (import 해서 호출만)
