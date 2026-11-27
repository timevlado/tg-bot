"""Ассистент чата клуба «СВОИ» — отдельный бот со статистикой НХЛ.

Запускается ОТДЕЛЬНЫМ сервисом Railway (команда: python assistant.py) со своим токеном.
Платёжного бота (bot.py) не импортирует и не трогает; из общей базы использует только
свои таблицы: nhl_games, assistant_log и ключи assistant_* / nhl_* в settings.
"""
import asyncio
import json
import logging
import os
import re
import time
from collections import deque
from datetime import datetime, timedelta, timezone

from anthropic import AsyncAnthropic
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

import nhl_data as nhl

logging.basicConfig(format="%(asctime)s %(name)s %(levelname)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("assistant")

# ===== НАСТРОЙКИ (переменные Railway) =====
TOKEN = os.environ.get("ASSISTANT_BOT_TOKEN")
ADMIN_ID = 1546392669
CLUB_CHAT_ID = int(os.environ.get("CLUB_CHAT_ID") or 0)
NAME = os.environ.get("ASSISTANT_NAME", "Шайба").strip()
MODEL = os.environ.get("ASSISTANT_MODEL", "claude-sonnet-5-5").strip()
COOLDOWN = int(os.environ.get("ASSISTANT_COOLDOWN_MIN", "12")) * 60   # пауза между «влезаниями» в разговор
MAX_PER_HOUR = int(os.environ.get("ASSISTANT_MAX_PER_HOUR", "4"))      # сколько раз в час может влезть сам
DAILY_LIMIT = int(os.environ.get("ASSISTANT_DAILY_LIMIT", "300"))     # потолок запросов к ИИ в сутки
POST_COMMENTS = os.environ.get("ASSISTANT_POST_COMMENTS", "1") == "1"  # комментировать посты канала статистикой
DEBOUNCE_SEC = 30          # ждём, пока люди договорят, прежде чем решать, влезать ли
CONTEXT_MESSAGES = 30      # сколько последних сообщений чата видит ассистент
SYNC_EVERY_MIN = 20        # как часто обновлять матчи НХЛ

_client = None


def ai():
    """Клиент Claude (ключ — ANTHROPIC_API_KEY). Создаётся при первом запросе."""
    global _client
    if _client is None:
        _client = AsyncAnthropic()
    return _client
MSK = timedelta(hours=3)

state = {
    "last_proactive": 0.0,
    "last_eval": 0.0,        # когда последний раз думал «влезать ли» (даже если промолчал)
    "proactive_times": deque(),
    "user_direct": {},       # user_id -> deque времён обращений (антиспам)
    "pending": None,         # отложенная задача «влезть в разговор»
    "day": None, "calls": 0,
}

HOCKEY_WORDS = re.compile(
    r"нхл|nhl|хоккей|шайб|броск|бросает|бросал|тотал|индивидуал|вратар|кипер|овертайм|буллит|створ|"
    r"\b(?:тб|тм|ит|xg|хг)\b|"
    + "|".join(re.escape(n.lower()[:5]) for n in nhl.TEAMS.values())
    + r"|\b(?:" + "|".join(nhl.TEAMS).lower() + r")\b",
    re.IGNORECASE)


# ===== ПРОМПТ =====
TEAM_LIST = ", ".join(f"{code} — {name}" for code, name in nhl.TEAMS.items())

SYSTEM = f"""Ты — {NAME}, ассистент закрытого клуба «СВОИ» Влада Морозова в Telegram. Ты бот, и все в чате это знают. Если спросят, кто ты, спокойно подтверждай, что ты бот-ассистент клуба. За человека себя не выдаёшь.

Твоя тема — статистика НХЛ. Ты сидишь в чате как свой: участвуешь в разговоре, подкалываешь по-доброму, споришь с цифрами в руках, подкидываешь интересные факты к матчам.

Жёсткие правила:
1. Никаких ставок. Не советуешь, что брать, не говоришь «зайдёт / не зайдёт», «я бы взял», «тут вэлью», не называешь фаворита как совет. Прогнозы Влада не оцениваешь и не оспариваешь — это его работа. Спросили «зайдёт?» или «что брать?» — дай цифры по теме и с юмором переведи стрелки на разбор Влада.
2. Каждая цифра — только из инструментов. Нет данных — так и скажи. Не выдумывай составы, травмы, вратарей, новости, коэффициенты: у тебя есть только результаты матчей, голы, броски в створ, расписание.
3. Всегда понятно, на какой выборке цифра («дома, 12 матчей сезона», «последние 5»). Маленькая выборка — скажи об этом.
4. Голы и тоталы считаешь за основное время (60 минут), если не спросили иначе.
5. Только НХЛ. Про КХЛ, футбол и остальное честно скажи, что пока ты только по НХЛ.
6. Пиши как человек в чате: коротко, 1–4 предложения. Без заголовков, списков, markdown и звёздочек — обычный текст. Эмодзи — максимум одно и не в каждом сообщении.
7. Язык клуба: тотал, ТБ/ТМ, индивидуалка (ИТ), бросковая, буллиты, овертайм, кэф, «валуй», «вэлью». Слово «заход» не используешь. Юмор сухой и по-доброму. Без мата, без оскорблений, никого не высмеиваешь за проигрыш.
8. Если человек пишет, что отыгрывается, поставил последнее, в большом минусе или на нервах — не подначивай и не шути над этим, сбавь обороты и ответь по-человечески.
9. Подписка, оплата, доступ в клуб — это к Службе заботы @vm_N17.
10. Время матчей называешь по Москве. Даты в NHL API — североамериканские: матч «27-го» в Америке обычно ночью на 28-е по Москве.

Команды НХЛ (код — как в чате): {TEAM_LIST}.
"""

MODE_HINTS = {
    "direct": "К тебе обратились напрямую — ответь на последнее сообщение.",
    "private": "Это личка с Владом, он тестирует тебя — отвечай как в чате клуба.",
    "proactive": (
        "Тебя не звали. Влезай в разговор, только если реально есть что добавить по статистике НХЛ: "
        "вопрос повис без ответа, в споре звучит неверная цифра, обсуждают матч и у тебя есть интересный факт. "
        "Не здоровайся и не пиши пустых реплик. Если добавить нечего — ответь ровно одним словом SKIP. "
        "Лучше промолчать, чем написать ерунду."
    ),
    "post": (
        "Влад только что опубликовал пост в канале клуба (он последний в переписке). Если пост про матч(и) НХЛ — "
        "напиши короткий комментарий-справку: 2–3 самые интересные цифры к этому матчу (броски дома/в гостях, "
        "тоталы основного времени, очные встречи, форма). Без оценки прогноза и без подсказок, что ставить. "
        "Если пост не про конкретный матч НХЛ — ответь ровно одним словом SKIP."
    ),
}


# ===== ИНСТРУМЕНТЫ =====
TEAM_PROP = {"type": "string", "enum": list(nhl.TEAMS), "description": "Код команды"}
TOOLS = [
    {"name": "team_stats",
     "description": "Средние показатели команды по завершённым матчам регулярки: голы за и против (осн. время), "
                    "тотал осн. времени и как часто он больше 5.5/6.5, броски в створ за/против/суммарно, разброс бросков, "
                    "сколько матчей ушло в ОТ/буллиты, последние 5 матчей.",
     "input_schema": {"type": "object", "properties": {
         "team": TEAM_PROP,
         "venue": {"type": "string", "enum": ["all", "home", "away"], "description": "все / дома / в гостях"},
         "last_n": {"type": "integer", "description": "Последние N матчей. Не указывай — весь сезон."},
         "season": {"type": "string", "enum": ["current", "previous"]}},
         "required": ["team"]}},
    {"name": "recent_games",
     "description": "Список последних матчей команды: дата, соперник, дом/выезд, счёт, броски.",
     "input_schema": {"type": "object", "properties": {
         "team": TEAM_PROP, "n": {"type": "integer"},
         "venue": {"type": "string", "enum": ["all", "home", "away"]}}, "required": ["team"]}},
    {"name": "head_to_head",
     "description": "Очные встречи двух команд за текущий и прошлый сезоны, со статистикой с точки зрения team1.",
     "input_schema": {"type": "object", "properties": {
         "team1": TEAM_PROP, "team2": TEAM_PROP, "limit": {"type": "integer"}}, "required": ["team1", "team2"]}},
    {"name": "games_on_date",
     "description": "Матчи НХЛ за дату (североамериканская дата): время по МСК, итоговый счёт и броски.",
     "input_schema": {"type": "object", "properties": {
         "date": {"type": "string", "description": "YYYY-MM-DD"}}, "required": ["date"]}},
    {"name": "upcoming_games",
     "description": "Ближайшие матчи (всей лиги или одной команды) со временем по МСК.",
     "input_schema": {"type": "object", "properties": {
         "team": TEAM_PROP, "limit": {"type": "integer"}}}},
    {"name": "league_ranking",
     "description": "Рейтинг всех команд лиги по показателю (по убыванию): броски за/против/суммарно, голы за/против/тотал.",
     "input_schema": {"type": "object", "properties": {
         "metric": {"type": "string", "enum": list(nhl.METRICS)},
         "venue": {"type": "string", "enum": ["all", "home", "away"]},
         "last_n": {"type": "integer"}}, "required": ["metric"]}},
]

TOOL_FUNCS = {
    "team_stats": nhl.team_stats, "recent_games": nhl.recent_games, "head_to_head": nhl.head_to_head,
    "games_on_date": lambda date: nhl.games_on_date(date), "upcoming_games": nhl.upcoming_games,
    "league_ranking": nhl.league_ranking,
}


async def run_tool(name, args):
    func = TOOL_FUNCS.get(name)
    if not func:
        return {"error": f"нет инструмента {name}"}
    try:
        return await asyncio.to_thread(func, **args)
    except Exception as e:  # ошибку отдаём модели — пусть честно скажет, что данных нет
        log.warning("tool %s %s → %s", name, args, e)
        return {"error": str(e)}


# ===== ЖУРНАЛ ЧАТА (контекст разговора) =====
def init_log_table():
    nhl._db("""
        CREATE TABLE IF NOT EXISTS assistant_log (
            id         BIGSERIAL PRIMARY KEY,
            chat_id    BIGINT,
            msg_id     BIGINT,
            author     TEXT,
            reply_to   TEXT,
            text       TEXT,
            is_post    BOOLEAN DEFAULT FALSE,
            is_bot     BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""")
    nhl._db("CREATE INDEX IF NOT EXISTS assistant_log_chat ON assistant_log (chat_id, id)")


def author_of(msg):
    if msg.is_automatic_forward or (msg.sender_chat and msg.sender_chat.type == "channel"):
        return "Влад (пост в канале)"
    u = msg.from_user
    if not u:
        return "кто-то"
    if u.id == ADMIN_ID:
        return "Влад"
    if getattr(u, "is_bot", False):
        return NAME
    return u.first_name or u.username or "участник"


def save_message(msg, is_bot=False, text=None):
    text = text if text is not None else (msg.text or msg.caption or "")
    if not text:
        return
    reply_to = None
    if msg.reply_to_message:
        reply_to = author_of(msg.reply_to_message)
    nhl._db(
        "INSERT INTO assistant_log (chat_id, msg_id, author, reply_to, text, is_post, is_bot) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (msg.chat_id, msg.message_id, NAME if is_bot else author_of(msg), reply_to, text[:4000],
         bool(msg.is_automatic_forward), is_bot))


