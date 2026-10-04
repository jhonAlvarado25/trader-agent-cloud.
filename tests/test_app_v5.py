from pathlib import Path
import unittest
from unittest.mock import patch
import pandas as pd
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "app.py"
RULES = {"tick_size": ".01", "step_size": ".001", "min_qty": .001, "max_qty": 100000,
         "min_notional": 5, "max_notional": 0, "min_price": .01, "max_price": 100000}


class AppTests(unittest.TestCase):
    def app(self):
        at = AppTest.from_file(str(APP), default_timeout=10).run()
        self.assertEqual(len(at.exception), 0)
        return at

    def test_capital_and_risk_are_editable_and_reactive(self):
        at = self.app()
        self.assertFalse(at.number_input(key="capital_cop").disabled)
        self.assertIn("5,000", at.metric[0].value)
        at.number_input(key="capital_cop").set_value(2_000_000).run()
        self.assertEqual(len(at.exception), 0)
        self.assertIn("10,000", at.metric[0].value)
        self.assertEqual(at.number_input(key="available_cop").value, 2_000_000)
        at.number_input(key="capital_cop").set_value(500_000).run()
        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.number_input(key="available_cop").value, 500_000)
        self.assertIn("2,500", at.metric[0].value)

    def test_recompute_binance_quantity_after_capital_change(self):
        at = self.app()
        at.selectbox(key="calc_instrument").set_value("FUTURES")
        at.number_input(key="calc_entry").set_value(100)
        at.number_input(key="calc_stop").set_value(98)
        at.number_input(key="calc_tp").set_value(104)
        at.number_input(key="cop_per_usdt").set_value(4000)
        at.checkbox(key="fx_confirmed").set_value(True)
        at.run()
        snapshot = {"price": 100, "bid": 100, "ask": 100, "spread_fraction": 0,
                    "instrument": "FUTURES", "funding_rate": 0, "funding_interval_hours": 8,
                    "quoted_at": pd.Timestamp.now(tz="UTC").isoformat(), "source": "unit-native"}
        with patch("market_v5.quote", return_value=snapshot), patch("market_v5.exchange_rules", return_value=RULES):
            next(x for x in at.button if x.label == "Validar cotización y calcular").click().run()
        self.assertEqual(len(at.exception), 0)
        table = at.dataframe[0].value
        qty1 = float(table.loc[table["Campo Binance"] == "Cantidad del activo", "Valor"].iloc[0])
        at.number_input(key="capital_cop").set_value(2_000_000).run()
        self.assertEqual(len(at.exception), 0)
        table = at.dataframe[0].value
        qty2 = float(table.loc[table["Campo Binance"] == "Cantidad del activo", "Valor"].iloc[0])
        self.assertGreater(qty2, qty1)
        self.assertAlmostEqual(qty2, qty1*2, delta=.00100001)


if __name__ == "__main__":
    unittest.main()
