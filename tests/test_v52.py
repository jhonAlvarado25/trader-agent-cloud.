from __future__ import annotations
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch, Mock
import pandas as pd
import requests

from test_v51 import feed_fixture, RULES
from realtime_v52 import Supervisor, QuoteBook, order_for_realtime
from service_v52 import Service, preflight, AccessBlocked, streams, http_server


def prepared():
    now = pd.Timestamp.now(tz="UTC")
    supervisor = Supervisor(["ETHUSDT"])
    report = feed_fixture()
    report["market_rules"] = {"symbol": "ETHUSDT", "instrument": "FUTURES", "rules": RULES,
                              "funding_interval_hours": 8, "checked_at": now.isoformat()}
    supervisor.research = report
    update(supervisor, now)
    return supervisor, now


def update(supervisor, now, price=100):
    stamp = int(now.timestamp()*1000)
    for market in ("SPOT", "FUTURES"):
        supervisor.book.ingest(market, {"stream": "ethusdt@bookTicker", "data": {
            "s": "ETHUSDT", "E": stamp, "u": stamp, "b": str(price), "a": str(price)}}, now)
        supervisor.book.ingest(market, {"e": "aggTrade", "s": "ETHUSDT", "E": stamp, "a": stamp, "p": str(price)}, now)
    supervisor.book.ingest("SPOT", {"e": "24hrMiniTicker", "s": "ETHUSDT", "E": stamp, "c": "100", "o": "99"}, now)
    supervisor.book.ingest("FUTURES", {"e": "markPriceUpdate", "s": "ETHUSDT", "E": stamp,
                                     "p": "100", "r": "0", "T": stamp+3_600_000}, now)


class RealtimeRiskTests(unittest.TestCase):
    def test_each_capital_sizes_without_any_rest_call(self):
        engine, now = prepared()
        with patch("auto_decision_v5.quote") as quote, patch("auto_decision_v5.exchange_rules") as rules:
            feed = engine.tick(now)
            first = order_for_realtime(feed, 1_000_000, now)["order"]
            second = order_for_realtime(feed, 2_000_000, now)["order"]
        quote.assert_not_called()
        rules.assert_not_called()
        self.assertLessEqual(first["net_loss_cop"], 15000)
        self.assertAlmostEqual(second["qty"], 2*first["qty"], delta=.001001)
        self.assertEqual(first["profile"]["risk_pct"], .015)
        self.assertNotIn("profile", feed["candidate"])
        self.assertNotIn("capital_cop", json.dumps(feed))

    def test_heartbeat_does_not_make_old_quotes_fresh(self):
        engine, now = prepared()
        first = engine.tick(now)
        later = now+pd.Timedelta(seconds=4)
        report = engine.tick(later)
        self.assertNotEqual(first["finished_at"], report["finished_at"])
        self.assertIsNone(report["live_quote"])
        self.assertIsNone(report["markets"][0]["price"])
        self.assertFalse(report["entry_valid"])

    def test_market_mismatch_and_nonfinite_rejected(self):
        engine, now = prepared()
        feed = engine.tick(now)
        feed["live_quote"]["symbol"] = "BTCUSDT"
        with self.assertRaises(ValueError):
            order_for_realtime(feed, 1_000_000, now)
        feed = engine.tick(now)
        feed["live_quote"]["price"] = float("nan")
        with self.assertRaises(ValueError):
            order_for_realtime(feed, 1_000_000, now)

    def test_stale_research_rules_quote_and_worker_each_block(self):
        engine, now = prepared()
        for field, seconds in (("finished_at", 4), ("research_at", 721)):
            feed = engine.tick(now)
            feed[field] = (now-pd.Timedelta(seconds=seconds)).isoformat()
            self.assertIsNone(order_for_realtime(feed, 1_000_000, now)["order"])
        for field, age in (("live_quote", 4), ("market_rules", 3601)):
            feed = engine.tick(now)
            feed[field]["quoted_at" if field == "live_quote" else "checked_at"] = (now-pd.Timedelta(seconds=age)).isoformat()
            self.assertIsNone(order_for_realtime(feed, 1_000_000, now)["order"])

    def test_expiry_and_weak_evidence_block(self):
        engine, now = prepared()
        engine.research["candidate"]["expires_at"] = (now-pd.Timedelta(seconds=1)).isoformat()
        self.assertFalse(engine.tick(now)["entry_valid"])
        engine, now = prepared()
        engine.research["candidate"]["evidence"] = "EN OBSERVACIÓN"
        self.assertFalse(engine.tick(now)["entry_valid"])

    def test_crossing_and_rebound_cannot_revive_same_signal(self):
        engine, now = prepared()
        engine.tick(now)
        crossed = now+pd.Timedelta(milliseconds=100)
        update(engine, crossed, 97)
        engine.observe(crossed)
        rebound = now+pd.Timedelta(milliseconds=200)
        update(engine, rebound, 100)
        self.assertTrue(engine.tick(rebound)["invalidated"])
        self.assertFalse(engine.tick(rebound)["entry_valid"])

    def test_gap_and_reconnect_require_a_new_signal(self):
        engine, now = prepared()
        self.assertTrue(engine.tick(now)["entry_valid"])
        engine.book.clear("FUTURES")
        self.assertFalse(engine.tick(now)["entry_valid"])
        later = now+pd.Timedelta(seconds=1)
        update(engine, later)
        self.assertFalse(engine.tick(later)["entry_valid"])

    def test_duplicate_book_cannot_refresh_timestamp(self):
        engine, now = prepared()
        stamp = int(now.timestamp()*1000)
        engine.book.ingest("SPOT", {"stream": "ethusdt@bookTicker", "data": {
            "s": "ETHUSDT", "u": stamp, "b": "100", "a": "100"}}, now+pd.Timedelta(seconds=2))
        original = engine.book.components[("SPOT", "ETHUSDT", "book")]["at"]
        self.assertLess(abs((pd.Timestamp(original)-now).total_seconds()), .002)

    def test_late_exchange_events_rejected(self):
        engine, now = prepared()
        with self.assertRaises(ValueError):
            engine.book.ingest("FUTURES", {"s": "ETHUSDT", "e": "aggTrade", "p": "100", "E": 1}, now)

    def test_futures_requires_mark_and_native_book(self):
        engine, now = prepared()
        engine.book.components.pop(("FUTURES", "ETHUSDT", "mark"))
        self.assertFalse(engine.tick(now)["entry_valid"])

    def test_same_setup_cannot_move_stop_or_extend_expiry(self):
        engine, now = prepared()
        old = deepcopy(engine.research["candidate"])
        revised = deepcopy(engine.research)
        revised["candidate"]["stop"] = 95
        revised["candidate"]["expires_at"] = (now+pd.Timedelta(hours=1)).isoformat()
        engine.install_research(revised)
        self.assertEqual(engine.research["candidate"], old)
        engine.install_research({**revised, "candidate": None})
        engine.install_research(revised)
        self.assertEqual(engine.research["candidate"], old)


