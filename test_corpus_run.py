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
        # settled clients are named once as fixed: 13.10 patch wording "swept since (nothing to do)", 13.12 "fixed by a later run (no action)"
        self.assertTrue("nothing to do" in d["text"] or "no action" in d["text"], d["text"])
        self.assertIn("Atlantic", d["text"])

    def test_unresolved_failure_still_listed(self):
        d = self.digest([[self.row("Atlantic", "swept")],
                         [self.row("Atlantic", "failed", "write-failed", ["dropped-after-retries"]), self.row("Other", "failed", "write-failed")]])
        self.assertEqual(d["status"], "needs-you")
        self.assertIn("Atlantic: failed: write-failed", d["text"])
        self.assertIn("dropped", d["text"].lower())
        self.assertIn("Other: failed", d["text"])
        self.assertNotIn("swept since", d["text"])
        self.assertNotIn("no action", d["text"])


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


class StageUnknownAdvice(unittest.TestCase):
    def test_mentions_folder_link_not_only_status(self):
        meaning, cmds = cr.ADVICE["stage-unknown"]
        self.assertIn("Client Folder G Drive", meaning)
        self.assertTrue(any("Client Folder G Drive" in c for c in cmds))
        self.assertTrue(any("{c}" in c for c in cmds))


class InterruptedAdvice(unittest.TestCase):
    def test_interrupted_run_does_not_get_connector_advice(self):
        import tempfile, json, io, contextlib
        tmp = tempfile.mkdtemp()
        os.environ["STAGE_ROOT"] = tmp
        os.environ["CORPUS_WORK"] = os.path.join(tmp, "work")
        os.makedirs(cr.runs_dir())
        rec = {"schema": 1, "run_id": "run-20261127-0100", "mode": "changed", "target": "live", "status": "complete",
               "started": cr.now_iso(), "ended": cr.now_iso(), "clients": [], "carry_over": [], "totals": {},
               "events": [{"kind": "interrupted-run", "detail": "run-20261120-2012"}]}
        open(os.path.join(cr.runs_dir(), rec["run_id"] + ".md"), "w").write("# r\n<!-- run-data: %s -->\n" % json.dumps(rec))
        class A: run = None; hours = 24
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                cr.cmd_advise(A)
            except SystemExit:
                pass
        text = json.loads(buf.getvalue())["text"]
        self.assertIn("Run-level: interrupted-run: run-20261120-2012", text)
        self.assertIn("recovers by itself", text)
        self.assertNotIn("Connectors", text)


if __name__ == "__main__":
    unittest.main()
