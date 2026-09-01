"""Command-line launcher for the Streamlit case viewer."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="terralogic-view",
        description="Open a read-only web viewer for a local TerraLogic CaseStore",
    )
    parser.add_argument("--store", default="./case-store")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8501)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if not 1 <= args.port <= 65535:
        raise SystemExit("--port must be between 1 and 65535")

    try:
        from streamlit.web import cli as streamlit_cli
    except ImportError as exc:
        raise SystemExit(
            "Viewer dependencies are not installed. Install them with: "
            "pip install 'terralogic-engine[viewer]'"
        ) from exc

    application = Path(__file__).with_name("app.py")
    store = Path(args.store).expanduser().resolve()
    sys.argv = [
        "streamlit",
        "run",
        str(application),
        f"--server.address={args.host}",
        f"--server.port={args.port}",
        "--server.headless=true",
        "--",
        "--store",
        str(store),
    ]
    raise SystemExit(streamlit_cli.main())


if __name__ == "__main__":
    main()
