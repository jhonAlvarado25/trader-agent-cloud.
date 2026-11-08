"""Pruebas de backend PostgreSQL PAPER y worker sin claves ni red."""
from __future__ import annotations
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import pandas as pd

from paper_pg_backend import is_postgres_target, rewrite_postgres_sql
from paper_engine_pro import init_paper, paper_enter, paper_snapshot, paper_update_worker_status, paper_settle_candles
from paper_worker_pro import replay_one, run_worker


class PaperPhaseTwoTests(unittest.TestCase):
    def test_sql_translation_private_schema(self):
        sql = rewrite_postgres_sql("INSERT OR IGNORE INTO account VALUES(1,?,?,?,?)")
        self.assertIn("trader_pro_paper.account", sql)
        self.assertIn("ON CONFLICT DO NOTHING", sql)
        self.assertIn("%s", sql)
        self.assertNotIn("?", sql)
        self.assertIn("BIGSERIAL PRIMARY KEY",
            rewrite_postgres_sql("CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT)"))
        self.assertIn("GREATEST(high_water_usdt,%s)",
            rewrite_postgres_sql("UPDATE account SET high_water_usdt=MAX(high_water_usdt,?)"))
        self.assertTrue(is_postgres_target("postgresql://private"))
        self.assertFalse(is_postgres_target(".state/pro.db"))

    def test_worker_heartbeat_is_durable_in_sqlite(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"paper.db"
            init_paper(path)
            self.assertEqual(paper_snapshot(path)["worker"]["status"],"NOT_CONFIGURED")
            paper_update_worker_status(path,"OK",{"mode":"PAPER_ONLY","closed":0})
            worker=paper_snapshot(path)["worker"]
            self.assertEqual(worker["status"],"OK")
            self.assertIsNotNone(worker["last_success_utc"])
            self.assertEqual(worker["details"]["mode"],"PAPER_ONLY")

    def test_no_remote_worker_on_local_sqlite(self):
        with self.assertRaises(RuntimeError):
            run_worker(".state/local.db")

    def test_candle_opened_before_entry_never_awards_preentry_tp(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"paper.db"
            init_paper(path)
            trade=paper_enter(
                path,symbol="BTCUSDT",observed_price=85000,stop_price=83000,
                tp_price=89000,signal_key="entry-mid-candle",source="DEMO",
            )
            opened=datetime.fromisoformat(trade["opened_at_utc"])
            bar={"open_time":opened-timedelta(minutes=2),
                 "close_time":opened+timedelta(minutes=2),
                 "open":85000,"high":90000,"low":84000,"close":85000}
            result=paper_settle_candles(path,"BTCUSDT",[bar])
            self.assertEqual(result,[])
            self.assertEqual(paper_snapshot(path)["open_trades"][0]["id"],trade["id"])

    def test_worker_detects_old_or_gapped_market_data(self):
        now=datetime.now(timezone.utc)
        opened=now-timedelta(hours=2)
        trade={"id":"test","symbol":"BTCUSDT","opened_at_utc":opened.isoformat()}
        # No se deben inventar cierres si no se dispone de velas.
        with patch("paper_worker_pro.get_klines",return_value=pd.DataFrame()):
            result=replay_one("postgresql://unused",trade,now=now)
            self.assertEqual(result["status"],"DATA_GAP")
        df=pd.DataFrame([
            {"open_time":pd.Timestamp(now-timedelta(hours=2,minutes=30)),
             "close_time":pd.Timestamp(now-timedelta(hours=2,minutes=15)),
             "open":85000,"high":85100,"low":84900,"close":85000},
            {"open_time":pd.Timestamp(now-timedelta(hours=1,minutes=30)),
             "close_time":pd.Timestamp(now-timedelta(hours=1,minutes=15)),
             "open":85000,"high":85100,"low":84900,"close":85000},
        ])
        with patch("paper_worker_pro.get_klines",return_value=df):
            result=replay_one("postgresql://unused",trade,now=now)
            self.assertEqual(result["status"],"DATA_GAP")


if __name__=="__main__":
    unittest.main()
