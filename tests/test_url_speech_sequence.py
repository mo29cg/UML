from __future__ import annotations

import unittest

from tools.uml_speech_harness import (
    continuation_chunks,
    leading_url_continuations,
    run_uml_pipeline,
    sequence_text_items,
)


LONG_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1r-HSlVQnmu6NN6JQitGn6yQccLPoDh8kKJB-N_FTQ7o"
    "/edit?gid=1952100019#gid=1952100019"
)


class UrlSpeechSequenceTest(unittest.TestCase):
    def test_single_long_url_stays_one_speech_text_item(self):
        result = run_uml_pipeline([f"資料はこちらです: {LONG_URL} を確認"])

        modseq_texts = sequence_text_items(result["modified"])
        synth_texts = [entry["text"] for entry in result["captured"]]

        self.assertIn(LONG_URL, modseq_texts)
        self.assertTrue(any(LONG_URL in item for item in synth_texts))
        self.assertEqual([], leading_url_continuations(modseq_texts))
        self.assertEqual([], continuation_chunks(result["captured"]))

    def test_split_long_url_does_not_leave_tail_as_readable_chunk(self):
        result = run_uml_pipeline(
            [
                "資料はこちらです: "
                "https://docs.google.com/spreadsheets/d/"
                "1r-HSlVQnmu6NN6JQitGn6yQccLPoDh8kKJB-N_FTQ7o",
                "/edit?gid=1952100019#gid=1952100019 を確認",
            ]
        )

        self.assertEqual(
            [],
            leading_url_continuations(sequence_text_items(result["modified"])),
        )
        self.assertEqual([], continuation_chunks(result["captured"]))

    def test_url_followed_by_bullet_is_not_merged(self):
        url = "https://example.com"
        result = run_uml_pipeline([url, "- 次の項目"])

        modseq_texts = sequence_text_items(result["modified"])

        self.assertIn(url, modseq_texts)
        self.assertNotIn(url + "- 次の項目", modseq_texts)


if __name__ == "__main__":
    unittest.main()
