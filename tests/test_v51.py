from __future__ import annotations
from copy import deepcopy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
import pandas as pd

from dashboard_v51 import order_for_capital, public_plan, select_candidate, binance_rows
from fx_v51 import automatic_fx, validate_fx
from profile_v5 import TradingProfile, load_profile
from monitor_v51 import run_scan, opportunity_message, overview
from publish_market_v51 import publish

RULES = {"tick_size": ".01", "step_size": ".001", "min_qty": .001, "max_qty": 100000,
         "min_notional": 5, "max_notional": 0, "min_price": .01, "max_price": 100000}


def feed_fixture(evidence="FUERTE"):
    now = pd.Timestamp.now(tz="UTC")
    signal = {"symbol": "ETHUSDT", "instrument": "FUTURES", "direction": "LONG", "timeframe": "1h",
              "key": "V51-unit", "entry": 100, "stop": 98, "take_profit": 104, "atr": 1,
              "evidence": evidence, "expires_at": (now+pd.Timedelta(minutes=10)).isoformat(),
              "created_at": now.isoformat(), "development": {"validation": {"expectancy_r": .2, "n": 30}}}
    fx = {"cop_per_usdt": 4000, "trm": 4000/1.03, "buffer_pct": .03,
          "valid_from": (now-pd.Timedelta(days=1)).date().isoformat(), "valid_to": (now+pd.Timedelta(days=1)).date().isoformat()}
    return {"version": "5.1", "started_at": now.isoformat(), "finished_at": now.isoformat(), "fx": fx,
            "candidate": signal, "pause": None, "health": "ACTUALIZADO", "errors": [],
            "markets": [{"symbol": "ETHUSDT", "price": 100.0, "change_24h": 1.2, "verdict": "Candidata", "reason": "Regla validada"}]}


def snapshot(instrument="FUTURES"):
    return {"price": 100, "bid": 100, "ask": 100, "spread_fraction": 0, "instrument": instrument,
            "funding_rate": 0, "funding_interval_hours": 8,
            "quoted_at": pd.Timestamp.now(tz="UTC").isoformat(), "source": "Binance nativo (test)"}


class SimplePolicyTests(unittest.TestCase):
    def test_default_and_loaded_risk_are_fixed_15(self):
        self.assertEqual(TradingProfile().risk_budget_cop, 15000)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"profile.json"
            path.write_text(json.dumps({"risk_pct": .005, "max_open_risk_pct": .01}))
            p = load_profile(path, {"TRADER_RISK_PCT": ".05"})
            self.assertEqual(p.risk_pct, .015)
            self.assertEqual(p.max_open_risk_pct, .015)

    def test_capital_alone_recalculates_quantity_at_fixed_risk(self):
        feed = feed_fixture()
        with patch("auto_decision_v5.quote", return_value=snapshot()), patch("auto_decision_v5.exchange_rules", return_value=RULES):
            first = order_for_capital(feed, 1_000_000)["order"]
            second = order_for_capital(feed, 2_000_000)["order"]
        self.assertLessEqual(first["net_loss_cop"], 15000)
        self.assertLessEqual(second["net_loss_cop"], 30000)
        self.assertAlmostEqual(second["qty"], first["qty"]*2, delta=.0010001)
        self.assertTrue(first["conversion_estimated"])

    def test_stale_report_never_uses_a_quote(self):
        feed = feed_fixture()
        feed["finished_at"] = (pd.Timestamp.now(tz="UTC")-pd.Timedelta(minutes=13)).isoformat()
        with patch("auto_decision_v5.quote") as quote:
            self.assertIsNone(order_for_capital(feed, 1_000_000)["order"])
            quote.assert_not_called()

    def test_weak_signal_never_displays_execution_fields(self):
        with patch("auto_decision_v5.quote") as quote:
            result = order_for_capital(feed_fixture("EN OBSERVACIÓN"), 1_000_000)
            self.assertIsNone(result["order"])
            self.assertEqual(result["action"], "SOLO OBSERVAR")
            quote.assert_not_called()

    def test_expired_signal_does_not_have_order_values(self):
        feed = feed_fixture()
        feed["candidate"]["expires_at"] = (pd.Timestamp.now(tz="UTC")-pd.Timedelta(seconds=1)).isoformat()
        self.assertIsNone(order_for_capital(feed, 1_000_000)["order"])

    def test_selection_uses_validation_not_best_test(self):
        a, b = feed_fixture()["candidate"], deepcopy(feed_fixture()["candidate"])
        a["stats"] = {"expectancy_test_r": -999}
        b["stats"] = {"expectancy_test_r": 999}
        b["development"]["validation"]["expectancy_r"] = .1
        self.assertIs(select_candidate([a,b]), a)

    def test_public_plan_drops_capital_profile_and_secrets(self):
        signal = {**feed_fixture()["candidate"], "capital_cop": 999, "profile": {"private": True}, "api_key": "secret"}
        plan = public_plan(signal)
        self.assertNotIn("profile", plan)
        self.assertNotIn("capital_cop", plan)
        self.assertNotIn("api_key", plan)

    def test_telegram_has_no_stale_capital_or_quantities(self):
        text = opportunity_message(feed_fixture()["candidate"])
        self.assertIn("1,5%", text)
        self.assertNotIn("Cantidad:", text)
        self.assertNotIn("1.000.000", text)


