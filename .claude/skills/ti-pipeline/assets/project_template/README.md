# __PROJECT__

스테이지형 분석 파이프라인 (ti-pipeline 규약). 인풋 `data/` · 코드 `src/` · 아웃풋 `outputs/` 분리.

```
python run.py --list                 # 스테이지·산출 파일 현황
python run.py                        # 전체 실행
python run.py --only s3_extract      # 한 스테이지만
python run.py --from s2 --to s4      # 구간
python run.py -c configs/exp01.yaml --tag exp01
```

## 규약 (5줄)
1. 스테이지 1개 = `src/<stage>/stage.py` 의 `run(cfg) -> Path` 1개. 스테이지 간 전달은 파일(`configs: artifacts`).
2. 상수·경로·모델명은 `configs/*.yaml`, 프롬프트는 `prompts/*.md`. 코드에 리터럴을 두지 않는다.
3. 스테이지 경계에서 `schemas/<stage>.out.yaml` 로 검증. 컬럼이 바뀌면 그 자리에서 실패한다.
4. LLM 호출은 `src/common/llm.py` 를 통해서만 — 캐시(`cache/llm/`)와 비용 로그(`logs/llm_cost.jsonl`)가 따라온다.
5. 스테이지가 끝날 때마다 `outputs/<run_id>/reports/<stage>.md` 리포트가 자동으로 남는다 (`index.md` 가 묶음).
   중간 산출 검수는 parquet 대신 이 리포트로 한다. 스테이지 고유 지표는 `add_metrics`, 지켜야 할 범위는 `schemas/*.out.yaml` 의 `invariants`.

## 스테이지
__STAGE_TABLE__

각 스테이지 폴더의 `CARD.md` 에 목적·입출력·파라미터·비용을 기록한다 (논문 Method 절의 원재료). `최근 실행` 행은 리포트 생성 시 자동 갱신된다.

## 리포트 읽는 법
`outputs/<run_id>/reports/index.md` → 스테이지별 `<stage>.md`. 각 리포트는 6블록 고정:
① 실행 메타 · ② 입출력 요약(행 증감·스키마·결측) · ③ 핵심 지표·분포 · ④ 표본(seed 고정) · ⑤ 직전 실행 대비(Δ·변경 파라미터) · ⑥ 확인 필요(⚠ 불변식·스키마·경고).
⑥ 이 비어 있지 않으면 다음 스테이지로 가기 전에 본다. 리포트만 다시 만들려면 `python -m src.common.report <stage> --run-id <run_id>`.

## 노트북
`notebooks/` 는 탐색·시각화 전용. 로직은 `src/` 에 두고 노트북에서는 `from src.s3_extract.stage import run` 식으로 호출한다.
