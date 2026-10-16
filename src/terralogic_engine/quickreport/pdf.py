"""PDF-версия QuickReport: Markdown → HTML → PDF (weasyprint).

Опциональный extra ``pdf`` (markdown + weasyprint); при отсутствии
библиотек или сбое рендера отчёт не падает — возвращается предупреждение.
Ссылки на изображения в Markdown относительные (``../maps/…``) — базовым
каталогом для их разрешения служит каталог отчётов кейса.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

LOGGER = logging.getLogger(__name__)

_H2_RE = re.compile(r"(<h2>.*?</h2>)", flags=re.DOTALL)


def _wrap_sections(body: str) -> str:
    """Оборачивает каждый раздел h2 в <div class="section">.

    Для CSS break-inside: avoid — раздел не разрывается между страницами:
    если целиком не помещается на текущей, переносится на следующую.
    """
    parts = _H2_RE.split(body)
    result = [parts[0]]
    for i in range(1, len(parts), 2):
        h2 = parts[i]
        content = parts[i + 1] if i + 1 < len(parts) else ""
        result.append(f'<div class="section">{h2}{content}</div>')
    return "".join(result)

_PAGE_CSS = """
@page { size: A4; margin: 18mm 16mm; }
body { font-family: "DejaVu Sans", "Liberation Sans", sans-serif;
       font-size: 10.5pt; line-height: 1.45; color: #1a1a1a; }
h1 { font-size: 17pt; margin: 0 0 2mm; }
h2 { font-size: 13pt; margin: 6mm 0 2mm; border-bottom: 1px solid #999;
     padding-bottom: 1mm; }
h3 { font-size: 11.5pt; margin: 4mm 0 1.5mm; }
.section { break-inside: avoid; }
table { border-collapse: collapse; width: 100%; font-size: 9.5pt; }
th, td { border: 1px solid #999; padding: 1.2mm 2mm; vertical-align: top;
         text-align: left; }
th { background: #eee; }
img { max-width: 75%; display: block; margin: 0 auto; }
ul { margin: 1mm 0 2mm; padding-left: 5mm; }
li { margin-bottom: 0.8mm; }
"""


def markdown_to_pdf(
    markdown_text: str,
    out_path: Path,
    *,
    base_dir: Path,
) -> list[str]:
    """Рендерит Markdown отчёта в PDF; возвращает предупреждения.

    ``base_dir`` — каталог, относительно которого резолвятся ссылки на
    изображения (каталог reports/ кейса: ссылка ``../maps/x.png`` укажет
    на артефакт карты).
    """
    try:
        import markdown as markdown_lib
        import weasyprint
    except ImportError:
        return ["pdf не построен: не установлен extra 'pdf' (markdown, weasyprint)"]
    try:
        body = markdown_lib.markdown(markdown_text, extensions=["tables", "sane_lists"])
        html = (
            '<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">'
            f"<style>{_PAGE_CSS}</style></head><body>{_wrap_sections(body)}</body></html>"
        )
        weasyprint.HTML(string=html, base_url=str(base_dir)).write_pdf(out_path)
    except Exception as exc:
        LOGGER.warning("PDF quickreport не построен", exc_info=exc)
        return [f"pdf не построен: {exc}"]
    return []
