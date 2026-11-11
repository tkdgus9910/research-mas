"""게이트 훅(gate.py)과 상태 관리(state.py) 시험.

실행: python -m unittest discover -s .claude/tests -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GATE = REPO / ".claude" / "hooks" / "gate.py"
STATE = REPO / ".claude" / "skills" / "research" / "scripts" / "state.py"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for d in ("research", "audits", "reports", "src"):
            (self.root / d).mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, rel, text="x\n"):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def lock(self):
        self.put("research/protocol.lock.md")


class GateTest(Base):
    def gate(self, tool, tool_input, agent=None):
        """반환: 'allow' | 'deny' | 'ask' ('allow' = 훅이 결정을 내리지 않음)"""
        payload = {"tool_name": tool, "tool_input": tool_input, "cwd": str(self.root)}
        if agent:
            payload["agent_type"] = agent
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(self.root))
        env.pop("RESEARCH_MAS_HOOK_DEBUG", None)
        r = subprocess.run([sys.executable, str(GATE)], input=json.dumps(payload).encode(),
                           capture_output=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        if not r.stdout.strip():
            return "allow"
        return json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"]

    def write(self, rel, agent=None, absolute=False):
        path = str(self.root / rel) if absolute else rel
        return self.gate("Write", {"file_path": path, "content": "x"}, agent)

    def bash(self, cmd, agent=None):
        return self.gate("Bash", {"command": cmd}, agent)

    def test_code_blocked_before_lock(self):
        for rel in ("src/s1_collect/stage.py", "configs/default.yaml", "prompts/p.md", "schemas/a.yaml", "run.py"):
            self.assertEqual(self.write(rel), "deny", rel)
            self.assertEqual(self.write(rel, absolute=True), "deny", rel)

    def test_code_allowed_after_lock(self):
        self.lock()
        self.assertEqual(self.write("src/s1_collect/stage.py"), "allow")
        self.assertEqual(self.bash("python run.py --only s1_collect"), "allow")

    def test_pre_lock_documents_and_profiling_allowed(self):
        for rel in ("research/brief.md", "research/design.md", "profiling/profile.py", "playbook/lessons.md"):
            self.assertEqual(self.write(rel), "allow", rel)

    def test_lock_file_asks_user(self):
        self.assertEqual(self.write("research/protocol.lock.md"), "ask")
        self.lock()
        self.assertEqual(self.gate("Edit", {"file_path": "research/protocol.lock.md",
                                            "old_string": "a", "new_string": "b"}), "ask")
        self.assertEqual(self.bash("cp research/design.md research/protocol.lock.md"), "ask")
        self.assertEqual(self.bash("cat research/protocol.lock.md"), "allow")

    def test_state_json_is_script_only(self):
        self.assertEqual(self.write("research/state.json"), "deny")
        self.assertEqual(self.bash("echo '{}' > research/state.json"), "deny")
        self.assertEqual(self.bash("python .claude/skills/research/scripts/state.py advance"), "allow")
        self.assertEqual(self.bash("cat research/state.json"), "allow")

    def test_override_asks_user(self):
        self.assertEqual(self.bash('python .claude/skills/research/scripts/state.py advance --override "x"'), "ask")

    def test_auditor_scope(self):
        self.lock()
        self.assertEqual(self.write("audits/s1__method-auditor.md", agent="method-auditor"), "allow")
        self.assertEqual(self.write("src/s1/stage.py", agent="simplicity-auditor"), "deny")
        self.assertEqual(self.write("reports/s1.md", agent="result-verifier"), "deny")

    def test_only_auditors_write_audits(self):
        self.assertEqual(self.write("audits/s1__method-auditor.md"), "deny")
        self.assertEqual(self.write("audits/s1__method-auditor.md", agent="experimenter"), "deny")

    def test_raw_data_read_only(self):
        self.lock()
        self.assertEqual(self.write("data/raw/patents.csv"), "deny")
        self.assertEqual(self.write("data/interim/s1.parquet"), "allow")

    def test_shell_pipeline_blocked_before_lock(self):
        self.assertEqual(self.bash("python .claude/skills/ti-pipeline/scripts/scaffold.py . --stages a,b"), "deny")
        self.assertEqual(self.bash("python run.py"), "deny")
        self.assertEqual(self.bash("cat > src/x.py <<'EOF'\nprint(1)\nEOF"), "deny")
        self.assertEqual(self.bash("python profiling/profile.py"), "allow")

    def test_outside_project_and_other_tools_ignored(self):
        self.assertEqual(self.write("/tmp/elsewhere/src/x.py"), "allow")
        self.assertEqual(self.gate("Read", {"file_path": "src/x.py"}), "allow")

    def test_windows_style_path(self):
        self.assertEqual(self.write("src\\s1\\stage.py"), "deny")


class StateTest(Base):
    def state(self, *args, ok=True):
        r = subprocess.run([sys.executable, str(STATE), "--root", str(self.root), *args],
                           capture_output=True, text=True, encoding="utf-8")
        if ok:
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        else:
            self.assertNotEqual(r.returncode, 0, r.stdout)
        return r.stdout + r.stderr

    def phase(self):
        st = json.loads((self.root / "research" / "state.json").read_text(encoding="utf-8"))
        return st["phase"], st["stage_index"]

    def audit(self, unit, auditor, verdict):
        self.put(f"audits/{unit}__{auditor}.md", f"# 감사\n판정: {verdict}\n")

    def pass_all(self, unit, auditors):
        for a in auditors:
            self.audit(unit, a, "통과")

    def test_full_flow(self):
        self.state("init")
        self.state("advance", ok=False)                       # brief 없음
        self.put("research/brief.md")
        self.state("advance")
        self.put("research/data_profile.md")
        self.state("advance")
        self.assertEqual(self.phase()[0], "design")

        self.put("research/design.md")
        self.state("advance", ok=False)                       # 감사 없음
        self.audit("design", "design-reviewer", "통과")
        self.audit("design", "method-auditor", "수정")
        self.state("advance", ok=False)                       # 미통과
        self.state("revise")
        self.assertTrue((self.root / "audits/archive/design__method-auditor.r1.md").is_file())
        self.state("advance", ok=False)                       # 보관 후 감사 다시 필요
        self.pass_all("design", ["design-reviewer", "method-auditor"])
        self.state("advance")
        self.assertEqual(self.phase()[0], "gate1")

        self.state("advance", ok=False)                       # 잠금 파일 없음
        self.state("advance", "--override", "x", ok=False)    # 잠금은 override 불가
        self.lock()
        self.state("advance")
        self.assertEqual(self.phase()[0], "pilot")

        self.put("reports/pilot.md")
        self.pass_all("pilot", ["simplicity-auditor", "result-verifier"])
        self.state("advance", ok=False)                       # 단계 목록 없음
        self.state("set-stages", "S1 bad", ok=False)
        self.state("set-stages", "s1_collect,s2_analyze")
        self.state("advance")
        self.assertEqual(self.phase(), ("stages", 0))

        three = ["simplicity-auditor", "method-auditor", "result-verifier"]
        self.put("reports/s1_collect.md")
        self.pass_all("s1_collect", three)
        self.state("advance")
        self.assertEqual(self.phase(), ("stages", 1))
        self.state("set-stages", "a,b", ok=False)              # 파일럿 이후 변경 불가

        self.put("reports/s2_analyze.md")
        self.pass_all("s2_analyze", three)
        self.audit("s2_analyze", "simplicity-auditor", "상신")
        self.state("advance", ok=False)
        self.state("advance", "--override", "사용자 결정: 현 구조 유지")
        self.assertEqual(self.phase()[0], "final")
        self.assertIn("사용자 결정", (self.root / "research/decisions.md").read_text(encoding="utf-8"))

        self.put("reports/final.md")
        self.state("advance")
        self.put("research/retro.md")
        self.state("advance")
        self.assertEqual(self.phase()[0], "done")
        self.state("advance", ok=False)

    def test_reopen_from_gate1(self):
        self.state("init")
        self.state("reopen", ok=False)                         # gate1 이 아님
        self.put("research/brief.md")
        self.state("advance")
        self.put("research/data_profile.md")
        self.state("advance")
        self.put("research/design.md")
        self.pass_all("design", ["design-reviewer", "method-auditor"])
        self.state("advance")
        self.state("reopen")
        self.assertEqual(self.phase()[0], "design")
        self.assertTrue((self.root / "audits/archive/design__design-reviewer.u1.md").is_file())
        self.state("advance", ok=False)                        # 감사 다시 필요
        self.pass_all("design", ["design-reviewer", "method-auditor"])
        self.state("advance")
        self.lock()
        self.state("reopen", ok=False)                         # 확정 후에는 불가

    def test_revision_limit(self):
        self.state("init")
        self.put("research/brief.md")
        self.state("advance")
        self.put("research/data_profile.md")
        self.state("advance")
        self.state("revise")
        self.state("revise")
        out = self.state("revise", ok=False)
        self.assertIn("상신", out)

    def test_missing_audit_cannot_be_overridden(self):
        self.state("init")
        self.put("research/brief.md")
        self.state("advance")
        self.put("research/data_profile.md")
        self.state("advance")
        self.put("research/design.md")
        self.audit("design", "design-reviewer", "통과")
        self.state("advance", "--override", "x", ok=False)     # method-auditor 기록 없음

    def test_status_runs(self):
        self.state("status", ok=False)                         # init 전
        self.state("init")
        self.assertIn("의도 정제", self.state("status"))
        self.assertIn("research/brief.md", self.state())


if __name__ == "__main__":
    unittest.main()
