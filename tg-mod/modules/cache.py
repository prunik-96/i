"""Кэш сообщений и медиа

Запоминает входящие сообщения (и заранее скачивает медиа), чтобы остальные
модули могли показать их после удаления или изменения. Сохраняет
самоуничтожающиеся фото и видео.
"""
import asyncio
import time

from telethon import events, utils
from telethon.tl import functions, types

from core import (
    MEDIA_DIR, config_defaults, ctx, handler, header, is_tracked, log, media_kind,
    chat_kind, on_startup, parse_command, send_log,
)

config_defaults(
    # Скачивать медиа заранее (после удаления скачать уже нельзя)
    save_media=True,
    max_media_mb=20,
    # Сохранять самоуничтожающиеся фото/видео и сразу присылать копию
    save_self_destructing=True,
    # Не кэшировать сообщения ботов (они часто удаляют свои сообщения)
    ignore_bots=True,
    # Сколько дней хранить обычные (не удалённые) сообщения в кэше
    keep_days=7,
)

topic_cache = {}  # (chat_id, topic_id) -> (название, время запроса)
TOPIC_CACHE_SECONDS = 3600


async def get_topic(chat, msg):
    """Возвращает (ID темы, название) для сообщения в группе-форуме, иначе (None, None)."""
    if not getattr(chat, "forum", False):
        return None, None
    rt = msg.reply_to
    if isinstance(rt, types.MessageReplyHeader) and rt.forum_topic:
        topic_id = rt.reply_to_top_id or rt.reply_to_msg_id
    else:
        topic_id = 1  # без привязки к теме = General
    if topic_id == 1:
        return 1, "General"
    key = (utils.get_peer_id(chat), topic_id)
    cached = topic_cache.get(key)
    if cached and time.time() - cached[1] < TOPIC_CACHE_SECONDS:
        return topic_id, cached[0]
    title = cached[0] if cached else None
    try:
        res = await ctx.client(
            functions.messages.GetForumTopicsByIDRequest(peer=chat, topics=[topic_id])
        )
        if res.topics:
            title = getattr(res.topics[0], "title", None) or title
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось получить название темы %s: %s", key, e)
    topic_cache[key] = (title, time.time())
    return topic_id, title


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
    ctx.store.set_media_path(chat_id, msg.id, path)
    row = ctx.store.get(chat_id, msg.id)
    if notify_kind is not None and row is not None:
        await send_log(header(row, "⏳ Самоуничтожающееся медиа"), path, notify_kind)


@handler(events.NewMessage())
async def on_new_message(event):
    msg = event.message
    if msg.out and parse_command(msg.raw_text):
        return  # команды мода не кэшируем
    if not is_tracked(event.chat_id):
        return

    chat = await event.get_chat()
    sender = await event.get_sender()
    if ctx.cfg["ignore_bots"] and isinstance(sender, types.User) and sender.bot:
        return

    ttl = getattr(msg.media, "ttl_seconds", None)
    kind = media_kind(msg)
    topic_id, topic_title = await get_topic(chat, msg)
    ctx.store.save(
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
        topic_id=topic_id,
        topic_title=topic_title,
    )

    if not can_download(msg):
        return
    if ttl and ctx.cfg["save_self_destructing"] and not msg.out:
        asyncio.create_task(download(msg, event.chat_id, notify_kind=kind))
    elif ctx.cfg["save_media"]:
        size = msg.file.size if msg.file else None
        if size is None or size <= ctx.cfg["max_media_mb"] * 1024 * 1024:
            asyncio.create_task(download(msg, event.chat_id))


@on_startup
async def cleanup_loop():
    while True:
        removed = ctx.store.cleanup(ctx.cfg["keep_days"])
        if removed:
            log.info("Очищено старых сообщений из кэша: %s", removed)
        await asyncio.sleep(6 * 3600)
