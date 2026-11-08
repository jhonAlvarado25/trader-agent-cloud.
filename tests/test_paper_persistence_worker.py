import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pandas as pd

from paper_pg_backend import is_postgres_target, rewrite_postgres_sql
from paper_engine_pro import (
    init_paper, paper_enter, paper_snapshot, paper_update_worker_status,
)
from paper_worker_pro import replay_one, run_worker


class PostgresAdapterTests(unittest.TestCase):
    def test_target_detection(self):
        self.assertTrue(is_postgres_target("postgresql://user:secret@db:5432/db"))
        self.assertTrue(is_postgres_target("postgres://user:secret@db/db"))
        self.assertFalse(is_postgres_target(".state/paper.db"))

    def test_schema_and_postgres_types(self):
        q=rewrite_postgres_sql(
            "CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT)"
        )
        self.assertIn("trader_pro_paper.events",q)
        self.assertIn("BIGSERIAL PRIMARY KEY",q)
        self.assertNotIn("AUTOINCREMENT",q)

    def test_insert_ignore_and_placeholders(self):
        q=rewrite_postgres_sql("INSERT OR IGNORE INTO account VALUES(1,?,?,?,?)")
        self.assertEqual(
            q,
            "INSERT INTO trader_pro_paper.account VALUES(1,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        )

    def test_max_highwater_and_schema(self):
        q=rewrite_postgres_sql(
            "UPDATE account SET high_water_usdt=MAX(high_water_usdt,?) WHERE id=1"
        )
        self.assertIn("GREATEST(high_water_usdt,%s)",q)
        self.assertIn("UPDATE trader_pro_paper.account",q)

    def test_trade_select_and_status_sql(self):
        q=rewrite_postgres_sql(
            "SELECT * FROM trades WHERE status='OPEN' AND symbol=?"
        )
        self.assertEqual(
            q,
            "SELECT * FROM trader_pro_paper.trades WHERE status='OPEN' AND symbol=%s",
        )


class WorkerSimulationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Path(self.tmp.name)/"worker.db"
        init_paper(self.db,30.0)

    def tearDown(self):
        self.tmp.cleanup()

    def test_worker_heartbeat_persists(self):
        paper_update_worker_status(self.db,"OK",{"closed":0,"mode":"PAPER_ONLY"})
        snap=paper_snapshot(self.db)
        self.assertEqual(snap["worker"]["status"],"OK")
        stamp=snap["worker"]["last_success_utc"]
        self.assertTrue(stamp)
        paper_update_worker_status(self.db,"DEGRADED",{"closed":0})
        status=paper_snapshot(self.db)["worker"]
        self.assertEqual(status["last_success_utc"],stamp)
        self.assertEqual(status["status"],"DEGRADED")

    def test_worker_fails_closed_for_local_shared_mode(self):
        with self.assertRaises(RuntimeError):
            run_worker(str(self.db))

    def test_replay_closes_only_on_completed_candle(self):
        tr=paper_enter(
            self.db,symbol="BTCUSDT",observed_price=85000,
            stop_price=83000,tp_price=89000,signal_key="worker-fixture",
            source="DEMO",min_notional_usdt=5,
        )
        opened=datetime.now(timezone.utc)-timedelta(hours=1)
        import sqlite3
        with sqlite3.connect(str(self.db)) as con:
            con.execute("UPDATE trades SET opened_at_utc=? WHERE id=?",
                        (opened.isoformat(),tr["id"]))
        old=opened-timedelta(minutes=15)
        bar=pd.DataFrame([
            {
                "open_time":pd.Timestamp(old),
                "close_time":pd.Timestamp(old+timedelta(minutes=15)-timedelta(milliseconds=1)),
                "open":85000.0,"high":86000.0,"low":84000.0,"close":85000.0,
            },
            {
                "open_time":pd.Timestamp(opened+timedelta(minutes=1)),
                "close_time":pd.Timestamp(opened+timedelta(minutes=16)),
                "open":85000.0,"high":90000.0,"low":83500.0,"close":89500.0,
            },
        ])
        with patch("paper_worker_pro.get_klines",return_value=bar), patch(
            "paper_worker_pro.get_live_price",return_value=85000.0
        ):
            result=replay_one(str(self.db),{**tr,"opened_at_utc":opened.isoformat()})
        self.assertEqual(result["status"],"SETTLED_CANDLES")
        closed=paper_snapshot(self.db)["trades"][0]
        self.assertEqual(closed["exit_reason"],"TP")
        self.assertEqual(closed["status"],"CLOSED")


if __name__=="__main__":
    unittest.main()
