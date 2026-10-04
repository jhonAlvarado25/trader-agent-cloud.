from __future__ import annotations

import requests
import pandas as pd

REPO_API = "https://api.github.com/repos/jhonAlvarado25/trader-agent-cloud."
WORKFLOW = "auto-recommend-v4.yml"
BOGOTA_TZ = "America/Bogota"


def next_4h_close(now_utc: pd.Timestamp | None = None) -> pd.Timestamp:
    now = now_utc or pd.Timestamp.now(tz="UTC")
    return now.floor("4h") + pd.Timedelta(hours=4)


def next_monitor_run(now_utc: pd.Timestamp | None = None) -> pd.Timestamp:
    now = now_utc or pd.Timestamp.now(tz="UTC")
    base = now.floor("h")
    for minute in range(2, 60, 5):
        candidate = base + pd.Timedelta(minutes=minute)
        if candidate > now:
            return candidate
    return base + pd.Timedelta(hours=1, minutes=2)


def format_local(ts) -> str:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    return t.tz_convert(BOGOTA_TZ).strftime("%Y-%m-%d %I:%M:%S %p")


def get_last_scheduled_run(timeout: int = 8) -> dict | None:
    url = f"{REPO_API}/actions/workflows/{WORKFLOW}/runs"
    params = {"event": "schedule", "per_page": 1}
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "TraderAgentCloudV5",
    }
    try:
        r = requests.get(url, params=params, headers=headers, timeout=timeout)
        if r.status_code != 200:
            return None
        runs = r.json().get("workflow_runs", [])
        if not runs:
            return None
        run = runs[0]
        return {
            "status": run.get("status"),
            "conclusion": run.get("conclusion"),
            "created_at": run.get("created_at"),
            "updated_at": run.get("updated_at"),
            "html_url": run.get("html_url"),
            "run_number": run.get("run_number"),
        }
    except requests.RequestException:
        return None
