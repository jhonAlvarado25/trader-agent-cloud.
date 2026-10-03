from __future__ import annotations
import math
import pandas as pd

from config import StrategyConfig
from market import get_klines, get_live_price
from futures_market import get_futures_klines, get_funding_history
from futures_lab import LabCosts, bars_for_years, enrich_lab, simulate_config, summarize_config
from risk import position_size_directional

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


def _mask(df,c):
    o=enrich_auto(df)
    d=c["direction"]; s=c["setup"]
    if s=="STRICT_PULLBACK":
        m=o["long_signal"] if d=="LONG" else o["short_signal"]
    elif s=="BALANCED_PULLBACK":
        m=o["bal_pb_long"] if d=="LONG" else o["bal_pb_short"]
    else:
        m=o["bal_bo_long"] if d=="LONG" else o["bal_bo_short"]
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
            out.append({"timeframe":tf,"direction":direction,"setup":setup,"score":int(score),
                        "priority":priority,"signal_time":fr["close_time"],"signal_close":float(fr["close"]),
                        "atr":float(fr["atr"]),"row":fr})

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


def _eval(symbol,c,cfg):
    tf=c["timeframe"]; d=c["direction"]
    yrs={"30m":1,"1h":2,"2h":3,"4h":4}.get(tf,2)
    bars=bars_for_years(tf,yrs)
    # Monitor rápido: precios Spot como proxy del perpetual. Reservamos costo
    # adicional en Futures para no sobreestimar la ventaja cuando funding no
    # está disponible desde el runner cloud.
    costs=LabCosts(futures_fee_each_side=0.0008)
    out=[]
    base=_history(symbol,tf,bars)
    f=_mask(base,c).dropna().reset_index(drop=True)
    funding=pd.DataFrame(columns=["funding_time","funding_rate"])
    tr=simulate_config(f,funding,d,"FUTURES",cfg.auto_stop_atr,cfg.auto_reward_risk,tf,costs)
    st=summarize_config(tr,f["open_time"].iloc[0],f["close_time"].iloc[-1],cfg.auto_bootstrap_sims,cfg.default_risk_pct)
    if st:
        st["evidence"]=_evidence(st,cfg)
        out.append({"instrument":"FUTURES","direction":d,"timeframe":tf,"setup":c["setup"],**st})

    if d=="LONG":
        s=_mask(base,c).dropna().reset_index(drop=True)
        tr=simulate_config(s,pd.DataFrame(columns=["funding_time","funding_rate"]),"LONG","SPOT",cfg.auto_stop_atr,cfg.auto_reward_risk,tf,costs)
        st=summarize_config(tr,s["open_time"].iloc[0],s["close_time"].iloc[-1],cfg.auto_bootstrap_sims,cfg.default_risk_pct)
        if st:
            st["evidence"]=_evidence(st,cfg)
            out.append({"instrument":"SPOT","direction":"LONG","timeframe":tf,"setup":c["setup"],**st})
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
                valid.sort(key=lambda x:(RANK[x["evidence"]],x["expectancy_test_r"],x["prob_positive"],x["pf_test"]),reverse=True)
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

    good.sort(key=lambda x:(RANK[x[1]["evidence"]],x[1]["expectancy_test_r"],x[1]["prob_positive"],x[1]["pf_test"]),reverse=True)
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
        "reason":f"{c['setup']} {c['direction']} {c['timeframe']} · evidencia {w['evidence']} · {w['instrument']}.",
        "symbol":symbol,"instrument":w["instrument"],"direction":c["direction"],"timeframe":c["timeframe"],
        "setup":c["setup"],"evidence":w["evidence"],"signal_time":str(c["signal_time"]),"entry_drift_atr":drift,"atr":float(c["atr"]),
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
                 "avg_funding_r":float(w["avg_funding_r"])},
        "alternatives":outs,
    }
