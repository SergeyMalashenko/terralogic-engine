"""Application service exposed by the TerraLogic MCP adapter."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path

from terralogic_engine.acquisition.pipeline import AcquisitionPipeline
from terralogic_engine.analytics.pipeline import AnalysisPipeline
from terralogic_engine.domain.models import CollectionRequest, RefreshPolicy
from terralogic_engine.quickreport import (
    QuickReportContext,
    QuickReportResult,
    build_quick_context,
    methodology_sha256,
    render_quickreport,
)
from terralogic_engine.reporting.context import (
    build_report_context,
    collection_receipt_for_run,
)
from terralogic_engine.reporting.models import (
    GeneratedReport,
    PrepareCaseResult,
    ReportContext,
    ReportTemplate,
)
from terralogic_engine.reporting.template_registry import (
    DEFAULT_TEMPLATE_ID,
    DEFAULT_TEMPLATE_VERSION,
    ReportTemplateRegistry,
    create_default_template_registry,
)
from terralogic_engine.store.base import CaseStore

MAX_REPORT_CHARACTERS = 500_000
MAX_REPORT_TITLE_CHARACTERS = 300
MAX_MODEL_NAME_CHARACTERS = 200

QUICK_REPORT_TEMPLATE_ID = "quick_report"


class CasePreparationError(RuntimeError):
    """Raised when source collection cannot produce an analyzable parcel."""


class ReportingService:
    """Coordinate collection, analytics, report context, and persistence."""

    def __init__(
        self,
        *,
        store: CaseStore,
        acquisition: AcquisitionPipeline,
        template_registry: ReportTemplateRegistry | None = None,
        tile_fetcher: Callable[[int, int, int, float], bytes | None] | None = None,
    ) -> None:
        self.store = store
        self.acquisition = acquisition
        self.template_registry = template_registry or create_default_template_registry()
        self.tile_fetcher = tile_fetcher

    async def prepare_case(
        self,
        cadastral_number: str,
        *,
        case_id: str | None = None,
        margin_m: int = 1000,
        refresh_policy: RefreshPolicy = "if_stale",
    ) -> PrepareCaseResult:
        normalized_case_id = case_id or (
            f"case-{cadastral_number.replace(':', '-').replace(' ', '')}"
        )
        receipt = await self.acquisition.collect(
            CollectionRequest(
                case_id=normalized_case_id,
                cadastral_number=cadastral_number,
                refresh_policy=refresh_policy,
                margin_m=margin_m,
                allow_partial=True,
            )
        )
        if receipt.aoi_id is None:
            details = "; ".join(receipt.errors) or "collection produced no AOI"
            raise CasePreparationError(f"Case preparation failed: {details}")
        analysis = self.store.get_analysis_result(
            receipt.case_id,
            receipt.run_id,
        )
        if analysis is None:
            analysis = AnalysisPipeline(store=self.store).analyze(
                receipt.case_id,
                run_id=receipt.run_id,
            )
        return PrepareCaseResult(
            case_id=receipt.case_id,
            collection_run_id=receipt.run_id,
            collection_status=receipt.status,
            analysis_id=analysis.id,
            reused_collection=receipt.reused,
            feature_counts=receipt.feature_counts,
            warnings=list(dict.fromkeys([*receipt.warnings, *analysis.warnings])),
            errors=receipt.errors,
        )

    def get_report_context(
        self,
        case_id: str,
        *,
        collection_run_id: str | None = None,
    ) -> ReportContext:
        return build_report_context(
            self.store,
            case_id,
            collection_run_id=collection_run_id,
        )

    def prepare_quickreport(
        self,
        case_id: str,
        *,
        collection_run_id: str | None = None,
    ) -> QuickReportResult:
        """Build and persist the deterministic quick report for a case run.

        No LLM is involved: factor texts come from ``zone_guidance.yaml``
        and the score from the stub methodology in ``scoring_rules.yaml``.
        The report is stored with ``template_id="quick_report"`` and the
        methodology version as the template version.
        """

        context = build_quick_context(
            self.store,
            case_id,
            collection_run_id=collection_run_id,
        )
        map_lines, map_relative_path, map_warnings = self._build_quick_map(
            case_id, context
        )
        context.warnings.extend(map_warnings)
        markdown = render_quickreport(context, map_lines=map_lines)
        analysis = self.store.get_analysis_result(
            case_id,
            context.collection_run_id,
        )
        if analysis is None:
            raise ValueError("The selected run has no analytics result")
        report = self.store.save_generated_report(
            case_id=case_id,
            collection_run_id=context.collection_run_id,
            analysis_id=analysis.id,
            title=f"Экспресс-оценка участка {context.parcel.cadastral_number}",
            template_id=QUICK_REPORT_TEMPLATE_ID,
            template_version=context.methodology_version,
            template_sha256=methodology_sha256(),
            markdown=markdown,
        )
        return QuickReportResult(
            report_id=report.id,
            case_id=report.case_id,
            collection_run_id=report.collection_run_id,
            analysis_id=report.analysis_id,
            methodology_version=context.methodology_version,
            title=report.title,
            relative_path=report.relative_path,
            content_sha256=report.content_sha256,
            generated_at=report.generated_at,
            markdown=report.markdown,
            map_relative_path=map_relative_path,
            warnings=context.warnings,
        )

    def _build_quick_map(
        self, case_id: str, context: QuickReportContext
    ) -> tuple[list[str] | None, str | None, list[str]]:
        """PNG-схема участка и зон для раздела карты quick-отчёта.

        Подложка — тайлы OSM (кэш в ``<store-root>/tile-cache``); слои —
        restriction_zone геометрии снапшотов выбранного run. Любой сбой
        (нет Pillow, нет сети, нет геометрий) деградирует в заглушку
        раздела с предупреждением, отчёт при этом собирается всегда.
        """
        warnings: list[str] = []
        try:
            from terralogic_engine.quickreport import mapimg
        except ImportError:
            return (
                None,
                None,
                ["карта не построена: не установлен extra 'map' (Pillow)"],
            )
        try:
            parcel_geometry = self._parcel_geometry_for_run(
                case_id, context.collection_run_id
            )
            if parcel_geometry is None:
                return None, None, ["карта не построена: нет геометрии участка"]
            features = self._run_features(case_id, context.collection_run_id)
            layers = mapimg.collect_map_layers(features)
            parcel_label = context.parcel.cadastral_number
            if context.parcel.area_m2:
                area = f"{context.parcel.area_m2:,.0f}".replace(",", " ")
                parcel_label = f"{parcel_label} · {area} м²"
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            try:
                store_root = getattr(self.store, "root", None)
                cache_dir = (
                    Path(store_root) / "tile-cache" if store_root is not None else None
                )
                result = mapimg.render_overview_map(
                    parcel_geometry,
                    layers,
                    tmp_path,
                    cache_dir=cache_dir,
                    fetch_tile=self.tile_fetcher,
                    parcel_label=parcel_label,
                )
                warnings.extend(result.warnings)
                relative_path = self.store.save_case_artifact(
                    case_id=case_id,
                    kind="maps",
                    filename=f"quickreport-{context.collection_run_id}.png",
                    payload=result.path.read_bytes(),
                )
            finally:
                tmp_path.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001 — карта не должна ломать отчёт
            return None, None, [f"карта не построена: {exc}"]
        caption = (
            "Подложка — © OpenStreetMap contributors. Схема предварительная: "
            "границы зон в источниках расходятся; точное нахождение границ "
            "участка подлежит проверке по градостроительному плану участка (ГПЗУ)."
        )
        if not result.basemap_available:
            caption = f"Подложка карты недоступна (офлайн). {caption}"
        lines = [
            f"![Схема участка и зон (предварительная)](../{relative_path})",
            "",
            caption,
        ]
        return lines, relative_path, warnings

    def _parcel_geometry_for_run(
        self, case_id: str, run_id: str
    ) -> dict[str, object] | None:
        receipts = self.store.list_collection_receipts(case_id)
        receipt = next((item for item in receipts if item.run_id == run_id), None)
        if receipt is not None and receipt.aoi_id is not None:
            aoi = self.store.get_area_of_interest(case_id, receipt.aoi_id)
            if isinstance(aoi.parcel_geometry, dict) and aoi.parcel_geometry:
                return aoi.parcel_geometry
        for feature in self._run_features(case_id, run_id):
            if feature.feature_class == "parcel" and isinstance(feature.geometry, dict):
                return feature.geometry
        return None

    def _run_features(self, case_id: str, run_id: str) -> list:
        features: list = []
        for snapshot in self.store.list_snapshots(case_id):
            if snapshot.run_id != run_id:
                continue
            features.extend(self.store.load_features(case_id, snapshot_id=snapshot.id))
        return features

    def get_report_template(
        self,
        template_id: str = DEFAULT_TEMPLATE_ID,
        *,
        template_version: str = DEFAULT_TEMPLATE_VERSION,
    ) -> ReportTemplate:
        return self.template_registry.get(template_id, template_version)

    def save_report(
        self,
        case_id: str,
        markdown: str,
        *,
        collection_run_id: str | None = None,
        title: str | None = None,
        model_name: str | None = None,
        template_id: str = DEFAULT_TEMPLATE_ID,
        template_version: str = DEFAULT_TEMPLATE_VERSION,
    ) -> GeneratedReport:
        normalized_markdown = markdown.strip()
        if not normalized_markdown:
            raise ValueError("markdown must not be empty")
        if "\x00" in normalized_markdown:
            raise ValueError("markdown must not contain NUL characters")
        if len(normalized_markdown) > MAX_REPORT_CHARACTERS:
            raise ValueError(
                f"markdown exceeds the {MAX_REPORT_CHARACTERS} character limit"
            )
        normalized_title = title.strip() if title else None
        normalized_model_name = model_name.strip() if model_name else None
        if normalized_title and len(normalized_title) > MAX_REPORT_TITLE_CHARACTERS:
            raise ValueError(
                f"title exceeds the {MAX_REPORT_TITLE_CHARACTERS} character limit"
            )
        if (
            normalized_model_name
            and len(normalized_model_name) > MAX_MODEL_NAME_CHARACTERS
        ):
            raise ValueError(
                f"model_name exceeds the {MAX_MODEL_NAME_CHARACTERS} character limit"
            )
        template = self.template_registry.get(
            template_id,
            template_version,
        )
        self.template_registry.validate_markdown(
            template,
            normalized_markdown,
        )
        receipt = collection_receipt_for_run(
            self.store,
            case_id,
            collection_run_id,
        )
        analysis = self.store.get_analysis_result(case_id, receipt.run_id)
        if analysis is None:
            raise ValueError("The selected run has no analytics result")
        case = self.store.get_case(case_id)
        return self.store.save_generated_report(
            case_id=case_id,
            collection_run_id=receipt.run_id,
            analysis_id=analysis.id,
            title=(normalized_title or f"Отчёт об участке {case.cadastral_number}"),
            template_id=template.template_id,
            template_version=template.version,
            template_sha256=template.content_sha256,
            markdown=f"{normalized_markdown}\n",
            model_name=normalized_model_name,
        )
