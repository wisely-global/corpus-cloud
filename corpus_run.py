#!/usr/bin/env python3
"""Mechanics of the hourly unattended corpus run in the cloud container.

The judgement parts of a run (reading Drive documents, summarising, composing sweep deltas,
reading the Accounts board, sending board write-backs, email and Slack) stay with the agent.
This tool does the repeated mechanical parts, in the order a run needs them, and prints JSON.

  begin [--mode changed|nightly]  check rclone/skill, stage Wisely Shared Config, start the run record
  discover                   one Drive listing: files changed since the last run + pending syncs
                             (nightly: also every client with manifest activity in the last 90 days)
  stages-from-board --file F turn a saved Accounts board read into stages.json for the candidates
  plan --stages FILE         plan the run, stage the corpus folders it needs, preflight each client
  maintenance                nightly only: the clients the night's maintenance pass can fix (cap 6), staged
  requests                   nightly only: pending build requests (at most two a run)
  shadow CLIENT              nightly only: stage the client's shadow corpus folder for a build request
  apply-pending CLIENT       undo copies + apply a client's pending syncs (restores on a crash)
  undo CLIENT                undo copies before a sweep writes (safe to repeat)
  drive-diff CLIENT          the client's Drive files that are new or changed against its manifest
  demote CLIENT [--file F]   nightly only: demote an over-budget current-state.md or now.md (handles the undo copies)
  record-swept CLIENT        record a client as swept (builds the --file list from the undo folder)
  wrap CLIENT                after a client's writes: maintenance, brief.json, company index
  advise [--run ID|--hours N] a plain-text "how to resolve" block (owner, what it means, commands) for the emails
  finish [--no-push]         finish the record, fold the tidy queue, push everything to Drive

Environment: STAGE_ROOT (as corpus_stage.py), SKILL_DIR (default: the installed client-corpus
skill), CORPUS_WORK (scratch folder, default ~/.corpus-work), GMAIL_SWEEP (on|off, default off). Exit 3 means stop and treat as an error.
"""
import argparse, glob, json, os, shutil, subprocess, sys, time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
KB = "Claude Project Knowledge Base"
CLIENTS = "Shared drives/Consulting | Clients"
OPS = "Shared drives/Consulting | Operations/Wisely Shared Config"
UNDO_FILES = ("now.md", "current-state.md", "changelog.md", "source-manifest.json")


def root():
    return os.path.expanduser(os.environ.get("STAGE_ROOT", "~/Library/CloudStorage/GoogleDrive-wisely-svc"))


def work():
    d = os.path.expanduser(os.environ.get("CORPUS_WORK", "~/.corpus-work"))
    os.makedirs(d, exist_ok=True)
    return d


def skill():
    d = os.environ.get("SKILL_DIR")
    if d:
        return d
    c = sorted(glob.glob(os.path.expanduser("~/.claude/plugins/synced/*/client-corpus*/skills/client-corpus")))
    return c[-1] if c else None


def runs_dir():
    return os.path.join(root(), OPS, "corpus-runs")


def kb_of(client):
    return os.path.join(root(), CLIENTS, client, KB)


def out(obj, code=0):
    print(json.dumps(obj, indent=1, ensure_ascii=False))
    sys.exit(code)


def sh(cmd, check=False):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError("%s failed (%d): %s" % (cmd[1] if len(cmd) > 1 else cmd[0], p.returncode, (p.stderr or p.stdout).strip()[-500:]))
    return p


def py(script, args):
    return sh([sys.executable, os.path.join(skill(), "scripts", script)] + args)


def jpy(script, args):
    p = py(script, args)
    try:
        return p.returncode, json.loads(p.stdout or "{}")
    except json.JSONDecodeError:
        return p.returncode, {"raw": (p.stdout + p.stderr).strip()[-500:]}


def rclone(args):
    b = os.environ.get("RCLONE_BIN") or shutil.which("rclone") or os.path.expanduser("~/.local/bin/rclone")
    cmd = [b] + args
    if os.environ.get("RCLONE_CONFIG"):
        cmd += ["--config", os.environ["RCLONE_CONFIG"]]
    return sh(cmd)


def stage(args):
    return sh([sys.executable, os.path.join(HERE, "corpus_stage.py")] + args)


def state():
    p = os.path.join(work(), "run.json")
    if not os.path.exists(p):
        out({"ok": False, "error": "no run started: run `begin` first"}, 3)
    return json.load(open(p))


def save(name, obj):
    json.dump(obj, open(os.path.join(work(), name), "w"), indent=1)


def load(name, default=None):
    p = os.path.join(work(), name)
    return json.load(open(p)) if os.path.exists(p) else default


def event(kind, detail):
    s = state()
    py("unattended.py", ["record", "event", "--runs-dir", runs_dir(), "--run-id", s["run_id"], "--kind", kind, "--detail", detail[:200]])


def record_client(client_short, outcome, reason=None, sources="", extra=None):
    s = state()
    a = ["record", "client", "--runs-dir", runs_dir(), "--run-id", s["run_id"], "--client", client_short, "--outcome", outcome]
    if reason:
        a += ["--reason", reason]
    if sources:
        a += ["--sources", sources]
    a += extra or []
    return jpy("unattended.py", a)


def gmail_on():
    """GMAIL_SWEEP=on lets a run sweep Gmail. Off by default: the service account's mailbox holds no Clients/* labels,
    so client email reaches the corpus through people's own sweeps (pending syncs), not through this task."""
    return os.environ.get("GMAIL_SWEEP", "off").strip().lower() in ("on", "1", "yes", "true")


def strip_gmail(plan):
    """Drop the gmail source from every planned and carried-over client (the carry-over is written to the run
    state by `record finish`, so a later hourly run must not inherit a source this task cannot read)."""
    for key in ("sweep_now", "carry_over"):
        for c in plan.get(key, []):
            if "sources" in c:
                c["sources"] = [x for x in c["sources"] if x != "gmail"]
    return plan


def folder_name(client):
    """Accept either the Drive folder name or the registry short name for a client (the sessions mixed them up once)."""
    cands = load("candidates.json", {}) or {}
    if client in cands:
        return client
    inv = {v: k for k, v in (load("shorts.json", {}) or {}).items()}
    return inv.get(client, client)


def short_of(client):
    return (load("shorts.json", {}) or {}).get(client, client)


