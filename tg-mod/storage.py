"""SQLite-кэш сообщений: нужен, чтобы показать текст и медиа после удаления."""
import os
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
)


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def save(self, **row):
        values = [row.get(c) for c in COLUMNS]
        self.db.execute(
            f"INSERT OR IGNORE INTO messages ({', '.join(COLUMNS)}) "
            f"VALUES ({', '.join('?' * len(COLUMNS))})",
            values,
        )
        self.db.commit()

    def set_media_path(self, chat_id, msg_id, path):
        self.db.execute(
            "UPDATE messages SET media_path = ? WHERE chat_id = ? AND msg_id = ?",
            (path, chat_id, msg_id),
        )
        self.db.commit()

    def get(self, chat_id, msg_id):
        return self.db.execute(
            "SELECT * FROM messages WHERE chat_id = ? AND msg_id = ?", (chat_id, msg_id)
        ).fetchone()

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
        """Сохраняет старую версию текста. Возвращает (старая строка, старый текст) или None."""
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
            "SELECT chat_id, msg_id, media_path FROM messages WHERE deleted_at IS NULL AND date < ?",
            (cutoff,),
        ).fetchall()
        for r in old:
            if r["media_path"] and os.path.isfile(r["media_path"]):
                try:
                    os.remove(r["media_path"])
                except OSError:
                    pass
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
