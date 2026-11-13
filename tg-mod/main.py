#!/usr/bin/env python3
"""TG-мод (юзербот на Telethon).

* сохраняет удалённые сообщения (текст + фото/видео/голосовые/кружки/файлы)
  и присылает их тебе в «Избранное»;
* показывает, как было до редактирования;
* сохраняет самоуничтожающиеся (view-once / с таймером) фото и видео;
* команды .id, .info, .deleted, .history, .stats, .help.

Запуск: python main.py  (при первом запуске спросит api_id / api_hash и номер)
"""
import asyncio
import html
import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from telethon import TelegramClient, events, utils
from telethon.tl import types
from telethon.tl.functions.users import GetFullUserRequest

from storage import Store

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
DATA_DIR = BASE / "data"
MEDIA_DIR = DATA_DIR / "media"

DEFAULTS = {
    "api_id": 0,
    "api_hash": "",
    # Префикс команд: .id, .info ...
    "prefix": ".",
    # Куда слать уведомления: "me" = Избранное, либо @username / ID своего канала/группы
    "log_chat": "me",
    # В каких чатах сообщать об удалении/изменении: private, groups, channels
    "notify_deleted_in": ["private", "groups"],
    "notify_edited_in": ["private"],
    # Скачивать медиа заранее (после удаления скачать уже нельзя)
    "save_media": True,
    "max_media_mb": 20,
    # Сохранять самоуничтожающиеся фото/видео и сразу присылать копию
    "save_self_destructing": True,
    # Не кэшировать сообщения ботов (они часто удаляют свои сообщения)
    "ignore_bots": True,
    # Сколько дней хранить обычные (не удалённые) сообщения в кэше
    "keep_days": 7,
}

log = logging.getLogger("tg-mod")


# ---------------------------------------------------------------- конфиг ----
def load_config():
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    if not cfg["api_id"] or not cfg["api_hash"]:
        print("Нужны api_id и api_hash. Получить: https://my.telegram.org → API development tools")
        cfg["api_id"] = int(input("api_id: ").strip())
        cfg["api_hash"] = input("api_hash: ").strip()
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Сохранено в {CONFIG_PATH.name}. Там же можно поменять остальные настройки.")
    return cfg


cfg = load_config()
DATA_DIR.mkdir(exist_ok=True)
MEDIA_DIR.mkdir(exist_ok=True)
store = Store(str(DATA_DIR / "cache.db"))
client = TelegramClient(str(DATA_DIR / "session"), cfg["api_id"], cfg["api_hash"])
client.parse_mode = "html"
state = {"me_id": None, "log_chat": None, "log_chat_id": None}


# --------------------------------------------------------------- утилиты ----
def esc(text):
    return html.escape(text or "")


def fmt_time(ts):
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M:%S") if ts else "?"


def chat_kind(chat):
    if isinstance(chat, types.User):
        return "private"
    if isinstance(chat, types.Channel):
        return "channels" if chat.broadcast else "groups"
    return "groups"


def media_kind(msg):
    checks = (
        ("sticker", "стикер"), ("gif", "GIF"), ("video_note", "кружок"),
        ("voice", "голосовое"), ("audio", "аудио"), ("video", "видео"),
        ("photo", "фото"), ("document", "файл"), ("geo", "геопозиция"),
        ("contact", "контакт"), ("poll", "опрос"),
    )
    for attr, name in checks:
        if getattr(msg, attr, None):
            return name
    return "медиа" if msg.media else None


def user_link(user_id, name):
    name = esc(name or "Без имени")
    if user_id and user_id > 0:
        return f'<a href="tg://user?id={user_id}">{name}</a>'
    return name


def msg_link(row):
    if row["is_channel"]:
        real_id, _ = utils.resolve_id(row["chat_id"])
        return f"https://t.me/c/{real_id}/{row['msg_id']}"
    return None


def header(row, title):
    lines = [f"<b>{title}</b>"]
    sender = f"👤 {user_link(row['sender_id'], row['sender_name'])}"
    if row["sender_id"]:
        sender += f" · <code>{row['sender_id']}</code>"
    lines.append(sender)
    if row["kind"] != "private":
        chat = esc(row["chat_title"])
        link = msg_link(row)
        if link:
            chat = f'<a href="{link}">{chat}</a>'
        lines.append(f"💬 {chat} · <code>{row['chat_id']}</code>")
    lines.append(f"🕒 {fmt_time(row['date'])}")
    if row["media_kind"]:
        extra = f" (таймер {row['ttl']} с)" if row["ttl"] else ""
        lines.append(f"📎 {row['media_kind']}{extra}")
    return "\n".join(lines)


