#!/usr/bin/env python3
"""Unit tests for corpus_tidy.py. No Drive: a fake stands in for rclone. Run: python3 test_corpus_tidy.py"""
import json, os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import corpus_tidy as ct

DOC = "application/vnd.google-apps.document"


def F(id_, path, mime="text/markdown", md5=None, created="2026-01-01T00:00:00Z", size=10):
    d, _, name = path.rpartition("/")
    return {"id": id_, "name": name, "path": path, "dir": d, "parent": "P:" + d, "mime": mime, "size": size,
            "md5": md5 or ("m-" + id_), "created": created, "modified": created}


class Merge(unittest.TestCase):
    def test_union_without_collision(self):
        a = {"sources": [{"id": "s1", "status": "open"}, {"id": "s2"}], "last_updated": "2026-09-01"}
        b = {"sources": [{"id": "s2"}, {"id": "s3"}], "last_updated": "2026-10-01", "extra": 1}
        m, c = ct.merge_json(a, b)
        self.assertEqual(c, [])
        self.assertEqual([r["id"] for r in m["sources"]], ["s1", "s2", "s3"])
        self.assertEqual(m["last_updated"], "2026-10-01")      # newer wins, not a conflict
        self.assertEqual(m["extra"], 1)

    def test_collision_is_named(self):
        _, c = ct.merge_json({"sources": [{"id": "s1", "status": "open"}]}, {"sources": [{"id": "s1", "status": "closed"}]})
        self.assertEqual(len(c), 1)
        self.assertEqual(c[0]["path"], "$.sources[id=s1].status")

    def test_superset_text_and_json(self):
        self.assertEqual(ct.resolve_pair("x.md", "a\nb\nc\n", "a\nc\n")[0], "a")
        self.assertEqual(ct.resolve_pair("x.md", "a\n", "a\nb\n")[0], "b")
        self.assertEqual(ct.resolve_pair("x.md", "a\nb\n", "a\nc\n")[0], "conflict")
        self.assertEqual(ct.resolve_pair("v.json", '{"a":1,"b":2}', '{"a":1}')[0], "a")
        r = ct.resolve_pair("v.json", '{"a":1}', '{"b":2}')
        self.assertEqual(r[0], "merge")
        self.assertEqual(json.loads(r[1]), {"a": 1, "b": 2})
        self.assertEqual(ct.resolve_pair("v.json", '{"a":1}', '{"a":2}')[0], "conflict")


class Plan(unittest.TestCase):
    def setUp(self):
        self.text = {}
        self.read = lambda f: self.text[f["id"]]

    def plan(self, files):
        return ct.plan("Flo", files, self.read, "2026-11-28")

    def test_native_docs_leave_the_kb_tree(self):
        p = self.plan([F("d1abcdefgh", "archive/x.md (pre-tidy)", mime=DOC), F("keep", "now.md")])
        self.assertEqual([o["op"] for o in p["ops"]], ["move"])
        self.assertTrue(p["ops"][0]["dest"].startswith("Flo/Archived from KB 2026-11-28/archive/d1abcdef"))
        self.assertNotIn(ct.KB, p["ops"][0]["dest"])           # outside the KB, so the size check cannot see it

    def test_doc_copy_beside_text_copy_resolves_without_a_fork(self):
        p = self.plan([F("doc00001", "now 2026-09-17.md", mime=DOC, md5=None), F("txt00001", "now 2026-09-17.md"), F("now", "now.md")])
        moved = [o["file"]["id"] for o in p["ops"] if o["op"] == "move"]
        self.assertIn("doc00001", moved)
        self.assertEqual(p["flags"], [])

    def test_identical_duplicates_keep_the_oldest(self):
        p = self.plan([F("old00001", "h.md", md5="same", created="2026-01-01T00:00:00Z"),
                       F("new00001", "h.md", md5="same", created="2026-02-01T00:00:00Z")])
        self.assertEqual([o["file"]["id"] for o in p["ops"]], ["new00001"])

    def test_fork_with_a_superset_keeps_the_superset(self):
        self.text = {"a": "x\ny\nz\n", "b": "x\ny\n"}
        p = self.plan([F("a", "h.md", created="2026-01-01T00:00:00Z"), F("b", "h.md", created="2026-02-01T00:00:00Z")])
        self.assertEqual([o["file"]["id"] for o in p["ops"]], ["b"])

    def test_real_conflict_moves_nothing_and_asks_the_exact_question(self):
        self.text = {"a": '{"sources":[{"id":"s1","status":"open"}]}', "b": '{"sources":[{"id":"s1","status":"closed"}]}'}
        p = self.plan([F("a", "m.json", created="2026-01-01T00:00:00Z"), F("b", "m.json", created="2026-02-01T00:00:00Z")])
        self.assertEqual(p["ops"], [])
        self.assertIn("$.sources[id=s1].status", p["flags"][0]["question"])

    def test_clean_json_merge_moves_both_originals_and_writes_one(self):
        self.text = {"a": '{"a":1}', "b": '{"b":2}'}
        p = self.plan([F("a", "m.json", created="2026-01-01T00:00:00Z"), F("b", "m.json", created="2026-02-01T00:00:00Z")])
        self.assertEqual([o["op"] for o in p["ops"]], ["move", "move", "put"])
        self.assertEqual(json.loads(p["ops"][2]["content"]), {"a": 1, "b": 2})

    def test_core_files_are_never_collapsed(self):
        p = self.plan([F("a", "current-state.md"), F("b", "current-state.md")])
        self.assertEqual(p["ops"], [])
        self.assertEqual(p["flags"][0]["kind"], "core-duplicate")

    def test_dated_history_moves_to_archive_only_when_canonical_is_live_and_newer(self):
        self.text = {"cs": "Version: 10\n", "h5": "Version: 5\n", "h11": "Version: 11\n"}
        files = [F("cs", "current-state.md"), F("h5", "current-state 2026-09-17 v5.md"), F("h11", "current-state 2026-10-02 v11.md"),
                 F("nw", "now.md"), F("n1", "now 2026-10-10.md"), F("m4", "source-manifest-v4.json"), F("sm", "source-manifest.json"),
                 F("sl", "session-log-2026-09-17.md")]
        p = self.plan(files)
        moved = {o["file"]["id"]: o["dest"] for o in p["ops"]}
        self.assertEqual(moved["h5"], "Flo/%s/archive" % ct.KB)
        self.assertEqual(moved["n1"], "Flo/%s/archive" % ct.KB)
        self.assertEqual(moved["m4"], "Flo/%s/archive" % ct.KB)
        self.assertEqual(moved["sl"], "Flo/%s/session-logs" % ct.KB)
        self.assertNotIn("h11", moved)                          # newer than the live file: a person's question
        self.assertEqual([f["kind"] for f in p["flags"]], ["history-newer"])

    def test_history_without_its_canonical_is_left_alone(self):
        p = self.plan([F("n1", "now 2026-10-10.md")])
        self.assertEqual(p["ops"], [])
        self.assertEqual(p["flags"][0]["kind"], "history-without-canonical")

    def test_files_in_subfolders_are_not_treated_as_root_history(self):
        p = self.plan([F("n1", "archive/now 2026-10-10.md"), F("nw", "now.md")])
        self.assertEqual(p["ops"], [])


