import os
import sqlite3
import tempfile
import time
import unittest

from storage import Store, fts_query


def row(chat_id, msg_id, text, **kw):
    base = dict(chat_id=chat_id, msg_id=msg_id, kind="private", is_channel=0, chat_title="Чат",
                sender_id=1, sender_name="Вася", out=0, text=text, date=int(time.time()))
    base.update(kw)
    return base


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "cache.db")
        self.store = Store(self.path)
        self.addCleanup(self.store.close)

    def test_delete_in_private_without_chat_id(self):
        self.store.save(**row(111, 5, "привет"))
        rows = self.store.mark_deleted(None, [5, 404])
        self.assertEqual([r["msg_id"] for r in rows], [5])
        self.assertEqual(self.store.mark_deleted(None, [5]), [], "повторно не помечается")

    def test_channel_ids_do_not_clash_with_private(self):
        self.store.save(**row(111, 5, "личка"))
        self.store.save(**row(-100777, 5, "группа", kind="groups", is_channel=1))
        rows = self.store.mark_deleted(-100777, [5])
        self.assertEqual([r["text"] for r in rows], ["группа"])
        self.assertIsNone(self.store.get(111, 5)["deleted_at"])

    def test_edit_history_and_index(self):
        self.store.save(**row(111, 5, "старый текст"))
        old = self.store.record_edit(111, 5, "новый текст")
        self.assertEqual(old["text"], "старый текст")
        self.assertIsNone(self.store.record_edit(111, 5, "новый текст"), "без изменений не пишем")
        self.assertEqual([e["old_text"] for e in self.store.edits_for(111, 5)], ["старый текст"])
        self.assertEqual(self.store.search("старый"), [])
        self.assertEqual(len(self.store.search("новый")), 1)

    def test_search_is_case_and_yo_insensitive_with_prefixes(self):
        self.store.save(**row(111, 1, "Ёлка зелёная"))
        self.store.save(**row(111, 2, "ПРИВЕТСТВУЮ всех"))
        self.store.save(**row(222, 3, "привет мир"))
        self.assertEqual(len(self.store.search("елка")), 1)
        self.assertEqual(len(self.store.search("привет")), 2)
        self.assertEqual(len(self.store.search("привет мир")), 1)
        self.assertEqual(len(self.store.search("привет", chat_id=222)), 1)
        self.store.mark_deleted(None, [2])
        self.assertEqual([r["msg_id"] for r in self.store.search("привет", deleted_only=True)], [2])

    def test_query_is_sanitized(self):
        self.assertEqual(fts_query('a" OR *'), '"a"* AND "or"*')
        self.store.save(**row(111, 1, "текст"))
        self.store.search('"; DROP TABLE messages; --')
        self.assertEqual(self.store.stats()["messages"], 1)

    def test_cleanup_keeps_deleted(self):
        old = int(time.time()) - 30 * 86400
        self.store.save(**row(111, 1, "старое обычное", date=old))
        self.store.save(**row(111, 2, "старое удалённое", date=old))
        self.store.mark_deleted(None, [2])
        self.assertEqual(self.store.cleanup(7), 1)
        self.assertIsNone(self.store.get(111, 1))
        self.assertIsNotNone(self.store.get(111, 2))
        self.assertEqual(self.store.search("обычное"), [])

    def test_migrates_old_database(self):
        path = os.path.join(tempfile.mkdtemp(), "old.db")
        db = sqlite3.connect(path)
        db.execute("""CREATE TABLE messages (chat_id INTEGER, msg_id INTEGER, kind TEXT,
            is_channel INTEGER, chat_title TEXT, sender_id INTEGER, sender_name TEXT, out INTEGER,
            text TEXT, date INTEGER, media_kind TEXT, media_path TEXT, ttl INTEGER,
            deleted_at INTEGER, PRIMARY KEY (chat_id, msg_id))""")
        db.execute("INSERT INTO messages (chat_id, msg_id, text, is_channel, date) "
                   "VALUES (1, 1, 'старая база', 0, 0)")
        db.commit()
        db.close()
        store = Store(path)
        self.addCleanup(store.close)
        self.assertIn("topic_id", store.get(1, 1).keys())
        self.assertEqual(len(store.search("база")), 1, "старые сообщения проиндексированы")


if __name__ == "__main__":
    unittest.main()
