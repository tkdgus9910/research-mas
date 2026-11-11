# 심사평 출력 형식

## 1. 영문 심사평 (저널 제출용)

권고 등급(reject/major/minor/accept)은 본문에 쓰지 않는다. 어조와 Major의 무게로 전달하고,
등급은 마지막 confidential 블록에만 적는다. (사용자의 기존 심사 관행)

```
## Summary of the manuscript
[2–4문장. 저자의 주장·방법·데이터·핵심 결과를 중립적으로 요약. 여기서 이미 논문을
정확히 이해했음을 보여야 이후 비판에 무게가 실린다.]

## Overall assessment
[1문단. Tier 기대치 대비 원고의 위치. 강점 1–2문장 → "However/That said, …"로 핵심 우려
2–3개를 한 문장씩. 유감 어조로 강도 전달. 예: "I regret that, in its current form, the
manuscript does not yet establish …"]

## Major comments
[3–6개. MECE. 각 항목 구조: 위치 → 문제 → 왜 문제인지(저널 기준) → 요구 조치]
1. **[한 줄 제목, 예: Theoretical positioning of the "target–symptom" construct]**
   (Section 3.1, p. 7) The manuscript introduces … but does not … . For a TFSC audience,
   … . I would encourage the authors to … .
2. …

## Minor comments
[번호 목록. 위치 + 한 줄 지적. 오탈자는 묶어서 한 항목]
1. (Table 2) It is unclear which of the compared models was used in the subsequent experiments.
2. (Section 4.2.2) "Z-score" and "Ratio" are used without definition.
3. …

## [2차 이상] Evaluation of the authors' response
[이전 Major별로 실질 반영 / 부분 반영 / 미반영 한 줄씩. 응답서 형식 문제가 있으면 마지막에]

---
## Confidential comments to the editor  (저자 비공개)
Recommendation: [Reject / Major revision / Minor revision / Accept]
Rationale (2–3 sentences): [Tier 기준 대비 결정적 사유. 조정한 경우 스크립트 산출과 다른 이유]
Reviewer confidence: [High / Medium / Low] — [방법론·도메인 중 어디에 자신 있는지]
```

작성 규칙
- Major 제목은 명사구. 본문은 격식 영어, 1인칭 단수("I") 사용 가능.
- 각 Major는 저자가 **무엇을 추가·삭제·재구성해야 하는지**로 끝난다. "unclear"로 끝내지 않는다.
- 사실 확인 질문을 적극 활용한다: "Which assignee filed the patents in cluster 7?" 같은 질문은
  해석의 근거 부재를 가장 효과적으로 드러낸다.
- 칭찬은 Overall assessment 앞부분 1–2문장으로 제한. Major 안에 칭찬을 섞지 않는다.
- 2차 이상에서 새 Major를 추가할 때는 "이전 라운드에서 제기하지 못한 점"임을 밝힌다.

## 2. 국문 요약 (사용자 확인용, 개조식 보고체)

```
□ 판정: Tier 1 (TFSC) 기준 → Major revision 권고 (스크립트: Major / 심사자 조정 없음)
□ 핵심 사유
 - A(기여) 2점: TM 담론 내 포지션 부재, 정보검색 논문으로 읽힘
 - D(검증) 2점: LLM-as-a-judge 단독 의존, 평가자 간 신뢰도 미보고
 - F(논의) 3점: Discussion에 문헌 인용 없음
□ 축별 점수: A2 B3 C3 D2 E3 F3 G4 H3
□ Major 4건 / Minor 7건
 - Major 1: … (Section 3.1)
 - Major 2: …
□ 특이사항: 6.3–6.4 검증 도메인 수 모순(1개 vs 3개) — 반드시 수정 요구
```

- 명사형 종결("~부재", "~필요", "~판단됨"). 구어체·감상형 금지.
- 두괄식: 판정 → 사유 → 상세.

## 3. 투고 전 셀프리뷰 모드 추가 출력

```
□ 목표 Tier 통과 가능성: 낮음 / 보통 / 높음 (현재 상태로 통과 가능한 Tier: 2)
□ 수정 우선순위 Top 5 (효과 대비 비용 순)
 1. [축 D] 골드스탠다드 200건 + κ 보고 — 새 실험 필요, 2주
 2. [축 A] Introduction을 기술인텔리전스 문제에서 시작하도록 재구성 — 재작성, 3일
 3. …
□ 그대로 두면 리뷰어가 쓸 문장(예상 코멘트)
 - "The literature review still reads like a technical piece for information science …"
```

## 4. 학위논문 심사 모드

- 국문 "~함/~필요함" 체. 심사위원 명의로 읽히도록 간결하게.
- 첫 항목은 항상 **연구 간 관계와 전체 흐름도**(하위 연구가 2개 이상일 때).
- 항목 순서: 전반 구조 → 방법 타당성 → 핵심 개념 구체화 → 신뢰성 강화 → 기타(형식·수식·설문).
