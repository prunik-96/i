"""Белый и чёрный списки

Управление тем, за какими чатами следит мод. Без аргумента команда действует
на текущий чат, иначе укажи @username или ID.
"""
from telethon import utils

from core import command, ctx, esc, parse_entity_arg, save_config


async def target(event, arg):
    if arg:
        ent = await ctx.client.get_entity(parse_entity_arg(arg))
        return utils.get_peer_id(ent), utils.get_display_name(ent)
    chat = await event.get_chat()
    return event.chat_id, utils.get_display_name(chat) if chat else str(event.chat_id)


def move(chat_id, add_to=None, remove_from=()):
    for name in remove_from:
        if chat_id in ctx.cfg[name]:
            ctx.cfg[name].remove(chat_id)
    if add_to and chat_id not in ctx.cfg[add_to]:
        ctx.cfg[add_to].append(chat_id)
    save_config()


@command("ignore", help="добавить чат в чёрный список", usage="[@чат / ID]")
async def cmd_ignore(event, arg):
    chat_id, name = await target(event, arg)
    move(chat_id, add_to="blacklist", remove_from=("whitelist",))
    await event.edit(f"🚫 «{esc(name)}» (<code>{chat_id}</code>) в чёрном списке: мод его игнорирует.")


@command("unignore", help="убрать чат из чёрного списка", usage="[@чат / ID]")
async def cmd_unignore(event, arg):
    chat_id, name = await target(event, arg)
    move(chat_id, remove_from=("blacklist",))
    await event.edit(f"✅ «{esc(name)}» убран из чёрного списка.")


@command("watch", help="добавить в белый список (следить только за ним)", usage="[@чат / ID]")
async def cmd_watch(event, arg):
    chat_id, name = await target(event, arg)
    move(chat_id, add_to="whitelist", remove_from=("blacklist",))
    await event.edit(
        f"👁 «{esc(name)}» (<code>{chat_id}</code>) в белом списке.\n"
        f"Теперь мод следит только за чатами из белого списка ({len(ctx.cfg['whitelist'])} шт.)."
    )


@command("unwatch", help="убрать из белого списка", usage="[@чат / ID]")
async def cmd_unwatch(event, arg):
    chat_id, name = await target(event, arg)
    move(chat_id, remove_from=("whitelist",))
    tail = "" if ctx.cfg["whitelist"] else "\nБелый список пуст: мод снова следит за всеми чатами."
    await event.edit(f"➖ «{esc(name)}» убран из белого списка.{tail}")


@command("lists", help="показать белый и чёрный списки")
async def cmd_lists(event, arg):
    def fmt(ids):
        if not ids:
            return "  <i>пусто</i>"
        return "\n".join(f"  • {esc(ctx.store.chat_title(i) or '?')} · <code>{i}</code>" for i in ids)

    mode = ("следит <b>только</b> за белым списком" if ctx.cfg["whitelist"]
            else "следит за всеми чатами, кроме чёрного списка")
    await event.edit(
        f"<b>📋 Списки</b>\nРежим: {mode}\n\n"
        f"<b>👁 Белый список:</b>\n{fmt(ctx.cfg['whitelist'])}\n\n"
        f"<b>🚫 Чёрный список:</b>\n{fmt(ctx.cfg['blacklist'])}"
    )