# ---------------------------------------------------------------- begin
def cmd_begin(a):
    probs = []
    if not skill() or not os.path.exists(os.path.join(skill(), "scripts", "unattended.py")):
        probs.append("client-corpus skill not available")
    try:
        remotes = rclone(["listremotes"]).stdout.split()
    except Exception as e:  # noqa: BLE001
        remotes, probs = [], probs + ["rclone not available: %s" % e]
    for r in ("clients:", "ops:"):
        if remotes and r not in remotes:
            probs.append("rclone remote %s missing" % r)
    if probs:
        out({"ok": False, "error": "; ".join(probs)}, 3)
    p = stage(["pull", "--fresh"])
    if p.returncode != 0:
        out({"ok": False, "error": "stage pull failed", "detail": (p.stdout + p.stderr)[-600:]}, 3)
    rc, d = jpy("unattended.py", ["record", "start", "--runs-dir", runs_dir(), "--mode", a.mode])
    if rc == 3:
        out({"ok": True, "stop": "another run started less than three hours ago is still in progress"}, 0)
    if rc != 0:
        out({"ok": False, "error": "record start failed", "detail": d}, 3)
    for f in glob.glob(os.path.join(work(), "*.json")):
        os.remove(f)
    save("run.json", {"run_id": d["run_id"], "since": d["since"], "mode": a.mode, "started": time.time(), "interrupted": d.get("interrupted", [])})
    out({"ok": True, "run_id": d["run_id"], "mode": a.mode, "since": d["since"], "interrupted": d.get("interrupted", [])})


