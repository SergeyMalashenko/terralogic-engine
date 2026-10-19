"""PDF-версия QuickReport: HTML-вёрстка QuickReportModel (weasyprint).

PDF — второй детерминированный рендер модели наряду с Markdown
(render.py): структурные блоки (шапка, блок КН/адреса, оценка, главный
вывод, карта с ограничениями отчёта) верстаются из полей модели,
прозаические секции — через markdown_lib из того же Markdown. Дизайн-
токены — assets/report.css (фиксированная типографика, без shrink-to-fit),
шрифт Carlito — assets/fonts/carlito (@font-face, SIL OFL), колонтитулы —
@page margin boxes. Опциональный extra ``pdf`` (markdown + weasyprint);
при отсутствии библиотек или сбое рендера отчёт не падает — возвращается
предупреждение. Ссылки на изображения в Markdown относительные
(``../maps/…``) — базовым каталогом служит каталог отчётов кейса.
"""

from __future__ import annotations

import logging
import re
from html import escape
from pathlib import Path

from .model import QuickReportModel
from .render import SCORE_SCALE

LOGGER = logging.getLogger(__name__)

_ASSETS = Path(__file__).resolve().parent / "assets"
_BRAND_LINE = "TerraLogicX · экспресс-оценка участка"

_H2_LINE_RE = re.compile(r"^## (.+?)\s*$", flags=re.MULTILINE)

# CSS-классы секций по детерминированным заголовкам из render.py.
_SECTION_CLASS = {
    "Что уже удалось установить": "established",
    "Факторы, требующие внимания": "factors",
    "Паспорт участка": "passport",
    "Территориальная зона и разрешённое использование": "zone",
    "Окружение": "surroundings",
    "Что ещё нужно проверить": "missing",
    "Следующий шаг": "marketing",
}
_VERDICT_TITLE_PREFIX = "Оценка"
_MAP_TITLE = "Схема расположения зон"
_LIMITATIONS_TITLE = "Ограничения отчёта"


def _md(text: str) -> str:
    """Markdown → HTML; ImportError всплывает в model_to_pdf (extra 'pdf')."""
    import markdown as markdown_lib

    return markdown_lib.markdown(text, extensions=["tables", "sane_lists"])


def _md_inline(text: str) -> str:
    """Markdown одной строки без обёртки <p>."""
    html = _md(text)
    if html.startswith("<p>") and html.endswith("</p>"):
        html = html[3:-4]
    return html


def _split_markdown(markdown_text: str) -> tuple[str, list[tuple[str, str]]]:
    """Преамбула (до первого h2) + упорядоченные (заголовок, тело) секции."""
    matches = list(_H2_LINE_RE.finditer(markdown_text))
    if not matches:
        return markdown_text, []
    preamble = markdown_text[: matches[0].start()]
    sections = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown_text)
        sections.append(
            (match.group(1).strip(), markdown_text[match.end() : end].strip())
        )
    return preamble, sections


def _parse_preamble(preamble: str) -> tuple[str, str, str, str]:
    """Заголовок, подзаголовок, meta-строка и строка КН преамбулы render.py."""
    lines = [line.strip() for line in preamble.splitlines() if line.strip()]
    title = lines[0].lstrip("# ").strip() if lines else ""
    subtitle = lines[1] if len(lines) > 1 else ""
    meta = lines[2] if len(lines) > 2 else ""
    kn_line = lines[3] if len(lines) > 3 else ""
    return title, subtitle, meta, kn_line


def _verdict_html(model: QuickReportModel) -> str:
    """Блок оценки: крупное число + статус; главный вывод — акцентный блок."""
    verdict = model.status
    score_cell = (
        f'{verdict.score}<span class="score-max">/{SCORE_SCALE}</span>'
        if verdict.score is not None
        else "—"
    )
    prelim = (
        ' <span class="prelim">(предварительная)</span>'
        if verdict.preliminary
        else ""
    )
    return (
        '<table class="verdict"><tr>'
        f'<td class="score-cell">{score_cell}</td>'
        f'<td><div class="verdict-title">{escape(verdict.status_title)}'
        f"{prelim}</div></td>"
        "</tr></table>"
        f'<div class="conclusion">{escape(verdict.status_summary)}</div>'
    )


