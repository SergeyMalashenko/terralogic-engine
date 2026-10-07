"""Command-line entry point for one synchronous collection run."""

from __future__ import annotations

import argparse
import asyncio

from terralogic_engine.acquisition.clients import (
    McpDgisClient,
    McpGeodocsClient,
    McpNspdClient,
    McpNspdDocumentsClient,
    McpOsmClient,
    McpRgisClient,
    McpRgisDocumentsClient,
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
    parser.add_argument(
        "--nspd-url",
        default="http://127.0.0.1:8001/mcp",
        help="NSPD MCP URL; its document tools share the same server",
    )
    parser.add_argument("--osm-url", default="http://127.0.0.1:8002/mcp")
    parser.add_argument("--dgis-url", default="http://127.0.0.1:8003/mcp")
    parser.add_argument(
        "--rgis-url",
        help=(
            "Optional RGIS MO MCP URL; used only for cadastral region 50, "
            "and its document tools share the same server"
        ),
    )
    parser.add_argument(
        "--geodocs-url",
        help=(
            "Optional geodocs-mcp URL (second document contour: acquire and "
            "query); without it the document contour stops after sync"
        ),
    )
    parser.add_argument(
        "--profile-version",
        default="3.0",
        help=(
            "Collection profile version; 3.1 enables the geodocs document "
            "contour (acquire + query) when --geodocs-url is configured"
        ),
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
    nspd_transport = StreamableHttpMcpTransport(args.nspd_url)
    pipeline = AcquisitionPipeline(
        store=LocalCaseStore(args.store),
        nspd=McpNspdClient(nspd_transport),
        osm=McpOsmClient(StreamableHttpMcpTransport(args.osm_url)),
        dgis=McpDgisClient(StreamableHttpMcpTransport(args.dgis_url)),
        rgis=(
            McpRgisClient(StreamableHttpMcpTransport(args.rgis_url))
            if args.rgis_url
            else None
        ),
        rgis_documents=(
            McpRgisDocumentsClient(StreamableHttpMcpTransport(args.rgis_url))
            if args.rgis_url
            else None
        ),
        nspd_documents=McpNspdDocumentsClient(nspd_transport),
        geodocs=(McpGeodocsClient(url=args.geodocs_url) if args.geodocs_url else None),
    )
    receipt = await pipeline.collect(
        CollectionRequest(
            case_id=case_id,
            cadastral_number=args.cadastral_number,
            profile_version=args.profile_version,
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
