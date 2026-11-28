#!/usr/bin/env python3
"""Unit tests for the pure helpers in corpus_run.py. Run: python3 test_corpus_run.py"""
import json, os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import corpus_run as cr


class Helpers(unittest.TestCase):
    def test_pending_id_joins_parts(self):
        self.assertEqual(cr._pend_id("pending-2026-11-17-aayushb-1630-p1.json"), "2026-11-17-aayushb-1630")
        self.assertEqual(cr._pend_id("pending-2026-11-24-chrisu-1410.json"), "2026-11-24-chrisu-1410")

    def test_gmail_stripped_from_plan_and_carry_over(self):
        plan = {"sweep_now": [{"client": "A", "sources": ["drive", "gmail", "monday"]}, {"client": "B", "sources": ["drive"]}],
                "carry_over": [{"client": "C", "sources": ["drive", "gmail", "monday"], "attempts": 0}]}
        cr.strip_gmail(plan)
        self.assertEqual(plan["sweep_now"][0]["sources"], ["drive", "monday"])
        self.assertEqual(plan["sweep_now"][1]["sources"], ["drive"])
        self.assertEqual(plan["carry_over"][0]["sources"], ["drive", "monday"])

    def test_gmail_switch(self):
        for v, want in (("on", True), ("ON", True), ("1", True), ("off", False), ("", False)):
            os.environ["GMAIL_SWEEP"] = v
            self.assertEqual(cr.gmail_on(), want, v)
        del os.environ["GMAIL_SWEEP"]
        self.assertFalse(cr.gmail_on())  # off by default

class Advise(unittest.TestCase):
    def test_every_actionable_reason_has_commands(self):
        for why in ("duplicate-titles", "mount-name-mismatch", "pending-entry", "missing-core", "old-shape", "over-budget",
                    "stage-unknown", "write-failed", "maintenance-retry", "dropped-after-retries"):
            meaning, cmds = cr.ADVICE[why]
            self.assertTrue(meaning and cmds, why)
            self.assertTrue(any("{c}" in c for c in cmds), why)

    def test_benign_reasons_never_advised(self):
        for why in cr.BENIGN:
            self.assertNotIn(why, cr.ADVICE)

class PendingWatch(unittest.TestCase):
    def setUp(self):
        import tempfile, json
        self.tmp = tempfile.mkdtemp()
        os.environ["STAGE_ROOT"] = self.tmp
        os.environ["CORPUS_WORK"] = os.path.join(self.tmp, "work")
        os.makedirs(cr.runs_dir())
        self.json = json

    def test_stuck_and_failed_lines(self):
        old = "2026-01-01T00:00:00.000Z"
        new = cr.now_iso()[:19] + ".000Z"
        pend = [{"Path": "Alpha/%s/pending-2026-01-01-a-1.json" % cr.KB, "ModTime": old},
                {"Path": "Alpha/%s/pending-2026-01-01-b-2.json" % cr.KB, "ModTime": old},
                {"Path": "Beta/%s/pending-2026-11-24-c-1.json" % cr.KB, "ModTime": new},
                {"Path": "Gamma/%s/pending-failures.json" % cr.KB, "ModTime": old},
                {"Path": "Gamma/%s/pending-2026-11-24-d-1.json" % cr.KB, "ModTime": new}]   # the failed sync still waits beside its list
        cr.write_pending_watch(pend, {"Alpha": "1", "Beta": "2", "Gamma": "3"})
        text = self.advise()
        self.assertIn("Alpha: 2 waiting", text)
        self.assertNotIn("Beta", text)          # filed minutes ago: not stuck
        self.assertIn("Gamma: 1 failed", text)

    def test_applied_syncs_drop_out_at_finish(self):
        old = "2026-01-01T00:00:00.000Z"
        pend = [{"Path": "Alpha/%s/pending-2026-01-01-a-1.json" % cr.KB, "ModTime": old},
                {"Path": "Alpha/%s/pending-2026-01-01-b-2-p1.json" % cr.KB, "ModTime": old}]
        cr.write_pending_watch(pend, {"Alpha": "1"})
        cr.save("applied-syncs.json", ["2026-01-01-a-1"])
        cr.update_pending_watch()
        w = self.json.load(open(cr.watch_path()))["clients"]
        self.assertEqual([x["file"] for x in w["Alpha"]["waiting"]], ["pending-2026-01-01-b-2-p1.json"])
        cr.save("applied-syncs.json", ["2026-01-01-a-1", "2026-01-01-b-2"])
        cr.update_pending_watch()
        self.assertEqual(self.json.load(open(cr.watch_path()))["clients"], {})
        self.assertNotIn("Syncs not applied", self.advise())

    def advise(self):
        import io, contextlib
        class A: run = None; hours = 24
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                cr.cmd_advise(A)
            except SystemExit:
                pass
        return self.json.loads(buf.getvalue())["text"]


class FolderNames(unittest.TestCase):
    def test_short_name_maps_to_folder_name(self):
        import json, tempfile
        d = tempfile.mkdtemp(); os.environ["CORPUS_WORK"] = d
        json.dump({"Service Foods": {"folder_id": "x"}}, open(os.path.join(d, "candidates.json"), "w"))
        json.dump({"Service Foods": "Service Foods NZ"}, open(os.path.join(d, "shorts.json"), "w"))
        self.assertEqual(cr.folder_name("Service Foods NZ"), "Service Foods")   # short name -> folder name
        self.assertEqual(cr.folder_name("Service Foods"), "Service Foods")      # folder name unchanged
        self.assertEqual(cr.folder_name("Unknown Client"), "Unknown Client")    # unknown passes through
        del os.environ["CORPUS_WORK"]

