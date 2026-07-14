#!/usr/bin/env python3
"""常驻变异语料驱动（patch → kill-test → restore）。

夜跑用法（见 README.md）：对 corpus.json 中每条 status=killed 的变异，把产品码按 old→new
施加、跑其 kill_test 期望**转红**、随后无条件恢复原码。任一条"施加后仍全绿"（SURVIVED）
即语料回归失败——说明对应守门断言被误删/削弱，或产品码演化使锚点漂移。

status=waived/deferred_p0/open 的条目不驱动（仅登记），报为 SKIP + 理由。

驱动本身改造自红队 /tmp/claude-1128/dimmu2/run_mut2.py，锚点与 old/new 内联进 corpus.json
以脱离红队 scratch 常驻。产品码全程只读——`finally` 保证恢复，异常/中断亦不残留。

    PYTHONDONTWRITEBYTECODE=1 python tests/mutants/run_corpus.py            # 跑全部 killed
    PYTHONDONTWRITEBYTECODE=1 python tests/mutants/run_corpus.py V4 Y1 D1   # 只跑子集
    python tests/mutants/run_corpus.py --list                              # 只列语料
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
CORPUS = json.load(open(os.path.join(HERE, "corpus.json"), encoding="utf-8"))

ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", OPENBLAS_NUM_THREADS="1",
           OMP_NUM_THREADS="1", MKL_NUM_THREADS="1", NUMEXPR_NUM_THREADS="1")


def _run_kill_test(nodeid):
    p = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "--no-header", "-x", nodeid],
        cwd=REPO, env=ENV, capture_output=True, text=True)
    last = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else ""
    return p.returncode != 0, last  # red == killed


def run_one(entry):
    path = os.path.join(REPO, entry["file"])
    orig = open(path, encoding="utf-8").read()
    n = orig.count(entry["old"])
    if n != 1:
        return "ANCHOR", f"锚点计数={n}（期望 1）——产品码已演化，请更新 corpus.json"
    try:
        open(path, "w", encoding="utf-8").write(orig.replace(entry["old"], entry["new"]))
        red, last = _run_kill_test(entry["kill_test"])
    finally:
        open(path, "w", encoding="utf-8").write(orig)  # 无条件恢复
    return ("KILLED" if red else "SURVIVED"), last


def main(argv):
    if "--list" in argv:
        for e in CORPUS:
            print(f"{e['id']:7s} {e['wave']:4s} {e['status']:11s} "
                  f"{e['file']}:{e.get('line', 0)}  {e['kill_test'] or '-'}")
        return 0
    want = set(a for a in argv if not a.startswith("-"))
    n_killed = n_fail = n_skip = 0
    for e in CORPUS:
        if want and e["id"] not in want:
            continue
        if not e.get("auto"):
            n_skip += 1
            print(f"[{e['id']:7s}] SKIP     {e['status']:11s} {e['reason'][:80]}")
            continue
        verdict, last = run_one(e)
        ok = verdict == "KILLED"
        n_killed += ok
        n_fail += (not ok)
        flag = "OK " if ok else "!! "
        print(f"[{e['id']:7s}] {flag}{verdict:9s} {e['kill_test']}  {last}")
    print(f"\n语料回归：killed={n_killed}  FAILED={n_fail}  skipped(waived/deferred/open)={n_skip}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
