from __future__ import annotations
import math
import pandas as pd
from config import StrategyConfig
from market import get_klines
from indicators import enrich_4h, enrich_1d, merge_regime
from setups import identify_setups, current_setup, build_levels, SETUP_PULLBACK, SETUP_BREAKOUT
from backtest import backtest_setup, summarize, walk_forward, monte_carlo_empirical

def prepare_symbol(symbol: str, cfg: StrategyConfig) -> dict:
    raw4 = get_klines(symbol, cfg.timeframe_entry, cfg.bars_4h)
    raw1 = get_klines(symbol, cfg.timeframe_regime, cfg.bars_1d)

    d4 = enrich_4h(raw4, cfg)
    d1 = enrich_1d(raw1, cfg)
    df = merge_regime(d4, d1)
    df = identify_setups(df, cfg)

    now = pd.Timestamp.now(tz="UTC")
    df = df[df["close_time"] <= now].copy().reset_index(drop=True)
    if len(df) < 250:
        raise RuntimeError("Historial insuficiente")

    current = df.iloc[-1]
    setup_now = current_setup(current)

    setup_results = {}
    for setup in (SETUP_PULLBACK, SETUP_BREAKOUT):
        trades = backtest_setup(df, setup, cfg)
        stats_all = summarize(trades, cfg)
        folds, oos_trades = walk_forward(trades, df, cfg)
        stats_oos = summarize(oos_trades, cfg)
        mc = monte_carlo_empirical(oos_trades if not oos_trades.empty else trades)
        setup_results[setup] = {
            "trades": trades,
            "stats_all": stats_all,
            "folds": folds,
            "oos_trades": oos_trades,
            "stats_oos": stats_oos,
            "mc": mc,
        }

    return {
        "symbol": symbol,
        "df": df,
        "current": current,
        "setup_now": setup_now,
        "setup_results": setup_results,
    }

def quality_gate(result: dict, cfg: StrategyConfig) -> dict:
    setup = result["setup_now"]
    if setup is None:
        return {
            "state":"NO OPERAR",
            "reason":"No hay setup Pullback ni Breakout+Retest en la última vela 4H cerrada.",
            "setup":None,
            "passed":False,
        }

    s_all = result["setup_results"][setup]["stats_all"]
    s_oos = result["setup_results"][setup]["stats_oos"]

    checks = {
        f"≥ {cfg.min_total_trades} operaciones totales": s_all["n"] >= cfg.min_total_trades,
        f"≥ {cfg.min_oos_trades} operaciones OOS": s_oos["n"] >= cfg.min_oos_trades,
        f"Expectativa OOS ≥ {cfg.min_expectancy_r:.2f}R": s_oos["expectancy_r"] >= cfg.min_expectancy_r,
        f"Profit Factor OOS ≥ {cfg.min_profit_factor:.2f}": s_oos["profit_factor"] >= cfg.min_profit_factor,
        f"Edge OOS > {cfg.min_edge_pp:.1f} pp": s_oos["edge_pp"] > cfg.min_edge_pp,
    }

    if all(checks.values()):
        state = "SETUP VÁLIDO"
        reason = "La señal actual y los filtros estadísticos fuera de muestra cumplen los mínimos V2."
        passed = True
    else:
        state = "VIGILAR"
        reason = "Existe setup técnico, pero no supera todos los filtros fuera de muestra."
        passed = False

    return {
        "state":state,
        "reason":reason,
        "setup":setup,
        "passed":passed,
        "checks":checks,
    }

def provisional_levels(result: dict, cfg: StrategyConfig) -> dict | None:
    setup = result["setup_now"]
    if setup is None:
        return None
    df = result["df"]
    row = df.iloc[-1]
    # Para panel: usar el cierre como entrada de referencia porque todavía no existe la próxima apertura.
    entry_ref = float(row["close"])
    return build_levels(row, entry_ref, setup, cfg)