class CarryOver(unittest.TestCase):
    """A client carried over from the last run must get a registry row even with no Drive change (the 'no-registry-entry' bug)."""
    def setUp(self):
        import tempfile, json
        self.tmp = tempfile.mkdtemp()
        os.environ["STAGE_ROOT"] = self.tmp
        os.environ["CORPUS_WORK"] = os.path.join(self.tmp, "work")
        os.makedirs(cr.runs_dir())
        os.makedirs(os.path.join(self.tmp, cr.OPS), exist_ok=True)
        json.dump({"carry_over": [{"client": "Box Hill", "attempts": 0, "sources": ["drive"]},
                                  {"client": "Service Foods NZ", "attempts": 0, "sources": ["drive"]},
                                  {"client": "Gone Client", "attempts": 0, "sources": ["drive"]}]},
                  open(os.path.join(cr.runs_dir(), "corpus-run-state.json"), "w"))
        json.dump({"clients": [{"folder_name": "Service Foods", "short": "Service Foods NZ"}]},
                  open(os.path.join(self.tmp, cr.OPS, "corpus-index.json"), "w"))

    def test_folder_and_short_names_resolve(self):
        ids = {"Box Hill": "1", "Service Foods": "2", "Other": "3"}
        self.assertEqual(cr.carried_over_folders(ids), ["Box Hill", "Service Foods"])   # unknown client dropped, order kept

    def test_no_state_file_means_nothing_carried(self):
        os.remove(os.path.join(cr.runs_dir(), "corpus-run-state.json"))
        self.assertEqual(cr.carried_over_folders({"Box Hill": "1"}), [])

    def test_discover_adds_carried_clients_to_candidates_and_registry(self):
        import json, subprocess
        from unittest import mock
        json.dump({"run_id": "r", "since": "2026-11-26T12:38:42Z", "mode": "changed"}, open(os.path.join(cr.work(), "run.json"), "w"))
        top = json.dumps([{"Name": "Box Hill", "ID": "1"}, {"Name": "Service Foods", "ID": "2"}, {"Name": "Other", "ID": "3"}])
        def fake_rclone(args):
            return subprocess.CompletedProcess(args, 0, top if "--dirs-only" in args else "[]", "")   # no Drive file changed
        with mock.patch.object(cr, "rclone", fake_rclone):
            try:
                cr.cmd_discover(None)
            except SystemExit:
                pass
        cands = cr.load("candidates.json")
        self.assertEqual(sorted(cands), ["Box Hill", "Service Foods"])    # "Other" has no change and was not carried
        stages = {c: {"short": c, "stage": "Client"} for c in cands}
        json.dump(stages, open(os.path.join(cr.work(), "stages.json"), "w"))
        # the registry plan() writes is built from the candidates, so both carried clients now have a row
        reg_names = [c for c in cands if c in stages]
        self.assertEqual(reg_names, ["Box Hill", "Service Foods"])


@unittest.skipUnless(cr.skill(), "client-corpus skill not installed")
class DigestResolvedFailures(unittest.TestCase):
    """The digest must not list a failure or a dropped-after-retries flag for a client a later run swept."""
    def digest(self, rows_by_run):
        import json, shutil, subprocess, tempfile
        tmp = tempfile.mkdtemp()
        sk = os.path.join(tmp, "skill")
        shutil.copytree(cr.skill(), sk)
        r = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "apply_skill_patches.py"), sk],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        rd = os.path.join(tmp, "runs")
        os.makedirs(rd)
        for i, rows in enumerate(rows_by_run):
            rec = {"schema": 1, "run_id": "run-2026112%d-0%d00" % (6, i), "mode": "changed", "target": "live", "status": "complete",
                   "started": "2026-11-26T0%d:00:00Z" % i, "ended": "2026-11-26T0%d:05:00Z" % i, "since": "2026-11-26T00:00:00Z",
                   "clients": rows, "events": [], "carry_over": [], "totals": {}}
            open(os.path.join(rd, rec["run_id"] + ".md"), "w").write("# run\n<!-- run-data: %s -->\n" % json.dumps(rec))
        json.dump({"completed_runs": 3, "failure_totals": {"readback": 0, "manifest-shrank": 0, "over-budget": 0, "misfiled": 0},
                   "carry_over": []}, open(os.path.join(rd, "corpus-run-state.json"), "w"))
        p = subprocess.run([sys.executable, os.path.join(sk, "scripts", "unattended.py"), "digest", "--runs-dir", rd,
                            "--since", "2026-11-26T00:00:00Z", "--until", "2026-11-27T00:00:00Z", "--now", "2026-11-27T01:00:00Z"],
                           capture_output=True, text=True)
        return json.loads(p.stdout)

    @staticmethod
    def row(client, outcome, reason=None, flags=()):
        return {"client": client, "outcome": outcome, "reason": reason, "flags": list(flags), "failures": [], "files": [], "sources": []}

    def test_failure_then_sweep_is_resolved(self):
        d = self.digest([[self.row("Atlantic", "failed", "write-failed")],
                         [self.row("Atlantic", "failed", "write-failed", ["dropped-after-retries"])],
                         [self.row("Atlantic", "swept")]])
        self.assertEqual(d["status"], "ok")
        self.assertNotIn("Failed clients", d["text"])
        self.assertNotIn("Dropped after three", d["text"])
        # patched wording, or the skill's own wording from 13.12.0 on
        self.assertTrue("swept since (nothing to do): Atlantic" in d["text"]
                        or "fixed by a later run (no action):\n- Atlantic" in d["text"], d["text"])

    def test_unresolved_failure_still_listed(self):
        d = self.digest([[self.row("Atlantic", "swept")],
                         [self.row("Atlantic", "failed", "write-failed", ["dropped-after-retries"]), self.row("Other", "failed", "write-failed")]])
        self.assertEqual(d["status"], "needs-you")
        self.assertIn("Atlantic: failed: write-failed", d["text"])
        self.assertIn("Dropped after three failed attempts", d["text"])
        self.assertIn("Other: failed", d["text"])
        self.assertNotIn("swept since", d["text"])


class DuplicateTopFolders(unittest.TestCase):
    TOP = [{"Name": "Counties Manukau Sport", "ID": "19BG"}, {"Name": "Solo", "ID": "s1"}, {"Name": "Counties Manukau Sport", "ID": "1e9H"}]

    def test_split_keeps_unique_and_reports_duplicates(self):
        ids, dups = cr.split_top_folders(self.TOP)
        self.assertEqual(ids, {"Solo": "s1"})
        self.assertEqual(dups, {"Counties Manukau Sport": ["19BG", "1e9H"]})

    def test_discover_skips_duplicate_named_client_and_records_it(self):
        import json, subprocess, tempfile
        from unittest import mock
        tmp = tempfile.mkdtemp()
        os.environ["STAGE_ROOT"] = tmp
        os.environ["CORPUS_WORK"] = os.path.join(tmp, "work")
        os.makedirs(cr.runs_dir())
        json.dump({"run_id": "r", "since": "2026-11-26T12:38:42Z", "mode": "changed"}, open(os.path.join(cr.work(), "run.json"), "w"))
        listing = [{"Path": "Counties Manukau Sport/x.docx", "ID": "f1", "ModTime": "2026-11-27T01:00:00Z"},
                   {"Path": "Solo/y.docx", "ID": "f2", "ModTime": "2026-11-27T01:00:00Z"}]
        def fake_rclone(args):
            return subprocess.CompletedProcess(args, 0, json.dumps(self.TOP if "--dirs-only" in args else listing), "")
        recorded, events = [], []
        with mock.patch.object(cr, "rclone", fake_rclone), \
             mock.patch.object(cr, "record_client", lambda *a, **k: recorded.append(a)), \
             mock.patch.object(cr, "event", lambda *a: events.append(a)):
            try:
                cr.cmd_discover(None)
            except SystemExit:
                pass
        self.assertEqual(sorted(cr.load("candidates.json")), ["Solo"])    # the ambiguous folder is never a candidate
        self.assertEqual(recorded, [("Counties Manukau Sport", "skipped", "duplicate-titles", "drive")])
        self.assertIn("2 top-level client folders", events[0][1])


