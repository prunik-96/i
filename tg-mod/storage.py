"""SQLite-кэш сообщений: нужен, чтобы показать текст и медиа после удаления."""
import os
import re
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    chat_id     INTEGER NOT NULL,
    msg_id      INTEGER NOT NULL,
    kind        TEXT,               -- private / groups / channels
    is_channel  INTEGER,            -- 1 для супергрупп и каналов (у них свои ID сообщений)
    chat_title  TEXT,
    sender_id   INTEGER,
    sender_name TEXT,
    out         INTEGER,            -- 1, если сообщение отправил ты
    text        TEXT,
    date        INTEGER,
    media_kind  TEXT,
    media_path  TEXT,
    ttl         INTEGER,            -- таймер самоуничтожения (сек)
    deleted_at  INTEGER,
    topic_id    INTEGER,            -- ID темы в группе-форуме
    topic_title TEXT,
    PRIMARY KEY (chat_id, msg_id)
);
CREATE INDEX IF NOT EXISTS idx_messages_msg ON messages (msg_id);
CREATE INDEX IF NOT EXISTS idx_messages_deleted ON messages (chat_id, deleted_at);

CREATE TABLE IF NOT EXISTS edits (
    chat_id   INTEGER NOT NULL,
    msg_id    INTEGER NOT NULL,
    old_text  TEXT,
    new_text  TEXT,
    edited_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_edits_msg ON edits (chat_id, msg_id);
"""

COLUMNS = (
    "chat_id", "msg_id", "kind", "is_channel", "chat_title", "sender_id",
    "sender_name", "out", "text", "date", "media_kind", "media_path", "ttl",
    "topic_id", "topic_title",
)

# Колонки, добавленные после первой версии: старые базы дополняются автоматически.
MIGRATIONS = {"topic_id": "INTEGER", "topic_title": "TEXT"}


def normalize(text):
    """Для поиска: регистр не важен, «ё» = «е»."""
    return (text or "").lower().replace("ё", "е")


def fts_query(query):
    """Превращает «привет мир» в запрос FTS5: оба слова, поиск по началу слова."""
    words = re.findall(r"\w+", normalize(query))
    return " AND ".join(f'"{w}"*' for w in words)


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        existing = {r["name"] for r in self.db.execute("PRAGMA table_info(messages)")}
        for col, typ in MIGRATIONS.items():
            if col not in existing:
                self.db.execute(f"ALTER TABLE messages ADD COLUMN {col} {typ}")
        self._init_fts()
        self.db.commit()

    def close(self):
        self.db.close()

    # ------------------------------------------------------------ поиск ----
    def _init_fts(self):
        try:
            exists = self.db.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'messages_fts'"
            ).fetchone()
            self.db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5("
                "body, tokenize = 'unicode61 remove_diacritics 2')"
            )
            self.fts = True
        except sqlite3.OperationalError:
            self.fts = False  # SQLite без FTS5: будет медленный поиск через LIKE
            return
        if not exists:  # первый запуск новой версии: индексируем то, что уже есть
            rows = self.db.execute("SELECT rowid, text FROM messages WHERE text != ''").fetchall()
            self.db.executemany(
                "INSERT INTO messages_fts (rowid, body) VALUES (?, ?)",
                [(r["rowid"], normalize(r["text"])) for r in rows],
            )

    def _fts_set(self, rowid, text):
        if not self.fts:
            return
        self.db.execute("DELETE FROM messages_fts WHERE rowid = ?", (rowid,))
        if text:
            self.db.execute(
                "INSERT INTO messages_fts (rowid, body) VALUES (?, ?)", (rowid, normalize(text))
            )

    def search(self, query, chat_id=None, deleted_only=False, limit=20):
        filters, params = [], []
        if chat_id is not None:
            filters.append("m.chat_id = ?")
            params.append(chat_id)
        if deleted_only:
            filters.append("m.deleted_at IS NOT NULL")
        extra = "".join(f" AND {f}" for f in filters)

        if self.fts:
            match = fts_query(query)
            if not match:
                return []
            sql = (
                "SELECT m.* FROM messages_fts f JOIN messages m ON m.rowid = f.rowid "
                f"WHERE messages_fts MATCH ?{extra} ORDER BY m.date DESC LIMIT ?"
            )
            return self.db.execute(sql, [match, *params, limit]).fetchall()

        words = re.findall(r"\w+", query)
        if not words:
            return []
        likes = " AND ".join("m.text LIKE ?" for _ in words)
        sql = f"SELECT m.* FROM messages m WHERE {likes}{extra} ORDER BY m.date DESC LIMIT ?"
        return self.db.execute(sql, [*(f"%{w}%" for w in words), *params, limit]).fetchall()

    # -------------------------------------------------------- сообщения ----
    def save(self, **row):
        values = [row.get(c) for c in COLUMNS]
        cur = self.db.execute(
            f"INSERT OR IGNORE INTO messages ({', '.join(COLUMNS)}) "
            f"VALUES ({', '.join('?' * len(COLUMNS))})",
            values,
        )
        if cur.rowcount:
            self._fts_set(cur.lastrowid, row.get("text"))
        self.db.commit()

    def set_media_path(self, chat_id, msg_id, path):
        self.db.execute(
            "UPDATE messages SET media_path = ? WHERE chat_id = ? AND msg_id = ?",
            (path, chat_id, msg_id),
        )
        self.db.commit()

    def get(self, chat_id, msg_id):
        return self.db.execute(
            "SELECT rowid, * FROM messages WHERE chat_id = ? AND msg_id = ?", (chat_id, msg_id)
        ).fetchone()

    def chat_title(self, chat_id):
        row = self.db.execute(
            "SELECT chat_title FROM messages WHERE chat_id = ? AND chat_title != '' "
            "ORDER BY date DESC LIMIT 1",
            (chat_id,),
        ).fetchone()
        return row["chat_title"] if row else None

    def mark_deleted(self, chat_id, msg_ids):
        """Помечает сообщения удалёнными и возвращает те, что были в кэше.

        Для личек и обычных групп Telegram не сообщает, в каком чате удалили
        сообщение (chat_id = None), но там ID сообщений уникальны в пределах
        аккаунта, поэтому достаточно искать по msg_id среди не-каналов.
        """
        if not msg_ids:
            return []
        marks = ",".join("?" * len(msg_ids))
        if chat_id is None:
            where, params = f"is_channel = 0 AND msg_id IN ({marks})", list(msg_ids)
        else:
            where, params = f"chat_id = ? AND msg_id IN ({marks})", [chat_id, *msg_ids]
        rows = self.db.execute(
            f"SELECT * FROM messages WHERE deleted_at IS NULL AND {where} ORDER BY msg_id",
            params,
        ).fetchall()
        if rows:
            now = int(time.time())
            self.db.executemany(
                "UPDATE messages SET deleted_at = ? WHERE chat_id = ? AND msg_id = ?",
                [(now, r["chat_id"], r["msg_id"]) for r in rows],
            )
            self.db.commit()
        return rows

    def record_edit(self, chat_id, msg_id, new_text):
        """Сохраняет старую версию текста. Возвращает старую строку или None."""
        row = self.get(chat_id, msg_id)
        if row is None or (row["text"] or "") == (new_text or ""):
            return None
        self.db.execute(
            "INSERT INTO edits (chat_id, msg_id, old_text, new_text, edited_at) VALUES (?, ?, ?, ?, ?)",
            (chat_id, msg_id, row["text"], new_text, int(time.time())),
        )
        self.db.execute(
            "UPDATE messages SET text = ? WHERE chat_id = ? AND msg_id = ?",
            (new_text, chat_id, msg_id),
        )
        self._fts_set(row["rowid"], new_text)
        self.db.commit()
        return row

    def recent_deleted(self, chat_id, limit):
        rows = self.db.execute(
            "SELECT * FROM messages WHERE chat_id = ? AND deleted_at IS NOT NULL "
            "ORDER BY deleted_at DESC, msg_id DESC LIMIT ?",
            (chat_id, limit),
        ).fetchall()
        return list(reversed(rows))

    def edits_for(self, chat_id, msg_id):
        return self.db.execute(
            "SELECT * FROM edits WHERE chat_id = ? AND msg_id = ? ORDER BY edited_at",
            (chat_id, msg_id),
        ).fetchall()

    def stats(self):
        q = self.db.execute
        return {
            "messages": q("SELECT COUNT(*) FROM messages").fetchone()[0],
            "deleted": q("SELECT COUNT(*) FROM messages WHERE deleted_at IS NOT NULL").fetchone()[0],
            "edits": q("SELECT COUNT(*) FROM edits").fetchone()[0],
        }

    def cleanup(self, keep_days):
        """Удаляет старые НЕудалённые сообщения (удалённые храним всегда)."""
        cutoff = int(time.time()) - keep_days * 86400
        old = self.db.execute(
            "SELECT rowid, chat_id, msg_id, media_path FROM messages "
            "WHERE deleted_at IS NULL AND date < ?",
            (cutoff,),
        ).fetchall()
        for r in old:
            if r["media_path"] and os.path.isfile(r["media_path"]):
                try:
                    os.remove(r["media_path"])
                except OSError:
                    pass
        if self.fts:
            self.db.executemany(
                "DELETE FROM messages_fts WHERE rowid = ?", [(r["rowid"],) for r in old]
            )
        self.db.executemany(
            "DELETE FROM messages WHERE chat_id = ? AND msg_id = ?",
            [(r["chat_id"], r["msg_id"]) for r in old],
        )
        self.db.executemany(
            "DELETE FROM edits WHERE chat_id = ? AND msg_id = ?",
            [(r["chat_id"], r["msg_id"]) for r in old],
        )
        self.db.commit()
        return len(old)
