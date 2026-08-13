from __future__ import annotations

import unittest

from tools.uml_speech_harness import load_uml_module, run_uml_pipeline


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
    def test_conflicting_number_languages_use_japanese_in_one_sentence(self):
        result = run_uml_pipeline(["83% potベット: 23.1%"])

        self.assertIn(("ja", "八十三% "), language_chunks(result))
        self.assertIn(("ja", "二十三点一%"), language_chunks(result))

    def test_number_language_is_unified_across_index_commands(self):
        module, _ = load_uml_module()
        boundary = module.IndexCommand(831)
        modified = module.modseq(
            ["83% pot", boundary, "ベット: 23.1%"],
            "ja",
            "word",
        )
        result = {"module": module, "modified": modified}

        self.assertIn(("ja", "八十三% "), language_chunks(result))
        self.assertIn(("ja", "二十三点一%"), language_chunks(result))
        self.assertIn(boundary, modified)

    def test_japanese_in_later_sentence_unifies_numbers_on_same_line(self):
        result = run_uml_pipeline(["CPU 80%. 温度は35度。"])

        self.assertEqual(
            [
                ("en", "CPU "),
                ("ja", "八十%"),
                ("en", ". "),
                ("ja", "温度は三十五度。"),
            ],
            language_chunks(result),
        )

    def test_poker_percentages_are_japanese_across_full_stop_on_same_line(self):
        result = run_uml_pipeline(
            ["- Q92: CB 17.96%。33% potと50% potを約9%ずつ"]
        )

        self.assertEqual(
            [
                ("en", "- Q92: CB "),
                ("ja", "十七点九六%。三十三% "),
                ("en", "pot"),
                ("ja", "と五十% "),
                ("en", "pot"),
                ("ja", "を約九%ずつ"),
            ],
            language_chunks(result),
        )

    def test_number_language_does_not_cross_line_boundaries(self):
        result = run_uml_pipeline(["CPU 80%\n温度は35度"])

        self.assertEqual(
            [
                ("en", "CPU 80%\n"),
                ("ja", "温度は三十五度"),
            ],
            language_chunks(result),
        )

    def test_ascii_identifier_number_is_not_unified(self):
        result = run_uml_pipeline(["GPT-5の成功率: 23.1%"])

        self.assertIn(("en", "GPT-5"), language_chunks(result))
        self.assertIn(("ja", "二十三点一%"), language_chunks(result))

    def test_email_number_is_not_unified(self):
        result = run_uml_pipeline(
            ["2026@example.comの成功率: 23.1%"]
        )

        self.assertIn(
            ("en", "2026@example.com"),
            language_chunks(result),
        )
        self.assertIn(("ja", "二十三点一%"), language_chunks(result))

    def test_file_path_number_is_not_unified(self):
        result = run_uml_pipeline(
            ["docs/2026/report.txtの成功率: 23.1%"]
        )

        self.assertIn(
            ("en", "docs/2026/report.txt"),
            language_chunks(result),
        )
        self.assertIn(("ja", "二十三点一%"), language_chunks(result))

    def test_decimal_uses_japanese_left_context_as_one_token(self):
        result = run_uml_pipeline(
            ["SBがfoldしすぎる: BTNが15.5bbを取りすぎる"]
        )

        chunks = language_chunks(result)

        self.assertIn(("ja", "が十五点五"), chunks)
        self.assertIn(("en", "bb"), chunks)
        self.assertIn(("ja", "を取りすぎる"), chunks)

    def test_left_context_ignores_punctuation_and_spaces(self):
        result = run_uml_pipeline(["値: 15.5です"])

        self.assertIn(("ja", "十五点五です"), language_chunks(result))

    def test_japanese_calculation_uses_place_values_and_operator_words(self):
        result = run_uml_pipeline(["1,958 × 6 = 11,748円"])

        self.assertEqual(
            [("ja", "千九百五十八 かける 六 は 一万千七百四十八円")],
            language_chunks(result),
        )

    def test_japanese_calculation_reads_other_operators_as_words(self):
        result = run_uml_pipeline(["12 ÷ 3 + 2 - 1 = 5です"])

        self.assertEqual(
            [("ja", "十二 わる 三 たす 二 ひく 一 は 五です")],
            language_chunks(result),
        )

    def test_identifier_like_numbers_are_not_converted_to_quantities(self):
        cases = [
            ("注文ID: 001958です", "001958"),
            ("注文番号は1958です", "1958"),
            ("GPT-5の番号です", "5"),
            ("docs/2026/report.txtです", "2026"),
            ("2026@example.comです", "2026"),
            ("電話は090-1234-5678です", "090-1234-5678"),
            ("郵便番号は123-4567です", "123-4567"),
        ]

        for text, expected_number in cases:
            with self.subTest(text=text):
                spoken_text = "".join(
                    chunk for _, chunk in language_chunks(run_uml_pipeline([text]))
                )
                self.assertIn(expected_number, spoken_text)

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
                ("ja", "七十二件"),
            ],
            language_chunks(result),
        )

    def test_japanese_counter_numbers_keep_place_value_when_normalized(self):
        cases = [
            ("pending: 0件", "零件"),
            ("pending: 10件", "十件"),
            ("pending: 1,200件", "千二百件"),
            ("pending: 10,000件", "一万件"),
        ]

        for text, expected in cases:
            with self.subTest(text=text):
                result = run_uml_pipeline([text], last_lang="en")
                self.assertIn(("ja", expected), language_chunks(result))

    def test_japanese_counter_suffix_is_used_across_sequence_items(self):
        module, _ = load_uml_module()
        boundary = module.IndexCommand(72)
        modified = module.modseq(
            [
                "72",
                boundary,
                " 件のpending案件に対応するSlackスレッドをすべて確認しました。",
            ],
            "en",
            "word",
        )
        result = {"module": module, "modified": modified}

        self.assertEqual(
            [
                ("ja", "七十二"),
                ("ja", " 件の"),
                ("en", "pending"),
                ("ja", "案件に対応する"),
                ("en", "Slack"),
                ("ja", "スレッドをすべて確認しました。"),
            ],
            language_chunks(result),
        )
        self.assertIn(boundary, modified)

    def test_non_counter_next_item_does_not_override_english_number(self):
        module, _ = load_uml_module()
        modified = module.modseq(
            ["Python 3.12", module.IndexCommand(312), "を使う"],
            "en",
            "word",
        )
        result = {"module": module, "modified": modified}

        self.assertIn(("en", "Python 3.12"), language_chunks(result))
        self.assertIn(("ja", "を使う"), language_chunks(result))

    def test_right_context_is_used_when_number_starts_text_item(self):
        result = run_uml_pipeline(["15.5円です"], last_lang="en")

        self.assertEqual(
            [("ja", "十五点五円です")],
            language_chunks(result),
        )

    def test_last_language_is_used_without_surrounding_language(self):
        result = run_uml_pipeline(["15.5"], last_lang="en")

        self.assertEqual([("en", "15.5")], language_chunks(result))

    def test_japanese_wave_dash_range_uses_one_japanese_voice(self):
        result = run_uml_pipeline(["AJ: 合計CB 56〜67%"])

        self.assertEqual(
            [
                ("en", "AJ: "),
                ("ja", "合計"),
                ("en", "CB "),
                ("ja", "五十六〜六十七%"),
            ],
            language_chunks(result),
        )

    def test_fullwidth_wave_dash_range_uses_one_japanese_voice(self):
        result = run_uml_pipeline(["CB 56～67％"])

        self.assertEqual(
            [("en", "CB "), ("ja", "五十六～六十七％")],
            language_chunks(result),
        )

    def test_ascii_hyphen_range_keeps_english_context(self):
        result = run_uml_pipeline(["CPU 56-67%"])

        self.assertEqual(
            [("en", "CPU 56-67%")],
            language_chunks(result),
        )

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
