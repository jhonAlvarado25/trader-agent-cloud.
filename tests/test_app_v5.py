from pathlib import Path
import unittest
from unittest.mock import patch
import os
import streamlit as st
from streamlit.testing.v1 import AppTest
from test_v51 import feed_fixture, snapshot, RULES

APP = Path(__file__).resolve().parents[1] / "app.py"


class AppTests(unittest.TestCase):
    def setUp(self):
        st.cache_data.clear()

    def test_single_input_no_tabs_and_fixed_risk(self):
        feed = feed_fixture()
        feed["candidate"] = None
        with patch("dashboard_v51.fetch_feed", return_value=feed):
            at = AppTest.from_file(str(APP), default_timeout=10).run()
            self.assertEqual(len(at.exception), 0)
            self.assertEqual(len(at.number_input), 1)
            self.assertEqual(len(at.tabs), 0)
            self.assertEqual(len(at.selectbox), 0)
            self.assertIn("15.000", at.metric[0].value)
            at.number_input(key="capital_cop").set_value(2_000_000).run()
            self.assertEqual(len(at.exception), 0)
            self.assertIn("30.000", at.metric[0].value)

    def test_binance_values_appear_automatically_and_recalculate(self):
        with patch("dashboard_v51.fetch_feed", return_value=feed_fixture()), \
             patch("auto_decision_v5.quote", return_value=snapshot()), \
             patch("auto_decision_v5.exchange_rules", return_value=RULES):
            at = AppTest.from_file(str(APP), default_timeout=10).run()
            self.assertEqual(len(at.exception), 0)
            table = at.dataframe[0].value
            qty1 = float(table.loc[table["Campo"] == "Cantidad (ETH)", "Valor"].iloc[0])
            self.assertEqual(len(at.button), 0)
            at.number_input(key="capital_cop").set_value(2_000_000).run()
            self.assertEqual(len(at.exception), 0)
            table = at.dataframe[0].value
            qty2 = float(table.loc[table["Campo"] == "Cantidad (ETH)", "Valor"].iloc[0])
            self.assertAlmostEqual(qty2, qty1*2, delta=.0010001)

    def test_no_feed_never_claims_monitor_is_active(self):
        with patch("dashboard_v51.fetch_feed", side_effect=RuntimeError("no feed")):
            at = AppTest.from_file(str(APP), default_timeout=10).run()
        self.assertEqual(len(at.exception), 0)
        self.assertEqual(len(at.code), 0)
        self.assertTrue(any("no puedo confirmar" in x.value for x in at.warning))

    def test_weak_signal_only_shows_market_summary(self):
        with patch("dashboard_v51.fetch_feed", return_value=feed_fixture("EN OBSERVACIÓN")):
            at = AppTest.from_file(str(APP), default_timeout=10).run()
        self.assertEqual(len(at.exception), 0)
        self.assertEqual(len(at.code), 0)
        self.assertTrue(any("observar" in x.value for x in at.subheader))

    def test_realtime_screen_recalculates_without_rest_or_old_feed(self):
        from test_v52 import prepared
        engine, now = prepared()
        feed = engine.tick(now)
        feed["telegram_status"] = "SIN CONFIGURAR"
        with patch.dict(os.environ, {"TRADER_REALTIME_URL": "http://localhost:8787/snapshot"}), \
             patch("realtime_v52.fetch_realtime", return_value=feed), \
             patch("auto_decision_v5.quote") as rest, patch("dashboard_v51.fetch_feed") as old:
            at = AppTest.from_file(str(APP), default_timeout=10).run()
            self.assertEqual(len(at.exception), 0)
            self.assertEqual(len(at.number_input), 1)
            self.assertEqual(len(at.code), 1)
            at.number_input(key="capital_cop").set_value(2_000_000).run()
            self.assertEqual(len(at.exception), 0)
            self.assertIn("30.000", at.metric[0].value)
        rest.assert_not_called()
        old.assert_not_called()

    def test_realtime_outage_hides_orders_without_fallback(self):
        with patch.dict(os.environ, {"TRADER_REALTIME_URL": "http://localhost:8787/snapshot"}), \
             patch("realtime_v52.fetch_realtime", side_effect=RuntimeError("disconnected")), \
             patch("dashboard_v51.fetch_feed") as old:
            at = AppTest.from_file(str(APP), default_timeout=10).run()
            self.assertEqual(len(at.exception), 0)
            self.assertEqual(len(at.code), 0)
        old.assert_not_called()


if __name__ == "__main__":
    unittest.main()
