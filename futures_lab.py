from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
import pandas as pd

from futures_market import get_futures_klines, get_funding_history


TIMEFRAME_HOURS = {"1h": 1, "2h": 2, "4h": 4}


@dataclass(frozen=True)
class LabCosts:
    spot_fee_each_side: float = 0.0010
    futures_fee_each_side: float = 0.0005
    slippage_each_side: float = 0.0002


def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    avg_loss = loss.ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - 100/(1+rs)).fillna(50.0)


def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev).abs(),
        (df["low"] - prev).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False, min_periods=n).mean()


def enrich_lab(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["ema20"] = _ema(out["close"], 20)
    out["ema50"] = _ema(out["close"], 50)
    out["ema200"] = _ema(out["close"], 200)
    out["rsi"] = _rsi(out["close"], 14)
    out["atr"] = _atr(out, 14)
    out["vol_ma"] = out["volume"].rolling(20).mean()
    out["vol_ratio"] = out["volume"] / out["vol_ma"]
    out["swing_low_10"] = out["low"].rolling(10).min()
    out["swing_high_10"] = out["high"].rolling(10).max()
    out["dist_ema20_atr"] = (out["close"] - out["ema20"]).abs() / out["atr"]

    out["long_signal"] = (
        (out["close"] > out["ema200"]) &
        (out["ema20"] > out["ema50"]) &
        (out["ema50"] > out["ema200"]) &
        out["rsi"].between(44, 62) &
        (out["vol_ratio"] >= 0.80) &
        (out["dist_ema20_atr"] <= 0.90) &
        (out["low"] <= out["ema20"] + 0.15*out["atr"]) &
        (out["close"] >= out["ema20"])
    )

    out["short_signal"] = (
        (out["close"] < out["ema200"]) &
        (out["ema20"] < out["ema50"]) &
        (out["ema50"] < out["ema200"]) &
        out["rsi"].between(38, 56) &
        (out["vol_ratio"] >= 0.80) &
        (out["dist_ema20_atr"] <= 0.90) &
        (out["high"] >= out["ema20"] - 0.15*out["atr"]) &
        (out["close"] <= out["ema20"])
    )
    return out


def _funding_cumulative(funding: pd.DataFrame):
    if funding is None or funding.empty:
        return np.array([], dtype="datetime64[ns]"), np.array([0.0])
    times = funding["funding_time"].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy(dtype="datetime64[ns]")
    rates = funding["funding_rate"].to_numpy(float)
    cum = np.r_[0.0, np.cumsum(rates)]
    return times, cum


def _funding_sum(times, cum, start_ts, end_ts) -> float:
    if len(times) == 0:
        return 0.0
    s = np.datetime64(pd.Timestamp(start_ts).tz_convert("UTC").tz_localize(None))
    e = np.datetime64(pd.Timestamp(end_ts).tz_convert("UTC").tz_localize(None))
    left = np.searchsorted(times, s, side="right")
    right = np.searchsorted(times, e, side="right")
    return float(cum[right] - cum[left])


def simulate_config(
    df: pd.DataFrame,
    funding: pd.DataFrame,
    direction: str,
    instrument: str,
    stop_atr: float,
    rr: float,
    timeframe: str,
    costs: LabCosts,
) -> pd.DataFrame:
    direction = direction.upper()
    instrument = instrument.upper()
    signal_col = "long_signal" if direction == "LONG" else "short_signal"
    max_hold = max(4, int(round(72 / TIMEFRAME_HOURS[timeframe])))

    fund_times, fund_cum = _funding_cumulative(funding)
    rows = []
    i = 220

    while i < len(df) - 2:
        row = df.iloc[i]
        if not bool(row.get(signal_col, False)):
            i += 1
            continue

        entry_idx = i + 1
        entry = float(df.iloc[entry_idx]["open"])
        atr = float(row["atr"])

        if direction == "LONG":
            stop_atr_level = entry - stop_atr * atr
            stop_structure = float(row["swing_low_10"]) - 0.10 * atr
            stop = min(stop_atr_level, stop_structure)
            if not (0 < stop < entry):
                i += 1
                continue
            risk_abs = entry - stop
            tp = entry + rr * risk_abs
        else:
            stop_atr_level = entry + stop_atr * atr
            stop_structure = float(row["swing_high_10"]) + 0.10 * atr
            stop = max(stop_atr_level, stop_structure)
            if not (stop > entry > 0):
                i += 1
                continue
            risk_abs = stop - entry
            tp = entry - rr * risk_abs
            if tp <= 0:
                i += 1
                continue

        exit_idx = None
        exit_price = None
        gross_r = None
        outcome = None

        end_idx = min(entry_idx + max_hold, len(df) - 1)
        for j in range(entry_idx, end_idx + 1):
            high = float(df.iloc[j]["high"])
            low = float(df.iloc[j]["low"])

            if direction == "LONG":
                hit_stop = low <= stop
                hit_tp = high >= tp
            else:
                hit_stop = high >= stop
                hit_tp = low <= tp

            if hit_stop and hit_tp:
                exit_idx, exit_price, gross_r, outcome = j, stop, -1.0, "LOSS"
                break
            if hit_stop:
                exit_idx, exit_price, gross_r, outcome = j, stop, -1.0, "LOSS"
                break
            if hit_tp:
                exit_idx, exit_price, gross_r, outcome = j, tp, float(rr), "WIN"
                break

        if exit_idx is None:
            exit_idx = end_idx
            exit_price = float(df.iloc[exit_idx]["close"])
            if direction == "LONG":
                gross_r = (exit_price - entry) / risk_abs
            else:
                gross_r = (entry - exit_price) / risk_abs
            outcome = "TIME"

        risk_pct = risk_abs / entry
        if instrument == "SPOT":
            round_trip_pct = 2*(costs.spot_fee_each_side + costs.slippage_each_side)
            funding_pct = 0.0
        else:
            round_trip_pct = 2*(costs.futures_fee_each_side + costs.slippage_each_side)
            raw_funding = _funding_sum(
                fund_times, fund_cum,
                df.iloc[entry_idx]["open_time"],
                df.iloc[exit_idx]["close_time"],
            )
            funding_pct = raw_funding if direction == "LONG" else -raw_funding

        fee_r = round_trip_pct / risk_pct
        funding_r = funding_pct / risk_pct
        net_r = float(gross_r) - fee_r - funding_r

        rows.append({
            "signal_time": row["close_time"],
            "entry_time": df.iloc[entry_idx]["open_time"],
            "exit_time": df.iloc[exit_idx]["close_time"],
            "direction": direction,
            "instrument": instrument,
            "timeframe": timeframe,
            "stop_atr": float(stop_atr),
            "rr": float(rr),
            "entry": entry,
            "stop": stop,
            "tp": tp,
            "risk_pct": risk_pct,
            "gross_r": float(gross_r),
            "fee_r": float(fee_r),
            "funding_r": float(funding_r),
            "net_r": float(net_r),
            "outcome": outcome,
            "holding_hours": float((df.iloc[exit_idx]["close_time"] - df.iloc[entry_idx]["open_time"]).total_seconds()/3600),
        })

        # Operaciones no solapadas para evitar reutilizar capital simultáneamente.
        i = exit_idx + 1

    return pd.DataFrame(rows)


def _profit_factor(r: np.ndarray) -> float:
    pos = r[r > 0].sum()
    neg = abs(r[r < 0].sum())
    if neg == 0:
        return float("inf") if pos > 0 else 0.0
    return float(pos / neg)


def _max_drawdown_r(r: np.ndarray) -> float:
    if len(r) == 0:
        return 0.0
    eq = np.cumsum(r)
    peak = np.maximum.accumulate(np.r_[0.0, eq])[1:]
    return float(np.max(peak - eq))


def _bootstrap_stats(r: np.ndarray, simulations: int, seed: int = 42) -> dict:
    if len(r) == 0:
        return {
            "ci_low": 0.0, "ci_high": 0.0, "prob_positive": 0.0,
            "mc_median_100": 0.0, "mc_p05_100": 0.0,
            "mc_prob_negative_100": 1.0, "mc_dd95_100": 0.0,
        }

    rng = np.random.default_rng(seed)
    sims = max(500, int(simulations))

    means = np.empty(sims)
    chunk = 250
    for start in range(0, sims, chunk):
        n = min(chunk, sims-start)
        sample = rng.choice(r, size=(n, len(r)), replace=True)
        means[start:start+n] = sample.mean(axis=1)

    npaths = min(sims, 5000)
    paths = rng.choice(r, size=(npaths, 100), replace=True)
    curves = np.cumsum(paths, axis=1)
    peaks = np.maximum.accumulate(np.c_[np.zeros(npaths), curves], axis=1)[:,1:]
    dds = np.max(peaks-curves, axis=1)
    finals = curves[:,-1]

    return {
        "ci_low": float(np.quantile(means, 0.025)),
        "ci_high": float(np.quantile(means, 0.975)),
        "prob_positive": float(np.mean(means > 0)),
        "mc_median_100": float(np.median(finals)),
        "mc_p05_100": float(np.quantile(finals, 0.05)),
        "mc_prob_negative_100": float(np.mean(finals < 0)),
        "mc_dd95_100": float(np.quantile(dds, 0.95)),
    }


def summarize_config(
    trades: pd.DataFrame,
    data_start,
    data_end,
    simulations: int,
    risk_per_trade_pct: float = 0.005,
) -> dict:
    if trades.empty:
        return {}

    start = pd.Timestamp(data_start)
    end = pd.Timestamp(data_end)
    span = end - start
    cut60 = start + span*0.60
    cut80 = start + span*0.80

    train = trades[trades["entry_time"] < cut60]
    val = trades[(trades["entry_time"] >= cut60) & (trades["entry_time"] < cut80)]
    test = trades[trades["entry_time"] >= cut80]

    r_all = trades["net_r"].to_numpy(float)
    r_test = test["net_r"].to_numpy(float)
    bs = _bootstrap_stats(r_test, simulations)

    test_years = max((end-cut80).total_seconds()/(365.25*24*3600), 0.01)
    trades_per_year = len(test)/test_years
    exp_test = float(np.mean(r_test)) if len(r_test) else 0.0
    pf_test = _profit_factor(r_test)
    win_test = float(np.mean(r_test > 0)) if len(r_test) else 0.0
    dd_test = _max_drawdown_r(r_test)

    evidence = "INSUFICIENTE"
    if (
        len(test) >= 200 and
        bs["ci_low"] > 0 and
        pf_test >= 1.20 and
        bs["prob_positive"] >= 0.95
    ):
        evidence = "FUERTE"
    elif (
        len(test) >= 75 and
        exp_test > 0 and
        pf_test >= 1.10 and
        bs["prob_positive"] >= 0.80
    ):
        evidence = "PROMETEDORA"

    risk_pct_med = float(trades["risk_pct"].median())
    notional_fraction = risk_per_trade_pct / risk_pct_med if risk_pct_med > 0 else float("nan")

    return {
        "trades_total": int(len(trades)),
        "trades_train": int(len(train)),
        "trades_val": int(len(val)),
        "trades_test": int(len(test)),
        "win_test": win_test,
        "expectancy_test_r": exp_test,
        "pf_test": pf_test,
        "max_dd_test_r": dd_test,
        "trades_per_year_test": float(trades_per_year),
        "r_per_year_test": float(exp_test*trades_per_year),
        "return_proxy_pct_year_at_risk": float(exp_test*trades_per_year*risk_per_trade_pct*100),
        "median_risk_pct": risk_pct_med,
        "notional_fraction_account": float(notional_fraction),
        "avg_holding_hours": float(test["holding_hours"].mean()) if len(test) else 0.0,
        "avg_fee_r": float(test["fee_r"].mean()) if len(test) else 0.0,
        "avg_funding_r": float(test["funding_r"].mean()) if len(test) else 0.0,
        "net_r_test": float(r_test.sum()) if len(r_test) else 0.0,
        "evidence": evidence,
        **bs,
    }


def bars_for_years(timeframe: str, years: int) -> int:
    hours = TIMEFRAME_HOURS[timeframe]
    return int(math.ceil(years*365.25*24/hours)) + 300


def run_lab(
    symbol: str,
    years: int = 5,
    timeframes: tuple[str,...] = ("1h","2h","4h"),
    stops: tuple[float,...] = (1.0,1.25,1.5),
    rrs: tuple[float,...] = (1.5,2.0,2.5,3.0),
    simulations: int = 2000,
    risk_per_trade_pct: float = 0.005,
) -> pd.DataFrame:
    results = []
    costs = LabCosts()

    for tf in timeframes:
        bars = bars_for_years(tf, years)
        raw = get_futures_klines(symbol, tf, bars)
        df = enrich_lab(raw).dropna().reset_index(drop=True)

        start_ms = int(df["open_time"].iloc[0].timestamp()*1000)
        end_ms = int(df["close_time"].iloc[-1].timestamp()*1000)
        funding = get_funding_history(symbol, start_ms, end_ms)

        for direction in ("LONG","SHORT"):
            for stop_atr in stops:
                for rr in rrs:
                    instruments = ("SPOT","FUTURES") if direction == "LONG" else ("FUTURES",)
                    for instrument in instruments:
                        trades = simulate_config(
                            df, funding, direction, instrument,
                            stop_atr, rr, tf, costs
                        )
                        stats = summarize_config(
                            trades,
                            df["open_time"].iloc[0],
                            df["close_time"].iloc[-1],
                            simulations,
                            risk_per_trade_pct,
                        )
                        if not stats:
                            continue

                        row = {
                            "symbol": symbol,
                            "instrument": instrument,
                            "direction": direction,
                            "timeframe": tf,
                            "stop_atr": stop_atr,
                            "rr": rr,
                            **stats,
                        }
                        results.append(row)

    out = pd.DataFrame(results)
    if out.empty:
        return out

    evidence_rank = {"FUERTE": 2, "PROMETEDORA": 1, "INSUFICIENTE": 0}
    out["_evidence_rank"] = out["evidence"].map(evidence_rank).fillna(0)
    out = out.sort_values(
        ["_evidence_rank","ci_low","r_per_year_test","trades_test"],
        ascending=[False,False,False,False],
    ).drop(columns=["_evidence_rank"]).reset_index(drop=True)
    return out


def leverage_table(row: pd.Series, risk_per_trade_pct: float = 0.005) -> pd.DataFrame:
    notional = float(row.get("notional_fraction_account", float("nan")))
    rows = []
    for lev in (1,2,3,5):
        margin_frac = notional/lev if lev > 0 else float("nan")
        rough_bankruptcy_move = 1/lev if lev > 1 else 1.0
        rows.append({
            "Leverage": f"{lev}x",
            "Riesgo objetivo por trade": risk_per_trade_pct*100,
            "Nocional / capital": notional*100,
            "Margen aprox. / capital": margin_frac*100,
            "Movimiento ~1/leverage": rough_bankruptcy_move*100,
        })
    return pd.DataFrame(rows)
