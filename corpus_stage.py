#!/usr/bin/env python3
"""Stage the Wisely corpus onto local disk so the client-corpus scripts can run in a
container with no Drive for desktop mount.

The skill (unattended.py, sync.py) expects files under
  <ROOT>/Shared drives/Consulting | Clients/<client>/Claude Project Knowledge Base/
  <ROOT>/Shared drives/Consulting | Operations/Wisely Shared Config/corpus-runs/
This tool fills that layout from Drive with rclone (pull), records a snapshot, and after
the run sends back only what the run changed (push), refusing any file whose Drive copy
changed since the pull. It never deletes anything on Drive.

Subcommands (all print JSON; exit 0 ok, 2 usage, 3 conflict/failed push):
  setup      install rclone (pinned, checksum-verified) and write rclone.conf from env
  pull       --clients-file F   copy shared config + the listed clients' corpus folders
  push       copy changed/new files back, verify sizes by read-back
  status     show what a push would send

Environment:
  STAGE_ROOT            mount-shaped root (default ~/Library/CloudStorage/GoogleDrive-wisely-svc)
  RCLONE_BIN            default rclone on PATH, else ~/.local/bin/rclone
  RCLONE_CONFIG         config path (default ~/.config/rclone/rclone.conf)
  CLIENTS_REMOTE        remote rooted at the Clients shared drive   (default clients:)
  OPS_REMOTE            remote rooted at the Operations shared drive (default ops:)
  GDRIVE_SA_JSON        service-account key JSON (setup only)
  CLIENTS_DRIVE_ID, OPS_DRIVE_ID   shared drive ids (setup only)
  RCLONE_VERSION        setup only, default v1.69.1
"""
import argparse, hashlib, json, os, shutil, subprocess, sys, tempfile, urllib.request, zipfile

KB = "Claude Project Knowledge Base"
CLIENTS_DIR = "Shared drives/Consulting | Clients"
OPS_DIR = "Shared drives/Consulting | Operations"
CONFIG_SUB = "Wisely Shared Config"
# Only these parts of Wisely Shared Config are pulled (the rest is not needed by a run).
CONFIG_PULL = ["corpus-runs", "corpus-requests", "corpus-index.json", "client-list.md"]
SNAP = ".stage-snapshot.json"


def env(k, d=None):
    return os.environ.get(k, d)


def root():
    return os.path.expanduser(env("STAGE_ROOT", "~/Library/CloudStorage/GoogleDrive-wisely-svc"))


def rclone():
    b = env("RCLONE_BIN") or shutil.which("rclone") or os.path.expanduser("~/.local/bin/rclone")
    return b


def rc(args, check=True):
    cmd = [rclone()] + args
    cfg = env("RCLONE_CONFIG")
    if cfg:
        cmd += ["--config", cfg]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError("rclone %s failed (%d): %s" % (args[0], p.returncode, p.stderr.strip()[-600:]))
    return p


def remote(which):
    return env("CLIENTS_REMOTE", "clients:") if which == "clients" else env("OPS_REMOTE", "ops:")


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def remote_listing(rem, sub):
    """{relpath: {size, md5}} for files under rem/sub. A missing folder is empty.
    Duplicate titles in one folder make rclone report them; that is returned as an error."""
    p = rc(["lsjson", "-R", "--files-only", "--hash", "--hash-type", "md5", rem + sub], check=False)
    if p.returncode != 0:
        # Only a missing folder on Drive counts as "empty". A missing rclone config or remote must never look like
        # an empty Drive (rclone's own notice says "Config file ... not found", so do not match a bare "not found").
        if "directory not found" in p.stderr and "didn't find section" not in p.stderr and "Failed to create file system" not in p.stderr:
            return {}, []
        raise RuntimeError("lsjson failed: " + p.stderr.strip()[-400:])
    out, names = {}, {}
    for e in json.loads(p.stdout or "[]"):
        names.setdefault(e["Path"], 0)
        names[e["Path"]] += 1
        out[e["Path"]] = {"size": e["Size"], "md5": (e.get("Hashes") or {}).get("md5") or (e.get("Hashes") or {}).get("MD5")}
    dups = sorted(n for n, c in names.items() if c > 1)
    return out, dups


def local_files(base):
    out = {}
    for d, _, fs in os.walk(base):
        for f in fs:
            full = os.path.join(d, f)
            out[os.path.relpath(full, base)] = {"size": os.path.getsize(full), "md5": md5(full)}
    return out


