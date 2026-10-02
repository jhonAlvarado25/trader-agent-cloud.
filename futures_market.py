from __future__ import annotations

import io
import time
import zipfile
from datetime import timedelta

import requests
import pandas as pd

from market import get_klines as get_spot_klines

FUTURES_BASE_URLS = ["https://fapi.binance.com"]
VISION_BASE = "https://data.binance.vision"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "TraderAgentV4.1/4.1",
    "Accept": "*/*",
})

_last_futures_source = "Pendiente"


class FuturesGeoRestricted(RuntimeError):
    pass


def get_futures_data_source() -> str:
    return _last_futures_source


def _request(path: str, params: dict, timeout: int = 15):
    errors = []
    for base in FUTURES_BASE_URLS:
        try:
            r = SESSION.get(f"{base}{path}", params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 451:
                raise FuturesGeoRestricted(
                    "Binance Futures REST devolvió HTTP 451 por restricción geográfica del servidor cloud."
                )
            errors.append(f"{base}: HTTP {r.status_code} {r.text[:120]}")
        except FuturesGeoRestricted:
            raise
        except requests.RequestException as exc:
            errors.append(f"{base}: {type(exc).__name__}")
    raise RuntimeError("No fue posible consultar Binance Futures REST. " + " | ".join(errors))


def _interval_hours(interval: str) -> float:
    mapping = {
        "1m": 1/60, "3m": 3/60, "5m": 5/60, "15m": 0.25,
        "30m": 0.5, "1h": 1, "2h": 2, "4h": 4, "6h": 6,
        "8h": 8, "12h": 12, "1d": 24, "3d": 72, "1w": 168,
    }
    if interval not in mapping:
        raise ValueError(f"Intervalo no soportado por fallback: {interval}")
    return mapping[interval]


def _normalize_kline_rows(rows, bars: int) -> pd.DataFrame:
    cols = [
        "open_time","open","high","low","close","volume",
        "close_time","quote_volume","trades","taker_base",
        "taker_quote","ignore"
    ]
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=cols)

    # Los CSV públicos pueden venir con o sin cabecera.
    if not pd.to_numeric(df.iloc[:, 0], errors="coerce").notna().iloc[0]:
        df = df.iloc[1:].reset_index(drop=True)

    df = df.iloc[:, :12]
    df.columns = cols

    for c in ["open_time","close_time"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in ["open","high","low","close","volume","quote_volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df = df.dropna(subset=["open_time","close_time","open","high","low","close"])

    # Futures Vision usa milisegundos; si aparecieran microsegundos se detectan.
    open_unit = "us" if float(df["open_time"].median()) > 1e14 else "ms"
    close_unit = "us" if float(df["close_time"].median()) > 1e14 else "ms"
    df["open_time"] = pd.to_datetime(df["open_time"], unit=open_unit, utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit=close_unit, utc=True)

    df = (
        df.drop_duplicates("open_time")
          .sort_values("open_time")
          .tail(int(bars))
          .reset_index(drop=True)
    )
    return df


def _zip_csv(url: str, timeout: int = 30) -> pd.DataFrame | None:
    try:
        r = SESSION.get(url, timeout=timeout)
        if r.status_code == 404:
            return None
        r.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if not names:
                return None
            with zf.open(names[0]) as fh:
                return pd.read_csv(fh, header=None, low_memory=False)
    except (requests.RequestException, zipfile.BadZipFile, pd.errors.ParserError):
        return None


def _month_range(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    s = pd.Timestamp(start).tz_convert("UTC").tz_localize(None).to_period("M").to_timestamp()
    e = pd.Timestamp(end).tz_convert("UTC").tz_localize(None).to_period("M").to_timestamp()
    return list(pd.date_range(s, e, freq="MS"))


def _archive_klines(symbol: str, interval: str, bars: int) -> pd.DataFrame:
    now = pd.Timestamp.now(tz="UTC")
    hours = _interval_hours(interval)

    # Margen extra para asegurar suficientes barras tras huecos/listados recientes.
    lookback_days = max(45, int((bars * hours / 24) * 1.20) + 35)
    start = now - pd.Timedelta(days=lookback_days)

    frames = []
    for month in _month_range(start, now):
        stamp = month.strftime("%Y-%m")
        url = (
            f"{VISION_BASE}/data/futures/um/monthly/klines/"
            f"{symbol}/{interval}/{symbol}-{interval}-{stamp}.zip"
        )
        part = _zip_csv(url)
        if part is not None and not part.empty:
            frames.append(part)


    if not frames:
        raise RuntimeError("Binance Vision no devolvió archivos Futures para este período.")

    raw = pd.concat(frames, ignore_index=True)
    return _normalize_kline_rows(raw.values.tolist(), bars)


def _spot_proxy(symbol: str, interval: str, bars: int) -> pd.DataFrame:
    global _last_futures_source
    df = get_spot_klines(symbol, interval, bars)
    _last_futures_source = "Spot proxy en tiempo real (Binance Futures REST bloqueado por geolocalización cloud)"
    return df


def get_futures_klines(symbol: str, interval: str, bars: int) -> pd.DataFrame:
    """Obtiene USD-M Futures.

    Prioridad:
    1) REST oficial fapi.
    2) Para consultas cortas/currentes: Spot Binance como proxy temporal.
    3) Para historia larga: archivo oficial Binance Vision + cola Spot reciente.

    El proxy evita que un HTTP 451 del servidor Streamlit bloquee todo el agente.
    """
    global _last_futures_source

    try:
        rows = []
        remaining = int(bars)
        end_time = None

        while remaining > 0:
            request_limit = min(remaining, 1500)
            params = {"symbol": symbol, "interval": interval, "limit": request_limit}
            if end_time is not None:
                params["endTime"] = int(end_time)

            batch = _request("/fapi/v1/klines", params, timeout=20)
            if not batch:
                break

            rows = batch + rows
            end_time = int(batch[0][0]) - 1
            remaining -= len(batch)

            if len(batch) < request_limit:
                break
            time.sleep(0.05)

        if rows:
            _last_futures_source = "Binance USD-M Futures REST (fapi)"
            by_open = {int(r[0]): r for r in rows}
            clean = [by_open[k] for k in sorted(by_open)][-bars:]
            return _normalize_kline_rows(clean, bars)

    except (FuturesGeoRestricted, RuntimeError):
        pass

    # Para señales actuales no tiene sentido descargar años de archivos.
    if int(bars) <= 500:
        return _spot_proxy(symbol, interval, bars)

    # Para backtesting se utiliza el archivo histórico oficial de Futures.
    try:
        hist = _archive_klines(symbol, interval, bars)
        if hist.empty:
            raise RuntimeError("Archivo Futures vacío.")

        # El archivo público puede ir con retraso de un día. Para evitar señal obsoleta,
        # completar únicamente la cola reciente con Spot (proxy), manteniendo Futures
        # como fuente principal del backtest.
        hours = _interval_hours(interval)
        tail_n = min(max(200, int(35*24/hours)), int(bars))
        spot_tail = get_spot_klines(symbol, interval, tail_n)
        cutoff = hist["open_time"].max()
        extra = spot_tail[spot_tail["open_time"] > cutoff]

        if not extra.empty:
            merged = pd.concat([hist, extra], ignore_index=True)
            merged = (
                merged.drop_duplicates("open_time")
                      .sort_values("open_time")
                      .tail(int(bars))
                      .reset_index(drop=True)
            )
            _last_futures_source = "Binance Vision Futures + Spot proxy para cola reciente"
            return merged

        _last_futures_source = "Binance Vision USD-M Futures (archivo público oficial)"
        return hist.tail(int(bars)).reset_index(drop=True)

    except Exception:
        # Último fallback: mantener el agente operativo y etiquetar claramente el proxy.
        return _spot_proxy(symbol, interval, bars)


def _archive_funding(symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    start = pd.to_datetime(int(start_ms), unit="ms", utc=True)
    end = pd.to_datetime(int(end_ms), unit="ms", utc=True)

    frames = []
    for month in _month_range(start, end):
        stamp = month.strftime("%Y-%m")
        url = (
            f"{VISION_BASE}/data/futures/um/monthly/fundingRate/"
            f"{symbol}/{symbol}-fundingRate-{stamp}.zip"
        )
        part = _zip_csv(url)
        if part is not None and not part.empty:
            frames.append(part)

    if not frames:
        return pd.DataFrame(columns=["funding_time","funding_rate"])

    raw = pd.concat(frames, ignore_index=True)

    # Formato conocido de Vision:
    # calc_time, funding_interval_hours, last_funding_rate
    # Puede incluir cabecera.
    if not pd.to_numeric(raw.iloc[:, 0], errors="coerce").notna().iloc[0]:
        raw = raw.iloc[1:].reset_index(drop=True)

    if raw.shape[1] < 3:
        return pd.DataFrame(columns=["funding_time","funding_rate"])

    calc_time = pd.to_numeric(raw.iloc[:, 0], errors="coerce")
    rate = pd.to_numeric(raw.iloc[:, -1], errors="coerce")
    unit = "us" if float(calc_time.dropna().median()) > 1e14 else "ms"

    df = pd.DataFrame({
        "funding_time": pd.to_datetime(calc_time, unit=unit, utc=True, errors="coerce"),
        "funding_rate": rate,
    }).dropna()

    df = df[
        (df["funding_time"] >= start) &
        (df["funding_time"] <= end)
    ]

    return (
        df.drop_duplicates("funding_time")
          .sort_values("funding_time")
          .reset_index(drop=True)
    )


def get_funding_history(symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    global _last_futures_source

    try:
        rows = []
        cursor = int(start_ms)

        while cursor <= int(end_ms):
            batch = _request(
                "/fapi/v1/fundingRate",
                {
                    "symbol": symbol,
                    "startTime": cursor,
                    "endTime": int(end_ms),
                    "limit": 1000,
                },
                timeout=20,
            )
            if not batch:
                break

            rows.extend(batch)
            last_ts = int(batch[-1]["fundingTime"])
            if last_ts < cursor:
                break
            cursor = last_ts + 1
            if len(batch) < 1000:
                break
            time.sleep(0.05)

        if rows:
            df = pd.DataFrame(rows)
            df["funding_time"] = pd.to_datetime(
                pd.to_numeric(df["fundingTime"]), unit="ms", utc=True
            )
            df["funding_rate"] = pd.to_numeric(
                df["fundingRate"], errors="coerce"
            ).fillna(0.0)
            return (
                df[["funding_time","funding_rate"]]
                .drop_duplicates("funding_time")
                .sort_values("funding_time")
                .reset_index(drop=True)
            )

    except (FuturesGeoRestricted, RuntimeError):
        pass

    # Fallback oficial: Binance Vision.
    df = _archive_funding(symbol, start_ms, end_ms)
    if not df.empty:
        if "Vision" not in _last_futures_source:
            _last_futures_source = (
                "Binance Vision Futures/Funding (fallback por restricción geográfica de fapi)"
            )
        return df

    # No inventar funding si no hay archivo: devolver vacío; el backtest lo tratará como 0
    # y la interfaz debe indicar que el componente funding no estuvo disponible.
    return pd.DataFrame(columns=["funding_time","funding_rate"])
