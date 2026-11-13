"""Заглушки Telegram для офлайн-тестов: ни сети, ни аккаунта не нужно."""
import asyncio
import copy
import datetime
import logging
import os
import tempfile
from types import SimpleNamespace

from telethon import utils
from telethon.tl import functions, types

import core
import main
from core import ctx
from storage import Store

main.load_modules()  # регистрирует команды и обработчики всех модулей один раз

from modules import cache, deleted  # noqa: E402  (импорт после загрузки модулей)

ME = 999
USER = types.User(id=111, first_name="Вася <b>", last_name="Пупкин")
USER2 = types.User(id=222, first_name="Петя")
BOT = types.User(id=333, first_name="Бот", bot=True)


def channel(cid=777, title="Группа", forum=False):
    return types.Channel(id=cid, title=title, photo=types.ChatPhotoEmpty(), date=None,
                         megagroup=True, forum=forum)


def channel_chat_id(cid=777):
    return utils.get_peer_id(types.PeerChannel(cid))  # -1000000000777


class FakeClient:
    """Записывает всё, что мод «отправил» в Telegram."""

    def __init__(self):
        self.sent = []          # [(тип, текст, файл)]
        self.entities = {}
        self.topics = {}        # topic_id -> название

    async def send_message(self, target, text, **kw):
        self.sent.append(("msg", text, None))

    async def send_file(self, target, file, caption=None, **kw):
        self.sent.append(("file", caption, file))

    async def get_entity(self, key):
        return self.entities[key]

    async def __call__(self, request):
        if isinstance(request, functions.messages.GetForumTopicsByIDRequest):
            return SimpleNamespace(topics=[SimpleNamespace(title=self.topics.get(t))
                                           for t in request.topics if t in self.topics])
        raise NotImplementedError(type(request).__name__)

    @property
    def texts(self):
        return [t for _, t, _ in self.sent]


class FakeMsg:
    def __init__(self, msg_id, text="", out=False, sender=USER, peer=None, reply_to=None,
                 fwd_from=None):
        self.id = msg_id
        self.raw_text = text
        self.out = out
        self.media = None
        self._sender = types.User(id=ME, first_name="Я") if out else sender
        self.sender_id = self._sender.id if self._sender else None
        self.peer_id = peer or types.PeerUser(self.sender_id or ME)
        self.date = datetime.datetime.now(datetime.timezone.utc)
        self.reply_to = reply_to
        self.fwd_from = fwd_from
        self.file = None
        for attr in ("photo", "document", "sticker", "gif", "video_note", "voice",
                     "audio", "video", "geo", "contact", "poll"):
            setattr(self, attr, None)

    async def get_sender(self):
        return self._sender


class FakeEvent:
    def __init__(self, msg, chat=USER, chat_id=None, reply=None):
        self.message = msg
        self._chat = chat
        self.chat_id = chat_id if chat_id is not None else utils.get_peer_id(chat)
        self._reply = reply
        self.edited = None

    async def get_chat(self):
        return self._chat

    async def get_sender(self):
        return await self.message.get_sender()

    async def get_reply_message(self):
        return self._reply

    async def edit(self, text, **kw):
        self.edited = text


class FakeDeleted:
    def __init__(self, ids, chat_id=None):
        self.deleted_ids = ids
        self.chat_id = chat_id


def setup_ctx(**overrides):
    """Свежий контекст: временная база, заглушка клиента, настройки по умолчанию."""
    logging.disable(logging.CRITICAL)  # ошибки, которые тесты вызывают нарочно, не печатаем
    if ctx.store is not None:
        ctx.store.close()
    tmp = tempfile.mkdtemp(prefix="tgmod-test-")
    ctx.cfg = copy.deepcopy(core.DEFAULTS)
    ctx.cfg.update(api_id=1, api_hash="x", batch_delay=0)
    ctx.cfg.update(overrides)
    ctx.config_path = None
    ctx.store = Store(os.path.join(tmp, "cache.db"))
    ctx.client = FakeClient()
    ctx.me_id = ctx.log_chat = ctx.log_chat_id = ME
    cache.topic_cache.clear()
    deleted.pending.clear()
    return ctx.client


async def feed(event):
    """Прогоняет новое сообщение так же, как Telethon: сначала команды, потом кэш."""
    if event.message.out:
        await core.dispatch_command(event)
    await cache.on_new_message(event)


async def delete(ids, chat_id=None):
    await deleted.on_deleted(FakeDeleted(ids, chat_id))
    while deleted.flush_tasks:
        await asyncio.gather(*list(deleted.flush_tasks))
