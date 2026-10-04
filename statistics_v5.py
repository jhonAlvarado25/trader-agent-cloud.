"""Purged chronological partitions and dependent-trade bootstrap for fixed policies."""
from __future__ import annotations

import numpy as np
import pandas as pd


def partitions(trades, start, end):
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    cut60, cut80 = start + (end-start)*.6, start + (end-start)*.8
    # A trade crossing a boundary is excluded from both sides, not assigned by entry alone.
    return {
        "train": trades[(trades.entry_time >= start) & (trades.exit_time < cut60)].copy(),
        "validation": trades[(trades.entry_time >= cut60) & (trades.exit_time < cut80)].copy(),
        "test": trades[(trades.entry_time >= cut80) & (trades.exit_time <= end)].copy(),
    }


def simple_stats(trades):
    r = trades.net_r.to_numpy(float) if not trades.empty else np.array([])
    if len(r) and not np.isfinite(r).all():
        raise ValueError("Resultados históricos no finitos")
    pos, neg = r[r > 0].sum(), abs(r[r < 0].sum())
    return {
        "n": len(r), "expectancy_r": float(r.mean()) if len(r) else 0.0,
        "win_rate": float((r > 0).mean()) if len(r) else 0.0,
        "profit_factor": float(pos/neg) if neg else (float("inf") if pos else 0.0),
    }


def block_bootstrap(r, simulations=2000, seed=51):
    r = np.asarray(r, dtype=float)
    if len(r) < 2 or not np.isfinite(r).all():
        return {"ci_low": -float("inf"), "ci_high": float("inf"), "prob_positive": 0.0,
                "mc_dd95_100": float("inf"), "mc_prob_negative_100": 1.0}
    rng = np.random.default_rng(seed)
    block = max(2, min(10, int(np.sqrt(len(r)))))
    sims = max(500, min(10000, int(simulations)))
    means, dds, negative = [], [], []
    for _ in range(sims):
        starts = rng.integers(0, len(r), size=int(np.ceil(len(r)/block)))
        indices = (starts[:, None] + np.arange(block)) % len(r)
        means.append(float(r[indices.ravel()[:len(r)]].mean()))
        path_starts = rng.integers(0, len(r), size=int(np.ceil(100/block)))
        path = r[((path_starts[:, None] + np.arange(block)) % len(r)).ravel()[:100]]
        eq = np.r_[0.0, np.cumsum(path)]
        dds.append(float((np.maximum.accumulate(eq)-eq).max()))
        negative.append(eq[-1] < 0)
    return {
        "ci_low": float(np.quantile(means, .025)), "ci_high": float(np.quantile(means, .975)),
        "prob_positive": float(np.mean(np.array(means) > 0)),
        "mc_dd95_100": float(np.quantile(dds, .95)), "mc_prob_negative_100": float(np.mean(negative)),
    }


def selection_score(trades, start, end):
    parts = partitions(trades, start, end)
    train, val = simple_stats(parts["train"]), simple_stats(parts["validation"])
    # TEST is deliberately neither scored nor inspected here.
    ok = train["n"] >= 50 and val["n"] >= 25 and val["expectancy_r"] > .05 and val["profit_factor"] >= 1.15
    score = (val["expectancy_r"], min(val["profit_factor"], 5), val["n"])
    return ok, score, {"train": train, "validation": val}


def final_evidence(trades, stressed_trades, start, end, risk_pct=.005, simulations=2000, max_dd_pct=.05):
    parts = partitions(trades, start, end)
    test = parts["test"]
    s = simple_stats(test)
    stress = simple_stats(partitions(stressed_trades, start, end)["test"])
    bs = block_bootstrap(test.net_r.to_numpy(float) if not test.empty else [], simulations)
    # Four expanding-origin checks of the SAME fixed rule; no tuning on those folds.
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    folds = []
    for i in range(4):
        left, right = start+(end-start)*(.6+i*.1), start+(end-start)*(.7+i*.1)
        train = trades[trades.exit_time < left]
        held = trades[(trades.entry_time >= left) & (trades.exit_time < right)]
        fs = simple_stats(held)
        folds.append({"fold": i+1, "train_trades": len(train), "test_start": left.isoformat(),
                      "test_end": right.isoformat(), **fs})
    positive_folds = sum(x["n"] >= 5 and x["expectancy_r"] > 0 for x in folds)
    checks = {
        "TEST >= 75 operaciones": s["n"] >= 75,
        "Expectativa TEST > 0,05R": s["expectancy_r"] > .05,
        "PF TEST >= 1,20": s["profit_factor"] >= 1.20,
        "IC95% por bloques completamente positivo": bs["ci_low"] > 0,
        "Bootstrap positivo >= 95% (no probabilidad de ganar)": bs["prob_positive"] >= .95,
        "TEST rentable con slippage/costos estresados": stress["n"] >= 50 and stress["expectancy_r"] > 0,
        "3/4 ventanas OOS positivas": positive_folds >= 3,
        "Drawdown MC P95 compatible con límite": bs["mc_dd95_100"] * risk_pct <= max_dd_pct,
    }
    evidence = "FUERTE" if all(checks.values()) else "INSUFICIENTE"
    if evidence == "INSUFICIENTE" and s["n"] >= 25 and s["expectancy_r"] > 0 and s["profit_factor"] >= 1.08:
        evidence = "EN OBSERVACIÓN"
    return {
        "evidence": evidence, "trades_test": s["n"], "win_test": s["win_rate"],
        "expectancy_test_r": s["expectancy_r"], "pf_test": s["profit_factor"],
        "stress_expectancy_r": stress["expectancy_r"], "positive_folds": positive_folds,
        "checks": checks, "walk_forward": folds, **bs,
    }
