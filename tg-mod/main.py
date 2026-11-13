#!/usr/bin/env python3
"""TG-мод (юзербот на Telethon): запуск и загрузка модулей.

Вся функциональность лежит в папке modules/: каждый файл — отдельный модуль,
который подключается автоматически. Чтобы добавить свою функцию, создай новый
файл в modules/ (см. modules/_example.py).

Запуск: python main.py  (при первом запуске спросит api_id / api_hash и номер)
"""
import asyncio
import copy
import importlib
import json
import logging
import sys

import core
from core import BASE, DATA_DIR, MEDIA_DIR, ctx, log
from storage import Store

CONFIG_PATH = BASE / "config.json"


def load_modules(disabled=()):
    """Импортирует все modules/*.py, кроме начинающихся с «_» и отключённых."""
    loaded = []
    for path in sorted((BASE / "modules").glob("*.py")):
        name = path.stem
        if name.startswith("_") or name in disabled:
            continue
        try:
            importlib.import_module(f"modules.{name}")
        except Exception:  # noqa: BLE001 — сломанный модуль не должен ронять весь мод
            log.exception("Модуль %s не загрузился и будет пропущен", name)
            continue
        loaded.append(name)
    core.LOADED_MODULES[:] = loaded
    return loaded


def load_config():
    raw = {}
    if CONFIG_PATH.exists():
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    load_modules(raw.get("disabled_modules", []))
    cfg = copy.deepcopy(core.DEFAULTS)  # общие настройки + настройки всех загруженных модулей
    cfg.update(raw)
    if not cfg["api_id"] or not cfg["api_hash"]:
        print("Нужны api_id и api_hash. Получить: https://my.telegram.org → API development tools")
        cfg["api_id"] = int(input("api_id: ").strip())
        cfg["api_hash"] = input("api_hash: ").strip()
    ctx.cfg = cfg
    ctx.config_path = CONFIG_PATH
    core.save_config()  # дописывает в файл новые настройки, если появились
    return cfg


def attach_handlers(client):
    from telethon import events

    client.add_event_handler(core.dispatch_command, events.NewMessage(outgoing=True))
    for _module, builder, func in core.HANDLERS:
        client.add_event_handler(func, builder)


async def run(client):
    from telethon import utils

    await client.start()
    me = await client.get_me()
    ctx.me_id = me.id
    ctx.log_chat = await client.get_entity(ctx.cfg["log_chat"])
    ctx.log_chat_id = utils.get_peer_id(ctx.log_chat)
    for func in core.STARTUP:
        asyncio.create_task(func())
    log.info("TG-мод запущен как %s (%s). Модули: %s. Напиши %shelp в любом чате.",
             utils.get_display_name(me), me.id, ", ".join(core.LOADED_MODULES), ctx.cfg["prefix"])
    await client.run_until_disconnected()


def main():
    from telethon import TelegramClient

    DATA_DIR.mkdir(exist_ok=True)
    MEDIA_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(DATA_DIR / "mod.log", encoding="utf-8"),
        ],
    )
    logging.getLogger("telethon").setLevel(logging.WARNING)

    cfg = load_config()
    ctx.store = Store(str(DATA_DIR / "cache.db"))
    client = TelegramClient(str(DATA_DIR / "session"), cfg["api_id"], cfg["api_hash"])
    client.parse_mode = "html"
    ctx.client = client
    attach_handlers(client)
    try:
        client.loop.run_until_complete(run(client))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
