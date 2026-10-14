"""Skills: what each one asks the model, as parts of a prompt.

Needs the app's own requirements; without them the whole module is skipped.
"""

from __future__ import annotations

import unittest

try:
    from app.skills import SKILLS, build_prompt, prompt_parts
except ImportError as exc:  # pragma: no cover - only without the requirements
    raise unittest.SkipTest(f"app requirements are not installed: {exc}")

RESULTS = ["wikipedia: Python is a language.", "wikipedia: Rust is a language."]
FAILED = ["weather: No place called 'Atlantis' was found."]


class PromptPartTests(unittest.TestCase):
    def test_joined_as_a_prompt_joins_them_the_parts_are_the_prompt_to_the_letter(self) -> None:
        for skill in SKILLS.values():
            for failures in (None, FAILED):
                with self.subTest(skill=skill.id, failures=failures):
                    parts = prompt_parts(skill, "  Python vs Rust ", RESULTS, failures)
                    self.assertEqual(
                        "\n\n".join(p.content for p in parts),
                        build_prompt(skill, "  Python vs Rust ", RESULTS, failures),
                    )
                    self.assertEqual({p.role for p in parts}, {"user"})

    def test_what_was_found_is_one_part_and_says_where_it_came_from(self) -> None:
        parts = prompt_parts(SKILLS["compare"], "Python vs Rust", RESULTS, tools=("wikipedia",))
        self.assertEqual([p.kind for p in parts], ["instruction", "data"])
        self.assertEqual(parts[1].tools, ("wikipedia",))
        self.assertIn("Python is a language.", parts[1].content)

    def test_a_piece_that_is_only_the_users_text_is_the_question(self) -> None:
        parts = prompt_parts(SKILLS["explain_code"], "print(1)", [])
        self.assertEqual([(p.kind, p.content) for p in parts][-1], ("question", "print(1)"))

    def test_a_claim_quoted_in_an_instruction_stays_an_instruction(self) -> None:
        parts = prompt_parts(SKILLS["fact_check"], "The sky is green", RESULTS)
        self.assertEqual([p.kind for p in parts], ["instruction", "data", "instruction"])


if __name__ == "__main__":
    unittest.main()
