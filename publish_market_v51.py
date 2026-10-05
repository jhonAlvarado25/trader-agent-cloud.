"""Publish allowlisted public market research on a separate branch, not app code."""
from __future__ import annotations
import json
import os
from pathlib import Path
import requests

REPO = "jhonAlvarado25/trader-agent-cloud."
BRANCH = "market-data"


def publish(path=".state/market_snapshot.json"):
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    allowed = {"version", "started_at", "finished_at", "scheduled_interval_minutes", "scheduler", "risk_pct", "fx", "markets",
               "candidate", "pause", "errors", "alerts_delivered", "health", "simulated_positions"}
    if report.get("version") not in {"5.1", "5.3"} or set(report)-allowed:
        raise ValueError("Solo se permite publicar el informe público V5.1/V5.3")
    forbidden = {"profile", "journal", "api_key", "api_secret", "capital_cop", "operation_budget_cop",
                 "available_cop", "TELEGRAM_BOT_TOKEN", "BINANCE_API_SECRET"}
    def check(value):
        if isinstance(value, dict):
            if forbidden & set(value):
                raise ValueError("Datos privados no publicables")
            for item in value.values():
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)
    check(report)
    token = os.environ["V51_PUBLISH_TOKEN"]
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                            "X-GitHub-Api-Version": "2022-11-28"})
    base = f"https://api.github.com/repos/{REPO}"
    def api(method, suffix, data=None):
        response = session.request(method, base+suffix, json=data, timeout=20)
        if not response.ok:
            raise RuntimeError(f"Publicación rechazada: HTTP {response.status_code}; no intentar otro destino")
        return response.json()
    ref = session.get(base+f"/git/ref/heads/{BRANCH}", timeout=20)
    parents, base_tree = [], None
    if ref.status_code == 200:
        parent = ref.json()["object"]["sha"]
        old = api("GET", f"/git/commits/{parent}")
        parents, base_tree = [parent], old["tree"]["sha"]
    elif ref.status_code != 404:
        raise RuntimeError(f"No se puede leer la rama de datos: HTTP {ref.status_code}")
    tree = {"tree": [{"path": "market_snapshot.json", "mode": "100644", "type": "blob",
                       "content": json.dumps(report, ensure_ascii=False, allow_nan=False)}]}
    if base_tree:
        tree["base_tree"] = base_tree
    tree_sha = api("POST", "/git/trees", tree)["sha"]
    commit = api("POST", "/git/commits", {"message": "Actualizar revisión pública del mercado V5.1",
                                         "tree": tree_sha, "parents": parents})["sha"]
    if parents:
        api("PATCH", f"/git/refs/heads/{BRANCH}", {"sha": commit, "force": False})
    else:
        api("POST", "/git/refs", {"ref": f"refs/heads/{BRANCH}", "sha": commit})
    print("Informe público actualizado; no se publicaron capital, cuenta ni bitácora del usuario.")


if __name__ == "__main__":
    publish()
