from __future__ import annotations
import math
import pandas as pd

from config import StrategyConfig
from market import get_klines, get_live_price
from futures_market import get_futures_klines, get_funding_history
from futures_lab import LabCosts, bars_for_years, enrich_lab, simulate_config, summarize_config
from risk import position_size_directional
from quant_math import validation_score

RANK={"INSUFICIENTE":0,"VALIDA":1,"PROMETEDORA":2,"FUERTE":3}
_HISTORY_CACHE={}


def _history(symbol,tf,bars):
    key=(symbol,tf,int(bars))
    if key not in _HISTORY_CACHE:
        _HISTORY_CACHE[key]=get_klines(symbol,tf,int(bars))
    return _HISTORY_CACHE[key].copy()


def _last(df):
    now=pd.Timestamp.now(tz="UTC")
    x=df[df["close_time"]<=now]
    if x.empty: raise RuntimeError("No hay velas cerradas")
    return x.iloc[-1]


def enrich_auto(df):
    o=enrich_lab(df).copy()
    ph=o["high"].rolling(20).max().shift(1)
    pl=o["low"].rolling(20).min().shift(1)

    ls=(o["rsi"].between(42,68).astype(int)+(o["vol_ratio"]>=.60).astype(int)+
        (o["dist_ema20_atr"]<=1.35).astype(int)+(o["low"]<=o["ema20"]+.40*o["atr"]).astype(int)+
        (o["close"]>=o["ema20"]-.10*o["atr"]).astype(int))
    ss=(o["rsi"].between(32,58).astype(int)+(o["vol_ratio"]>=.60).astype(int)+
        (o["dist_ema20_atr"]<=1.35).astype(int)+(o["high"]>=o["ema20"]-.40*o["atr"]).astype(int)+
        (o["close"]<=o["ema20"]+.10*o["atr"]).astype(int))

    o["bal_pb_long"]=(o["close"]>o["ema50"])&(o["ema20"]>o["ema50"])&(ls>=4)
    o["bal_pb_short"]=(o["close"]<o["ema50"])&(o["ema20"]<o["ema50"])&(ss>=4)
    o["bal_bo_long"]=(o["close"]>ph)&(o["close"]>o["ema50"])&(o["ema20"]>o["ema50"])&o["rsi"].between(50,78)&(o["vol_ratio"]>=.75)
    o["bal_bo_short"]=(o["close"]<pl)&(o["close"]<o["ema50"])&(o["ema20"]<o["ema50"])&o["rsi"].between(22,50)&(o["vol_ratio"]>=.75)
    o["bal_long_score"]=(o["close"]>o["ema50"]).astype(int)+(o["ema20"]>o["ema50"]).astype(int)+ls
    o["bal_short_score"]=(o["close"]<o["ema50"]).astype(int)+(o["ema20"]<o["ema50"]).astype(int)+ss
    return o


def _mask(df,c,require_fib:bool=False):
    o=enrich_auto(df)
    d=c["direction"]; s=c["setup"]
    if s=="STRICT_PULLBACK":
        m=o["long_signal"] if d=="LONG" else o["short_signal"]
    elif s=="BALANCED_PULLBACK":
        m=o["bal_pb_long"] if d=="LONG" else o["bal_pb_short"]
    else:
        m=o["bal_bo_long"] if d=="LONG" else o["bal_bo_short"]

    if require_fib:
        fib_col="fib_long_confluence" if d=="LONG" else "fib_short_confluence"
        m=m & o[fib_col].fillna(False).astype(bool)

    o["long_signal"]=False; o["short_signal"]=False
    o["long_signal" if d=="LONG" else "short_signal"]=m.astype(bool)
    return o