SHADOW = KB + " (shadow)"


def targets(clients, shadow=False):
    """(label, remote_base, local_base) for every tree the run uses."""
    r = root()
    t = []
    for item in CONFIG_PULL:
        t.append(("config:" + item, remote("ops"), CONFIG_SUB + "/" + item, os.path.join(r, OPS_DIR, CONFIG_SUB, item)))
    for c in clients:
        t.append(("client:" + c, remote("clients"), c + "/" + KB, os.path.join(r, CLIENTS_DIR, c, KB)))
        if shadow:  # nightly build requests write a shadow corpus beside the live one
            t.append(("shadow:" + c, remote("clients"), c + "/" + SHADOW, os.path.join(r, CLIENTS_DIR, c, SHADOW)))
    return t


def cmd_setup(a):
    bin_ = os.path.expanduser("~/.local/bin/rclone")
    if not (shutil.which("rclone") or os.path.exists(bin_)):
        v = env("RCLONE_VERSION", "v1.69.1")
        base = "https://downloads.rclone.org/%s/" % v
        z = "rclone-%s-linux-amd64.zip" % v
        tmp = tempfile.mkdtemp()
        urllib.request.urlretrieve(base + z, os.path.join(tmp, z))
        sums = urllib.request.urlopen(base + "SHA256SUMS").read().decode()
        want = [l.split()[0] for l in sums.splitlines() if l.endswith(z)]
        got = hashlib.sha256(open(os.path.join(tmp, z), "rb").read()).hexdigest()
        if not want or want[0] != got:
            print(json.dumps({"ok": False, "error": "rclone checksum mismatch"}))
            return 3
        zipfile.ZipFile(os.path.join(tmp, z)).extractall(tmp)
        os.makedirs(os.path.dirname(bin_), exist_ok=True)
        shutil.copy(os.path.join(tmp, "rclone-%s-linux-amd64" % v, "rclone"), bin_)
        os.chmod(bin_, 0o755)
    sa, cid, oid = env("GDRIVE_SA_JSON"), env("CLIENTS_DRIVE_ID"), env("OPS_DRIVE_ID")
    if not sa and env("GDRIVE_SA_JSON_B64"):  # the same key, base64-encoded so an environment-variable box cannot mangle its quotes or \n escapes
        import base64
        sa = base64.b64decode(env("GDRIVE_SA_JSON_B64")).decode("utf-8")
    if not (sa and cid and oid):
        print(json.dumps({"ok": False, "error": "need GDRIVE_SA_JSON (or GDRIVE_SA_JSON_B64), CLIENTS_DRIVE_ID, OPS_DRIVE_ID"}))
        return 2
    json.loads(sa)  # must be valid JSON
    cfg = os.path.expanduser(env("RCLONE_CONFIG", "~/.config/rclone/rclone.conf"))
    os.makedirs(os.path.dirname(cfg), exist_ok=True)
    keyfile = os.path.join(os.path.dirname(cfg), "sa.json")
    with open(os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        f.write(sa)
    with open(os.open(cfg, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        for name, did in (("clients", cid), ("ops", oid)):
            f.write("[%s]\ntype = drive\nscope = drive\nservice_account_file = %s\nteam_drive = %s\n\n" % (name, keyfile, did))
    p = rc(["lsd", "clients:", "--max-depth", "1"], check=False)
    print(json.dumps({"ok": p.returncode == 0, "config": cfg, "probe": (p.stderr or "")[-300:]}))
    return 0 if p.returncode == 0 else 3


def ensure_remotes():
    """Stop with a clear error when rclone has no clients:/ops: remotes (no config), instead of staging an empty Drive."""
    have = rc(["listremotes"], check=False).stdout.split()
    want = [remote("clients"), remote("ops")]
    missing = [w for w in want if w not in have]
    return missing


def cmd_pull(a):
    missing = ensure_remotes()
    if missing:
        print(json.dumps({"ok": False, "error": "rclone remotes not configured: " + ", ".join(missing) + " (run container-setup.sh)"}))
        return 3
    clients = [l.strip() for l in open(a.clients_file) if l.strip()] if a.clients_file else []
    snap_path = os.path.join(root(), SNAP)
    prev = {"trees": {}, "meta": {}}
    if os.path.exists(snap_path):
        if a.add:  # keep what is staged; pull only clients not yet staged
            prev = json.load(open(snap_path))
        elif a.fresh:
            shutil.rmtree(os.path.join(root(), "Shared drives"), ignore_errors=True)
            os.remove(snap_path)
        else:
            print(json.dumps({"ok": False, "error": "stage holds a previous pull; use --add for more clients, or --fresh to discard it"}))
            return 3
    snap, report = {}, []
    todo = [t for t in targets(clients, a.shadow) if not (a.add and t[3] in prev["trees"])]
    if a.add:
        todo = [t for t in todo if t[0].startswith(("client:", "shadow:"))]
    for label, rem, rsub, lbase in todo:
        rl, dups = remote_listing(rem, rsub)
        if dups:
            report.append({"target": label, "error": "duplicate-titles", "files": dups})
            continue
        if rsub.endswith((".json", ".md")):  # a single file
            os.makedirs(os.path.dirname(lbase), exist_ok=True)
            p = rc(["copyto", rem + rsub, lbase], check=False)
            if p.returncode != 0 and ("directory not found" in p.stderr or "object not found" in p.stderr) \
                    and "didn't find section" not in p.stderr and "Failed to create file system" not in p.stderr:
                snap[lbase] = {}
                report.append({"target": label, "files": 0, "note": "absent on Drive"})
                continue
            if p.returncode != 0:
                report.append({"target": label, "error": "pull-failed", "detail": p.stderr.strip()[-200:]})
                continue
            snap[lbase] = {"": {"size": os.path.getsize(lbase), "md5": md5(lbase)}}
            report.append({"target": label, "files": 1})
            continue
        os.makedirs(lbase, exist_ok=True)
        if rl:
            rc(["copy", rem + rsub, lbase])
        lf = local_files(lbase)
        bad = sorted(k for k, v in lf.items() if k in rl and v["size"] != rl[k]["size"])
        if bad:
            report.append({"target": label, "error": "size-mismatch", "files": bad})
            continue
        snap[lbase] = lf
        report.append({"target": label, "files": len(lf)})
    state = {"trees": {**prev["trees"], **snap},
             "meta": {**prev["meta"], **{lbase: {"label": l, "remote": rem, "rsub": rsub} for l, rem, rsub, lbase in todo}}}
    os.makedirs(root(), exist_ok=True)
    json.dump(state, open(snap_path, "w"), indent=1)
    ok = not any("error" in r for r in report)
    print(json.dumps({"ok": ok, "root": root(), "report": report}, indent=1))
    return 0 if ok else 3


def plan():
    state = json.load(open(os.path.join(root(), SNAP)))
    changes = []
    for lbase, files in state["trees"].items():
        meta = state["meta"][lbase]
        if os.path.isfile(lbase):
            cur = {"": {"size": os.path.getsize(lbase), "md5": md5(lbase)}}
        else:
            cur = local_files(lbase) if os.path.isdir(lbase) else {}
        for rel, info in cur.items():
            before = files.get(rel)
            if before is None or before["md5"] != info["md5"]:
                changes.append({"lbase": lbase, "rel": rel, "new": before is None, "size": info["size"], "md5": info["md5"], "meta": meta})
    # Corpus files first, run records and run state last: a run that dies mid-push must not
    # look finished on Drive while its client writes are missing.
    changes.sort(key=lambda c: 0 if c["meta"]["label"].startswith(("client:", "shadow:")) else 1)
    state["moves"] = []
    for lbase, files in state["trees"].items():
        meta = state["meta"][lbase]
        if not meta["label"].startswith("client:") or not os.path.isdir(lbase):
            continue
        for rel, info in files.items():
            if "/" in rel or not (rel.startswith("pending-") and rel.endswith(".json")) or rel == "pending-failures.json":
                continue
            if os.path.exists(os.path.join(lbase, rel)):
                continue
            arch = os.path.join(lbase, "archive", "pending-applied", rel)
            if os.path.isfile(arch):  # applied: the scanner moved it into the archive
                state["moves"].append({"lbase": lbase, "rel": rel, "md5": info["md5"], "meta": meta})
    return state, changes


def cmd_status(a):
    st, ch = plan()
    print(json.dumps({"would_push": [{"target": c["meta"]["label"], "file": c["rel"], "new": c["new"], "size": c["size"]} for c in ch],
                      "would_trash_applied_pending": [m["meta"]["label"] + "/" + m["rel"] for m in st["moves"]]}, indent=1))
    return 0


def cmd_push(a):
    state, changes = plan()
    results, failed = [], False
    cache = {}
    for c in changes:
        m, rel = c["meta"], c["rel"]
        isfile = rel == ""
        rsub = m["rsub"] if isfile else m["rsub"] + "/" + rel
        key = (m["remote"], m["rsub"])
        if key not in cache:
            cache[key] = remote_listing(m["remote"], m["rsub"])[0] if not isfile else {}
        before = state["trees"][c["lbase"]].get(rel)
        # Conflict check: Drive's copy must still be what we pulled.
        if isfile:
            rl, _ = remote_listing(m["remote"], os.path.dirname(m["rsub"]) or "")
            cur = rl.get(os.path.basename(m["rsub"]))
        else:
            cur = cache[key].get(rel)
        if cur is not None and cur["md5"] == c["md5"]:
            results.append({"file": rsub, "status": "already-current", "size": c["size"]}); continue
        if before is None and cur is not None:
            results.append({"file": rsub, "status": "conflict", "why": "created on Drive since pull"}); failed = True; continue
        if before is not None and (cur is None or cur["md5"] != before["md5"]):
            results.append({"file": rsub, "status": "conflict", "why": "Drive copy changed or vanished since pull"}); failed = True; continue
        src = c["lbase"] if isfile else os.path.join(c["lbase"], rel)
        if a.dry_run:
            results.append({"file": rsub, "status": "dry-run", "size": c["size"]}); continue
        p = rc(["copyto", src, m["remote"] + rsub], check=False)
        if p.returncode != 0:
            results.append({"file": rsub, "status": "failed", "detail": p.stderr.strip()[-200:]}); failed = True; continue
        rl, _ = remote_listing(m["remote"], os.path.dirname(rsub))
        got = rl.get(os.path.basename(rsub))
        if not got or got["size"] != c["size"]:
            results.append({"file": rsub, "status": "readback-failed", "want": c["size"], "got": got and got["size"]}); failed = True; continue
        results.append({"file": rsub, "status": "pushed", "size": c["size"]})
    # A pending sync the scanner applied was moved into archive/pending-applied locally. Copy never
    # deletes, so the original would stay in the KB root and be applied again by the next scanner.
    # Send the move: only once the archive copy is on Drive, and only if the root copy is still
    # exactly what we pulled. rclone deletefile goes to the Drive trash, never a permanent delete.
    for mv in state.get("moves", []):
        m, rel = mv["meta"], mv["rel"]
        rroot = m["rsub"] + "/" + rel
        arch = m["rsub"] + "/archive/pending-applied/" + rel
        if a.dry_run:
            results.append({"file": rroot, "status": "dry-run-move"}); continue
        rl, _ = remote_listing(m["remote"], m["rsub"] + "/archive/pending-applied")
        ar = rl.get(rel)
        root_rl, _ = remote_listing(m["remote"], m["rsub"])
        cur = root_rl.get(rel)
        if cur is None:
            results.append({"file": rroot, "status": "move-already-done"}); continue
        if not ar or ar["md5"] != mv["md5"] or cur["md5"] != mv["md5"]:
            results.append({"file": rroot, "status": "move-skipped", "why": "archive copy missing or root copy changed"}); failed = True; continue
        p = rc(["deletefile", "--drive-use-trash=true", m["remote"] + rroot], check=False)
        if p.returncode != 0:
            results.append({"file": rroot, "status": "move-failed", "detail": p.stderr.strip()[-200:]}); failed = True; continue
        results.append({"file": rroot, "status": "moved-to-archive"})
    print(json.dumps({"ok": not failed, "results": results}, indent=1))
    return 3 if failed else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    s = ap.add_subparsers(dest="cmd", required=True)
    s.add_parser("setup").set_defaults(f=cmd_setup)
    p = s.add_parser("pull"); p.add_argument("--clients-file"); p.add_argument("--fresh", action="store_true"); p.add_argument("--add", action="store_true"); p.add_argument("--shadow", action="store_true"); p.set_defaults(f=cmd_pull)
    p = s.add_parser("push"); p.add_argument("--dry-run", action="store_true"); p.set_defaults(f=cmd_push)
    s.add_parser("status").set_defaults(f=cmd_status)
    a = ap.parse_args()
    try:
        sys.exit(a.f(a))
    except (RuntimeError, OSError, json.JSONDecodeError) as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        sys.exit(3)


if __name__ == "__main__":
    main()
