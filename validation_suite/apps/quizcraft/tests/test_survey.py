"""Unit tests for the survey document rules (src/survey.py), over the seed. No server needed."""

import copy
import json
import sys
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "src"))

from survey import Invalid, canonical, check_document, numbers  # noqa: E402

SEED = json.loads((APP / "seed" / "survey.json").read_text(encoding="utf-8"))["document"]


def seed() -> dict:
    return copy.deepcopy(SEED)


class SeedTest(unittest.TestCase):
    def test_seed_is_a_valid_document_of_six_questions_two_required(self):
        document = check_document(seed())
        self.assertEqual(numbers(document), (6, 2))
        self.assertEqual([q["id"] for q in document["questions"]], ["q1", "q2", "q3", "q4", "q5", "q6"])
        self.assertEqual(document["questions"][1]["rule"], {"when": "no", "target": "q4"})

    def test_canonical_form_ignores_key_order(self):
        reordered = {"questions": seed()["questions"], "title": SEED["title"]}
        self.assertEqual(canonical(check_document(reordered)), canonical(check_document(seed())))


class RefusalTest(unittest.TestCase):
    def refused(self, document) -> str:
        with self.assertRaises(Invalid) as caught:
            check_document(document)
        return caught.exception.key

    def test_titles(self):
        empty = seed()
        empty["questions"][2]["title"] = "   "
        self.assertEqual(self.refused(empty), "error.question_title_missing")
        long = seed()
        long["questions"][0]["title"] = "A" * 2048
        self.assertEqual(self.refused(long), "error.question_title_long")

    def test_at_most_nine_questions(self):
        many = seed()
        for n in range(7, 11):
            many["questions"].append({"id": f"q{n}", "kind": "text", "title": "Extra", "required": False,
                                      "options": [], "rule": None})
        self.assertEqual(self.refused(many), "error.count")

    def test_rules_point_forward_from_yes_no_questions_only(self):
        backwards = seed()
        backwards["questions"][1]["rule"] = {"when": "no", "target": "q1"}
        self.assertEqual(self.refused(backwards), "error.rule")
        on_text = seed()
        on_text["questions"][4]["rule"] = {"when": "no", "target": "q6"}
        self.assertEqual(self.refused(on_text), "error.rule")
        on_yes = seed()
        on_yes["questions"][1]["rule"] = {"when": "yes", "target": "q4"}
        self.assertEqual(self.refused(on_yes), "error.rule")

    def test_duplicate_ids_and_unknown_kinds(self):
        twice = seed()
        twice["questions"][5]["id"] = "q5"
        self.assertEqual(self.refused(twice), "error.document")
        odd = seed()
        odd["questions"][0]["kind"] = "slider"
        self.assertEqual(self.refused(odd), "error.document")

    def test_choice_options(self):
        single = seed()
        single["questions"][0]["options"] = single["questions"][0]["options"][:1]
        self.assertEqual(self.refused(single), "error.options")


if __name__ == "__main__":
    unittest.main()
