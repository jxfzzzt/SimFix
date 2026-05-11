#!/usr/bin/env python3
"""
evaluate.py - Summarize SimFix repair results.

SimFix writes successful patches to:
    <SIMFIX_ROOT>/patch/<proj_lower>/<bug_id>/<currentTry>/<n>_<src_file>.java
(see Repair.java around line 265). This script walks that tree and prints,
per project, which bug ids have at least one generated patch. The list of
candidate (project, bug_id) pairs comes from project_bug_id.json.

All settings are hardcoded as module-level constants. Just run:
    python3 evaluate.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Set

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SIMFIX_ROOT = Path(__file__).resolve().parent
PATCH_DIR = SIMFIX_ROOT / "patch"
PROJECT_JSON = SIMFIX_ROOT / "project_bug_id.json"
JSON_OUT = SIMFIX_ROOT / "run_logs" / "evaluate.json"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def load_bug_ids() -> Dict[str, List[int]]:
    """Return {D4JProjectName: [bug_id, ...]} from project_bug_id.json."""
    if not PROJECT_JSON.is_file():
        sys.exit(f"[evaluate.py] project_bug_id.json not found: {PROJECT_JSON}")
    with PROJECT_JSON.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    return {str(k): sorted(int(i) for i in v) for k, v in raw.items()}


def scan_patches(proj_to_ids: Dict[str, List[int]]) -> Dict[str, List[int]]:
    """For each project, return the bug ids whose patch/<proj>/<id>/ has *.java."""
    fixed: Dict[str, List[int]] = {p: [] for p in proj_to_ids}
    if not PATCH_DIR.is_dir():
        return fixed
    for d4j_proj, ids in proj_to_ids.items():
        proj_lower = d4j_proj.lower()
        proj_dir = PATCH_DIR / proj_lower
        if not proj_dir.is_dir():
            continue
        found: Set[int] = set()
        for bug_dir in proj_dir.iterdir():
            if not bug_dir.is_dir():
                continue
            try:
                bug_id = int(bug_dir.name)
            except ValueError:
                continue
            if bug_id not in ids:
                continue
            if any(bug_dir.rglob("*.java")):
                found.add(bug_id)
        fixed[d4j_proj] = sorted(found)
    return fixed


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    proj_to_ids = load_bug_ids()
    fixed = scan_patches(proj_to_ids)
    totals = {k: len(v) for k, v in proj_to_ids.items()}

    print("=== SimFix Repair Result ===")
    print(f"patch_dir : {PATCH_DIR}")
    print()

    total_fixed = 0
    max_name = max((len(k) for k in proj_to_ids), default=8)
    for d4j_proj, ids in proj_to_ids.items():
        fixed_ids = fixed.get(d4j_proj, [])
        total = totals.get(d4j_proj, 0)
        print(f"  {d4j_proj:<{max_name}} : {fixed_ids}  ({len(fixed_ids)} / {total})")
        total_fixed += len(fixed_ids)

    print()
    print(f"Total fixed: {total_fixed}")

    out = {
        "patch_dir": str(PATCH_DIR),
        "fixed": fixed,
        "total_fixed": total_fixed,
        "totals": totals,
    }
    JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"json summary -> {JSON_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
