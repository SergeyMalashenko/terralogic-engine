"""Тесты Report QA: детерминированный валидатор и критик (§64–86 ТЗ).

Реальный Hermes/Gemini не запускается: критик — фейк-исполнитель с
подконтрольным stdout. Бенчмарк §86: испорченные отчёты ловятся кодом.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pytest

from terralogic_engine.acquisition.pipeline import AcquisitionPipeline
from terralogic_engine.analytics.pipeline import AnalysisPipeline
from terralogic_engine.domain.models import CollectionRequest
from terralogic_engine.qa import (
    CRITIC_RESULT_MARKER,
    CriticExecResult,
    build_critic_prompt,
    parse_critic_result,
    run_critic,
    validate_quickreport,
)
from terralogic_engine.qa.models import ValidatorResult
from terralogic_engine.quickreport import (
    build_quick_context_from_report,
    build_quick_model,
    load_marketing,
    render_quick_model,
)
from terralogic_engine.reporting.service import ReportingService
from terralogic_engine.store.local import LocalCaseStore

from .fakes import FakeDgisClient, FakeNspdClient, FakeOsmClient
from .test_quickreport import _CONFIG_OK, _base_context, _fake_tile


@pytest.fixture()
def rules():
    from terralogic_engine.quickreport import load_scoring_rules

    return load_scoring_rules()


@pytest.fixture()
def guidance():
    from terralogic_engine.quickreport import load_zone_guidance

    return load_zone_guidance()


@pytest.fixture()
def quick(rules, guidance):
    return build_quick_context_from_report(
        _base_context(), rules=rules, guidance=guidance, configuration=_CONFIG_OK
    )


@pytest.fixture()
def draft(quick):
    model = build_quick_model(quick, marketing=load_marketing())
    markdown = render_quick_model(
        model, today=date(2026, 10, 7), zone_regulations=quick.zone_regulations
    )
    return quick, model, markdown


# ---------------------------------------------------------------------------
# Валидатор: эталон проходит (§64)
# ---------------------------------------------------------------------------


def test_validator_passes_reference_report(draft):
    quick, model, markdown = draft
    result = validate_quickreport(model, markdown, context=quick)
    assert result.status == "pass", [i.model_dump() for i in result.issues]


# ---------------------------------------------------------------------------
# Бенчмарк испорченных отчётов (§86): детерминированные ошибки ловит код
# ---------------------------------------------------------------------------


def test_validator_catches_wrong_score(draft):
    _, model, markdown = draft
    corrupted = markdown.replace(
        "## Оценка участка — 55/100", "## Оценка участка — 95/100"
    )
    result = validate_quickreport(model, corrupted)
    assert result.status == "fail"
    assert any(
        i.rule_id == "score_matches_model" and i.severity == "critical"
        for i in result.issues
    )


def test_validator_catches_foreign_price(draft):
    _, model, markdown = draft
    corrupted = markdown.replace("30 000 ₽", "50 000 ₽")
    result = validate_quickreport(model, corrupted)
    assert result.status == "fail"
    assert any(i.rule_id == "prices_match_config" for i in result.issues)


def test_validator_catches_missing_section(draft):
    _, model, markdown = draft
    corrupted = markdown.replace("## Что ещё нужно проверить", "## Проверки")
    result = validate_quickreport(model, corrupted)
    assert result.status == "fail"
    assert any(i.rule_id == "mandatory_sections" for i in result.issues)


def test_validator_catches_modified_disclaimer(draft):
    _, model, markdown = draft
    corrupted = markdown.replace(
        "не заменяет юридическое, кадастровое или градостроительное заключение",
        "гарантирует юридическую чистоту участка",
    )
    result = validate_quickreport(model, corrupted)
    assert result.status == "fail"
    assert any(i.rule_id == "limitations_static" for i in result.issues)


def test_validator_catches_wrong_vri(quick):
    model = build_quick_model(quick)
    markdown = render_quick_model(
        model, today=date(2026, 10, 7), zone_regulations=quick.zone_regulations
    )
    corrupted = markdown.replace(
        "- **Разрешённое использование:** ИЖС",
        "- **Разрешённое использование:** ВРИ 4.4 — Магазины",
    )
    # ВРИ в фикстуре не обогащён (origin SOURCE) — подмена ловится как
    # несовпадение паспорта только при LEGAL; здесь проверяем LEGAL-вариант
    quick.parcel.vri_origin = "LEGAL"
    from terralogic_engine.reporting.models import VriReportContext

    quick.parcel.vri = VriReportContext(
        code="2.1",
        name="Для индивидуального жилищного строительства",
        description="",
        act_number="П/0412",
        revision_date="2026-06-02",
    )
    model = build_quick_model(quick)
    good = render_quick_model(
        model, today=date(2026, 10, 7), zone_regulations=quick.zone_regulations
    )
    assert validate_quickreport(model, good).status == "pass"
    corrupted = good.replace("ВРИ 2.1", "ВРИ 4.4")
    result = validate_quickreport(model, corrupted)
    assert result.status == "fail"
    assert any(i.rule_id == "vri_matches_legal" for i in result.issues)


def test_validator_warns_untraced_factor(quick):
    """Фактор без строки в протоколе скоринга — warning, не error (§64)."""
    from terralogic_engine.quickreport.models import QuickFactor

    quick.factors.append(
        QuickFactor(
            rule_id="invented_zone",
            title="Придуманная зона",
            zone_name="Придуманная зона",
            coverage_percent=10.0,
            coverage_word="край участка",
            guidance="...",
        )
    )
    model = build_quick_model(quick)
    markdown = render_quick_model(
        model, today=date(2026, 10, 7), zone_regulations=quick.zone_regulations
    )
    result = validate_quickreport(model, markdown, context=quick)
    assert any(
        i.rule_id == "factors_have_evidence" and i.severity == "warning"
        for i in result.issues
    )


def test_validator_catches_wrong_status_and_area(draft):
    _, model, markdown = draft
    corrupted = markdown.replace(
        "Требует повышенного внимания", "Можно продолжить рассмотрение"
    )
    result = validate_quickreport(model, corrupted)
    assert any(i.rule_id == "status_matches_score" for i in result.issues)
    corrupted = markdown.replace("1 000 м²", "1 500 м²")
    result = validate_quickreport(model, corrupted)
    assert any(i.rule_id == "area_matches_context" for i in result.issues)


# ---------------------------------------------------------------------------
# Критик: парсинг, schema validation, fail-safe (§73, §77, §83)
# ---------------------------------------------------------------------------


def _critic_line(payload: dict) -> str:
    return f"{CRITIC_RESULT_MARKER} {json.dumps(payload, ensure_ascii=False)}"


class FakeCritic:
    """Подконтрольный исполнитель критика; падает или отвечает по сценарию."""

    def __init__(self, stdout: str | None = None, fail: bool = False) -> None:
        self.stdout = stdout
        self.fail = fail
        self.prompts: list[str] = []

    def run(self, prompt: str, workdir: Path) -> CriticExecResult:
        self.prompts.append(prompt)
        if self.fail:
            raise RuntimeError("gemini недоступен")
        return CriticExecResult(stdout=self.stdout or "", returncode=0)


def test_parse_critic_result_valid():
    stdout = "рассуждения\n" + _critic_line(
        {
            "status": "needs_revision",
            "issues": [
                {
                    "rule_id": "overstatement",
                    "section": "Оценка",
                    "severity": "warning",
                    "message": "вывод сильнее доказательств",
                    "claim": "отличный участок",
                    "evidence_ids": [],
                    "suggested_action": "смягчить формулировку",
                }
            ],
        }
    )
    result = parse_critic_result(stdout)
    assert result is not None
    assert result.status == "needs_revision"
    assert result.issues[0].rule_id == "overstatement"
    assert result.issues[0].suggested_action


def test_parse_critic_result_invalid_schema():
    # невалидный severity не проходит pydantic-валидацию (§83)
    bad = _critic_line(
        {
            "status": "needs_revision",
            "issues": [
                {"rule_id": "x", "section": "s", "severity": "fatal", "message": "m"}
            ],
        }
    )
    assert parse_critic_result(bad) is None
    assert parse_critic_result("без маркера") is None
    assert parse_critic_result(f"{CRITIC_RESULT_MARKER} {{битый json") is None


def test_run_critic_failsafe_when_executor_dead(draft):
    """Gemini недоступен → None + warning, отчёт не ломается (§77)."""
    _, model, markdown = draft
    warnings: list[str] = []
    result = run_critic(
        FakeCritic(fail=True), model, markdown, ValidatorResult(status="pass"),
        warnings=warnings,
    )
    assert result is None
    assert any("недоступен" in warning for warning in warnings)


def test_run_critic_retries_on_broken_result(draft):
    _, model, markdown = draft
    warnings: list[str] = []
    # обе попытки без маркера → None + warning; retry был (2 вызова)
    critic = FakeCritic(stdout="ответ без маркера")
    result = run_critic(
        critic, model, markdown, ValidatorResult(status="pass"), warnings=warnings
    )
    assert result is None
    assert len(critic.prompts) == 2
    assert "ОТКЛОНЁН" in critic.prompts[1]


def test_critic_prompt_policy_and_readonly(draft):
    """Промпт несёт политику §71; инструментов нет — read-only по построению."""
    _, model, markdown = draft
    prompt = build_critic_prompt(model, markdown, ValidatorResult(status="pass"))
    assert "Не используй собственные знания модели" in prompt
    assert "unverifiable" in prompt
    assert "50:11:0020310:49" in prompt
    assert "Водоохранная зона" in prompt
    # никаких tool-инструкций: критик получает только текст
    assert "read_document" not in prompt
    assert "web_search" not in prompt


# ---------------------------------------------------------------------------
# Интеграция: prepare_quickreport сохраняет QA-артефакт, отчёт не меняется
# ---------------------------------------------------------------------------


async def _prepare(tmp_path, critic) -> tuple[ReportingService, object, LocalCaseStore]:
    store = LocalCaseStore(tmp_path / "store")
    acquisition = AcquisitionPipeline(
        store=store,
        nspd=FakeNspdClient(),
        osm=FakeOsmClient(),
        dgis=FakeDgisClient(),
    )
    await acquisition.collect(
        CollectionRequest(
            case_id="case-qa",
            cadastral_number="52:26:0040002:3823",
            refresh_policy="always",
        )
    )
    AnalysisPipeline(store=store).analyze("case-qa")
    service = ReportingService(
        store=store, acquisition=acquisition, tile_fetcher=_fake_tile,
        critic=critic,
    )
    result = service.prepare_quickreport("case-qa")
    return service, result, store


async def test_prepare_quickreport_saves_qa_artifact(tmp_path) -> None:
    critic = FakeCritic(stdout=_critic_line({"status": "pass", "issues": []}))
    _, result, store = await _prepare(tmp_path, critic)

    assert result.qa_relative_path is not None
    qa_markdown = (
        store.cases_root / result.case_id / result.qa_relative_path
    ).read_text(encoding="utf-8")
    assert "## Валидатор: pass" in qa_markdown
    assert "## Критик: pass" in qa_markdown
    # QA не меняет клиентский отчёт
    assert result.markdown.startswith("# Экспресс-оценка земельного участка\n")
    assert "## Валидатор" not in result.markdown
    assert len(critic.prompts) == 1


async def test_prepare_quickreport_critic_needs_revision(tmp_path) -> None:
    critic = FakeCritic(
        stdout=_critic_line(
            {
                "status": "needs_revision",
                "issues": [
                    {
                        "rule_id": "missing_uncertainty",
                        "section": "Оценка участка",
                        "severity": "warning",
                        "message": "не отражена предварительность оценки",
                        "claim": None,
                        "evidence_ids": [],
                        "suggested_action": "уточнить формулировку",
                    }
                ],
            }
        )
    )
    _, result, store = await _prepare(tmp_path, critic)
    qa_markdown = (
        store.cases_root / result.case_id / result.qa_relative_path
    ).read_text(encoding="utf-8")
    assert "## Критик: needs_revision" in qa_markdown
    assert "missing_uncertainty" in qa_markdown
    assert any("needs_revision" in warning for warning in result.warnings)
    # отчёт сохранён несмотря на замечания критика
    assert result.report_id


async def test_prepare_quickreport_without_critic(tmp_path) -> None:
    """Критик не сконфигурирован: валидатор работает, отчёт сохраняется."""
    _, result, store = await _prepare(tmp_path, None)
    assert result.qa_relative_path is not None
    qa_markdown = (
        store.cases_root / result.case_id / result.qa_relative_path
    ).read_text(encoding="utf-8")
    assert "## Валидатор: pass" in qa_markdown
    assert "## Критик: недоступен" in qa_markdown
    assert any("критик отключён" in warning for warning in result.warnings)


async def test_score_independent_from_llm(tmp_path) -> None:
    """Падающий критик не влияет на балл и сохранность отчёта (§83)."""
    _, result, _ = await _prepare(tmp_path, FakeCritic(fail=True))
    assert re.search(r"## Оценка (участка — \d+/100|не рассчитана)", result.markdown)
    assert result.report_id
    assert any("критик недоступен" in warning for warning in result.warnings)
