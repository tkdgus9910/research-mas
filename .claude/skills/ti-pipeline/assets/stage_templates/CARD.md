# __STAGE__

| 항목 | 내용 |
|---|---|
| 목적 | __HINT__ |
| 입력 | (파일 · 스키마 `schemas/__STAGE__.in.yaml` 또는 직전 스테이지 out) |
| 출력 | (파일 · 스키마 `schemas/__STAGE__.out.yaml`) |
| 파라미터 | `params.__STAGE__` : |
| 외부 의존 | (API · 모델 · 패키지) |
| 비용/소요 | (호출 수 · 토큰 · 시간 — `logs/llm_cost.jsonl`, `logs/runs.jsonl` 참조) |
| 리포트 지표 | (`add_metrics` 로 올리는 지표와 `schemas/__STAGE__.out.yaml` 의 invariants — 왜 이 숫자를 보는지) |
| 최근 실행 | (자동 갱신 — run.py 가 리포트를 만들 때 이 행을 채운다) |

## 로직 요약
(2–4줄. 논문 Method 절에 옮겨 적을 수 있는 수준으로 — 무엇을 어떤 기준으로 변환하는지)

## 알려진 한계 / 결정 사항
- 
