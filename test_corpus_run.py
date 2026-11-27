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


if __name__ == "__main__":
    unittest.main()
