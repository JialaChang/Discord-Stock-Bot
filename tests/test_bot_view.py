"""Embed title assembly. Importing dc_bot_view pulls in discord but touches no network, token or database"""
from src.bot.dc_bot_view import display_name


class TestDisplayName:
    def test_a_known_name_is_qualified_by_its_ticker(self):
        assert display_name('台積電', '2330.TW') == '台積電 (2330.TW)'

    def test_an_unknown_name_shows_the_ticker_once(self):
        # fetch_stock_name() returns the ticker for anything not in the database.
        assert display_name('BRK-B', 'BRK-B') == 'BRK-B'