def load_context(chat_id):
    rows = nhl._db(
        "SELECT author, reply_to, text, is_post, created_at FROM assistant_log "
        "WHERE chat_id=%s AND created_at > NOW() - INTERVAL '2 days' ORDER BY id DESC LIMIT %s",
        (chat_id, CONTEXT_MESSAGES), fetch="all") or []
    lines = []
    for author, reply_to, text, is_post, created in reversed(rows):
        t = (created.astimezone(timezone.utc) + MSK).strftime("%d.%m %H:%M")
        limit = 1500 if is_post else 600
        body = text if len(text) <= limit else text[:limit] + "…"
        to = f" (в ответ {reply_to})" if reply_to else ""
        lines.append(f"[{t}] {author}{to}: {body}")
    return "\n".join(lines)


def prune_log():
    nhl._db("DELETE FROM assistant_log WHERE created_at < NOW() - INTERVAL '7 days'")


# ===== ВЫЗОВ ИИ =====
def budget_ok():
    today = datetime.now(timezone.utc).date()
    if state["day"] != today:
        state["day"], state["calls"] = today, 0
    if state["calls"] >= DAILY_LIMIT:
        return False
    state["calls"] += 1
    return True


async def ask_claude(mode, transcript, extra=""):
    if not budget_ok():
        log.warning("Дневной лимит запросов к ИИ исчерпан")
        return None
    now_msk = (datetime.now(timezone.utc) + MSK).strftime("%d.%m.%Y %H:%M")
    user_text = (
        f"Сейчас {now_msk} по Москве.\n\nПоследние сообщения чата клуба:\n{transcript or '(пусто)'}\n\n"
        f"{extra}{MODE_HINTS[mode]}\nНапиши только текст своего сообщения в чат."
    )
    messages = [{"role": "user", "content": user_text}]
    for _ in range(6):
        resp = await ai().messages.create(
            model=MODEL, max_tokens=700, system=SYSTEM, tools=TOOLS, messages=messages)
        if resp.stop_reason != "tool_use":
            text = "".join(b.text for b in resp.content if b.type == "text").strip()
            return clean_reply(text)
        messages.append({"role": "assistant", "content": resp.content})
        results = []
        for block in resp.content:
            if block.type == "tool_use":
                out = await run_tool(block.name, block.input or {})
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": json.dumps(out, ensure_ascii=False, default=str)})
        messages.append({"role": "user", "content": results})
    return None


