"""Детерминированный offline-импорт классификатора ВРИ из DOCX приказа.

Разбор — stdlib (`zipfile` + `xml.etree`), без LLM: DOCX читается как zip,
таблица приложения ищется в word/document.xml по строке заголовка
(«наименование»/«описание»/«код»). Пустые строки пропускаются; строка
с пустыми наименованием и кодом, но с текстом описания считается
продолжением описания предыдущей позиции.
"""

from __future__ import annotations

import json
import re
import zipfile
from datetime import date
from pathlib import Path
from xml.etree import ElementTree

from .models import VriClassifier, VriEntry, VriSource

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
# коды ВРИ — от 2 до 4 уровней: «1.0», «2.1», «4.9.1», «4.9.1.4»
_CODE_RE = re.compile(r"^\d+(\.\d+){1,3}$")
_COLUMN_NUMBER_RE = re.compile(r"^\d+$")
# служебные строки редакционных примечаний КонсультантПлюс между позициями
_NOTE_RE = re.compile(r"^\((в\s+ред\.|введен[ыо]?)", re.IGNORECASE)
_HEADER_MARKERS = ("наименование", "описание", "код")

DEFAULT_ACT_NUMBER = "П/0412"
DEFAULT_ACT_DATE = date(2020, 11, 10)
DEFAULT_REVISION_DATE = date(2026, 6, 2)
DEFAULT_TITLE = (
    "Об утверждении классификатора видов разрешенного использования "
    "земельных участков"
)
DEFAULT_MIN_ENTRIES = 100


class VriImportError(ValueError):
    """DOCX не соответствует ожидаемой структуре классификатора ВРИ."""


def _cell_text(cell: ElementTree.Element) -> str:
    paragraphs: list[str] = []
    for para in cell.iter(f"{{{_W}}}p"):
        text = "".join(
            node.text or "" for node in para.iter(f"{{{_W}}}t")
        ).strip()
        if text:
            paragraphs.append(" ".join(text.split()))
    return "\n".join(paragraphs).strip()


def _table_rows(table: ElementTree.Element) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in table.findall(f"{{{_W}}}tr"):
        rows.append([_cell_text(cell) for cell in row.findall(f"{{{_W}}}tc")])
    return rows


def _header_columns(rows: list[list[str]]) -> dict[str, int] | None:
    """Индексы колонок по строке заголовка; None — это не таблица классификатора."""
    for row in rows:
        normalized = [" ".join(cell.casefold().split()) for cell in row]
        columns: dict[str, int] = {}
        for index, cell in enumerate(normalized):
            for marker in _HEADER_MARKERS:
                if marker in cell and marker not in columns:
                    columns[marker] = index
        if len(columns) == len(_HEADER_MARKERS):
            return columns
    return None


def parse_vri_docx(docx_path: Path) -> list[VriEntry]:
    """Строки таблицы классификатора из DOCX; VriImportError при разборе."""
    try:
        with zipfile.ZipFile(docx_path) as archive:
            document_xml = archive.read("word/document.xml")
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise VriImportError(f"{docx_path}: не DOCX-архив: {exc}") from exc
    root = ElementTree.fromstring(document_xml)

    rows: list[list[str]] | None = None
    columns: dict[str, int] | None = None
    for table in root.iter(f"{{{_W}}}tbl"):
        candidate_rows = _table_rows(table)
        candidate_columns = _header_columns(candidate_rows)
        if candidate_columns is not None:
            rows, columns = candidate_rows, candidate_columns
            break
    if rows is None or columns is None:
        raise VriImportError(
            f"{docx_path}: таблица классификатора не найдена "
            "(нет строки заголовка «наименование/описание/код»)"
        )

    entries: list[VriEntry] = []
    seen: set[str] = set()
    header_seen = False
    for row in rows:
        if not header_seen:
            if _header_columns([row]) is not None:
                header_seen = True
            continue
        cells = row + [""] * (max(columns.values()) + 1 - len(row))
        name = cells[columns["наименование"]]
        description = cells[columns["описание"]]
        code = cells[columns["код"]].replace(" ", "")
        non_empty = [cell for cell in (name, description, code) if cell]
        if not non_empty:
            continue
        # строка номеров граф («1», «2», «3») после заголовка
        if all(_COLUMN_NUMBER_RE.match(cell) for cell in non_empty):
            continue
        # редакционные примечания между позициями — не позиции классификатора
        if not code and any(_NOTE_RE.match(cell) for cell in non_empty):
            continue
        if not name and not code:
            if entries:
                previous = entries[-1]
                entries[-1] = VriEntry(
                    code=previous.code,
                    name=previous.name,
                    description="\n".join(
                        part for part in (previous.description, description) if part
                    ),
                )
                continue
            raise VriImportError(f"{docx_path}: строка-продолжение до первой позиции")
        if not code:
            raise VriImportError(f"{docx_path}: пустой код у позиции {name!r}")
        if not name:
            raise VriImportError(f"{docx_path}: пустое наименование у кода {code!r}")
        if not _CODE_RE.match(code):
            raise VriImportError(
                f"{docx_path}: код {code!r} не соответствует формату ВРИ"
            )
        if code in seen:
            raise VriImportError(f"{docx_path}: дублирующийся код ВРИ {code!r}")
        seen.add(code)
        entries.append(VriEntry(code=code, name=name, description=description))
    return entries


def import_classifier(
    docx_path: Path,
    *,
    revision_date: date = DEFAULT_REVISION_DATE,
    min_entries: int = DEFAULT_MIN_ENTRIES,
) -> VriClassifier:
    """DOCX → валидированный снимок классификатора одной редакции."""
    entries = parse_vri_docx(docx_path)
    if len(entries) < min_entries:
        raise VriImportError(
            f"{docx_path}: разобрано позиций {len(entries)} < {min_entries} — "
            "похоже на неполную таблицу классификатора"
        )
    return VriClassifier(
        revision_date=revision_date,
        source=VriSource(
            act_number=DEFAULT_ACT_NUMBER,
            act_date=DEFAULT_ACT_DATE,
            revision_date=revision_date,
            title=DEFAULT_TITLE,
        ),
        items=entries,
    )


def classifier_json(classifier: VriClassifier) -> str:
    """Детерминированная JSON-сериализация снимка."""
    return (
        json.dumps(
            classifier.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
