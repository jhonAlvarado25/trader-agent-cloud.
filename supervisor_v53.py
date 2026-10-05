"""V5.3 resilient supervisor: repeated research cycles inside one GitHub job."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

TARGET_SECONDS = 300
SNAPSHOT = Path(".state/market_snapshot.json")


def run_cycle(number: int) -> dict:
    env = os.environ.copy()
    # Keep the workflow summary compact; the supervisor writes one final line.
    env.pop("GITHUB_STEP_SUMMARY", None)
    started = time.monotonic()
    subprocess.run([sys.executable, "monitor_v51.py"], check=True, timeout=270, env=env)
    subprocess.run([sys.executable, "publish_market_v51.py"], check=True, timeout=60, env=env)
    report = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    elapsed = time.monotonic() - started
    print(
        f"[V5.3] ciclo {number}: {len(report.get('markets', []))} activos · "
        f"salud {report.get('health')} · alertas {report.get('alerts_delivered', 0)} · "
        f"{elapsed:.1f}s"
    )
    return report


def main() -> int:
    rounds = max(1, min(12, int(os.getenv("TRADER_SUPERVISOR_ROUNDS", "1"))))
    last = None
    failures = 0
    for index in range(1, rounds + 1):
        cycle_start = time.monotonic()
        try:
            last = run_cycle(index)
        except Exception as exc:
            failures += 1
            print(f"[V5.3] ciclo {index} falló: {type(exc).__name__}: {str(exc)[:180]}")
        if index < rounds:
            remaining = TARGET_SECONDS - (time.monotonic() - cycle_start)
            if remaining > 0:
                time.sleep(remaining)

    summary = (
        f"V5.3 supervisor · ciclos solicitados {rounds} · fallos {failures} · "
        f"última salud {(last or {}).get('health', 'SIN INFORME')}\n"
    )
    print(summary.strip())
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(summary)
    # A failed cycle should make the job visible as unhealthy, but transient
    # failures do not stop subsequent cycles.
    return 1 if failures == rounds else 0


if __name__ == "__main__":
    raise SystemExit(main())
