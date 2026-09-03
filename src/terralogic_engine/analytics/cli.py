"""Command-line entry point for deterministic case analytics."""

from __future__ import annotations

import argparse

from terralogic_engine.analytics.pipeline import (
    AnalysisInputError,
    AnalysisPipeline,
)
from terralogic_engine.store.local import LocalCaseStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="terralogic-analyze",
        description="Calculate spatial metrics for one collected TerraLogic case",
    )
    parser.add_argument("case_id")
    parser.add_argument("--store", default="./case-store")
    parser.add_argument(
        "--run-id",
        help="Collection run to analyze; defaults to the latest completed run",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        result = AnalysisPipeline(store=LocalCaseStore(args.store)).analyze(
            args.case_id, run_id=args.run_id
        )
    except (AnalysisInputError, KeyError, ValueError) as exc:
        raise SystemExit(f"Analysis failed: {exc}") from exc
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