class EndedSessionMarkers(unittest.TestCase):
    """A session that files a pending sync leaves its open-*.md marker for apply-pending to close. The run's
    open-session skip must not hold such a client back, or the pending sync that closes the marker never runs."""
    MARKER = "open-20261127-0942-owenb.md"

    def setUp(self):
        import tempfile
        self.kb = tempfile.mkdtemp()

    def pend(self, name, part=1, parts=1, marker=MARKER, closed_at="2026-11-26T20:43:46Z"):
        import json
        delta = {"member": "owen.b@wiselyglobal.tech", "date": "2026-11-27"}
        if part == 1:  # the light keys travel in part 1
            if marker:
                delta["marker"] = marker
            if closed_at:
                delta["closed_at"] = closed_at
        with open(os.path.join(self.kb, name), "w") as fh:
            json.dump({"pending": 2, "id": cr._pend_id(name), "part": part, "parts": parts, "delta": delta}, fh)

    def test_complete_single_part_names_its_marker(self):
        self.pend("pending-2026-11-27-owenb-0943.json")
        self.assertEqual(cr.ended_session_markers(self.kb), {self.MARKER})

    def test_complete_multi_part_names_its_marker(self):
        self.pend("pending-2026-11-27-owenb-0943-p1.json", 1, 2)
        self.pend("pending-2026-11-27-owenb-0943-p2.json", 2, 2)
        self.assertEqual(cr.ended_session_markers(self.kb), {self.MARKER})

    def test_missing_part_keeps_the_marker_open(self):
        self.pend("pending-2026-11-27-owenb-0943-p1.json", 1, 2)   # part 2 not uploaded yet
        self.assertEqual(cr.ended_session_markers(self.kb), set())

    def test_no_marker_or_no_closed_at_names_nothing(self):
        self.pend("pending-2026-11-27-owenb-1003.json", marker=None)
        self.pend("pending-2026-11-27-owenb-1100.json", closed_at=None)
        self.assertEqual(cr.ended_session_markers(self.kb), set())

    def test_failures_file_and_garbage_are_ignored(self):
        open(os.path.join(self.kb, "pending-failures.json"), "w").write("{}")
        open(os.path.join(self.kb, "pending-2026-11-27-x-1.json"), "w").write("not json")
        self.assertEqual(cr.ended_session_markers(self.kb), set())
        self.assertEqual(cr.ended_session_markers(os.path.join(self.kb, "missing")), set())

    def open_marker(self, *lines, undated=()):
        return {"skip": True, "reason": "session-open", "undated": list(undated),
                "open": [{"date": "2026-11-27", "time": "09:42", "age_hours": 10.0, "line": ln} for ln in lines]}

    def test_no_skip_stays_no_skip(self):
        self.assertFalse(cr.session_still_open({"skip": False, "open": [], "undated": []}, set()))

    def test_marker_awaiting_its_pending_sync_does_not_hold_the_client(self):
        self.assertFalse(cr.session_still_open(self.open_marker(self.MARKER), {self.MARKER}))

    def test_live_session_still_skips(self):
        self.assertTrue(cr.session_still_open(self.open_marker(self.MARKER), set()))
        self.assertTrue(cr.session_still_open(self.open_marker(self.MARKER, "open-20261127-1321-owenb.md"), {self.MARKER}))

    def test_changelog_or_unreadable_markers_still_skip(self):
        legacy = "opened 2026-11-27 09:42 - owen.b@wiselyglobal.tech"   # an in-changelog marker is not a file name
        self.assertTrue(cr.session_still_open(self.open_marker(legacy), {self.MARKER}))
        self.assertTrue(cr.session_still_open(self.open_marker(self.MARKER, undated=["open-bad.md"]), {self.MARKER}))

    def test_plan_uses_the_ended_marker_check(self):
        import inspect
        src = inspect.getsource(cr.cmd_plan)
        self.assertIn("session_still_open(om, ended_session_markers(kb))", src)
        self.assertNotIn('if om.get("skip"):', src)


class StageUnknownAdvice(unittest.TestCase):
    def test_mentions_folder_link_not_only_status(self):
        meaning, cmds = cr.ADVICE["stage-unknown"]
        self.assertIn("Client Folder G Drive", meaning)
        self.assertTrue(any("Client Folder G Drive" in c for c in cmds))
        self.assertTrue(any("{c}" in c for c in cmds))


class IdleDuplicateFolder(unittest.TestCase):
    def test_idle_duplicate_is_a_note_not_a_skip(self):
        import json, subprocess, tempfile
        from unittest import mock
        tmp = tempfile.mkdtemp()
        os.environ["STAGE_ROOT"] = tmp
        os.environ["CORPUS_WORK"] = os.path.join(tmp, "work")
        os.makedirs(cr.runs_dir())
        json.dump({"run_id": "r", "since": "2026-11-26T12:38:42Z", "mode": "changed"}, open(os.path.join(cr.work(), "run.json"), "w"))
        top = [{"Name": "Tegel Foods Ltd", "ID": "a1"}, {"Name": "Tegel Foods Ltd", "ID": "b2"}, {"Name": "Solo", "ID": "s1"}]
        listing = [{"Path": "Solo/y.docx", "ID": "f2", "ModTime": "2026-11-27T01:00:00Z"},
                   {"Path": "Tegel Foods Ltd/old.docx", "ID": "f3", "ModTime": "2026-01-01T00:00:00Z"}]
        def fake_rclone(args):
            return subprocess.CompletedProcess(args, 0, json.dumps(top if "--dirs-only" in args else listing), "")
        recorded, events = [], []
        with mock.patch.object(cr, "rclone", fake_rclone), \
             mock.patch.object(cr, "record_client", lambda *a, **k: recorded.append(a)), \
             mock.patch.object(cr, "event", lambda *a: events.append(a)):
            try:
                cr.cmd_discover(None)
            except SystemExit:
                pass
        self.assertEqual(recorded, [])
        self.assertIn("idle", events[0][1])


