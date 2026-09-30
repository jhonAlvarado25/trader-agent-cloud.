from __future__ import annotations
import numpy as np
import pandas as pd
from config import StrategyConfig
from strategy import evaluate_row, build_levels

def backtest(df: pd.DataFrame, cfg: StrategyConfig) -> tuple[pd.DataFrame, dict]:
    trades = []
    start = max(cfg.ema_slow + 5, 220)

    for i in range(start, len(df) - 1):
        row = df.iloc[i]
        ev = evaluate_row(row, cfg)
        if not ev.get("signal", False):
            continue

        levels = build_levels(row, cfg)
        entry, stop, tp = levels["entry"], levels["stop"], levels["tp"]

        outcome = None
        exit_price = None
        exit_bar = None

        max_j = min(i + 1 + cfg.max_holding_bars, len(df))
        for j in range(i + 1, max_j):
            h = float(df.iloc[j]["high"])
            l = float(df.iloc[j]["low"])

            hit_stop = l <= stop
            hit_tp = h >= tp

            if hit_stop and hit_tp:
                # Convención conservadora cuando no conocemos la secuencia intrabar.
                outcome, exit_price, exit_bar = "LOSS", stop, j
                break
            elif hit_stop:
                outcome, exit_price, exit_bar = "LOSS", stop, j
                break
            elif hit_tp:
                outcome, exit_price, exit_bar = "WIN", tp, j
                break

        if outcome is None:
            # Time stop al cierre de la última vela del horizonte.
            exit_bar = max_j - 1
            exit_price = float(df.iloc[exit_bar]["close"])
            r_mult = (exit_price - entry) / (entry - stop)
            outcome = "TIME"
        else:
            r_mult = cfg.reward_risk if outcome == "WIN" else -1.0

        trades.append({
            "signal_time": row["close_time"],
            "entry": entry,
            "stop": stop,
            "tp": tp,
            "exit_price": exit_price,
            "outcome": outcome,
            "r": r_mult,
            "holding_bars": exit_bar - i,
            "rsi": float(row["rsi"]),
            "vol_ratio": float(row["vol_ratio"]),
            "atr_pct": float(row["atr_pct"]),
        })

    t = pd.DataFrame(trades)
    if t.empty:
        return t, _empty_stats(cfg)

    resolved = t[t["outcome"].isin(["WIN","LOSS"])].copy()
    wins = int((resolved["outcome"] == "WIN").sum())
    losses = int((resolved["outcome"] == "LOSS").sum())
    n = wins + losses

    raw_win_rate = wins / n if n else 0.0
    adj_win_rate = (wins + cfg.beta_alpha) / (n + cfg.beta_alpha + cfg.beta_beta) if n else 0.5
    breakeven = 1.0 / (1.0 + cfg.reward_risk)
    expectancy_r = adj_win_rate * cfg.reward_risk - (1.0 - adj_win_rate)

    gross_win_r = float(resolved.loc[resolved["r"] > 0, "r"].sum())
    gross_loss_r = abs(float(resolved.loc[resolved["r"] < 0, "r"].sum()))
    profit_factor = gross_win_r / gross_loss_r if gross_loss_r > 0 else float("inf")

    # Equity curve en R, incluyendo time stops.
    eq = t["r"].cumsum()
    peak = eq.cummax()
    dd = eq - peak
    max_dd_r = abs(float(dd.min())) if len(dd) else 0.0

    stats = {
        "trades_total": int(len(t)),
        "resolved": int(n),
        "wins": wins,
        "losses": losses,
        "time_stops": int((t["outcome"] == "TIME").sum()),
        "raw_win_rate": raw_win_rate,
        "adjusted_win_rate": adj_win_rate,
        "breakeven_win_rate": breakeven,
        "expectancy_r": expectancy_r,
        "profit_factor": profit_factor,
        "max_drawdown_r": max_dd_r,
        "avg_holding_bars": float(t["holding_bars"].mean()),
        "net_r": float(t["r"].sum()),
    }
    return t, stats

def _empty_stats(cfg):
    return {
        "trades_total": 0, "resolved": 0, "wins": 0, "losses": 0, "time_stops": 0,
        "raw_win_rate": 0.0, "adjusted_win_rate": 0.5,
        "breakeven_win_rate": 1/(1+cfg.reward_risk), "expectancy_r": 0.0,
        "profit_factor": 0.0, "max_drawdown_r": 0.0,
        "avg_holding_bars": 0.0, "net_r": 0.0
    }

def monte_carlo(stats: dict, cfg: StrategyConfig, simulations: int = 3000, trades_per_sim: int = 100, seed: int = 42) -> dict:
    rng = np.random.default_rng(seed)
    p = float(stats.get("adjusted_win_rate", 0.5))
    rr = cfg.reward_risk

    if stats.get("resolved", 0) == 0:
        return {"median_r": 0.0, "p05_r": 0.0, "p95_r": 0.0, "prob_negative": 0.5, "dd95_r": 0.0}

    finals = np.zeros(simulations)
    max_dds = np.zeros(simulations)

    for s in range(simulations):
        wins = rng.random(trades_per_sim) < p
        r = np.where(wins, rr, -1.0)
        eq = np.cumsum(r)
        peak = np.maximum.accumulate(np.r_[0.0, eq])[1:]
        dd = peak - eq
        finals[s] = eq[-1]
        max_dds[s] = dd.max() if len(dd) else 0.0

    return {
        "median_r": float(np.median(finals)),
        "p05_r": float(np.quantile(finals, 0.05)),
        "p95_r": float(np.quantile(finals, 0.95)),
        "prob_negative": float(np.mean(finals < 0)),
        "dd95_r": float(np.quantile(max_dds, 0.95)),
    }
