#!/usr/bin/env python3
"""Conflict cards: what a person is told when the runs reach a real disagreement (see POLICY.md).

A card is derived fresh from the live Accounts board and Drive on every run, so it disappears the moment the
conflict is fixed (nothing to clear by hand, no stale row). Each card answers the receiver's six questions:
what conflicts and where (links), the facts side by side, what it blocks, the exact question, what to do for each
possible answer, and what happens next. The text carries names, ids, links, counts and dates, never client content.

Kinds:
  duplicate-account    two Accounts board items that look like one client
  duplicate-folders    two top-level Drive folders with one name
  unknown-status       an Account Status label the runs do not know how to read
"""
import re

LEGAL = {"ltd", "limited", "pty", "inc", "incorporated", "llc", "plc", "nz", "au", "co", "company", "the", "group", "holdings"}
# "Customer" is a won deal awaiting onboarding (owner, 2026-11-28): not delivering yet, so it is read as a prospect
# (not swept, not built, no card) until the status becomes Client.
KNOWN_STATUS = {"Client": "client", "Past Client": "past-client", "Buying process": "prospect", "Customer": "prospect"}
STATUS_MEANING = {"Customer": "a won deal awaiting onboarding"}
KB = "Claude Project Knowledge Base"
STAGES = ("client", "past-client", "prospect")
FOLDER = "application/vnd.google-apps.folder"


def name_key(name):
    """'Serko Ltd.' and 'Serko' are one key; 'Tegel Foods Ltd' and 'Tegel' are not (Foods is not a legal suffix)."""
    toks = [t for t in re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).split() if t not in LEGAL]
    return "".join(toks)


def status_text(label):
    if not label:
        return "none"
    return "%s (%s)" % (label, STATUS_MEANING[label]) if label in STATUS_MEANING else label


def folder_url(fid):
    return "https://drive.google.com/drive/folders/%s" % fid


def item_url(item):
    return item.get("url") or ""


def parse_items(raw_items, re_folder=r"/folders/([A-Za-z0-9_-]+)"):
    """Accounts board items (get_board_items_page rows) -> flat dicts."""
    out = []
    for it in raw_items:
        cv = it.get("column_values", {}) or {}
        st = cv.get("status")
        label = st if isinstance(st, str) else None
        m = re.search(re_folder, str(cv.get("link_mm2vffc2") or ""))
        out.append({"id": str(it.get("id")), "name": it.get("name") or "", "status": label,
                    "status_unreadable": (st is not None and not isinstance(st, str)),
                    "short": cv.get("text_mm8pevdf"), "client_id": cv.get("text_mm8pgmx8"),
                    "folder_id": m.group(1) if m else None, "url": it.get("url") or "", "created": (it.get("created_at") or "")[:10]})
    return out


def duplicate_accounts(items):
    groups = {}
    for it in items:
        k = name_key(it["name"])
        if k:
            groups.setdefault(k, []).append(it)
    return [g for g in groups.values() if len(g) > 1]


def unknown_statuses(items, status_map=None):
    """{label: [items]} for items whose status the runs cannot read (and no status-map entry covers)."""
    known = dict(KNOWN_STATUS)
    known.update(status_map or {})
    out = {}
    for it in items:
        if it["status_unreadable"]:
            out.setdefault("(label not readable from the board export)", []).append(it)
        elif it["status"] and it["status"] not in known:
            out.setdefault(it["status"], []).append(it)
    return out


def folder_facts(listing):
    """From `rclone lsjson -R` of a folder's whole tree -> file count, top-level subfolders, whether a corpus is present and the
    newest change. Counting the whole tree matters: real content sits in subfolders, and a top-level count calls a full folder empty."""
    files = [e for e in listing if not e.get("IsDir")]
    subs = [e for e in listing if e.get("IsDir") and "/" not in e.get("Path", "")]
    newest = max([e.get("ModTime", "") for e in files] or [""])[:10]
    return {"files": len(files), "subfolders": len(subs), "has_kb": any(e.get("Path", "").startswith(KB + "/") for e in listing), "newest": newest or None}


