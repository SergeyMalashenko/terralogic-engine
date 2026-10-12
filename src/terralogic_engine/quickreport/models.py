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
    """Вердикт методики 6.1: балл (None — не рассчитан), статус, причины."""

    score: int | None
    status_key: str
    status_title: str
    status_summary: str
    preliminary: bool = False
    reasons: list[str] = Field(default_factory=list)
    explanation: str = ""
    geometry_note: str | None = None


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
    protocol: "QuickProtocol | None" = None


class QuickProtocolFactor(BaseModel):
    """Протокольная запись о классифицированной зоне (после слияния дублей)."""

    group_key: str
    group_title: str
    factor_title: str
    zone_name: str  # сырое наименование представителя слияния
    display_name: str
    source: str
    coverage_percent: float
    deduction: float


class QuickProtocolGroup(BaseModel):
    """Вычет группы ограничений: сырая сумма и после потолка group_cap."""

    key: str
    title: str
    raw_deduction: float
    deduction: float
    zone_names: list[str] = Field(default_factory=list)


class QuickProtocolComm(BaseModel):
    """Один вопрос неопределённости коммуникаций (п. 4.2)."""

    question: str
    state: str
    weight: float


class QuickProtocolArea(BaseModel):
    """Проверка минимальной площади (п. 4.3): вход, сравнение, результат A."""

    status: str  # computed | min_not_set | no_regulations | applicability_unknown | below_minimum
    deduction: float | None
    parcel_area_m2: float | None
    s_min_m2: float | None = None
    target_code: str | None = None
    target_name: str | None = None
    note: str | None = None


class QuickProtocolGeometry(BaseModel):
    """Геометрическая проверка (п. 4.4): C и протокольная строка теста."""

    c: int
    detail: str


class QuickProtocolCheck(BaseModel):
    """Результат одной обязательной проверки ключевых зон (п. 4.1)."""

    name: str
    result: str


class QuickProtocolSource(BaseModel):
    """Происхождение одного снапшота источника."""

    source: str
    snapshot_id: str
    retrieved_at: datetime
    adapter_version: str
    content_sha256: str


class QuickProtocol(BaseModel):
    """Внутренний протокол расчёта методики 6.1 (сервисный отчёт разработчика)."""

    methodology_sha256: str
    formula: str
    base_score: int
    factors: list[QuickProtocolFactor] = Field(default_factory=list)
    unmatched_zones: list[str] = Field(default_factory=list)
    classify_notes: list[str] = Field(default_factory=list)
    conservative: bool = False
    groups: list[QuickProtocolGroup] = Field(default_factory=list)
    restrictions_total: float = 0.0
    communications: list[QuickProtocolComm] = Field(default_factory=list)
    u_total: float = 0.0
    area: QuickProtocolArea | None = None
    geometry: QuickProtocolGeometry | None = None
    g: float | None = None
    raw_score: int | None = None
    score: int | None = None
    status_key: str
    status_rule: str
    preliminary: bool
    preliminary_flags: list[str] = Field(default_factory=list)
    stop_reasons: list[str] = Field(default_factory=list)
    suspend_reasons: list[str] = Field(default_factory=list)
    checks: list[QuickProtocolCheck] = Field(default_factory=list)
    sources: list[QuickProtocolSource] = Field(default_factory=list)
    target_use_prefix: str | None = None


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
    map_relative_path: str | None = None
    pdf_relative_path: str | None = None
    service_relative_path: str | None = None
    service_markdown: str | None = None
    warnings: list[str] = Field(default_factory=list)