def clip(text, limit):
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def send_log(text, file=None, kind=None):
    """Отправляет текст (и файл) в лог-чат, учитывая лимиты Telegram."""
    target = state["log_chat"]
    if file and os.path.isfile(file):
        opts = {"voice_note": kind == "голосовое", "video_note": kind == "кружок"}
        if len(text) <= 1024 and kind != "кружок":
            await client.send_file(target, file, caption=text, **opts)
            return
        await client.send_message(target, clip(text, 4096), link_preview=False)
        await client.send_file(target, file, **opts)
        return
    await client.send_message(target, clip(text, 4096), link_preview=False)


def can_download(msg):
    return bool(msg.photo or msg.document) and not isinstance(msg.media, types.MessageMediaWebPage)


async def download(msg, chat_id, notify_kind=None):
    try:
        path = await msg.download_media(file=str(MEDIA_DIR / f"{chat_id}_{msg.id}"))
    except Exception as e:  # noqa: BLE001 — медиа может быть недоступно
        log.warning("Не удалось скачать медиа %s/%s: %s", chat_id, msg.id, e)
        return
    if not path:
        return
    store.set_media_path(chat_id, msg.id, path)
    row = store.get(chat_id, msg.id)
    if notify_kind is not None and row is not None:
        await send_log(header(row, "⏳ Самоуничтожающееся медиа"), path, notify_kind)


# ------------------------------------------------------- кэш сообщений ----
@client.on(events.NewMessage())
async def on_new_message(event):
    msg = event.message
    if msg.out and msg.raw_text and msg.raw_text.startswith(cfg["prefix"]):
        if await handle_command(event):
            return
    if event.chat_id == state["log_chat_id"]:
        return

    chat = await event.get_chat()
    sender = await event.get_sender()
    if cfg["ignore_bots"] and isinstance(sender, types.User) and sender.bot:
        return

    ttl = getattr(msg.media, "ttl_seconds", None)
    kind = media_kind(msg)
    store.save(
        chat_id=event.chat_id,
        msg_id=msg.id,
        kind=chat_kind(chat),
        is_channel=int(isinstance(msg.peer_id, types.PeerChannel)),
        chat_title=utils.get_display_name(chat) if chat else "",
        sender_id=msg.sender_id,
        sender_name=utils.get_display_name(sender) if sender else "",
        out=int(msg.out),
        text=msg.raw_text,
        date=int(msg.date.timestamp()),
        media_kind=kind,
        ttl=ttl,
    )

    if not can_download(msg):
        return
    if ttl and cfg["save_self_destructing"] and not msg.out:
        asyncio.create_task(download(msg, event.chat_id, notify_kind=kind))
    elif cfg["save_media"]:
        size = msg.file.size if msg.file else None
        if size is None or size <= cfg["max_media_mb"] * 1024 * 1024:
            asyncio.create_task(download(msg, event.chat_id))


# ---------------------------------------------------------- удаления ----
@client.on(events.MessageDeleted())
async def on_deleted(event):
    rows = store.mark_deleted(event.chat_id, event.deleted_ids)
    rows = [r for r in rows if not r["out"] and r["kind"] in cfg["notify_deleted_in"]]
    for row in rows:
        text = header(row, "🗑 Удалено сообщение")
        if row["text"]:
            text += f"\n\n{esc(row['text'])}"
        try:
            await send_log(text, row["media_path"], row["media_kind"])
        except Exception as e:  # noqa: BLE001
            log.warning("Не удалось отправить отчёт об удалении: %s", e)


# ------------------------------------------------------ редактирования ----
@client.on(events.MessageEdited())
async def on_edited(event):
    if event.chat_id == state["log_chat_id"]:
        return
    msg = event.message
    old = store.record_edit(event.chat_id, msg.id, msg.raw_text)
    if old is None or old["out"] or old["kind"] not in cfg["notify_edited_in"]:
        return
    text = (
        header(old, "✏️ Сообщение изменено")
        + f"\n\n<b>Было:</b>\n{esc(old['text']) or '<i>пусто</i>'}"
        + f"\n\n<b>Стало:</b>\n{esc(msg.raw_text) or '<i>пусто</i>'}"
    )
    try:
        await send_log(text)
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось отправить отчёт об изменении: %s", e)


