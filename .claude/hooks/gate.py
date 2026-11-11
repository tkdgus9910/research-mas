#!/usr/bin/env python3
"""연구 프로세스 게이트 (PreToolUse 훅).

표준입력으로 받은 도구 호출을 보고 허용 / 차단(deny) / 사용자 확인(ask) 을 결정한다.

규칙
  R1  research/state.json 직접 수정 금지          -> state.py 로만 변경
  R2  research/protocol.lock.md 생성·수정         -> 사용자 확인 (게이트 1·2)
  R3  감사 에이전트는 audits/ 밖에 쓸 수 없음
  R4  audits/ 에는 감사 에이전트만 쓸 수 있음
  R5  data/raw/ 는 읽기 전용
  R6  프로토콜 확정 전에는 실험 코드(src/ configs/ prompts/ schemas/ run.py) 작성 금지
  R7  감사 판정을 무시하고 진행(--override) 하려면 사용자 확인

셸 명령(Bash/PowerShell)은 문자열 패턴으로만 검사한다. 실수로 절차를 건너뛰는 것을 막는
장치이며 보안 경계가 아니다.
"""
import json
import os
import re
import sys
from pathlib import Path, PurePosixPath

LOCK = "research/protocol.lock.md"
STATE = "research/state.json"
CODE_PREFIXES = ("src/", "configs/", "prompts/", "schemas/")
CODE_FILES = ("run.py",)
RAW_PREFIX = "data/raw/"
AUDIT_PREFIX = "audits/"
AUDITORS = {"design-reviewer", "simplicity-auditor", "method-auditor", "result-verifier"}
WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}

# 셸에서 파일을 바꾸는 명령·연산자
SHELL_WRITE = re.compile(
    r"(>|\btee\b|\bsed\s+-i|\brm\b|\bmv\b|\bcp\b|\btruncate\b|\bdd\b|\btouch\b"
    r"|\bgit\s+(checkout|restore|rm|mv|stash|reset)\b"
    r"|Set-Content|Add-Content|Out-File|Remove-Item|Move-Item|Copy-Item|New-Item)",
    re.IGNORECASE,
)
SHELL_CODE_REDIRECT = re.compile(r">\s*[\"']?(\./)?(src|configs|prompts|schemas)/")
SHELL_RUNS_PIPELINE = re.compile(r"(scaffold\.py|(^|[\s/\\\"'])run\.py\b)")


def emit(decision: str, reason: str) -> None:
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }
    # ASCII 로 출력해 Windows 콘솔 인코딩 문제를 피한다.
    sys.stdout.write(json.dumps(out, ensure_ascii=True))
    sys.exit(0)


def rel_path(raw: str, root: Path):
    """프로젝트 루트 기준 POSIX 상대경로. 프로젝트 밖이면 None."""
    if not raw:
        return None
    p = Path(raw.replace("\\", "/"))
    if not p.is_absolute():
        p = root / p
    try:
        rel = Path(os.path.normpath(p)).relative_to(Path(os.path.normpath(root)))
    except ValueError:
        return None
    return PurePosixPath(rel.as_posix()).as_posix()


def is_code(rel: str) -> bool:
    return rel.startswith(CODE_PREFIXES) or rel in CODE_FILES


def check_write(rel: str, agent: str, locked: bool) -> None:
    if rel == STATE:
        emit("deny", "R1: research/state.json 은 직접 수정할 수 없다. "
                     "`python .claude/skills/research/scripts/state.py` 의 advance / revise / set-stages 를 사용한다.")
    if agent in AUDITORS and not rel.startswith(AUDIT_PREFIX):
        emit("deny", f"R3: 감사 에이전트({agent})는 audits/ 에만 쓸 수 있다. 수정은 실험 에이전트의 몫이다.")
    if rel.startswith(AUDIT_PREFIX) and agent not in AUDITORS:
        emit("deny", "R4: audits/ 에는 감사 에이전트만 쓸 수 있다. 감사 대상의 작성자가 판정을 기록하면 안 된다.")
    if rel == LOCK:
        emit("ask", "R2: 프로토콜 확정/변경이다. 사용자가 설계(또는 변경 사유)를 승인한 경우에만 허용한다.")
    if rel.startswith(RAW_PREFIX) and not rel.endswith(".gitkeep"):
        emit("deny", "R5: data/raw/ 는 읽기 전용이다. 원본은 사용자가 직접 넣고, 가공본은 data/interim/ 에 쓴다.")
    if is_code(rel) and not locked:
        emit("deny", "R6: 프로토콜이 확정되지 않았다(research/protocol.lock.md 없음). "
                     "게이트 1 승인 전에는 실험 코드를 작성할 수 없다. 프로파일링 코드는 profiling/ 에 둔다.")


def check_shell(cmd: str, agent: str, locked: bool) -> None:
    writes = bool(SHELL_WRITE.search(cmd))
    if "state.json" in cmd and writes:
        emit("deny", "R1: research/state.json 은 state.py 로만 변경한다.")
    if "protocol.lock.md" in cmd and writes:
        emit("ask", "R2: 프로토콜 파일을 바꾸는 명령이다. 사용자 승인이 있는 경우에만 허용한다.")
    if "state.py" in cmd and "--override" in cmd:
        emit("ask", "R7: 감사 판정을 무시하고 다음 단계로 진행하려 한다. 사용자가 결정한 경우에만 허용한다.")
    if not locked and (SHELL_RUNS_PIPELINE.search(cmd) or SHELL_CODE_REDIRECT.search(cmd)):
        emit("deny", "R6: 프로토콜 확정 전에는 파이프라인 골격 생성·실행을 할 수 없다. "
                     "프로파일링은 profiling/ 의 스크립트로 수행한다.")


def main() -> None:
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    debug = os.environ.get("RESEARCH_MAS_HOOK_DEBUG")
    if debug:
        with open(debug, "a", encoding="utf-8") as f:
            f.write(raw + "\n")
    data = json.loads(raw or "{}")
    tool = data.get("tool_name", "")
    tool_input = data.get("tool_input") or {}
    agent = data.get("agent_type") or ""
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd())
    locked = (root / LOCK).is_file()

    if tool in WRITE_TOOLS:
        rel = rel_path(tool_input.get("file_path") or tool_input.get("notebook_path") or "", root)
        if rel:
            check_write(rel, agent, locked)
    elif tool in SHELL_TOOLS:
        check_shell(str(tool_input.get("command") or ""), agent, locked)
    sys.exit(0)  # 해당 규칙 없음: 통상 권한 절차를 따른다


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # 훅 자체의 오류는 작업을 막지 않되 사용자에게 보인다(비차단 오류).
        sys.stderr.write(f"research-mas gate error: {e!r}\n")
        sys.exit(1)
