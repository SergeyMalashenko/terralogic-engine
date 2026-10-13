"""Unit-тесты нативного приёма фактов из geodocs query_documents (§80 ТЗ).

Legacy-проекция в конверты удалённых инструментов удалена: факты строятся
напрямую из нативного ответа (data.zones / data.regimes + documents).
"""

from __future__ import annotations

from terralogic_engine.acquisition.normalize import (
    document_vri_facts_from_geodocs,
    zouit_regime_facts_from_geodocs,
)

from .fakes import geodocs_vri_query_result, geodocs_zouit_query_result

_KW = {"case_id": "case-t", "subject_feature_id": "feat-parcel", "snapshot_id": "snap-1"}


def test_vri_facts_native_path():
    facts = document_vri_facts_from_geodocs(
        result=geodocs_vri_query_result(), **_KW
    )
    (fact,) = facts
    assert fact.fact_type == "document_vri"
    assert fact.quality == "extracted"
    assert fact.value["zone_code"] == "218020020006"
    assert fact.value["doc_number"] == "ПЗЗ-Т-592"
    assert fact.value["version_date"] == "2021-04-03"
    assert fact.value["source_file"] == "pzz-592.docx"
    assert fact.value["confidence"] == 0.93
    assert len(fact.value["items"]) == 2
    assert fact.value["snapshot_id"] == "snap-1"


def test_vri_facts_native_skips_unfound_zone():
    result = geodocs_vri_query_result()
    result["data"]["zones"][0]["found"] = False
    assert document_vri_facts_from_geodocs(result=result, **_KW) == []


def test_vri_facts_native_prefers_extraction_with_items():
    result = geodocs_vri_query_result()
    zone = result["data"]["zones"][0]
    empty = {
        "version_id": 9002,
        "status": "extracted",
        "extractor": "pdf",
        "table": {"zone_code": "218020020006", "items": []},
    }
    zone["extractions"] = [empty, *zone["extractions"]]
    facts = document_vri_facts_from_geodocs(result=result, **_KW)
    (fact,) = facts
    assert fact.value["items"]  # взята extraction с items, не первая пустая
    assert fact.value["doc_number"] == "ПЗЗ-Т-592"


def test_vri_facts_native_empty_without_data():
    assert document_vri_facts_from_geodocs(result={"status": "success"}, **_KW) == []
    assert document_vri_facts_from_geodocs(result={"status": "failed"}, **_KW) == []


def test_regime_facts_native_path():
    facts = zouit_regime_facts_from_geodocs(
        result=geodocs_zouit_query_result(), **_KW
    )
    (fact,) = facts
    assert fact.fact_type == "zouit_regime"
    assert fact.quality == "registered"
    assert fact.value["registry_number"] == "50:32-6.1"
    assert fact.value["name"] == "Тестовая охранная зона"
    assert fact.value["zone_type"] == "охранная зона"
    assert fact.value["registration_date"] == "2015-05-12"
    assert fact.value["restrictions"] == "Запрет строительства"
    # реквизиты документа подтянуты по version_id из documents[]
    assert fact.value["document_number"] == "Постановление № 111"


def test_regime_facts_native_without_document_match():
    result = geodocs_zouit_query_result()
    result["documents"] = []  # документ неизвестен — факт всё равно строится
    (fact,) = zouit_regime_facts_from_geodocs(result=result, **_KW)
    assert fact.value["document_number"] == ""


def test_regime_facts_native_empty_without_data():
    assert zouit_regime_facts_from_geodocs(result={"status": "failed"}, **_KW) == []
