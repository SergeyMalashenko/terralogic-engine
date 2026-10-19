"""Тесты bounded NarrativeGenerator блока «Что уже удалось установить» (§62–63, §74, §77 ТЗ).

Реальный LLM не запускается: executor и критик — фейки с подконтрольным
stdout. Ключевые инварианты: без narrative рендер байт-в-байт прежний;
deterministic fallback даёт тот же Markdown, что deterministic-путь;
балл, статус, факторы и маркетинг не зависят от LLM.
"""

from __future__ import annotations

import json
import re
import subprocess
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
    load_marketing,
    render_quick_model,
)
from terralogic_engine.quickreport.model import QuickNarrative
from terralogic_engine.quickreport.narrative import (
    MAX_NARRATIVE_CHARS,
    NARRATIVE_RESULT_MARKER,
    WRITER_PROMPT_VERSION,
    DeterministicNarrativeGenerator,
    FallbackNarrativeGenerator,
    HermesNarrativeGenerator,
    NarrativeExecResult,
    build_established_facts_input,
    build_narrative_prompt,
    check_narrative_text,
    input_sha256,
    parse_narrative_output,
)
from terralogic_engine.reporting.service import ReportingService
from terralogic_engine.store.local import LocalCaseStore

from .fakes import FakeDgisClient, FakeNspdClient, FakeOsmClient
from .test_quickreport import _CONFIG_OK, _base_context, _fake_tile

MODEL_NAME = "qwen3.5:9b-q4_K_M"


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


@pytest.fixture()
def facts_input(quick, model):
    return build_established_facts_input(quick, model)


def _narrative_line(payload: dict) -> str:
    return f"{NARRATIVE_RESULT_MARKER} {json.dumps(payload, ensure_ascii=False)}"


def _hermes(executor, **kwargs) -> FallbackNarrativeGenerator:
    return FallbackNarrativeGenerator(
        HermesNarrativeGenerator(executor, model_name=MODEL_NAME, **kwargs),
        DeterministicNarrativeGenerator(),
    )


class FakeNarrativeExecutor:
    """Подконтрольный executor: очередь ответов, падение или timeout."""

    def __init__(self, *payloads: dict | None, fail: str | None = None) -> None:
        self.payloads = list(payloads)
        self.fail = fail
        self.prompts: list[str] = []

    def run(self, prompt: str, workdir: Path) -> NarrativeExecResult:
        self.prompts.append(prompt)
        if self.fail == "exception":
            raise RuntimeError("writer недоступен")
        if self.fail == "timeout":
            raise subprocess.TimeoutExpired(cmd="hermes", timeout=1)
        payload = self.payloads.pop(0) if self.payloads else None
        if payload is None:
            return NarrativeExecResult(stdout="нет маркера", returncode=0)
        return NarrativeExecResult(stdout=_narrative_line(payload), returncode=0)


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
# Bounded input (§62): только разрешённые данные, маркетинга нет
# ---------------------------------------------------------------------------


def test_bounded_input_fields(quick, model, facts_input):
    assert facts_input.target_use == "индивидуальный жилой дом"
    assert facts_input.parcel.cadastral_number == "50:11:0020310:49"
    assert facts_input.parcel.area_m2 == quick.parcel.area_m2
    assert facts_input.score == 55
    assert facts_input.status_title == model.status.status_title
    assert facts_input.deterministic_facts == model.established_facts
    assert facts_input.evidence_gaps == [i.text for i in model.missing_information]
    zone_factors = [f for f in model.attention_factors if not f.informational]
    assert [r.rule_id for r in facts_input.restrictions] == [
        f.rule_id for f in zone_factors
    ]
    # informational-факторы (коммуникации) в restrictions не попадают
    assert "communications_unknown" not in {
        r.rule_id for r in facts_input.restrictions
    }


def test_bounded_input_excludes_marketing(quick, facts_input):
    marketing = load_marketing()
    model = build_quick_model(quick, marketing=marketing)
    assert model.marketing is not None
    facts_input = build_established_facts_input(quick, model)
    serialized = facts_input.model_dump_json()
    assert "marketing" not in serialized
    assert "30000" not in serialized and "45000" not in serialized
    assert marketing.cta not in serialized


def test_input_hash_stable(facts_input):
    assert input_sha256(facts_input) == input_sha256(facts_input)


# ---------------------------------------------------------------------------
# Structured output и детерминированные рамки (§62–63)
# ---------------------------------------------------------------------------


