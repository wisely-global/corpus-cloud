#!/usr/bin/env python3
"""Put one client's KB folder right without a person, wherever the evidence gives a single answer (see POLICY.md).

Everything here MOVES files; nothing is trashed or deleted (rclone has no trash-by-id, and a move is reversible).
Files are addressed by Drive file id (`rclone backend moveid`), so two files with one name are told apart.

What it does, per client, in this order:
  1. Native Google Docs/Sheets/Slides inside the KB tree move to a sibling folder `Archived from KB <date>/`
     (they list at size -1, so they make a pull fail with size-mismatch).
  2. Files that share a path:
       byte-identical             -> keep the oldest, move the rest to `Archived from KB <date>/duplicates/`
       one is a superset          -> keep the superset (text lines or JSON), move the other
       JSON that merges cleanly   -> both originals move out, the merged file is written at the path
       anything else              -> flagged with the exact disagreement; nothing moves
  3. Dated history at the KB root (`current-state <date> vN.md`, `now <date>.md`, `source-manifest-vN.json`, ...)
     moves into `archive/` (or `session-logs/`), but only once the canonical file exists, and for current-state
     only if the canonical Version is at least the dated one.

Dry run by default. `apply` re-checks each file against the plan first and skips one that changed since.
The core corpus files are never touched.
"""
import hashlib, json, os, re, shutil, sys, tempfile

KB = "Claude Project Knowledge Base"
FOLDER = "application/vnd.google-apps.folder"
CORE = ("current-state.md", "now.md", "changelog.md", "source-manifest.json", "corrections-log.md",
        "deliverables-register.md", "open-items-register.md", "board-register.md", "brief.json", "environment.json")
HISTORY = (  # (pattern, canonical counterpart, destination folder inside the KB)
    (re.compile(r"^current-state \d{4}-\d{2}-\d{2} v\d+\.md$"), "current-state.md", "archive"),
    (re.compile(r"^now \d{4}-\d{2}-\d{2}\.md$"), "now.md", "archive"),
    (re.compile(r"^source-manifest-v\d+\.json$"), "source-manifest.json", "archive"),
    (re.compile(r"^deliverables-register \d{4}-\d{2}-\d{2}\.md$"), "deliverables-register.md", "archive"),
    (re.compile(r"^corrections-log \d{4}-\d{2}-\d{2}\.md$"), "corrections-log.md", "archive"),
    (re.compile(r"^session-log-\d{4}-\d{2}-\d{2}.*\.md$"), None, "session-logs"),
)
NEWER_WINS = ("last_updated", "last_activity", "updated_by", "generated", "as_at")
ROW_KEYS = ("id", "source_id", "fingerprint")
DEFAULT_CAP = 40


# ---------------------------------------------------------------- merging (pure)
def _short(v, n=60):
    s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    return s if len(s) <= n else s[:n - 1] + "…"


def merge_json(a, b, path="$"):
    """(merged, conflicts). Union wherever the two copies do not collide; a collision is two different values
    for the same key, and is reported with its path instead of being guessed."""
    if a == b:
        return a, []
    if isinstance(a, dict) and isinstance(b, dict):
        out, conf = {}, []
        for k in list(a) + [k for k in b if k not in a]:
            if k in a and k in b:
                if k in NEWER_WINS and isinstance(a[k], str) and isinstance(b[k], str):
                    out[k] = max(a[k], b[k])
                    continue
                out[k], c = merge_json(a[k], b[k], "%s.%s" % (path, k))
                conf += c
            else:
                out[k] = a[k] if k in a else b[k]
        return out, conf
    if isinstance(a, list) and isinstance(b, list):
        key = next((k for k in ROW_KEYS if all(isinstance(x, dict) and k in x for x in a + b)), None)
        if key:
            out, conf, idx = [dict(x) for x in a], [], {x[key]: i for i, x in enumerate(a)}
            for x in b:
                if x[key] in idx:
                    out[idx[x[key]]], c = merge_json(out[idx[x[key]]], x, "%s[%s=%s]" % (path, key, x[key]))
                    conf += c
                else:
                    out.append(x)
            return out, conf
        seen, out = set(), []
        for x in a + b:
            d = json.dumps(x, sort_keys=True, ensure_ascii=False)
            if d not in seen:
                seen.add(d)
                out.append(x)
        return out, []
    return a, [{"path": path, "a": _short(a), "b": _short(b)}]


def _lines(text):
    return [ln.rstrip() for ln in text.splitlines() if ln.strip()]