class StaleFailureList(unittest.TestCase):
    def _watch(self, pend):
        import json, tempfile
        tmp = tempfile.mkdtemp()
        os.environ["STAGE_ROOT"] = tmp
        os.environ["CORPUS_WORK"] = os.path.join(tmp, "work")
        os.makedirs(cr.runs_dir())
        cr.write_pending_watch(pend, {"A": "idA", "B": "idB"})
        return json.load(open(cr.watch_path()))["clients"]

    def test_failure_list_with_no_pending_file_beside_it_is_stale(self):
        kb = "Claude Project Knowledge Base"
        w = self._watch([{"Path": "A/%s/pending-failures.json" % kb, "ModTime": "2026-11-23T07:13:25Z"}])
        self.assertEqual(w, {})

    def test_failure_list_with_a_waiting_pending_file_stays_open(self):
        kb = "Claude Project Knowledge Base"
        w = self._watch([{"Path": "B/%s/pending-failures.json" % kb, "ModTime": "2026-11-23T07:13:25Z"},
                         {"Path": "B/%s/pending-2026-11-17-x-1.json" % kb, "ModTime": "2026-11-17T00:55:04Z"}])
        self.assertTrue(w["B"]["failed"])
        self.assertEqual(len(w["B"]["waiting"]), 1)


class TidyWiring(unittest.TestCase):
    def test_stage_unknown_advice_says_what_to_do(self):
        meaning, cmds = cr.ADVICE["stage-unknown"]
        text = " ".join(cmds)
        for needle in ("Accounts board", "Client Folder G Drive", "Account Status", "/sweep"):
            self.assertIn(needle, text)
        self.assertIn("board is the authority", meaning)

    def test_conflicts_become_questions_and_a_needs_question_record(self):
        import argparse
        from unittest import mock
        import corpus_tidy as ct, corpus_stage as cs
        rep = {"client": "A", "files_seen": 3, "planned": 0, "ops": [], "applied": True, "attempted": True, "moved": 2, "written": 0, "deferred": 0, "problems": [],
               "flags": [{"path": "m.json", "kind": "conflict", "question": "m.json exists twice and the copies disagree: $.x is 1 and 2. Which is right?"}]}
        events, recorded = [], []
        import tempfile
        os.environ["CORPUS_WORK"] = tempfile.mkdtemp()
        cr.save("run.json", {"run_id": "r"})                      # a run is active, so the run record is written
        with mock.patch.object(ct, "run", lambda *a, **k: rep), mock.patch.object(ct, "RcloneDrive", lambda rc, **k: None), \
             mock.patch.object(cr, "event", lambda k, d: events.append(d)), \
             mock.patch.object(cr, "record_client", lambda *a, **k: recorded.append(a)), \
             mock.patch.object(cr, "short_of", lambda c: "A"):
            try:
                cr.cmd_tidy(argparse.Namespace(client="A", apply=True, cap=40, today="2026-11-28"))
            except SystemExit as e:
                self.assertEqual(e.code, 0)
        self.assertTrue(any("needs-question: m.json" in e for e in events))
        self.assertEqual(recorded, [("A", "skipped", "needs-question", "drive")])


    def test_apply_outside_a_run_still_reports_and_does_not_abort(self):
        import argparse, io, tempfile, contextlib
        from unittest import mock
        import corpus_tidy as ct
        os.environ["CORPUS_WORK"] = tempfile.mkdtemp()           # no run.json here
        rep = {"client": "A", "files_seen": 1, "planned": 1, "ops": [], "applied": True, "attempted": True, "moved": 1, "written": 0, "deferred": 0, "problems": [], "flags": []}
        buf = io.StringIO()
        with mock.patch.object(ct, "run", lambda *a, **k: rep), mock.patch.object(ct, "RcloneDrive", lambda rc, **k: None), \
             mock.patch.object(cr, "short_of", lambda c: "A"), contextlib.redirect_stdout(buf):
            try:
                cr.cmd_tidy(argparse.Namespace(client="A", apply=True, cap=40, today="2026-11-28"))
            except SystemExit as e:
                self.assertEqual(e.code, 0)
        out = json.loads(buf.getvalue())
        self.assertTrue(out["ok"])
        self.assertEqual(out["moved"], 1)
        self.assertFalse(out["logged_to_run"])

    def test_dry_run_records_nothing(self):
        import argparse
        from unittest import mock
        import corpus_tidy as ct
        rep = {"client": "A", "files_seen": 1, "planned": 1, "ops": [], "applied": False, "attempted": False, "flags": [{"path": "x", "kind": "conflict", "question": "q"}]}
        events, recorded = [], []
        with mock.patch.object(ct, "run", lambda *a, **k: rep), mock.patch.object(ct, "RcloneDrive", lambda rc, **k: None), \
             mock.patch.object(cr, "event", lambda k, d: events.append(d)), \
             mock.patch.object(cr, "record_client", lambda *a, **k: recorded.append(a)), \
             mock.patch.object(cr, "short_of", lambda c: "A"):
            try:
                cr.cmd_tidy(argparse.Namespace(client="A", apply=False, cap=40, today="2026-11-28"))
            except SystemExit:
                pass
        self.assertEqual((events, recorded), ([], []))


class AdviseCards(unittest.TestCase):
    def test_cards_lead_the_email_and_replace_the_generic_duplicate_line(self):
        import json, tempfile, io, contextlib, argparse
        import corpus_conflicts as cc
        tmp = tempfile.mkdtemp()
        os.environ["STAGE_ROOT"] = tmp
        os.environ["CORPUS_WORK"] = os.path.join(tmp, "work")
        os.makedirs(cr.runs_dir())
        card = cc.card_duplicate_folders("Mossop", [{"id": "OLD", "created": "2025-05-17T00:00:00Z", "facts": None}, {"id": "NEW", "created": "2026-05-02T00:00:00Z", "facts": None}], {})
        card["first_seen"] = "2026-11-27"
        json.dump({"cards": [card]}, open(cr.conflicts_path(), "w"))
        rec = {"run_id": "run-1", "started": cr.now_iso(), "clients": [{"client": "Mossop", "outcome": "skipped", "reason": "duplicate-titles"}], "events": []}
        open(os.path.join(cr.runs_dir(), "run-1.md"), "w").write("# r\n<!-- run-data: %s -->\n" % json.dumps(rec))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                cr.cmd_advise(argparse.Namespace(run=None, hours=24))
            except SystemExit:
                pass
        text = json.loads(buf.getvalue()).get("text") if buf.getvalue().lstrip().startswith("{") else buf.getvalue()
        self.assertIn("CONFLICT: Mossop", text)
        self.assertIn("What we need from you:", text)
        self.assertNotIn("What it means: Two Drive files carry the same corpus file title", text)


