from __future__ import annotations
import math
import numpy as np
import pandas as pd
from config import StrategyConfig
from setups import SETUP_PULLBACK, SETUP_BREAKOUT, build_levels

def _setup_signal(row: pd.Series, setup: str) -> bool:
    if setup == SETUP_PULLBACK:
        return bool(row.get("pullback_signal", False))
    if setup == SETUP_BREAKOUT:
        return bool(row.get("breakout_retest_signal", False))
    return False

def backtest_setup(df: pd.DataFrame, setup: str, cfg: StrategyConfig) -> pd.DataFrame:
    trades = []
    i = max(cfg.ema_slow + 10, 220)
    round_trip_cost = 2 * (cfg.fee_each_side + cfg.slippage_each_side)

    while i < len(df) - 2:
        row = df.iloc[i]
        if not _setup_signal(row, setup):
            i += 1
            continue

        entry_bar = i + 1
        next_open = float(df.iloc[entry_bar]["open"])

        try:
            levels = build_levels(row, next_open, setup, cfg)
        except Exception:
            i += 1
            continue

        entry = levels["entry"]
        stop = levels["stop"]
        tp = levels["tp"]
        gross_risk_pct = (entry - stop) / entry

        if gross_risk_pct <= 0 or gross_risk_pct > 0.20:
            i += 1
            continue

        exit_bar = None
        exit_price = None
        exit_reason = None

        last_bar = min(entry_bar + cfg.max_holding_bars - 1, len(df)-1)

        for j in range(entry_bar, last_bar + 1):
            h = float(df.iloc[j]["high"])
            l = float(df.iloc[j]["low"])

            hit_stop = l <= stop
            hit_tp = h >= tp

            if hit_stop and hit_tp:
                # Conservador: si ambos ocurren en la misma vela, contar Stop primero.
                exit_bar, exit_price, exit_reason = j, stop, "STOP"
                break
            elif hit_stop:
                exit_bar, exit_price, exit_reason = j, stop, "STOP"
                break
            elif hit_tp:
                exit_bar, exit_price, exit_reason = j, tp, "TP"
                break

        if exit_bar is None:
            exit_bar = last_bar
            exit_price = float(df.iloc[exit_bar]["close"])
            exit_reason = "TIME"

        gross_return = exit_price / entry - 1.0
        net_return = gross_return - round_trip_cost
        net_r = net_return / gross_risk_pct
        profitable = net_r > 0

        trades.append({
            "setup": setup,
            "signal_index": int(i),
            "signal_time": row["close_time"],
            "entry_time": df.iloc[entry_bar]["open_time"],
            "exit_time": df.iloc[exit_bar]["close_time"],
            "entry": entry,
            "stop": stop,
            "tp": tp,
            "exit": exit_price,
            "exit_reason": exit_reason,
            "gross_risk_pct": gross_risk_pct,
            "net_return_pct": net_return,
            "net_r": net_r,
            "profitable": profitable,
            "holding_bars": exit_bar - entry_bar + 1,
            "rsi": float(row["rsi"]),
            "atr_pct": float(row["atr_pct"]),
            "vol_ratio": float(row["vol_ratio"]),
        })

        # Evitar trades solapados dentro de la misma estrategia.
        i = max(exit_bar + 1, i + 1)

    return pd.DataFrame(trades)