def resolve_pair(path, ta, tb):
    """Two different files at one path. -> ("a"|"b", note) when one contains the other, ("merge", bytes, note) when
    JSON merges without a collision, ("conflict", question) otherwise."""
    if path.endswith(".json"):
        try:
            ja, jb = json.loads(ta), json.loads(tb)
        except ValueError:
            ja = jb = None
        if ja is not None:
            merged, conf = merge_json(ja, jb)
            if conf:
                shown = "; ".join("%s is %s in one copy and %s in the other" % (c["path"], c["a"], c["b"]) for c in conf[:3])
                return ("conflict", "%s exists twice and the copies disagree: %s%s. Which is right?" % (path, shown, " (+%d more)" % (len(conf) - 3) if len(conf) > 3 else ""))
            if merged == ja:
                return ("a", "a contains everything in b")
            if merged == jb:
                return ("b", "b contains everything in a")
            return ("merge", json.dumps(merged, indent=1, ensure_ascii=False).encode("utf-8"), "merged without a collision")
    la, lb = set(_lines(ta)), set(_lines(tb))
    if lb <= la:
        return ("a", "a contains every line of b")
    if la <= lb:
        return ("b", "b contains every line of a")
    return ("conflict", "%s exists twice with different text (%d lines only in one, %d only in the other). Which is right?" % (path, len(la - lb), len(lb - la)))


def version_of(text):
    m = re.search(r"^\s*Version:\s*(\d+)", text or "", re.M)
    return int(m.group(1)) if m else None


def version_from_name(name):
    """A dated current-state file carries its version in its name (`current-state 2026-09-17 v5.md`)."""
    m = re.search(r" v(\d+)\.md$", name or "")
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------- planning (pure given the listing and a reader)
def _id8(f):
    return f["id"][:8]


def _history_rule(name):
    for pat, canon, folder in HISTORY:
        if pat.match(name):
            return pat, canon, folder
    return None


def plan(client, files, read, today):
    """files: [{id, name, path, dir, parent, mime, size, md5, created, modified}] for the whole KB tree.
    read(file) -> text. Returns {"ops": [...], "flags": [...]}."""
    side = "%s/Archived from KB %s" % (client, today)
    kb = "%s/%s" % (client, KB)
    ops, flags, handled = [], [], set()

    def move(f, dest, why):
        handled.add(f["id"])
        ops.append({"op": "move", "file": f, "dest": dest, "why": why})

    # 1. native Google files
    for f in files:
        if f["mime"].startswith("application/vnd.google-apps.") and f["mime"] != FOLDER:
            move(f, "%s/%s/%s" % (side, f["dir"], _id8(f)) if f["dir"] else "%s/%s" % (side, _id8(f)), "native Google file inside the KB tree")
    rest = [f for f in files if f["id"] not in handled]
    # 2. same path
    by_path = {}
    for f in rest:
        by_path.setdefault(f["path"], []).append(f)
    for path, grp in sorted(by_path.items()):
        if len(grp) < 2:
            continue
        if path in CORE:
            flags.append({"path": path, "kind": "core-duplicate",
                          "question": "%s (a core corpus file) exists %d times. The system does not collapse core files; which copy is the live one?" % (path, len(grp))})
            handled.update(f["id"] for f in grp)
            continue
        grp.sort(key=lambda f: (f.get("created") or "", f["id"]))
        hist = _history_rule(grp[0]["name"]) if not grp[0]["dir"] else None
        if hist and (hist[1] is None or any(g["path"] == hist[1] and g["id"] not in handled for g in files)) \
                and len({g.get("md5") for g in grp}) > 1:
            # Superseded history with two different copies: neither is the live file, so there is nothing to choose.
            # Both are kept side by side in the archive, each in a folder named for its id.
            for g in grp:
                move(g, "%s/%s/%s" % (kb, hist[2], _id8(g)), "two different copies of dated history; both kept, neither is live")
            continue
        keep = grp[0]
        for other in grp[1:]:
            if keep.get("md5") and keep.get("md5") == other.get("md5"):
                move(other, "%s/duplicates/%s/%s" % (side, keep["dir"], _id8(other)), "byte-identical to %s" % _id8(keep))
                continue
            try:
                ta, tb = read(keep), read(other)
            except Exception as e:  # noqa: BLE001
                flags.append({"path": path, "kind": "unreadable", "question": "%s exists twice and one copy could not be read (%s). Which is right?" % (path, type(e).__name__)})
                handled.update([keep["id"], other["id"]])
                continue
            res = resolve_pair(path, ta, tb)
            if res[0] == "conflict":
                flags.append({"path": path, "kind": "conflict", "question": res[1]})
                handled.update([keep["id"], other["id"]])
            elif res[0] == "a":
                move(other, "%s/duplicates/%s/%s" % (side, keep["dir"], _id8(other)), res[1])
            elif res[0] == "b":
                move(keep, "%s/duplicates/%s/%s" % (side, keep["dir"], _id8(keep)), res[1])
                keep = other
            else:
                move(keep, "%s/duplicates/%s/%s" % (side, keep["dir"], _id8(keep)), "originals kept; merged file written")
                move(other, "%s/duplicates/%s/%s" % (side, other["dir"], _id8(other)), "originals kept; merged file written")
                ops.append({"op": "put", "rel": path, "dest_path": "%s/%s" % (kb, path), "content": res[1], "why": res[2]})
    # 3. dated history at the root
    live = {f["path"]: f for f in files if f["id"] not in handled}
    for f in files:
        if f["id"] in handled or f["dir"]:
            continue
        for pat, canon, folder in HISTORY:
            if not pat.match(f["name"]):
                continue
            if canon and canon not in live:
                flags.append({"path": f["path"], "kind": "history-without-canonical",
                              "question": "%s is dated history but %s is not in the KB root, so it was left in place. Where is the live file?" % (f["name"], canon)})
            elif canon == "current-state.md":
                try:
                    vc = version_of(read(live[canon]))
                    vh = version_of(read(f))
                    if vh is None:
                        vh = version_from_name(f["name"])
                except Exception:  # noqa: BLE001
                    vc = vh = None
                if vc is None or vh is None or vc < vh:
                    flags.append({"path": f["path"], "kind": "history-newer",
                                  "question": "%s (Version %s) is not older than current-state.md (Version %s). Which is the live state?" % (f["name"], vh, vc)})
                else:
                    move(f, "%s/%s" % (kb, folder), "superseded current-state (Version %d, live is %d)" % (vh, vc))
            else:
                move(f, "%s/%s" % (kb, folder), "dated history; canonical %s is live" % (canon or "layout"))
            break
    return {"ops": ops, "flags": flags}


