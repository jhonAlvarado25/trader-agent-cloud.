from __future__ import annotations

import os
import sys
import time
import requests

API = "https://api.telegram.org"


def _call(method: str, token: str, *, params=None, payload=None, timeout=20):
    url = f"{API}/bot{token}/{method}"
    if payload is not None:
        r = requests.post(url, json=payload, timeout=timeout)
    else:
        r = requests.get(url, params=params, timeout=timeout)

    if r.status_code != 200:
        raise RuntimeError(f"Telegram {method} HTTP {r.status_code}: {r.text[:300]}")

    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method}: {data}")
    return data


def main() -> int:
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

    if not token:
        print("ERROR: falta el secret TELEGRAM_BOT_TOKEN.")
        return 2

    # 1) Validar el token antes de intentar descubrir el chat.
    try:
        me = _call("getMe", token)
        bot = me.get("result", {})
        username = bot.get("username") or "bot"
        print(f"[OK] Token válido. Bot detectado: @{username}")
    except Exception as exc:
        print(f"ERROR: el token no pudo validarse: {exc}")
        return 3

    # 2) Si ya existe CHAT_ID, comprobar la integración completa.
    if chat_id:
        try:
            _call(
                "sendMessage",
                token,
                payload={
                    "chat_id": chat_id,
                    "text": (
                        "Trader Agent V4.1 conectado correctamente con Telegram. "
                        "Las próximas alertas llegarán cuando aparezca una operación candidata."
                    ),
                },
            )
            print("[OK] TELEGRAM_CHAT_ID configurado y mensaje de prueba enviado.")
            return 0
        except Exception as exc:
            print(f"ERROR: TELEGRAM_CHAT_ID existe pero no pude enviar el mensaje: {exc}")
            return 4

    # 3) Sin CHAT_ID: buscar mensajes recientes enviados al bot.
    # Hacemos varios intentos cortos para evitar que el usuario tenga que acertar
    # exactamente con el momento del workflow.
    found = []
    for attempt in range(3):
        try:
            data = _call(
                "getUpdates",
                token,
                params={"limit": 50, "timeout": 2},
                timeout=10,
            )
        except Exception as exc:
            print(f"ERROR consultando getUpdates: {exc}")
            return 5

        for update in data.get("result", []):
            msg = (
                update.get("message")
                or update.get("edited_message")
                or update.get("channel_post")
                or {}
            )
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
            found.append((str(cid), name))

        if found:
            break
        if attempt < 2:
            time.sleep(2)

    if not found:
        print("")
        print("[PENDIENTE] El token está correcto, pero todavía no hay un chat iniciado.")
        print(f"1. Abre Telegram y busca @{username}.")
        print("2. Pulsa START o envía exactamente /start.")
        print("3. Envía además un mensaje normal, por ejemplo: hola.")
        print("4. Vuelve a ejecutar Actions > Telegram Setup Test > Run workflow.")
        print("")
        print("Este estado no es un fallo del código ni del token.")
        # No marcar el workflow como failed: todavía falta una acción del usuario.
        return 0

    print("")
    print("[OK] Chats encontrados:")
    seen = set()
    for cid, name in reversed(found):
        if cid in seen:
            continue
        seen.add(cid)
        print(f"TELEGRAM_CHAT_ID={cid} | {name}")

    print("")
    print("SIGUIENTE PASO:")
    print("Copia únicamente el número TELEGRAM_CHAT_ID.")
    print("Guárdalo en GitHub > Settings > Secrets and variables > Actions")
    print("con el nombre exacto TELEGRAM_CHAT_ID.")
    print("Después ejecuta nuevamente Telegram Setup Test para recibir el mensaje de prueba.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
