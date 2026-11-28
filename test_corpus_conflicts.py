#!/usr/bin/env python3
"""Unit tests for corpus_conflicts.py, using the real cases that prompted it. Run: python3 test_corpus_conflicts.py"""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import corpus_conflicts as cc

def raw(id_, name, status, short=None, cid=None, fid=None, created="2026-11-17T11:41:09Z"):
    return {"id": id_, "name": name, "url": "https://x.monday.com/pulses/%s" % id_, "created_at": created,
            "column_values": {"status": status, "text_mm8pevdf": short, "text_mm8pgmx8": cid,
                              "link_mm2vffc2": ("%s - https://drive.google.com/drive/folders/%s" % (name, fid)) if fid else None}}

SERKO = [raw("2588114840", "Serko", "Client", "Serko", "CL-0071", "1NdnFvx", "2026-02-12T15:57:13Z"),
         raw("2909492020", "Serko Ltd.", "Customer", None, None, "1G9kaFT")]
FACTS = {"1NdnFvx": {"files": 44, "subfolders": 8, "has_kb": True, "newest": "2026-11-04"},
         "1G9kaFT": {"files": 0, "subfolders": 4, "has_kb": False, "newest": "2026-11-16"}}


class Names(unittest.TestCase):
    def test_legal_suffixes_do_not_separate_one_client(self):
        self.assertEqual(cc.name_key("Serko Ltd."), cc.name_key("Serko"))
        self.assertEqual(cc.name_key("Mushroom Creative House Pty Ltd"), "mushroomcreativehouse")

    def test_a_different_trading_name_is_not_a_duplicate(self):
        self.assertNotEqual(cc.name_key("Tegel Foods Ltd"), cc.name_key("Tegel"))


class Detect(unittest.TestCase):
    def test_serko_pair_is_found_and_a_lone_item_is_not(self):
        items = cc.parse_items(SERKO + [raw("1", "Fonterra", "Client")])
        g = cc.duplicate_accounts(items)
        self.assertEqual(len(g), 1)
        self.assertEqual({i["name"] for i in g[0]}, {"Serko", "Serko Ltd."})

    def test_customer_is_a_known_status_not_a_conflict(self):
        items = cc.parse_items(SERKO + [raw("3", "Arnott's", "Customer", None, None, "1P")])
        self.assertEqual(cc.unknown_statuses(items), {})
        self.assertEqual(cc.KNOWN_STATUS["Customer"], "prospect")      # a won deal awaiting onboarding: not delivering yet

    def test_an_unknown_status_is_named_with_its_items(self):
        items = cc.parse_items(SERKO + [raw("3", "Arnott's", "Dormant", None, None, "1P"), raw("4", "TMR", "Dormant", None, None, "1Q")])
        u = cc.unknown_statuses(items)
        self.assertEqual(list(u), ["Dormant"])
        self.assertEqual(len(u["Dormant"]), 2)

    def test_a_status_map_entry_silences_it(self):
        items = cc.parse_items([raw("3", "Arnott's", "Dormant", None, None, "1P")])
        self.assertEqual(cc.unknown_statuses(items, {"Dormant": "past-client"}), {})

    def test_a_blank_status_label_is_an_unset_status_not_an_unknown_one(self):
        # the export shows the board's blank default label as a dict with an index and no text
        items = cc.parse_items([raw("4", "Mitre 10", {"changed_at": "x", "index": 5})])
        self.assertEqual(cc.unknown_statuses(items), {})
        self.assertIsNone(items[0]["status"])

    def test_default_named_new_items_are_not_a_duplicate_client(self):
        items = cc.parse_items([raw("5", "New item", None, None, None, "1A"), raw("6", "New item", None, None, None, "1A")])
        self.assertEqual(cc.duplicate_accounts(items), [])