def test_parse_narrative_output_valid_and_invalid():
    ok = parse_narrative_output("текст\n" + _narrative_line({"text": "Хорошо."}))
    assert ok is not None and ok.text == "Хорошо."
    assert parse_narrative_output("без маркера") is None
    assert parse_narrative_output(f"{NARRATIVE_RESULT_MARKER} {{битый") is None
    # extra fields запрещены строгой схемой
    assert parse_narrative_output(
        _narrative_line({"text": "Хорошо.", "extra": 1})
    ) is None
    assert parse_narrative_output(_narrative_line({"summary": "нет text"})) is None


def test_guardrails_reject_foreign_numbers(facts_input):
    reason = check_narrative_text("Участок площадью 2500 м² осмотрен.", facts_input)
    assert reason is not None and "чужие числа" in reason and "2500" in reason
    # число из входа (площадь 1000 м²) допустимо
    assert (
        check_narrative_text("По кадастру площадь 1 000 м².", facts_input) is None
    )


def test_guardrails_reject_banned_phrases(facts_input):
    reason = check_narrative_text("Участок юридически чист.", facts_input)
    assert reason is not None and "категоричная" in reason


def test_guardrails_reject_repeated_identity_status_and_source_mix(facts_input):
    reason = check_narrative_text(
        f"Участок {facts_input.parcel.cadastral_number} проверен.", facts_input
    )
    assert reason is not None and "кадастровый номер" in reason

    reason = check_narrative_text(
        f"По итогам проверки статус — {facts_input.status_title}.", facts_input
    )
    assert reason is not None and "статус" in reason

    reason = check_narrative_text(
        "Пространственный анализ подтвердил регламент территориальной зоны.",
        facts_input,
    )
    assert reason is not None and "смешивает" in reason


def test_guardrails_reject_overlength_and_newlines(facts_input):
    reason = check_narrative_text("x" * (MAX_NARRATIVE_CHARS + 1), facts_input)
    assert reason is not None and str(MAX_NARRATIVE_CHARS) in reason
    reason = check_narrative_text("Первое предложение.\nВторое.", facts_input)
    assert reason is not None and "переводы строк" in reason
    assert check_narrative_text("   ", facts_input) is not None


def test_prompt_bounds(facts_input):
    prompt = build_narrative_prompt(facts_input)
    assert "Не добавляй новых фактов" in prompt
    assert "50:11:0020310:49" in prompt
    assert "Что уже удалось установить" in prompt
    assert NARRATIVE_RESULT_MARKER in prompt
    # маркетинга и балла-инструкций нет во входе
    assert "30 000" not in prompt


# ---------------------------------------------------------------------------
# Генераторы: success, retry, fallback (§62–63, §77)
# ---------------------------------------------------------------------------


def test_deterministic_generator_matches_bullet_list(facts_input):
    narrative = DeterministicNarrativeGenerator().generate_established_facts(
        facts_input
    )
    assert narrative.source == "deterministic_fallback"
    assert narrative.text == "\n".join(
        f"- {fact}" for fact in facts_input.deterministic_facts
    )
    assert narrative.model_name is None
    assert narrative.attempts == 0
    assert narrative.input_sha256 == input_sha256(facts_input)


def test_hermes_success_metadata(facts_input):
    executor = FakeNarrativeExecutor(
        {"text": "По кадастровым сведениям известны площадь 1 000 м² и вид использования."}
    )
    generator = HermesNarrativeGenerator(executor, model_name=MODEL_NAME)
    narrative = generator.generate_established_facts(facts_input)
    assert narrative is not None
    assert narrative.source == "llm"
    assert narrative.model_name == MODEL_NAME
    assert narrative.prompt_version == WRITER_PROMPT_VERSION
    assert narrative.input_sha256 == input_sha256(facts_input)
    assert narrative.attempts == 1
    assert narrative.latency_ms == 0.0  # фейк не засекает время


def test_hermes_returns_none_when_executor_missing(facts_input):
    generator = HermesNarrativeGenerator(None)
    assert generator.generate_established_facts(facts_input) is None
    assert "отключён" in (generator.last_error or "")


def test_fallback_when_executor_dead_or_timeout(facts_input):
    for fail in ("exception", "timeout"):
        generator = _hermes(FakeNarrativeExecutor(fail=fail))
        narrative = generator.generate_established_facts(facts_input)
        assert narrative.source == "deterministic_fallback"
        assert narrative.text.startswith("- По кадастровым сведениям")
        assert any("LLM narrative недоступен" in w for w in narrative.warnings)


