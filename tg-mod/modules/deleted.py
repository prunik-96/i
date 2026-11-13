"""Удалённые сообщения

Присылает в лог-чат удалённые сообщения. Если в чате удалили сразу много,
присылает одну сводку вместо потока уведомлений.
"""
import asyncio
from datetime import datetime

from telethon import events

from core import (
    clip, command, config_defaults, ctx, esc, fmt_time, handler, header, is_tracked,
    log, send_chunks, send_log, topic_label,
)

config_defaults(
    # В каких чатах сообщать об удалении: private, groups, channels
    notify_deleted_in=["private", "groups"],
    # Если в чате за раз удалили больше стольких сообщений, придёт одна сводка (0 = выкл.)
    batch_threshold=5,
    # Сколько секунд собирать удаления в одну пачку
    batch_delay=3,
)

pending = {}         # chat_id -> удалённые сообщения, ждущие отправки
flush_tasks = set()  # чтобы задачи не собрал сборщик мусора (и для тестов)


@handler(events.MessageDeleted())
async def on_deleted(event):
    rows = ctx.store.mark_deleted(event.chat_id, event.deleted_ids)
    for row in rows:
        if row["out"] or row["kind"] not in ctx.cfg["notify_deleted_in"]:
            continue
        if not is_tracked(row["chat_id"]):
            continue
        bucket = pending.setdefault(row["chat_id"], [])
        if not bucket:  # первое удаление в пачке: через batch_delay секунд отправим всё разом
            task = asyncio.create_task(flush(row["chat_id"]))
            flush_tasks.add(task)
            task.add_done_callback(flush_tasks.discard)
        bucket.append(row)


async def flush(chat_id):
    await asyncio.sleep(ctx.cfg["batch_delay"])
    rows = pending.pop(chat_id, [])
    threshold = ctx.cfg["batch_threshold"]
    try:
        if threshold > 0 and len(rows) > threshold:
            await send_summary(rows)
        else:
            for row in rows:
                await send_one(row)
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось отправить отчёт об удалении: %s", e)


async def send_one(row):
    text = header(row, "🗑 Удалено сообщение")
    if row["text"]:
        text += f"\n\n{esc(row['text'])}"
    await send_log(text, row["media_path"], row["media_kind"])


async def send_summary(rows):
    rows = sorted(rows, key=lambda r: (r["date"], r["msg_id"]))
    first = rows[0]
    head = [f"<b>🗑 Удалено сообщений: {len(rows)}</b>"]
    if first["kind"] != "private":
        head.append(f"💬 {esc(first['chat_title'])} · <code>{first['chat_id']}</code>")
    head.append(f"👥 Авторов: {len({r['sender_id'] for r in rows})}")
    lines = []
    for r in rows:
        line = (f"<code>{datetime.fromtimestamp(r['date']).strftime('%H:%M')}</code> "
                f"<b>{esc(r['sender_name'])}</b>")
        topic = topic_label(r)
        if topic:
            line += f" <i>[{esc(topic)}]</i>"
        body = esc(clip(r["text"] or "", 200))
        if r["media_kind"]:
            body = f"📎{r['media_kind']} {body}".strip()
        lines.append(f"{line}: {body or '<i>пусто</i>'}")
    media = sum(1 for r in rows if r["media_path"])
    if media:
        lines.append(f"\n📎 Медиа из сводки ({media} шт.) сохранено в папке data/media.")
    await send_chunks("\n".join(head) + "\n", lines)


@command("deleted", help="последние удалённые сообщения в этом чате", usage="[N]")
async def cmd_deleted(event, arg):
    limit = max(1, min(int(arg), 50)) if arg.isdigit() else 10
    rows = ctx.store.recent_deleted(event.chat_id, limit)
    if not rows:
        await event.edit("🗑 В этом чате удалённых сообщений не найдено.")
        return
    parts = [f"<b>🗑 Удалённые сообщения ({len(rows)})</b>"]
    for r in rows:
        body = esc(clip(r["text"] or "", 300)) or f"<i>[{r['media_kind'] or 'пусто'}]</i>"
        topic = topic_label(r)
        where = f" · {esc(topic)}" if topic else ""
        parts.append(f"\n<b>{esc(r['sender_name'])}</b>{where} · {fmt_time(r['date'])}\n{body}")
    await event.edit(clip("\n".join(parts), 4096))
