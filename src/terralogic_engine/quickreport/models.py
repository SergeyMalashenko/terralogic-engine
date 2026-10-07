"""Типизированные контракты QuickReport: контекст, факторы, вердикт."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class QuickFactor(BaseModel):
    """Строка таблицы «Факторы, требующие внимания»."""

    rule_id: str
    title: str
    zone_name: str
    coverage_percent: float = Field(ge=0, le=100)
    coverage_word: str
    guidance: str
    action: str | None = None
    informational: bool = False


class QuickVerdict(BaseModel):
    """Скоринг участка и градация из scoring_rules.yaml."""

    score: int
    grade_key: str
    grade_title: str
    grade_summary: str


class QuickPassport(BaseModel):
    """Паспорт участка для QuickReport."""

    cadastral_number: str
    address: str | None = None
    status: str | None = None
    area_m2: float | None = None
    land_category: str | None = None
    permitted_use: str | None = None
    territorial_zone: str | None = None
    cadastral_value_rub: float | None = None


class QuickSurroundings(BaseModel):
    """Окружение: ближайшие объекты + информационный блок коммуникаций."""

    lines: list[str] = Field(default_factory=list)
    communications_note: str | None = None


class QuickZoneUse(BaseModel):
    """Строка ВРИ из таблицы ПЗЗ для отчёта (без сырого текста)."""

    code: str | None = None
    name: str | None = None
    area_min: int | float | str | None = None
    area_max: int | float | str | None = None
    building_percentage: str | None = None
    margin: int | float | str | None = None


class QuickZoneRegulations(BaseModel):
    """Территориальная зона участка и её ВРИ по документу ПЗЗ."""

    zone_code: str
    doc_number: str | None = None
    doc_version_date: str | None = None
    total_uses: int = 0
    housing_uses: list[QuickZoneUse] = Field(default_factory=list)


class QuickReportContext(BaseModel):
    """Полный контекст QuickReport: паспорт, вердикт, факторы, окружение."""

    case_id: str
    collection_run_id: str
    collected_at: datetime
    methodology_version: str
    parcel: QuickPassport
    verdict: QuickVerdict
    factors: list[QuickFactor] = Field(default_factory=list)
    zone_regulations: QuickZoneRegulations | None = None
    surroundings: QuickSurroundings
    warnings: list[str] = Field(default_factory=list)


class QuickReportResult(BaseModel):
    """Результат prepare_quickreport: Markdown и метаданные сохранённого отчёта."""

    report_id: str
    case_id: str
    collection_run_id: str
    analysis_id: str
    methodology_version: str
    title: str
    relative_path: str
    content_sha256: str
    generated_at: datetime
    markdown: str
    warnings: list[str] = Field(default_factory=list)
