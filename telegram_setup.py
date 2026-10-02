from __future__ import annotations

import os
import sys
import requests

API = "https://api.telegram.org"


def main() -> int:
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

    if not token:
        print("Falta el secret TELEGRAM_BOT_TOKEN.")
        return 2

    if chat_id:
        r = requests.post(
            f"{API}/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": (
                    "Trader Agent V4.1 conectado correctamente. "
                    "Telegram recibirá alertas cuando aparezca una operación candidata."
                ),
            },
            timeout=20,
        )
        if r.status_code != 200:
            print(f"Telegram respondió HTTP {r.status_code}: {r.text[:300]}")
            return 3
        print("Mensaje de prueba enviado correctamente.")
        return 0

    r = requests.get(
        f"{API}/bot{token}/getUpdates",
        params={"limit": 20, "timeout": 0},
        timeout=20,
    )
    if r.status_code != 200:
        print(f"Telegram respondió HTTP {r.status_code}: {r.text[:300]}")
        return 3

    data = r.json()
    found = []
    for update in data.get("result", []):
        msg = update.get("message") or update.get("channel_post") or {}
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        if cid is None:
            continue
        name = (
            chat.get("username")
            or chat.get("title")
            or " ".join(
                x for x in [chat.get("first_name"), chat.get("last_name")] if x
            )
            or "chat"
        )
        found.append((cid, name))

    if not found:
        print(
            "No encontré chats. Abre tu bot en Telegram, pulsa START o envía /start "
            "y vuelve a ejecutar este workflow."
        )
        return 1

    print("Chats encontrados:")
    seen = set()
    for cid, name in reversed(found):
        if cid in seen:
            continue
        seen.add(cid)
        print(f"TELEGRAM_CHAT_ID={cid} | {name}")

    print(
        "Copia únicamente el número TELEGRAM_CHAT_ID y guárdalo como GitHub Actions secret. "
        "No copies el token en el chat."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