class Cards(unittest.TestCase):
    def serko_card(self):
        return cc.card_duplicate_accounts(cc.duplicate_accounts(cc.parse_items(SERKO))[0], FACTS, {})

    def test_the_card_names_both_items_with_links_and_the_difference(self):
        c = self.serko_card()
        text = "\n".join(c["facts"])
        self.assertIn("https://x.monday.com/pulses/2588114840", text)
        self.assertIn("https://x.monday.com/pulses/2909492020", text)
        self.assertIn("registered (Client ID CL-0071", text)
        self.assertIn("not registered (no Client ID", text)
        self.assertIn("status Customer (a won deal awaiting onboarding)", text)
        self.assertIn("https://drive.google.com/drive/folders/1G9kaFT", text)
        self.assertIn("empty template folder", text)
        self.assertIn("44 files in the folder tree", text)
        self.assertEqual(c["client"], "Serko")

    def test_the_card_says_what_it_blocks_asks_the_question_and_gives_steps_for_each_answer(self):
        c = self.serko_card()
        self.assertIn("Nothing is lost", " ".join(c["blocks"]))
        self.assertTrue(c["question"].endswith("same organisation?"))
        self.assertEqual([a["if"] for a in c["answers"]], ["they are the same organisation", "they are different organisations"])
        self.assertIn("archive", " ".join(c["answers"][0]["do"]))
        self.assertIn("won deal awaiting onboarding", " ".join(c["answers"][0]["do"]))
        self.assertTrue(c["after"])

    def test_duplicate_folders_card_says_which_one_the_board_links(self):
        items = cc.parse_items([raw("9", "Mossop", "Client", "Mossop", "CL-1", "NEW")])
        by_folder = {i["folder_id"]: i for i in items}
        c = cc.card_duplicate_folders("Mossop", [{"id": "OLD", "created": "2025-05-17T00:00:00Z", "facts": {"files": 6, "subfolders": 4, "has_kb": False, "newest": "2025-07-09"}},
                                                 {"id": "NEW", "created": "2026-05-02T07:19:53Z", "facts": {"files": 0, "subfolders": 4, "has_kb": False, "newest": "2026-05-02"}}], by_folder)
        text = "\n".join(c["facts"])
        self.assertIn("the Accounts board links this one", text)
        self.assertIn("no Accounts item links it", text)
        self.assertIn("created 2025-05-17", text)
        self.assertIn("the folder the board links (B)", c["answers"][0]["if"])


    def test_an_empty_linked_folder_beside_a_full_one_is_called_out(self):
        items = cc.parse_items([raw("9", "Mossop", "Client", "Mossop", "CL-1", "NEW")])
        c = cc.card_duplicate_folders("Mossop", [{"id": "OLD", "created": "2025-05-17T00:00:00Z", "facts": {"files": 7, "subfolders": 4, "has_kb": False, "newest": "2025-07-09"}},
                                                 {"id": "NEW", "created": "2026-05-02T00:00:00Z", "facts": {"files": 0, "subfolders": 4, "has_kb": False, "newest": None}}],
                                {i["folder_id"]: i for i in items})
        self.assertIn("Observation: the folder the board links holds no files, while A holds 7", "\n".join(c["facts"]))

    def test_unknown_status_card_asks_what_it_means_and_says_how_to_answer(self):
        c = cc.card_unknown_status("Customer", cc.parse_items(SERKO)[1:])
        self.assertIn("What does the status \"Customer\" mean", c["question"])
        self.assertIn("status-map.json", " ".join(" ".join(a["do"]) for a in c["answers"]))
        self.assertIn("not swept", " ".join(c["blocks"]))


class State(unittest.TestCase):
    def test_first_seen_is_kept_and_a_fixed_conflict_is_gone(self):
        old = [{"id": "a", "first_seen": "2026-11-20"}, {"id": "gone", "first_seen": "2026-11-01"}]
        new = cc.merge_first_seen(old, [{"id": "a"}, {"id": "b"}], "2026-11-28")
        self.assertEqual([(c["id"], c["first_seen"]) for c in new], [("a", "2026-11-20"), ("b", "2026-11-28")])

    def test_render_has_every_part_a_receiver_needs(self):
        c = cc.card_duplicate_accounts(cc.duplicate_accounts(cc.parse_items(SERKO))[0], FACTS, {})
        c["first_seen"] = "2026-11-28"
        text = "\n".join(cc.render([c]))
        for needle in ("CONFLICT: Serko", "What conflicts:", "The facts:", "What it blocks:", "What we need from you:", "If they are the same organisation", "After you act:", "open since 2026-11-28"):
            self.assertIn(needle, text)

    def test_folder_facts_count_the_whole_tree_not_just_the_top_level(self):
        f = cc.folder_facts([{"Path": "Commercials", "IsDir": True}, {"Path": "Commercials/proposal.pdf", "IsDir": False, "ModTime": "2025-07-09T01:00:00Z"},
                             {"Path": "Commercials/deep", "IsDir": True}, {"Path": "Commercials/deep/a.docx", "IsDir": False, "ModTime": "2025-05-23T00:00:00Z"}])
        self.assertEqual(f, {"files": 2, "subfolders": 1, "has_kb": False, "newest": "2025-07-09"})
        self.assertIn("2 files in the folder tree", cc.describe_folder(f))

    def test_a_folder_with_files_only_in_subfolders_is_not_called_empty(self):
        f = cc.folder_facts([{"Path": "Resources", "IsDir": True}, {"Path": "Resources/x.pdf", "IsDir": False, "ModTime": "2025-07-09T00:00:00Z"}])
        self.assertNotIn("empty", cc.describe_folder(f))

    def test_a_corpus_is_recognised_by_its_folder(self):
        f = cc.folder_facts([{"Path": cc.KB, "IsDir": True}, {"Path": cc.KB + "/now.md", "IsDir": False, "ModTime": "2026-11-05T00:00:00Z"}])
        self.assertTrue(f["has_kb"])


if __name__ == "__main__":
    unittest.main()
