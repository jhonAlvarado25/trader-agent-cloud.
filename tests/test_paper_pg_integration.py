"""PostgreSQL real con TLS temporal en CI; NO utiliza Supabase del usuario."""
import os
import unittest

from paper_pg_backend import is_postgres_target
from paper_engine_pro import (
    PaperRiskBlocked,
    init_paper,
    paper_enter,
    paper_close,
    paper_snapshot,
    paper_update_worker_status,
    reset_paper,
)


@unittest.skipUnless(os.getenv("TRADER_TEST_PG_URL"),"Requires local CI PostgreSQL")
class PostgresPaperIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.url=os.environ["TRADER_TEST_PG_URL"]
        assert is_postgres_target(cls.url)

    def setUp(self):
        init_paper(self.url, 30)
        reset_paper(self.url,30)

    def test_lifecycle_with_transactions_and_heartbeat(self):
        trade=paper_enter(
            self.url,symbol="BTCUSDT",observed_price=85000,
            stop_price=83000,tp_price=89000,signal_key="postgres-test-1",
            source="DEMO",evidence="CI",timeframe="15m",
        )
        snap=paper_snapshot(self.url)
        self.assertEqual(len(snap["open_trades"]),1)
        self.assertLessEqual(trade["estimated_stop_loss_usdt"],0.36+1e-9)

        with self.assertRaises(PaperRiskBlocked):
            paper_enter(
                self.url,symbol="BTCUSDT",observed_price=85000,
                stop_price=83000,tp_price=89000,signal_key="postgres-test-1",
                source="DEMO",
            )

        paper_update_worker_status(self.url,"OK",{"mode":"PAPER_ONLY","checked":True})
        self.assertEqual(paper_snapshot(self.url)["worker"]["status"],"OK")

        done=paper_close(self.url,trade["id"],observed_price=90000)
        self.assertEqual(done["status"],"CLOSED")
        self.assertEqual(paper_snapshot(self.url)["closed_count"],1)
        self.assertGreater(paper_snapshot(self.url)["realized_pnl_usdt"],0)

        with self.assertRaises(PaperRiskBlocked):
            paper_close(self.url,trade["id"],observed_price=90000)

    def tearDown(self):
        reset_paper(self.url,30)


if __name__=="__main__":
    unittest.main()