class FakeDrive:
    def __init__(self, files, texts=None, fail=None):
        self.files, self.texts, self.fail, self.calls = files, texts or {}, fail or set(), []
        self.where = {f["id"]: f["parent"] for f in files}

    def kb_tree(self, client):
        return self.files

    def read(self, f):
        return self.texts[f["id"]]

    def still_there(self, f):
        return {"id": f["id"], "md5Checksum": f["md5"]} if self.where.get(f["id"]) == f["parent"] else None

    def move(self, f, dest):
        if f["id"] in self.fail:
            raise RuntimeError("boom")
        self.calls.append(("move", f["id"], dest))
        self.where[f["id"]] = "DEST:" + dest

    def lands(self, f, dest):
        return self.where.get(f["id"]) == "DEST:" + dest

    def put(self, path, content):
        self.calls.append(("put", path))
        return True


class Apply(unittest.TestCase):
    def files(self):
        return [F("doc00001", "x.md", mime=DOC), F("now", "now.md")]

    def test_dry_run_changes_nothing(self):
        d = FakeDrive(self.files())
        r = ct.run("Flo", d, "2026-11-28")
        self.assertFalse(r["applied"])
        self.assertEqual(d.calls, [])
        self.assertEqual(r["planned"], 1)

    def test_apply_moves_and_verifies(self):
        d = FakeDrive(self.files())
        r = ct.run("Flo", d, "2026-11-28", do_apply=True)
        self.assertEqual(r["moved"], 1)
        self.assertEqual(r["problems"], [])

    def test_a_file_changed_since_the_plan_is_skipped(self):
        d = FakeDrive([F("a", "h.md", md5="same", created="1"), F("b", "h.md", md5="same", created="2")])
        orig = d.still_there
        d.still_there = lambda f: {"id": f["id"], "md5Checksum": "changed"} if f["id"] == "b" else orig(f)
        r = ct.run("Flo", d, "2026-11-28", do_apply=True)
        self.assertEqual(r["results"][0]["status"], "stale")
        self.assertEqual(d.calls, [])

    def test_a_failed_move_is_reported_not_hidden(self):
        d = FakeDrive(self.files(), fail={"doc00001"})
        r = ct.run("Flo", d, "2026-11-28", do_apply=True)
        self.assertEqual(r["results"][0]["status"], "failed")
        self.assertEqual(len(r["problems"]), 1)

    def test_merged_file_is_written_only_after_both_originals_moved(self):
        files = [F("a", "m.json", created="1"), F("b", "m.json", created="2")]
        d = FakeDrive(files, {"a": '{"a":1}', "b": '{"b":2}'}, fail={"b"})
        r = ct.run("Flo", d, "2026-11-28", do_apply=True)
        self.assertEqual([x["status"] for x in r["results"]], ["moved", "failed", "skipped"])
        self.assertFalse([c for c in d.calls if c[0] == "put"])

    def test_cap_defers_the_rest(self):
        files = [F("d%07d" % i, "x%d.md" % i, mime=DOC) for i in range(5)]
        r = ct.run("Flo", FakeDrive(files), "2026-11-28", do_apply=True, cap=2)
        self.assertEqual((r["moved"], r["deferred"]), (2, 3))


if __name__ == "__main__":
    unittest.main()
