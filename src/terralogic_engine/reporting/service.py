"""Application service exposed by the TerraLogic MCP adapter."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path

from terralogic_engine.acquisition.pipeline import AcquisitionPipeline
from terralogic_engine.analytics.pipeline import AnalysisPipeline
from terralogic_engine.domain.models import CollectionRequest, RefreshPolicy
from terralogic_engine.qa.critic import CriticExecutor
from terralogic_engine.quickreport import (
    MarketingConfig,
    QuickReportContext,
    QuickReportResult,
    build_quick_context,
    load_marketing,
    methodology_sha256,
)
from terralogic_engine.quickreport.model import (
    QuickNarrative,
    QuickReportModel,
    build_quick_model,
)
from terralogic_engine.quickreport.narrative import (
    DeterministicNarrativeGenerator,
    EstablishedFactsInput,
    EstablishedFactsNarrative,
    FallbackNarrativeGenerator,
    HermesNarrativeGenerator,
    NarrativeExecutor,
    NarrativeGenerator,
    build_established_facts_input,
)
from terralogic_engine.quickreport.render import render_quick_model
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
        marketing: MarketingConfig | None = None,
        critic: CriticExecutor | None = None,
        narrative_generator: NarrativeGenerator | None = None,
        writer: NarrativeExecutor | None = None,
    ) -> None:
        self.store = store
        self.acquisition = acquisition
        self.template_registry = template_registry or create_default_template_registry()
        self.tile_fetcher = tile_fetcher
        # Коммерческий блок отчёта: статическая конфигурация, не из LLM.
        self._marketing = marketing if marketing is not None else load_marketing()
        # LLM-критик отчёта (§65 ТЗ); None — QA ограничено валидатором.
        self._critic = critic
        # Bounded NarrativeGenerator блока «Что уже удалось установить»
        # (§62 ТЗ); None — текст полностью детерминированный.
        # Compatibility shim: старый writer-executor оборачивается в
        # HermesNarrativeGenerator с детерминированным fallback.
        if narrative_generator is not None:
            self._narrative_generator = narrative_generator
        elif writer is not None:
            self._narrative_generator = FallbackNarrativeGenerator(
                HermesNarrativeGenerator(writer),
                DeterministicNarrativeGenerator(),
            )
        else:
            self._narrative_generator = None

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
        """Build and persist the quick report for a case run.

        Факты, скоринг 6.2, паспорт, факторы и маркетинг детерминированны;
        единственный опциональный LLM-этап — NarrativeGenerator раздела
        «Что уже удалось установить» (§62 ТЗ) поверх готовой модели; его
        сбой — fallback на детерминированный текст (§77). Отчёт сохраняется
        с ``template_id="quick_report"`` и версией методики как версией
        шаблона; при применённом LLM-narrative модель уходит в model_name.
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
        # Продуктовая модель → (опц.) bounded narrative → черновик → QA
        # (§61–63 ТЗ). Narrative меняет только текст раздела «Что уже
        # удалось установить», не факты; сбой генератора — fallback на
        # детерминированный текст (§77).
        model = build_quick_model(context, marketing=self._marketing)
        facts_input: EstablishedFactsInput | None = None
        narrative_result: EstablishedFactsNarrative | None = None
        if self._narrative_generator is not None:
            facts_input = build_established_facts_input(context, model)
            narrative_result = self._narrative_generator.generate_established_facts(
                facts_input
            )
            if narrative_result is not None:
                context.warnings.extend(narrative_result.warnings)
                model = model.model_copy(
                    update={"narrative": self._quick_narrative(narrative_result)}
                )
        markdown = render_quick_model(
            model,
            map_lines=map_lines,
            zone_regulations=context.zone_regulations,
        )
        qa_relative_path, qa_warnings, markdown, model, narrative_result = (
            self._build_qa_report(
                case_id,
                context,
                model,
                markdown,
                map_lines=map_lines,
                facts_input=facts_input,
                narrative_result=narrative_result,
            )
        )
        context.warnings.extend(qa_warnings)
        pdf_relative_path, pdf_warnings = self._build_quick_pdf(
            case_id, context, model, markdown
        )
        context.warnings.extend(pdf_warnings)
        service_markdown, service_relative_path, service_warnings = (
            self._build_service_report(case_id, context)
        )
        context.warnings.extend(service_warnings)
        analysis = self.store.get_analysis_result(
            case_id,
            context.collection_run_id,
        )
        if analysis is None:
            raise ValueError("The selected run has no analytics result")
        narrative = model.narrative
        report = self.store.save_generated_report(
            case_id=case_id,
            collection_run_id=context.collection_run_id,
            analysis_id=analysis.id,
            title=f"Экспресс-оценка участка {context.parcel.cadastral_number}",
            template_id=QUICK_REPORT_TEMPLATE_ID,
            template_version=context.methodology_version,
            template_sha256=methodology_sha256(),
            markdown=markdown,
            model_name=(
                narrative.model_name
                if narrative is not None and narrative.source == "llm"
                else None
            ),
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
            pdf_relative_path=pdf_relative_path,
            service_relative_path=service_relative_path,
            service_markdown=service_markdown,
            qa_relative_path=qa_relative_path,
            warnings=context.warnings,
        )

    @staticmethod
    def _quick_narrative(result: EstablishedFactsNarrative) -> QuickNarrative:
        return QuickNarrative(
            established_summary=result.text or None,
            source=result.source,
            model_name=result.model_name,
            prompt_version=result.prompt_version,
            input_sha256=result.input_sha256,
            latency_ms=result.latency_ms,
            attempts=result.attempts,
        )

    def _build_qa_report(
        self,
        case_id: str,
        context: QuickReportContext,
        model: QuickReportModel,
        markdown: str,
        *,
        map_lines: list[str] | None,
        facts_input: EstablishedFactsInput | None,
        narrative_result: EstablishedFactsNarrative | None,
    ) -> tuple[
        str | None,
        list[str],
        str,
        QuickReportModel,
        EstablishedFactsNarrative | None,
    ]:
        """QA-артефакт рядом с quick-отчётом: валидатор + критик (§61 ТЗ).

        Детерминированный валидатор — всегда; LLM-критик — если задан
        ``critic`` в конструкторе. При needs_revision критика и включённом
        генераторе выполняется один раунд targeted revision (§74), суженный
        до секции «Что уже удалось установить»: принимается только valid
        LLM-результат и только после повторного pass валидатора; score,
        вывод и смыслы факторов revision не затрагивает. Возвращает (путь
        к QA-артефакту, warnings, итоговый markdown, итоговая модель,
        итоговый narrative). Любой сбой деградирует в warning: отчёт
        сохраняется независимо (§77).
        """
        from terralogic_engine.qa import run_critic, validate_quickreport

        warnings: list[str] = []
        try:
            validator_result = validate_quickreport(model, markdown, context=context)
        except Exception as exc:  # noqa: BLE001 — QA не ломает конвейер
            return (
                None,
                [f"QA-валидатор не отработал: {exc}"],
                markdown,
                model,
                narrative_result,
            )
        critic_result = run_critic(
            self._critic, model, markdown, validator_result, warnings=warnings
        )
        # Порог содержательности: needs_revision без error/critical — это
        # придирки уровня warning; эффективный статус понижается до pass,
        # сырой статус критика остаётся виден в артефакте.
        critic_effective_pass = (
            critic_result is not None
            and critic_result.status == "needs_revision"
            and not any(
                issue.severity in ("error", "critical")
                for issue in critic_result.issues
            )
        )

        revision_note = "не требовалась"
        if (
            critic_result is not None
            and critic_result.status == "needs_revision"
            and not critic_effective_pass
            and self._narrative_generator is not None
            and facts_input is not None
        ):
            # Revision — только по содержательным замечаниям (error/critical)
            # секции «Что уже удалось установить»; warning фиксируется без
            # переписывания, другие секции revision не затрагивает.
            relevant = [
                issue
                for issue in critic_result.issues
                if issue.severity in ("error", "critical")
                and issue.section.startswith("Что уже удалось установить")
            ]
            if relevant:
                revised = self._narrative_generator.generate_established_facts(
                    facts_input, issues=relevant
                )
                if revised is not None:
                    warnings.extend(revised.warnings)
                if revised is not None and revised.source == "llm" and revised.text:
                    revised_model = model.model_copy(
                        update={"narrative": self._quick_narrative(revised)}
                    )
                    revised_markdown = render_quick_model(
                        revised_model,
                        map_lines=map_lines,
                        zone_regulations=context.zone_regulations,
                    )
                    recheck = validate_quickreport(
                        revised_model, revised_markdown, context=context
                    )
                    if recheck.status == "pass":
                        model, markdown, validator_result = (
                            revised_model,
                            revised_markdown,
                            recheck,
                        )
                        narrative_result = revised
                        revision_note = "принята (валидатор pass)"
                    else:
                        revision_note = "отклонена: валидатор fail после revision"
                        warnings.append(
                            "QA revision отклонена валидатором — отчёт без изменений"
                        )
                else:
                    # fallback/failure — сохраняется прежний текст
                    revision_note = "отклонена: генератор не вернул пригодный LLM-текст"
            else:
                revision_note = (
                    "не выполнялась: нет error/critical в секции "
                    "«Что уже удалось установить»"
                )

        narrative_status = "отключён (детерминированный текст)"
        if self._narrative_generator is not None:
            if narrative_result is not None and narrative_result.source == "llm":
                narrative_status = "llm (применён)"
            else:
                narrative_status = "deterministic_fallback"
        lines = [
            f"# QA отчёта — {context.parcel.cadastral_number}",
            "",
            (
                "Результат проверки экспресс-отчёта: детерминированный"
                " валидатор (равенство полей) и LLM-критик (семантика,"
                " read-only)."
            ),
            "",
            f"Run {context.collection_run_id} · Методика {context.methodology_version}",
            "",
            f"## Валидатор: {validator_result.status}",
            "",
        ]
        if validator_result.issues:
            lines.extend(
                f"- [{issue.severity}] `{issue.rule_id}` ({issue.section}):"
                f" {issue.message}"
                for issue in validator_result.issues
            )
        else:
            lines.append("Проблем не найдено.")
        critic_status = (
            "недоступен" if critic_result is None else critic_result.status
        )
        if critic_effective_pass:
            critic_status += " → pass (только warning/info, порог severity)"
        lines.extend(["", f"## Критик: {critic_status}", ""])
        if critic_result is not None and critic_result.issues:
            lines.extend(
                f"- [{issue.severity}] {issue.rule_id} ({issue.section}):"
                f" {issue.message}"
                + (
                    f" Действие: {issue.suggested_action}"
                    if issue.suggested_action
                    else ""
                )
                for issue in critic_result.issues
            )
        elif critic_result is not None:
            lines.append("Проблем не найдено.")
        lines.extend(["", f"## Narrative: {narrative_status}", ""])
        if narrative_result is not None:
            # Observability прогона генератора; chain-of-thought и сырой
            # промпт не сохраняются.
            lines.append(f"- источник: {narrative_result.source}")
            if narrative_result.model_name:
                lines.append(f"- модель: {narrative_result.model_name}")
            if narrative_result.prompt_version:
                lines.append(f"- версия промпта: {narrative_result.prompt_version}")
            lines.append(f"- sha256 входа: `{narrative_result.input_sha256[:12]}`")
            if narrative_result.latency_ms is not None:
                lines.append(f"- latency: {narrative_result.latency_ms:.0f} мс")
            lines.append(f"- попыток: {narrative_result.attempts}")
            if narrative_result.usage:
                usage = ", ".join(
                    f"{key}={value}"
                    for key, value in sorted(narrative_result.usage.items())
                )
                lines.append(f"- usage: {usage}")
            if narrative_result.warnings:
                lines.append("- предупреждения генератора:")
                lines.extend(
                    f"  - {warning}" for warning in narrative_result.warnings
                )
        lines.extend(["", f"## Revision: {revision_note}", ""])
        qa_markdown = "\n".join(lines) + "\n"
        try:
            relative_path = self.store.save_case_artifact(
                case_id=case_id,
                kind="reports",
                filename=f"quickreport-{context.collection_run_id}-qa.md",
                payload=qa_markdown.encode("utf-8"),
            )
        except Exception as exc:  # noqa: BLE001 — QA не ломает конвейер
            return (
                None,
                [*warnings, f"QA-артефакт не сохранён: {exc}"],
                markdown,
                model,
                narrative_result,
            )
        if validator_result.status == "fail":
            warnings.append(
                "QA-валидатор: отчёт не прошёл проверку — см. QA-артефакт"
            )
        if critic_result is not None and critic_result.status == "needs_revision":
            if critic_effective_pass:
                warnings.append(
                    "QA-критик: needs_revision без error/critical — понижено до pass"
                )
            else:
                warnings.append("QA-критик: needs_revision — см. QA-артефакт")
        return relative_path, warnings, markdown, model, narrative_result

    def _build_service_report(
        self, case_id: str, context: QuickReportContext
    ) -> tuple[str | None, str | None, list[str]]:
        """Сервисный отчёт скоринга (протокол для разработчиков) рядом с quick-отчётом.

        Любой сбой деградирует в предупреждение — основной отчёт при этом
        сохраняется всегда.
        """
        try:
            from terralogic_engine.quickreport.service import render_service_report

            markdown = render_service_report(context)
            relative_path = self.store.save_case_artifact(
                case_id=case_id,
                kind="reports",
                filename=f"quickreport-{context.collection_run_id}-service.md",
                payload=markdown.encode("utf-8"),
            )
        except Exception as exc:  # noqa: BLE001 — сервисный отчёт не ломает конвейер
            return None, None, [f"сервисный отчёт не построен: {exc}"]
        return markdown, relative_path, []

    def _build_quick_pdf(
        self,
        case_id: str,
        context: QuickReportContext,
        model: QuickReportModel,
        markdown: str,
    ) -> tuple[str | None, list[str]]:
        """PDF-версия quick-отчёта рядом с Markdown-артефактом.

        Модель — финальная (после narrative и QA-revision), верстка блоков
        берёт из неё вердикт и идентификаторы. Любой сбой (нет extra 'pdf',
        ошибка рендера) деградирует в предупреждение — Markdown-отчёт при
        этом сохраняется всегда.
        """
        from terralogic_engine.quickreport.pdf import model_to_pdf

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            store_root = getattr(self.store, "root", None)
            base_dir = (
                Path(store_root) / "cases" / case_id / "reports"
                if store_root is not None
                else Path.cwd()
            )
            warnings = model_to_pdf(model, markdown, tmp_path, base_dir=base_dir)
            if warnings:
                return None, warnings
            relative_path = self.store.save_case_artifact(
                case_id=case_id,
                kind="reports",
                filename=f"quickreport-{context.collection_run_id}.pdf",
                payload=tmp_path.read_bytes(),
            )
        finally:
            tmp_path.unlink(missing_ok=True)
        return relative_path, []

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
