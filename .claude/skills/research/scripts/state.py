#!/usr/bin/env python3
"""연구 진행 상태 관리. research/state.json 은 이 스크립트로만 바꾼다.

사용법
  python .claude/skills/research/scripts/state.py init
  python .claude/skills/research/scripts/state.py status
  python .claude/skills/research/scripts/state.py advance [--override "사유"]
  python .claude/skills/research/scripts/state.py revise
  python .claude/skills/research/scripts/state.py reopen
  python .claude/skills/research/scripts/state.py set-stages s1_collect,s2_clean,s3_analyze

advance 는 현재 단계의 산출물과 감사 판정이 갖춰졌을 때만 다음 단계로 넘긴다.
revise 는 현재 단위의 감사 기록을 보관함으로 옮기고 수정 횟수를 1 올린다(단위당 최대 2회).
reopen 은 게이트 1 에서 사용자가 설계 수정을 요구했을 때 design 단계로 되돌린다.
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

PHASES = ["intent", "profile", "design", "gate1", "pilot", "stages", "final", "retro", "done"]
PHASE_LABEL = {
    "intent": "의도 정제",
    "profile": "데이터 프로파일링",
    "design": "설계·검토",
    "gate1": "게이트 1 (사용자 승인)",
    "pilot": "파일럿",
    "stages": "단계별 실험",
    "final": "종합 리포트",
    "retro": "회고",
    "done": "종료",
}
MAX_REVISIONS = 2
AUDITS = {
    "design": ["design-reviewer", "method-auditor"],
    "pilot": ["simplicity-auditor", "result-verifier"],
    "stage": ["simplicity-auditor", "method-auditor", "result-verifier"],
}
VERDICT = re.compile(r"^\s*판정\s*[:：]\s*(통과|수정|상신)", re.MULTILINE)
STAGE_NAME = re.compile(r"^[a-z0-9_]+$")


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Project:
    def __init__(self, root: Path):
        self.root = root
        self.state_path = root / "research" / "state.json"

    # ---- 상태 파일
    def load(self) -> dict:
        if not self.state_path.is_file():
            sys.exit("research/state.json 이 없다. 먼저 `state.py init` 을 실행한다.")
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def save(self, st: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(st, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # ---- 단위(감사 대상)와 요구 사항
    def unit(self, st: dict):
        """현재 감사 단위 이름. 감사가 없는 단계는 None."""
        ph = st["phase"]
        if ph in ("design", "pilot"):
            return ph
        if ph == "stages":
            return st["stages"][st["stage_index"]]
        return None

    def auditors(self, st: dict) -> list:
        ph = st["phase"]
        if ph in ("design", "pilot"):
            return AUDITS[ph]
        if ph == "stages":
            return AUDITS["stage"]
        return []

    def has(self, rel: str) -> bool:
        p = self.root / rel
        return p.is_file() and p.stat().st_size > 0

    def verdict(self, unit: str, auditor: str):
        p = self.root / "audits" / f"{unit}__{auditor}.md"
        if not p.is_file():
            return None
        m = VERDICT.search(p.read_text(encoding="utf-8", errors="replace"))
        return m.group(1) if m else "판정 줄 없음"

    def requirements(self, st: dict) -> list:
        """[(종류, 설명, 충족 여부)] — 종류: file | audit"""
        ph = st["phase"]
        req = []

        def need(rel):
            req.append(("file", rel, self.has(rel)))

        if ph == "intent":
            need("research/brief.md")
        elif ph == "profile":
            need("research/data_profile.md")
        elif ph == "design":
            need("research/design.md")
        elif ph == "gate1":
            need("research/protocol.lock.md")
        elif ph == "pilot":
            req.append(("file", "단계 목록 (set-stages)", bool(st["stages"])))
            need("reports/pilot.md")
        elif ph == "stages":
            need(f"reports/{self.unit(st)}.md")
        elif ph == "final":
            need("reports/final.md")
        elif ph == "retro":
            need("research/retro.md")
        unit = self.unit(st)
        for a in self.auditors(st):
            v = self.verdict(unit, a)
            req.append(("audit", f"audits/{unit}__{a}.md → {v or '없음'}", v == "통과"))
        return req

    # ---- 명령
    def cmd_init(self, _):
        if self.state_path.exists():
            sys.exit("research/state.json 이 이미 있다.")
        self.save({"version": 1, "phase": "intent", "stages": [], "stage_index": 0,
                   "revisions": {}, "history": [{"at": now(), "event": "init"}]})
        print("초기화 완료. 현재 단계: intent (의도 정제)")

    def cmd_status(self, args):
        st = self.load()
        if args.json:
            print(json.dumps({"state": st, "unit": self.unit(st),
                              "requirements": self.requirements(st)}, ensure_ascii=False, indent=2))
            return
        ph = st["phase"]
        print(f"단계: {ph} — {PHASE_LABEL[ph]}")
        if st["stages"]:
            marks = []
            for i, s in enumerate(st["stages"]):
                done = ph in ("final", "retro", "done") or (ph == "stages" and i < st["stage_index"])
                cur = ph == "stages" and i == st["stage_index"]
                marks.append(f"{s}{'(완료)' if done else '(진행 중)' if cur else ''}")
            print("실험 단계: " + " → ".join(marks))
        unit = self.unit(st)
        if unit:
            print(f"감사 단위: {unit} / 수정 횟수: {st['revisions'].get(unit, 0)}/{MAX_REVISIONS}")
        req = self.requirements(st)
        if req:
            print("다음 단계로 넘어가려면:")
            for _, desc, ok in req:
                print(f"  [{'O' if ok else 'X'}] {desc}")
        if ph == "gate1":
            print("※ protocol.lock.md 는 사용자가 설계를 승인한 뒤에만 만든다.")

    def cmd_advance(self, args):
        st = self.load()
        ph = st["phase"]
        if ph == "done":
            sys.exit("이미 종료된 연구다.")
        req = self.requirements(st)
        missing = [d for kind, d, ok in req if kind == "file" and not ok]
        failed = [d for kind, d, ok in req if kind == "audit" and not ok]
        if missing:
            sys.exit("진행 불가 — 산출물이 없다:\n  " + "\n  ".join(missing))
        unit = self.unit(st)
        if failed:
            absent = [d for d in failed if d.endswith("없음")]
            if absent:
                sys.exit("진행 불가 — 감사 기록이 없다(감사는 생략할 수 없다):\n  " + "\n  ".join(absent))
            if not args.override:
                sys.exit("진행 불가 — 통과하지 못한 감사가 있다:\n  " + "\n  ".join(failed)
                         + "\n수정하려면 `revise`, 사용자가 그대로 진행을 결정했으면 `advance --override \"사유\"`.")
            self.log_decision(f"감사 판정 무시 진행 ({unit})",
                              [f"사유: {args.override}"] + [f"판정: {d}" for d in failed])
        if ph == "stages" and st["stage_index"] < len(st["stages"]) - 1:
            st["stage_index"] += 1
            to = f"stages:{st['stages'][st['stage_index']]}"
        else:
            st["phase"] = PHASES[PHASES.index(ph) + 1]
            to = st["phase"]
        st["history"].append({"at": now(), "event": "advance", "from": unit or ph, "to": to,
                              **({"override": args.override} if failed else {})})
        self.save(st)
        print(f"진행: {unit or ph} → {to}")
        self.cmd_status(argparse.Namespace(json=False))

    def cmd_revise(self, _):
        st = self.load()
        unit = self.unit(st)
        if not unit:
            sys.exit(f"{st['phase']} 단계에는 감사 단위가 없다.")
        n = st["revisions"].get(unit, 0)
        if n >= MAX_REVISIONS:
            sys.exit(f"수정 한도({MAX_REVISIONS}회) 초과 — {unit} 은(는) 사용자에게 상신한다.")
        archive = self.root / "audits" / "archive"
        archive.mkdir(parents=True, exist_ok=True)
        moved = 0
        for a in self.auditors(st):
            src = self.root / "audits" / f"{unit}__{a}.md"
            if src.is_file():
                src.replace(archive / f"{unit}__{a}.r{n + 1}.md")
                moved += 1
        st["revisions"][unit] = n + 1
        st["history"].append({"at": now(), "event": "revise", "unit": unit, "round": n + 1})
        self.save(st)
        print(f"수정 {n + 1}/{MAX_REVISIONS}회차 시작: {unit} (감사 기록 {moved}건을 audits/archive/ 로 옮김)")
        print("수정 후 해당 단위의 감사를 전부 다시 받는다.")

    def cmd_reopen(self, _):
        st = self.load()
        if st["phase"] != "gate1":
            sys.exit("reopen 은 gate1 단계에서만 쓴다(사용자가 설계 수정을 요구한 경우).")
        if self.has("research/protocol.lock.md"):
            sys.exit("프로토콜이 이미 확정됐다. 변경은 게이트 2 절차를 따른다.")
        k = sum(1 for h in st["history"] if h.get("event") == "reopen") + 1
        archive = self.root / "audits" / "archive"
        archive.mkdir(parents=True, exist_ok=True)
        for a in AUDITS["design"]:
            src = self.root / "audits" / f"design__{a}.md"
            if src.is_file():
                src.replace(archive / f"design__{a}.u{k}.md")
        st["phase"] = "design"
        st["revisions"]["design"] = 0
        st["history"].append({"at": now(), "event": "reopen", "round": k})
        self.save(st)
        print(f"design 단계로 되돌림(사용자 수정 요구 {k}회차). 설계 수정 후 감사를 다시 받는다.")

    def cmd_set_stages(self, args):
        st = self.load()
        if st["phase"] != "pilot":
            sys.exit("단계 목록은 pilot 단계에서만 정한다. 이후 변경은 프로토콜 변경(게이트 2)이다.")
        names = [s.strip() for s in args.names.split(",") if s.strip()]
        bad = [s for s in names if not STAGE_NAME.match(s)]
        if not names or bad or len(set(names)) != len(names):
            sys.exit(f"단계 이름은 중복 없는 영문 소문자·숫자·밑줄이어야 한다: {bad or names}")
        st["stages"], st["stage_index"] = names, 0
        st["history"].append({"at": now(), "event": "set-stages", "stages": names})
        self.save(st)
        print("실험 단계: " + " → ".join(names))

    def log_decision(self, title: str, lines: list) -> None:
        p = self.root / "research" / "decisions.md"
        head = "" if p.is_file() else "# 결정 기록\n\n프로토콜 변경과 감사 판정 무시 진행을 시간순으로 남긴다.\n"
        with p.open("a", encoding="utf-8") as f:
            f.write(f"{head}\n## {now()} — {title}\n" + "".join(f"- {x}\n" for x in lines))


def main() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="연구 진행 상태 관리")
    ap.add_argument("--root", default=None, help="프로젝트 루트 (기본: 이 스크립트 기준)")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("init")
    p = sub.add_parser("status")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("advance")
    p.add_argument("--override", default=None, metavar="사유")
    sub.add_parser("revise")
    sub.add_parser("reopen")
    p = sub.add_parser("set-stages")
    p.add_argument("names")
    args = ap.parse_args()
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[4]
    proj = Project(root)
    cmd = args.cmd or "status"
    if cmd == "status" and not hasattr(args, "json"):
        args.json = False
    getattr(proj, "cmd_" + cmd.replace("-", "_"))(args)


if __name__ == "__main__":
    main()