def detect_current_candidates(symbol:str,cfg:StrategyConfig)->list[dict]:
    out=[]
    for tf in cfg.auto_timeframes:
        f=enrich_auto(get_futures_klines(symbol,tf,350)).dropna().reset_index(drop=True)
        s=enrich_auto(get_klines(symbol,tf,350)).dropna().reset_index(drop=True)
        if f.empty or s.empty: continue
        fr,sr=_last(f),_last(s)

        def add(direction,setup,score,priority):
            fib_prefix="fib_long" if direction=="LONG" else "fib_short"
            out.append({"timeframe":tf,"direction":direction,"setup":setup,"score":int(score),
                        "priority":priority,"signal_time":fr["close_time"],"signal_close":float(fr["close"]),
                        "atr":float(fr["atr"]),"row":fr,
                        "fib_confluence":bool(fr.get(f"{fib_prefix}_confluence",False)),
                        "fib_nearest":float(fr.get(f"{fib_prefix}_nearest",float("nan"))),
                        "fib_distance_atr":float(fr.get(f"{fib_prefix}_distance_atr",float("nan"))),
                        "fib_extension_1272":float(fr.get(f"{fib_prefix}_ext_1272",float("nan"))),
                        "fib_extension_1618":float(fr.get(f"{fib_prefix}_ext_1618",float("nan"))),
                        "volatility_ratio":float(fr.get("volatility_ratio_100",float("nan")))})

        if bool(fr["long_signal"]) and bool(sr["long_signal"]): add("LONG","STRICT_PULLBACK",9,3)
        if bool(fr["bal_pb_long"]) and bool(sr["bal_pb_long"]):
            add("LONG","BALANCED_PULLBACK",min(fr["bal_long_score"],sr["bal_long_score"]),2)
        if bool(fr["bal_bo_long"]) and bool(sr["bal_bo_long"]): add("LONG","BALANCED_BREAKOUT",7,2)

        if bool(fr["short_signal"]): add("SHORT","STRICT_PULLBACK",9,3)
        if bool(fr["bal_pb_short"]): add("SHORT","BALANCED_PULLBACK",fr["bal_short_score"],2)
        if bool(fr["bal_bo_short"]): add("SHORT","BALANCED_BREAKOUT",7,2)

    tfr={"30m":4,"1h":3,"2h":2,"4h":1}
    out.sort(key=lambda x:(x["priority"],x["score"],tfr.get(x["timeframe"],0)),reverse=True)
    return out[:cfg.auto_balanced_max_candidates_per_symbol]


def _evidence(st,cfg):
    if st.get("evidence") in {"PROMETEDORA","FUERTE"}: return st["evidence"]
    ok=(st.get("trades_test",0)>=cfg.auto_balanced_min_test_trades and
        st.get("expectancy_test_r",0)>=cfg.auto_balanced_min_expectancy_r and
        st.get("pf_test",0)>=cfg.auto_balanced_min_profit_factor and
        st.get("prob_positive",0)>=cfg.auto_balanced_min_prob_positive)
    return "VALIDA" if ok else "INSUFICIENTE"


def _variant_result(symbol,c,cfg,base,funding,instrument,direction,variant,costs):
    require_fib=variant=="FIBONACCI"
    x=_mask(base,c,require_fib=require_fib).dropna().reset_index(drop=True)
    if x.empty:
        return None
    tr=simulate_config(
        x,funding,direction,instrument,
        cfg.auto_stop_atr,cfg.auto_reward_risk,c["timeframe"],costs
    )
    if tr.empty:
        return None
    st=summarize_config(
        tr,x["open_time"].iloc[0],x["close_time"].iloc[-1],
        cfg.auto_bootstrap_sims,cfg.default_risk_pct
    )
    if not st:
        return None
    st["variant"]=variant
    st["validation_score"]=validation_score(st)
    return {"instrument":instrument,"direction":direction,"timeframe":c["timeframe"],
            "setup":c["setup"],**st}


def _choose_variant(rows,c,cfg):
    if not rows:
        return None
    base=next((x for x in rows if x.get("variant")=="BASE"),None)
    fib=next((x for x in rows if x.get("variant")=="FIBONACCI"),None)
    chosen=base or fib

    if base is not None and fib is not None and bool(c.get("fib_confluence")):
        min_val=max(
            cfg.fib_min_validation_trades,
            int(math.ceil(0.35*max(base.get("trades_val",0),1)))
        )
        enough=fib.get("trades_val",0)>=min_val
        better_score=(
            fib.get("validation_score",0.0)
            >= base.get("validation_score",0.0)+cfg.fib_min_validation_score_gain
        )
        better_exp=(
            fib.get("expectancy_val_r",0.0)
            >= base.get("expectancy_val_r",0.0)+cfg.fib_min_validation_expectancy_gain_r
        )
        not_worse_pf=fib.get("pf_val",0.0)>=base.get("pf_val",0.0)
        if enough and better_score and better_exp and not_worse_pf:
            chosen=fib

    comparison=[]
    for row in rows:
        comparison.append({
            "variant":row.get("variant"),
            "trades_val":int(row.get("trades_val",0)),
            "expectancy_val_r":float(row.get("expectancy_val_r",0.0)),
            "pf_val":float(row.get("pf_val",0.0)),
            "psr_val":float(row.get("psr_val",0.0)),
            "validation_score":float(row.get("validation_score",0.0)),
        })
    chosen=dict(chosen)
    chosen["variant_comparison"]=comparison
    chosen["evidence"]=_evidence(chosen,cfg)
    return chosen


