"""SHORT/NORMAL/LONG regression baseline for QuickReport Markdown and PDF."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from terralogic_engine.quickreport.pdf import _PAGE_CSS, markdown_to_pdf

from .quickreport_fixtures import (
    FIXTURE_EXPECTATIONS,
    FIXTURE_KINDS,
    LONG_ADDRESS,
    build_fixture_context,
    render_fixture_markdown,
)

_GOLDEN_DIR = Path(__file__).parent / "fixtures" / "quickreport"
_PRODUCT_SECTIONS = (
    "# Экспресс-оценка земельного участка",
    "## Что уже удалось установить",
    "## Факторы, требующие внимания",
    "## Паспорт участка",
    "## Территориальная зона и разрешённое использование",
    "## Окружение",
    "## Что ещё нужно проверить",
    "## Схема расположения зон",
    "## Ограничения отчёта",
    "## Следующий шаг",
)


@pytest.mark.parametrize("kind", FIXTURE_KINDS)
def test_markdown_golden_and_product_contract(kind: str) -> None:
    markdown = render_fixture_markdown(kind)
    golden = (_GOLDEN_DIR / f"{kind}.md").read_text(encoding="utf-8")
    assert markdown == golden
    assert render_fixture_markdown(kind) == markdown

    context = build_fixture_context(kind)
    expected_score, expected_status, expected_factors = FIXTURE_EXPECTATIONS[kind]
    assert context.verdict.score == expected_score
    assert context.verdict.status_key == expected_status
    factors = [factor for factor in context.factors if not factor.informational]
    assert len(factors) == expected_factors

    positions = [markdown.index(section) for section in _PRODUCT_SECTIONS]
    assert positions == sorted(positions)
    assert markdown.index("## Оценка участка") < positions[1]
    assert "30 000 ₽" in markdown
    assert "от 45 000 ₽" in markdown
    assert "не заменяет юридическое, кадастровое" in markdown

    if kind == "normal":
        assert context.parcel.permitted_use == "ИЖС"
        assert context.parcel.vri is not None
        assert context.parcel.vri.code == "2.1"
        assert context.parcel.vri_origin == "LEGAL"
        assert "ИЖС · ВРИ 2.1" in markdown


def test_pdf_smoke_page_count_and_long_content(tmp_path: Path) -> None:
    pytest.importorskip("markdown")
    pytest.importorskip("weasyprint")

    outputs: dict[str, Path] = {}
    for kind in FIXTURE_KINDS:
        output = tmp_path / f"{kind}.pdf"
        warnings = markdown_to_pdf(
            render_fixture_markdown(kind), output, base_dir=tmp_path
        )
        assert warnings == []
        payload = output.read_bytes()
        assert payload.startswith(b"%PDF")
        assert len(payload) > 1000
        outputs[kind] = output

    pdfinfo = shutil.which("pdfinfo")
    if pdfinfo is not None:
        pages: dict[str, int] = {}
        for kind, output in outputs.items():
            result = subprocess.run(
                [pdfinfo, str(output)],
                capture_output=True,
                text=True,
                check=True,
            )
            match = re.search(r"^Pages:\s+(\d+)$", result.stdout, re.MULTILINE)
            assert match is not None
            pages[kind] = int(match.group(1))
            assert pages[kind] >= 1
        assert pages["long"] >= pages["short"]

    pdftotext = shutil.which("pdftotext")
    if pdftotext is not None:
        result = subprocess.run(
            [pdftotext, "-raw", str(outputs["long"]), "-"],
            capture_output=True,
            text=True,
            check=True,
        )
        # raw сохраняет содержимое ячейки последовательно; -layout вставляет
        # текст соседних колонок между строками перенесённого названия зоны.
        unwrapped = re.sub(r"-[ \t]*\n[ \t]*", "-", result.stdout)
        text = " ".join(unwrapped.split())
        assert LONG_ADDRESS in text
        long_context = build_fixture_context("long")
        for factor in long_context.factors:
            if not factor.informational:
                assert factor.zone_name in text
        assert "Ограничения отчёта" in text
        assert "Следующий шаг" in text
        assert "менеджер свяжется с вами" in text


def test_fixed_typography_baseline() -> None:
    assert '@page { size: A4; margin: 18mm 16mm; }' in _PAGE_CSS
    assert 'font-family: "DejaVu Sans"' in _PAGE_CSS
    assert "font-size: 10.5pt; line-height: 1.45;" in _PAGE_CSS
    assert "h1 { font-size: 17pt;" in _PAGE_CSS
    assert "h2 { font-size: 13pt;" in _PAGE_CSS
    assert "h3 { font-size: 11.5pt;" in _PAGE_CSS
    assert "table { border-collapse: collapse; width: 100%; font-size: 9.5pt; }" in _PAGE_CSS

    folded = _PAGE_CSS.casefold()
    assert "shrink" not in folded
    assert "scale(" not in folded
    assert "font-size: calc" not in folded
    assert "zoom:" not in folded