# ---------------------------------------------------------------- Drive through rclone
class RcloneDrive:
    def __init__(self, rc, remote="clients:"):
        self.rc, self.remote = rc, remote

    def _json(self, args):
        p = self.rc(args, check=False)
        if p.returncode != 0:
            raise RuntimeError("rclone %s failed (%d): %s" % (args[0], p.returncode, (p.stderr or "").strip()[-300:]))
        return json.loads(p.stdout or "[]") or []     # rclone 1.76 prints `null` for a folder with no children

    def check(self):
        """Fail before planning, not at the first move, if this rclone cannot move by id."""
        p = self.rc(["backend", "help", "drive"], check=False)
        if "moveid" not in (p.stdout or "") + (p.stderr or ""):
            v = self.rc(["version"], check=False).stdout.splitlines()[:1]
            raise RuntimeError("this rclone (%s) has no drive `backend moveid` command; R tidy needs v1.70.0 or newer" % (v[0] if v else "unknown version"))

    def children(self, folder_id):
        out = []
        for it in self._json(["backend", "query", self.remote, "'%s' in parents and trashed = false" % folder_id]):
            out.append(it)
        return out

    def kb_tree(self, client):
        dirs = self._json(["lsjson", self.remote + client, "--dirs-only", "--no-modtime", "--no-mimetype"])
        kbs = [d for d in dirs if d["Name"] == KB]
        if len(kbs) != 1:
            raise RuntimeError("%d '%s' folders under %s" % (len(kbs), KB, client))
        files, queue = [], [(kbs[0]["ID"], "")]
        while queue:
            fid, rel = queue.pop(0)
            for it in self.children(fid):
                if it["mimeType"] == FOLDER:
                    queue.append((it["id"], rel + it["name"] + "/"))
                    continue
                files.append({"id": it["id"], "name": it["name"], "path": rel + it["name"], "dir": rel.rstrip("/"), "parent": fid,
                              "mime": it["mimeType"], "size": int(it["size"]) if it.get("size") not in (None, "") else -1,
                              "md5": it.get("md5Checksum"), "created": it.get("createdTime"), "modified": it.get("modifiedTime")})
        return files

    def read(self, f):
        d = tempfile.mkdtemp(prefix="tidy-")
        try:
            p = self.rc(["backend", "copyid", self.remote, f["id"], d + "/"], check=False)
            names = os.listdir(d)
            if p.returncode != 0 or len(names) != 1:
                raise RuntimeError("copyid failed")
            return open(os.path.join(d, names[0]), encoding="utf-8").read()
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def still_there(self, f):
        for it in self.children(f["parent"]):
            if it["id"] == f["id"]:
                return it
        return None

    def mkdir(self, path):
        p = self.rc(["mkdir", self.remote + path], check=False)
        if p.returncode != 0:
            raise RuntimeError("mkdir failed: " + (p.stderr or "").strip()[-200:])

    def move(self, f, dest):
        self.mkdir(dest)
        p = self.rc(["backend", "moveid", self.remote, f["id"], dest + "/"], check=False)
        if p.returncode != 0:
            raise RuntimeError("moveid failed: " + (p.stderr or "").strip()[-200:])

    def lands(self, f, dest):
        p = self.rc(["lsjson", self.remote + dest, "--files-only", "--no-modtime"], check=False)
        if p.returncode != 0:
            return False
        return any(e.get("ID") == f["id"] for e in json.loads(p.stdout or "[]"))

    def put(self, dest_path, content):
        d = tempfile.mkdtemp(prefix="tidy-")
        try:
            src = os.path.join(d, os.path.basename(dest_path))
            open(src, "wb").write(content)
            p = self.rc(["copyto", src, self.remote + dest_path], check=False)
            if p.returncode != 0:
                raise RuntimeError("copyto failed: " + (p.stderr or "").strip()[-200:])
            q = self.rc(["lsjson", self.remote + dest_path, "--files-only", "--no-mimetype", "--hash", "--hash-type", "md5"], check=False)
            e = json.loads(q.stdout or "[]") if q.returncode == 0 else []
            return len(e) == 1 and e[0]["Size"] == len(content) and (e[0].get("Hashes") or {}).get("md5", hashlib.md5(content).hexdigest()) == hashlib.md5(content).hexdigest()
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- applying
STOP_AFTER = 3     # consecutive failed operations before the rest are not attempted (a systemic fault, not a bad file)


