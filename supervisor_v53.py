"""V5.3 resilient supervisor: repeated research cycles inside one GitHub job."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time

from monitor_v51 import OUTPUT, run_scan
from publish_market_v51 import publish
from state_v5 import load_state

TARGET_SECONDS = 300
SNAPSHOT = Path(".state/market_snapshot.json")


def run_cycle(number: int) -> dict:
    started = time.monotonic()
    state = load_state()
    report = run_scan(state)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    publish(OUTPUT)
    elapsed = time.monotonic() - started
    print(
        f"[V5.3] ciclo {number}: {len(report.get('markets', []))} activos · "
        f"salud {report.get('health')} · fuertes {report.get('alerts_delivered', 0)} · "
        f"observación {report.get('observations_delivered', 0)} · {elapsed:.1f}s"
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
    return 1 if failures == rounds else 0


if __name__ == "__main__":
    raise SystemExit(main())
