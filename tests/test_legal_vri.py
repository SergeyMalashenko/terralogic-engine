"""Классификатор ВРИ (Приказ Росреестра № П/0412): импорт, lookup, обогащение."""

from __future__ import annotations

import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from terralogic_engine.analytics.models import AnalysisResult, IntersectionSummary
from terralogic_engine.domain.models import GeoFeature
from terralogic_engine.legal import (
    LegalKnowledgeService,
    VriImportError,
    VriRepository,
    classifier_json,
    import_classifier,
)
from terralogic_engine.legal.repository import PACKAGED_CLASSIFIER
from terralogic_engine.reporting.context import _parcel_context

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

_HEADER = [
    "Наименование вида разрешенного использования",
    "Описание вида разрешенного использования",
    "Код вида разрешенного использования",
]
_ITEMS = [
    ["Сельскохозяйственное использование", "Описание сельхозгруппы", "1.0"],
    ["Жилая застройка", "Описание жилой группы", "2.0"],
    ["Для индивидуального жилищного строительства", "размещение ИЖС", "2.1"],
    [
        "Для ведения личного подсобного хозяйства (приусадебный земельный участок)",
        "ЛПХ",
        "2.2",
    ],
    ["Магазины", "торговля", "4.4"],
    ["Ремонт автомобилей", "ремонт", "4.9.1.4"],  # четырёхуровневый код
]
# строки реального DOCX, не являющиеся позициями классификатора:
# номера граф после заголовка и редакционные примечания КонсультантПлюс
_NON_ITEM_ROWS = [
    ["1", "2", "3"],
    ["(в ред. Приказа Росреестра от 30.07.2021 N П/0326)", "", ""],
    ["(введено Приказом Росреестра от 23.06.2022 N П/0246)", "", ""],
]