class ServiceTests(unittest.TestCase):
    def test_451_preflight_stops_before_other_routes(self):
        response = Mock(status_code=451)
        with patch("service_v52.requests.get", return_value=response) as get, self.assertRaises(AccessBlocked):
            preflight()
        self.assertEqual(get.call_count, 1)

    def test_official_futures_routed_streams(self):
        routes = streams(["ETHUSDT"])
        self.assertIn("/public/stream?streams=ethusdt@bookTicker", routes[1][1])
        self.assertIn("/market/stream?streams=", routes[2][1])
        self.assertIn("ethusdt@markPrice@1s", routes[2][1])

    def test_block_and_invalidations_survive_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"state.json"
            service = Service(path)
            service.supervisor.invalidated["crossed"] = "2026-10-05T00:00:00Z"
            service.block("HTTP 451")
            other = Service(path)
            self.assertEqual(other.supervisor.blocked, "HTTP 451")
            self.assertIn("crossed", other.supervisor.invalidated)

    def test_restart_invalidates_candidate_with_a_monitoring_gap(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"state.json"
            service = Service(path)
            service.state["last_candidate_key"] = "last-watched"
            service.save()
            self.assertIn("last-watched", Service(path).supervisor.invalidated)

    def test_public_http_snapshot_has_no_account_data(self):
        with tempfile.TemporaryDirectory() as temp:
            service = Service(Path(temp)/"state.json")
            engine, now = prepared()
            service.report = engine.tick(now)
            server = http_server(service, port=0)
            try:
                response = requests.get(f"http://127.0.0.1:{server.server_port}/snapshot", timeout=2)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertTrue(response.json()["entry_valid"])
                self.assertNotIn("capital_cop", response.text)
                self.assertNotIn("profile", response.text)
                response = requests.post(f"http://127.0.0.1:{server.server_port}/snapshot", timeout=2)
                self.assertEqual(response.status_code, 501)
            finally:
                server.shutdown()
                server.server_close()


class AsyncServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_loop_evaluates_at_one_second_intervals(self):
        with tempfile.TemporaryDirectory() as temp:
            service = Service(Path(temp)/"state.json")
            marks, stop = [], asyncio.Event()
            original = service.supervisor.tick
            def observed():
                marks.append(time.monotonic())
                if len(marks) == 3:
                    stop.set()
                return original()
            with patch.object(service.supervisor, "tick", side_effect=observed):
                await asyncio.wait_for(service.evaluate(stop), timeout=6)
            self.assertEqual(len(marks), 3)
            for a, b in zip(marks, marks[1:]):
                self.assertGreaterEqual(b-a, .95)
                self.assertLess(b-a, 1.5)

    async def test_failed_telegram_is_not_acknowledged(self):
        with tempfile.TemporaryDirectory() as temp:
            service = Service(Path(temp)/"state.json")
            signal = feed_fixture()["candidate"]
            with patch("monitor_v51.telegram", side_effect=RuntimeError("delivery failed")):
                await service.alert(signal)
            self.assertNotIn(signal["key"], service.state["sent"])
            with patch("monitor_v51.telegram"):
                await service.alert(signal)
            self.assertIn(signal["key"], service.state["sent"])

    async def test_missing_eligibility_never_opens_network(self):
        with tempfile.TemporaryDirectory() as temp:
            service = Service(Path(temp)/"state.json")
            with patch.dict(os.environ, {"TRADER_ACCESS_CONFIRMED": "no"}), \
                 patch("service_v52.preflight") as check, patch("service_v52.connect") as connect:
                task = asyncio.create_task(service.run())
                await asyncio.sleep(.05)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            check.assert_not_called()
            connect.assert_not_called()
            self.assertEqual(service.status, "PENDIENTE")


if __name__ == "__main__":
    unittest.main()
