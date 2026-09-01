from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from terralogic_engine.acquisition.pipeline import AcquisitionPipeline
from terralogic_engine.mcp import (
    _build_parser,
    create_mcp_server,
)
from terralogic_engine.reporting.service import ReportingService
from terralogic_engine.store.local import LocalCaseStore

from .fakes import FakeDgisClient, FakeNspdClient, FakeOsmClient


def _valid_markdown(service: ReportingService) -> str:
    template = service.get_report_template()
    sections = "\n\n".join(
        f"{section.heading}\n\nСодержимое раздела."
        for section in template.sections
    )
    return f"# Отчёт\n\n{sections}"


class FakeFastMCP:
    def __init__(self, name: str, **kwargs: Any) -> None:
        self.name = name
        self.kwargs = kwargs
        self.tools: dict[str, Callable[..., Any]] = {}

    def tool(self) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(function: Callable[..., Any]) -> Callable[..., Any]:
            self.tools[function.__name__] = function
            return function

        return decorator


def _service(tmp_path) -> ReportingService:
    store = LocalCaseStore(tmp_path / "store")
    return ReportingService(
        store=store,
        acquisition=AcquisitionPipeline(
            store=store,
            nspd=FakeNspdClient(),
            osm=FakeOsmClient(),
            dgis=FakeDgisClient(),
        ),
    )


def test_mcp_registers_four_report_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "terralogic_engine.mcp._load_mcp_server_class",
        lambda: FakeFastMCP,
    )

    server = create_mcp_server(_service(tmp_path))

    assert list(server.tools) == [
        "terralogic_prepare_case",
        "terralogic_get_report_context",
        "terralogic_get_report_template",
        "terralogic_save_report",
    ]
    assert server.kwargs["port"] == 8004
    assert server.kwargs["stateless_http"] is True
    assert server.kwargs["json_response"] is True


@pytest.mark.filterwarnings(
    "ignore:Field 'lifespan' has an incomplete definition"
)
async def test_real_streamable_http_lists_four_tools(tmp_path) -> None:
    pytest.importorskip("mcp.server.fastmcp")
    app = create_mcp_server(_service(tmp_path)).streamable_http_app()
    headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
    }
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8004",
        ) as client,
    ):
        initialized = await client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "1"},
                },
            },
        )
        listed = await client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )

    assert initialized.status_code == 200
    assert listed.status_code == 200
    assert [
        tool["name"] for tool in listed.json()["result"]["tools"]
    ] == [
        "terralogic_prepare_case",
        "terralogic_get_report_context",
        "terralogic_get_report_template",
        "terralogic_save_report",
    ]


async def test_mcp_supports_complete_hermes_report_workflow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "terralogic_engine.mcp._load_mcp_server_class",
        lambda: FakeFastMCP,
    )
    service = _service(tmp_path)
    server = create_mcp_server(service)

    prepared = await server.tools["terralogic_prepare_case"](
        "52:26:0040002:3823",
        refresh_policy="always",
    )
    assert prepared.ok
    assert prepared.data is not None

    context = await server.tools["terralogic_get_report_context"](
        prepared.data.case_id,
        prepared.data.collection_run_id,
    )
    assert context.ok
    assert context.data is not None
    assert context.data.analysis_id == prepared.data.analysis_id

    template = await server.tools["terralogic_get_report_template"]()
    assert template.ok
    assert template.data is not None
    assert template.data.template_id == "full_land_report"

    saved = await server.tools["terralogic_save_report"](
        prepared.data.case_id,
        _valid_markdown(service),
        prepared.data.collection_run_id,
        model_name="gemma4:31b",
    )
    assert saved.ok
    assert saved.data is not None
    assert saved.data.analysis_id == prepared.data.analysis_id
    assert saved.data.template_id == "full_land_report"


async def test_mcp_returns_structured_error_for_unknown_case(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "terralogic_engine.mcp._load_mcp_server_class",
        lambda: FakeFastMCP,
    )
    server = create_mcp_server(_service(tmp_path))

    result = await server.tools["terralogic_get_report_context"]("missing")

    assert not result.ok
    assert result.error is not None
    assert result.error.code == "not_found"


def test_mcp_cli_defaults_to_stdio_and_port_8004() -> None:
    args = _build_parser().parse_args([])

    assert args.transport == "stdio"
    assert args.host == "127.0.0.1"
    assert args.port == 8004
    assert args.store == "./case-store"