# ------------------------------------------------------------ команды ----
async def resolve_target(event, arg):
    if arg:
        return await client.get_entity(int(arg) if re.fullmatch(r"-?\d+", arg) else arg)
    reply = await event.get_reply_message()
    if reply:
        sender = await reply.get_sender()
        if sender:
            return sender
    return await event.get_chat()


async def cmd_id(event, arg):
    chat = await event.get_chat()
    lines = ["<b>🆔 ID</b>"]
    if arg:
        ent = await client.get_entity(int(arg) if re.fullmatch(r"-?\d+", arg) else arg)
        lines.append(f"🔎 {esc(utils.get_display_name(ent))} · <code>{utils.get_peer_id(ent)}</code>")
    lines.append(f"💬 Чат: {esc(utils.get_display_name(chat))} · <code>{event.chat_id}</code>")
    lines.append(f"🙋 Ты: <code>{state['me_id']}</code>")
    reply = await event.get_reply_message()
    if reply:
        sender = await reply.get_sender()
        name = utils.get_display_name(sender) if sender else "?"
        lines.append(f"✉️ Сообщение: <code>{reply.id}</code>")
        lines.append(f"👤 Автор: {esc(name)} · <code>{reply.sender_id}</code>")
        fwd = reply.fwd_from
        if fwd:
            if fwd.from_id:
                lines.append(f"↪️ Переслано от: <code>{utils.get_peer_id(fwd.from_id)}</code>")
            elif fwd.from_name:
                lines.append(f"↪️ Переслано от: {esc(fwd.from_name)} (ID скрыт)")
            if fwd.channel_post:
                lines.append(f"↪️ ID поста в канале: <code>{fwd.channel_post}</code>")
        if reply.file:
            size = f"{reply.file.size / 1024 / 1024:.2f} МБ" if reply.file.size else "?"
            lines.append(f"📎 Файл: {esc(reply.file.name or media_kind(reply) or '')} · {size}")
    await event.edit("\n".join(lines))


def fmt_status(status):
    if isinstance(status, types.UserStatusOnline):
        return "в сети"
    if isinstance(status, types.UserStatusOffline):
        return f"был(а) {status.was_online.astimezone().strftime('%d.%m.%Y %H:%M')}"
    return {
        types.UserStatusRecently: "был(а) недавно",
        types.UserStatusLastWeek: "был(а) на этой неделе",
        types.UserStatusLastMonth: "был(а) в этом месяце",
    }.get(type(status), "давно / скрыто")


async def cmd_info(event, arg):
    ent = await resolve_target(event, arg)
    if not isinstance(ent, types.User):
        kind = "канал" if getattr(ent, "broadcast", False) else "группа"
        lines = [
            f"<b>ℹ️ {esc(utils.get_display_name(ent))}</b>",
            f"Тип: {kind}",
            f"ID: <code>{utils.get_peer_id(ent)}</code>",
        ]
        if getattr(ent, "username", None):
            lines.append(f"Юзернейм: @{ent.username}")
        if getattr(ent, "participants_count", None):
            lines.append(f"Участников: {ent.participants_count}")
        await event.edit("\n".join(lines))
        return

    full = await client(GetFullUserRequest(ent))
    u, fu = full.users[0], full.full_user
    usernames = [f"@{u.username}"] if u.username else []
    usernames += [f"@{x.username}" for x in (u.usernames or []) if x.username != u.username]
    flags = [n for n, v in (("бот", u.bot), ("Premium", u.premium), ("верифицирован", u.verified),
                            ("SCAM", u.scam), ("FAKE", u.fake), ("удалён", u.deleted),
                            ("в контактах", u.contact)) if v]
    lines = [
        f"<b>ℹ️ {user_link(u.id, utils.get_display_name(u))}</b>",
        f"ID: <code>{u.id}</code>",
    ]
    if usernames:
        lines.append("Юзернейм: " + ", ".join(usernames))
    if u.phone:
        lines.append(f"Телефон: <code>+{u.phone}</code>")
    if u.photo and hasattr(u.photo, "dc_id"):
        lines.append(f"DC: {u.photo.dc_id}")
    lines.append(f"Статус: {fmt_status(u.status)}")
    if fu.about:
        lines.append(f"О себе: {esc(fu.about)}")
    lines.append(f"Общих чатов: {fu.common_chats_count}")
    if flags:
        lines.append("Метки: " + ", ".join(flags))
    await event.edit("\n".join(lines))


