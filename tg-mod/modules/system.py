"""Служебное

Справка (собирается автоматически из всех модулей), статистика, список модулей.
"""
from core import COMMANDS, LOADED_MODULES, command, ctx, esc, module_title


@command("help", "modhelp", help="список команд")
async def cmd_help(event, arg):
    p = esc(ctx.cfg["prefix"])
    seen, by_module = set(), {}
    for cmd in COMMANDS.values():
        if cmd.name not in seen:
            seen.add(cmd.name)
            by_module.setdefault(cmd.module, []).append(cmd)
    parts = ["<b>🛠 TG-мод — команды</b>"]
    for module in LOADED_MODULES:
        cmds = by_module.get(module)
        if not cmds:
            continue
        parts.append(f"\n<b>{esc(module_title(module))}</b>")
        for c in cmds:
            usage = f" {esc(c.usage)}" if c.usage else ""
            alias = "".join(f" / <code>{p}{a}</code>" for a in c.aliases)
            parts.append(f"<code>{p}{c.name}</code>{alias}{usage} — {esc(c.help)}")
    parts.append("\nУдалённые и изменённые сообщения автоматически приходят в лог-чат.")
    await event.edit("\n".join(parts))


@command("stats", help="статистика кэша")
async def cmd_stats(event, arg):
    s = ctx.store.stats()
    await event.edit(
        f"<b>📊 TG-мод</b>\nВ кэше: {s['messages']}\nУдалённых: {s['deleted']}\nИзменений: {s['edits']}"
    )


@command("modules", help="загруженные модули")
async def cmd_modules(event, arg):
    lines = [f"<b>🧩 Модули ({len(LOADED_MODULES)})</b>"]
    lines += [f"• <code>{m}</code> — {esc(module_title(m))}" for m in LOADED_MODULES]
    disabled = ctx.cfg.get("disabled_modules") or []
    if disabled:
        lines.append("\nОтключены: " + ", ".join(f"<code>{esc(m)}</code>" for m in disabled))
    await event.edit("\n".join(lines))