def _verdict_remainder(body: str, summary: str) -> str:
    """Тело секции оценки без абзаца главного вывода (он в accent-блоке)."""
    if body.startswith(summary):
        return body[len(summary) :].lstrip()
    return body


def _compose_html(model: QuickReportModel, markdown_text: str) -> str:
    """HTML-документ отчёта: верстка блоков модели + markdown секций."""
    preamble, sections = _split_markdown(markdown_text)
    title, subtitle, meta, kn_line = _parse_preamble(preamble)
    parts = [f'<div class="brand">{escape(_BRAND_LINE)}</div>']
    if title:
        parts.append(f"<h1>{escape(title)}</h1>")
    if subtitle:
        parts.append(f'<div class="subtitle">{escape(subtitle)}</div>')
    if meta:
        parts.append(f'<div class="meta">{escape(meta)}</div>')
    if kn_line:
        parts.append(f'<div class="kn-block">{_md_inline(kn_line)}</div>')
    parts.append(_verdict_html(model))

    map_open = False
    for section_title, body in sections:
        if section_title.startswith(_VERDICT_TITLE_PREFIX):
            remainder = _verdict_remainder(body, model.status.status_summary)
            if remainder:
                parts.append(f'<div class="verdict-rest">{_md(remainder)}</div>')
            continue
        if section_title == _MAP_TITLE:
            map_html = _md(body)
            # Подпись подложки (детерминированный префикс из service.py) —
            # второстепенный шрифт 8pt.
            map_html = map_html.replace(
                "<p>Подложка", '<p class="map-caption">Подложка'
            )
            parts.append(
                f'<div class="section map-block"><h2>{escape(section_title)}</h2>'
                f"{map_html}"
            )
            map_open = True
            continue
        if section_title == _LIMITATIONS_TITLE:
            parts.append(
                '<div class="limitations">'
                f'<div class="limitations-title">{escape(section_title)}</div>'
                f"{_md(body)}</div>"
            )
            if map_open:
                parts.append("</div>")
                map_open = False
            continue
        if map_open:
            parts.append("</div>")
            map_open = False
        css_class = _SECTION_CLASS.get(section_title, "generic")
        parts.append(
            f'<div class="section s-{css_class}">'
            f"<h2>{escape(section_title)}</h2>{_md(body)}</div>"
        )
    if map_open:
        parts.append("</div>")
    return "".join(parts)


def model_to_pdf(
    model: QuickReportModel,
    markdown_text: str,
    out_path: Path,
    *,
    base_dir: Path,
) -> list[str]:
    """Рендерит модель отчёта в PDF; возвращает предупреждения.

    ``base_dir`` — каталог, относительно которого резолвятся ссылки на
    изображения (каталог reports/ кейса: ссылка ``../maps/x.png`` укажет
    на артефакт карты). Дизайн-токены читаются из assets/report.css,
    относительные url шрифтов — от каталога assets.
    """
    try:
        import markdown as markdown_lib  # noqa: F401
        import weasyprint
    except ImportError:
        return ["pdf не построен: не установлен extra 'pdf' (markdown, weasyprint)"]
    try:
        body = _compose_html(model, markdown_text)
        html = (
            '<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">'
            f"<title>TerraLogicX</title></head><body>{body}</body></html>"
        )
        css = (_ASSETS / "report.css").read_text(encoding="utf-8")
        weasyprint.HTML(string=html, base_url=str(base_dir)).write_pdf(
            out_path,
            stylesheets=[weasyprint.CSS(string=css, base_url=str(_ASSETS))],
        )
    except Exception as exc:
        LOGGER.warning("PDF quickreport не построен", exc_info=exc)
        return [f"pdf не построен: {exc}"]
    return []
