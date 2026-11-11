# research-mas

연구 한 건을 다중 에이전트 절차로 진행하는 Claude Code 템플릿이다. 연구마다 이 저장소를 복제해 새 저장소를 만들고, 그 안에서 `/research` 로 진행한다.

## 흐름

```
의도 입력
 → 의도 정제                      research/brief.md
 → 데이터 프로파일링              research/data_profile.md
 → 설계 ⇄ 설계 검토 ∥ 방법론 감사   research/design.md
 → ■ 게이트 1: 사용자 승인         research/protocol.lock.md
 → 파일럿(소표본 전 단계 실행)    reports/pilot.md
 → 단계별 실험                    reports/<단계>.md
     → 단순화 감사 ∥ 방법론 감사 ∥ 결과 검증
     → 통과 → 다음 단계 / 수정(단위당 2회까지) / 사용자 상신
     (프로토콜 변경 → ■ 게이트 2: 사용자 승인 → research/decisions.md)
 → 종합 리포트 → 회고             reports/final.md, research/retro.md
```

## 구성

| 역할 | 정의 파일 | 하는 일 | 쓰기 범위 |
|---|---|---|---|
| 오케스트레이터 | `.claude/skills/research/SKILL.md` | 단계 진행, 에이전트 호출, 판정 처리, 의도 정제, 종합 리포트 | 전체(게이트 적용) |
| 설계 | `.claude/agents/designer.md` | 연구설계서 작성·수정 | `research/design.md` |
| 설계 검토 | `.claude/agents/design-reviewer.md` | 목표 저널 심사자 관점의 타당성 검토 | `audits/` |
| 실험 | `.claude/agents/experimenter.md` | 프로파일링, 파일럿, 단계 구현·실행, 단계 리포트 | 코드, 산출물, `reports/` |
| 단순화 감사 | `.claude/agents/simplicity-auditor.md` | 복잡도 측정, 제거 가능한 구성요소 지적 | `audits/` |
| 방법론 감사 | `.claude/agents/method-auditor.md` | 방법 요소별 선례 확인, AI스러운 요소 지적 | `audits/` |
| 결과 검증 | `.claude/agents/result-verifier.md` | 리포트 수치와 산출물 대조, 누수·사후 조정 점검 | `audits/` |

## 사전 준비 (PC)

1. git — Windows 는 Git for Windows (훅이 Git Bash 로 실행된다)
2. Python 3.8 이상 — `python` 또는 `python3` 이 PATH 에 있어야 한다
3. VS Code 와 Claude Code 확장

## 새 연구 시작

1. GitHub 에서 이 저장소의 **Use this template** 으로 연구용 저장소를 만든다(이 저장소 Settings 에서 Template repository 를 켜 둔다).
2. PC 에 clone 하고 VS Code 로 연다.
3. 데이터를 `data/raw/` 에 넣는다(기본 설정에서 데이터는 커밋되지 않는다).
4. Claude 패널에서 실행한다.

```
/research <연구 의도를 2~3문장으로>
```

5. 이후에는 `/research` 만 입력하면 멈춘 곳에서 이어간다. 현재 위치는 아래 명령으로도 볼 수 있다.

```bash
python .claude/skills/research/scripts/state.py status
```

## 사용자가 개입하는 지점

| 지점 | 사용자가 하는 일 |
|---|---|
| 브리프 확인 | 정제된 연구 의도가 맞는지 확인 |
| 게이트 1 | 설계서 승인 또는 수정 요구. 승인하면 프로토콜이 확정된다 |
| 파일럿 결과 | 예상 비용·시간을 보고 본 실험 진행 결정 |
| 단계 리포트 | 단계마다 결과 확인 후 진행 지시("끝까지 자동 진행"으로 생략 가능) |
| 상신 | 감사가 프로토콜·연구질문 수준의 문제를 제기했을 때 결정 |
| 게이트 2 | 프로토콜 변경 승인 |
| 회고 | 플레이북에 넣을 교훈 선택 |

## 게이트 (훅)

`.claude/hooks/gate.py` 가 파일 쓰기와 셸 명령을 검사한다.

| 규칙 | 내용 |
|---|---|
| R1 | `research/state.json` 은 `state.py` 로만 바꾼다 |
| R2 | `research/protocol.lock.md` 생성·수정은 사용자 확인 창을 띄운다 |
| R3 | 감사 에이전트는 `audits/` 밖에 쓸 수 없다 |
| R4 | `audits/` 에는 감사 에이전트만 쓸 수 있다 |
| R5 | `data/raw/` 는 읽기 전용이다 |
| R6 | 프로토콜 확정 전에는 `src/ configs/ prompts/ schemas/ run.py` 를 쓸 수 없다 |
| R7 | 감사 판정을 무시한 진행(`advance --override`)은 사용자 확인 창을 띄운다 |

훅은 절차를 실수로 건너뛰는 것을 막는 장치다. 셸 명령은 문자열 패턴으로만 검사하므로 보안 경계로 쓰지 않는다.

## 플레이북 (`playbook/`)

감사의 판단 기준이다. 채울수록 감사가 사용자 기준에 맞춰진다.

| 파일 | 내용 | 채우는 사람 |
|---|---|---|
| `precedents.md` | 목표 저널, 확인된 방법 선례 | 사용자, 회고 |
| `method-smells.md` | AI스러운 방법의 유형, 사용자 판단 사례 | 사용자 |
| `complexity-budget.md` | 복잡도 예산(단계 수, 파라미터 수 등) | 사용자 |
| `lessons.md` | 연구 간 교훈 | 회고 |

연구 저장소에서 플레이북을 고쳤으면 이 템플릿 저장소에도 옮겨야 다음 연구에 반영된다.

## 포함된 스킬

`.claude/skills/` 에 `ti-pipeline`(실험 파이프라인 구조), `tm-reviewer`(평가 축·저널 수준), `prompt-optimizer`(의도 정제)의 사본이 들어 있다. 연구 저장소는 생성 시점의 사본을 그대로 가지므로 이후 스킬이 바뀌어도 지난 연구의 절차는 바뀌지 않는다. `tm-reviewer` 에는 개인 심사 경험에서 나온 내용이 있으므로 저장소를 공개로 바꾸기 전에 확인한다.

## 시험

```bash
python -m unittest discover -s .claude/tests -v
```

게이트 규칙과 상태 전이를 시험한다. 훅이나 `state.py` 를 고친 뒤 실행한다.
