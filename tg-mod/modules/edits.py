"""Изменённые сообщения

Присылает «Было / Стало» при редактировании и хранит всю историю правок.
"""
from telethon import events

from core import clip, command, config_defaults, ctx, esc, fmt_time, handler, header, is_tracked, log, send_log

config_defaults(
    # В каких чатах сообщать об изменении: private, groups, channels
    notify_edited_in=["private"],
)


@handler(events.MessageEdited())
async def on_edited(event):
    if not is_tracked(event.chat_id):
        return
    msg = event.message
    old = ctx.store.record_edit(event.chat_id, msg.id, msg.raw_text)
    if old is None or old["out"] or old["kind"] not in ctx.cfg["notify_edited_in"]:
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


@command("history", help="история правок сообщения (ответом на него)")
async def cmd_history(event, arg):
    reply = await event.get_reply_message()
    if not reply:
        await event.edit("Ответь этой командой на сообщение.")
        return
    edits = ctx.store.edits_for(event.chat_id, reply.id)
    if not edits:
        await event.edit("✏️ Это сообщение не редактировалось (или не было в кэше).")
        return
    parts = [f"<b>✏️ История изменений ({len(edits)})</b>"]
    for i, e in enumerate(edits, 1):
        parts.append(f"\n<b>{i}.</b> до {fmt_time(e['edited_at'])}:\n{esc(clip(e['old_text'] or '', 500))}")
    await event.edit(clip("\n".join(parts), 4096))