def test_retry_only_on_invalid_structured_output(facts_input):
    # битый ответ, затем валидный: один retry, успех со второй попытки
    executor = FakeNarrativeExecutor(
        None,
        {"text": "По кадастровым сведениям известны площадь и категория земель."},
    )
    generator = HermesNarrativeGenerator(executor, model_name=MODEL_NAME)
    narrative = generator.generate_established_facts(facts_input)
    assert narrative is not None and narrative.attempts == 2
    assert "ОТКЛОНЁН" in executor.prompts[1]

    # обе попытки битые → None → fallback
    generator = _hermes(FakeNarrativeExecutor(None, None))
    narrative = generator.generate_established_facts(facts_input)
    assert narrative.source == "deterministic_fallback"


def test_no_retry_on_guardrail_rejection(facts_input):
    # чужое число → отклонение рамками без retry → fallback
    executor = FakeNarrativeExecutor({"text": "Участок площадью 2500 м²."})
    hermes = HermesNarrativeGenerator(executor, model_name=MODEL_NAME)
    assert hermes.generate_established_facts(facts_input) is None
    assert "чужие числа" in (hermes.last_error or "")
    assert len(executor.prompts) == 1

    generator = _hermes(FakeNarrativeExecutor({"text": "Можно строить без ограничений."}))
    narrative = generator.generate_established_facts(facts_input)
    assert narrative.source == "deterministic_fallback"

    generator = _hermes(FakeNarrativeExecutor({"text": "x" * (MAX_NARRATIVE_CHARS + 1)}))
    narrative = generator.generate_established_facts(facts_input)
    assert narrative.source == "deterministic_fallback"


# ---------------------------------------------------------------------------
# Рендер: narrative только в «Что уже удалось установить»; байт-стабильность
# ---------------------------------------------------------------------------


def _render(model, quick):
    return render_quick_model(
        model, today=date(2026, 10, 7), zone_regulations=quick.zone_regulations
    )


def _established_section(markdown: str) -> str:
    return markdown.split("## Что уже удалось установить", 1)[1].split(
        "## Факторы, требующие внимания", 1
    )[0]


def test_render_without_narrative_byte_stable(quick, model):
    reference = _render(model, quick)
    assert reference == _render(model, quick)
    assert "Возможность реализовать ваш замысел требует подробного анализа." in reference


def test_render_with_deterministic_fallback_byte_identical(quick, model, facts_input):
    baseline = _render(model, quick)
    narrative = DeterministicNarrativeGenerator().generate_established_facts(
        facts_input
    )
    narrated = model.model_copy(
        update={
            "narrative": QuickNarrative(
                established_summary=narrative.text,
                source=narrative.source,
                input_sha256=narrative.input_sha256,
            )
        }
    )
    assert _render(narrated, quick) == baseline


def test_render_with_llm_narrative_only_in_established(quick, model, facts_input):
    text = (
        "По кадастровым сведениям известны площадь 1 000 м² и разрешённое "
        "использование. Территориальная зона участка установлена, "
        "пространственный анализ выполнен."
    )
    executor = FakeNarrativeExecutor({"text": text})
    narrative = _hermes(executor).generate_established_facts(facts_input)
    assert narrative.source == "llm"
    baseline = _render(model, quick)
    narrated = model.model_copy(
        update={
            "narrative": QuickNarrative(
                established_summary=narrative.text,
                source="llm",
                model_name=narrative.model_name,
                prompt_version=narrative.prompt_version,
                input_sha256=narrative.input_sha256,
                latency_ms=narrative.latency_ms,
                attempts=narrative.attempts,
            )
        }
    )
    markdown = _render(narrated, quick)
    section = _established_section(markdown)
    # LLM-текст заменяет deterministic-список внутри секции
    assert text in section
    assert "- По кадастровым сведениям" not in section
    # и не влияет на остальные секции
    assert markdown.count(text) == 1
    assert "## Оценка участка — 55/100" in markdown
    assert model.status.status_summary in markdown
    assert "| 1 | Водоохранная зона | Практически весь участок |" in markdown
    assert "Может влиять на размещение дома" not in markdown  # guidance не из LLM
    # секции вне established байт-совпадают с baseline
    assert markdown.split("## Что уже удалось установить", 1)[0] == baseline.split(
        "## Что уже удалось установить", 1
    )[0]
    assert markdown.split("## Факторы, требующие внимания", 1)[1] == baseline.split(
        "## Факторы, требующие внимания", 1
    )[1]
    from terralogic_engine.qa import validate_quickreport

    assert validate_quickreport(narrated, markdown, context=quick).status == "pass"


