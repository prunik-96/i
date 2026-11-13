"""ID и информация

Команды .id и .info: ID чатов, пользователей, сообщений, пересылок и сведения о профиле.
"""
from telethon import utils
from telethon.tl import types
from telethon.tl.functions.users import GetFullUserRequest

from core import command, ctx, esc, media_kind, parse_entity_arg, user_link


@command("id", help="ID чата и твой; ответом — ID автора, сообщения, пересылки", usage="[@user]")
async def cmd_id(event, arg):
    chat = await event.get_chat()
    lines = ["<b>🆔 ID</b>"]
    if arg:
        ent = await ctx.client.get_entity(parse_entity_arg(arg))
        lines.append(f"🔎 {esc(utils.get_display_name(ent))} · <code>{utils.get_peer_id(ent)}</code>")
    lines.append(f"💬 Чат: {esc(utils.get_display_name(chat))} · <code>{event.chat_id}</code>")
    lines.append(f"🙋 Ты: <code>{ctx.me_id}</code>")
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


async def resolve_target(event, arg):
    if arg:
        return await ctx.client.get_entity(parse_entity_arg(arg))
    reply = await event.get_reply_message()
    if reply:
        sender = await reply.get_sender()
        if sender:
            return sender
    return await event.get_chat()


@command("info", help="подробная информация о пользователе или чате", usage="[@user / ответ]")
async def cmd_info(event, arg):
    ent = await resolve_target(event, arg)
    if not isinstance(ent, types.User):
        kind = "канал" if getattr(ent, "broadcast", False) else "группа"
        if getattr(ent, "forum", False):
            kind += " с темами"
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

    full = await ctx.client(GetFullUserRequest(ent))
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
