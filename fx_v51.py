"""Automatic public FX reference. This is NOT a Binance P2P purchase quote."""
from __future__ import annotations
import math
import pandas as pd
import requests

TRM_URL = "https://www.datos.gov.co/resource/32sa-8pi3.json"
FX_BUFFER = .03


def automatic_fx(now=None):
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    day = now.tz_convert("America/Bogota").date().isoformat()
    response = requests.get(TRM_URL, params={"$where": f"vigenciadesde <= '{day}T00:00:00' AND vigenciahasta >= '{day}T00:00:00'",
                                            "$order": "vigenciadesde DESC", "$limit": 1}, timeout=12)
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list) or not rows:
        raise ValueError("No hay TRM vigente; no convertir capital con una tasa inventada")
    row = rows[0]
    rate = float(row["valor"])
    start, end = pd.Timestamp(row["vigenciadesde"]).date(), pd.Timestamp(row["vigenciahasta"]).date()
    if not math.isfinite(rate) or not 1000 <= rate <= 10000 or not start <= pd.Timestamp(day).date() <= end:
        raise ValueError("TRM inválida o fuera de vigencia")
    return {"trm": rate, "cop_per_usdt": rate*(1+FX_BUFFER), "buffer_pct": FX_BUFFER,
            "valid_from": start.isoformat(), "valid_to": end.isoformat(), "checked_at": now.isoformat(),
            "source": "TRM · Datos Abiertos Colombia / Superfinanciera",
            "estimated": True, "note": "TRM + reserva de conversión 3%; no es el precio real de USDT en Binance"}


def validate_fx(fx, now=None):
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    day = now.tz_convert("America/Bogota").date()
    if not fx or not pd.Timestamp(fx["valid_from"]).date() <= day <= pd.Timestamp(fx["valid_to"]).date():
        raise ValueError("Conversión automática sin tasa vigente; esperar actualización")
    rate = float(fx["cop_per_usdt"])
    if not math.isfinite(rate) or not 1000 <= rate <= 11000:
        raise ValueError("Conversión automática inválida")
    return rate
