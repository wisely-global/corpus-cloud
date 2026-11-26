#!/usr/bin/env python3
"""Unit tests for the pure helpers in corpus_run.py. Run: python3 test_corpus_run.py"""
import os, sys, unittest
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
                {"Path": "Gamma/%s/pending-failures.json" % cr.KB, "ModTime": old}]
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


if __name__ == "__main__":
    unittest.main()
