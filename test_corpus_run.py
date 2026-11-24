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


if __name__ == "__main__":
    unittest.main()
