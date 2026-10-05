from __future__ import annotations
from dataclasses import replace
from decimal import Decimal
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
import pandas as pd
import numpy as np

from profile_v5 import TradingProfile, load_profile, goal_summary, profile_json
from risk_v5 import size_order, rules_from_exchange, step_round
from statistics_v5 import partitions, selection_score, final_evidence, block_bootstrap
from state_v5 import load_state, save_state, deliver_once
from journal_v5 import journal_summary, risk_stop, validate_records
from auto_decision_v5 import automatic_recommendation, revalidate_entry, apply_regime
from market_v5 import history, quote, funding_reserve, funding_history, exchange_rules
from monitor_v5 import update_paper, open_paper_record, remaining_profile
from telegram_v5 import message
from telegram_notify import send_message
from futures_lab import simulate_config, LabCosts

RULES = {"tick_size": ".01", "step_size": ".001", "min_qty": .001, "max_qty": 100000,
         "min_notional": 5, "max_notional": 0, "min_price": .01, "max_price": 100000}


def profile(**kwargs):
    return replace(TradingProfile(cop_per_usdt=4000, fx_confirmed=True, risk_pct=.005, max_open_risk_pct=.01), **kwargs)


class ProfileTests(unittest.TestCase):
    def test_goal_annual_not_monthly(self):
        g = goal_summary(profile())
        self.assertEqual(g["annual_gain_min_cop"], 150000)
        self.assertEqual(g["annual_gain_max_cop"], 200000)
        self.assertAlmostEqual((1+g["monthly_rate_min"])**12, 1.15)

    def test_env_capital_updates_available(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"p.json"
            path.write_text(profile_json(profile()), encoding="utf-8")
            p = load_profile(path, {"TRADER_CAPITAL_COP": "2000000"})
            self.assertEqual(p.capital_cop, 2_000_000)
            self.assertEqual(p.available_cop, 2_000_000)

    def test_bad_parameters(self):
        for fields in ({"capital_cop": float("nan")}, {"risk_pct": .05}, {"available_cop": 2_000_000},
                       {"futures_leverage": 3}, {"mode": "AUTOTRADE"}, {"fx_confirmed": "false"}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                profile(**fields)

    def test_total_risk_budget(self):
        self.assertEqual(profile(committed_risk_cop=8000).risk_budget_cop, 2000)
        self.assertEqual(profile(committed_risk_cop=11000).risk_budget_cop, 0)


class SizingTests(unittest.TestCase):
    def sized(self, p=None, direction="LONG", instrument="FUTURES", rules=None):
        return size_order(p or profile(), 100, 98 if direction == "LONG" else 102,
                          104 if direction == "LONG" else 96, direction, instrument, rules or RULES)

    def test_capital_doubles_amount_not_risk_fraction(self):
        a, b = self.sized(), self.sized(profile(capital_cop=2_000_000, available_cop=2_000_000))
        self.assertAlmostEqual(b["qty"], a["qty"]*2, delta=.00100001)
        self.assertLessEqual(a["net_loss_cop"], 5000)
        self.assertLessEqual(b["net_loss_cop"], 10000)

    def test_spot_futures_long_short_limits(self):
        for instrument, direction in (("SPOT", "LONG"), ("FUTURES", "LONG"), ("FUTURES", "SHORT")):
            order = self.sized(direction=direction, instrument=instrument)
            self.assertLessEqual(order["net_loss_cop"], 5000)
            self.assertGreater(order["net_gain_cop"], 0)
            self.assertGreaterEqual(order["rr_net"], 1.5)
            self.assertEqual(Decimal(order["qty_text"]) % Decimal(RULES["step_size"]), 0)
        with self.assertRaises(ValueError):
            self.sized(direction="SHORT", instrument="SPOT")

    def test_available_margin_includes_cost_buffer(self):
        p = profile(available_cop=50000)
        order = self.sized(p)
        self.assertLessEqual(order["margin_cop"]+order["costs_cop"], 50000)

    def test_exchange_minimum_does_not_round_up(self):
        with self.assertRaises(ValueError):
            self.sized(rules={**RULES, "min_notional": 100})

    def test_committed_risk_and_pilot_confirmation(self):
        self.assertLessEqual(self.sized(profile(committed_risk_cop=8000))["net_loss_cop"], 2000)
        with self.assertRaises(ValueError):
            self.sized(profile(mode="PILOTO_MANUAL", fx_confirmed=False))

    def test_invalid_or_unprofitable_levels(self):
        for e, s, t in ((100, 101, 104), (100, 98, 99), (float("nan"), 98, 104), (100, 98, 100.1)):
            with self.subTest(levels=(e,s,t)), self.assertRaises(ValueError):
                size_order(profile(), e, s, t, "LONG", "SPOT", RULES)

    def test_exchange_rules_not_precision_placeholders(self):
        info = {"status": "TRADING", "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": ".05", "minPrice": ".05", "maxPrice": "10000"},
            {"filterType": "LOT_SIZE", "stepSize": ".003", "minQty": ".003", "maxQty": "10000"},
            {"filterType": "MIN_NOTIONAL", "notional": "10"}]}
        rules = rules_from_exchange(info)
        self.assertEqual(rules["min_notional"], 10)
        self.assertEqual(step_round(1.001, ".003"), Decimal(".999"))