# ---------------------------------------------------------------------------
# Валидатор: narrative-проверки (§64)
# ---------------------------------------------------------------------------


def _model_with_narrative(model, text, *, source="llm"):
    return model.model_copy(
        update={
            "narrative": QuickNarrative(established_summary=text, source=source)
        }
    )


def test_validator_catches_narrative_drop(quick, model, facts_input):
    from terralogic_engine.qa import validate_quickreport

    text = "По кадастровым сведениям известны площадь 1 000 м² и вид использования."
    narrated = _model_with_narrative(model, text)
    markdown = _render(model, quick)  # рендер БЕЗ narrative — текст потерян
    result = validate_quickreport(narrated, markdown, context=quick)
    assert result.status == "fail"
    assert any(i.rule_id == "narrative_in_report" for i in result.issues)


def test_validator_catches_narrative_substitution(quick, model, facts_input):
    from terralogic_engine.qa import validate_quickreport

    text = "По кадастровым сведениям известны площадь 1 000 м²."
    narrated = _model_with_narrative(model, text)
    markdown = _render(narrated, quick).replace(text, "Подменённый текст секции.")
    result = validate_quickreport(narrated, markdown, context=quick)
    assert result.status == "fail"
    assert any(i.rule_id == "narrative_in_report" for i in result.issues)


def test_validator_catches_llm_narrative_violations(quick, model):
    from terralogic_engine.qa import validate_quickreport

    # источник llm без текста
    narrated = _model_with_narrative(model, None)
    markdown = _render(model, quick)
    result = validate_quickreport(narrated, markdown, context=quick)
    assert any(i.rule_id == "narrative_llm_text" for i in result.issues)

    # чужое число в llm narrative
    narrated = _model_with_narrative(model, "Площадь 2500 м² подтверждена.")
    markdown = _render(narrated, quick)
    result = validate_quickreport(narrated, markdown, context=quick)
    assert any(i.rule_id == "narrative_numbers_in_context" for i in result.issues)

    # категоричная фраза
    narrated = _model_with_narrative(model, "Участок юридически чист.")
    markdown = _render(narrated, quick)
    result = validate_quickreport(narrated, markdown, context=quick)
    assert any(i.rule_id == "narrative_llm_text" for i in result.issues)


# ---------------------------------------------------------------------------
# Сквозные: prepare_quickreport с генератором и критиком (§61, §74, §77)
# ---------------------------------------------------------------------------


async def _prepare(
    tmp_path, generator=None, critic=None, *, writer=None
) -> tuple[object, LocalCaseStore]:
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
        narrative_generator=generator, critic=critic, writer=writer,
    )
    return service.prepare_quickreport("case-writer"), store


def _qa(store, result) -> str:
    return (store.cases_root / result.case_id / result.qa_relative_path).read_text(
        encoding="utf-8"
    )


async def test_quickreport_without_generator_is_deterministic(tmp_path) -> None:
    result, store = await _prepare(tmp_path)
    assert (
        "## Оценка участка —" in result.markdown
        or "Оценка не рассчитана" in result.markdown
    )
    qa = _qa(store, result)
    assert "## Narrative: отключён (детерминированный текст)" in qa
    assert "## Revision: не требовалась" in qa


async def test_quickreport_with_llm_narrative(tmp_path) -> None:
    text = (
        "По кадастровым сведениям известны площадь участка и разрешённое "
        "использование. Территориальная зона установлена, пространственный "
        "анализ выполнен."
    )
    executor = FakeNarrativeExecutor({"text": text})
    result, store = await _prepare(tmp_path, generator=_hermes(executor))
    assert text in result.markdown
    assert len(executor.prompts) == 1
    qa = _qa(store, result)
    assert "## Narrative: llm (применён)" in qa
    assert f"- модель: {MODEL_NAME}" in qa
    assert f"- версия промпта: {WRITER_PROMPT_VERSION}" in qa
    assert "- источник: llm" in qa
    assert "- попыток: 1" in qa
    assert "- latency:" in qa
    assert "- sha256 входа:" in qa
    # model_name сохранён с отчётом, балл не зависит от LLM (§83)
    report = store.get_latest_generated_report(
        result.case_id, result.collection_run_id
    )
    assert report is not None and report.model_name == MODEL_NAME
    assert re.search(r"## Оценка (участка — \d+/100|не рассчитана)", result.markdown)