class Promote(unittest.TestCase):
    OK = dict(names={"current-state.md", "now.md", "source-manifest.json", "changelog.md", "brief.json"}, live_exists=False,
              manifest={"sources": []}, cs_text="# X\nVersion: 1\n", budget_ok=True, stage_row={"stage": "client", "client_id": "CL-0195"})

    def problems(self, **kw):
        a = dict(self.OK)
        a.update(kw)
        return cr.promote_problems(a["names"], a["live_exists"], a["manifest"], a["cs_text"], a["budget_ok"], a["stage_row"])

    def test_a_complete_build_for_a_board_client_may_go_live(self):
        self.assertEqual(self.problems(), [])

    def test_each_skill_check_blocks_it(self):
        self.assertIn("shadow is missing now.md", self.problems(names={"current-state.md", "source-manifest.json", "changelog.md"}))
        self.assertIn("a live corpus folder already exists: promotion never overwrites it", self.problems(live_exists=True))
        self.assertTrue(any("manifest" in e for e in self.problems(manifest={"nope": 1})))
        self.assertIn("current-state.md has no Version line", self.problems(cs_text="no version"))
        self.assertTrue(any("budget" in e for e in self.problems(budget_ok=False)))
        self.assertEqual(self.problems(names=None), ["no shadow folder to promote"])

    def test_the_board_must_say_client_and_hold_a_client_id(self):
        self.assertTrue(any("Client (stage" in e for e in self.problems(stage_row={"stage": "prospect", "client_id": "CL-1"})))
        self.assertTrue(any("Client (stage" in e for e in self.problems(stage_row=None)))
        self.assertIn("the Accounts item has no Client ID", self.problems(stage_row={"stage": "client", "client_id": None}))


class CustomerStatus(unittest.TestCase):
    def test_customer_reads_as_a_prospect_so_it_is_neither_swept_nor_built(self):
        self.assertEqual(cr._status_of("Customer"), "prospect")
        self.assertEqual(cr._status_of("Client"), "client")

    def test_a_status_map_entry_still_overrides(self):
        self.assertEqual(cr._status_of("Customer", {"Customer": "client"}), "client")


class BuildRequests(unittest.TestCase):
    CLIENT = {"stage": "client", "client_id": "CL-0195", "short": "One New Zealand", "item_id": "2704765722"}

    def test_a_client_with_files_and_no_request_gets_one(self):
        self.assertEqual(cr.build_request_decision(self.CLIENT, 6, False, 0), (True, "ok"))

    def test_the_board_decides_identity(self):
        self.assertFalse(cr.build_request_decision(None, 6, False, 0)[0])
        self.assertFalse(cr.build_request_decision({"stage": "prospect", "client_id": "CL-1"}, 6, False, 0)[0])
        self.assertFalse(cr.build_request_decision({"stage": "client", "client_id": None}, 6, False, 0)[0])

    def test_an_empty_template_folder_never_becomes_an_empty_corpus(self):
        ok, why = cr.build_request_decision(self.CLIENT, 0, False, 0)
        self.assertFalse(ok)
        self.assertIn("empty template", why)

    def test_a_thin_folder_waits_for_three_files(self):
        self.assertFalse(cr.build_request_decision(self.CLIENT, 2, False, 0)[0])
        self.assertTrue(cr.build_request_decision(self.CLIENT, 3, False, 0)[0])

    def test_no_activity_in_twelve_months_means_no_request(self):
        self.assertFalse(cr.build_request_decision(self.CLIENT, 9, False, 0, newest="2025-11-01", today="2026-11-28")[0])
        self.assertTrue(cr.build_request_decision(self.CLIENT, 9, False, 0, newest="2025-12-01", today="2026-11-28")[0])
        self.assertTrue(cr.build_request_decision(self.CLIENT, 9, False, 0, newest=None, today="2026-11-28")[0])

    def test_seed_plan_continues_the_board_sequence_for_eligible_clients_only(self):
        nc = {"A": {"folder_id": "1", "files": 9, "newest": "2026-10-01"}, "B": {"folder_id": "2", "files": 5, "newest": "2026-09-01"},
              "Old": {"folder_id": "3", "files": 8, "newest": "2024-01-01"}, "Thin": {"folder_id": "4", "files": 2, "newest": "2026-10-01"},
              "HasId": {"folder_id": "5", "files": 7, "newest": "2026-10-01"}, "Prosp": {"folder_id": "6", "files": 7, "newest": "2026-10-01"}}
        st = {n: {"stage": "client", "client_id": None, "short": n, "item_id": "i" + n} for n in nc}
        st["HasId"]["client_id"] = "CL-0007"; st["Prosp"]["stage"] = "prospect"
        plan = cr.seed_plan(nc, st, ["CL-0204", "CL-0007", None, "x"], {}, 10, "2026-11-28")
        self.assertEqual([(p["folder"], p["client_id"]) for p in plan], [("A", "CL-0205"), ("B", "CL-0206")])
        self.assertEqual(len(cr.seed_plan(nc, st, [], {}, 1, "2026-11-28")), 1)

    def test_one_request_per_client_and_a_cap_per_night(self):
        self.assertFalse(cr.build_request_decision(self.CLIENT, 6, True, 0)[0])
        self.assertFalse(cr.build_request_decision(self.CLIENT, 6, False, 2, cap=2)[0])
        self.assertTrue(cr.build_request_decision(self.CLIENT, 6, False, 1, cap=2)[0])

    def test_compute_no_corpus_finds_folders_with_files_and_no_kb_or_shadow(self):
        ids = {"OneNZ": "id1", "Serko": "id2", "NewCo": "id3", "Empty": "id4"}
        files = [{"Path": "OneNZ/a.pdf"}, {"Path": "OneNZ/Commercials/b.pdf"}, {"Path": "Serko/x.docx"}, {"Path": "NewCo/y.pdf"}]
        dirs = [{"Path": "Serko/" + cr.KB}, {"Path": "NewCo/" + cr.KB + " (shadow)"}, {"Path": "OneNZ/Commercials"}]
        nc = cr.compute_no_corpus(ids, files, dirs)
        self.assertEqual(set(nc), {"OneNZ", "Empty"})            # Serko has a corpus, NewCo a shadow build under way
        self.assertEqual(nc["OneNZ"]["files"], 2)
        self.assertEqual(nc["Empty"]["files"], 0)

    def _run(self, nc, stages, existing=()):
        import json, tempfile, io, contextlib, argparse
        from unittest import mock
        tmp = tempfile.mkdtemp()
        os.environ["STAGE_ROOT"] = tmp
        os.environ["CORPUS_WORK"] = os.path.join(tmp, "work")
        os.makedirs(os.path.join(tmp, cr.OPS, "corpus-requests"))
        cr.save("no_corpus.json", nc)
        cr.save("stages.json", stages)
        for name in existing:
            json.dump({"client": name, "status": "requested"}, open(os.path.join(tmp, cr.OPS, "corpus-requests", "build-request-x-2026-11-01.json"), "w"))
        events = []

        def fake_jpy(script, args):
            d = args[args.index("--out") + 1]
            f = os.path.join(d, "build-request-%s.json" % args[args.index("--client") + 1].lower().replace(" ", "-"))
            json.dump({"client": args[args.index("--client") + 1], "status": "requested", "note": args[args.index("--note") + 1]}, open(f, "w"))
            return 0, {"ok": True, "file": f}
        buf = io.StringIO()
        with mock.patch.object(cr, "jpy", fake_jpy), mock.patch.object(cr, "event", lambda k, d: events.append(d)), contextlib.redirect_stdout(buf):
            try:
                cr.cmd_build_requests(argparse.Namespace(cap=2))
            except SystemExit:
                pass
        return json.loads(buf.getvalue()), tmp, events

    def test_the_command_files_requests_for_clients_only_and_is_idempotent(self):
        nc = {"OneNZ": {"folder_id": "f1", "files": 6}, "Empty": {"folder_id": "f2", "files": 0}, "Prosp": {"folder_id": "f3", "files": 9}}
        stages = {"OneNZ": dict(self.CLIENT), "Empty": {"stage": "client", "client_id": "CL-2", "short": "Empty"}, "Prosp": {"stage": "prospect", "client_id": "CL-3"}}
        r, tmp, events = self._run(nc, stages)
        self.assertEqual([f["client"] for f in r["filed"]], ["One New Zealand"])
        self.assertTrue(os.path.exists(os.path.join(tmp, cr.OPS, "corpus-requests", "build-request-one-new-zealand.json")))
        self.assertEqual(len(events), 1)
        r2, _, _ = self._run(nc, stages, existing=["One New Zealand"])          # now a request is on file
        self.assertEqual(r2["filed"], [])
        self.assertIn("a build request already exists", r2["skipped"])

    def test_the_cap_limits_a_night(self):
        nc = {"A": {"folder_id": "1", "files": 5}, "B": {"folder_id": "2", "files": 4}, "C": {"folder_id": "3", "files": 3}}
        stages = {n: {"stage": "client", "client_id": "CL-" + n, "short": n} for n in nc}
        r, _, _ = self._run(nc, stages)
        self.assertEqual(len(r["filed"]), 2)
        self.assertEqual(r["skipped"], {"tonight's cap of 2 requests is reached": 1})

    def test_a_client_with_a_request_on_file_is_not_reported_as_needing_a_person(self):
        import json, tempfile, io, contextlib, argparse
        tmp = tempfile.mkdtemp()
        os.environ["STAGE_ROOT"] = tmp
        os.environ["CORPUS_WORK"] = os.path.join(tmp, "work")
        os.makedirs(os.path.join(tmp, cr.OPS, "corpus-requests"))
        os.makedirs(cr.runs_dir())
        json.dump({"client": "One New Zealand", "status": "requested"}, open(os.path.join(tmp, cr.OPS, "corpus-requests", "build-request-x.json"), "w"))
        rec = {"run_id": "run-1", "started": cr.now_iso(), "clients": [{"client": "One New Zealand", "outcome": "skipped", "reason": "no-corpus"}], "events": []}
        open(os.path.join(cr.runs_dir(), "run-1.md"), "w").write("# r\n<!-- run-data: %s -->\n" % json.dumps(rec))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                cr.cmd_advise(argparse.Namespace(run=None, hours=24))
            except SystemExit:
                pass
        self.assertNotIn("no-corpus", buf.getvalue())



