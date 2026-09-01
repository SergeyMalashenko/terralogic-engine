"""Application service exposed by the TerraLogic MCP adapter."""

from __future__ import annotations

from terralogic_acquisition.acquisition.pipeline import AcquisitionPipeline
from terralogic_acquisition.analytics.pipeline import AnalysisPipeline
from terralogic_acquisition.domain.models import CollectionRequest, RefreshPolicy
from terralogic_acquisition.reporting.context import (
    build_report_context,
    collection_receipt_for_run,
)
from terralogic_acquisition.reporting.models import (
    GeneratedReport,
    PrepareCaseResult,
    ReportContext,
    ReportTemplate,
)
from terralogic_acquisition.reporting.template_registry import (
    DEFAULT_TEMPLATE_ID,
    DEFAULT_TEMPLATE_VERSION,
    ReportTemplateRegistry,
    create_default_template_registry,
)
from terralogic_acquisition.store.base import CaseStore

MAX_REPORT_CHARACTERS = 500_000
MAX_REPORT_TITLE_CHARACTERS = 300
MAX_MODEL_NAME_CHARACTERS = 200


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
    ) -> None:
        self.store = store
        self.acquisition = acquisition
        self.template_registry = (
            template_registry or create_default_template_registry()
        )

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
                "model_name exceeds the "
                f"{MAX_MODEL_NAME_CHARACTERS} character limit"
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
            title=(
                normalized_title
                or f"Отчёт об участке {case.cadastral_number}"
            ),
            template_id=template.template_id,
            template_version=template.version,
            template_sha256=template.content_sha256,
            markdown=f"{normalized_markdown}\n",
            model_name=normalized_model_name,
        )
