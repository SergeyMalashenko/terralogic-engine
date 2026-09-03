"""Command-line entry point for one synchronous collection run."""

from __future__ import annotations

import argparse
import asyncio

from terralogic_engine.acquisition.clients import (
    McpDgisClient,
    McpNspdClient,
    McpOsmClient,
    McpRgisClient,
    StreamableHttpMcpTransport,
)
from terralogic_engine.acquisition.pipeline import AcquisitionPipeline
from terralogic_engine.domain.models import CollectionRequest
from terralogic_engine.store.local import LocalCaseStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="terralogic-collect",
        description="Collect NSPD, OSM, 2GIS, and optional RGIS data",
    )
    parser.add_argument("cadastral_number")
    parser.add_argument("--case-id")
    parser.add_argument("--store", default="./case-store")
    parser.add_argument("--nspd-url", default="http://127.0.0.1:8001/mcp")
    parser.add_argument("--osm-url", default="http://127.0.0.1:8002/mcp")
    parser.add_argument("--dgis-url", default="http://127.0.0.1:8003/mcp")
    parser.add_argument(
        "--rgis-url",
        help="Optional RGIS MO MCP URL; used only for cadastral region 50",
    )
    parser.add_argument(
        "--margin-m",
        type=int,
        help="Metres added to the parcel minimum enclosing radius",
    )
    parser.add_argument(
        "--refresh-policy",
        choices=("never", "if_stale", "always"),
        default="if_stale",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Mark a source failure as failed instead of preserving a partial run",
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    case_id = args.case_id or f"case-{args.cadastral_number.replace(':', '-')}"
    pipeline = AcquisitionPipeline(
        store=LocalCaseStore(args.store),
        nspd=McpNspdClient(StreamableHttpMcpTransport(args.nspd_url)),
        osm=McpOsmClient(StreamableHttpMcpTransport(args.osm_url)),
        dgis=McpDgisClient(StreamableHttpMcpTransport(args.dgis_url)),
        rgis=(
            McpRgisClient(StreamableHttpMcpTransport(args.rgis_url))
            if args.rgis_url
            else None
        ),
    )
    receipt = await pipeline.collect(
        CollectionRequest(
            case_id=case_id,
            cadastral_number=args.cadastral_number,
            refresh_policy=args.refresh_policy,
            allow_partial=not args.strict,
            margin_m=args.margin_m,
        )
    )
    print(receipt.model_dump_json(indent=2))
    return 0 if receipt.status in {"complete", "partial"} else 1


def main() -> None:
    args = _parser().parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