class QueueReconcile(unittest.TestCase):
    def f(self, *names, native=()):
        return [{"Path": n, "MimeType": "application/vnd.google-apps.document" if n in native else "text/plain"} for n in names]
    CORE = ("current-state.md", "source-manifest.json", "changelog.md", "now.md")

    def test_a_mount_mismatch_row_closes_when_drive_has_one_file_per_name(self):
        self.assertTrue(cr.queue_row_resolved("mount-name-mismatch", self.f(*self.CORE), None, False)[0])

    def test_suffixed_or_duplicate_names_keep_the_row_open(self):
        self.assertFalse(cr.queue_row_resolved("duplicate-titles", self.f(*self.CORE, "now (1).md"), None, False)[0])
        self.assertFalse(cr.queue_row_resolved("duplicate-titles", self.f(*self.CORE, "now.md"), None, False)[0])

    def test_two_top_level_folders_keep_the_row_open(self):
        self.assertFalse(cr.queue_row_resolved("duplicate-titles", self.f(*self.CORE), None, True)[0])

    def test_a_pending_entry_row_closes_when_nothing_is_waiting(self):
        self.assertTrue(cr.queue_row_resolved("pending-entry", self.f(*self.CORE, "pending-failures.json"), None, False)[0])
        self.assertFalse(cr.queue_row_resolved("pending-entry", self.f(*self.CORE, "pending-2026-11-17-x.json"), None, False)[0])
        self.assertFalse(cr.queue_row_resolved("pending-entry", self.f(*self.CORE, "changelog-pending-2026-11-09.md"), None, False)[0])

    def test_stage_unknown_closes_when_the_board_gives_a_stage(self):
        self.assertTrue(cr.queue_row_resolved("stage-unknown", None, {"stage": "client"}, False)[0])
        self.assertFalse(cr.queue_row_resolved("stage-unknown", None, {"stage": ""}, False)[0])

    def test_missing_core_and_old_shape(self):
        self.assertFalse(cr.queue_row_resolved("missing-core", self.f("changelog.md"), None, False)[0])
        self.assertFalse(cr.queue_row_resolved("old-shape", self.f(*self.CORE, "current-state 2026-10-01.md"), None, False)[0])
        self.assertTrue(cr.queue_row_resolved("old-shape", self.f(*self.CORE), None, False)[0])

    def test_no_corpus_folder_leaves_the_row_open(self):
        self.assertFalse(cr.queue_row_resolved("pending-entry", None, None, False)[0])

    def test_a_duplicate_folder_row_closes_once_the_folder_is_one_and_has_no_corpus(self):
        self.assertTrue(cr.queue_row_resolved("duplicate-titles", None, None, False)[0])
        self.assertFalse(cr.queue_row_resolved("duplicate-titles", None, None, True)[0])

    def test_alias_rows_resolve_to_their_folder(self):
        m = cr.folder_lookup({"Barfoot & Thompson", "Mushroom Creative House Pty Ltd"},
                             [{"short": "Barfoot", "aliases": ["B&T"], "folder_name": "Barfoot & Thompson"},
                              {"short": "Mushroom CH", "aliases": [], "folder_name": "Mushroom Creative House Pty Ltd"}], {})
        import corpus_conflicts as cc
        self.assertEqual(m[cc.name_key("Barfoot")], "Barfoot & Thompson")
        self.assertEqual(m.get(cc.name_key("Mushroom CH")), "Mushroom Creative House Pty Ltd")

    def test_resolve_prefers_exact_names_and_never_guesses_between_two(self):
        names = {"Serko", "Serko Ltd.", "Barfoot & Thompson", "Barfield Ltd"}
        m = cr.folder_lookup(names, [], {})
        self.assertEqual(cr.resolve_folder("Serko", names, m), "Serko")              # two folders share a key: exact name wins
        self.assertEqual(cr.resolve_folder("Barfoot", names, m), "Barfoot & Thompson")  # one folder starts with it
        self.assertIsNone(cr.resolve_folder("Barf", names, m))                       # too short
        self.assertIsNone(cr.resolve_folder("CMS", names, m))


