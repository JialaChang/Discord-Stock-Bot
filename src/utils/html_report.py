import html
import os
import re
from datetime import datetime
from string import Template
from functools import lru_cache
from typing import Any


_TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), 'templates', 'report.html')

_EXPORT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'exports')

# A table cell is either plain text, or (text, css_class) to color it (e.g. 'up' / 'down').
Cell = str | tuple[str, str]

# Anything outside this set is replaced, so a ticker cannot steer the path out of exports/.
_UNSAFE_IN_FILENAME = re.compile(r'[^A-Za-z0-9._-]')


def _esc(value: Any) -> str:
    """Escape a value for insertion into HTML. Callers never write entities by hand."""
    return html.escape(str(value), quote=True)


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
    Headers and cells are escaped, so pass them as plain text.
    """
    parts = ['<table>']
    if headers:
        head = ''.join(f'<th>{_esc(h)}</th>' for h in headers)
        parts.append(f'<thead><tr>{head}</tr></thead>')
    body_rows = []
    for row in rows:
        cells = []
        for cell in row:
            if isinstance(cell, tuple):
                text, cls = cell
                cells.append(f'<td class="{_esc(cls)}">{_esc(text)}</td>')
            else:
                cells.append(f'<td>{_esc(cell)}</td>')
        body_rows.append('<tr>' + ''.join(cells) + '</tr>')
    parts.append('<tbody>\n' + '\n'.join(body_rows) + '\n</tbody>')
    parts.append('</table>')
    return '\n'.join(parts)


def html_document(title: str, body: str, *, subtitle: str | None = None) -> str:
    """Inject ``body`` into the shared report template.

    ``title`` is used for both the browser tab and the heading; ``subtitle`` renders as
    muted meta text just below the heading. Both are escaped; ``body`` is the only
    parameter taken as raw HTML, and is meant to come from ``html_table``.
    """
    meta = f'<div class="meta">{_esc(subtitle)}</div>' if subtitle else ''
    return _template().substitute(title=_esc(title), meta=meta, body=body)


def write_report(html_text: str, name: str) -> str:
    """Write a report to ``exports/<name>_<timestamp>.html`` and return the path."""
    stem = _UNSAFE_IN_FILENAME.sub('_', name).lstrip('.') or 'report'
    os.makedirs(_EXPORT_DIR, exist_ok=True)
    filepath = os.path.join(_EXPORT_DIR, f'{stem}_{datetime.now():%Y%m%d_%H%M%S}.html')
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(html_text)
    return filepath
