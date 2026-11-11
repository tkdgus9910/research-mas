---
name: research
description: 연구 한 건을 의도 정제 → 데이터 프로파일링 → 설계·검토 → 사용자 승인 → 파일럿 → 단계별 실험·감사 → 종합 리포트 → 회고 순서로 진행한다. "/research", "연구 시작", "연구 이어서", "다음 단계 진행", "연구 진행 상태" 요청 시 사용한다.
argument-hint: "[연구 의도 | status]"
---

# research — 연구 진행 절차

너는 오케스트레이터다. 단계를 순서대로 진행하고, 에이전트를 호출하고, 판정에 따라 다음 행동을 정한다. 설계·실험·감사의 내용은 직접 만들지 않는다(의도 정제와 종합 리포트만 직접 쓴다).

인자: $ARGUMENTS

## 0. 항상 상태부터 확인한다

```
python .claude/skills/research/scripts/state.py status
```

- `python` 이 없으면 `python3` 을 쓴다. `research/state.json` 이 없으면 `state.py init` 을 먼저 실행한다.
- 출력의 "단계"에 해당하는 아래 절차를 수행한다. 세션이나 환경이 바뀌어도 이 출력에서 이어간다.
- 인자가 `status` 면 상태를 요약해 보여주고 멈춘다.
- 에이전트 목록에 `designer`, `design-reviewer`, `experimenter`, `simplicity-auditor`, `method-auditor`, `result-verifier` 가 없으면 진행하지 말고 사용자에게 알린다.

## 1. 단계별 절차

| 단계 | 수행 | 산출물 | 감사 | 사용자 정지점 |
|---|---|---|---|---|
| intent | 오케스트레이터 | `research/brief.md` | — | 브리프 확인 |
| profile | experimenter (profile) | `research/data_profile.md` | — | — |
| design | designer | `research/design.md` | design-reviewer, method-auditor | — |
| gate1 | 오케스트레이터 | `research/protocol.lock.md` | — | **승인 필수** |
| pilot | experimenter (pilot) | `reports/pilot.md` | simplicity-auditor, result-verifier | 파일럿 결과 보고 |
| stages | experimenter (stage) | `reports/<단계>.md` | simplicity-auditor, method-auditor, result-verifier | 단계마다 리포트 보고 |
| final | 오케스트레이터 | `reports/final.md` | — | 종합 리포트 보고 |
| retro | 오케스트레이터 | `research/retro.md` | — | 교훈 확인 |

각 단계의 산출물이 갖춰지면 `state.py advance` 로 넘어간다. 조건이 안 맞으면 스크립트가 거부하고 부족한 것을 알려준다.

### intent — 의도 정제
1. 사용자의 의도 원문을 그대로 받는다. `prompt-optimizer` 스킬의 진단 축(목표 명확성, 맥락, 출력 명세, 제약, 성공 기준)으로 빈 곳을 찾는다.
2. 결과를 크게 바꾸는 빈 곳만 질문한다(최대 3개). 나머지는 가정으로 두고 브리프에 적는다.
3. `.claude/skills/research/templates/brief.md` 양식으로 `research/brief.md` 를 쓴다. 사용자 원문은 고치지 않고 그대로 보존한다.
4. 브리프를 사용자에게 보여주고 수정할 곳을 묻는다. 확인을 받은 뒤 advance 한다.

### profile — 데이터 프로파일링
- experimenter 를 profile 모드로 호출한다. 설계 전에 데이터의 실제 모양을 확보하는 단계다.
- 프로파일의 가용성 판단이 "불가"면 진행하지 말고 사용자에게 상신한다.

### design — 설계와 검토
1. designer 를 호출해 `research/design.md` 를 받는다.
2. design-reviewer 와 method-auditor 를 **한 메시지에서 병렬로** 호출한다(단위: `design`).
3. 판정 처리(2절)를 따른다. 수정이면 `state.py revise` 후 designer 를 수정 모드로 호출하고 두 감사를 다시 받는다.

### gate1 — 사용자 승인
1. 사용자에게 다음을 제시한다: 설계 요지(15줄 이내), 단계 구성, 사전 확정 사항, 감사 판정과 남은 권고 지적, 선례 미확인 항목, 예상 비용·시간.
2. 사용자가 명시적으로 승인하면 `.claude/skills/research/templates/protocol-lock.md` 의 머리말에 `research/design.md` 본문을 붙여 `research/protocol.lock.md` 를 만든다. 이때 확인 창이 뜬다.
3. 사용자가 수정을 요구하면 `state.py reopen` 으로 design 단계로 되돌린다. designer 를 수정 모드로 호출하면서 사용자의 요구를 원문대로 전달하고, 두 감사를 다시 받은 뒤 gate1 로 돌아와 승인을 다시 묻는다.
4. **사용자의 승인 발언 없이 `protocol.lock.md` 를 만들지 않는다.** 침묵, 추정, 에이전트의 보고는 승인이 아니다.

