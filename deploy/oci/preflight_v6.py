from __future__ import annotations

import json
import sys
import time

import requests

CHECKS = [
    ("Spot time", "https://api.binance.com/api/v3/time", None),
    ("Spot exchangeInfo", "https://api.binance.com/api/v3/exchangeInfo", {"symbol": "BTCUSDT"}),
    ("Futures time", "https://fapi.binance.com/fapi/v1/time", None),
    ("Futures exchangeInfo", "https://fapi.binance.com/fapi/v1/exchangeInfo", None),
]

DENIED = {401, 403, 418, 429, 451}


def main() -> int:
    ok = True
    print("Trader V6 · preflight de conectividad Binance")
    print("No usa API keys y no realiza operaciones.")
    for name, url, params in CHECKS:
        started = time.perf_counter()
        try:
            r = requests.get(url, params=params, timeout=12)
            elapsed = (time.perf_counter() - started) * 1000
            if r.status_code in DENIED:
                ok = False
                print(f"[BLOQUEADO] {name}: HTTP {r.status_code} · {elapsed:.0f} ms")
                continue
            r.raise_for_status()
            payload = r.json()
            if not isinstance(payload, (dict, list)):
                raise ValueError("respuesta JSON inesperada")
            print(f"[OK] {name}: HTTP {r.status_code} · {elapsed:.0f} ms")
        except Exception as exc:
            ok = False
            print(f"[ERROR] {name}: {type(exc).__name__}: {str(exc)[:180]}")
    print("\nRESULTADO:", "APTO PARA CONTINUAR" if ok else "NO CONTINUAR TODAVÍA")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
