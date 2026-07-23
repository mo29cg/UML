from __future__ import annotations

import unittest

from tools.uml_speech_harness import run_uml_pipeline


def language_chunks(result):
    module = result["module"]
    current_language = None
    chunks = []
    for item in result["modified"]:
        if isinstance(item, module.LangChangeCommand):
            current_language = item.lang
        elif isinstance(item, str):
            chunks.append((current_language, item))
    return chunks


class NumberLanguageSequenceTest(unittest.TestCase):
    def test_decimal_uses_japanese_left_context_as_one_token(self):
        result = run_uml_pipeline(
            ["SBがfoldしすぎる: BTNが15.5bbを取りすぎる"]
        )

        chunks = language_chunks(result)

        self.assertIn(("ja", "が15.5"), chunks)
        self.assertIn(("en", "bb"), chunks)
        self.assertIn(("ja", "を取りすぎる"), chunks)

    def test_left_context_ignores_punctuation_and_spaces(self):
        result = run_uml_pipeline(["値: 15.5です"])

        self.assertIn(("ja", "15.5です"), language_chunks(result))

    def test_english_left_context_remains_english(self):
        result = run_uml_pipeline(["Python 3.12を使う"])

        chunks = language_chunks(result)

        self.assertIn(("en", "Python 3.12"), chunks)
        self.assertIn(("ja", "を使う"), chunks)

    def test_japanese_counter_suffix_overrides_english_left_context(self):
        result = run_uml_pipeline(["DB上のpending: 72件"])

        self.assertEqual(
            [
                ("en", "DB"),
                ("ja", "上の"),
                ("en", "pending: "),
                ("ja", "72件"),
            ],
            language_chunks(result),
        )

    def test_right_context_is_used_when_number_starts_text_item(self):
        result = run_uml_pipeline(["15.5円です"], last_lang="en")

        self.assertEqual([("ja", "15.5円です")], language_chunks(result))

    def test_last_language_is_used_without_surrounding_language(self):
        result = run_uml_pipeline(["15.5"], last_lang="en")

        self.assertEqual([("en", "15.5")], language_chunks(result))

    def test_english_ordinal_after_japanese_stays_whole(self):
        result = run_uml_pipeline(["によって2nd pair以下"])

        self.assertEqual(
            [("ja", "によって"), ("en", "2nd pair"), ("ja", "以下")],
            language_chunks(result),
        )

    def test_english_ordinal_before_japanese_stays_whole(self):
        result = run_uml_pipeline(["第2nd以下"])

        self.assertEqual(
            [("ja", "第"), ("en", "2nd"), ("ja", "以下")],
            language_chunks(result),
        )

    def test_mixed_japanese_and_english_words_are_unchanged(self):
        result = run_uml_pipeline(["ご飯 means rice"])

        self.assertEqual(
            [("ja", "ご飯 "), ("en", "means rice")],
            language_chunks(result),
        )


if __name__ == "__main__":
    unittest.main()
