"""Продуктовая модель QuickReport (§40 ТЗ): что именно видит пользователь.

`QuickReportContext` — семантический контекст расчёта (факты, скоринг,
протокол). `QuickReportModel` — фиксированная структура продукта для
рендера: секции отчёта в продуктовом порядке, с разделением происхождения
(SOURCE/DERIVED/DOCUMENT/LEGAL/STATIC) и состояний (KNOWN/DERIVED/TO_VERIFY).

Сборка `build_quick_model` — чистая детерминированная функция контекста;
маркетинг присоединяется здесь (presentation layer) и никогда не попадает
в ReportContext (§37 ТЗ). LLM на этом этапе нет.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from .models import (
    QuickFactor,
    QuickMissingItem,
    QuickPassport,
    QuickReportContext,
    QuickSurroundings,
    QuickVerdict,
)
from .rules import MarketingConfig, MarketingService

# Статический disclaimer (§36 ТЗ): юридически значимый текст меняется только
# кодом, LLM его не трогает.
REPORT_LIMITATIONS = (
    "Отчёт подготовлен по данным публичных источников. Полнота и актуальность "
    "сведений зависят от организаций, которые ведут и обновляют эти источники, "
    "и находятся вне контроля TerraLogicX. Отчёт содержит результаты "
    "предварительного анализа и не заменяет юридическое, кадастровое или "
    "градостроительное заключение."
)


class QuickIdentity(BaseModel):
    """Детерминированные метаданные отчёта (§24 ТЗ)."""

    case_id: str
    collection_run_id: str
    collected_at: datetime
    methodology_version: str
    cadastral_number: str
    address: str | None = None


class QuickMarketingService(BaseModel):
    """Одна услуга блока «Следующий шаг» (snapshot MarketingService)."""

    key: str
    name: str
    description: str
    price_rub: int | None = None
    price_from_rub: int | None = None
    credit_rub: int | None = None

    @classmethod
    def of(cls, service: MarketingService) -> QuickMarketingService:
        return cls(
            key=service.key,
            name=service.name,
            description=service.description,
            price_rub=service.price_rub,
            price_from_rub=service.price_from_rub,
            credit_rub=service.credit_rub,
        )


class QuickMarketing(BaseModel):
    """Коммерческий блок отчёта: значения только из MarketingConfig."""

    services: list[QuickMarketingService]
    cta: str
    sha256: str


class QuickNarrative(BaseModel):
    """Narrative раздела «Что уже удалось установить» + observability.

    Единственный LLM-слот отчёта (§62–63 ТЗ): текст и метаданные прогона
    NarrativeGenerator (источник, модель, версия промпта, sha256 входа,
    latency, attempts). Балл, статус, вывод и смыслы факторов остаются
    детерминированными полями модели — narrative на них не влияет.
    """

    established_summary: str | None = None  # §27 — текст раздела
    source: Literal["llm", "deterministic_fallback"] = "deterministic_fallback"
    model_name: str | None = None
    prompt_version: str | None = None
    input_sha256: str | None = None
    latency_ms: float | None = None
    attempts: int = 0


class QuickReportModel(BaseModel):
    """Фиксированная продуктовая структура QuickReport (§23, §40 ТЗ)."""

    identity: QuickIdentity
    score: int | None
    status: QuickVerdict
    main_conclusion: str
    established_facts: list[str] = Field(default_factory=list)
    attention_factors: list[QuickFactor] = Field(default_factory=list)
    parcel_passport: QuickPassport
    environment: QuickSurroundings
    missing_information: list[QuickMissingItem] = Field(default_factory=list)
    limitations: str = REPORT_LIMITATIONS
    marketing: QuickMarketing | None = None
    narrative: QuickNarrative | None = None


def _established_facts(context: QuickReportContext) -> list[str]:
    """Краткая сводка результатов, а не повтор паспорта и таблицы факторов.

    Полные реквизиты остаются в parcel_passport, названия зон и их охват — в
    attention_factors. TO_VERIFY сюда не попадает: это отдельный блок
    missing_information.
    """
    parcel = context.parcel
    facts: list[str] = []

    cadastral: list[str] = []
    if parcel.area_m2 is not None:
        cadastral.append(f"площадь {parcel.area_m2:,.0f} м²".replace(",", " "))
    if parcel.permitted_use:
        cadastral.append(f"разрешённое использование — {parcel.permitted_use}")
    if cadastral:
        facts.append(f"По кадастровым сведениям: {'; '.join(cadastral)}.")

    if parcel.territorial_zone:
        zoning = "Территориальная зона участка установлена"
        if context.zone_regulations is not None:
            zoning += "; регламент ПЗЗ получен"
        facts.append(f"{zoning}.")
    elif context.zone_regulations is not None:
        facts.append("Регламент территориальной зоны получен из ПЗЗ.")

    zone_factors = [factor for factor in context.factors if not factor.informational]
    if zone_factors:
        facts.append(
            "Пространственный анализ выполнен: факторов, требующих внимания, "
            f"— {len(zone_factors)}."
        )
    elif (
        context.protocol is not None
        and not context.protocol.unmatched_zones
        and any(source.source == "nspd" for source in context.protocol.sources)
    ):
        facts.append(
            "Пространственный анализ выполнен: факторов, требующих внимания, "
            "не выявлено."
        )

    if context.protocol is not None and context.protocol.geometry is not None:
        facts.append("Выполнен предварительный геометрический тест конфигурации участка.")
    return facts


def build_quick_model(
    context: QuickReportContext,
    marketing: MarketingConfig | None = None,
) -> QuickReportModel:
    """Собирает продуктовую модель отчёта из контекста (без store и LLM).

    ``marketing`` — статическая конфигурация (load_marketing); None — блок
    «Следующий шаг» в отчёт не включается.
    """
    return QuickReportModel(
        identity=QuickIdentity(
            case_id=context.case_id,
            collection_run_id=context.collection_run_id,
            collected_at=context.collected_at,
            methodology_version=context.methodology_version,
            cadastral_number=context.parcel.cadastral_number,
            address=context.parcel.address,
        ),
        score=context.verdict.score,
        status=context.verdict,
        main_conclusion=context.verdict.status_summary,
        established_facts=_established_facts(context),
        attention_factors=list(context.factors),
        parcel_passport=context.parcel,
        environment=context.surroundings,
        missing_information=list(context.missing),
        marketing=(
            QuickMarketing(
                services=[QuickMarketingService.of(s) for s in marketing.services],
                cta=marketing.cta,
                sha256=marketing.sha256,
            )
            if marketing is not None
            else None
        ),
    )