def clean_reply(text):
    if not text or text.strip().upper().startswith("SKIP"):
        return None
    text = re.sub(r"\*\*|__|^#+\s*", "", text, flags=re.MULTILINE).strip()
    return text[:1500] or None


async def reply(context, msg, mode, extra=""):
    try:
        await context.bot.send_chat_action(msg.chat_id, ChatAction.TYPING)
    except Exception:
        pass
    transcript = await asyncio.to_thread(load_context, msg.chat_id) if mode != "private" else \
        f"Влад: {msg.text}"
    try:
        text = await ask_claude(mode, transcript, extra)
    except Exception as e:
        log.exception("Ошибка ИИ: %s", e)
        if mode in ("direct", "private"):
            await msg.reply_text("Что-то я подвис, спроси ещё раз через минуту 🙃")
        return None
    if not text:
        return None
    sent = await msg.reply_text(text)
    if mode != "private":
        await asyncio.to_thread(save_message, sent, True, text)
    return sent


# ===== РЕШЕНИЕ: ОТВЕЧАТЬ ИЛИ НЕТ =====
def is_direct(msg, bot):
    text = (msg.text or msg.caption or "").lower()
    if f"@{bot.username.lower()}" in text:
        return True
    r = msg.reply_to_message
    if r and r.from_user and r.from_user.id == bot.id:
        return True
    return bool(re.search(rf"(^|\W){re.escape(NAME.lower())}", text))


