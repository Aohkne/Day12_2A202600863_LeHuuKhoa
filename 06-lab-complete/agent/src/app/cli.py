from __future__ import annotations

import argparse
from pathlib import Path

from app.graph import ShoppingAssistant


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="VinShop Multi-Agent Shopping Assistant CLI.")
    parser.add_argument("--question", help="Run one question through the graph.")
    parser.add_argument("--test-file", default="data/test.json")
    parser.add_argument("--trace-file", default=None)
    parser.add_argument("--batch", action="store_true", help="Run all questions from test file.")
    parser.add_argument("--rebuild-index", action="store_true", help="Force rebuild Chroma index.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    assistant = ShoppingAssistant()

    if args.batch:
        test_file = Path(args.test_file)
        output_dir = assistant.settings.traces_dir
        print(f"Running batch test from {test_file} ...")
        summary = assistant.run_batch(
            test_file=test_file,
            output_dir=output_dir,
            rebuild_index=args.rebuild_index,
        )
        passed = summary["passed"]
        total = summary["total"]
        print(f"\nBatch complete: {passed}/{total} status matches")
        print(f"Summary saved to: {output_dir / 'summary.json'}")
        for r in summary["results"]:
            mark = "OK" if r.get("status_match") else "FAIL"
            err = r.get("error", "")
            if err:
                print(f"  [{mark}] {r['id']}: ERROR - {err}")
            else:
                print(f"  [{mark}] {r['id']} | expected={r['expected_status']} actual={r['actual_status']}")

    elif args.question:
        trace_file = Path(args.trace_file) if args.trace_file else None
        result = assistant.ask(
            args.question,
            trace_file=trace_file,
            rebuild_index=args.rebuild_index,
        )
        print("\n" + "=" * 60)
        print(result.get("final_answer", "No answer generated."))
        print("=" * 60)
        if trace_file:
            print(f"\nTrace saved to: {trace_file}")

    else:
        print("Usage: python -m app.cli --question <question>")
        print("       python -m app.cli --batch [--test-file data/test.json]")


if __name__ == "__main__":
    main()
