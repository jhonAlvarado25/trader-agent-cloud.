"""Pruebas sin conexión a Binance: PAPER no puede enviar órdenes reales."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from paper_engine_pro import (
    PaperRiskBlocked, init_paper, paper_enter, paper_close, paper_snapshot,
    paper_settle_candles, paper_mark_current_price, reset_paper,
    RISK_FRACTION,
)


class PaperEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/"paper.db"
        init_paper(self.path,initial_usdt=30.0)

    def tearDown(self):
        self.temp.cleanup()

    def enter(self,key="validated-123",source="VALIDATED"):
        return paper_enter(
            self.path,symbol="BTCUSDT",observed_price=85000,
            stop_price=83300,tp_price=88400,signal_key=key,source=source,
            evidence="VALIDA",timeframe="2h",min_notional_usdt=5,
        )

    def test_risk_never_exceeds_1_2pct_and_no_margin(self):
        trade=self.enter()
        self.assertEqual(trade["status"],"OPEN")
        self.assertLessEqual(trade["estimated_stop_loss_usdt"],30*RISK_FRACTION+1e-9)
        self.assertLessEqual(trade["committed_usdt"],30+1e-9)
        self.assertEqual(trade["source"],"VALIDATED")
        self.assertLessEqual(paper_snapshot(self.path)["cash_usdt"],30)

    def test_idempotency_and_only_one_position(self):
        self.enter()
        with self.assertRaises(PaperRiskBlocked):
            self.enter()
        with self.assertRaises(PaperRiskBlocked):
            self.enter(key="another-entry")

    def test_target_and_profit_include_fees(self):
        tr=self.enter()
        done=paper_mark_current_price(self.path,"BTCUSDT",88500)
        self.assertEqual(len(done),1)
        self.assertEqual(done[0]["exit_reason"],"TP")
        self.assertGreater(done[0]["net_pnl_usdt"],0)
        snap=paper_snapshot(self.path)
        self.assertEqual(snap["wins"],1)
        self.assertEqual(snap["closed_count"],1)
        # Cierra una operación, pero no abre otra en el mismo día UTC.
        self.assertFalse(snap["gate"]["allowed"])

    def test_gap_can_exceed_risk_and_is_recorded(self):
        tr=self.enter()
        done=paper_mark_current_price(self.path,"BTCUSDT",78000)
        self.assertEqual(done[0]["exit_reason"],"STOP")
        self.assertLess(done[0]["net_pnl_usdt"],-tr["risk_budget_usdt"])
        self.assertEqual(paper_snapshot(self.path)["losses"],1)

    def test_candle_same_bar_stop_first(self):
        tr=self.enter()
        opened=datetime.fromisoformat(tr["opened_at_utc"])
        now=datetime.now(timezone.utc)
        candles=[{
            "open_time":opened.isoformat(),
            "close_time":now.isoformat(),
            "open":84900.0,"high":90000.0,"low":82000.0,"close":86000.0,
        }]
        closed=paper_settle_candles(self.path,"BTCUSDT",candles)
        self.assertEqual(len(closed),1)
        self.assertEqual(closed[0]["exit_reason"],"STOP_AND_TP_SAME_CANDLE")
        self.assertLess(closed[0]["net_pnl_usdt"],0)

    def test_invalid_entry_and_min_notional_fail_closed(self):
        with self.assertRaises(PaperRiskBlocked):
            paper_enter(
                self.path,symbol="BTCUSDT",observed_price=85000,
                stop_price=86000,tp_price=89000,signal_key="bad",
                source="DEMO",
            )
        with self.assertRaises(PaperRiskBlocked):
            paper_enter(
                self.path,symbol="BTCUSDT",observed_price=85000,
                stop_price=83300,tp_price=88400,signal_key="below-notional",
                source="DEMO",min_notional_usdt=100,
            )

    def test_reset_changes_only_virtual_capital(self):
        self.enter()
        reset_paper(self.path,75)
        snap=paper_snapshot(self.path)
        self.assertEqual(snap["initial_usdt"],75)
        self.assertEqual(snap["cash_usdt"],75)
        self.assertEqual(snap["trades"],[])

    def test_explicit_manual_close_no_duplicate(self):
        tr=self.enter()
        done=paper_close(self.path,tr["id"],observed_price=87500)
        self.assertEqual(done["status"],"CLOSED")
        with self.assertRaises(PaperRiskBlocked):
            paper_close(self.path,tr["id"],observed_price=87500)


if __name__=="__main__":
    unittest.main()
