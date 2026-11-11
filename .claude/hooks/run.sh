#!/bin/sh
# 게이트 훅 실행기. Python 3.8+ 인터프리터를 찾아 gate.py 를 실행한다.
# macOS/Linux 의 sh, Windows 의 Git Bash 에서 동작한다.
# 인터프리터를 찾지 못하면 exit 2 로 도구 호출을 차단한다(게이트가 조용히 꺼지는 것을 막기 위함).
root="${CLAUDE_PROJECT_DIR:-$(pwd)}"
for py in python3 python py; do
  if command -v "$py" >/dev/null 2>&1 \
    && "$py" -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" </dev/null >/dev/null 2>&1; then
    exec "$py" "$root/.claude/hooks/gate.py"
  fi
done
echo "research-mas gate: Python 3.8+ not found on PATH. Install Python, then retry." >&2
exit 2
