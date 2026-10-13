"""In-memory репозиторий классификатора ВРИ поверх JSON-снимка.

Lookup — обычный словарь: без LLM, embeddings и поиска по документу.
Пакетный снимок (`legal/data/vri_classifier_*.json`) генерируется
offline-импортом из DOCX приказа; до появления файла репозиторий
недоступен, а обогащение ВРИ молча отключено (см. `LegalKnowledgeService`).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .models import VriClassifier, VriEntry, VriSource

_DATA_DIR = Path(__file__).resolve().parent / "data"
PACKAGED_CLASSIFIER = _DATA_DIR / "vri_classifier_2026-06-02.json"
# хвост-уточнение канонического наименования: «… (приусадебный земельный участок)»
_PAREN_TAIL_RE = re.compile(r"\s*\([^)]*\)\s*$")


def normalize_name(text: str) -> str:
    """Нормализация наименования для точного сравнения: casefold + пробелы."""
    return " ".join(text.casefold().split())


class VriRepository:
    """Снимок классификатора в памяти: по коду и по наименованию.

    Наименование матчится точно (после нормализации) либо по алиасу —
    каноническому наименованию без хвоста-уточнения в скобках: источники
    отдают «для ведения личного подсобного хозяйства», а каноническое имя —
    «Для ведения личного подсобного хозяйства (приусадебный земельный
    участок)». Алиас, общий у нескольких позиций, не используется.
    """

    def __init__(self, classifier: VriClassifier) -> None:
        self._classifier = classifier
        self._by_code = {item.code: item for item in classifier.items}
        self._by_name = {
            normalize_name(item.name): item for item in classifier.items
        }
        alias_owner: dict[str, str | None] = {}
        for item in classifier.items:
            alias = normalize_name(_PAREN_TAIL_RE.sub("", item.name))
            if not alias or alias in self._by_name:
                continue
            if alias_owner.get(alias, item.code) != item.code:
                alias_owner[alias] = None  # алиас неоднозначен — отключаем
            else:
                alias_owner[alias] = item.code
        self._by_alias = {
            alias: self._by_code[code]
            for alias, code in alias_owner.items()
            if code is not None
        }

    @classmethod
    def from_json(cls, path: Path) -> VriRepository:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(VriClassifier.model_validate(raw))

    @classmethod
    def packaged(cls) -> VriRepository:
        """Пакетный снимок; FileNotFoundError, пока импорт не выполнен."""
        return cls.from_json(PACKAGED_CLASSIFIER)

    @property
    def source(self) -> VriSource:
        return self._classifier.source

    def get(self, code: str) -> VriEntry | None:
        return self._by_code.get(code.strip())

    def match_name(self, text: str) -> VriEntry | None:
        """Точное совпадение по наименованию или алиасу; None при промахе."""
        if not text.strip():
            return None
        normalized = normalize_name(text)
        return self._by_name.get(normalized) or self._by_alias.get(normalized)

    def all(self) -> list[VriEntry]:
        return list(self._classifier.items)