class ValidationTests(unittest.TestCase):
    def trades(self):
        start = pd.Timestamp("2020-01-01", tz="UTC")
        times = pd.date_range(start, periods=1000, freq="D")
        return pd.DataFrame({"entry_time": times, "exit_time": times+pd.Timedelta(hours=1),
                             "net_r": np.tile([1.8,1.8,-1.1,1.8,-1.1],200)})

    def test_boundary_trades_are_purged(self):
        tr = self.trades()
        start, end = tr.entry_time.min(), tr.exit_time.max()
        cut = start+(end-start)*.8
        tr.loc[0, ["entry_time", "exit_time"]] = [cut-pd.Timedelta(hours=1), cut+pd.Timedelta(hours=1)]
        parts = partitions(tr, start, end)
        self.assertFalse(any(0 in f.index for f in parts.values()))

    def test_test_results_cannot_affect_selection(self):
        tr = self.trades()
        start, end = tr.entry_time.min(), tr.exit_time.max()
        first = selection_score(tr, start, end)
        tr.loc[tr.entry_time >= start+(end-start)*.8, "net_r"] = -1000
        second = selection_score(tr, start, end)
        self.assertEqual(first, second)

    def test_block_bootstrap_and_final_gates(self):
        tr = self.trades()
        st = final_evidence(tr, tr.copy(), tr.entry_time.min(), tr.exit_time.max(), simulations=500)
        self.assertEqual(st["evidence"], "FUERTE")
        self.assertGreater(st["ci_low"], 0)
        self.assertEqual(len(st["walk_forward"]), 4)
        self.assertLess(block_bootstrap([-1]*100, 500)["ci_high"], 0)

    def test_failed_test_does_not_try_runner_up(self):
        cs = [{"instrument": "SPOT", "direction": "LONG", "setup": "STRICT_PULLBACK", "timeframe": "1h", "tag": 1},
              {"instrument": "SPOT", "direction": "LONG", "setup": "BALANCED_PULLBACK", "timeframe": "4h", "tag": 2}]
        def evaluate(symbol, c, p):
            return {"candidate": c, "selection_ok": True, "score": (3-c["tag"],), "development": {}}
        with patch("auto_decision_v5.evaluate_for_selection", side_effect=evaluate), \
                patch("auto_decision_v5.selected_test", return_value={"evidence": "INSUFICIENTE"}) as final:
            r = automatic_recommendation("BTCUSDT", candidates=cs, profile=profile())
            self.assertEqual(r["state"], "NO OPERAR")
            self.assertEqual(final.call_count, 1)
            self.assertEqual(final.call_args.args[0]["candidate"]["tag"], 1)


