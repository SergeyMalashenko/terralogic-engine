"""Тонкий сервис нормативных знаний: разрешение ВРИ по классификатору.

Источники первого контура (NSPD/RGIS) отдают ВРИ либо кодом («2.1»), либо
текстом («для индивидуального жилищного строительства») — оба случая
резолвятся детерминированно, без LLM. Исходное значение источника сервис
не заменяет: он только дополняет его каноническими реквизитами.

Снимок классификатора ищется так: env `TERRALOGIC_VRI_CLASSIFIER` →
пакетный JSON (`legal/data/`). Пока файл не сгенерирован импортером,
сервис доступен, но резолв возвращает None (обогащение отключено).
"""

from __future__ import annotations

import os
import warnings
from dataclasses import dataclass
from pathlib import Path

from .models import VriEntry, VriSource
from .repository import PACKAGED_CLASSIFIER, VriRepository

_ENV_PATH = "TERRALOGIC_VRI_CLASSIFIER"


@dataclass(frozen=True)
class VriResolution:
    """Каноническое значение ВРИ + реквизиты нормативного источника."""

    code: str
    name: str
    description: str
    act_number: str
    revision_date: str  # ISO

    @classmethod
    def of(cls, entry: VriEntry, source: VriSource) -> VriResolution:
        return cls(
            code=entry.code,
            name=entry.name,
            description=entry.description,
            act_number=source.act_number,
            revision_date=source.revision_date.isoformat(),
        )


class LegalKnowledgeService:
    """Резолв ВРИ: `resolve_vri` по коду, `match_vri` по тексту источника."""

    def __init__(self, repository: VriRepository | None) -> None:
        self._repository = repository

    @classmethod
    def from_env(cls) -> LegalKnowledgeService:
        path = Path(os.environ.get(_ENV_PATH) or PACKAGED_CLASSIFIER)
        try:
            return cls(VriRepository.from_json(path))
        except FileNotFoundError:
            warnings.warn(
                f"классификатор ВРИ не найден ({path}) — обогащение ВРИ отключено",
                stacklevel=2,
            )
            return cls(None)

    @property
    def available(self) -> bool:
        return self._repository is not None

    def resolve_vri(self, code: str | None) -> VriResolution | None:
        if self._repository is None or not code:
            return None
        entry = self._repository.get(code)
        return (
            VriResolution.of(entry, self._repository.source)
            if entry is not None
            else None
        )

    def match_vri(self, text: str | None) -> VriResolution | None:
        """Резолв по наименованию: точное нормализованное совпадение."""
        if self._repository is None or not text:
            return None
        entry = self._repository.match_name(text)
        return (
            VriResolution.of(entry, self._repository.source)
            if entry is not None
            else None
        )


_cache: dict[Path, LegalKnowledgeService] = {}


def default_legal_knowledge() -> LegalKnowledgeService:
    """Процессный синглтон; путь снимка — из env или пакетный (см. from_env)."""
    path = Path(os.environ.get(_ENV_PATH) or PACKAGED_CLASSIFIER)
    service = _cache.get(path)
    if service is None:
        service = LegalKnowledgeService.from_env()
        _cache[path] = service
    return service
