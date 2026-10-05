"""Persistent, public-data-only service. Start only on an eligible, approved host."""
from __future__ import annotations
import asyncio
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
import time

import requests
from websockets.asyncio.client import connect

from config import CFG
from realtime_v52 import Supervisor, fresh, utcnow, MAX_TICK_AGE

RUNTIME = Path(".state/v52_runtime.json")
RESEARCH = Path(".state/v52_research.json")
DENIED_CODES = {401, 403, 418, 429, 451}


class AccessBlocked(RuntimeError):
    pass


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    os.replace(tmp, path)


def restriction(error):
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    if status in DENIED_CODES:
        return True
    text = str(error)
    return any(str(code) in text for code in DENIED_CODES) or "GeoRestricted" in text


def preflight():
    """One official host per market. A rejection prevents ALL WebSocket startup."""
    checks = [
        ("https://api.binance.com/api/v3/exchangeInfo", {"symbol": "BTCUSDT"}),
        ("https://api.binance.com/api/v3/ticker/24hr", {"symbols": json.dumps(list(CFG.symbols))}),
        ("https://fapi.binance.com/fapi/v1/exchangeInfo", {}),
        ("https://fapi.binance.com/fapi/v1/premiumIndex", {"symbol": "BTCUSDT"}),
    ]
    for url, params in checks:
        response = requests.get(url, params=params, timeout=15)
        if response.status_code in DENIED_CODES:
            raise AccessBlocked(f"Acceso detenido: HTTP {response.status_code}. Revisar permiso, región o límites; no cambiar de ruta.")
        response.raise_for_status()


def streams(symbols):
    # Official split introduced by Binance: bookTicker /public, trades and mark /market.
    names = [s.lower() for s in symbols]
    return [
        ("SPOT", "wss://stream.binance.com:9443/stream?streams=" + "/".join(
            f"{s}@{kind}" for s in names for kind in ("bookTicker", "aggTrade", "miniTicker"))),
        ("FUTURES", "wss://fstream.binance.com/public/stream?streams=" + "/".join(f"{s}@bookTicker" for s in names)),
        ("FUTURES", "wss://fstream.binance.com/market/stream?streams=" + "/".join(
            f"{s}@{kind}" for s in names for kind in ("aggTrade", "markPrice@1s"))),
    ]


def research_once():
    """CPU-heavy research in a separate process so it cannot delay the 1s loop."""
    from monitor_v51 import run_scan
    from state_v5 import load_state
    from market_v5 import exchange_rules, quote
    report = run_scan(load_state(), notify=False)
    candidate = report.get("candidate")
    if candidate:
        symbol, instrument = candidate["symbol"], candidate["instrument"]
        rules = exchange_rules(symbol, instrument)
        snap = quote(symbol, instrument)
        report["market_rules"] = {"symbol": symbol, "instrument": instrument, "rules": rules,
                                  "funding_interval_hours": snap["funding_interval_hours"],
                                  "checked_at": utcnow().isoformat()}
    atomic_json(RESEARCH, report)


