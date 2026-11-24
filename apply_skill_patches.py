#!/usr/bin/env python3
"""Apply the fixes in patches/ to the installed client-corpus skill, only if missing (idempotent).
Python on purpose: the container has no `patch` tool. Usage: apply_skill_patches.py SKILL_DIR
Exit 0 ok / already applied, 3 could not apply."""
import ast, os, sys

HELPER = '''def norm_last_sweep(man: dict) -> dict:
    """last_sweep is a map of member to ISO time. An older manifest shape stores one bare
    ISO string (or a list); read it as {"legacy": TIME} and write the map back, so the
    old shape converts the first time a sweep touches the manifest."""
    ls = man.get("last_sweep")
    if isinstance(ls, dict):
        return ls
    if isinstance(ls, str) and ls.strip():
        ls = {"legacy": ls.strip()}
    elif isinstance(ls, (list, tuple)):
        ls = {"legacy-%d" % i: v for i, v in enumerate(ls) if isinstance(v, str) and v.strip()}
    else:
        ls = {}
    man["last_sweep"] = ls
    return ls


def merge_rows('''


def main():
    p = os.path.join(sys.argv[1], "scripts", "sync.py")
    s = open(p).read()
    if "def norm_last_sweep" in s:
        print("already applied")
        return 0
    olds = (s.count('(man.get("last_sweep") or {}).values()'), s.count('man.setdefault("last_sweep", {})[member] = U.iso(now)'))
    if olds == (0, 0):
        # The released skill (13.4 or later) no longer has the code this patch changes: the fix is upstream.
        print("fixed upstream: nothing to patch")
        return 0
    if s.count("def merge_rows(") != 1 or olds != (2, 1):
        print("sync.py is not the version the patch was written for; not patched", file=sys.stderr)
        return 3
    s = s.replace("def merge_rows(", HELPER, 1)
    s = s.replace('(man.get("last_sweep") or {}).values()', "norm_last_sweep(man).values()")
    s = s.replace('man.setdefault("last_sweep", {})[member] = U.iso(now)', "norm_last_sweep(man)[member] = U.iso(now)")
    ast.parse(s)
    open(p + ".tmp", "w").write(s)
    os.replace(p + ".tmp", p)
    print("applied")
    return 0


sys.exit(main())
