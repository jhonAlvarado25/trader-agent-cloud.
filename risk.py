from __future__ import annotations

def position_size(
    capital_cop: float,
    risk_pct: float,
    cop_per_usdt: float,
    entry: float,
    stop: float,
    tp: float,
    fee_rate_round_trip: float = 0.002,
) -> dict:
    risk_cop = capital_cop * risk_pct
    risk_pct_trade = (entry - stop) / entry
    if risk_pct_trade <= 0:
        raise ValueError("Stop debe estar debajo de la entrada.")

    position_cop = min(capital_cop, risk_cop / risk_pct_trade)
    position_usdt = position_cop / cop_per_usdt
    qty = position_usdt / entry

    gross_loss_cop = position_cop * risk_pct_trade
    gross_gain_cop = position_cop * ((tp - entry) / entry)
    est_fees_cop = position_cop * fee_rate_round_trip

    return {
        "risk_cop": risk_cop,
        "risk_pct_trade": risk_pct_trade,
        "position_cop": position_cop,
        "position_usdt": position_usdt,
        "qty_btc": qty,
        "gross_loss_cop": gross_loss_cop,
        "gross_gain_cop": gross_gain_cop,
        "est_fees_cop": est_fees_cop,
        "net_loss_cop": gross_loss_cop + est_fees_cop,
        "net_gain_cop": gross_gain_cop - est_fees_cop,
    }
