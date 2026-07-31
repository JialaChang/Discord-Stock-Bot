import os
from string import Template
from functools import lru_cache
from typing import Any


_TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), 'templates', 'report.html')

# A table cell is either plain text, or (text, css_class) to color it (e.g. 'up' / 'down').
Cell = str | tuple[str, str]


@lru_cache(maxsize=1)
def _template() -> Template:
    """Load and cache the report template ($title / $meta / $body placeholders)."""
    with open(_TEMPLATE_PATH, encoding='utf-8') as f:
        return Template(f.read())


def fmt_num(v: Any, decimals: int = 2) -> str:
    """Format a numeric value to a fixed number of decimals, or 'N/A' when missing."""
    return f'{v:.{decimals}f}' if v is not None else 'N/A'


def fmt_int(v: Any) -> str:
    """Format an integer value with thousands separators, or 'N/A' when missing."""
    return f'{v:,.0f}' if v is not None else 'N/A'


def html_table(headers: list[str] | None, rows: list[list[Cell]]) -> str:
    """Build a ``<table>`` from data.

    ``headers`` renders a sticky ``<thead>`` (pass ``None`` for a headerless table).
    Each cell in ``rows`` is plain text, or a ``(text, css_class)`` tuple to color it.
    """
    parts = ['<table>']
    if headers:
        head = ''.join(f'<th>{h}</th>' for h in headers)
        parts.append(f'<thead><tr>{head}</tr></thead>')
    body_rows = []
    for row in rows:
        cells = []
        for cell in row:
            if isinstance(cell, tuple):
                text, cls = cell
                cells.append(f'<td class="{cls}">{text}</td>')
            else:
                cells.append(f'<td>{cell}</td>')
        body_rows.append('<tr>' + ''.join(cells) + '</tr>')
    parts.append('<tbody>\n' + '\n'.join(body_rows) + '\n</tbody>')
    parts.append('</table>')
    return '\n'.join(parts)


def html_document(title: str, body: str, *, subtitle: str | None = None) -> str:
    """Inject ``body`` into the shared report template.

    ``title`` is used for both the browser tab and the heading; ``subtitle`` renders as
    muted meta text just below the heading.
    """
    meta = f'<div class="meta">{subtitle}</div>' if subtitle else ''
    return _template().substitute(title=title, meta=meta, body=body)
