"""Модели нормативного классификатора ВРИ.

Источник — Приказ Росреестра от 10.11.2020 № П/0412 «Об утверждении
классификатора видов разрешенного использования земельных участков».
JSON-снимок классификатора генерируется offline-импортом из DOCX
(`legal.importer`), runtime работает только с JSON.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field, field_validator, model_validator


class VriSource(BaseModel):
    """Реквизиты нормативного акта — источника классификатора."""

    act_number: str
    act_date: date
    revision_date: date
    title: str


class VriEntry(BaseModel):
    """Одна позиция классификатора ВРИ."""

    code: str
    name: str
    description: str = ""

    @field_validator("code", "name")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("код и наименование ВРИ не могут быть пустыми")
        return value


class VriClassifier(BaseModel):
    """Снимок классификатора ВРИ одной редакции."""

    namespace: str = "VRI"
    revision_date: date
    source: VriSource
    items: list[VriEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_codes(self) -> VriClassifier:
        seen: set[str] = set()
        for item in self.items:
            if item.code in seen:
                raise ValueError(f"дублирующийся код ВРИ: {item.code!r}")
            seen.add(item.code)
        return self
