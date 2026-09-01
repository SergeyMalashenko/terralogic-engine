"""Versioned registry and structural validation for report templates."""

from __future__ import annotations

import re
from hashlib import sha256
from importlib.resources import files

from terralogic_acquisition.reporting.models import (
    ReportSectionTemplate,
    ReportTemplate,
)

DEFAULT_TEMPLATE_ID = "full_land_report"
DEFAULT_TEMPLATE_VERSION = "1.0"
_PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*[^{}]+\s*\}\}")


def _heading_position(markdown: str, heading: str) -> int | None:
    match = re.search(
        rf"(?m)^{re.escape(heading)}[ \t]*\r?$",
        markdown,
    )
    return match.start() if match is not None else None

_FULL_LAND_REPORT_SECTIONS = (
    ReportSectionTemplate(
        key="executive_summary",
        order=1,
        heading="## 1. Краткое резюме",
        purpose="Кратко изложить ключевые характеристики и ограничения участка.",
    ),
    ReportSectionTemplate(
        key="parcel_passport",
        order=2,
        heading="## 2. Паспорт участка",
        purpose="Привести основные сведения НСПД и вычисленную площадь.",
    ),
    ReportSectionTemplate(
        key="zouit",
        order=3,
        heading="## 3. ЗОУИТ и ограничения",
        purpose="Описать зоны, отношения и площади пересечения.",
    ),
    ReportSectionTemplate(
        key="natural_resources",
        order=4,
        heading="## 4. Леса и водные ресурсы",
        purpose="Описать пересечения и ближайшие природные объекты.",
    ),
    ReportSectionTemplate(
        key="social_infrastructure",
        order=5,
        heading="## 5. Социальная инфраструктура",
        purpose="Привести ближайшие объекты по категориям 2GIS.",
    ),
    ReportSectionTemplate(
        key="transport",
        order=6,
        heading="## 6. Транспортная инфраструктура",
        purpose="Описать транспортный инвентарь и классы дорог.",
    ),
    ReportSectionTemplate(
        key="limitations",
        order=7,
        heading="## 7. Качество и ограничения данных",
        purpose="Явно перечислить предупреждения и границы интерпретации.",
    ),
    ReportSectionTemplate(
        key="sources",
        order=8,
        heading="## 8. Источники",
        purpose="Перечислить снимки, даты и версии адаптеров.",
    ),
)

_GENERATION_RULES = (
    "Используй только факты и числа из ReportContext.",
    "Не вычисляй площади, доли и расстояния самостоятельно.",
    "Замени все заполнители вида {{ name }}; не оставляй их в результате.",
    "Для отсутствующего значения явно пиши «нет данных».",
    "Не найденный в области поиска объект не объявляй отсутствующим на местности.",
    "Расстояния transport_inventory измерены от центра поиска, а не от участка.",
    "Отделяй факты источников от интерпретации и не давай юридического заключения.",
    "Сохраняй заголовки и порядок обязательных разделов без изменений.",
)


class ReportTemplateNotFoundError(KeyError):
    """Raised when a requested template identity is absent from the registry."""


class ReportStructureError(ValueError):
    """Raised when generated Markdown violates its declared template."""


class ReportTemplateRegistry:
    """In-memory registry of immutable report templates."""

    def __init__(self, templates: tuple[ReportTemplate, ...]) -> None:
        self._templates = {
            (template.template_id, template.version): template
            for template in templates
        }
        if len(self._templates) != len(templates):
            raise ValueError("Report template identities must be unique")
        for template in templates:
            orders = [section.order for section in template.sections]
            if orders != sorted(set(orders)):
                raise ValueError(
                    f"Template {template.template_id!r} has invalid section order"
                )
            for section in template.sections:
                if (
                    section.required
                    and section.heading not in template.markdown_skeleton
                ):
                    raise ValueError(
                        f"Template skeleton is missing heading {section.heading!r}"
                    )
            digest = sha256(
                template.markdown_skeleton.encode("utf-8")
            ).hexdigest()
            if digest != template.content_sha256:
                raise ValueError(
                    f"Template {template.template_id!r} has an invalid SHA-256"
                )

    def get(self, template_id: str, version: str) -> ReportTemplate:
        try:
            return self._templates[(template_id, version)]
        except KeyError as exc:
            raise ReportTemplateNotFoundError(
                f"Unknown report template {template_id!r} version {version!r}"
            ) from exc

    def list(self) -> list[ReportTemplate]:
        return [self._templates[key] for key in sorted(self._templates)]

    def validate_markdown(
        self,
        template: ReportTemplate,
        markdown: str,
    ) -> None:
        required_positions = [
            (section.heading, _heading_position(markdown, section.heading))
            for section in template.sections
            if section.required
        ]
        missing = [
            heading
            for heading, position in required_positions
            if position is None
        ]
        if missing:
            raise ReportStructureError(
                "Report is missing required headings: " + "; ".join(missing)
            )
        positions = [
            position
            for _heading, position in required_positions
            if position is not None
        ]
        if positions != sorted(positions):
            raise ReportStructureError(
                "Required report headings are not in template order"
            )
        unresolved = sorted(set(_PLACEHOLDER_PATTERN.findall(markdown)))
        if unresolved:
            raise ReportStructureError(
                "Report contains unresolved template placeholders: "
                + "; ".join(unresolved)
            )


def create_default_template_registry() -> ReportTemplateRegistry:
    resource = files("terralogic_acquisition.reporting.templates").joinpath(
        "full_land_report_v1.md"
    )
    skeleton = resource.read_text(encoding="utf-8")
    template = ReportTemplate(
        template_id=DEFAULT_TEMPLATE_ID,
        version=DEFAULT_TEMPLATE_VERSION,
        name="Полный отчёт о земельном участке",
        description=(
            "Версионированный русский Markdown-отчёт по данным НСПД, OSM, "
            "2GIS и детерминированной пространственной аналитики."
        ),
        sections=_FULL_LAND_REPORT_SECTIONS,
        generation_rules=_GENERATION_RULES,
        markdown_skeleton=skeleton,
        content_sha256=sha256(skeleton.encode("utf-8")).hexdigest(),
    )
    return ReportTemplateRegistry((template,))