def direct_allowed(user_id):
    """Антиспам: один человек — не больше 15 обращений в час."""
    q = state["user_direct"].setdefault(user_id, deque())
    now = time.time()
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= 15:
        return False
    q.append(now)
    return True


def proactive_allowed():
    if nhl.get_setting("assistant_muted") == "1":
        return False
    now = time.time()
    q = state["proactive_times"]
    while q and now - q[0] > 3600:
        q.popleft()
    return (now - state["last_proactive"] >= COOLDOWN and len(q) < MAX_PER_HOUR
            and now - state["last_eval"] >= 120)


def mark_proactive():
    now = time.time()
    state["last_proactive"] = now
    state["proactive_times"].append(now)


async def delayed_proactive(context, msg):
    try:
        await asyncio.sleep(DEBOUNCE_SEC)
        if not proactive_allowed():
            return
        state["last_eval"] = time.time()
        sent = await reply(context, msg, "proactive")
        if sent:
            mark_proactive()
    except asyncio.CancelledError:
        pass


def in_club_chat(chat_id):
    return not CLUB_CHAT_ID or chat_id == CLUB_CHAT_ID


async def on_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not in_club_chat(msg.chat_id):
        return
    if msg.from_user and msg.from_user.id == context.bot.id:
        return
    await asyncio.to_thread(save_message, msg)
    text = msg.text or msg.caption or ""

    # Новый пост канала → справка со статистикой в комментариях
    if msg.is_automatic_forward:
        if POST_COMMENTS and HOCKEY_WORDS.search(text) and nhl.get_setting("assistant_muted") != "1":
            await asyncio.sleep(5)
            await reply(context, msg, "post")
        return

    # Пока люди пишут — откладываем решение «влезать или нет»
    if state["pending"] and not state["pending"].done():
        state["pending"].cancel()
        state["pending"] = None

    if is_direct(msg, context.bot):
        if msg.from_user and direct_allowed(msg.from_user.id):
            await reply(context, msg, "direct")
        return

    if len(text) >= 8 and (HOCKEY_WORDS.search(text) or "?" in text) and proactive_allowed():
        state["pending"] = asyncio.create_task(delayed_proactive(context, msg))


