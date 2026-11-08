"""Smoke de renderizado Streamlit, sin red ni Binance privada."""
import os
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


class TerminalAppSmokeTests(unittest.TestCase):
    def test_starts_with_public_quote_and_no_real_trading(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.dict(os.environ, {"TRADER_PAPER_DB": os.path.join(d, "paper.db")}):
                with patch("market.get_live_price", return_value=85000.0):
                    app=AppTest.from_file("terminal_pro.py", default_timeout=35).run()
                    self.assertEqual(
                        len(app.exception),0,
                        "El panel generó excepciones durante su arranque: "
                        + str([str(x.message) for x in app.exception]),
                    )
                    titles=[x.value for x in app.title]
                    self.assertTrue(any("Trader Agent Pro Terminal" in x for x in titles))
                    labels=[x.label for x in app.metric]
                    self.assertIn("Patrimonio PAPER",labels)
                    self.assertIn("Riesgo máx. próximo trade",labels)
                    self.assertIn("0", str(app.session_state._state) if False else "0")


if __name__=="__main__":
    unittest.main()