def describe_folder(f):
    if f is None:
        return "folder contents could not be read"
    if not f["files"] and not f["has_kb"]:
        return "empty template folder (%d subfolders, no files, no corpus)" % f["subfolders"]
    return "%d file%s in the folder tree, %s%s" % (f["files"], "" if f["files"] == 1 else "s", "corpus present" if f["has_kb"] else "no corpus",
                                                  ", newest change %s" % f["newest"] if f["newest"] else "")


# ---------------------------------------------------------------- cards
def card_duplicate_accounts(group, facts_by_folder, folder_created):
    def line(letter, it):
        reg = "registered (Client ID %s, short name %s)" % (it["client_id"], it["short"]) if it["client_id"] else "not registered (no Client ID, no short name)"
        fid = it["folder_id"]
        return "%s. Accounts item \"%s\" (%s) | status %s | %s | created %s | Drive folder %s | %s" % (
            letter, it["name"], item_url(it), status_text(it["status"]), reg, it["created"] or "unknown",
            folder_url(fid) if fid else "none linked", describe_folder(facts_by_folder.get(fid)) if fid else "no folder")
    canon = [i for i in group if i["client_id"]]
    facts = [line(chr(65 + n), it) for n, it in enumerate(group)]
    real = canon[0] if len(canon) == 1 else None
    names = " and ".join('"%s"' % i["name"] for i in group)
    blocks = ["Nothing is lost and no client is blocked: the runs ignore an account with no Client ID, and an empty folder is never a candidate."
              if real else "The runs cannot tell which account is the client, so neither is swept until this is answered."]
    answers = []
    if real:
        others = [i for i in group if i is not real]
        answers.append({"if": "they are the same organisation",
                        "do": ["In the Accounts board, move anything worth keeping from %s onto \"%s\" (contacts, opportunities), then archive %s." % (
                                   ", ".join('"%s"' % o["name"] for o in others), real["name"], ", ".join('"%s"' % o["name"] for o in others)),
                               "A status of Customer means a won deal awaiting onboarding: if that deal is real work for this client, record it on \"%s\" so onboarding follows from the existing client." % real["name"],
                               "In the Clients drive, remove the unused folder(s): " + ", ".join(folder_url(o["folder_id"]) for o in others if o["folder_id"])
                               + " (the runs never delete; this is a manual move to trash)."]})
        answers.append({"if": "they are different organisations",
                        "do": ["Give \"%s\" a Short name, a Client ID and a status of Client, Past Client or Buying process. The next run picks it up." % others[0]["name"]]})
    else:
        answers.append({"if": "you know which item is the client", "do": ["Give that item a Short name, a Client ID and a status, and its Client Folder G Drive link must point at the client's own folder."]})
    return {"kind": "duplicate-account", "id": "duplicate-account:" + name_key(group[0]["name"]), "client": real["name"] if real else group[0]["name"],
            "title": "Two Accounts board items look like one client: %s." % names, "facts": facts, "blocks": blocks,
            "question": "Are %s the same organisation?" % names, "answers": answers,
            "after": "The next hourly run re-reads the board and this card disappears."}


