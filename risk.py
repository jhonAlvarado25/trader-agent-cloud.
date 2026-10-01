from __future__ import annotations

def position_size(
    capital_cop: float,
    risk_pct: float,
    max_position_fraction: float,
    cop_per_usdt: float,
    entry: float,
    stop: float,
    tp: float,
    round_trip_cost_pct: float,
) -> dict:
    stop_pct = (entry - stop) / entry
    if stop_pct <= 0:
        raise ValueError("Stop inválido")

    risk_budget_cop = capital_cop * risk_pct
    unconstrained = risk_budget_cop / stop_pct
    cap_limit = capital_cop * max_position_fraction
    position_cop = min(unconstrained, cap_limit, capital_cop)

    position_usdt = position_cop / cop_per_usdt
    qty = position_usdt / entry

    gross_loss = position_cop * stop_pct
    gross_gain = position_cop * ((tp-entry)/entry)
    costs = position_cop * round_trip_cost_pct

    return {
        "risk_budget_cop": risk_budget_cop,
        "stop_pct": stop_pct,
        "position_cop": position_cop,
        "position_usdt": position_usdt,
        "qty": qty,
        "gross_loss_cop": gross_loss,
        "gross_gain_cop": gross_gain,
        "costs_cop": costs,
        "net_loss_cop": gross_loss + costs,
        "net_gain_cop": gross_gain - costs,
        "position_cap_hit": unconstrained > cap_limit,
        "actual_risk_pct_capital": (gross_loss + costs) / capital_cop,
    }
