"""Тесты bounded narrative writer'а и targeted revision (§62–63, §74, §77 ТЗ).

Реальный LLM не запускается: writer и критик — фейк-исполнители
с подконтрольным stdout. Ключевой инвариант: без writer'а рендер
байт-в-байт детерминированный; балл не зависит от LLM.
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
from terralogic_engine.qa import CRITIC_RESULT_MARKER, CriticExecResult
from terralogic_engine.quickreport import (
    build_quick_context_from_report,
    build_quick_model,
    render_quick_model,
)
from terralogic_engine.quickreport.writer import (
    NARRATIVE_RESULT_MARKER,
    WriterExecResult,
    _WriterOutput,
    build_writer_prompt,
    parse_writer_output,
    run_writer,
    validate_writer_output,
)
from terralogic_engine.reporting.service import ReportingService
from terralogic_engine.store.local import LocalCaseStore

from .fakes import FakeDgisClient, FakeNspdClient, FakeOsmClient
from .test_quickreport import _CONFIG_OK, _base_context, _fake_tile


@pytest.fixture()
def quick():
    from terralogic_engine.quickreport import load_scoring_rules, load_zone_guidance

    return build_quick_context_from_report(
        _base_context(),
        rules=load_scoring_rules(),
        guidance=load_zone_guidance(),
        configuration=_CONFIG_OK,
    )


@pytest.fixture()
def model(quick):
    return build_quick_model(quick)


def _narrative_line(payload: dict) -> str:
    return f"{NARRATIVE_RESULT_MARKER} {json.dumps(payload, ensure_ascii=False)}"


class FakeWriter:
    """Подконтрольный writer: очередь ответов или падение."""

    def __init__(self, *payloads: dict | None, fail: bool = False) -> None:
        self.payloads = list(payloads)
        self.fail = fail
        self.prompts: list[str] = []

    def run(self, prompt: str, workdir: Path) -> WriterExecResult:
        self.prompts.append(prompt)
        if self.fail:
            raise RuntimeError("writer недоступен")
        payload = self.payloads.pop(0) if self.payloads else None
        if payload is None:
            return WriterExecResult(stdout="нет маркера", returncode=0)
        return WriterExecResult(stdout=_narrative_line(payload), returncode=0)


class FakeCritic:
    def __init__(self, payload: dict | None) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    def run(self, prompt: str, workdir: Path) -> CriticExecResult:
        self.prompts.append(prompt)
        if self.payload is None:
            return CriticExecResult(stdout="нет маркера", returncode=0)
        return CriticExecResult(
            stdout=f"{CRITIC_RESULT_MARKER} {json.dumps(self.payload, ensure_ascii=False)}",
            returncode=0,
        )


# ---------------------------------------------------------------------------
# Парсинг и рамки (§62–63)
# ---------------------------------------------------------------------------


def test_parse_writer_output_valid_and_invalid():
    ok = parse_writer_output("текст\n" + _narrative_line({"main_conclusion": "Хорошо."}))
    assert ok is not None and ok.main_conclusion == "Хорошо."
    assert parse_writer_output("без маркера") is None
    assert parse_writer_output(f"{NARRATIVE_RESULT_MARKER} {{битый") is None
    assert parse_writer_output(_narrative_line({"unknown_field": 1})) is not None


def test_guardrails_reject_foreign_numbers(model):
    output = _WriterOutput(
        main_conclusion="Участок площадью 2500 м² в отличном состоянии.",
        established_summary="Водоохранная зона покрывает 100% участка.",
    )
    narrative, rejections = validate_writer_output(output, model)
    # 2500 нет в контексте → отброшено; 100% — легальное число охвата
    assert narrative.main_conclusion is None
    assert narrative.established_summary is not None
    assert any("main_conclusion" in reason for reason in rejections)


def test_guardrails_reject_banned_phrases(model):
    output = _WriterOutput(main_conclusion="Участок юридически чист, можно строить.")
    narrative, rejections = validate_writer_output(output, model)
    assert narrative.main_conclusion is None
    assert any("категоричная" in reason for reason in rejections)


def test_guardrails_reject_unknown_rule_id(model):
    output = _WriterOutput(
        factor_meanings={
            "water_protection": "Зона влияет на размещение дома.",
            "invented": "Несуществующий фактор.",
        }
    )
    narrative, rejections = validate_writer_output(output, model)
    assert narrative.factor_meanings == {"water_protection": "Зона влияет на размещение дома."}
    assert any("invented" in reason for reason in rejections)


def test_guardrails_reject_overlength(model):
    output = _WriterOutput(main_conclusion="x" * 601)
    narrative, rejections = validate_writer_output(output, model)
    assert narrative.main_conclusion is None
    assert any("600" in reason for reason in rejections)


def test_run_writer_failsafe_and_retry(model):
    warnings: list[str] = []
    narrative, _ = run_writer(FakeWriter(fail=True), model, warnings=warnings)
    assert narrative is None
    assert any("writer недоступен" in warning for warning in warnings)

    # два ответа без маркера → None, был один retry
    writer = FakeWriter(None, None)
    narrative, _ = run_writer(writer, model, warnings=warnings)
    assert narrative is None
    assert len(writer.prompts) == 2
    assert "ОТКЛОНЁН" in writer.prompts[1]


def test_writer_prompt_bounds(model):
    prompt = build_writer_prompt(model)
    assert "Не добавляй новых фактов" in prompt
    assert "50:11:0020310:49" in prompt
    assert "water_protection" in prompt  # rule_id фактора виден writer'у
    assert "не установлены" in prompt  # политика пробелов данных


# ---------------------------------------------------------------------------
# Рендер с narrative; без writer — байт-стабильность (регрессионный инвариант)
# ---------------------------------------------------------------------------


def test_render_without_narrative_byte_stable(quick, model):
    reference = render_quick_model(
        model, today=date(2026, 10, 7), zone_regulations=quick.zone_regulations
    )
    again = render_quick_model(
        model, today=date(2026, 10, 7), zone_regulations=quick.zone_regulations
    )
    assert reference == again
    # детерминированный текст — прежний
    assert "Возможность реализовать ваш замысел требует подробного анализа." in reference


def test_render_with_narrative(quick, model):
    warnings: list[str] = []
    writer = FakeWriter(
        {
            "main_conclusion": "Участок можно рассматривать, но два фактора требуют проверки.",
            "established_summary": "Из источников уже известны площадь и категория.",
            "factor_meanings": {"water_protection": "Может влиять на размещение дома."},
        }
    )
    narrative, rejections = run_writer(writer, model, warnings=warnings)
    assert narrative is not None and not rejections
    narrated = model.model_copy(update={"narrative": narrative})
    markdown = render_quick_model(
        narrated, today=date(2026, 10, 7), zone_regulations=quick.zone_regulations
    )
    assert "Участок можно рассматривать, но два фактора требуют проверки." in markdown
    assert "Из источников уже известны площадь и категория." in markdown
    assert "Может влиять на размещение дома." in markdown
    # детерминированное содержание не пострадало
    assert "## Оценка участка — 55/100" in markdown
    assert "Водоохранная зона: практически весь участок (100%)" in markdown
    # валидатор пропускает текст с narrative
    from terralogic_engine.qa import validate_quickreport

    assert validate_quickreport(narrated, markdown, context=quick).status == "pass"


# ---------------------------------------------------------------------------
# Сквозные: prepare_quickreport с writer и критиком (§61, §74, §77, §83)
# ---------------------------------------------------------------------------


async def _prepare(tmp_path, writer, critic) -> tuple[object, LocalCaseStore]:
    store = LocalCaseStore(tmp_path / "store")
    acquisition = AcquisitionPipeline(
        store=store, nspd=FakeNspdClient(), osm=FakeOsmClient(), dgis=FakeDgisClient(),
    )
    await acquisition.collect(
        CollectionRequest(
            case_id="case-writer",
            cadastral_number="52:26:0040002:3823",
            refresh_policy="always",
        )
    )
    AnalysisPipeline(store=store).analyze("case-writer")
    service = ReportingService(
        store=store, acquisition=acquisition, tile_fetcher=_fake_tile,
        writer=writer, critic=critic,
    )
    return service.prepare_quickreport("case-writer"), store


async def test_quickreport_without_writer_is_deterministic(tmp_path) -> None:
    result, store = await _prepare(tmp_path, None, None)
    assert (
        "## Оценка участка —" in result.markdown
        or "Оценка не рассчитана" in result.markdown
    )
    qa = (
        store.cases_root / result.case_id / result.qa_relative_path
    ).read_text(encoding="utf-8")
    assert "## Writer: отключён" in qa
    assert "## Revision: не требовалась" in qa


async def test_quickreport_with_writer_applies_narrative(tmp_path) -> None:
    writer = FakeWriter(
        {"main_conclusion": "Участок стоит рассмотреть дальше с проверкой факторов."}
    )
    result, store = await _prepare(tmp_path, writer, None)
    assert "Участок стоит рассмотреть дальше с проверкой факторов." in result.markdown
    assert writer.prompts and len(writer.prompts) == 1
    qa = (
        store.cases_root / result.case_id / result.qa_relative_path
    ).read_text(encoding="utf-8")
    assert "## Writer: применён" in qa
    # балл не зависит от LLM (§83)
    assert re.search(r"## Оценка (участка — \d+/100|не рассчитана)", result.markdown)


async def test_quickreport_writer_dead_keeps_deterministic(tmp_path) -> None:
    result, store = await _prepare(tmp_path, FakeWriter(fail=True), None)
    assert result.report_id
    assert any("writer недоступен" in warning for warning in result.warnings)
    qa = (
        store.cases_root / result.case_id / result.qa_relative_path
    ).read_text(encoding="utf-8")
    assert "## Writer: недоступен/отброшен" in qa


async def test_targeted_revision_after_critic(tmp_path) -> None:
    """Критик needs_revision → один раунд revision; принятие при pass (§74)."""
    writer = FakeWriter(
        {"main_conclusion": "Первичная формулировка."},
        {"main_conclusion": "Уточнённая формулировка после замечания."},
    )
    critic = FakeCritic(
        {
            "status": "needs_revision",
            "issues": [
                {
                    "rule_id": "overstatement",
                    "section": "Оценка участка",
                    "severity": "warning",
                    "message": "вывод слишком категоричен",
                    "claim": None,
                    "evidence_ids": [],
                    "suggested_action": "смягчить",
                }
            ],
        }
    )
    result, store = await _prepare(tmp_path, writer, critic)
    assert "Уточнённая формулировка после замечания." in result.markdown
    # writer вызван дважды: начальный narrative + revision с замечанием
    assert len(writer.prompts) == 2
    assert "вывод слишком категоричен" in writer.prompts[1]
    qa = (
        store.cases_root / result.case_id / result.qa_relative_path
    ).read_text(encoding="utf-8")
    assert "## Revision: принята" in qa


async def test_revision_rejected_when_all_fields_dropped(tmp_path) -> None:
    """Revision отклоняется, если рамки отбросили все поля (§74)."""
    writer = FakeWriter(
        {"main_conclusion": "Первичная формулировка."},
        {"main_conclusion": "Можно строить, гарантированно."},  # banned
    )
    critic = FakeCritic(
        {
            "status": "needs_revision",
            "issues": [
                {
                    "rule_id": "missing_uncertainty",
                    "section": "Оценка участка",
                    "severity": "warning",
                    "message": "нет неопределённости",
                    "claim": None,
                    "evidence_ids": [],
                    "suggested_action": "добавить оговорку",
                }
            ],
        }
    )
    result, store = await _prepare(tmp_path, writer, critic)
    # исходная формулировка сохранена
    assert "Первичная формулировка." in result.markdown
    assert "гарантированно" not in result.markdown
    qa = (
        store.cases_root / result.case_id / result.qa_relative_path
    ).read_text(encoding="utf-8")
    assert "Revision: отклонена" in qa
