"""Private read-only Binance bridge for deployment in the user's eligible region.

This service NEVER implements trading, transfer or withdrawal endpoints.
Deploy behind HTTPS. Keep BINANCE_API_SECRET and BINANCE_BRIDGE_TOKEN only in
server-side environment variables/secrets.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os

from binance_readonly import (
    BinanceReadOnlyClient,
    BinanceLocationRestricted,
    BinanceReadOnlyError,
    balance_map,
    permission_is_read_only,
)


def snapshot():
    key = os.environ.get("BINANCE_API_KEY", "").strip()
    secret = os.environ.get("BINANCE_API_SECRET", "").strip()
    if not key or not secret:
        return 500, {"ok": False, "error": "Binance secrets missing on bridge"}
    try:
        client = BinanceReadOnlyClient(key, secret)
        permissions = client.permissions()
        if not permission_is_read_only(permissions):
            return 403, {
                "ok": False,
                "error": "API permissions are not read-only",
                "read_only": False,
            }
        balances = balance_map(client.account())
        usdt = balances.get("USDT", {"free": 0.0, "locked": 0.0, "total": 0.0})
        return 200, {
            "ok": True,
            "read_only": True,
            "usdt_free": float(usdt["free"]),
            "usdt_locked": float(usdt["locked"]),
            "usdt_total": float(usdt["total"]),
            "balances": balances,
        }
    except BinanceLocationRestricted:
        return 451, {"ok": False, "error": "Binance rejected this bridge location"}
    except BinanceReadOnlyError as exc:
        return 502, {"ok": False, "error": str(exc)[:240]}
    except Exception as exc:
        return 500, {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:180]}"}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body):
        raw = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def authorized(self):
        expected = os.environ.get("BINANCE_BRIDGE_TOKEN", "")
        header = self.headers.get("Authorization", "")
        supplied = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
        return bool(expected) and hmac.compare_digest(supplied, expected)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"ok": True, "service": "binance-readonly-bridge"})
        if self.path != "/snapshot":
            return self._send(404, {"ok": False, "error": "Not found"})
        if not self.authorized():
            return self._send(401, {"ok": False, "error": "Unauthorized"})
        status, body = snapshot()
        return self._send(status, body)

    def do_POST(self):
        return self._send(405, {"ok": False, "error": "Read-only service"})

    def log_message(self, *args):
        pass


def main():
    host = os.environ.get("BINANCE_BRIDGE_BIND", "127.0.0.1")
    port = int(os.environ.get("PORT", "8788"))
    if not os.environ.get("BINANCE_BRIDGE_TOKEN"):
        raise RuntimeError("BINANCE_BRIDGE_TOKEN is required")
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Binance read-only bridge listening on {host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