class AutomaticFxTests(unittest.TestCase):
    def test_trm_reference_is_automatic_and_explicitly_estimated(self):
        response = Mock()
        response.json.return_value = [{"valor": "4000", "vigenciadesde": "2026-10-03", "vigenciahasta": "2026-10-05"}]
        with patch("fx_v51.requests.get", return_value=response):
            fx = automatic_fx(pd.Timestamp("2026-10-04T15:00:00Z"))
        self.assertEqual(fx["cop_per_usdt"], 4120)
        self.assertTrue(fx["estimated"])

    def test_stale_or_missing_fx_blocks_sizing(self):
        with self.assertRaises(ValueError):
            validate_fx(None)
        feed = feed_fixture()
        feed["fx"]["valid_to"] = "2020-01-01"
        with self.assertRaises(ValueError):
            order_for_capital(feed, 1_000_000)


class MonitorAndPublicationTests(unittest.TestCase):
    def test_market_scan_continues_during_a_simulated_risk_pause(self):
        state = {"journal": [], "sent_keys": []}
        answer = {"state": "NO OPERAR", "reason": "Sin señal", "data_errors": []}
        with patch("monitor_v51.automatic_fx", return_value=feed_fixture()["fx"]), \
             patch("monitor_v51.overview", return_value={}), patch("monitor_v51.risk_stop", return_value="Pausa PAPER"), \
             patch("monitor_v51.market_brief", return_value={"trend": "Alcista"}), \
             patch("monitor_v51.automatic_recommendation", return_value=answer) as scan, patch("monitor_v51.save_state"):
            report = run_scan(state, ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
        self.assertEqual(scan.call_count, 3)
        self.assertEqual(len(report["markets"]), 3)
        self.assertEqual(report["pause"], "Pausa PAPER")
        self.assertNotIn("journal", report)
        self.assertNotIn("capital_cop", report)

    def test_market_error_is_visible_instead_of_success(self):
        with patch("monitor_v51.automatic_fx", return_value=feed_fixture()["fx"]), \
             patch("monitor_v51.overview", return_value={}), patch("monitor_v51.save_state"), \
             patch("monitor_v51.automatic_recommendation", side_effect=RuntimeError("451")):
            report = run_scan({"journal": [], "sent_keys": []}, ["ETHUSDT"])
        self.assertEqual(report["health"], "PARCIAL")
        self.assertEqual(report["markets"][0]["verdict"], "Datos no disponibles")
        self.assertIsNone(report["candidate"])

    def test_publisher_rejects_private_fields_before_network(self):
        feed = feed_fixture()
        feed["candidate"]["profile"] = {"capital_cop": 1000000}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"feed.json"
            path.write_text(json.dumps(feed))
            with patch("publish_market_v51.requests.Session") as network, self.assertRaises(ValueError):
                publish(path)
            network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