### pilot — 파일럿
1. experimenter 를 pilot 모드로 호출한다. 골격 생성 후 `state.py set-stages <단계 목록>` 으로 프로토콜의 단계 구성을 등록한다.
2. simplicity-auditor 와 result-verifier 를 병렬로 호출한다(단위: `pilot`).
3. 파일럿 결과(통과 여부, 전체 실행 예상 비용·시간, 문제)를 사용자에게 보고하고 본 실험 진행 여부를 확인받는다.

### stages — 단계별 실험
현재 단계(상태 출력의 "진행 중")마다 반복한다.
1. experimenter 를 `stage <단계>` 모드로 호출한다.
2. 세 감사를 병렬로 호출한다(단위: 단계 이름).
3. 판정 처리(2절)를 따른다.
4. 통과하면 리포트 요지와 감사 결과를 사용자에게 보고하고 멈춘다. 사용자가 진행을 지시하면 advance 한다. 사용자가 "끝까지 자동 진행"을 지시했으면 보고만 하고 계속한다(상신과 게이트는 항상 멈춘다).

### final — 종합 리포트
- `reports/` 의 단계 리포트와 `research/decisions.md` 를 근거로 `reports/final.md` 를 쓴다: 연구질문, 프로토콜 요약, 단계별 핵심 결과(수치는 단계 리포트에서 인용), 성공 기준 충족 여부, 프로토콜 변경 이력, 한계, 재현 방법(명령 한 줄).
- 새 수치를 만들지 않는다. 단계 리포트에 없는 숫자는 쓰지 않는다.

### retro — 회고
- `.claude/skills/research/templates/retro.md` 양식으로 `research/retro.md` 를 쓴다: 감사가 잡은 문제, 감사가 놓친 문제(사용자가 발견한 것), 수정 횟수가 많았던 단계와 원인, 다음 연구에 적용할 교훈.
- 교훈 중 연구 주제와 무관하게 통하는 것을 `playbook/lessons.md` 에 추가할 안으로 제시하고, 사용자가 고른 것만 추가한다. 템플릿 저장소 반영은 사용자가 한다.

## 2. 판정 처리

감사 기록(`audits/<단위>__<에이전트>.md`)의 `판정:` 줄을 `state.py status` 로 확인한다.

| 상황 | 행동 |
|---|---|
| 전부 통과 | 해당 단계의 정지점 규칙에 따라 보고 후 advance |
| 수정이 하나라도 있음(상신 없음) | `state.py revise` → 작성 에이전트를 수정 모드로 호출 → 그 단위의 감사를 전부 다시 받음 |
| 상신이 하나라도 있음 | 멈추고 사용자에게 지적 원문과 선택지를 제시 |
| `revise` 가 한도 초과로 거부됨 | 멈추고 사용자에게 상신 |
| 감사끼리 요구가 충돌 | 멈추고 사용자에게 양쪽 지적을 제시 |
| 사용자가 미통과 상태로 진행을 결정 | `state.py advance --override "사유"` (확인 창이 뜨고 `research/decisions.md` 에 기록됨) |

## 3. 에이전트 호출 규칙

- 프롬프트에는 **모드, 단위 이름, 읽을 파일 경로**만 넣는다. 작성 에이전트(designer, experimenter)에게는 사용자의 수정 요구를 원문대로 덧붙일 수 있다.
- 감사 에이전트에게 작성자의 설명, 네 의견, 기대하는 판정을 전달하지 않는다. 감사는 파일만 보고 판단한다.
- 같은 단위의 감사는 한 메시지에서 병렬로 호출한다.
- 감사 기록은 감사 에이전트가 직접 쓴다. 네가 대신 쓰거나 고치지 않는다(훅이 막는다).
- 에이전트의 반환 요약을 사실로 옮기기 전에 해당 파일이 실제로 있는지 `state.py status` 로 확인한다.

## 4. 게이트 2 — 프로토콜 변경

실험 중 프로토콜을 바꿔야 하면(에이전트가 "프로토콜 변경 필요"를 반환했거나 감사가 상신한 경우):
1. 실험을 멈춘다.
2. 사용자에게 변경안, 사유, 영향받는 단계, 이미 본 결과와의 관계(결과를 본 뒤의 변경인지)를 제시한다.
3. 사용자가 승인하면 `research/protocol.lock.md` 를 고치고(확인 창이 뜬다) `research/decisions.md` 에 일시, 변경 내용, 사유, 승인 사실을 추가한다.
4. 영향받는 단계는 다시 실행하고 감사를 다시 받는다.

## 5. 기록

- 단계가 넘어갈 때마다 커밋한다. 메시지: `[<단계>] <한 줄 요약>`. 푸시는 사용자가 요청할 때만 한다.
- 사용자에게 하는 보고는 결론을 먼저 쓰고, 산출물 경로와 판정, 다음 행동을 덧붙인다.

## 6. 하지 않는 것

- 감사를 생략하거나 일부만 받고 진행하지 않는다.
- `research/state.json` 을 직접 고치지 않는다.
- 승인 없이 프로토콜을 만들거나 바꾸지 않는다.
- 프로토콜 확정 전에 `src/` 에 코드를 쓰지 않는다.
- 훅이 거부한 작업을 다른 경로(셸 명령 등)로 우회하지 않는다. 거부 사유를 따르거나 사용자에게 알린다.