class DataAndExecutionTests(unittest.TestCase):
    def test_daily_regime_never_reads_a_future_close(self):
        daily_times = pd.date_range("2025-01-01", periods=250, freq="D", tz="UTC")
        daily = pd.DataFrame({"open_time": daily_times, "close_time": daily_times+pd.Timedelta(hours=23),
                              "open": 100, "high": 102, "low": 99, "close": np.arange(250)+100, "volume": 10})
        frame = pd.DataFrame({"close_time": [daily_times[-1]+pd.Timedelta(hours=12)], "long_signal": [True]})
        merged = apply_regime(frame, daily, "LONG")
        self.assertLessEqual(merged.iloc[0].daily_close_time, merged.iloc[0].close_time)
        self.assertEqual(merged.iloc[0].daily_close_time, daily.iloc[-2].close_time)

    def test_native_quote_uses_futures_endpoints_only(self):
        now_ms = int(pd.Timestamp.now(tz="UTC").timestamp()*1000)
        def response(path, params, timeout):
            return {"/fapi/v1/ticker/bookTicker": {"bidPrice": "100", "askPrice": "100.01"},
                    "/fapi/v1/ticker/price": {"price": "100.005"},
                    "/fapi/v1/premiumIndex": {"markPrice": "100", "lastFundingRate": ".0001", "time": now_ms, "nextFundingTime": now_ms+1000},
                    "/fapi/v1/fundingInfo": [{"symbol": "UNITUSDT", "fundingIntervalHours": 4}]}[path]
        with patch("market_v5.futures_request", side_effect=response), patch("market_v5.spot_request") as spot:
            result = quote("UNITUSDT", "FUTURES")
            self.assertEqual(result["funding_interval_hours"], 4)
            spot.assert_not_called()

    def test_futures_451_uses_explicit_research_fallback(self):
        times = pd.date_range("2026-01-01", periods=300, freq="h", tz="UTC")
        fallback = pd.DataFrame({
            "open_time": times,
            "close_time": times + pd.Timedelta(hours=1) - pd.Timedelta(milliseconds=1),
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0,
        })
        with patch("market_v5.futures_request", side_effect=RuntimeError("451")), \
             patch("market_v5.fallback_futures_klines", return_value=fallback), \
             patch("market_v5.get_futures_data_source", return_value="Spot proxy test"):
            result = history("NATIVEONLYUSDT", "1h", 300, "FUTURES")
        self.assertEqual(result.attrs["source"], "Spot proxy test")

    def signal(self):
        now = pd.Timestamp.now(tz="UTC")
        return {"key": "V5-unit", "symbol": "ETHUSDT", "instrument": "FUTURES", "direction": "LONG",
                "entry": 100, "stop": 98, "take_profit": 104, "atr": 1,
                "expires_at": (now+pd.Timedelta(minutes=10)).isoformat(), "profile": json.loads(profile_json(profile()))}

    def snapshot(self, price):
        return {"price": price, "bid": price, "ask": price, "spread_fraction": 0,
                "instrument": "FUTURES", "funding_rate": .0001, "funding_interval_hours": 8,
                "quoted_at": pd.Timestamp.now(tz="UTC").isoformat()}

    def test_proxy_futures_quote_forces_manual_binance_recheck(self):
        now = pd.Timestamp.now(tz="UTC")
        signal = self.signal()
        proxy = {
            "price": 100, "bid": 100, "ask": 100, "spread_fraction": .001,
            "instrument": "FUTURES", "funding_rate": .0001, "funding_interval_hours": 8,
            "quoted_at": now.isoformat(), "quote_quality": "PROXY_SPOT",
            "source": "Binance Spot público como proxy conservador de Futures",
        }
        with patch("auto_decision_v5.exchange_rules", return_value=RULES):
            result = revalidate_entry(signal, snapshot=proxy)
        self.assertEqual(result["action"], "REVALIDAR EN BINANCE")
        self.assertTrue(result["order"]["execution_requires_binance_check"])

    def test_expired_alert_has_no_order(self):
        s = self.signal()
        s["expires_at"] = (pd.Timestamp.now(tz="UTC")-pd.Timedelta(seconds=1)).isoformat()
        self.assertIsNone(revalidate_entry(s)["order"])

    def test_price_change_recalculates_size(self):
        with patch("auto_decision_v5.exchange_rules", return_value=RULES):
            a = revalidate_entry(self.signal(), snapshot=self.snapshot(100))["order"]
            b = revalidate_entry(self.signal(), snapshot=self.snapshot(100.1))["order"]
            self.assertNotEqual(a["qty"], b["qty"])
            self.assertLessEqual(b["net_loss_cop"], 5000)

    def test_revalidation_preserves_the_new_profile_not_the_old_one(self):
        p = profile(capital_cop=2_000_000, available_cop=2_000_000, futures_fee_each_side=.0006)
        with patch("auto_decision_v5.exchange_rules", return_value=RULES):
            order = revalidate_entry(self.signal(), p, self.snapshot(100))["order"]
        self.assertEqual(order["profile"]["capital_cop"], 2_000_000)
        self.assertEqual(order["profile"]["futures_fee_each_side"], .0006)
        self.assertLessEqual(order["net_loss_cop"], 10000)

    def test_nonfinite_funding_history_is_rejected(self):
        now_ms = int(pd.Timestamp.now(tz="UTC").timestamp()*1000)
        rows = [{"fundingTime": now_ms, "fundingRate": float("nan"), "markPrice": "100"}]
        with patch("market_v5.futures_request", return_value=rows), self.assertRaises(RuntimeError):
            funding_history("INVALIDFUNDINGUSDT", now_ms-1000, now_ms+1000)

    def test_unknown_market_never_defaults_to_futures(self):
        for func in (quote, exchange_rules):
            with self.subTest(func=func.__name__), self.assertRaises(ValueError):
                func("BTCUSDT", "INVALID")

    def test_market_mismatch_and_bad_quote_block(self):
        snap = self.snapshot(100)
        snap["instrument"] = "SPOT"
        with self.assertRaises(ValueError):
            revalidate_entry(self.signal(), snapshot=snap)
        self.assertEqual(revalidate_entry(self.signal(), snapshot=self.snapshot(105))["action"], "NO ENTRAR")

    def test_funding_reserve_is_not_invented_zero(self):
        self.assertGreater(funding_reserve(self.snapshot(100)), 0)

    def test_weak_paper_signal_cannot_be_promoted_by_switching_mode(self):
        s = self.signal()
        s["evidence"] = "EN OBSERVACIÓN"
        with self.assertRaises(ValueError):
            revalidate_entry(s, profile(mode="PILOTO_MANUAL"), self.snapshot(100))