class FolderMerge(unittest.TestCase):
    def d(self, path, id_):
        return {"Path": path, "ID": id_, "IsDir": True}

    def f(self, path, id_, md5="a", size=10):
        return {"Path": path, "ID": id_, "IsDir": False, "Size": size, "Hashes": {"md5": md5}}

    def test_what_the_linked_folder_lacks_moves_and_matching_folders_are_merged_inside(self):
        linked = [self.d("Commercials", "lc")]
        other = [self.d("Commercials", "oc"), self.f("Commercials/p.pdf", "f1"), self.d("Ref", "or"), self.f("Ref/x.xlsx", "f2")]
        plan = cr.plan_folder_merge("LR", "OR", linked, other)
        self.assertEqual([(m["path"], m["from"], m["to"]) for m in plan["moves"]], [("Ref", "OR", "LR"), ("Commercials/p.pdf", "oc", "lc")])
        self.assertEqual(plan["conflicts"], [])

    def test_a_moved_folder_hides_what_is_inside_it(self):
        plan = cr.plan_folder_merge("LR", "OR", [], [self.d("Ref", "or"), self.f("Ref/x", "f2"), self.d("Ref/deep", "d3"), self.f("Ref/deep/y", "f4")])
        self.assertEqual([m["path"] for m in plan["moves"]], ["Ref"])

    def test_identical_files_are_not_moved_and_different_ones_are_conflicts(self):
        linked = [self.f("a.pdf", "l1", "m1"), self.f("b.pdf", "l2", "m2")]
        other = [self.f("a.pdf", "o1", "m1"), self.f("b.pdf", "o2", "OTHER"), self.f("c.pdf", "o3", "m3")]
        plan = cr.plan_folder_merge("LR", "OR", linked, other)
        self.assertEqual(plan["identical"], ["a.pdf"])
        self.assertEqual(plan["conflicts"], ["b.pdf"])
        self.assertEqual([m["path"] for m in plan["moves"]], ["c.pdf"])

    def test_native_docs_without_a_hash_are_never_called_identical(self):
        plan = cr.plan_folder_merge("LR", "OR", [{"Path": "n.docx", "ID": "l", "IsDir": False, "Size": -1}], [{"Path": "n.docx", "ID": "o", "IsDir": False, "Size": -1}])
        self.assertEqual(plan["conflicts"], ["n.docx"])

    def test_a_folder_against_a_file_is_a_conflict(self):
        plan = cr.plan_folder_merge("LR", "OR", [self.f("x", "l")], [self.d("x", "o")])
        self.assertEqual(plan["conflicts"], ["x"])


class FolderMergeCommand(unittest.TestCase):
    def _run(self, trees, apply=True, bad_readback=False):
        import json, tempfile, io, contextlib, argparse, types
        from unittest import mock
        import corpus_stage as cs, drive_api
        tmp = tempfile.mkdtemp()
        os.environ["STAGE_ROOT"] = tmp
        os.environ["CORPUS_WORK"] = os.path.join(tmp, "work")
        cr.save("dup_folders.json", {"Acme": ["L", "O"]})
        board = os.path.join(tmp, "board.json")
        json.dump({"items": [{"id": 1, "name": "Acme", "column_values": {"status": "Client", "link_mm2vffc2": "https://drive.google.com/drive/folders/L"}}]}, open(board, "w"))
        parents = {"f1": ["O"], "f9": ["O"], "L": ["root"], "O": ["root"]}
        calls = []

        class FakeApi:
            def __init__(self, *a, **k): pass
            def get(self, fid): return {"id": fid, "parents": list(parents.get(fid, [])), "trashed": False}
            def move(self, fid, new, old):
                calls.append((fid, new))
                if not bad_readback:
                    parents[fid] = [new]
                    for k in trees:
                        trees[k] = [e for e in trees[k] if e["ID"] != fid]
                return {"id": fid, "parents": list(parents.get(fid, [])), "trashed": False}
            def find_folder(self, parent, name): return None
            def _call(self, *a, **k): parents["HOLD"] = ["L"]; return {"id": "HOLD"}
        def fake_rc(args, check=True):
            fid = args[args.index("--drive-root-folder-id") + 1]
            return types.SimpleNamespace(stdout=json.dumps(trees[fid]), returncode=0, stderr="")
        events, buf = [], io.StringIO()
        with mock.patch.object(cs, "rc", fake_rc), mock.patch.object(drive_api, "DriveApi", FakeApi), mock.patch.object(cr, "event", lambda k, d: events.append(d)), contextlib.redirect_stdout(buf):
            try:
                cr.cmd_merge_folders(argparse.Namespace(file=board, apply=apply))
            except SystemExit:
                pass
        return json.loads(buf.getvalue()), calls, cr.load("dup_folders.json"), events

    def f(self, path, id_, md5="a"):
        return {"Path": path, "ID": id_, "IsDir": False, "Size": 5, "Hashes": {"md5": md5}}

    def test_apply_moves_then_archives_the_twin_and_clears_the_duplicate(self):
        r, calls, dups, events = self._run({"L": [], "O": [self.f("a.pdf", "f1")]})
        self.assertEqual(calls, [("f1", "L"), ("O", "HOLD")])
        self.assertEqual(dups, {})
        self.assertTrue(r["merged"][0]["archived"])

    def test_a_conflict_leaves_the_twin_in_place_and_names_the_file(self):
        r, calls, dups, events = self._run({"L": [self.f("a.pdf", "l1", "x")], "O": [self.f("a.pdf", "f9", "y"), self.f("b.pdf", "f1")]})
        self.assertEqual(calls, [("f1", "L")])
        self.assertEqual(dups, {"Acme": ["L", "O"]})
        self.assertEqual(r["merged"][0]["conflicts"], ["a.pdf"])

    def test_dry_run_moves_nothing(self):
        r, calls, dups, events = self._run({"L": [], "O": [self.f("a.pdf", "f1")]}, apply=False)
        self.assertEqual(calls, [])
        self.assertEqual(r["mode"], "dry-run")

    def test_a_failed_readback_stops_the_run(self):
        r, calls, dups, events = self._run({"L": [], "O": [self.f("a.pdf", "f1")]}, bad_readback=True)
        self.assertFalse(r["ok"])
        self.assertEqual(dups, {"Acme": ["L", "O"]})


