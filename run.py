#!/usr/bin/env python3
"""
run.py - Multi-process SimFix runner.

Reads project_bug_id.json (sibling of this script) and for every
(D4JProject, bug_id) pair spawns:
    java -Xmx<HEAP> -jar simfix.jar \
         --proj_home=<PROJ_HOME> \
         --proj_name=<proj_lower> --bug_id=<id>
The java process is launched with cwd == SimFix root because
cofix.common.config.Constant uses System.getProperty("user.dir") for HOME.

All settings are hardcoded as module-level constants. Just run:
    python3 run.py
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
SIMFIX_JAR = SIMFIX_ROOT / "simfix.jar"
PROJECT_JSON = SIMFIX_ROOT / "project_bug_id.json"
DEFAULT_D4J_HOME = "/home/zhouzhuotong/defects4j"

PROJ_HOME = SIMFIX_ROOT / "projects"        # parent of <proj_lower>/<proj_lower>_<id>_buggy
LOG_DIR = SIMFIX_ROOT / "run_logs"
SUMMARY_JSON = LOG_DIR / "summary.json"
TIME_JSON = LOG_DIR / "time.json"           # per-bug elapsed time, updated incrementally

WORKERS = 32                                 # parallel SimFix processes
TIMEOUT = 3600 * 3                           # outer per-bug timeout in seconds
HEAP = "4g"                                  # JVM -Xmx
JAVA_BIN: str | None = None                  # None => auto-detect from PATH
SKIP_FIXED = True                            # skip bugs whose patch/<proj>/<id> already has *.java


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def load_bug_ids() -> Dict[str, List[int]]:
    """Return {D4JProjectName: [bug_id, ...]} from project_bug_id.json."""
    if not PROJECT_JSON.is_file():
        sys.exit(f"[run.py] project_bug_id.json not found: {PROJECT_JSON}")
    with PROJECT_JSON.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    return {str(k): sorted(int(i) for i in v) for k, v in raw.items()}


def ensure_env() -> tuple[str, Dict[str, str]]:
    """Return (java_executable, env_dict) with DEFECTS4J_HOME set."""
    env = os.environ.copy()
    d4j_home = env.get("DEFECTS4J_HOME") or DEFAULT_D4J_HOME
    if not Path(d4j_home, "framework", "bin", "defects4j").is_file():
        sys.exit(f"[run.py] DEFECTS4J_HOME invalid: {d4j_home}")
    env["DEFECTS4J_HOME"] = d4j_home
    env["PATH"] = f"{d4j_home}/framework/bin:{env.get('PATH', '')}"

    java = JAVA_BIN or shutil.which("java")
    if not java:
        sys.exit("[run.py] java not found on PATH; set JAVA_BIN at top of script")
    if not SIMFIX_JAR.is_file():
        sys.exit(f"[run.py] simfix.jar not found at {SIMFIX_JAR}; run ./build.sh first")
    return java, env


def patch_dir_for(proj_lower: str, bug_id: int) -> Path:
    return SIMFIX_ROOT / "patch" / proj_lower / str(bug_id)


def is_already_fixed(proj_lower: str, bug_id: int) -> bool:
    p = patch_dir_for(proj_lower, bug_id)
    if not p.is_dir():
        return False
    for _ in p.rglob("*.java"):
        return True
    return False


def load_time_map() -> Dict[str, Dict[str, dict]]:
    """Load existing time.json (if any) so we don't clobber previous stats."""
    if not TIME_JSON.is_file():
        return {}
    try:
        data = json.loads(TIME_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    cleaned: Dict[str, Dict[str, dict]] = {}
    for proj, items in data.items():
        if isinstance(items, dict):
            cleaned[str(proj)] = {str(k): v for k, v in items.items()}
    return cleaned


def save_time_map(time_map: Dict[str, Dict[str, dict]]) -> None:
    """Persist time.json with stable ordering: project asc, bug_id asc."""
    TIME_JSON.parent.mkdir(parents=True, exist_ok=True)
    ordered: Dict[str, Dict[str, dict]] = {}
    for proj in sorted(time_map.keys()):
        items = time_map[proj]
        try:
            keys = sorted(items.keys(), key=lambda x: int(x))
        except ValueError:
            keys = sorted(items.keys())
        ordered[proj] = {k: items[k] for k in keys}
    TIME_JSON.write_text(json.dumps(ordered, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# worker
# ---------------------------------------------------------------------------
def _run_one(args_tuple) -> dict:
    (proj_lower, bug_id, java, jar_path, proj_home_abs, log_path,
     timeout, heap, env, skip_fixed) = args_tuple

    log_path.parent.mkdir(parents=True, exist_ok=True)
    if skip_fixed and is_already_fixed(proj_lower, bug_id):
        return {"proj": proj_lower, "id": bug_id, "status": "skipped",
                "reason": "patch dir already non-empty", "elapsed": 0.0}

    buggy_dir = Path(proj_home_abs) / proj_lower / f"{proj_lower}_{bug_id}_buggy"
    if not buggy_dir.is_dir():
        return {"proj": proj_lower, "id": bug_id, "status": "missing",
                "reason": f"buggy dir not found: {buggy_dir}", "elapsed": 0.0}

    cmd = [
        java,
        f"-Xmx{heap}",
        "-jar", str(jar_path),
        f"--proj_home={proj_home_abs}",
        f"--proj_name={proj_lower}",
        f"--bug_id={bug_id}",
    ]
    started = time.time()
    timed_out = False
    rc = -1
    with log_path.open("w", encoding="utf-8") as logf:
        logf.write(f"$ cd {SIMFIX_ROOT} && {' '.join(cmd)}\n")
        logf.flush()
        try:
            rc = subprocess.call(
                cmd, stdout=logf, stderr=subprocess.STDOUT,
                cwd=str(SIMFIX_ROOT), env=env, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            timed_out = True
            logf.write(f"\n[run.py] killed: outer timeout={timeout}s exceeded\n")
    elapsed = time.time() - started

    fixed = is_already_fixed(proj_lower, bug_id)
    if timed_out:
        status = "timeout"
    elif fixed:
        status = "fixed"
    elif rc == 0:
        status = "no_patch"
    else:
        status = "fail"
    return {"proj": proj_lower, "id": bug_id, "status": status, "rc": rc,
            "elapsed": elapsed, "log": str(log_path)}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    java, env = ensure_env()
    proj_to_ids = load_bug_ids()
    if not proj_to_ids:
        sys.exit("[run.py] project_bug_id.json is empty")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    proj_home_abs = str(PROJ_HOME.resolve())

    total_bugs = sum(len(v) for v in proj_to_ids.values())
    print(f"[run.py] java          : {java}")
    print(f"[run.py] simfix.jar    : {SIMFIX_JAR}")
    print(f"[run.py] cwd           : {SIMFIX_ROOT}")
    print(f"[run.py] proj_home     : {proj_home_abs}")
    print(f"[run.py] DEFECTS4J     : {env['DEFECTS4J_HOME']}")
    print(f"[run.py] projects      : {list(proj_to_ids.keys())}")
    print(f"[run.py] total bugs    : {total_bugs}")
    print(f"[run.py] workers       : {WORKERS}  timeout: {TIMEOUT}s  heap: {HEAP}")
    print(f"[run.py] skip_fixed    : {SKIP_FIXED}")
    print(f"[run.py] log_dir       : {LOG_DIR}")
    print(f"[run.py] time_json     : {TIME_JSON}")
    print()

    tasks = []
    for d4j_proj, ids in proj_to_ids.items():
        proj_lower = d4j_proj.lower()
        for bid in ids:
            log_path = LOG_DIR / f"{proj_lower}_{bid}.log"
            tasks.append((proj_lower, bid, java, str(SIMFIX_JAR), proj_home_abs,
                          log_path, TIMEOUT, HEAP, env, SKIP_FIXED))

    results: list[dict] = []
    started_all = time.time()
    color_map = {
        "FIXED":    "\033[1;32m",
        "NO_PATCH": "\033[1;33m",
        "TIMEOUT":  "\033[1;35m",
        "FAIL":     "\033[1;31m",
        "SKIPPED":  "\033[1;36m",
        "MISSING":  "\033[1;31m",
    }
    time_map: Dict[str, Dict[str, dict]] = load_time_map()
    with cf.ProcessPoolExecutor(max_workers=max(1, WORKERS)) as pool:
        future_map = {pool.submit(_run_one, t): (t[0], t[1]) for t in tasks}
        for fut in cf.as_completed(future_map):
            res = fut.result()
            results.append(res)
            tag = res["status"].upper()
            color = color_map.get(tag, "")
            reset = "\033[0m" if color else ""
            extra = ""
            if res["status"] in ("fail", "no_patch"):
                extra = f"  rc={res.get('rc')}"
            elif res["status"] in ("skipped", "missing"):
                extra = f"  ({res.get('reason')})"
            print(f"  {color}[{tag}]{reset} {res['proj']:9} "
                  f"{res['id']:>3}  {res['elapsed']:7.1f}s{extra}  "
                  f"-> {res.get('log','')}")

            proj_entry = time_map.setdefault(res["proj"], {})
            proj_entry[str(res["id"])] = {
                "elapsed": round(float(res.get("elapsed", 0.0)), 2),
                "status": res["status"],
            }
            try:
                save_time_map(time_map)
            except OSError as exc:
                print(f"[run.py] warn: failed to write {TIME_JSON}: {exc}")

    total_elapsed = time.time() - started_all
    counts = {"fixed": 0, "no_patch": 0, "timeout": 0, "fail": 0,
              "skipped": 0, "missing": 0}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    fixed_by_proj: Dict[str, List[int]] = {}
    for r in results:
        if r["status"] == "fixed":
            fixed_by_proj.setdefault(r["proj"], []).append(r["id"])
    for k in fixed_by_proj:
        fixed_by_proj[k].sort()

    print()
    print(f"[run.py] done in {total_elapsed:.1f}s   "
          + "  ".join(f"{k}={v}" for k, v in counts.items()))
    if fixed_by_proj:
        for proj_lower, ids in fixed_by_proj.items():
            print(f"[run.py] fixed: {proj_lower}: {ids}")

    summary = {
        "projects": list(proj_to_ids.keys()),
        "results": results,
        "counts": counts,
        "fixed_by_proj": fixed_by_proj,
        "elapsed": total_elapsed,
        "simfix_root": str(SIMFIX_ROOT),
        "proj_home": proj_home_abs,
    }
    SUMMARY_JSON.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_JSON.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"[run.py] summary -> {SUMMARY_JSON}")
    try:
        save_time_map(time_map)
        print(f"[run.py] time    -> {TIME_JSON}")
    except OSError as exc:
        print(f"[run.py] warn: failed to write {TIME_JSON}: {exc}")
    return 0 if counts.get("fail", 0) == 0 and counts.get("missing", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
