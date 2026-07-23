from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

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


def built_in_cases():
    return [
        (
            "single_long_url_item",
            [f"資料はこちらです: {LONG_URL} を確認"],
        ),
        (
            "split_url_across_nvda_items",
            [
                "資料はこちらです: "
                "https://docs.google.com/spreadsheets/d/"
                "1r-HSlVQnmu6NN6JQitGn6yQccLPoDh8kKJB-N_FTQ7o",
                "/edit?gid=1952100019#gid=1952100019 を確認",
            ],
        ),
    ]


def print_case(name, input_sequence) -> None:
    result = run_uml_pipeline(input_sequence)
    print(f"Case: {name}")
    print("Input items:")
    for index, item in enumerate(input_sequence, start=1):
        print(f"{index}: {item!r}")

    modseq_texts = sequence_text_items(result["modified"])
    print("UML modseq text items:")
    for index, item in enumerate(modseq_texts, start=1):
        print(f"{index}: {item!r}")

    print("Underlying synth chunks:")
    for index, entry in enumerate(result["captured"], start=1):
        print(f"{index}: synth={entry['synth']}; text={entry['text']!r}")

    modseq_dangling = leading_url_continuations(modseq_texts)
    synth_dangling = continuation_chunks(result["captured"])
    print(f"Standalone URL continuation in modseq: {'yes' if modseq_dangling else 'no'}")
    for index, item in enumerate(modseq_dangling, start=1):
        print(f"Continuation {index}: {item!r}")
    print(f"Standalone URL continuation in synth chunks: {'yes' if synth_dangling else 'no'}")
    for index, item in enumerate(synth_dangling, start=1):
        print(f"Synth continuation {index}: {item!r}")
    print()


def main(argv) -> int:
    if argv:
        print_case("argv", [" ".join(argv)])
        return 0

    for name, input_sequence in built_in_cases():
        print_case(name, input_sequence)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
