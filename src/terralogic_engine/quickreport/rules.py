"""Правила скоринга QuickReport: загрузка yaml-активов и вычисление вердикта.

Скоринг — заглушка v0 (равномерные веса): замена на методику 5.0 — правка
`assets/scoring_rules.yaml`, интерфейс этого модуля не меняется.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_ASSETS = Path(__file__).resolve().parent / "assets"


class QuickRulesError(ValueError):
    """Битый или несогласованный yaml-актив quickreport."""


@dataclass(frozen=True)
class Grade:
    min_score: int
    key: str
    title: str
    summary: str


@dataclass(frozen=True)
class ScoringRule:
    id: str
    title: str
    guidance_key: str
    weight: float
    informational: bool = False
    fixed_coverage: float | None = None  # coverage.fixed_100 → 100 независимо от геометрии


@dataclass(frozen=True)
class ScoringRules:
    methodology_version: str
    base_score: int
    score_min: int
    score_max: int
    grades: tuple[Grade, ...]
    coverage_words: tuple[tuple[int, str], ...]  # (min_pct, слово), по убыванию порога
    rules: tuple[ScoringRule, ...]

    def coverage_word(self, percent: float) -> str:
        for min_pct, word in self.coverage_words:
            if percent >= min_pct:
                return word
        return self.coverage_words[-1][1] if self.coverage_words else ""

    def grade_for(self, score: int) -> Grade:
        for grade in self.grades:
            if score >= grade.min_score:
                return grade
        return self.grades[-1]


@dataclass(frozen=True)
class ZoneGuidanceEntry:
    key: str
    match: tuple[str, ...]
    title: str
    guidance: str
    action: str | None = None


@dataclass(frozen=True)
class ZoneGuidance:
    zones: dict[str, ZoneGuidanceEntry]
    misc: dict[str, str] = field(default_factory=dict)  # ключ → текст guidance


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise QuickRulesError(f"не удалось разобрать {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise QuickRulesError(f"{path}: корень должен быть mapping")
    return raw


def load_scoring_rules(path: Path | None = None) -> ScoringRules:
    """Загружает scoring_rules.yaml; без path — пакетный актив."""
    raw = _load_yaml(path or _ASSETS / "scoring_rules.yaml")
    try:
        grades = tuple(
            Grade(
                min_score=int(item["min"]),
                key=str(item["key"]),
                title=str(item["title"]),
                summary=str(item["summary"]),
            )
            for item in raw["grades"]
        )
        coverage_words = tuple(
            (int(item["min_pct"]), str(item["word"])) for item in raw["coverage_words"]
        )
        rules: list[ScoringRule] = []
        for item in raw["rules"]:
            coverage = item.get("coverage") or {}
            rules.append(
                ScoringRule(
                    id=str(item["id"]),
                    title=str(item["title"]),
                    guidance_key=str(item["guidance"]),
                    weight=float(item.get("weight", 0)),
                    informational=bool(item.get("informational", False)),
                    fixed_coverage=100.0 if coverage.get("fixed_100") else None,
                )
            )
        return ScoringRules(
            methodology_version=str(raw["methodology_version"]),
            base_score=int(raw["base_score"]),
            score_min=int(raw["score_min"]),
            score_max=int(raw["score_max"]),
            grades=grades,
            coverage_words=coverage_words,
            rules=tuple(rules),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise QuickRulesError(f"scoring_rules.yaml: битая структура: {exc}") from exc


def load_zone_guidance(path: Path | None = None) -> ZoneGuidance:
    """Загружает zone_guidance.yaml; без path — пакетный актив."""
    raw = _load_yaml(path or _ASSETS / "zone_guidance.yaml")
    try:
        zones = {
            str(key): ZoneGuidanceEntry(
                key=str(key),
                match=tuple(str(m).casefold() for m in entry.get("match", [])),
                title=str(entry["title"]),
                guidance=str(entry["guidance"]).strip(),
                action=(
                    str(entry["action"]).strip() if entry.get("action") else None
                ),
            )
            for key, entry in (raw.get("zones") or {}).items()
        }
        misc = {
            str(key): str(entry.get("guidance") or "").strip()
            for key, entry in (raw.get("misc") or {}).items()
            if isinstance(entry, dict)
        }
        return ZoneGuidance(zones=zones, misc=misc)
    except (KeyError, TypeError, ValueError) as exc:
        raise QuickRulesError(f"zone_guidance.yaml: битая структура: {exc}") from exc


def match_guidance(
    guidance: ZoneGuidance, *, zone_type: str | None, name: str
) -> ZoneGuidanceEntry | None:
    """Первая запись guidance, чьи match-подстроки встретились в типе/названии зоны."""
    haystack = f"{zone_type or ''} {name}".casefold()
    for entry in guidance.zones.values():
        if any(token in haystack for token in entry.match):
            return entry
    return None


def compute_score(
    rules: ScoringRules, deductions: list[tuple[ScoringRule, float]]
) -> int:
    """Итоговый балл: base − Σ weight × coverage/100; informational не вычитает."""
    total = 0.0
    for rule, coverage in deductions:
        if rule.informational:
            continue
        total += rule.weight * coverage / 100.0
    score = round(rules.base_score - total)
    return max(rules.score_min, min(rules.score_max, score))
