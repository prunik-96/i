"""Пример модуля

Файлы, имя которых начинается с «_», не загружаются. Чтобы подключить этот
пример, скопируй его под другим именем, например modules/hello.py,
и перезапусти мод. Основной код править не нужно.
"""
from telethon import events

from core import command, config_defaults, ctx, esc, handler

# Свои настройки: появятся в config.json при следующем запуске.
config_defaults(hello_text="Привет")


# Команда: напиши «.hello Вася» в любом чате.
@command("hello", help="поздороваться", usage="[имя]")
async def cmd_hello(event, arg):
    await event.edit(f"👋 {esc(ctx.cfg['hello_text'])}, {esc(arg or 'мир')}!")


# Обработчик событий Telegram (любые события Telethon: NewMessage, MessageEdited, ...).
@handler(events.NewMessage(incoming=True, pattern=r"(?i)^пинг$"))
async def on_ping(event):
    await event.reply("понг")
