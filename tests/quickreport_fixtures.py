"""SHORT/NORMAL/LONG fixtures for QuickReport regression checks."""

from __future__ import annotations

from datetime import date

from terralogic_engine.quickreport import (
    build_quick_context_from_report,
    build_quick_model,
    load_marketing,
    render_quick_model,
)
from terralogic_engine.quickreport.models import QuickReportContext
from terralogic_engine.reporting.models import VriReportContext

from .test_quickreport import _CONFIG_OK, _base_context, _with_pzz, _zouit

FIXTURE_DATE = date(2026, 10, 7)
FIXTURE_KINDS = ("short", "normal", "long")
FIXTURE_EXPECTATIONS: dict[str, tuple[int, str, int]] = {
    "short": (85, "ok", 1),
    "normal": (35, "attention", 3),
    "long": (27, "attention", 5),
}

LONG_ADDRESS = (
    "Российская Федерация, Московская область, городской округ с очень длинным "
    "официальным наименованием, сельское поселение, посёлок у дальнего леса, "
    "территория садоводческого некоммерческого товарищества, улица Центральная, "
    "дом 1"
)

LONG_ZONE_NAMES = (
    "Водоохранная зона реки с историческим наименованием и уточнением бассейна",
    "Прибрежная защитная полоса водного объекта федерального значения",
    "Шестая подзона приаэродромной территории аэродрома Москва (Шереметьево)",
    "Охранная зона высоковольтной линии электропередачи 750 кВ с уточнением опоры",
    "Санитарно-защитная зона действующего производственного объекта первого класса",
)


def build_fixture_context(kind: str) -> QuickReportContext:
    base = _with_pzz(_base_context())
    if kind == "short":
        base.parcel.address = "Московская область, Красногорск"
        base.zouit = [
            _zouit("Водоохранная зона р. Банка", "Водоохранная зона", 10.0)
        ]
    elif kind == "normal":
        base.parcel.permitted_use_vri = VriReportContext(
            code="2.1",
            name="Для индивидуального жилищного строительства",
            act_number="П/0412",
            revision_date="2026-06-02",
        )
        base.zouit = [
            *base.zouit,
            _zouit(
                "Охранная зона ЛЭП 750 кВ",
                "Электроэнергетические объекты",
                15.0,
            ),
        ]
    elif kind == "long":
        base.parcel.address = LONG_ADDRESS
        base.zouit = [
            _zouit(LONG_ZONE_NAMES[0], "Водоохранная зона", 50.0),
            _zouit(LONG_ZONE_NAMES[1], "Прибрежная защитная полоса", 20.0),
            _zouit(LONG_ZONE_NAMES[2], "Приаэродромная территория", 40.0),
            _zouit(LONG_ZONE_NAMES[3], "Электроэнергетические объекты", 5.0),
            _zouit(LONG_ZONE_NAMES[4], "Санитарно-защитная зона", 5.0),
        ]
        nearest = base.natural_nearest[0]
        base.natural_nearest = [
            nearest,
            *[
                nearest.model_copy(
                    update={
                        "object_name": name,
                        "category_name": category,
                        "distance_m": distance,
                    }
                )
                for name, category, distance in (
                    ("пруд Длинный", "Водные объекты", 120.0),
                    ("роща Сосновая", "Леса", 240.0),
                    ("река Большая", "Реки", 360.0),
                    ("ручей Лесной", "Ручьи", 480.0),
                    ("пруд Верхний с очень длинным названием", "Водные объекты", 600.0),
                )
            ],
        ]
    else:
        raise ValueError(f"Unknown QuickReport fixture: {kind!r}")
    return build_quick_context_from_report(base, configuration=_CONFIG_OK)


def render_fixture_markdown(kind: str) -> str:
    context = build_fixture_context(kind)
    model = build_quick_model(context, marketing=load_marketing())
    return render_quick_model(
        model,
        today=FIXTURE_DATE,
        zone_regulations=context.zone_regulations,
    )
