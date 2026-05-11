#!/usr/bin/env python3
"""
checkout.py - Batch checkout Defects4J buggy versions for SimFix.

Reads project_bug_id.json (sibling of this script) which has the shape:
    {
        "Chart":    [1, 2, 3, ...],
        "Cli":      [1, 2, ...],
        "Compress": [...],
        "Csv":      [...],
        "Lang":     [...],
        "Math":     [...],
        "Time":     [...]
    }
and checks every (project, bug_id) out to:
    <SIMFIX_ROOT>/projects/<proj_lower>/<proj_lower>_<id>_buggy

All settings are hardcoded as module-level constants. Just run:
    python3 checkout.py
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SIMFIX_ROOT = Path(__file__).resolve().parent
PROJECT_JSON = SIMFIX_ROOT / "project_bug_id.json"
DEFAULT_D4J_HOME = "/home/zhouzhuotong/defects4j"

PROJ_HOME = SIMFIX_ROOT / "projects"
LOG_DIR = SIMFIX_ROOT / "checkout_logs"
WORKERS = 32
FORCE = False  # set to True to delete and re-checkout existing dirs


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def load_bug_ids() -> Dict[str, List[int]]:
    """Return {D4JProjectName: [bug_id, ...]} from project_bug_id.json."""
    if not PROJECT_JSON.is_file():
        sys.exit(f"[checkout.py] project_bug_id.json not found: {PROJECT_JSON}")
    with PROJECT_JSON.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    return {str(k): sorted(int(i) for i in v) for k, v in raw.items()}


def ensure_defects4j(env: Dict[str, str]) -> str:
    """Return absolute path to the `defects4j` executable; abort otherwise."""
    d4j_home = env.get("DEFECTS4J_HOME") or DEFAULT_D4J_HOME
    cli = Path(d4j_home) / "framework" / "bin" / "defects4j"
    if not cli.is_file():
        sys.exit(f"[checkout.py] defects4j cli not found at {cli}; "
                 f"set DEFECTS4J_HOME correctly")
    env["DEFECTS4J_HOME"] = d4j_home
    env["PATH"] = f"{cli.parent}:{env.get('PATH', '')}"
    return str(cli)


def is_dir_nonempty(p: Path) -> bool:
    return p.is_dir() and any(p.iterdir())


# ---------------------------------------------------------------------------
# worker
# ---------------------------------------------------------------------------
def _checkout_one(args_tuple) -> dict:
    d4j_proj, proj_lower, bug_id, d4j_cli, work_dir, log_path, env, force = args_tuple
    log_path.parent.mkdir(parents=True, exist_ok=True)
    work_dir.parent.mkdir(parents=True, exist_ok=True)

    if work_dir.exists():
        if force:
            shutil.rmtree(work_dir)
        elif is_dir_nonempty(work_dir):
            return {"proj": proj_lower, "id": bug_id, "status": "skipped",
                    "reason": "already checked out", "elapsed": 0.0}
        else:
            shutil.rmtree(work_dir)

    cmd = [d4j_cli, "checkout", "-p", d4j_proj, "-v", f"{bug_id}b", "-w", str(work_dir)]
    started = time.time()
    with log_path.open("w", encoding="utf-8") as logf:
        logf.write(f"$ {' '.join(cmd)}\n")
        logf.flush()
        rc = subprocess.call(cmd, stdout=logf, stderr=subprocess.STDOUT, env=env)
    elapsed = time.time() - started
    if rc == 0 and is_dir_nonempty(work_dir):
        return {"proj": proj_lower, "id": bug_id, "status": "ok",
                "rc": rc, "elapsed": elapsed}
    return {"proj": proj_lower, "id": bug_id, "status": "fail",
            "rc": rc, "elapsed": elapsed, "log": str(log_path)}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    proj_to_ids = load_bug_ids()
    if not proj_to_ids:
        sys.exit("[checkout.py] project_bug_id.json is empty")

    env = os.environ.copy()
    d4j_cli = ensure_defects4j(env)
    PROJ_HOME.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    total_bugs = sum(len(v) for v in proj_to_ids.values())
    print(f"[checkout.py] projects        : {list(proj_to_ids.keys())}")
    print(f"[checkout.py] total bugs      : {total_bugs}")
    print(f"[checkout.py] proj_home       : {PROJ_HOME}")
    print(f"[checkout.py] DEFECTS4J_HOME  : {env['DEFECTS4J_HOME']}")
    print(f"[checkout.py] workers         : {WORKERS}")
    print(f"[checkout.py] log_dir         : {LOG_DIR}")
    print(f"[checkout.py] force           : {FORCE}")
    print()

    tasks = []
    for d4j_proj, ids in proj_to_ids.items():
        proj_lower = d4j_proj.lower()
        for bid in ids:
            work_dir = PROJ_HOME / proj_lower / f"{proj_lower}_{bid}_buggy"
            log_path = LOG_DIR / f"{proj_lower}_{bid}.log"
            tasks.append((d4j_proj, proj_lower, bid, d4j_cli,
                          work_dir, log_path, env, FORCE))

    ok, fail, skipped = 0, [], 0
    started = time.time()
    with cf.ProcessPoolExecutor(max_workers=max(1, WORKERS)) as pool:
        for res in pool.map(_checkout_one, tasks):
            tag = res["status"].upper()
            if res["status"] == "ok":
                ok += 1
                print(f"  [{tag}] {res['proj']:9} {res['id']:>3}  ({res['elapsed']:.1f}s)")
            elif res["status"] == "skipped":
                skipped += 1
                print(f"  [{tag}] {res['proj']:9} {res['id']:>3}  ({res['reason']})")
            else:
                fail.append((res["proj"], res["id"]))
                print(f"  [{tag}] {res['proj']:9} {res['id']:>3}  "
                      f"rc={res.get('rc')}  see {res.get('log')}")

    total_elapsed = time.time() - started
    print()
    print(f"[checkout.py] done in {total_elapsed:.1f}s: "
          f"ok={ok}, skipped={skipped}, fail={len(fail)}")
    if fail:
        print(f"[checkout.py] failed bugs: {fail}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
