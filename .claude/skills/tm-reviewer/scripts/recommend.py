#!/usr/bin/env python3
"""Map axis scores (A-H, 1-5) + journal tier to a recommendation.

Usage:
  python recommend.py --tier 1 --scores A=2,B=2,C=3,D=3,E=3,F=2,G=4,H=3
  python recommend.py --tier 2 --scores A=4,B=3,C=3,D=2,E=4,F=3,G=4,H=4 --json

Decision rules mirror references/journal-tiers.md. The output is a floor for the
reviewer's judgement, not a substitute: adjust and record the reason.
"""
import argparse
import json
import sys

AXES = ["A", "B", "C", "D", "E", "F", "G", "H"]
AXIS_NAMES = {
    "A": "Contribution & positioning",
    "B": "Theoretical grounding",
    "C": "Method justification",
    "D": "Validation rigor",
    "E": "Results & case narrative",
    "F": "Discussion, limitations, implications",
    "G": "Internal consistency",
    "H": "Writing & audience fit",
}


def parse_scores(s: str) -> dict:
    scores = {}
    for part in s.split(","):
        k, v = part.strip().split("=")
        k = k.strip().upper()
        v = int(v)
        if k not in AXES or not 1 <= v <= 5:
            raise ValueError(f"bad score '{part}' (axes A-H, values 1-5)")
        scores[k] = v
    missing = [a for a in AXES if a not in scores]
    if missing:
        raise ValueError(f"missing axes: {missing}")
    return scores


def decide(tier: int, s: dict):
    reasons = []
    low3 = [a for a in AXES if s[a] <= 3]
    all_ge4 = all(s[a] >= 4 for a in AXES)

    if tier == 1:
        if s["A"] <= 2 or s["B"] <= 2:
            reasons.append("Tier 1: contribution (A) or theory (B) <= 2 -> Reject")
            return "Reject", reasons
        if s["D"] <= 2:
            reasons.append("Tier 1: validation (D) <= 2 -> at least Major")
            return "Major revision", reasons
        if all_ge4:
            return "Accept", ["Tier 1: all axes >= 4"]
        if all(s[a] >= 4 for a in "ABCDEF") and all(s[a] >= 3 for a in "GH"):
            return "Minor revision", ["Tier 1: substantive axes >= 4, only G/H at 3"]
        reasons.append(f"Tier 1: axes <= 3 present: {low3}")
        return "Major revision", reasons

    if tier == 2:
        if s["A"] == 1 or s["B"] == 1:
            return "Reject", ["Tier 2: contribution (A) or theory (B) == 1"]
        if s["C"] <= 2 or s["D"] <= 2:
            return "Major revision", ["Tier 2: method (C) or validation (D) <= 2"]
        if all_ge4:
            return "Accept", ["Tier 2: all axes >= 4"]
        if all(s[a] >= 3 for a in AXES) and len(low3) <= 2:
            return "Minor revision", [f"Tier 2: all >= 3, {len(low3)} axes at 3"]
        return "Major revision", [f"Tier 2: {len(low3)} axes <= 3: {low3}"]

    if tier == 3:
        if s["G"] == 1 or s["C"] == 1:
            return "Reject", ["Tier 3: consistency (G) or reproducibility (C) == 1"]
        if all_ge4:
            return "Accept", ["Tier 3: all axes >= 4"]
        if len(low3) >= 3:
            return "Major revision", [f"Tier 3: {len(low3)} axes <= 3: {low3}"]
        return "Minor revision", [f"Tier 3: {len(low3)} axes <= 3"]

    raise ValueError("tier must be 1, 2 or 3")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", type=int, required=True, choices=[1, 2, 3])
    ap.add_argument("--scores", required=True)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    try:
        s = parse_scores(a.scores)
    except ValueError as e:
        sys.exit(str(e))
    rec, reasons = decide(a.tier, s)
    weakest = sorted(AXES, key=lambda k: s[k])[:3]
    out = {
        "tier": a.tier,
        "scores": s,
        "recommendation": rec,
        "reasons": reasons,
        "weakest_axes": [f"{k} ({AXIS_NAMES[k]}) = {s[k]}" for k in weakest],
        "mean": round(sum(s.values()) / len(s), 2),
    }
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"Tier {a.tier} -> {rec}")
        for r in reasons:
            print(f"  - {r}")
        print("  weakest: " + "; ".join(out["weakest_axes"]))
        print(f"  mean score: {out['mean']}")


if __name__ == "__main__":
    main()