def apply(drive, ops, cap=DEFAULT_CAP):
    """Run the plan. Each move is re-checked against the plan first (a file that changed or went is skipped) and
    read back after (the id must be in the destination and gone from its old folder)."""
    results, streak = [], 0
    for i, op in enumerate(ops):
        res = {"op": op["op"], "why": op.get("why")}
        if streak >= STOP_AFTER:
            res["status"] = "not-attempted"
            res["path"] = op["file"]["path"] if op["op"] == "move" else op["dest_path"]
            results.append(res)
            continue
        if i >= cap:
            res["status"] = "deferred"
        elif op["op"] == "move":
            f = op["file"]
            res.update({"path": f["path"], "dest": op["dest"]})
            try:
                cur = drive.still_there(f)
                if cur is None:
                    res["status"] = "gone"
                elif f.get("md5") and cur.get("md5Checksum") != f["md5"]:
                    res["status"] = "stale"
                else:
                    drive.move(f, op["dest"])
                    res["status"] = "moved" if drive.lands(f, op["dest"]) and drive.still_there(f) is None else "readback-failed"
            except Exception as e:  # noqa: BLE001
                res["status"], res["detail"] = "failed", str(e)[:200]
        else:
            res.update({"path": op["dest_path"]})
            # the merged file replaces two originals: write it only once both have actually moved out
            if any(r["status"] != "moved" for r in results if r["op"] == "move" and r.get("path") == op["rel"]):
                res["status"] = "skipped"
            else:
                try:
                    res["status"] = "written" if drive.put(op["dest_path"], op["content"]) else "readback-failed"
                except Exception as e:  # noqa: BLE001
                    res["status"], res["detail"] = "failed", str(e)[:200]
        results.append(res)
        streak = streak + 1 if res["status"] in ("failed", "readback-failed") else 0
    return results


def run(client, drive, today, do_apply=False, cap=DEFAULT_CAP):
    if hasattr(drive, "check"):
        drive.check()
    files = drive.kb_tree(client)
    p = plan(client, files, drive.read, today)
    report = {"client": client, "mode": "dry-run", "files_seen": len(files), "planned": len(p["ops"]), "flags": p["flags"],
              "attempted": False, "applied": False,
              "ops": [{"op": o["op"], "path": (o["file"]["path"] if o["op"] == "move" else o["dest_path"]),
                       "dest": o.get("dest"), "why": o["why"]} for o in p["ops"]]}
    if do_apply and p["ops"]:
        report["results"] = apply(drive, p["ops"], cap)
        report["attempted"] = True
        report["mode"] = "apply"
        report["moved"] = sum(r["status"] == "moved" for r in report["results"])
        report["written"] = sum(r["status"] == "written" for r in report["results"])
        report["deferred"] = sum(r["status"] == "deferred" for r in report["results"])
        report["not_attempted"] = sum(r["status"] == "not-attempted" for r in report["results"])
        report["problems"] = [r for r in report["results"] if r["status"] not in ("moved", "written", "deferred")]
        report["applied"] = not report["problems"]            # true only when nothing went wrong
        report["summary"] = ("%d of %d operations done" % (report["moved"] + report["written"], len(p["ops"]))
                             + ("" if report["applied"] else "; %d problem(s): see problems" % len(report["problems"])))
    return report


if __name__ == "__main__":
    sys.exit("run it through corpus_run.py tidy CLIENT [--apply]")
