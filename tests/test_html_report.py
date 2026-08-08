"""The report layer escapes on behalf of its callers, so no caller writes entities by hand."""
import os

import pytest

from src.utils.html_report import fmt_int, fmt_num, html_document, html_table, write_report


class TestEscaping:
    def test_header_with_an_ampersand(self):
        assert '<th>P&amp;L</th>' in html_table(['P&L'], [])

    def test_cell_text(self):
        # Latent until a report gains a column of company names: 'AT&T', 'Procter & Gamble'.
        assert '<td>AT&amp;T Inc.</td>' in html_table(None, [['AT&T Inc.']])

    def test_cell_markup_cannot_break_out(self):
        out = html_table(None, [['<script>alert(1)</script>']])
        assert '<script>' not in out
        assert '&lt;script&gt;' in out

    def test_cell_css_class(self):
        assert '<td class="up">1.50</td>' in html_table(None, [[('1.50', 'up')]])

    def test_title_and_subtitle(self):
        out = html_document('A & B', '<p>x</p>', subtitle='C & D')
        assert 'A &amp; B' in out and 'C &amp; D' in out

    def test_body_is_raw_html(self):
        # html_table already escaped its inputs; escaping again would show the tags.
        assert '<table>' in html_document('t', '<table></table>')

    def test_nothing_is_escaped_twice(self):
        assert '&amp;amp;' not in html_document('A & B', html_table(['P&L'], [['A & B']]))


class TestWriteReport:
    def test_writes_into_exports(self, tmp_path, monkeypatch):
        monkeypatch.setattr('src.utils.html_report._EXPORT_DIR', str(tmp_path))
        path = write_report('<p>x</p>', '2330.TW_prices')
        assert os.path.dirname(path) == str(tmp_path)
        assert os.path.basename(path).startswith('2330.TW_prices_')
        assert open(path, encoding='utf-8').read() == '<p>x</p>'

    @pytest.mark.parametrize("name", ['../../etc/passwd', '/abs/path', '..', 'a/b\\c'])
    def test_a_hostile_name_cannot_escape_exports(self, tmp_path, monkeypatch, name):
        monkeypatch.setattr('src.utils.html_report._EXPORT_DIR', str(tmp_path))
        path = write_report('<p>x</p>', name)
        assert os.path.dirname(os.path.abspath(path)) == str(tmp_path)


class TestFormatting:
    def test_missing_values_read_as_na(self):
        assert fmt_num(None) == 'N/A'
        assert fmt_int(None) == 'N/A'

    def test_numbers(self):
        assert fmt_num(1234.5678) == '1234.57'
        assert fmt_num(1234.5678, decimals=0) == '1235'
        assert fmt_int(1234567) == '1,234,567'
