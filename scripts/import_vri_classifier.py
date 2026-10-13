#!/usr/bin/env python3
"""Offline-импорт классификатора ВРИ из DOCX приказа Росреестра № П/0412.

Использование (из корня terralogic-engine, venv с установленным пакетом):

    .venv/bin/python scripts/import_vri_classifier.py \
        legal/sources/rosreestr_p0412_2026-06-02.docx

Результат — JSON-снимок `src/terralogic_engine/legal/data/` (переопределяется
--out). Валидация не прошла — сообщение в stderr и код выхода 1.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from terralogic_engine.legal.importer import (
    DEFAULT_MIN_ENTRIES,
    DEFAULT_REVISION_DATE,
    VriImportError,
    classifier_json,
    import_classifier,
)
from terralogic_engine.legal.repository import PACKAGED_CLASSIFIER


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("docx", type=Path, help="DOCX приказа с таблицей ВРИ")
    parser.add_argument(
        "--out",
        type=Path,
        default=PACKAGED_CLASSIFIER,
        help=f"куда писать JSON (по умолчанию: {PACKAGED_CLASSIFIER})",
    )
    parser.add_argument(
        "--revision-date",
        type=date.fromisoformat,
        default=DEFAULT_REVISION_DATE,
        help=f"дата редакции ISO (по умолчанию: {DEFAULT_REVISION_DATE})",
    )
    parser.add_argument(
        "--min-entries",
        type=int,
        default=DEFAULT_MIN_ENTRIES,
        help=f"минимум позиций в таблице (по умолчанию: {DEFAULT_MIN_ENTRIES})",
    )
    args = parser.parse_args()
    try:
        classifier = import_classifier(
            args.docx,
            revision_date=args.revision_date,
            min_entries=args.min_entries,
        )
    except VriImportError as exc:
        print(f"импорт не выполнен: {exc}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(classifier_json(classifier), encoding="utf-8")
    print(
        f"импортировано позиций: {len(classifier.items)}; "
        f"редакция {classifier.revision_date}; записано в {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
