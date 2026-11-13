"""Ядро мода: общий контекст, реестр команд и обработчиков, общие утилиты.

Модули из папки modules/ импортируют отсюда всё, что им нужно:

    from core import ctx, command, handler, on_startup, config_defaults, esc, ...
"""
import copy
import html
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from telethon import utils
from telethon.tl import types

log = logging.getLogger("tg-mod")

BASE = Path(__file__).resolve().parent
DATA_DIR = BASE / "data"
MEDIA_DIR = DATA_DIR / "media"

# Общие настройки. Модули добавляют свои через config_defaults(...).
DEFAULTS = {
    "api_id": 0,
    "api_hash": "",
    # Префикс команд: .id, .info ...
    "prefix": ".",
    # Куда слать уведомления: "me" = Избранное, либо @username / ID своего канала/группы
    "log_chat": "me",
    # Белый список (ID чатов): если не пуст, мод следит ТОЛЬКО за этими чатами
    "whitelist": [],
    # Чёрный список (ID чатов): эти чаты мод полностью игнорирует
    "blacklist": [],
    # Модули, которые не нужно загружать (имена файлов без .py)
    "disabled_modules": [],
}


class Context:
    """Всё, что общее для модулей: настройки, база, клиент Telegram."""

    def __init__(self):
        self.cfg = copy.deepcopy(DEFAULTS)
        self.config_path = None
        self.store = None
        self.client = None
        self.me_id = None
        self.log_chat = None
        self.log_chat_id = None


ctx = Context()


# ---------------------------------------------------------------- реестр ----
@dataclass
class Command:
    name: str
    func: object
    help: str = ""
    usage: str = ""
    module: str = ""
    aliases: tuple = field(default_factory=tuple)


COMMANDS = {}      # имя или алиас -> Command
HANDLERS = []      # (модуль, событие Telethon, функция)
STARTUP = []       # функции, запускаемые после входа в аккаунт
LOADED_MODULES = []


def _module_name(func):
    return func.__module__.rsplit(".", 1)[-1]


def command(name, *aliases, help="", usage=""):
    """Регистрирует команду: @command("id", help="...") async def f(event, arg)."""

    def deco(func):
        cmd = Command(name, func, help, usage, _module_name(func), aliases)
        for n in (name, *aliases):
            other = COMMANDS.get(n)
            if other and other.module != cmd.module:
                raise ValueError(f"Команда {n} уже занята модулем {other.module}")
            COMMANDS[n] = cmd
        return func

    return deco


def handler(event_builder):
    """Регистрирует обработчик событий Telegram: @handler(events.NewMessage())."""

    def deco(func):
        HANDLERS.append((_module_name(func), event_builder, func))
        return func

    return deco


def on_startup(func):
    """Функция запустится фоном после входа в аккаунт (например, периодическая задача)."""
    STARTUP.append(func)
    return func


def config_defaults(**values):
    """Модуль объявляет свои настройки и их значения по умолчанию."""
    for key, value in values.items():
        DEFAULTS.setdefault(key, value)
        ctx.cfg.setdefault(key, copy.deepcopy(value))


def save_config():
    if ctx.config_path:
        Path(ctx.config_path).write_text(
            json.dumps(ctx.cfg, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def module_title(name):
    mod = sys.modules.get(f"modules.{name}")
    doc = (mod.__doc__ or "").strip() if mod else ""
    return doc.splitlines()[0] if doc else name


# ---------------------------------------------------------------- команды ----
def parse_command(text):
    """'.search кот' -> (Command, 'кот'), либо None, если это не команда мода."""
    prefix = ctx.cfg["prefix"]
    if not text or not text.startswith(prefix):
        return None
    name, _, arg = text[len(prefix):].partition(" ")
    cmd = COMMANDS.get(name.lower())
    return (cmd, arg.strip()) if cmd else None


async def dispatch_command(event):
    parsed = parse_command(event.message.raw_text)
    if not parsed:
        return
    cmd, arg = parsed
    try:
        await cmd.func(event, arg)
    except Exception as e:  # noqa: BLE001
        log.exception("Ошибка в команде %s", cmd.name)
        await event.edit(f"⚠️ Ошибка: <code>{esc(str(e))}</code>")


def is_tracked(chat_id):
    """Следит ли мод за этим чатом (с учётом белого и чёрного списков)."""
    if chat_id is None or chat_id == ctx.log_chat_id:
        return False
    if chat_id in ctx.cfg["blacklist"]:
        return False
    return not ctx.cfg["whitelist"] or chat_id in ctx.cfg["whitelist"]


# --------------------------------------------------------------- утилиты ----
def esc(text):
    return html.escape(text or "")


def clip(text, limit):
    return text if len(text) <= limit else text[: limit - 1] + "…"


def fmt_time(ts):
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M:%S") if ts else "?"


def parse_entity_arg(arg):
    """'123' / '-100123' -> int, '@user' -> str (для client.get_entity)."""
    return int(arg) if re.fullmatch(r"-?\d+", arg) else arg


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
    if not row["is_channel"]:
        return None
    real_id, _ = utils.resolve_id(row["chat_id"])
    topic = row["topic_id"]
    if topic and topic != 1:
        return f"https://t.me/c/{real_id}/{topic}/{row['msg_id']}"
    return f"https://t.me/c/{real_id}/{row['msg_id']}"


def topic_label(row):
    if not row["topic_id"]:
        return None
    return row["topic_title"] or f"тема #{row['topic_id']}"


def header(row, title):
    """Шапка уведомления: автор, чат, тема, время, тип медиа."""
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
        topic = topic_label(row)
        if topic:
            lines.append(f"🧵 Тема: {esc(topic)} · <code>{row['topic_id']}</code>")
    lines.append(f"🕒 {fmt_time(row['date'])}")
    if row["media_kind"]:
        extra = f" (таймер {row['ttl']} с)" if row["ttl"] else ""
        lines.append(f"📎 {row['media_kind']}{extra}")
    return "\n".join(lines)


async def send_log(text, file=None, kind=None):
    """Отправляет текст (и файл) в лог-чат, учитывая лимиты Telegram."""
    target = ctx.log_chat
    if file and os.path.isfile(file):
        opts = {"voice_note": kind == "голосовое", "video_note": kind == "кружок"}
        if len(text) <= 1024 and kind != "кружок":
            await ctx.client.send_file(target, file, caption=text, **opts)
            return
        await ctx.client.send_message(target, clip(text, 4096), link_preview=False)
        await ctx.client.send_file(target, file, **opts)
        return
    await ctx.client.send_message(target, clip(text, 4096), link_preview=False)


async def send_chunks(head, lines):
    """Отправляет список строк в лог-чат, разбивая на сообщения до 4096 символов."""
    chunk = head
    for line in lines:
        if len(chunk) + len(line) + 1 > 4000:
            await ctx.client.send_message(ctx.log_chat, chunk, link_preview=False)
            chunk = ""
        chunk += "\n" + clip(line, 3900)
    if chunk.strip():
        await ctx.client.send_message(ctx.log_chat, chunk, link_preview=False)
