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
    """Пользовательские формулировки поверх детерминированного содержания.

    Заполняется только LLM-writer'ом (§62–63 ТЗ) и только текстом: факты,
    балл, статус и паспорт остаются детерминированными полями модели.
    """

    main_conclusion: str | None = None  # §26 — формулировка вывода
    established_summary: str | None = None  # §27 — интерпретация фактов
    factor_meanings: dict[str, str] = Field(default_factory=dict)  # §31
    source: Literal["writer"] = "writer"


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
    """«Что уже удалось установить» (§27 ТЗ) — только факты контекста.

    KNOWN — значения паспорта, подтверждённые источником (и LEGAL-разрешение
    ВРИ); DERIVED — покрытия зон из геометрического анализа. TO_VERIFY сюда
    не попадает: это отдельный блок missing_information.
    """
    parcel = context.parcel
    facts: list[str] = []
    if parcel.address:
        facts.append(f"Адрес (местоположение): {parcel.address}")
    if parcel.area_m2 is not None:
        facts.append(f"Площадь по кадастру: {parcel.area_m2:,.0f} м²".replace(",", " "))
    if parcel.land_category:
        facts.append(f"Категория земель: {parcel.land_category}")
    if parcel.permitted_use:
        facts.append(f"Вид разрешённого использования: {parcel.permitted_use}")
    if parcel.vri is not None:
        facts.append(
            f"Код ВРИ по классификатору (П/0412, ред. {parcel.vri.revision_date}): "
            f"{parcel.vri.code} — {parcel.vri.name}"
        )
    if parcel.territorial_zone:
        facts.append(f"Территориальная зона: {parcel.territorial_zone}")
    if parcel.cadastral_value_rub is not None:
        facts.append(
            f"Кадастровая стоимость: {parcel.cadastral_value_rub:,.0f} ₽".replace(
                ",", " "
            )
        )
    for factor in context.factors:
        if factor.informational:
            continue
        facts.append(
            f"{factor.zone_name}: {factor.coverage_word} "
            f"({factor.coverage_percent:.0f}%)"
        )
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