async def cmd_deleted(event, arg):
    limit = max(1, min(int(arg), 50)) if arg and arg.isdigit() else 10
    rows = store.recent_deleted(event.chat_id, limit)
    if not rows:
        await event.edit("🗑 В этом чате удалённых сообщений не найдено.")
        return
    parts = [f"<b>🗑 Удалённые сообщения ({len(rows)})</b>"]
    for r in rows:
        body = esc(clip(r["text"] or "", 300)) or f"<i>[{r['media_kind'] or 'пусто'}]</i>"
        parts.append(f"\n<b>{esc(r['sender_name'])}</b> · {fmt_time(r['date'])}\n{body}")
    await event.edit(clip("\n".join(parts), 4096))


async def cmd_history(event, arg):
    reply = await event.get_reply_message()
    if not reply:
        await event.edit("Ответь этой командой на сообщение.")
        return
    edits = store.edits_for(event.chat_id, reply.id)
    if not edits:
        await event.edit("✏️ Это сообщение не редактировалось (или не было в кэше).")
        return
    parts = [f"<b>✏️ История изменений ({len(edits)})</b>"]
    for i, e in enumerate(edits, 1):
        parts.append(f"\n<b>{i}.</b> до {fmt_time(e['edited_at'])}:\n{esc(clip(e['old_text'] or '', 500))}")
    await event.edit(clip("\n".join(parts), 4096))


async def cmd_stats(event, arg):
    s = store.stats()
    await event.edit(
        f"<b>📊 TG-мод</b>\nВ кэше: {s['messages']}\nУдалённых: {s['deleted']}\nИзменений: {s['edits']}"
    )


async def cmd_help(event, arg):
    p = esc(cfg["prefix"])
    await event.edit(
        "<b>🛠 TG-мод — команды</b>\n"
        f"<code>{p}id</code> — ID чата, твой; в ответ на сообщение — ID автора, сообщения, пересылки\n"
        f"<code>{p}id @user</code> — ID пользователя/канала\n"
        f"<code>{p}info</code> [@user / ответ] — подробная инфа о пользователе\n"
        f"<code>{p}deleted</code> [N] — последние удалённые сообщения в этом чате\n"
        f"<code>{p}history</code> (ответ) — история правок сообщения\n"
        f"<code>{p}stats</code> — статистика кэша\n\n"
        "Удалённые и изменённые сообщения автоматически приходят в Избранное."
    )


COMMANDS = {
    "id": cmd_id, "info": cmd_info, "deleted": cmd_deleted,
    "history": cmd_history, "stats": cmd_stats, "help": cmd_help, "modhelp": cmd_help,
}


async def handle_command(event):
    body = event.message.raw_text[len(cfg["prefix"]):]
    name, _, arg = body.partition(" ")
    handler = COMMANDS.get(name.lower())
    if handler is None:
        return False
    try:
        await handler(event, arg.strip())
    except Exception as e:  # noqa: BLE001
        await event.edit(f"⚠️ Ошибка: <code>{esc(str(e))}</code>")
    return True


# -------------------------------------------------------------- запуск ----
async def cleanup_loop():
    while True:
        removed = store.cleanup(cfg["keep_days"])
        if removed:
            log.info("Очищено старых сообщений из кэша: %s", removed)
        await asyncio.sleep(6 * 3600)


async def main():
    await client.start()
    me = await client.get_me()
    state["me_id"] = me.id
    log_entity = await client.get_entity(cfg["log_chat"])
    state["log_chat"] = log_entity
    state["log_chat_id"] = utils.get_peer_id(log_entity)
    asyncio.create_task(cleanup_loop())
    log.info("TG-мод запущен как %s (%s). Напиши %shelp в любом чате.",
             utils.get_display_name(me), me.id, cfg["prefix"])
    await client.run_until_disconnected()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("telethon").setLevel(logging.WARNING)
    try:
        client.loop.run_until_complete(main())
    except KeyboardInterrupt:
        sys.exit(0)
