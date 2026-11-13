import unittest

from telethon.tl import types

import core
from core import ctx
from tests.helpers import (
    BOT, USER, USER2, FakeEvent, FakeMsg, channel, channel_chat_id, delete, feed, setup_ctx,
)


def group_msg(msg_id, text, sender=USER, cid=777, reply_to=None):
    return FakeMsg(msg_id, text, sender=sender, peer=types.PeerChannel(cid), reply_to=reply_to)


class LoaderTest(unittest.TestCase):
    def test_all_modules_and_commands_registered(self):
        for m in ("cache", "deleted", "edits", "ids", "lists", "search", "system"):
            self.assertIn(m, core.LOADED_MODULES)
        self.assertNotIn("_example", core.LOADED_MODULES, "файлы с _ не загружаются")
        for c in ("id", "info", "deleted", "history", "search", "s", "ignore", "unignore",
                  "watch", "unwatch", "lists", "help", "stats", "modules"):
            self.assertIn(c, core.COMMANDS)

    def test_duplicate_command_from_other_module_is_rejected(self):
        with self.assertRaises(ValueError):
            async def fake(event, arg): ...
            fake.__module__ = "modules.other"
            core.command("id")(fake)


class DeletedTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = setup_ctx()

    async def test_single_deleted_message_reported(self):
        await feed(FakeEvent(FakeMsg(5, "привет & пока")))
        await delete([5])
        self.assertEqual(len(self.client.sent), 1)
        text = self.client.texts[0]
        self.assertIn("Удалено сообщение", text)
        self.assertIn("привет &amp; пока", text)
        self.assertIn("Вася &lt;b&gt;", text, "имя экранировано")

    async def test_own_deleted_messages_not_reported(self):
        await feed(FakeEvent(FakeMsg(6, "моё", out=True)))
        await delete([6])
        self.assertEqual(self.client.sent, [])

    async def test_bots_ignored(self):
        await feed(FakeEvent(FakeMsg(7, "капча", sender=BOT), chat=BOT))
        await delete([7])
        self.assertEqual(self.client.sent, [])

    async def test_mass_delete_sends_one_summary(self):
        chat = channel()
        for i in range(1, 9):
            await feed(FakeEvent(group_msg(i, f"спам {i}", sender=USER if i % 2 else USER2), chat=chat))
        await delete(list(range(1, 9)), chat_id=channel_chat_id())
        self.assertEqual(len(self.client.sent), 1)
        summary = self.client.texts[0]
        self.assertIn("Удалено сообщений: 8", summary)
        self.assertIn("Авторов: 2", summary)
        self.assertIn("спам 8", summary)

    async def test_few_deletes_sent_one_by_one(self):
        chat = channel()
        for i in range(1, 4):
            await feed(FakeEvent(group_msg(i, f"сообщение {i}"), chat=chat))
        await delete([1, 2, 3], chat_id=channel_chat_id())
        self.assertEqual(len(self.client.sent), 3)

    async def test_summary_can_be_disabled(self):
        ctx.cfg["batch_threshold"] = 0
        chat = channel()
        for i in range(1, 9):
            await feed(FakeEvent(group_msg(i, "x"), chat=chat))
        await delete(list(range(1, 9)), chat_id=channel_chat_id())
        self.assertEqual(len(self.client.sent), 8)

    async def test_long_summary_is_split(self):
        chat = channel()
        for i in range(1, 61):
            await feed(FakeEvent(group_msg(i, "длинный текст " * 20), chat=chat))
        await delete(list(range(1, 61)), chat_id=channel_chat_id())
        self.assertGreater(len(self.client.sent), 1)
        self.assertTrue(all(len(t) <= 4096 for t in self.client.texts))

    async def test_deleted_command(self):
        await feed(FakeEvent(FakeMsg(5, "удалю")))
        await delete([5])
        ev = FakeEvent(FakeMsg(50, ".deleted", out=True))
        await feed(ev)
        self.assertIn("удалю", ev.edited)


class ForumTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = setup_ctx()
        self.client.topics[55] = "Флуд"
        self.chat = channel(forum=True)

    async def test_topic_shown_in_report_with_link(self):
        reply_to = types.MessageReplyHeader(forum_topic=True, reply_to_msg_id=55)
        await feed(FakeEvent(group_msg(100, "в теме", reply_to=reply_to), chat=self.chat))
        await delete([100], chat_id=channel_chat_id())
        text = self.client.texts[0]
        self.assertIn("Тема: Флуд", text)
        self.assertIn("https://t.me/c/777/55/100", text)

    async def test_reply_inside_topic_uses_top_id(self):
        reply_to = types.MessageReplyHeader(forum_topic=True, reply_to_msg_id=120, reply_to_top_id=55)
        await feed(FakeEvent(group_msg(130, "ответ", reply_to=reply_to), chat=self.chat))
        self.assertEqual(ctx.store.get(channel_chat_id(), 130)["topic_id"], 55)

    async def test_general_topic(self):
        await feed(FakeEvent(group_msg(101, "общий"), chat=self.chat))
        self.assertEqual(ctx.store.get(channel_chat_id(), 101)["topic_title"], "General")

    async def test_not_a_forum(self):
        await feed(FakeEvent(group_msg(102, "обычная группа"), chat=channel()))
        self.assertIsNone(ctx.store.get(channel_chat_id(), 102)["topic_id"])


class ListsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = setup_ctx()

    async def test_blacklisted_chat_not_tracked(self):
        ctx.cfg["blacklist"] = [USER.id]
        await feed(FakeEvent(FakeMsg(5, "секрет")))
        self.assertIsNone(ctx.store.get(USER.id, 5))
        await delete([5])
        self.assertEqual(self.client.sent, [])

    async def test_whitelist_tracks_only_listed(self):
        ctx.cfg["whitelist"] = [USER2.id]
        await feed(FakeEvent(FakeMsg(5, "от Васи")))
        await feed(FakeEvent(FakeMsg(6, "от Пети", sender=USER2), chat=USER2))
        self.assertIsNone(ctx.store.get(USER.id, 5))
        self.assertIsNotNone(ctx.store.get(USER2.id, 6))

    async def test_commands_work_in_ignored_chat_and_are_not_cached(self):
        ctx.cfg["blacklist"] = [USER.id]
        ev = FakeEvent(FakeMsg(9, ".stats", out=True))
        await feed(ev)
        self.assertIn("TG-мод", ev.edited)
        self.assertIsNone(ctx.store.get(USER.id, 9))

    async def test_ignore_watch_commands(self):
        await feed(FakeEvent(FakeMsg(1, ".ignore", out=True)))
        self.assertEqual(ctx.cfg["blacklist"], [USER.id])
        await feed(FakeEvent(FakeMsg(2, ".watch", out=True)))
        self.assertEqual(ctx.cfg["blacklist"], [])
        self.assertEqual(ctx.cfg["whitelist"], [USER.id])
        ev = FakeEvent(FakeMsg(3, ".lists", out=True))
        await feed(ev)
        self.assertIn(f"<code>{USER.id}</code>", ev.edited)
        await feed(FakeEvent(FakeMsg(4, ".unwatch", out=True)))
        self.assertEqual(ctx.cfg["whitelist"], [])
        self.assertEqual(core.DEFAULTS["whitelist"], [], "значения по умолчанию не меняются")


class EditsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = setup_ctx()

    async def test_edit_reported_and_history(self):
        from modules import edits

        await feed(FakeEvent(FakeMsg(5, "было")))
        await edits.on_edited(FakeEvent(FakeMsg(5, "стало")))
        self.assertIn("Было:</b>\nбыло", self.client.texts[0])
        self.assertIn("Стало:</b>\nстало", self.client.texts[0])
        ev = FakeEvent(FakeMsg(6, ".history", out=True), reply=FakeMsg(5))
        await feed(ev)
        self.assertIn("История изменений (1)", ev.edited)


class CommandsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = setup_ctx()

    async def test_search(self):
        await feed(FakeEvent(FakeMsg(1, "Купил ёлку на рынке")))
        await feed(FakeEvent(FakeMsg(2, "елка уже стоит")))
        await delete([2])
        ev = FakeEvent(FakeMsg(10, ".search елк", out=True))
        await feed(ev)
        self.assertIn("«елк»: 2", ev.edited)
        self.assertIn("<b>ёлк</b>", ev.edited)
        self.assertIn("🗑", ev.edited)
        ev = FakeEvent(FakeMsg(11, ".s елка -уд", out=True))
        await feed(ev)
        self.assertIn("«елка»: 1", ev.edited)
        ev = FakeEvent(FakeMsg(12, ".search нетакого", out=True))
        await feed(ev)
        self.assertIn("ничего не найдено", ev.edited)

    async def test_id_with_reply_and_forward(self):
        fwd = types.MessageFwdHeader(date=None, from_id=types.PeerChannel(42), channel_post=7)
        ev = FakeEvent(FakeMsg(12, ".id", out=True), reply=FakeMsg(5, "x", fwd_from=fwd))
        await feed(ev)
        self.assertIn(f"Ты: <code>{ctx.me_id}</code>", ev.edited)
        self.assertIn("Сообщение: <code>5</code>", ev.edited)
        self.assertIn("<code>-1000000000042</code>", ev.edited)
        self.assertIn("ID поста в канале: <code>7</code>", ev.edited)

    async def test_help_lists_every_command(self):
        ev = FakeEvent(FakeMsg(1, ".help", out=True))
        await feed(ev)
        for name in ("id", "info", "deleted", "history", "search", "ignore", "watch", "lists", "modules"):
            self.assertIn(f"<code>.{name}</code>", ev.edited)

    async def test_custom_prefix(self):
        ctx.cfg["prefix"] = "!"
        ev = FakeEvent(FakeMsg(1, "!stats", out=True))
        await feed(ev)
        self.assertIn("TG-мод", ev.edited)

    async def test_command_error_is_shown_not_raised(self):
        ev = FakeEvent(FakeMsg(1, ".id @nobody", out=True))
        await feed(ev)
        self.assertIn("Ошибка", ev.edited)


if __name__ == "__main__":
    unittest.main()