def _eval(symbol,c,cfg):
    tf=c["timeframe"]; d=c["direction"]
    yrs={"30m":1,"1h":2,"2h":3,"4h":4}.get(tf,2)
    bars=bars_for_years(tf,yrs)
    costs=LabCosts(futures_fee_each_side=0.0008)
    base=_history(symbol,tf,bars)
    empty_funding=pd.DataFrame(columns=["funding_time","funding_rate"])
    variants=["BASE"]
    if bool(c.get("fib_confluence")):
        variants.append("FIBONACCI")

    out=[]
    futures_rows=[]
    for variant in variants:
        row=_variant_result(symbol,c,cfg,base,empty_funding,"FUTURES",d,variant,costs)
        if row:
            futures_rows.append(row)
    selected=_choose_variant(futures_rows,c,cfg)
    if selected:
        out.append(selected)

    if d=="LONG":
        spot_rows=[]
        for variant in variants:
            row=_variant_result(symbol,c,cfg,base,empty_funding,"SPOT","LONG",variant,costs)
            if row:
                spot_rows.append(row)
        selected=_choose_variant(spot_rows,c,cfg)
        if selected:
            out.append(selected)
    return out

def _levels(c,live,cfg):
    r=c["row"]; a=float(r["atr"]); e=float(live)
    if c["direction"]=="LONG":
        stop=min(e-cfg.auto_stop_atr*a,float(r["swing_low_10"])-.10*a)
        risk=e-stop; tp=e+cfg.auto_reward_risk*risk
    else:
        stop=max(e+cfg.auto_stop_atr*a,float(r["swing_high_10"])+.10*a)
        risk=stop-e; tp=e-cfg.auto_reward_risk*risk
    if risk<=0 or tp<=0: raise ValueError("Niveles inválidos")
    return {"entry":e,"stop":stop,"tp":tp,"rr":cfg.auto_reward_risk}