# ---------------------------------------------------------------- discover
def parse_iso(t):
    return datetime.strptime(t[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def nightly_activity(ids, days=90):
    """Clients with a corpus whose source-manifest.json shows activity in the last `days` days.
    Reads each corpus client's manifest straight from Drive, in parallel (the index has no last_activity)."""
    from concurrent.futures import ThreadPoolExecutor
    idx = os.path.join(root(), OPS, "corpus-index.json")
    try:
        rows = json.load(open(idx)).get("clients", [])
    except Exception:  # noqa: BLE001
        rows = []
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400

    def one(r):
        name = r.get("folder_name")
        if not name or name not in ids:
            return None
        p = subprocess.run([os.environ.get("RCLONE_BIN") or shutil.which("rclone") or os.path.expanduser("~/.local/bin/rclone"),
                            "cat", "clients:%s/%s/source-manifest.json" % (name, KB)] +
                           (["--config", os.environ["RCLONE_CONFIG"]] if os.environ.get("RCLONE_CONFIG") else []),
                           capture_output=True, text=True)
        if p.returncode != 0:
            return None
        try:
            la = (json.loads(p.stdout).get("last_activity") or "")[:10]
            t = datetime.strptime(la, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() if la else 0
        except Exception:  # noqa: BLE001
            return None
        return {"client": r.get("short") or name, "folder_name": name, "last_activity": la} if t >= cutoff else None

    with ThreadPoolExecutor(max_workers=8) as ex:
        return [x for x in ex.map(one, rows) if x]


def carried_over_folders(ids):
    """Drive folder names of the clients the last completed run carried over (corpus-runs/corpus-run-state.json).
    A carry-over entry names the client by registry short name or by folder name, so the corpus index maps
    short names back to folders. Only folders that exist on Drive (`ids`) are returned."""
    try:
        co = json.load(open(os.path.join(runs_dir(), "corpus-run-state.json"))).get("carry_over") or []
    except Exception:  # noqa: BLE001
        return []
    try:
        rows = json.load(open(os.path.join(root(), OPS, "corpus-index.json"))).get("clients", [])
    except Exception:  # noqa: BLE001
        rows = []
    by_short = {r["short"]: r["folder_name"] for r in rows if r.get("short") and r.get("folder_name")}
    found = []
    for e in co:
        name = e.get("client") if isinstance(e, dict) else e
        folder = name if name in ids else by_short.get(name)
        if folder in ids and folder not in found:
            found.append(folder)
    return found


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def watch_path():
    return os.path.join(runs_dir(), "pending-watch.json")


def write_pending_watch(pend, ids):
    """corpus-runs/pending-watch.json: what is waiting to be applied (and what failed), as of the last discover. It is how the
    07:00 summary (which reads only the run records) can say a sync has waited more than a day."""
    try:
        old = json.load(open(watch_path())).get("clients", {})
    except Exception:  # noqa: BLE001
        old = {}
    clients, failfile = {}, set()
    for e in pend:
        parts = e["Path"].split("/")
        if parts[0] not in ids or not parts[2].startswith("pending-") or not parts[2].endswith(".json"):
            continue
        if parts[2] == "pending-failures.json":
            failfile.add(parts[0])
        else:
            clients.setdefault(parts[0], {"waiting": [], "failed": []})["waiting"].append({"file": parts[2], "since": e["ModTime"][:19] + "Z"})
    for c in failfile:
        # A failed sync stays put as a pending file, so a failure list is open only while a pending file still waits
        # beside it. When a tidy folds the sync in by hand and archives the file, nothing rewrites the list; counting
        # it anyway re-raised the same "failed to apply" line every hour (Service Foods, Sydney Opera House).
        if clients.get(c, {}).get("waiting"):
            clients[c]["failed"] = old.get(c, {}).get("failed") or ["see pending-failures.json"]
    os.makedirs(runs_dir(), exist_ok=True)
    json.dump({"updated": now_iso(), "clients": clients}, open(watch_path(), "w"), indent=1)


def update_pending_watch():
    """At finish: syncs applied this run are no longer waiting; syncs that failed to apply are recorded as failed."""
    try:
        w = json.load(open(watch_path()))
    except Exception:  # noqa: BLE001
        return
    applied = set(load("applied-syncs.json", []))
    failed = load("failed-syncs.json", {})
    for c, v in list(w.get("clients", {}).items()):
        v["waiting"] = [x for x in v.get("waiting", []) if _pend_id(x["file"]) not in applied]
        if c in failed:
            v["failed"] = failed[c]
        if not v["waiting"] and not v.get("failed"):
            del w["clients"][c]
    json.dump(w, open(watch_path(), "w"), indent=1)


def split_top_folders(top):
    """({name: id} for client folders with a unique name, {name: [ids]} for names used by more than one folder).
    Two top-level folders with one name cannot be told apart by name, and a name-keyed map would silently keep
    the last one, so such a client is left out of the run (and reported) until a person merges or renames them."""
    by_name = {}
    for e in top:
        by_name.setdefault(e["Name"], []).append(e["ID"])
    return ({n: v[0] for n, v in by_name.items() if len(v) == 1}, {n: v for n, v in by_name.items() if len(v) > 1})


def cmd_discover(a):
    s = state()
    since = parse_iso(s["since"])
    top = json.loads(rclone(["lsjson", "clients:", "--dirs-only", "--no-modtime", "--no-mimetype"]).stdout)
    ids, dup_folders = split_top_folders(top)
    # ONE whole-drive file listing straight from the Drive API (not the mount). It yields the files changed
    # since the last run and the pending syncs (client/KB/pending-*.json), which are old files, so no age filter.
    # Corpus subfolders (archive, session-logs, undo...) are not listed at all.
    p = rclone(["lsjson", "clients:", "-R", "--fast-list", "--files-only", "--no-mimetype",
                "--filter", "- **/%s/*/**" % KB, "--filter", "- **/%s (shadow)/**" % KB, "--filter", "+ **"])
    listing = json.loads(p.stdout or "[]")
    changed = [e for e in listing if KB not in e["Path"].split("/")[:-1]]
    pend = [e for e in listing if len(e["Path"].split("/")) == 3 and e["Path"].split("/")[1] == KB]
    changes, byc = [], {}
    for e in changed:
        mt = parse_iso(e["ModTime"])
        if mt <= since:
            continue
        parts = e["Path"].split("/")
        cli = parts[0]
        if cli not in ids or len(parts) < 2:
            continue
        changes.append({"file_id": e["ID"], "title": parts[-1], "modified_time": e["ModTime"][:19] + "Z",
                        "client_folder_id": ids[cli], "client_folder_name": cli, "path": parts[:-1]})
        byc.setdefault(cli, {"changes": 0, "pending": []})["changes"] += 1
    for e in pend:
        parts = e["Path"].split("/")
        if parts[2].startswith("pending-") and parts[2].endswith(".json") and parts[2] != "pending-failures.json" and parts[0] in ids:
            byc.setdefault(parts[0], {"changes": 0, "pending": []})["pending"].append(parts[2])
    # Never guess which of two same-named folders is the client. Say so only when work is actually blocked
    # (a changed file since the last run or a pending sync under that name); an idle duplicate, such as an empty
    # template shell from a double create, is a maintainer note, not an error the hourly run re-raises every hour.
    for name, fids in sorted(dup_folders.items()):
        blocked = any(e["Path"].split("/")[0] == name and len(e["Path"].split("/")) > 1 and parse_iso(e["ModTime"]) > since for e in changed) \
            or any(e["Path"].split("/")[0] == name and e["Path"].split("/")[2].startswith("pending-") for e in pend)
        note = "%s: duplicate-titles: %d top-level client folders share this name (%s)" % (name, len(fids), ", ".join(f[:8] for f in fids))
        if blocked:
            record_client(name, "skipped", "duplicate-titles", "drive")
            event("maintainer-note", note)
        else:
            event("maintainer-note", note + " (idle: no changed files or pending syncs, so nothing is blocked)")
    # Carried-over clients must be candidates too: the registry is built from the candidates only, so a client
    # carried over from the last run with no new Drive change would otherwise get no registry row and be
    # dropped as no-registry-entry (and re-carried by the next nightly run, forever).
    for name in carried_over_folders(ids):
        byc.setdefault(name, {"changes": 0, "pending": []})
    write_pending_watch(pend, ids)
    activity = []
    if s.get("mode") == "nightly":
        activity = nightly_activity(ids)
        for row in activity:  # active clients join the candidates so their stage is looked up and gmail/monday swept
            byc.setdefault(row["folder_name"], {"changes": 0, "pending": []})
        save("activity.json", activity)
    save("changes.json", changes)
    save("candidates.json", {c: dict(v, folder_id=ids[c]) for c, v in byc.items()})
    npend = sum(len(v["pending"]) for v in byc.values())
    event("source", "drive (Drive API listing via rclone): %d files since last run, %d pending syncs" % (len(changes), npend))
    out({"ok": True, "mode": s.get("mode"), "files_changed": len(changes), "since": s["since"], "active_clients_90d": len(activity),
         "duplicate_folders": dup_folders or None,
         "clients": [{"folder_name": c, "folder_id": ids[c], "changed_files": v["changes"], "pending": v["pending"]} for c, v in sorted(byc.items())],
         "next": "look up each client's stage on the Accounts board (Account Status, Short name), write stages.json {folder_name: {short, stage}}, then run plan"})


# ---------------------------------------------------------------- plan
def cmd_plan(a):
    s = state()
    cands = load("candidates.json", {})
    stages = json.load(open(a.stages))
    missing = [c for c in cands if c not in stages]
    if missing:
        out({"ok": False, "error": "stage missing for: " + ", ".join(missing)}, 3)
    reg = [{"short_name": stages[c].get("short") or c, "stage": stages[c].get("stage", ""), "folder_id": v["folder_id"],
            "folder_name": c, "aliases": stages[c].get("aliases", [])} for c, v in cands.items()]
    save("registry.json", reg)
    save("shorts.json", {c: stages[c].get("short") or c for c in cands})
    rd = os.path.join(runs_dir(), "corpus-run-state.json")
    mode = s.get("mode", "changed")
    args = ["plan", "--changes", os.path.join(work(), "changes.json"), "--registry", os.path.join(work(), "registry.json"), "--mode", mode]
    if os.path.exists(rd):
        args += ["--state", rd]
    if mode == "nightly":
        act = [{"client": stages[r["folder_name"]].get("short") or r["folder_name"], "last_activity": r["last_activity"]} for r in load("activity.json", [])
               if r["folder_name"] in stages]
        save("activity-plan.json", act)
        args += ["--activity", os.path.join(work(), "activity-plan.json")]
    rc, plan = jpy("unattended.py", args)
    if rc != 0:
        out({"ok": False, "error": "plan failed", "detail": plan}, 3)
    if not gmail_on():
        strip_gmail(plan)
        event("source", "gmail: not swept by this task (owner setting); client email arrives through people's own sweeps")
    save("plan.json", plan)
    byshort = {r["short_name"]: r["folder_name"] for r in reg}
    sweep = [byshort.get(c["client"], c["client"]) for c in plan["sweep_now"]]
    sources_of = {byshort.get(c["client"], c["client"]): c.get("sources", []) for c in plan["sweep_now"]}
    stage_of = {r["folder_name"]: (r["stage"] or "") for r in reg}
    pend_only = [c for c, v in cands.items() if v["pending"] and c not in sweep and stage_of[c] in ("", "client", "past-client")]
    todo = list(dict.fromkeys(sweep + pend_only))
    if todo:
        cf = os.path.join(work(), "clients.txt")
        open(cf, "w").write("\n".join(todo) + "\n")
        p = stage(["pull", "--add", "--clients-file", cf])
        try:
            rep = {r["target"]: r for r in json.loads(p.stdout)["report"]}
        except Exception:  # noqa: BLE001
            out({"ok": False, "error": "client pull failed", "detail": (p.stdout + p.stderr)[-600:]}, 3)
    else:
        rep = {}
    result = []
    for c in todo:
        short = short_of(c)
        r = rep.get("client:" + c, {})
        entry = {"folder_name": c, "short": short, "sweep": c in sweep, "sources": sources_of.get(c, []), "pending": cands[c]["pending"]}
        if r.get("error"):
            reason = "duplicate-titles" if r["error"] == "duplicate-titles" else "write-failed"
            record_client(short, "skipped" if reason == "duplicate-titles" else "failed", reason, "drive")
            event("maintainer-note", "%s: %s: %s" % (short, r["error"], ", ".join(r.get("files", []))[:120]))
            entry.update(status="skipped", reason=r["error"], files=r.get("files"))
            result.append(entry)
            continue
        kb = kb_of(c)
        titles = json.loads(rclone(["lsjson", "clients:%s/%s" % (c, KB), "--no-modtime"]).stdout or "[]")
        tf = os.path.join(work(), "titles-%d.json" % len(result))
        json.dump([{"id": e["ID"], "title": e["Name"], "mimeType": e.get("MimeType", "")} for e in titles], open(tf, "w"))
        names = {e["Name"] for e in titles}
        if not os.path.isdir(kb) or not titles:
            record_client(short, "skipped", "no-corpus", "drive")
            entry.update(status="skipped", reason="no-corpus")
        else:
            rc, pf = jpy("unattended.py", ["preflight", "--corpus", kb, "--titles", tf])
            if pf.get("verdict") == "skip":
                record_client(short, "skipped", pf.get("reason"), "drive")
                event("maintainer-note", "%s: %s: %s" % (short, pf.get("reason"), ", ".join(pf.get("detail") or [])[:120]))
                entry.update(status="skipped", reason=pf.get("reason"))
            else:
                _, om = jpy("unattended.py", ["open-marker", "--changelog", os.path.join(kb, "changelog.md"), "--markers-dir", os.path.join(kb, "session-logs")])
                if session_still_open(om, ended_session_markers(kb)):
                    record_client(short, "skipped", "session-open", "drive")
                    entry.update(status="skipped", reason="session-open")
                else:
                    try:
                        n = len(json.load(open(os.path.join(kb, "source-manifest.json"))).get("sources", []))
                    except Exception:  # noqa: BLE001
                        n = None
                    entry.update(status="ready", manifest_sources=n, stale_marker=bool(om.get("stale")), titles=tf, kb_files=sorted(names))
        result.append(entry)
    save("todo.json", result)
    out({"ok": True, "run_id": s["run_id"], "mode": mode, "plan_skipped": plan.get("skipped"), "unregistered": plan.get("unregistered"),
         "ambiguous": plan.get("ambiguous"), "carry_over": plan.get("carry_over"), "clients": result})


# ---------------------------------------------------------------- undo / apply
def undo_files(client):
    kb = kb_of(client)
    names = list(UNDO_FILES) + sorted(f for f in os.listdir(kb) if "register" in f and f.endswith((".md", ".tsv")))
    s = state()
    done, bad = [], []
    for f in names:
        if not os.path.isfile(os.path.join(kb, f)):
            continue
        rc, d = jpy("unattended.py", ["undo", "--corpus", kb, "--run-id", s["run_id"], "--file", os.path.join(kb, f)])
        (done if rc in (0, 3) else bad).append(f)
    return done, bad


def cmd_undo(a):
    done, bad = undo_files(a.client)
    out({"ok": not bad, "files": done, "failed": bad}, 0 if not bad else 3)


def _pend_id(fname):
    """pending-2026-11-17-aayushb-1630-p1.json -> 2026-11-17-aayushb-1630 (a multi-part sync is one id)."""
    import re
    return re.sub(r"-p\d+$", "", fname[len("pending-"):-len(".json")])


def ended_session_markers(kb):
    """Names of open-*.md markers whose session has ended: a complete pending sync (every part present, closed_at
    set) names the marker, and applying it is what closes the marker. A user session that files a pending sync leaves
    its marker open, so without this the open-session skip would hold the client back for 24 hours and the pending
    sync that closes the marker would never run."""
    groups = {}
    for fn in sorted(os.listdir(kb)) if os.path.isdir(kb) else []:
        if not (fn.startswith("pending-") and fn.endswith(".json")) or fn == "pending-failures.json":
            continue
        try:
            env = json.load(open(os.path.join(kb, fn), encoding="utf-8"))
            part, parts, delta = int(env["part"]), int(env["parts"]), env["delta"]
        except (OSError, ValueError, KeyError, TypeError):
            continue  # unreadable or a different shape: leave it to apply-pending, and keep the skip
        g = groups.setdefault(_pend_id(fn), {"parts": parts, "have": set(), "delta": None})
        g["have"].add(part)
        if part == 1:
            g["delta"] = delta  # the light keys, marker and closed_at, travel in part 1
    ended = set()
    for g in groups.values():
        d = g["delta"] or {}
        if g["have"] == set(range(1, g["parts"] + 1)) and d.get("marker") and d.get("closed_at"):
            ended.add(os.path.basename(d["marker"]))
    return ended


def session_still_open(om, ended):
    """The skill's open-marker result says skip, but a marker that a complete pending sync is waiting to close is not
    a live session. Anything else keeps the skip: an in-changelog marker, a marker with no readable time, or a
    marker no complete pending sync names."""
    if not om.get("skip"):
        return False
    if om.get("undated"):
        return True
    return any(m.get("line") not in ended for m in om.get("open", []))


def cmd_apply(a):
    s = state()
    kb = kb_of(a.client)
    short = short_of(a.client)
    pend = sorted(f for f in os.listdir(kb) if f.startswith("pending-") and f.endswith(".json") and f != "pending-failures.json")
    try:
        known = {x["id"] for x in json.load(open(os.path.join(kb, "pending-failures.json"))).get("failed", [])}
    except Exception:  # noqa: BLE001
        known = set()
    fresh = [f for f in pend if _pend_id(f) not in known]
    if not fresh:
        out({"ok": True, "applied": [], "skipped": "no pending syncs" if not pend else "only syncs already recorded as failed"})
    done, bad = undo_files(a.client)
    if bad:
        record_client(short, "failed", "undo-copy-failed", "drive")
        out({"ok": False, "error": "undo copy failed: " + ", ".join(bad)}, 3)
    rc, d = jpy("sync.py", ["apply-pending", "--kb", kb])
    if "applied" not in d:  # the script crashed or printed nothing usable (sync.py exits 1 when a sync is left failed, with JSON)
        restored = []
        for f in done:  # put back anything the crashed apply touched
            u = os.path.join(kb, "archive", "undo", s["run_id"], f)
            if os.path.isfile(u) and open(u, "rb").read() != open(os.path.join(kb, f), "rb").read():
                shutil.copy(u, os.path.join(kb, f))
                restored.append(f)
        record_client(short, "failed", "write-failed", "drive")
        event("maintainer-note", "%s: pending sync not applied: apply-pending error; restored %s" % (short, ",".join(restored) or "nothing"))
        out({"ok": False, "error": "apply-pending failed", "detail": (d.get("raw") or "")[-600:], "restored": restored}, 3)
    save("applied-syncs.json", sorted(set(load("applied-syncs.json", [])) | {x["id"] for x in d["applied"]}))
    if d.get("failed"):
        ff = load("failed-syncs.json", {})
        ff[a.client] = [f.get("id") for f in d["failed"]]
        save("failed-syncs.json", ff)
    new_fail = [f for f in d.get("failed", []) if f.get("id") not in known]
    if new_fail:  # a newly failed sync is news; one already in pending-failures.json is not repeated every run
        event("maintainer-note", "%s: pending sync failed to apply: %s" % (short, "; ".join("%s (%s)" % (f.get("id"), (f.get("errors") or [""])[0][:60]) for f in new_fail)))
    out({"ok": True, "applied": [x["id"] for x in d["applied"]], "failed": d.get("failed"), "new_failures": [f.get("id") for f in new_fail],
         "waiting": d.get("waiting_for_parts"),
         "board_payloads": [dict(id=x["id"], **{k: x["board_payload"][k] for k in ("boardId", "itemId", "columnValues")})
                            for x in d["applied"] if x.get("board_payload")],
         "board_queue": d.get("board_queue")})


def cmd_drive_diff(a):
    """Files in the client's Drive folder that are new or changed against the corpus manifest (the whole folder,
    as unattended.md step 4 asks; the change list only says which clients to visit)."""
    cands = load("candidates.json", {})
    fid = cands.get(a.client, {}).get("folder_id")
    kb = kb_of(a.client)
    try:
        man = json.load(open(os.path.join(kb, "source-manifest.json")))
    except Exception as e:  # noqa: BLE001
        out({"ok": False, "error": "manifest unreadable: %s" % e}, 3)
    known = {}
    for r in man.get("sources", []):
        if r.get("type") == "drive" and r.get("id"):
            known[r["id"]] = r.get("modifiedTime") or ""
    listing = json.loads(rclone(["lsjson", "clients:%s" % a.client, "-R", "--fast-list", "--files-only", "--no-mimetype",
                                 "--filter", "- %s/**" % KB, "--filter", "- **/%s/**" % KB, "--filter", "- %s (shadow)/**" % KB,
                                 "--filter", "- **/%s (shadow)/**" % KB, "--filter", "+ **"]).stdout or "[]")
    new, changed = [], []
    for e in listing:
        mt = e["ModTime"][:19] + "Z"
        row = {"file_id": e["ID"], "title": e["Path"].split("/")[-1], "path": e["Path"].split("/")[:-1], "modified_time": mt, "size": e.get("Size")}
        if e["ID"] not in known:
            new.append(row)
        elif known[e["ID"]] and mt > known[e["ID"]][:19] + "Z":
            changed.append(row)
    ls = man.get("last_sweep")
    vals = list(ls.values()) if isinstance(ls, dict) else ([ls] if isinstance(ls, str) else [])
    since = max([v[:19] + ("Z" if len(v) <= 10 or not v.endswith("Z") else "") if len(v) > 10 else v + "T00:00:00Z" for v in vals if v] or [""])
    since = since[:19] + "Z" if since else ""
    # The manifest keeps rows only for sources that contributed a fact, so most older files are simply
    # unrecorded and are not news. What a sweep reads: changed rows, plus files modified since the last sweep.
    recent = [r for r in changed + new if not since or r["modified_time"] > since]
    out({"ok": True, "client": a.client, "last_sweep": since or None, "drive_files": len(listing), "manifest_drive_rows": len(known),
         "to_read": recent, "older_ignored": len(changed) + len(new) - len(recent)})


def cmd_record_swept(a):
    s = state()
    kb = kb_of(a.client)
    ud = os.path.join(kb, "archive", "undo", s["run_id"])
    args = []
    if os.path.isdir(ud):
        for f in sorted(os.listdir(ud)):
            args += ["--file", "%s=%s/%s/archive/undo/%s/%s" % (f, a.client, KB, s["run_id"], f)]
    args += ["--board", a.board]
    shorts = load("shorts.json", {}) or {}
    if a.client not in shorts:
        out({"ok": False, "error": "unknown client folder name %r: use the Drive folder name from discover/plan" % a.client}, 3)
    rc, d = record_client(shorts[a.client], "swept", None, a.sources, args)
    out({"ok": rc == 0, "detail": d if rc else None}, 0 if rc == 0 else 3)


def cmd_wrap(a):
    kb = kb_of(a.client)
    idx = os.path.join(root(), OPS, "corpus-index.json")
    res = {}
    rc, d = jpy("sync.py", ["maintenance", "--kb", kb, "--rotate", "--propose"])
    res["maintenance"] = rc
    ent = {}
    try:
        ent = json.load(open(os.path.join(kb, "brief.json")))["client"]
    except Exception:  # noqa: BLE001
        ent = {"short": short_of(a.client), "aliases": [], "domains": [], "stage": "client", "folder_name": a.client}
    if a.item_id:
        ent["accounts_item_id"] = a.item_id
    ef = os.path.join(work(), "entry.json")
    json.dump(ent, open(ef, "w"))
    titles = json.loads(rclone(["lsjson", "clients:%s/%s" % (a.client, KB), "--no-modtime"]).stdout or "[]")
    tf = os.path.join(work(), "titles-wrap.json")
    json.dump([{"id": e["ID"], "title": e["Name"], "mimeType": e.get("MimeType", "")} for e in titles], open(tf, "w"))
    rc, d = jpy("sync.py", ["brief", "--kb", kb, "--entry", ef, "--titles", tf])
    res["brief"] = d.get("ok")
    rc, d = jpy("sync.py", ["index-update", "--kb", kb, "--index", idx])
    res["index"] = d.get("ok")
    out({"ok": all(v in (0, True) for v in res.values()), **res})


def _status_of(v):
    label = v if isinstance(v, str) else ""
    return {"Client": "client", "Past Client": "past-client", "Buying process": "prospect"}.get(label, label)


def cmd_stages(a):
    """Parse a saved get_board_items_page result of the Accounts board into stages.json, matching on the
    Client Folder G Drive link's folder id. Stage for a client the board lacks falls back to client-list.md."""
    import re
    cands = load("candidates.json", {})
    raw = json.load(open(a.file))
    items = raw.get("items", raw) if isinstance(raw, dict) else raw
    byid, dupes = {}, set()
    for it in items:
        cv = it.get("column_values", {})
        m = re.search(r"/folders/([A-Za-z0-9_-]+)", str(cv.get("link_mm2vffc2") or ""))
        if not m:
            continue
        if m.group(1) in byid:
            dupes.add(m.group(1))
        byid[m.group(1)] = {"short": cv.get("text_mm8pevdf") or it.get("name"), "stage": _status_of(cv.get("status")),
                            "item_id": str(it.get("id")), "client_id": cv.get("text_mm8pgmx8")}
    stages, missing, ambiguous = {}, [], []
    for c, v in cands.items():
        b = byid.get(v["folder_id"])
        if v["folder_id"] in dupes:
            ambiguous.append(c)
        elif b:
            stages[c] = b
        else:
            missing.append(c)
    fallback = {}
    if missing:
        lp = os.path.join(root(), OPS, "client-list.md")
        txt = open(lp, encoding="utf-8").read() if os.path.exists(lp) else ""
        for blk in re.split(r"\n### ", txt):
            did = re.search(r"- drive:.*\(([A-Za-z0-9_-]{20,})\)", blk)
            if did and did.group(1) in {cands[c]["folder_id"] for c in missing}:
                st = re.search(r"- stage:\s*(\S+)", blk)
                nm = blk.split("\n", 1)[0].strip("* ").strip()
                for c in missing:
                    if cands[c]["folder_id"] == did.group(1):
                        fallback[c] = {"short": nm, "stage": (st.group(1) if st else ""), "from_list": True}
    stages.update(fallback)
    missing = [c for c in missing if c not in fallback]
    json.dump(stages, open(os.path.join(work(), "stages.json"), "w"), indent=1)
    out({"ok": not ambiguous and not missing, "matched": len(stages) - len(fallback), "from_client_list": sorted(fallback),
         "not_found": missing, "ambiguous": ambiguous, "stages_file": os.path.join(work(), "stages.json"),
         "next": "look up not_found clients on the board by name; ambiguous ones are skipped and recorded, never guessed" if (missing or ambiguous) else "run plan"},
        0 if not (missing or ambiguous) else 0)


def cmd_maintenance(a):
    s = state()
    if s.get("mode") != "nightly":
        out({"ok": False, "error": "maintenance is for nightly runs"}, 3)
    rc, d = jpy("unattended.py", ["maintenance-candidates", "--runs-dir", runs_dir(), "--cap", "6"])
    if rc != 0:
        out({"ok": False, "error": "maintenance-candidates failed", "detail": d}, 3)
    items = d.get("candidates") or d.get("items") or d.get("clients") or []
    try:  # maintenance-candidates names clients by short name; staging needs the Drive folder name
        rows = json.load(open(os.path.join(root(), OPS, "corpus-index.json"))).get("clients", [])
    except Exception:  # noqa: BLE001
        rows = []
    fold = {r.get("short"): r.get("folder_name") for r in rows}
    names = [i.get("folder_name") or fold.get(i.get("client")) or i.get("client") for i in items if isinstance(i, dict)]
    ids = {e["Name"]: e["ID"] for e in json.loads(rclone(["lsjson", "clients:", "--dirs-only", "--no-modtime", "--no-mimetype"]).stdout)}
    staged, notes = [], {}
    shorts = load("shorts.json", {}) or {}
    for i, n in zip(items, names):  # so `record-swept` and `wrap` accept a maintenance client the plan never listed
        if n and isinstance(i, dict):
            shorts.setdefault(n, i.get("client") or n)
    save("shorts.json", shorts)
    names = [n for n in names if n]
    if names:
        cf = os.path.join(work(), "maint-clients.txt")
        open(cf, "w").write("\n".join(names) + "\n")
        p = stage(["pull", "--add", "--clients-file", cf])
        try:
            rep_ = {r["target"]: r for r in json.loads(p.stdout)["report"]}
        except Exception:  # noqa: BLE001
            out({"ok": False, "error": "client pull failed", "detail": (p.stdout + p.stderr)[-500:]}, 3)
        for n in names:
            r = rep_.get("client:" + n, {})
            if r.get("error"):
                notes[n] = "not staged (%s): work this client through the Drive connector, as nightly-maintenance.md says" % r["error"]
            else:
                staged.append(n)
    out({"ok": True, "raw": d, "staged": staged, "not_staged": notes,
         "folder_ids": {n: ids.get(n) for n in names}})


def cmd_demote(a):
    """Nightly demotion for one client: demote.py plans the moves, dry-runs, then applies. demote.py takes its own
    undo copies and refuses if they exist, so any copy `undo` already made for this run is set aside first and put
    back afterwards when the script did not make its own."""
    s = state()
    if s.get("mode") != "nightly":
        out({"ok": False, "error": "demotion is for nightly runs"}, 3)
    kb = kb_of(a.client)
    plan_f, moves_f = os.path.join(work(), "demote-plan.json"), os.path.join(work(), "demote-moves.json")
    p = py("demote.py", ["plan", "--kb", kb, "--file", a.file, "--emit-moves", moves_f])
    try:
        pl = json.loads(p.stdout)
    except json.JSONDecodeError:
        out({"ok": False, "error": "demote plan failed", "detail": (p.stdout + p.stderr)[-400:]}, 3)
    if not pl.get("fails_budget"):
        out({"ok": True, "demoted": False, "note": "%s is within budget (%s of %s bytes)" % (a.file, pl.get("bytes"), pl.get("budget"))})
    if pl.get("rewrites_needed"):
        out({"ok": False, "demoted": False, "needs_rewrite": pl["rewrites_needed"],
             "note": "narrative sections must shrink as well as move: write the rewrites into the moves file (%s) as nightly-maintenance.md section 1 says, then run demote.py apply yourself" % moves_f}, 3)
    ud = os.path.join(kb, "archive", "undo", s["run_id"])
    held = os.path.join(work(), "held-undo-" + a.client.replace("/", "_"))
    os.makedirs(held, exist_ok=True)
    if os.path.isdir(ud):
        for f in os.listdir(ud):
            shutil.move(os.path.join(ud, f), os.path.join(held, f))
    base = ["apply", "--kb", kb, "--file", a.file, "--moves", moves_f, "--run-id", s["run_id"]]
    rc, dry = jpy("demote.py", base + ["--dry-run"])
    if not dry.get("ok"):
        restore = True
    else:
        rc, res = jpy("demote.py", base)
        restore = True
    if restore and os.path.isdir(held):
        os.makedirs(ud, exist_ok=True)
        for f in os.listdir(held):
            if not os.path.exists(os.path.join(ud, f)):
                shutil.move(os.path.join(held, f), os.path.join(ud, f))
    if not dry.get("ok"):
        out({"ok": False, "error": "demote dry run refused", "detail": dry}, 3)
    if not res.get("ok"):
        out({"ok": False, "error": "demote apply failed", "detail": res}, 3)
    out({"ok": True, "demoted": True, "file": a.file, "before": res.get("before"), "after": res.get("after"), "moved": res.get("moved"),
         "changelog_line": res.get("changelog_line"), "undo_folder": res.get("undo_folder"),
         "next": "put changelog_line in the client's changelog entry (sweep delta summary), then record and wrap"})


def cmd_requests(a):
    s = state()
    if s.get("mode") != "nightly":
        out({"ok": False, "error": "build requests are for nightly runs"}, 3)
    d = os.path.join(root(), OPS, "corpus-requests")
    reqs = []
    for f in sorted(glob.glob(os.path.join(d, "build-request-*.json")))[:20]:
        try:
            j = json.load(open(f))
        except Exception:  # noqa: BLE001
            continue
        if j.get("status") == "requested":
            reqs.append({"file": os.path.basename(f), "client": j.get("client"), "by": j.get("by")})
    out({"ok": True, "requested": reqs[:2], "waiting": max(0, len(reqs) - 2)})


def cmd_shadow(a):
    """Stage a client's shadow corpus folder (nightly build requests write there, never to the live corpus)."""
    s = state()
    if s.get("mode") != "nightly":
        out({"ok": False, "error": "shadow builds are for nightly runs"}, 3)
    cf = os.path.join(work(), "shadow-client.txt")
    open(cf, "w").write(a.client + "\n")
    p = stage(["pull", "--add", "--shadow", "--clients-file", cf])
    try:
        rep_ = {r["target"]: r for r in json.loads(p.stdout)["report"]}
    except Exception:  # noqa: BLE001
        out({"ok": False, "error": "shadow pull failed", "detail": (p.stdout + p.stderr)[-500:]}, 3)
    bad = {k: v for k, v in rep_.items() if v.get("error")}
    out({"ok": not bad, "shadow_dir": os.path.join(root(), CLIENTS, a.client, KB + " (shadow)"), "problems": bad or None}, 0 if not bad else 3)



# ---------------------------------------------------------------- advise
FIX_VIA = ("Run these in a Claude session that has the client-corpus skill and the Drive connector "
           "(Google Drive for desktop mounted is best). Nothing is deleted: the loser of a duplicate goes to the Drive trash.")
ADVICE = {
    "duplicate-titles": ("Two Drive files share a corpus file name, so the client cannot be staged or swept.",
                         ['/tidy "{c}"   (shows the duplicates and the plan; approve once)', '/sweep "{c}"   (confirms the client sweeps clean)']),
    "mount-name-mismatch": ("A corpus file shows under a different name on the mount than on Drive.",
                            ['/tidy "{c}"', '/sweep "{c}"']),
    "pending-entry": ("An old-style changelog-pending note is not folded into the changelog.",
                      ['/tidy "{c}"   (folds it in, then archives it)', '/sweep "{c}"']),
    "missing-core": ("current-state.md or source-manifest.json is not on Drive.",
                     ['/tidy "{c}"   (restores the newest copy from archive/; if none, it offers to rebuild)', '/sweep "{c}"']),
    "old-shape": ("The corpus is in an older shape and needs migrating.",
                  ['/tidy "{c}"   (runs the migration from references/corpus-migration.md)', '/sweep "{c}"']),
    "over-budget": ("current-state.md or now.md is over its size ceiling, so new material cannot be folded in.",
                    ['/tidy "{c}"   (shows what moves where; the nightly run also demotes automatically)', '/sweep "{c}"']),
    "stage-unknown": ("The run could not determine this client's stage, so it is not swept. It looks the client up on the Accounts board "
                      "by the folder id in the item's Client Folder G Drive link; no item with that folder id, or no Account Status, means no stage. "
                      "The board is the authority for stage, and a person must supply what it lacks.",
                      ["1. In monday (wiselyglobal, Accounts board) find the client's item. If there is none, create one.",
                       "2. Set Client Folder G Drive to the client's own folder in Consulting | Clients (the one that contains Claude Project Knowledge Base). A link to any other folder leaves the client unmatched.",
                       "3. Set Account Status to Client, Past Client or Buying process (empty or Prospect is not swept).",
                       '4. Run /sweep "{c}" to confirm it now sweeps']),
    "needs-question": ("The run reached a point that needed a person's judgement.", ['/tidy "{c}"', '/sweep "{c}"']),
    "no-corpus": ("The client has a Drive folder but no corpus.", ['Ask for a build:  /sweep "{c}"  (offers to create the corpus; never started unattended)']),
    "write-failed": ("A write did not complete; the run restored what it had changed.", ['/tidy "{c}"', '/sweep "{c}"']),
    "undo-copy-failed": ("The undo copy could not be made, so nothing was written for this client.", ['Check Drive space and permissions on the client folder', '/sweep "{c}"']),
    "readback": ("A written file did not read back at the size written; the run put the old file back.", ['/tidy "{c}"', '/sweep "{c}"']),
    "manifest-shrank": ("source-manifest.json lost entries, so nothing was written.", ['/tidy "{c}"   (compare against archive/undo/ and restore)']),
    "maintenance-retry": ("Tonight's maintenance fix did not complete; it is tried again tomorrow.", ['/tidy "{c}"   (to do it now with a person present)']),
    "maintenance-failed": ("The nightly maintenance fix failed twice.", ['/tidy "{c}"', '/sweep "{c}"']),
    "dropped-after-retries": ("The client failed three runs in a row and was dropped from the carry-over.", ['/tidy "{c}"', '/sweep "{c}"']),
    "source-unreachable": ("A source for this client could not be read.", ['Check the connector named in the run record (Settings, Connectors), then /sweep "{c}"']),
}
BENIGN = {"prospect", "session-open", "not-recorded"}


def _owner_for(client):
    try:
        o = json.load(open(os.path.join(runs_dir(), "tidy-owners.json")))
    except Exception:  # noqa: BLE001
        o = {}
    return o.get(client) or o.get("default") or "Chris Upton"


def cmd_advise(a):
    """Plain-text 'how to resolve' block for the error and summary emails, built from the run records
    (reason codes only: no client facts beyond names and file names)."""
    import re
    rd = runs_dir()
    recs = []
    for f in sorted(glob.glob(os.path.join(rd, "run-*.md"))):
        try:
            m = re.search(r"<!-- run-data: (.*) -->", open(f, encoding="utf-8").read(), re.S)
            recs.append(json.loads(m.group(1)))
        except Exception:  # noqa: BLE001
            continue
    if a.run:
        recs = [r for r in recs if r.get("run_id") == a.run]
    else:
        cut = datetime.now(timezone.utc).timestamp() - a.hours * 3600
        recs = [r for r in recs if r.get("started") and parse_iso(r["started"]).timestamp() >= cut]
    recs.sort(key=lambda r: r.get("started") or "")
    notes, infra, latest = {}, [], {}
    for r in recs:
        for e in r.get("events", []):
            if e.get("kind") == "maintainer-note":
                notes.setdefault(e["detail"].split(":")[0].strip(), []).append(e["detail"])
            elif e.get("kind") in ("unreachable", "interrupted-run"):
                infra.append("%s: %s" % (e["kind"], e["detail"]))
        for c in r.get("clients", []):
            latest[c["client"]] = (r["run_id"], c)      # only a client's latest record counts: a later clean sweep resolves it
    items = {}
    for client, (run, c) in latest.items():
        if c.get("outcome") == "swept" and not [f for f in c.get("flags", []) if f in ADVICE]:
            continue
        reasons = [c.get("reason")] if c.get("reason") else []
        reasons += [f for f in c.get("flags", []) if f in ADVICE]
        for why in reasons:
            if why and why not in BENIGN:
                items[(client, why)] = run
    lines = []
    if items:
        lines += ["How to resolve", FIX_VIA, ""]
        for (client, why), run in sorted(items.items()):
            meaning, cmds = ADVICE.get(why, ("See the run record for the reason.", ['/tidy "{c}"']))
            lines.append("%s  [%s]  owner: %s" % (client, why, _owner_for(client)))
            lines.append("  What it means: " + meaning)
            for n in notes.get(client, [])[:2]:
                lines.append("  Detail: " + n[:180])
            lines.append("  Do this:")
            lines += ["    " + c.format(c=client) for c in cmds]
            lines.append("")
    for line in sorted(set(infra)):
        lines.append("Run-level: " + line)
    if infra:
        lines += ["  Do this: an unreachable source means a connector is signed out or failing: open Settings, Connectors and reconnect it, then the next run recovers by itself.",
                  "  An interrupted run left an in-progress record: nothing is lost (the next run asks Drive from the older time); runs stay blocked for up to three hours.", ""]
    try:
        w = json.load(open(watch_path())).get("clients", {})
    except Exception:  # noqa: BLE001
        w = {}
    cut = datetime.now(timezone.utc).timestamp() - 24 * 3600
    stuck = {c: v for c, v in w.items() if any(parse_iso(x["since"]).timestamp() <= cut for x in v.get("waiting", []))}
    failed = {c: v["failed"] for c, v in w.items() if v.get("failed")}
    if stuck or failed:
        lines.append("Syncs not applied")
        lines.append("  People's saved chats and sweeps wait as pending syncs until a run applies them. These have not been applied:")
        for c, v in sorted(stuck.items()):
            oldest = min(x["since"] for x in v["waiting"])
            lines.append("  %s: %d waiting, oldest filed %s UTC, owner: %s" % (c, len(v["waiting"]), oldest[:16].replace("T", " "), _owner_for(c)))
        for c, ids_ in sorted(failed.items()):
            lines.append("  %s: %d failed to apply (%s), owner: %s" % (c, len(ids_), ", ".join(str(i) for i in ids_[:3]), _owner_for(c)))
        lines.append("  Do this: run /tidy \"CLIENT\" for each, which clears what blocks it (a duplicate file, an old-style note, an open session), then /sweep \"CLIENT\".")
        lines.append("")
    try:
        q = json.load(open(os.path.join(rd, "tidy-queue.json")))
        openq = [v for v in q.get("items", {}).values() if v.get("status") == "open"] if isinstance(q.get("items"), dict) else \
                [v for k, v in q.get("items", []) if v.get("status") == "open"]
    except Exception:  # noqa: BLE001
        openq = []
    if openq:
        old = [v for v in openq if (v.get("days") or 0) > 7]
        lines.append("Tidy queue: %d open, %d older than 7 days (full list: corpus-runs/tidy-queue.md in Wisely Shared Config)." % (len(openq), len(old)))
    out_text = "\n".join(lines).rstrip() or "Nothing to resolve in this window."
    out({"ok": True, "records": len(recs), "text": out_text})


def cmd_finish(a):
    s = state()
    rc, d = jpy("unattended.py", ["record", "finish", "--runs-dir", runs_dir(), "--run-id", s["run_id"], "--plan", os.path.join(work(), "plan.json")])
    if rc != 0:
        # A second finish after a failed push must still be able to push: the record is already closed locally and
        # the push is idempotent (files already on Drive read as already-current). Any other failure stays an error.
        if "already" not in json.dumps(d).lower() or a.no_push:
            out({"ok": False, "error": "record finish failed", "detail": d}, 3)
        d = {}
    jpy("unattended.py", ["tidy-queue", "--runs-dir", runs_dir()])
    update_pending_watch()
    res = {"ok": True, "reply": d.get("reply")}
    if not a.no_push:
        p = stage(["push"])
        try:
            pj = json.loads(p.stdout)
        except Exception:  # noqa: BLE001
            pj = {"ok": False, "error": (p.stdout + p.stderr)[-500:]}
        bad = [r for r in pj.get("results", []) if r["status"] not in ("pushed", "already-current", "moved-to-archive", "move-already-done")]
        res["push"] = {"ok": pj.get("ok"), "pushed": sum(r["status"] == "pushed" for r in pj.get("results", [])), "problems": bad or pj.get("error")}
        if not pj.get("ok"):
            res["ok"] = False
    out(res, 0 if res["ok"] else 3)


def _nz_today():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Pacific/Auckland")).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def cmd_tidy(a):
    """Put one client's KB folder right wherever the evidence gives one answer (policy: POLICY.md). Dry run unless --apply.
    Moves only (native Google files out of the KB tree, duplicates, dated history); a real disagreement between two copies
    is not guessed: it is recorded as a question that names the exact conflict."""
    import corpus_stage as cs
    import corpus_tidy as ct
    drive = ct.RcloneDrive(cs.rc)
    rep = ct.run(a.client, drive, a.today or _nz_today(), do_apply=a.apply, cap=a.cap)
    short = short_of(a.client)
    # Logging needs a run record. Outside a run (a person supervising) the files still move and the report still prints.
    rep["logged_to_run"] = bool(a.apply and os.path.exists(os.path.join(work(), "run.json")))
    if rep["logged_to_run"]:
        if rep["attempted"]:
            event("maintainer-note", "%s: tidy: moved %d, wrote %d, flagged %d, deferred %d, problems %d" % (
                short, rep.get("moved", 0), rep.get("written", 0), len(rep["flags"]), rep.get("deferred", 0), len(rep.get("problems", []))))
        for fl in rep["flags"][:3]:
            event("maintainer-note", "%s: needs-question: %s" % (short, fl["question"]))
        if rep["flags"]:
            record_client(short, "skipped", "needs-question", "drive")
    bad = bool(rep.get("problems"))
    out({"ok": not bad, **rep}, 3 if bad else 0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("begin"); p.add_argument("--mode", choices=["changed", "nightly"], default="changed"); p.set_defaults(f=cmd_begin)
    sp.add_parser("discover").set_defaults(f=cmd_discover)
    p = sp.add_parser("stages-from-board"); p.add_argument("--file", required=True); p.set_defaults(f=cmd_stages)
    sp.add_parser("maintenance").set_defaults(f=cmd_maintenance)
    sp.add_parser("requests").set_defaults(f=cmd_requests)
    p = sp.add_parser("shadow"); p.add_argument("client"); p.set_defaults(f=cmd_shadow)
    p = sp.add_parser("plan"); p.add_argument("--stages", required=True); p.set_defaults(f=cmd_plan)
    p = sp.add_parser("apply-pending"); p.add_argument("client"); p.set_defaults(f=cmd_apply)
    p = sp.add_parser("undo"); p.add_argument("client"); p.set_defaults(f=cmd_undo)
    p = sp.add_parser("drive-diff"); p.add_argument("client"); p.set_defaults(f=cmd_drive_diff)
    p = sp.add_parser("demote"); p.add_argument("client"); p.add_argument("--file", default="current-state.md", choices=["current-state.md", "now.md"]); p.set_defaults(f=cmd_demote)
    p = sp.add_parser("record-swept"); p.add_argument("client"); p.add_argument("--sources", default="drive")
    p.add_argument("--board", default="not-needed", choices=["ok", "failed", "skipped", "not-needed"]); p.set_defaults(f=cmd_record_swept)
    p = sp.add_parser("wrap"); p.add_argument("client"); p.add_argument("--item-id"); p.set_defaults(f=cmd_wrap)
    p = sp.add_parser("advise"); p.add_argument("--run"); p.add_argument("--hours", type=float, default=24); p.set_defaults(f=cmd_advise)
    p = sp.add_parser("tidy"); p.add_argument("client"); p.add_argument("--apply", action="store_true")
    p.add_argument("--cap", type=int, default=40); p.add_argument("--today"); p.set_defaults(f=cmd_tidy)
    p = sp.add_parser("finish"); p.add_argument("--no-push", action="store_true"); p.set_defaults(f=cmd_finish)
    a = ap.parse_args()
    if getattr(a, "client", None):
        a.client = folder_name(a.client)
    try:
        a.f(a)
    except (RuntimeError, OSError, KeyError, json.JSONDecodeError) as e:
        out({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}, 3)


if __name__ == "__main__":
    main()
