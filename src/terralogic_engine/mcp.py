"""Model Context Protocol server for TerraLogic case reports."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field

from terralogic_engine.acquisition.clients import (
    McpDgisClient,
    McpNspdClient,
    McpOsmClient,
    McpRgisClient,
    StreamableHttpMcpTransport,
)
from terralogic_engine.acquisition.pipeline import AcquisitionPipeline
from terralogic_engine.reporting.context import ReportContextError
from terralogic_engine.reporting.models import (
    PrepareCaseResult,
    ReportContext,
    ReportTemplate,
    SavedReportResult,
)
from terralogic_engine.reporting.service import (
    CasePreparationError,
    ReportingService,
)
from terralogic_engine.reporting.template_registry import (
    DEFAULT_TEMPLATE_ID,
    DEFAULT_TEMPLATE_VERSION,
)
from terralogic_engine.store.local import LocalCaseStore

DEFAULT_INSTRUCTIONS = (
    "Use terralogic_prepare_case first when the user provides a cadastral number. "
    "It collects NSPD, OSM, 2GIS, and RGIS MO data, performs deterministic spatial "
    "analytics, and returns case_id plus collection_run_id. Then call "
    "terralogic_get_report_context for facts and terralogic_get_report_template "
    "for the independent report structure. Write a Russian Markdown report "
    "using only the context facts and the selected template. Never calculate "
    "areas, percentages, or "
    "distances yourself. Treat not_found_within_aoi as absence only in the "
    "collected search area. Transport example distances are measured from the "
    "search point, not the parcel. Do not present the report as a legal opinion. "
    "Finally call terralogic_save_report with the complete Markdown document."
)

T = TypeVar("T")
LOGGER = logging.getLogger(__name__)


class ToolError(BaseModel):
    """Stable error returned without turning a domain failure into MCP failure."""

    code: str
    message: str
    retryable: bool = False


class ToolMetadata(BaseModel):
    """Common provenance metadata for TerraLogic MCP responses."""

    provider: str = "TerraLogicX"
    sources: list[str] = Field(
        default_factory=lambda: [
            "NSPD",
            "OpenStreetMap",
            "2GIS",
            "RGIS MO (Геопортал Подмосковья)",
        ]
    )


class ToolResult(BaseModel, Generic[T]):
    """Structured result envelope convenient for local language models."""

    ok: bool
    data: T | None = None
    error: ToolError | None = None
    metadata: ToolMetadata = Field(default_factory=ToolMetadata)


class MCPDependencyError(RuntimeError):
    """Raised when the optional MCP SDK is unavailable."""


def _load_mcp_server_class() -> type[Any]:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise MCPDependencyError(
            "MCP support is not installed. Install it with "
            "`pip install 'terralogic-engine[mcp]'`."
        ) from exc
    return FastMCP


def create_reporting_service(
    *,
    store_path: str | Path = "./case-store",
    nspd_url: str = "http://127.0.0.1:8001/mcp",
    osm_url: str = "http://127.0.0.1:8002/mcp",
    dgis_url: str = "http://127.0.0.1:8003/mcp",
    rgis_url: str = "http://127.0.0.1:8005/mcp",
) -> ReportingService:
    """Create the production service using the four upstream MCP servers."""

    store = LocalCaseStore(store_path)
    acquisition = AcquisitionPipeline(
        store=store,
        nspd=McpNspdClient(StreamableHttpMcpTransport(nspd_url)),
        osm=McpOsmClient(StreamableHttpMcpTransport(osm_url)),
        dgis=McpDgisClient(StreamableHttpMcpTransport(dgis_url)),
        rgis=McpRgisClient(StreamableHttpMcpTransport(rgis_url)),
    )
    return ReportingService(store=store, acquisition=acquisition)


def _tool_failure(exc: Exception) -> ToolError:
    if isinstance(exc, KeyError):
        return ToolError(code="not_found", message=str(exc))
    if isinstance(exc, (ReportContextError, ValueError)):
        return ToolError(code="invalid_request", message=str(exc))
    if isinstance(exc, CasePreparationError):
        return ToolError(
            code="collection_failed",
            message=str(exc),
            retryable=True,
        )
    LOGGER.exception("Unexpected TerraLogic MCP tool failure", exc_info=exc)
    return ToolError(
        code="internal_error",
        message="Unexpected error while executing the TerraLogic tool",
        retryable=False,
    )


def create_mcp_server(
    service: ReportingService,
    *,
    name: str = "terralogic",
    instructions: str = DEFAULT_INSTRUCTIONS,
    host: str = "127.0.0.1",
    port: int = 8004,
    streamable_http_path: str = "/mcp",
    stateless_http: bool = True,
    json_response: bool = True,
) -> Any:
    """Create a FastMCP server exposing the report-generation workflow."""

    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    if not streamable_http_path.startswith("/"):
        raise ValueError("streamable_http_path must start with '/'")

    server_class = _load_mcp_server_class()
    server = server_class(
        name,
        instructions=instructions,
        host=host,
        port=port,
        streamable_http_path=streamable_http_path,
        stateless_http=stateless_http,
        json_response=json_response,
    )

    @server.tool()
    async def terralogic_prepare_case(
        cadastral_number: str,
        case_id: str | None = None,
        margin_m: int = 1000,
        refresh_policy: Literal["never", "if_stale", "always"] = "if_stale",
    ) -> ToolResult[PrepareCaseResult]:
        """Collect and analyze one land parcel before report generation.

        Args:
            cadastral_number: Four numeric parts separated by colons.
            case_id: Optional stable CaseStore identifier. If omitted, it is
                derived from the cadastral number.
            margin_m: Metres added to the parcel minimum enclosing radius,
                from 0 to 10000.
            refresh_policy: ``if_stale`` reuses fresh data, ``always`` obtains
                new source snapshots, and ``never`` reuses any compatible run.
        """

        try:
            data = await service.prepare_case(
                cadastral_number,
                case_id=case_id,
                margin_m=margin_m,
                refresh_policy=refresh_policy,
            )
        except Exception as exc:  # noqa: BLE001 - MCP application boundary
            return ToolResult[PrepareCaseResult](
                ok=False,
                error=_tool_failure(exc),
            )
        return ToolResult[PrepareCaseResult](ok=True, data=data)

    @server.tool()
    async def terralogic_get_report_context(
        case_id: str,
        collection_run_id: str | None = None,
    ) -> ToolResult[ReportContext]:
        """Load compact verified facts for a Hermes-generated report.

        Args:
            case_id: Existing CaseStore case identifier.
            collection_run_id: Optional exact source run. If omitted, the
                latest completed collection run is selected.
        """

        try:
            data = service.get_report_context(
                case_id,
                collection_run_id=collection_run_id,
            )
        except Exception as exc:  # noqa: BLE001 - MCP application boundary
            return ToolResult[ReportContext](
                ok=False,
                error=_tool_failure(exc),
            )
        return ToolResult[ReportContext](ok=True, data=data)

    @server.tool()
    async def terralogic_get_report_template(
        template_id: str = DEFAULT_TEMPLATE_ID,
        template_version: str = DEFAULT_TEMPLATE_VERSION,
    ) -> ToolResult[ReportTemplate]:
        """Load an independent versioned Markdown report template.

        Args:
            template_id: Stable template identity, currently
                ``full_land_report``.
            template_version: Exact immutable template version, currently
                ``1.0``.
        """

        try:
            data = service.get_report_template(
                template_id,
                template_version=template_version,
            )
        except Exception as exc:  # noqa: BLE001 - MCP application boundary
            return ToolResult[ReportTemplate](
                ok=False,
                error=_tool_failure(exc),
            )
        return ToolResult[ReportTemplate](ok=True, data=data)

    @server.tool()
    async def terralogic_save_report(
        case_id: str,
        markdown: str,
        collection_run_id: str | None = None,
        title: str | None = None,
        model_name: str | None = None,
        template_id: str = DEFAULT_TEMPLATE_ID,
        template_version: str = DEFAULT_TEMPLATE_VERSION,
    ) -> ToolResult[SavedReportResult]:
        """Persist a complete model-generated Markdown report in CaseStore.

        Args:
            case_id: Existing CaseStore case identifier.
            markdown: Complete report document, up to 500000 characters.
            collection_run_id: Exact source run used by the report. If omitted,
                the latest collection run is selected.
            title: Optional report title stored in artifact metadata.
            model_name: Optional model identifier for reproducibility.
            template_id: Template identity used to generate the Markdown.
            template_version: Exact template version used by Hermes.
        """

        try:
            report = service.save_report(
                case_id,
                markdown,
                collection_run_id=collection_run_id,
                title=title,
                model_name=model_name,
                template_id=template_id,
                template_version=template_version,
            )
            data = SavedReportResult(
                report_id=report.id,
                case_id=report.case_id,
                collection_run_id=report.collection_run_id,
                analysis_id=report.analysis_id,
                template_id=report.template_id,
                template_version=report.template_version,
                template_sha256=report.template_sha256,
                relative_path=report.relative_path,
                content_sha256=report.content_sha256,
                generated_at=report.generated_at,
            )
        except Exception as exc:  # noqa: BLE001 - MCP application boundary
            return ToolResult[SavedReportResult](
                ok=False,
                error=_tool_failure(exc),
            )
        return ToolResult[SavedReportResult](ok=True, data=data)

    return server


async def _run_server(server: Any, transport: str) -> None:
    if transport == "stdio":
        await server.run_stdio_async()
    elif transport == "streamable-http":
        await server.run_streamable_http_async()
    else:  # pragma: no cover - argparse restricts public values
        raise ValueError(f"Unsupported MCP transport: {transport}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="terralogic-mcp",
        description="Run the TerraLogic report workflow as an MCP server.",
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8004)
    parser.add_argument("--path", default="/mcp", dest="streamable_http_path")
    parser.add_argument("--store", default="./case-store")
    parser.add_argument("--nspd-url", default="http://127.0.0.1:8001/mcp")
    parser.add_argument("--osm-url", default="http://127.0.0.1:8002/mcp")
    parser.add_argument("--dgis-url", default="http://127.0.0.1:8003/mcp")
    parser.add_argument("--rgis-url", default="http://127.0.0.1:8005/mcp")
    parser.add_argument("--stateful-http", action="store_true")
    parser.add_argument("--sse-response", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    try:
        import anyio

        service = create_reporting_service(
            store_path=args.store,
            nspd_url=args.nspd_url,
            osm_url=args.osm_url,
            dgis_url=args.dgis_url,
            rgis_url=args.rgis_url,
        )
        server = create_mcp_server(
            service,
            host=args.host,
            port=args.port,
            streamable_http_path=args.streamable_http_path,
            stateless_http=not args.stateful_http,
            json_response=not args.sse_response,
        )
    except (ImportError, MCPDependencyError) as exc:
        raise SystemExit(
            "MCP support is not installed. Install it with "
            "`pip install 'terralogic-engine[mcp]'`."
        ) from exc
    anyio.run(_run_server, server, args.transport)


if __name__ == "__main__":  # pragma: no cover
    main()