class DeliveryAndJournalTests(unittest.TestCase):
    def test_failed_delivery_is_retryable_and_ack_is_atomic(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"state.json"
            state = load_state(path)
            with self.assertRaises(RuntimeError):
                deliver_once(state, {"key": "s1"}, Mock(side_effect=RuntimeError("failed")), path)
            self.assertNotIn("s1", state["sent_keys"])
            record = {"id": "s1", "kind": "PAPER", "status": "PENDING", "risk_cop": 5000}
            self.assertTrue(deliver_once(state, {"key": "s1"}, Mock(), path, lambda: state["journal"].append(record)))
            self.assertIn("s1", load_state(path)["sent_keys"])
            self.assertEqual(len(load_state(path)["journal"]), 1)
            self.assertFalse(deliver_once(state, {"key": "s1"}, Mock(), path))

    def test_corrupt_state_does_not_reset_silently(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"state.json"
            path.write_text("not-json")
            with self.assertRaises(ValueError):
                load_state(path)

    def test_telegram_requires_json_ack(self):
        response = Mock(status_code=200)
        response.json.return_value = {"ok": False}
        with patch("telegram_notify.requests.post", return_value=response), self.assertRaises(RuntimeError):
            send_message("unit-token", "unit-chat", "unit-message")

    def test_real_and_paper_are_not_combined(self):
        records = [{"id": "r", "kind": "REAL_MANUAL", "status": "CLOSED", "net_pnl_cop": 100},
                   {"id": "p", "kind": "PAPER", "status": "CLOSED", "net_pnl_cop": -200}]
        self.assertEqual(journal_summary(records, "REAL_MANUAL")["net_cop"], 100)
        self.assertEqual(journal_summary(records, "PAPER")["net_cop"], -200)

    def test_daily_loss_stop(self):
        now = pd.Timestamp.now(tz="UTC")
        records = [{"id": "p", "kind": "PAPER", "status": "CLOSED", "closed_at": now.isoformat(),
                    "capital_cop": 1_000_000, "net_pnl_cop": -20000}]
        self.assertIn("diaria", risk_stop(profile(), records, now))

    def test_missing_history_does_not_invent_an_unfilled_order(self):
        now = pd.Timestamp.now(tz="UTC")
        record = {"id": "p", "kind": "PAPER", "status": "PENDING", "symbol": "ETHUSDT", "instrument": "SPOT",
                  "created_at": (now-pd.Timedelta(hours=1)).isoformat(), "expires_at": (now-pd.Timedelta(minutes=50)).isoformat()}
        empty = pd.DataFrame({"open_time": pd.Series([], dtype="datetime64[ns, UTC]")})
        with patch("monitor_v5.history", return_value=empty), self.assertRaises(RuntimeError):
            update_paper(record, now)
        self.assertEqual(record["status"], "PENDING")

    def test_active_shadows_reserve_cash_and_not_only_risk(self):
        active = [{"risk_cop": 4000, "margin_cop": 700000, "costs_cop": 3000}]
        p = remaining_profile(profile(), active)
        self.assertEqual(p.available_cop, 297000)
        self.assertEqual(p.committed_risk_cop, 4000)
        order = size_order(p, 100, 99.9, 102, "LONG", "SPOT", RULES)
        self.assertLessEqual(order["margin_cop"]+order["costs_cop"], 297000)
        self.assertEqual(remaining_profile(profile(), active+active).available_cop, 0)
        self.assertEqual(remaining_profile(profile(), [{"risk_cop": 4000}]).available_cop, 0)

    def test_shadow_spot_stop_uses_the_conservative_limit_buffer(self):
        start = pd.Timestamp("2025-01-01", tz="UTC")
        record = {"status": "OPEN", "symbol": "UNITUSDT", "instrument": "SPOT", "direction": "LONG",
                  "created_at": start.isoformat(), "opened_at": start.isoformat(),
                  "entry": 100, "stop": 98, "limit_sl": 97.88, "take_profit": 104, "qty": 1,
                  "fee_each_side": .001, "slippage_each_side": .0003, "cop_per_usdt": 4000}
        candles = pd.DataFrame({"open_time": [start], "close_time": [start+pd.Timedelta(minutes=1)-pd.Timedelta(milliseconds=1)],
                                "open": [100], "high": [101], "low": [97], "close": [98]})
        with patch("monitor_v5.history", return_value=candles):
            update_paper(record, start+pd.Timedelta(minutes=2))
        self.assertEqual(record["status"], "CLOSED")
        self.assertEqual(record["exit"], 97.88)
        self.assertLess(record["net_pnl_usdt"], -2.12)


class BacktestRegressionTests(unittest.TestCase):
    def frame(self):
        times = pd.date_range("2025-01-01", periods=300, freq="h", tz="UTC")
        df = pd.DataFrame({"open_time": times, "close_time": times+pd.Timedelta(hours=1)-pd.Timedelta(milliseconds=1),
                           "open": 100.0, "close": 100.0, "high": 100.5, "low": 99.5,
                           "atr": 1.0, "swing_low_10": 99.0, "swing_high_10": 101.0,
                           "long_signal": False, "short_signal": False})
        df.loc[220, "long_signal"] = True
        return df

    def test_gap_stop_loss_is_not_capped_at_one_r(self):
        df = self.frame()
        df.loc[221, "low"] = 90
        df.loc[222, ["open", "low", "high"]] = [90, 89, 100]
        df.loc[221, "low"] = 99
        trades = simulate_config(df, pd.DataFrame(), "LONG", "FUTURES", 1.25, 2, "1h", LabCosts())
        self.assertLess(trades.iloc[0].gross_r, -1)

    def test_funding_uses_actual_mark_not_entry_notional(self):
        df = self.frame()
        df.loc[221, "high"] = 103
        funding = pd.DataFrame({"funding_time": [df.loc[221, "open_time"]+pd.Timedelta(minutes=5)],
                                "funding_rate": [.001], "mark_price": [200.0]})
        trades = simulate_config(df, funding, "LONG", "FUTURES", 1.25, 2, "1h", LabCosts())
        expected_r = .001*200/100/trades.iloc[0].risk_pct
        self.assertAlmostEqual(trades.iloc[0].funding_r, expected_r)

    def test_same_bar_stop_precedes_take_profit(self):
        df = self.frame()
        df.loc[221, ["low", "high"]] = [97, 105]
        trades = simulate_config(df, pd.DataFrame(), "LONG", "FUTURES", 1.25, 2, "1h", LabCosts())
        self.assertEqual(trades.iloc[0].outcome, "LOSS")


if __name__ == "__main__":
    unittest.main()