def card_duplicate_folders(name, folders, board_by_folder):
    """folders: [{id, created, modified, facts}] for two top-level folders sharing a name."""
    facts = []
    for n, f in enumerate(folders):
        b = board_by_folder.get(f["id"])
        facts.append("%s. Drive folder %s | created %s | %s | %s" % (
            chr(65 + n), folder_url(f["id"]), (f.get("created") or "unknown")[:10], describe_folder(f.get("facts")),
            "the Accounts board links this one (item \"%s\", %s)" % (b["name"], item_url(b)) if b else "no Accounts item links it"))
    linked = [f for f in folders if f["id"] in board_by_folder]
    real = linked[0] if len(linked) == 1 else None
    if real and real.get("facts") and real["facts"]["files"] == 0:
        fuller = [(chr(65 + n), f) for n, f in enumerate(folders) if f is not real and f.get("facts") and f["facts"]["files"] > 0]
        if fuller:
            facts.append("Observation: the folder the board links holds no files, while %s holds %s. The board link may point at the wrong folder." % (
                " and ".join(l for l, _ in fuller), " and ".join("%d" % f["facts"]["files"] for _, f in fuller)))
    blocks = ["Neither folder can be swept while two share one name (the run stages a client by folder name). Nothing is lost."]
    keep = next((n for n, f in enumerate(folders) if real and f["id"] == real["id"]), None)
    answers = []
    if real:
        other = [f for f in folders if f is not real]
        answers.append({"if": "the folder the board links (%s) is the client's" % chr(65 + keep),
                        "do": ["Move anything of value from the other folder(s) into it: " + ", ".join(folder_url(f["id"]) for f in other),
                               "Then move the other folder(s) to the trash in the Clients drive (the runs never delete)."]})
        answers.append({"if": "the other folder is the real one",
                        "do": ["Point the Accounts item's Client Folder G Drive link at it, then follow the steps above in the other direction."]})
    else:
        answers.append({"if": "you know which folder is the client's", "do": ["Link it from the client's Accounts item (Client Folder G Drive), merge the other into it, trash the other."]})
    return {"kind": "duplicate-folders", "id": "duplicate-folders:" + name_key(name), "client": name,
            "title": "Two top-level Drive folders are both named \"%s\"." % name, "facts": facts, "blocks": blocks,
            "question": "Which folder is the client's, and does the other hold anything worth keeping?", "answers": answers,
            "after": "The next hourly run lists the Clients drive again and this card disappears once one folder is left."}


def card_unknown_status(label, items):
    shown = items[:8]
    facts = ["Account Status \"%s\" is on %d Accounts item%s: %s%s" % (
        label, len(items), "" if len(items) == 1 else "s",
        "; ".join('"%s" (%s)' % (i["name"], item_url(i)) for i in shown), " and %d more" % (len(items) - len(shown)) if len(items) > len(shown) else "")]
    blocks = ["Items with this status are read as 'stage unknown' and are not swept. The runs know Client, Past Client and Buying process."]
    answers = [{"if": "\"%s\" means a client" % label, "do": ["Add it to status-map.json in Wisely Shared Config: {\"%s\": \"client\"}" % label]},
               {"if": "it means a prospect or a past client", "do": ["Add {\"%s\": \"prospect\"} or {\"%s\": \"past-client\"} the same way."  % (label, label)]},
               {"if": "it should be ignored", "do": ["Nothing to do: the items stay unswept. Say so and the card can be silenced."]}]
    return {"kind": "unknown-status", "id": "unknown-status:" + re.sub(r"[^a-z0-9]", "", label.lower()), "client": "Accounts board status \"%s\"" % label,
            "title": "The Accounts board uses a status the runs do not know: \"%s\"." % label, "facts": facts, "blocks": blocks,
            "question": "What does the status \"%s\" mean, compared with Client?" % label, "answers": answers,
            "after": "The next run reads status-map.json and these items are swept (or not) accordingly."}


# ---------------------------------------------------------------- state and rendering
def merge_first_seen(old, new, today):
    """Keep the date a conflict was first seen; a card no longer detected is gone."""
    seen = {c["id"]: c.get("first_seen") for c in (old or [])}
    for c in new:
        c["first_seen"] = seen.get(c["id"]) or today
    return new


def render(cards, owner="Chris Upton"):
    lines = []
    for c in cards:
        lines.append("CONFLICT: %s  [%s]  owner: %s  (open since %s)" % (c["client"], c["kind"], owner, c.get("first_seen", "?")))
        lines.append("  What conflicts: " + c["title"])
        lines.append("  The facts:")
        lines += ["    " + f for f in c["facts"]]
        lines.append("  What it blocks: " + " ".join(c["blocks"]))
        lines.append("  What we need from you: " + c["question"])
        for a in c["answers"]:
            lines.append("  If %s:" % a["if"])
            lines += ["    - " + d for d in a["do"]]
        lines.append("  After you act: " + c["after"])
        lines.append("")
    return lines