# ===== ЛИЧКА: тест для Влада и команды =====
async def on_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if msg.from_user.id != ADMIN_ID:
        await msg.reply_text(f"Я {NAME}, ассистент клуба «СВОИ» по статистике НХЛ. Живу в чате клуба — пиши мне там 🏒")
        return
    await reply(context, msg, "private")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"{NAME} — ассистент чата.\n\n"
        "Пиши мне сюда любой вопрос — отвечу как в чате (для проверки).\n\n"
        "/status — что с базой НХЛ и лимитами\n"
        "/sync — обновить матчи НХЛ сейчас\n"
        "/mute — не влезать в разговоры (отвечать только когда зовут)\n"
        "/unmute — снова влезать\n"
        "/chatid — в чате клуба покажет его ID")


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = await asyncio.to_thread(nhl.db_status)
    muted = nhl.get_setting("assistant_muted") == "1"
    await update.message.reply_text(
        f"База НХЛ: сезон {s['season']}, матчей {s['games_in_db']}, сыграно {s['finished']}, "
        f"без бросков {s['finished_without_shots']}. Обновлено: {s['last_update']}\n"
        f"Запросов к ИИ сегодня: {state['calls']} из {DAILY_LIMIT}\n"
        f"Режим: {'только по вызову (mute)' if muted else 'влезает в разговоры'}\n"
        f"Чат клуба: {CLUB_CHAT_ID or 'не задан (CLUB_CHAT_ID)'}\nМодель: {MODEL}")


async def sync_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Обновляю матчи... ⏳")
    n = await nhl.sync_recent()
    await update.message.reply_text(f"Готово, обновлено матчей: {n}")


async def mute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await asyncio.to_thread(nhl.set_setting, "assistant_muted", "1")
    await update.message.reply_text("Ок, сам в разговоры не лезу. Отвечаю, только когда зовут.")


async def unmute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await asyncio.to_thread(nhl.set_setting, "assistant_muted", "0")
    await update.message.reply_text("Снова в деле — буду подключаться к разговорам.")


async def chatid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"ID этого чата: {update.effective_chat.id}")


# ===== ФОН: обновление матчей =====
async def sync_loop(bot):
    try:
        n = await nhl.backfill_if_needed()
        if n:
            log.info("Первичная загрузка НХЛ: %s матчей", n)
            await bot.send_message(ADMIN_ID, f"{NAME}: база НХЛ загружена ({n} матчей). Можно проверять.")
    except Exception as e:
        log.exception("Первичная загрузка НХЛ упала: %s", e)
    while True:
        try:
            await nhl.sync_recent()
            await asyncio.to_thread(prune_log)
        except Exception as e:
            log.exception("Синк НХЛ: %s", e)
        await asyncio.sleep(SYNC_EVERY_MIN * 60)


async def on_startup(app: Application):
    app.bot_data["sync_task"] = asyncio.create_task(sync_loop(app.bot))
    log.info("%s запущен (@%s), чат клуба: %s", NAME, app.bot.username, CLUB_CHAT_ID or "любой")


def main():
    if not TOKEN:
        raise SystemExit("Нет ASSISTANT_BOT_TOKEN")
    nhl.init_db()
    init_log_table()
    app = Application.builder().token(TOKEN).post_init(on_startup).concurrent_updates(True).build()
    admin = filters.User(ADMIN_ID)
    private = filters.ChatType.PRIVATE
    app.add_handler(CommandHandler(["help", "start"], help_cmd, filters=private & admin))
    app.add_handler(CommandHandler("status", status_cmd, filters=private & admin))
    app.add_handler(CommandHandler("sync", sync_cmd, filters=private & admin))
    app.add_handler(CommandHandler("mute", mute_cmd, filters=admin))
    app.add_handler(CommandHandler("unmute", unmute_cmd, filters=admin))
    app.add_handler(CommandHandler("chatid", chatid_cmd, filters=admin))
    app.add_handler(MessageHandler(private & filters.TEXT & ~filters.COMMAND, on_private))
    app.add_handler(MessageHandler(
        filters.ChatType.GROUPS & (filters.TEXT | filters.CAPTION) & ~filters.COMMAND, on_group_message))
    log.info("Старт ассистента, модель %s", MODEL)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
