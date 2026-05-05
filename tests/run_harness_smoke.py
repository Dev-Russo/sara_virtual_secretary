#!/usr/bin/env python3
"""Small CLI for exercising the local development harness."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.harness.db import assert_dev_database, reset_seed_data
from tests.harness.seeds import seed_core, seed_volume
from tests.harness.simulate import DEFAULT_FAKE_RESPONSE, simulate_message

FAKE_LLM_MARKER = "[fake-llm]"


def _print_result(result: dict) -> None:
    for key, value in result.items():
        print(f"{key}: {value}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Sara development harness smoke commands.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("reset")
    subparsers.add_parser("seed-core")

    seed_volume_parser = subparsers.add_parser("seed-volume")
    seed_volume_parser.add_argument("--count", type=int, default=500)

    simulate_parser = subparsers.add_parser("simulate")
    simulate_parser.add_argument("message")
    simulate_parser.add_argument("--llm-mode", choices=["real", "fake", "recorded"], default="fake")

    subparsers.add_parser("phase-1-smoke")

    args = parser.parse_args()

    try:
        assert_dev_database()
        if args.command == "reset":
            reset_seed_data()
            print("reset: ok")
        elif args.command == "seed-core":
            _print_result(seed_core())
        elif args.command == "seed-volume":
            _print_result(seed_volume(count=args.count))
        elif args.command == "simulate":
            result = simulate_message(
                args.message,
                llm_mode=args.llm_mode,
                fake_responses=[DEFAULT_FAKE_RESPONSE],
            )
            print(f"llm_mode={result['llm_mode']}")
            print(f"session_state={result['session_state']}")
            print(result["response"])
        elif args.command == "phase-1-smoke":
            seed_core()
            simulate_message("listar minhas tarefas", llm_mode="fake")
            print("PHASE_1_SMOKE_OK")
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