async def test_quickreport_compat_shim_writer_executor(tmp_path) -> None:
    """Старый writer-executor оборачивается в HermesNarrativeGenerator."""
    text = "По кадастровым сведениям известны площадь и вид использования участка."
    executor = FakeNarrativeExecutor({"text": text})
    result, store = await _prepare(tmp_path, writer=executor)
    assert text in result.markdown
    assert "## Narrative: llm (применён)" in _qa(store, result)


async def test_quickreport_generator_dead_keeps_deterministic(tmp_path) -> None:
    executor = FakeNarrativeExecutor(fail="exception")
    result, store = await _prepare(tmp_path, generator=_hermes(executor))
    assert result.report_id
    assert any("LLM narrative недоступен" in w for w in result.warnings)
    qa = _qa(store, result)
    assert "## Narrative: deterministic_fallback" in qa
    assert "- источник: deterministic_fallback" in qa
    # fallback не меняет deterministic Markdown (run id в имени PNG различается)
    deterministic, _ = await _prepare(tmp_path / "plain")
    normalize = lambda md: re.sub(r"quickreport-run-[0-9a-f]+\.png", "map.png", md)
    assert normalize(result.markdown) == normalize(deterministic.markdown)
    report = store.get_latest_generated_report(
        result.case_id, result.collection_run_id
    )
    assert report is not None and report.model_name is None


def _critic_payload(section: str, severity: str = "error") -> dict:
    return {
        "status": "needs_revision",
        "issues": [
            {
                "rule_id": "overstatement",
                "section": section,
                "severity": severity,
                "message": "формулировка слишком категорична",
                "claim": None,
                "evidence_ids": [],
                "suggested_action": "смягчить",
            }
        ],
    }


async def test_targeted_revision_established_section(tmp_path) -> None:
    """Revision запускается только по error/critical секции established (§74)."""
    executor = FakeNarrativeExecutor(
        {"text": "Первичная формулировка раздела."},
        {"text": "Уточнённая формулировка после замечания критика."},
    )
    critic = FakeCritic(_critic_payload("Что уже удалось установить"))
    result, store = await _prepare(tmp_path, generator=_hermes(executor), critic=critic)
    assert "Уточнённая формулировка после замечания критика." in result.markdown
    assert "Первичная формулировка раздела." not in result.markdown
    assert len(executor.prompts) == 2
    assert "формулировка слишком категорична" in executor.prompts[1]
    assert "## Revision: принята" in _qa(store, result)


async def test_revision_skipped_for_other_sections(tmp_path) -> None:
    """Замечание по другой секции не запускает revision."""
    executor = FakeNarrativeExecutor({"text": "Первичная формулировка раздела."})
    critic = FakeCritic(_critic_payload("Оценка участка"))
    result, store = await _prepare(tmp_path, generator=_hermes(executor), critic=critic)
    assert "Первичная формулировка раздела." in result.markdown
    assert len(executor.prompts) == 1
    qa = _qa(store, result)
    assert "не выполнялась: нет error/critical" in qa


async def test_revision_skipped_for_warning_level(tmp_path) -> None:
    executor = FakeNarrativeExecutor({"text": "Первичная формулировка раздела."})
    critic = FakeCritic(_critic_payload("Что уже удалось установить", "warning"))
    result, store = await _prepare(tmp_path, generator=_hermes(executor), critic=critic)
    assert "Первичная формулировка раздела." in result.markdown
    assert len(executor.prompts) == 1
    qa = _qa(store, result)
    assert "→ pass (только warning/info, порог severity)" in qa
    assert "## Revision: не требовалась" in qa


async def test_revision_rejected_on_fallback(tmp_path) -> None:
    """Revision отклоняется, если повторный прогон не дал valid LLM-текст."""
    executor = FakeNarrativeExecutor(
        {"text": "Первичная формулировка раздела."},
        {"text": "Можно строить, гарантированно."},  # banned → отклонено рамками
    )
    critic = FakeCritic(_critic_payload("Что уже удалось установить"))
    result, store = await _prepare(tmp_path, generator=_hermes(executor), critic=critic)
    assert "Первичная формулировка раздела." in result.markdown
    assert "гарантированно" not in result.markdown
    qa = _qa(store, result)
    assert "Revision: отклонена" in qa