class Service:
    def __init__(self, state_path=RUNTIME):
        self.path = Path(state_path)
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {"sent": [], "invalidated": {}, "blocked": None}
        self.supervisor = Supervisor(CFG.symbols, self.state["invalidated"])
        previous_keys = self.state.get("watched_keys", [])
        if self.state.get("last_candidate_key"):
            previous_keys = previous_keys + [self.state["last_candidate_key"]]
        for key in previous_keys:
            self.supervisor.invalidated[key] = utcnow().isoformat()
        self.supervisor.blocked = self.state.get("blocked")
        self.report = None
        self.lock = threading.Lock()
        self.status = "BLOQUEADO" if self.supervisor.blocked else "INICIANDO"
        self.alert_task = None
        self.next_alert_attempt = 0
        self.alert_error = None
        self.research_error = None

    def save(self):
        self.supervisor.invalidated = dict(list(self.supervisor.invalidated.items())[-2000:])
        self.state["invalidated"] = dict(self.supervisor.invalidated)
        self.state["sent"] = self.state["sent"][-2000:]
        atomic_json(self.path, self.state)

    def block(self, reason):
        self.supervisor.blocked = reason
        self.state["blocked"] = reason
        self.supervisor.book.clear("SPOT")
        self.supervisor.book.clear("FUTURES")
        self.status = "BLOQUEADO"
        self.save()

    async def listen(self, instrument, url):
        backoff = 1
        while not self.supervisor.blocked:
            try:
                async with connect(url, open_timeout=15, ping_interval=20, ping_timeout=20,
                                   max_size=1_000_000, max_queue=8) as socket:
                    backoff = 1
                    while not self.supervisor.blocked:
                        message = await asyncio.wait_for(socket.recv(), timeout=15)
                        self.supervisor.book.ingest(instrument, json.loads(message))
                        self.supervisor.observe()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # A disconnect discards all related prices; no reused timestamp.
                self.supervisor.book.clear(instrument)
                if isinstance(exc, AccessBlocked) or restriction(exc):
                    self.block("Binance restringió la conexión. Revisar el acceso antes de reiniciar.")
                    return
                await asyncio.sleep(backoff)
                backoff = min(60, backoff*2)

    async def research_loop(self):
        while not self.supervisor.blocked:
            start = time.monotonic()
            process = None
            try:
                await asyncio.to_thread(preflight)
                process = await asyncio.create_subprocess_exec(sys.executable, "service_v52.py", "--research",
                                                               stdout=asyncio.subprocess.DEVNULL,
                                                               stderr=asyncio.subprocess.DEVNULL)
                await asyncio.wait_for(process.wait(), timeout=270)
                if process.returncode:
                    raise RuntimeError("No terminó el análisis histórico; se conserva su fecha anterior")
                report = json.loads(RESEARCH.read_text(encoding="utf-8"))
                if report.get("blocked") or any(restriction(error) for error in report.get("errors", [])):
                    raise AccessBlocked("Acceso restringido durante la investigación. Servicio detenido.")
                if not self.supervisor.blocked:
                    self.supervisor.install_research(report)
                    self.research_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.research_error = "Análisis histórico no actualizado; se bloquearán señales vencidas."
                if isinstance(exc, AccessBlocked) or restriction(exc):
                    self.block("Acceso nativo restringido. No se habilitará una conexión alternativa.")
                    return
            finally:
                if process is not None and process.returncode is None:
                    process.kill()
                    await process.wait()
            await asyncio.sleep(max(1, 300-(time.monotonic()-start)))

    async def alert(self, signal):
        from monitor_v51 import opportunity_message, telegram
        try:
            await asyncio.to_thread(telegram, opportunity_message(signal).replace("V5.1", "V5.2"))
            self.state["sent"].append(signal["key"])
            self.save()
            self.alert_error = None
        except Exception:
            self.alert_error = "Telegram no confirmó la entrega. La señal sigue disponible en la app."

    async def evaluate(self, stop=None):
        deadline = time.monotonic()
        while stop is None or not stop.is_set():
            report = self.supervisor.tick()
            report["service_status"] = self.status
            report["telegram_status"] = "ERROR" if self.alert_error else (
                "CONFIGURADO" if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID") else "SIN CONFIGURAR")
            for error in (self.alert_error, self.research_error):
                if error:
                    report["errors"].append(error)
                    report["health"] = "PARCIAL"
            report["loop_delay_ms"] = round(max(0, time.monotonic()-deadline)*1000)
            if self.state["invalidated"] != self.supervisor.invalidated:
                self.save()
            with self.lock:
                self.report = report
            candidate = report.get("candidate")
            if candidate and report.get("live_quote") and self.state.get("last_candidate_key") != candidate["key"]:
                self.state["last_candidate_key"] = candidate["key"]
                self.state["watched_keys"] = list(dict.fromkeys(self.state.get("watched_keys", [])+[candidate["key"]]))[-2000:]
                self.save()
            if (report["entry_valid"] and candidate["key"] not in self.state["sent"] and
                    report["telegram_status"] != "SIN CONFIGURAR" and time.monotonic() >= self.next_alert_attempt and
                    (self.alert_task is None or self.alert_task.done())):
                self.next_alert_attempt = time.monotonic()+60
                self.alert_task = asyncio.create_task(self.alert(deepcopy(candidate)))
            # No burst of catch-up evaluations after a stalled process.
            deadline = max(deadline+1, time.monotonic())
            await asyncio.sleep(max(0, deadline-time.monotonic()))

    def snapshot(self):
        with self.lock:
            return deepcopy(self.report)

    async def run(self):
        evaluator = asyncio.create_task(self.evaluate())
        tasks = [evaluator]
        try:
            if not self.supervisor.blocked:
                if os.getenv("TRADER_ACCESS_CONFIRMED") != "yes":
                    self.supervisor.blocked = "Pendiente de confirmar elegibilidad del usuario y alojamiento para Spot/Futures."
                    self.status = "PENDIENTE"
                else:
                    try:
                        await asyncio.to_thread(preflight)
                        self.status = "VIGILANDO"
                        tasks.extend(asyncio.create_task(self.listen(market, url)) for market, url in streams(CFG.symbols))
                        tasks.append(asyncio.create_task(self.research_loop()))
                    except Exception as exc:
                        self.block("Prevalidación nativa fallida. Revisar permisos, conexión y ubicación antes de activar.")
            await evaluator
        finally:
            for task in tasks + ([self.alert_task] if self.alert_task else []):
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


def http_server(service, host="127.0.0.1", port=8787):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            report = service.snapshot()
            if self.path == "/health":
                alive = bool(report and fresh(report["finished_at"], utcnow(), MAX_TICK_AGE))
                body = {"alive": alive, "market_status": (report or {}).get("health", "INICIANDO"),
                        "service_status": service.status, "ready": bool(alive and report.get("health") == "ACTUALIZADO")}
                status = 200 if alive else 503
            elif self.path == "/snapshot" and report:
                body, status = report, 200
            else:
                body, status = {"status": "No disponible"}, 404
            encoded = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main():
    if "--research" in sys.argv:
        try:
            research_once()
        except Exception as exc:
            if restriction(exc) or isinstance(exc, AccessBlocked):
                atomic_json(RESEARCH, {"blocked": True, "errors": ["Acceso nativo restringido en investigación"],
                                       "candidate": None, "finished_at": utcnow().isoformat()})
            else:
                raise
        return
    service = Service()
    server = http_server(service, os.getenv("TRADER_BIND", "127.0.0.1"), int(os.getenv("PORT", "8787")))
    try:
        asyncio.run(service.run())
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
