"""Типизированные контракты QuickReport: контекст, факторы, вердикт."""

from __future__ import annotations

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


class QuickReportContext(BaseModel):
    """Полный контекст QuickReport: паспорт, вердикт, факторы, окружение."""

    case_id: str
    collection_run_id: str
    methodology_version: str
    parcel: QuickPassport
    verdict: QuickVerdict
    factors: list[QuickFactor] = Field(default_factory=list)
    surroundings: QuickSurroundings
    warnings: list[str] = Field(default_factory=list)
