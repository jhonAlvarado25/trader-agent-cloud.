from __future__ import annotations


def position_size_directional(
    operation_budget_cop: float,
    risk_pct: float,
    cop_per_usdt: float,
    entry: float,
    stop: float,
    tp: float,
    round_trip_cost_pct: float,
    direction: str = "LONG",
    leverage: int = 1,
) -> dict:
    direction = direction.upper()
    if direction not in {"LONG","SHORT"}:
        raise ValueError("Dirección debe ser LONG o SHORT")
    if operation_budget_cop <= 0 or entry <= 0 or cop_per_usdt <= 0:
        raise ValueError("Valores de capital/precio inválidos")

    if direction == "LONG":
        stop_pct = (entry - stop) / entry
        reward_pct = (tp - entry) / entry
    else:
        stop_pct = (stop - entry) / entry
        reward_pct = (entry - tp) / entry

    if stop_pct <= 0 or reward_pct <= 0:
        raise ValueError("Stop/TP inválidos para la dirección")

    risk_budget_cop = operation_budget_cop * risk_pct

    # Dimensionar con pérdida de precio + costos ida/vuelta para que la pérdida
    # neta estimada permanezca dentro del presupuesto de riesgo.
    net_loss_rate = stop_pct + max(round_trip_cost_pct, 0.0)
    unconstrained_position = risk_budget_cop / net_loss_rate

    # "Inversión por operación" = presupuesto máximo, no obligación de usarlo completo.
    position_cop = min(unconstrained_position, operation_budget_cop)
    position_usdt = position_cop / cop_per_usdt
    qty = position_usdt / entry

    gross_loss_cop = position_cop * stop_pct
    gross_gain_cop = position_cop * reward_pct
    costs_cop = position_cop * max(round_trip_cost_pct, 0.0)
    net_loss_cop = gross_loss_cop + costs_cop
    net_gain_cop = gross_gain_cop - costs_cop

    leverage = max(1, int(leverage))
    margin_cop = position_cop / leverage
    margin_usdt = position_usdt / leverage

    return {
        "risk_budget_cop": risk_budget_cop,
        "stop_pct": stop_pct,
        "reward_pct": reward_pct,
        "position_cop": position_cop,
        "position_usdt": position_usdt,
        "qty": qty,
        "gross_loss_cop": gross_loss_cop,
        "gross_gain_cop": gross_gain_cop,
        "costs_cop": costs_cop,
        "net_loss_cop": net_loss_cop,
        "net_gain_cop": net_gain_cop,
        "actual_risk_pct_budget": net_loss_cop / operation_budget_cop,
        "budget_used_pct": position_cop / operation_budget_cop,
        "leverage": leverage,
        "margin_cop": margin_cop,
        "margin_usdt": margin_usdt,
        "notional_cop": position_cop,
        "notional_usdt": position_usdt,
    }


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
    """Compatibilidad con V3/V4 Spot LONG."""
    result = position_size_directional(
        operation_budget_cop=capital_cop,
        risk_pct=risk_pct,
        cop_per_usdt=cop_per_usdt,
        entry=entry,
        stop=stop,
        tp=tp,
        round_trip_cost_pct=round_trip_cost_pct,
        direction="LONG",
        leverage=1,
    )

    cap_limit = capital_cop * max_position_fraction
    if result["position_cop"] > cap_limit:
        scale = cap_limit / result["position_cop"]
        for key in [
            "position_cop","position_usdt","qty","gross_loss_cop",
            "gross_gain_cop","costs_cop","net_loss_cop","net_gain_cop",
            "margin_cop","margin_usdt","notional_cop","notional_usdt",
        ]:
            result[key] *= scale
        result["actual_risk_pct_budget"] = result["net_loss_cop"] / capital_cop
        result["budget_used_pct"] = result["position_cop"] / capital_cop
        result["position_cap_hit"] = True
    else:
        result["position_cap_hit"] = False

    result["actual_risk_pct_capital"] = result["actual_risk_pct_budget"]
    return result
