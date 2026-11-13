"""Поиск

Полнотекстовый поиск по всему кэшу, включая удалённые сообщения.
"""
import re

from core import clip, command, ctx, esc, fmt_time, msg_link, topic_label


def snippet(text, words, width=160):
    """Кусок текста вокруг первого найденного слова, найденные слова выделены жирным."""
    text = text or ""
    low = text.lower().replace("ё", "е")
    found = [low.find(w) for w in words if low.find(w) >= 0]
    start = max(0, min(found, default=0) - width // 3)
    part = text[start:start + width]
    # Выделяем по нормализованной копии, чтобы «Ёлка» нашлась по запросу «елка».
    part_low = part.lower().replace("ё", "е")
    marks = [False] * len(part)
    for w in words:
        for m in re.finditer(re.escape(w), part_low):
            for i in range(m.start(), m.end()):
                marks[i] = True
    out, i = [], 0
    while i < len(part):
        j = i
        while j < len(part) and marks[j] == marks[i]:
            j += 1
        chunk = esc(part[i:j])
        out.append(f"<b>{chunk}</b>" if marks[i] else chunk)
        i = j
    return ("…" if start else "") + "".join(out) + ("…" if start + width < len(text) else "")


def parse_args(arg):
    here = deleted = False
    limit = 15
    words = []
    for token in arg.split():
        t = token.lower()
        if t in ("-h", "-here", "-тут"):
            here = True
        elif t in ("-d", "-del", "-уд"):
            deleted = True
        elif re.fullmatch(r"-\d+", t):
            limit = max(1, min(int(t[1:]), 50))
        else:
            words.append(token)
    return " ".join(words), here, deleted, limit


@command("search", "s", help="поиск по кэшу (-тут: этот чат, -уд: только удалённые, -30: кол-во)",
         usage="слово")
async def cmd_search(event, arg):
    query, here, deleted, limit = parse_args(arg)
    if not query:
        p = esc(ctx.cfg["prefix"])
        await event.edit(
            f"Использование: <code>{p}search слово</code>\n"
            "Флаги: <code>-тут</code> только этот чат, <code>-уд</code> только удалённые, "
            "<code>-30</code> сколько результатов (до 50)"
        )
        return
    rows = ctx.store.search(query, chat_id=event.chat_id if here else None,
                            deleted_only=deleted, limit=limit)
    if not rows:
        await event.edit(f"🔎 По запросу «{esc(query)}» ничего не найдено.")
        return
    words = re.findall(r"\w+", query.lower().replace("ё", "е"))
    lines = [f"<b>🔎 «{esc(query)}»: {len(rows)}</b>"]
    for r in rows:
        mark = "🗑" if r["deleted_at"] else "💬"
        where = esc(r["chat_title"]) if r["kind"] != "private" else "ЛС"
        topic = topic_label(r)
        if topic:
            where += f" › {esc(topic)}"
        when = fmt_time(r["date"])[:16]
        link = msg_link(r)
        if link and not r["deleted_at"]:
            when = f'<a href="{link}">{when}</a>'
        lines.append(f"\n{mark} <b>{esc(r['sender_name'])}</b> · {where} · {when}\n"
                     f"{snippet(r['text'], words)}")
    await event.edit(clip("\n".join(lines), 4096), link_preview=False)