def _write_docx(path: Path, rows: list[list[str]]) -> Path:
    def cell(text: str) -> str:
        return f"<w:tc><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:tc>"

    body = "".join(
        "<w:tr>" + "".join(cell(value) for value in row) + "</w:tr>"
        for row in rows
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<w:document xmlns:w="{_W_NS}"><w:body><w:tbl>{body}</w:tbl></w:body>'
        "</w:document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)
    return path


@pytest.fixture()
def classifier(tmp_path: Path):
    rows = [_HEADER, _NON_ITEM_ROWS[0], _ITEMS[0], _NON_ITEM_ROWS[1], *_ITEMS[1:]]
    rows.extend([_NON_ITEM_ROWS[2]])
    docx = _write_docx(tmp_path / "vri.docx", rows)
    return import_classifier(docx, min_entries=2)


@pytest.fixture()
def repository(classifier, tmp_path: Path) -> VriRepository:
    path = tmp_path / "vri.json"
    path.write_text(classifier_json(classifier), encoding="utf-8")
    return VriRepository.from_json(path)


# ---------------------------------------------------------------------------
# Импортер DOCX → JSON
# ---------------------------------------------------------------------------


def test_importer_happy_path(classifier):
    # служебные строки (номера граф, ред.-примечания) отброшены
    assert len(classifier.items) == 6
    entry = classifier.items[2]
    assert entry.code == "2.1"
    assert entry.name == "Для индивидуального жилищного строительства"
    assert entry.description == "размещение ИЖС"
    assert classifier.items[5].code == "4.9.1.4"  # четырёхуровневый код
    assert classifier.source.act_number == "П/0412"
    assert classifier.revision_date.isoformat() == "2026-06-02"


def test_importer_continuation_row_extends_description(tmp_path: Path):
    rows = [
        _HEADER,
        ["Жилая застройка", "Первая часть описания", "2.0"],
        ["", "продолжение описания", ""],
        ["Магазины", "торговля", "4.4"],
    ]
    classifier = import_classifier(_write_docx(tmp_path / "vri.docx", rows), min_entries=2)
    assert len(classifier.items) == 2
    assert classifier.items[0].description == "Первая часть описания\nпродолжение описания"


def test_importer_duplicate_code_fails(tmp_path: Path):
    rows = [_HEADER, *_ITEMS, ["Магазины (дубль)", "торговля", "4.4"]]
    with pytest.raises(VriImportError, match="дублирующийся код"):
        import_classifier(_write_docx(tmp_path / "vri.docx", rows), min_entries=2)


def test_importer_empty_name_fails(tmp_path: Path):
    rows = [_HEADER, ["Жилая застройка", "текст", "2.0"], ["", "текст", "2.1"]]
    with pytest.raises(VriImportError, match="пустое наименование"):
        import_classifier(_write_docx(tmp_path / "vri.docx", rows), min_entries=2)


def test_importer_bad_code_fails(tmp_path: Path):
    rows = [_HEADER, ["Жилая застройка", "текст", "два.один"]]
    with pytest.raises(VriImportError, match="не соответствует формату"):
        import_classifier(_write_docx(tmp_path / "vri.docx", rows), min_entries=1)


def test_importer_without_table_fails(tmp_path: Path):
    docx = tmp_path / "empty.docx"
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<w:document xmlns:w="{_W_NS}"><w:body>'
        "<w:p><w:r><w:t>Текст без таблицы</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    with zipfile.ZipFile(docx, "w") as archive:
        archive.writestr("word/document.xml", document)
    with pytest.raises(VriImportError, match="таблица классификатора не найдена"):
        import_classifier(docx, min_entries=1)


def test_importer_suspiciously_small_table_fails(tmp_path: Path):
    docx = _write_docx(tmp_path / "vri.docx", [_HEADER, *_ITEMS])
    with pytest.raises(VriImportError, match="неполную таблицу"):
        import_classifier(docx)  # дефолтный минимум 100 позиций


# ---------------------------------------------------------------------------
# Репозиторий и сервис
# ---------------------------------------------------------------------------


def test_repository_lookup(repository: VriRepository):
    assert repository.get("2.1").name == "Для индивидуального жилищного строительства"
    assert repository.get(" 2.1 ").code == "2.1"  # код с пробелами
    assert repository.get("9.9") is None
    assert len(repository.all()) == 6
    # точное совпадение по наименованию: регистр и лишние пробелы не важны
    assert repository.match_name("для индивидуального  жилищного строительства").code == "2.1"
    # алиас: каноническое наименование без уточнения в скобках
    assert repository.match_name("для ведения личного подсобного хозяйства").code == "2.2"
    assert repository.match_name("ИЖС") is None  # аббревиатуры не угадываем
    assert repository.match_name("") is None


def test_service_resolution(repository: VriRepository):
    service = LegalKnowledgeService(repository)
    resolution = service.resolve_vri("2.1")
    assert resolution is not None
    assert resolution.name == "Для индивидуального жилищного строительства"
    assert resolution.act_number == "П/0412"
    assert resolution.revision_date == "2026-06-02"
    assert service.resolve_vri("9.9") is None
    assert service.resolve_vri(None) is None
    matched = service.match_vri("магазины")
    assert matched is not None and matched.code == "4.4"


def test_service_unavailable_without_data():
    service = LegalKnowledgeService(None)
    assert service.available is False
    assert service.resolve_vri("2.1") is None
    assert service.match_vri("ИЖС") is None


def test_service_from_env_missing_file_warns(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TERRALOGIC_VRI_CLASSIFIER", str(tmp_path / "absent.json"))
    with pytest.warns(UserWarning, match="классификатор ВРИ не найден"):
        service = LegalKnowledgeService.from_env()
    assert service.available is False


# ---------------------------------------------------------------------------
# Обогащение паспорта участка (reporting)
# ---------------------------------------------------------------------------


def _parcel_analysis() -> AnalysisResult:
    return AnalysisResult(
        id="analysis-1",
        case_id="case-1",
        collection_run_id="run-1",
        aoi_id="aoi-1",
        analytics_version="1.0",
        calculated_at=datetime(2026, 10, 12, tzinfo=UTC),
        parcel_feature_id="f-1",
        parcel_area_m2=1640.0,
        metric_crs="EPSG:3857",
        search_radius_m=500.0,
        zouit_summary=IntersectionSummary(
            key="zouit", name="ЗОУИТ", candidate_count=0, intersecting_count=0
        ),
    )


def _parcel_feature(permitted_use: str) -> GeoFeature:
    return GeoFeature(
        id="f-1",
        case_id="case-1",
        snapshot_id="snap-1",
        source="nspd",
        source_type="land_parcel",
        feature_class="parcel",
        geometry={"type": "MultiPolygon", "coordinates": []},
        properties={
            "cadastral_number": "50:11:0020310:49",
            "permitted_use": permitted_use,
            "area_m2": 1640,
        },
    )


def test_parcel_context_enriches_vri(
    classifier, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    json_path = tmp_path / "vri.json"
    json_path.write_text(classifier_json(classifier), encoding="utf-8")
    monkeypatch.setenv("TERRALOGIC_VRI_CLASSIFIER", str(json_path))

    parcel = _parcel_context(
        [_parcel_feature("для индивидуального жилищного строительства")],
        _parcel_analysis(),
    )
    # source fact сохранён как есть
    assert parcel.permitted_use == "для индивидуального жилищного строительства"
    assert parcel.permitted_use_vri is not None
    assert parcel.permitted_use_vri.code == "2.1"
    assert parcel.permitted_use_vri.name == "Для индивидуального жилищного строительства"
    assert parcel.permitted_use_vri.act_number == "П/0412"


def test_parcel_context_keeps_unmatched_vri(
    classifier, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    json_path = tmp_path / "vri.json"
    json_path.write_text(classifier_json(classifier), encoding="utf-8")
    monkeypatch.setenv("TERRALOGIC_VRI_CLASSIFIER", str(json_path))

    parcel = _parcel_context(
        [_parcel_feature("Свободный текст, которого нет в классификаторе")],
        _parcel_analysis(),
    )
    assert parcel.permitted_use == "Свободный текст, которого нет в классификаторе"
    assert parcel.permitted_use_vri is None


# ---------------------------------------------------------------------------
# Пакетный снимок реального классификатора (после импорта DOCX)
# ---------------------------------------------------------------------------

requires_packaged = pytest.mark.skipif(
    not PACKAGED_CLASSIFIER.exists(),
    reason="классификатор ещё не импортирован из DOCX (legal/sources/)",
)


@requires_packaged
def test_packaged_classifier_key_codes():
    service = LegalKnowledgeService(VriRepository.packaged())
    expected = {
        "1.0": "Сельскохозяйственное использование",
        "2.0": "Жилая застройка",
        "2.1": "Для индивидуального жилищного строительства",
        "4.4": "Магазины",
    }
    for code, name in expected.items():
        resolution = service.resolve_vri(code)
        assert resolution is not None, f"код {code} отсутствует в классификаторе"
        assert resolution.name == name
    assert service.resolve_vri("99.99") is None