class RegistryRows(unittest.TestCase):
    def test_client_list_blocks_give_aliases_and_the_drive_folder(self):
        txt = ("# list\n\n### **Mushroom Group**\n\n- aliases: Mushroom Creative, MCH, Mushroom CH\n- drive: Mushroom Creative House Pty Ltd (18BeqIOkecy5Mv1kS5Z90UoTuujMNSBdf)\n- 7pace: yes\n"
               "\n### **QA Electrical**\n\n- aliases: (none recorded yet)\n- drive: (not resolved)\n")
        rows = cr.registry_rows(txt)
        self.assertEqual(rows, [{"short": "Mushroom Group", "folder_name": "Mushroom Creative House Pty Ltd", "aliases": ["Mushroom Creative", "MCH", "Mushroom CH"]}])
        self.assertEqual(cr.registry_rows("# list\n\n### **Counties Manukau Sport**\n\n- aliases: CMS\n- drive: (not resolved)\n"),
                         [{"short": "Counties Manukau Sport", "folder_name": "Counties Manukau Sport", "aliases": ["CMS"]}])
        names = {"Mushroom Creative House Pty Ltd"}
        self.assertEqual(cr.resolve_folder("Mushroom CH", names, cr.folder_lookup(names, rows, {})), "Mushroom Creative House Pty Ltd")


class BoardRowsForNoCorpus(unittest.TestCase):
    def test_a_client_with_no_corpus_is_judged_from_the_board_rows_by_folder_id(self):
        nc = {"NewCo": {"folder_id": "F1", "files": 9, "newest": "2026-10-01"}}
        brows = {"F1": {"stage": "client", "client_id": None, "short": "NewCo", "item_id": "77"}}
        self.assertEqual(cr.board_row("NewCo", nc["NewCo"], {}, brows), brows["F1"])
        plan = cr.seed_plan(nc, {}, ["CL-0100"], {}, 10, "2026-11-28", brows)
        self.assertEqual([(p["folder"], p["client_id"]) for p in plan], [("NewCo", "CL-0101")])

    def test_seeding_covers_only_the_clients_inside_tonights_cap(self):
        nc = {"Big": {"folder_id": "1", "files": 50, "newest": "2026-10-01"}, "Mid": {"folder_id": "2", "files": 40, "newest": "2026-10-01"},
              "Low": {"folder_id": "3", "files": 5, "newest": "2026-10-01"}}
        st = {"Big": {"stage": "client", "client_id": "CL-0001", "short": "Big", "item_id": "a"}, "Mid": {"stage": "client", "client_id": None, "short": "Mid", "item_id": "b"},
              "Low": {"stage": "client", "client_id": None, "short": "Low", "item_id": "c"}}
        plan = cr.seed_plan(nc, st, ["CL-0001"], {}, 2, "2026-11-28")          # Big (has ID) and Mid fill the cap; Low waits
        self.assertEqual([p["folder"] for p in plan], ["Mid"])


class StageFromRegistry(unittest.TestCase):
    def test_a_registered_client_with_a_blank_board_status_keeps_its_registry_stage(self):
        import json, tempfile, io, contextlib, argparse
        tmp = tempfile.mkdtemp()
        os.environ["STAGE_ROOT"] = tmp
        os.environ["CORPUS_WORK"] = os.path.join(tmp, "work")
        os.makedirs(os.path.join(tmp, cr.OPS, "corpus-runs"))
        json.dump({"clients": [{"folder_name": "Alberts", "stage": "client"}, {"folder_name": "NoId", "stage": "client"}]}, open(os.path.join(tmp, cr.OPS, "corpus-index.json"), "w"))
        cr.save("candidates.json", {"Alberts": {"folder_id": "FA"}, "NoId": {"folder_id": "FN"}})
        items = [{"id": 1, "name": "Alberts", "column_values": {"status": {"index": 5}, "text_mm8pgmx8": "CL-0001", "link_mm2vffc2": "https://drive.google.com/drive/folders/FA"}},
                 {"id": 2, "name": "NoId", "column_values": {"status": {"index": 5}, "link_mm2vffc2": "https://drive.google.com/drive/folders/FN"}}]
        f = os.path.join(tmp, "b.json")
        json.dump({"items": items}, open(f, "w"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                cr.cmd_stages(argparse.Namespace(file=f))
            except SystemExit:
                pass
        st = cr.load("stages.json")
        self.assertEqual(st["Alberts"]["stage"], "client")      # had a Client ID: registry stage kept
        self.assertEqual(st["NoId"]["stage"], "")               # no Client ID: still a missing fact


class RequestsOrder(unittest.TestCase):
    def test_older_requests_build_first_and_built_ones_never_crowd_the_list(self):
        import json, tempfile, io, contextlib, argparse
        tmp = tempfile.mkdtemp()
        os.environ["STAGE_ROOT"] = tmp
        os.environ["CORPUS_WORK"] = os.path.join(tmp, "work")
        os.makedirs(os.path.join(tmp, cr.OPS, "corpus-requests"))
        cr.save("run.json", {"run_id": "r", "since": "2026-01-01T00:00:00Z", "mode": "nightly", "started": 0, "interrupted": []})
        d = os.path.join(tmp, cr.OPS, "corpus-requests")
        for n in range(25):           # 25 already built, which used to fill the 20-file window
            json.dump({"client": "Done%d" % n, "status": "built"}, open(os.path.join(d, "build-request-a%02d.json" % n), "w"))
        json.dump({"client": "Zed", "status": "requested"}, open(os.path.join(d, "build-request-zed.json"), "w"))
        json.dump({"client": "Alpha", "status": "requested"}, open(os.path.join(d, "build-request-alpha.json"), "w"))
        os.utime(os.path.join(d, "build-request-zed.json"), (1, 1))          # filed earlier: builds first
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                cr.cmd_requests(argparse.Namespace())
            except SystemExit:
                pass
        r = json.loads(buf.getvalue())
        self.assertEqual([x["client"] for x in r["requested"]], ["Zed", "Alpha"])

if __name__ == "__main__":
    unittest.main()