def summarize(trades: pd.DataFrame, cfg: StrategyConfig) -> dict:
    if trades is None or trades.empty:
        return {
            "n": 0, "wins": 0, "losses": 0,
            "raw_p": 0.0, "adjusted_p": 0.5,
            "avg_win_r": 0.0, "avg_loss_r": 1.0,
            "breakeven_p": 1.0,
            "edge_pp": -50.0,
            "expectancy_r": 0.0,
            "profit_factor": 0.0,
            "net_r": 0.0,
            "max_drawdown_r": 0.0,
            "avg_holding_hours": 0.0,
        }

    r = trades["net_r"].astype(float)
    positive = r[r > 0]
    negative = r[r <= 0]
    n = len(r)
    wins = len(positive)
    losses = len(negative)

    raw_p = wins / n
    adj_p = (wins + cfg.beta_alpha) / (n + cfg.beta_alpha + cfg.beta_beta)

    avg_win = float(positive.mean()) if wins else 0.0
    avg_loss = abs(float(negative.mean())) if losses else 1.0
    breakeven = avg_loss / (avg_win + avg_loss) if (avg_win + avg_loss) > 0 else 1.0
    edge_pp = (adj_p - breakeven) * 100
    expectancy = adj_p * avg_win - (1 - adj_p) * avg_loss

    gross_profit = float(positive.sum()) if wins else 0.0
    gross_loss = abs(float(negative.sum())) if losses else 0.0
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    eq = r.cumsum()
    peak = np.maximum.accumulate(np.r_[0.0, eq.to_numpy()])[1:]
    dd = peak - eq.to_numpy()
    max_dd = float(np.max(dd)) if len(dd) else 0.0

    return {
        "n": int(n),
        "wins": int(wins),
        "losses": int(losses),
        "raw_p": float(raw_p),
        "adjusted_p": float(adj_p),
        "avg_win_r": avg_win,
        "avg_loss_r": avg_loss,
        "breakeven_p": float(breakeven),
        "edge_pp": float(edge_pp),
        "expectancy_r": float(expectancy),
        "profit_factor": float(pf),
        "net_r": float(r.sum()),
        "max_drawdown_r": max_dd,
        "avg_holding_hours": float(trades["holding_bars"].mean() * 4),
    }

def walk_forward(trades: pd.DataFrame, df: pd.DataFrame, cfg: StrategyConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    if trades.empty:
        return pd.DataFrame(), pd.DataFrame()

    start_idx = int(len(df) * (1.0 - cfg.oos_fraction))
    end_idx = len(df) - 1
    span = max(end_idx - start_idx + 1, 1)
    fold_size = max(span // cfg.oos_folds, 1)

    fold_rows = []
    oos_parts = []

    for fold in range(cfg.oos_folds):
        a = start_idx + fold * fold_size
        b = end_idx if fold == cfg.oos_folds - 1 else min(start_idx + (fold+1)*fold_size - 1, end_idx)

        part = trades[(trades["signal_index"] >= a) & (trades["signal_index"] <= b)].copy()
        if not part.empty:
            oos_parts.append(part)

        s = summarize(part, cfg)
        fold_rows.append({
            "fold": fold + 1,
            "from": str(df.iloc[a]["close_time"]),
            "to": str(df.iloc[b]["close_time"]),
            "trades": s["n"],
            "expectancy_r": s["expectancy_r"],
            "profit_factor": s["profit_factor"],
            "edge_pp": s["edge_pp"],
            "net_r": s["net_r"],
        })

    oos = pd.concat(oos_parts, ignore_index=True) if oos_parts else pd.DataFrame()
    return pd.DataFrame(fold_rows), oos

def monte_carlo_empirical(trades: pd.DataFrame, simulations: int = 4000, horizon: int = 100, seed: int = 42) -> dict:
    if trades.empty:
        return {"median_r":0.0,"p05_r":0.0,"p95_r":0.0,"prob_negative":0.5,"dd95_r":0.0}

    outcomes = trades["net_r"].astype(float).to_numpy()
    rng = np.random.default_rng(seed)
    finals = np.zeros(simulations)
    dds = np.zeros(simulations)

    for i in range(simulations):
        sample = rng.choice(outcomes, size=horizon, replace=True)
        eq = np.cumsum(sample)
        peak = np.maximum.accumulate(np.r_[0.0, eq])[1:]
        dd = peak - eq
        finals[i] = eq[-1]
        dds[i] = dd.max() if len(dd) else 0.0

    return {
        "median_r": float(np.median(finals)),
        "p05_r": float(np.quantile(finals, 0.05)),
        "p95_r": float(np.quantile(finals, 0.95)),
        "prob_negative": float(np.mean(finals < 0)),
        "dd95_r": float(np.quantile(dds, 0.95)),
    }
