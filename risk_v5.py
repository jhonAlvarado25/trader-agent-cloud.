"""Conservative, exchange-filter-aware sizing. This module never places orders."""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import math

from profile_v5 import TradingProfile


def step_round(value, step, upward=False):
    v, s = Decimal(str(value)), Decimal(str(step))
    if not v.is_finite() or not s.is_finite() or s <= 0:
        raise ValueError("Precio/cantidad/precisión inválidos")
    mode = ROUND_CEILING if upward else ROUND_FLOOR
    return (v / s).to_integral_value(rounding=mode) * s


def number_text(value):
    return format(Decimal(str(value)), "f").rstrip("0").rstrip(".") if "." in format(Decimal(str(value)), "f") else format(Decimal(str(value)), "f")


def rules_from_exchange(symbol_info):
    if symbol_info.get("status") != "TRADING":
        raise ValueError("El activo no está habilitado para trading")
    fs = {x["filterType"]: x for x in symbol_info.get("filters", [])}
    price, lot = fs.get("PRICE_FILTER"), fs.get("LOT_SIZE")
    if not price or not lot:
        raise ValueError("No hay filtros PRICE_FILTER/LOT_SIZE")
    notional = fs.get("NOTIONAL", fs.get("MIN_NOTIONAL", {}))
    minimum = notional.get("minNotional", notional.get("notional"))
    if minimum is None:
        raise ValueError("No está disponible el mínimo nocional del mercado")
    return {
        "tick_size": price["tickSize"], "step_size": lot["stepSize"],
        "min_qty": float(lot["minQty"]), "max_qty": float(lot["maxQty"]),
        "min_price": float(price.get("minPrice", 0)), "max_price": float(price.get("maxPrice", 0)),
        "min_notional": float(minimum), "max_notional": float(notional.get("maxNotional", 0)),
    }


def size_order(profile: TradingProfile, entry, stop, tp, direction, instrument,
               rules, funding_reserve=0.0, spread_fraction=0.0, min_net_rr=1.5):
    direction, instrument = direction.upper(), instrument.upper()
    if direction not in {"LONG", "SHORT"} or instrument not in {"SPOT", "FUTURES"}:
        raise ValueError("Mercado/dirección inválidos")
    if instrument == "SPOT" and direction != "LONG":
        raise ValueError("Spot no permite abrir un SHORT sin otro producto")
    for v in (entry, stop, tp, funding_reserve, spread_fraction):
        if not math.isfinite(float(v)):
            raise ValueError("Nivel/costo no finito")
    if funding_reserve < 0 or spread_fraction < 0:
        raise ValueError("Reserva de costos negativa")
    long = direction == "LONG"
    entry = float(step_round(entry, rules["tick_size"], upward=long))
    stop = float(step_round(stop, rules["tick_size"], upward=not long))
    tp = float(step_round(tp, rules["tick_size"], upward=not long))
    if not (0 < stop < entry < tp if long else 0 < tp < entry < stop):
        raise ValueError("Stop y TP no protegen esta dirección después del redondeo")
    limit_sl = float(step_round(stop * .9988, rules["tick_size"])) if instrument == "SPOT" else None
    worst_stop = limit_sl if limit_sl is not None else stop
    fee = profile.spot_fee_each_side if instrument == "SPOT" else profile.futures_fee_each_side
    # Exit commissions/slippage are charged on exit notional, not entry notional.
    # Reserve the larger of stop/target notionals for both sizing scenarios.
    exit_ratio = max(entry, worst_stop, tp) / entry
    round_trip = (1 + exit_ratio) * (fee + profile.slippage_each_side) + funding_reserve + spread_fraction
    loss_rate = abs(entry - worst_stop) / entry + round_trip
    reward_rate = abs(tp - entry) / entry - round_trip
    lev = 1 if instrument == "SPOT" else profile.futures_leverage
    if instrument == "FUTURES" and abs(entry - stop) / entry >= .8 / lev:
        raise ValueError("Stop excesivamente lejano: requiere validar liquidación/margen")
    if profile.mode == "PILOTO_MANUAL" and not profile.fx_confirmed:
        raise ValueError("Confirma la tasa real COP/USDT antes de un piloto manual")
    risk_cap = profile.risk_budget_cop
    if risk_cap <= 0 or profile.available_cop <= 0:
        raise ValueError("Sin capital disponible o sin presupuesto de riesgo conjunto")
    position_cop = min(
        risk_cap / loss_rate,
        profile.capital_cop * profile.max_position_fraction,
        profile.available_cop / (1/lev + round_trip),
    )
    qty = float(step_round(position_cop / profile.cop_per_usdt / entry, rules["step_size"]))
    # Never round up to meet a minimum: that could violate the loss limit.
    qty = min(qty, float(step_round(rules["max_qty"], rules["step_size"])))
    if rules.get("max_notional", 0):
        qty = min(qty, float(step_round(rules["max_notional"] / entry, rules["step_size"])))
    exit_qty = float(step_round(qty * (1-fee), rules["step_size"])) if instrument == "SPOT" else qty
    prices = [entry, stop, tp] + ([limit_sl] if limit_sl is not None else [])
    if qty < rules["min_qty"] or exit_qty < rules["min_qty"]:
        raise ValueError("La cantidad permitida por tu riesgo es inferior al mínimo Binance")
    if qty * entry < rules["min_notional"] or exit_qty * min(prices) < rules["min_notional"]:
        raise ValueError("El nocional de entrada/protección es inferior al mínimo Binance; no aumentar riesgo")
    for price in prices:
        if price < rules.get("min_price", 0) or (rules.get("max_price", 0) and price > rules["max_price"]):
            raise ValueError("Precio fuera del filtro de Binance")
    actual_cop = qty * entry * profile.cop_per_usdt
    net_loss, net_gain = actual_cop * loss_rate, actual_cop * reward_rate
    if net_gain <= 0 or net_gain / net_loss < min_net_rr:
        raise ValueError("El R/R neto de costos ya no cumple el mínimo")
    return {
        "entry": entry, "stop": stop, "take_profit": tp, "limit_sl": limit_sl,
        "entry_text": number_text(entry), "stop_text": number_text(stop), "tp_text": number_text(tp),
        "limit_sl_text": None if limit_sl is None else number_text(limit_sl),
        "qty": qty, "qty_text": number_text(qty), "exit_qty_text": number_text(exit_qty),
        "position_cop": actual_cop, "position_usdt": qty * entry,
        "margin_cop": actual_cop / lev, "margin_usdt": qty * entry / lev,
        "risk_budget_cop": risk_cap, "net_loss_cop": net_loss, "net_gain_cop": net_gain,
        "costs_cop": actual_cop * round_trip, "cost_rate": round_trip,
        "actual_risk_pct_budget": net_loss / profile.capital_cop,
        "rr": abs(tp-entry) / abs(entry-stop), "rr_net": net_gain / net_loss,
        "leverage": lev, "margin_mode": "ISOLATED" if instrument == "FUTURES" else None,
        "instrument": instrument, "direction": direction,
        "operation_budget_cop": profile.capital_cop,
        "cop_per_usdt": profile.cop_per_usdt, "fx_confirmed": profile.fx_confirmed,
        "mode": profile.mode, "rules": rules,
    }
