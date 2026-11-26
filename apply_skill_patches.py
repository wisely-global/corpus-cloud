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


def patch_sync(skill):
    p = os.path.join(skill, "scripts", "sync.py")
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


# The daily digest lists every failure in its 24-hour window, even for a client a later run in the same window
# swept (and "needs you" stays on). Keep a failure or a dropped-after-retries flag only if no later run swept the client.
UNATT_EDITS = (
    ("    for _, r in recs:\n        for k, v in run_failures(r).items():",
     "    swept_at = {}\n    for started_at, r in recs:\n        for k, v in run_failures(r).items():"),
    ("                swept.add(c)\n",
     "                swept.add(c)\n                swept_at[c] = started_at\n"),
    ('                failed.append("%s: failed: %s (%s)" % (c, row.get("reason"), r["run_id"]))',
     '                failed.append((c, started_at, "%s: failed: %s (%s)" % (c, row.get("reason"), r["run_id"])))'),
    ('                    dropped.append("%s (%s)" % (c, r["run_id"]))',
     '                    dropped.append((c, started_at, "%s (%s)" % (c, r["run_id"])))'),
    ('    carry = [x.get("client") for x in st.get("carry_over", [])]\n    totals = st["failure_totals"]',
     '    resolved = sorted({c for c, t, _ in failed + dropped if swept_at.get(c, t) > t})\n'
     '    failed = [x for c, t, x in failed if swept_at.get(c, t) <= t]\n'
     '    dropped = [x for c, t, x in dropped if swept_at.get(c, t) <= t]\n'
     '    carry = [x.get("client") for x in st.get("carry_over", [])]\n    totals = st["failure_totals"]'),
    ('    L.append("")\n    L.append("Run records: corpus-runs/ in Wisely Shared Config.")',
     '    if resolved:\n        L.append("")\n        L.append("Failed earlier in this window, swept since (nothing to do): %s." % ", ".join(resolved))\n'
     '    L.append("")\n    L.append("Run records: corpus-runs/ in Wisely Shared Config.")'),
)


def patch_unattended(skill):
    p = os.path.join(skill, "scripts", "unattended.py")
    s = open(p).read()
    if "swept_at = {}" in s:
        print("already applied")
        return 0
    if any(s.count(old) != 1 for old, _ in UNATT_EDITS):
        print("unattended.py is not the version the patch was written for; not patched", file=sys.stderr)
        return 3
    for old, new in UNATT_EDITS:
        s = s.replace(old, new, 1)
    ast.parse(s)
    open(p + ".tmp", "w").write(s)
    os.replace(p + ".tmp", p)
    print("applied")
    return 0


def main():
    return patch_sync(sys.argv[1]) or patch_unattended(sys.argv[1])


sys.exit(main())