def automatic_recommendation(symbol:str,cfg:StrategyConfig,operation_budget_cop=None,risk_pct=None,cop_per_usdt=None,candidates=None)->dict:
    budget=float(operation_budget_cop or cfg.default_capital_cop)
    risk=float(risk_pct or cfg.default_risk_pct)
    fx=float(cop_per_usdt or cfg.default_cop_per_usdt)
    cs=candidates if candidates is not None else detect_current_candidates(symbol,cfg)
    if not cs:
        return {"state":"NO OPERAR","reason":"Sin señal técnica en 30m/1H/2H/4H.","symbol":symbol,
                "operation_budget_cop":budget,"risk_budget_cop":budget*risk}

    good=[]; diags=[]
    for c in cs:
        try:
            outs=_eval(symbol,c,cfg); diags+=outs
            valid=[x for x in outs if x["evidence"]!="INSUFICIENTE"]
            if valid:
                valid.sort(key=lambda x:(RANK[x["evidence"]],x.get("validation_score",0.0),x.get("psr_test",0.0),x["expectancy_test_r"],x["prob_positive"],x["pf_test"]),reverse=True)
                good.append((c,valid[0],outs))
        except Exception as exc:
            diags.append({"error":f"{type(exc).__name__}: {exc}","setup":c["setup"],"timeframe":c["timeframe"]})

    if not good:
        ds=[x for x in diags if "expectancy_test_r" in x]
        reason="Hay señal técnica, pero ninguna alternativa supera el filtro estadístico V4.2."
        if ds:
            b=max(ds,key=lambda x:(x["expectancy_test_r"],x["prob_positive"]))
            reason+=f" Mejor: {b['instrument']} {b['direction']} {b['timeframe']} exp={b['expectancy_test_r']:+.3f}R PF={b['pf_test']:.2f} P+={b['prob_positive']*100:.0f}%."
        return {"state":"NO OPERAR","reason":reason,"symbol":symbol,"operation_budget_cop":budget,"risk_budget_cop":budget*risk}

    good.sort(key=lambda x:(RANK[x[1]["evidence"]],x[1].get("validation_score",0.0),x[1].get("psr_test",0.0),x[1]["expectancy_test_r"],x[1]["prob_positive"],x[1]["pf_test"]),reverse=True)
    c,w,outs=good[0]
    live=float(get_live_price(symbol))
    drift=abs(live-c["signal_close"])/max(c["atr"],1e-12)
    if drift>cfg.auto_max_entry_drift_atr:
        return {"state":"ESPERAR","reason":f"Señal válida pero precio alejado {drift:.2f} ATR.","symbol":symbol,
                "instrument":w["instrument"],"direction":c["direction"],"timeframe":c["timeframe"],"setup":c["setup"],
                "evidence":w["evidence"],"operation_budget_cop":budget,"risk_budget_cop":budget*risk}

    lv=_levels(c,live,cfg)
    if w["instrument"]=="FUTURES":
        lev=cfg.auto_futures_leverage_strong if w["evidence"]=="FUERTE" else cfg.auto_futures_leverage_promising
        cost=2*(.0005+.0002)
    else:
        lev=1; cost=2*(.0010+.0002)

    sz=position_size_directional(budget,risk,fx,lv["entry"],lv["stop"],lv["tp"],cost,c["direction"],lev)
    lsl=lv["stop"]*(1-cfg.stop_limit_buffer_pct) if w["instrument"]=="SPOT" else None
    return {
        "state":"OPERACIÓN CANDIDATA",
        "reason":f"{c['setup']} {c['direction']} {c['timeframe']} · evidencia {w['evidence']} · {w['instrument']} · variante {w.get('variant','BASE')}.",
        "symbol":symbol,"instrument":w["instrument"],"direction":c["direction"],"timeframe":c["timeframe"],
        "setup":c["setup"],"evidence":w["evidence"],"signal_time":str(c["signal_time"]),"entry_drift_atr":drift,"atr":float(c["atr"]),
        "analysis_variant":w.get("variant","BASE"),
        "fib_confluence":bool(c.get("fib_confluence",False)),
        "fib_nearest":float(c.get("fib_nearest",float("nan"))),
        "fib_distance_atr":float(c.get("fib_distance_atr",float("nan"))),
        "fib_extension_1272":float(c.get("fib_extension_1272",float("nan"))),
        "fib_extension_1618":float(c.get("fib_extension_1618",float("nan"))),
        "volatility_ratio":float(c.get("volatility_ratio",float("nan"))),
        "entry":lv["entry"],"stop":lv["stop"],"limit_sl":lsl,"take_profit":lv["tp"],"rr":lv["rr"],
        "leverage":lev,"margin_mode":"ISOLATED" if w["instrument"]=="FUTURES" else None,
        "operation_budget_cop":budget,"risk_budget_cop":budget*risk,
        "position_cop":sz["position_cop"],"position_usdt":sz["position_usdt"],"qty":sz["qty"],
        "margin_cop":sz["margin_cop"],"margin_usdt":sz["margin_usdt"],
        "net_loss_cop":sz["net_loss_cop"],"net_gain_cop":sz["net_gain_cop"],
        "actual_risk_pct_budget":sz["actual_risk_pct_budget"],"budget_used_pct":sz["budget_used_pct"],
        "stats":{"trades_test":int(w["trades_test"]),"win_test":float(w["win_test"]),
                 "expectancy_test_r":float(w["expectancy_test_r"]),"ci_low":float(w["ci_low"]),
                 "ci_high":float(w["ci_high"]),"prob_positive":float(w["prob_positive"]),
                 "pf_test":float(w["pf_test"]) if math.isfinite(w["pf_test"]) else None,
                 "r_per_year_test":float(w["r_per_year_test"]),"avg_fee_r":float(w["avg_fee_r"]),
                 "avg_funding_r":float(w["avg_funding_r"]),
                 "sharpe_test":float(w.get("sharpe_test",0.0)),
                 "sortino_test":float(w.get("sortino_test",0.0)),
                 "calmar_test":float(w.get("calmar_test",0.0)),
                 "psr_test":float(w.get("psr_test",0.0)),
                 "kelly_full_test":float(w.get("kelly_full_test",0.0)),
                 "kelly_conservative_test":float(w.get("kelly_conservative_test",0.0)),
                 "kelly_quarter_conservative_test":float(w.get("kelly_quarter_conservative_test",0.0)),
                 "wilson_win_low_test":float(w.get("wilson_win_low_test",0.0)),
                 "geometric_growth_pct_year_test":float(w.get("geometric_growth_pct_year_test",0.0)),
                 "validation_score":float(w.get("validation_score",0.0)),
                 "variant":w.get("variant","BASE"),
                 "variant_comparison":w.get("variant_comparison",[])},
        "alternatives":outs,
    }
