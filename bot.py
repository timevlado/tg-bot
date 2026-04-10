import logging
import json
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# ===== НАСТРОЙКИ =====
TOKEN = "8660586485:AAG-m5LuMYPYSeq9H1IV9sAIgUHogRIzF44"
MY_CHANNEL = "https://t.me/+xaluK6hROws0Zjdi"
ADMIN_ID = 1546392669

# ===== БАЗА ПОЛЬЗОВАТЕЛЕЙ =====
USERS_FILE = "users.json"

def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(list(users), f)

def add_user(user_id):
    users = load_users()
    users.add(user_id)
    save_users(users)

# ===== ТЕКСТЫ =====
CHANNELS_LIST = """
📋 <b>Каналы букмекеров — читай наоборот:</b>

1. <a href="https://t.me/betcity">BetCity</a>
2. <a href="https://t.me/fonbet_official">Fonbet</a>
3. <a href="https://t.me/winline">Winline</a>
4. <a href="https://t.me/bc_pari">Pari</a>
5. <a href="https://t.me/olimpbet">Olimpbet</a>

⚠️ Помни: эти каналы публикуют прогнозы в своих интересах. Слепо брать каждый их прогноз в обратку — тоже ошибка. Как читать их правильно — я объяснил в видео.
"""

STATS_TEXT = """
📊 <b>Итог за 8 месяцев</b>

💰 Чистая прибыль: <b>+751,000₽ (+150.20%)</b>
🏦 Банк: 500,000₽

📅 По месяцам:
• июль–август: +124,500₽ (+24.90%) ✅
• август–сентябрь: +88,000₽ (+28.60%) ✅
• сентябрь–октябрь: +61,000₽ (+12.20%) ✅
• октябрь–ноябрь: +291,650₽ (+58.33%) ✅
• ноябрь–декабрь: +167,700₽ (+33.54%) ✅
• декабрь–январь: +8,200₽ (+1.64%) ✅
• январь–февраль: +34,900₽ (+6.98%) ✅
• февраль–март: -24,950₽ (-4.99%) ❌

Каждая ставка — в открытом доступе. Никаких догонов. 7 из 8 месяцев в плюсе.
"""

CLUB_TEXT = """
🔒 <b>Закрытый клуб</b>

Если ты уже следишь за каналом, ты и сам видишь мой подход, логику и то, как я работаю.

Закрытый клуб — это формат для тех, кто хочет быть внутри всей этой системы, а не просто наблюдать со стороны.

<b>Что получает каждый участник:</b>
• ставки с полной аналитикой
• закрытый чат единомышленников
• рекомендации по банку и психологии беттинга
• все платные услуги: одиночки, экспрессы, марафоны, комбо

💰 <b>Стоимость:</b>
Бессрочно — 100,000₽ (1270$)
1 год — 49,000₽ (625$)
1 месяц — 19,000₽ (245$)
1 неделя — 9,000₽ (115$)

Годовой доступ — менее 130₽ в день за полный доступ ко всей системе.

Для вступления: @vm_N17 🤝
"""

logging.basicConfig(level=logging.INFO)

# ===== СТАРТ =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    add_user(user_id)
    keyboard = [
        [InlineKeyboardButton("📊 Статистика", callback_data="stats"),
         InlineKeyboardButton("🔒 Закрытый клуб", callback_data="club")],
        [InlineKeyboardButton("📺 Мой канал с прогнозами", url=MY_CHANNEL)],
        [InlineKeyboardButton("✉️ Написать мне", callback_data="feedback")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "👋 Доброго времени!\n\n"
        "Выбери что тебя интересует:",
        reply_markup=reply_markup
    )

# ===== СТАТИСТИКА =====
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    users = load_users()
    await update.message.reply_text(f"👥 В базе: {len(users)} пользователей")

# ===== РАССЫЛКА ТЕКСТА =====
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Используй: /broadcast Текст сообщения")
        return
    message = " ".join(context.args)
    users = load_users()
    success = 0
    for uid in users:
        try:
            await context.bot.send_message(chat_id=uid, text=message)
            success += 1
        except:
            pass
    await update.message.reply_text(f"✅ Рассылка отправлена {success} пользователям")

# ===== РАССЫЛКА ФОТО =====
async def broadcast_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    caption = update.message.caption or ""
    photo = update.message.photo[-1].file_id
    users = load_users()
    success = 0
    for uid in users:
        try:
            await context.bot.send_photo(chat_id=uid, photo=photo, caption=caption)
            success += 1
        except:
            pass
    await update.message.reply_text(f"✅ Фото разослано {success} пользователям")

# ===== ОБРАБОТКА СООБЩЕНИЙ =====
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    add_user(user_id)
    text = update.message.text.lower().strip()

    if "давай" in text:
        keyboard = [
            [InlineKeyboardButton("📊 Статистика", callback_data="stats"),
             InlineKeyboardButton("🔒 Закрытый клуб", callback_data="club")],
            [InlineKeyboardButton("📺 Мой канал с прогнозами", url=MY_CHANNEL)],
            [InlineKeyboardButton("✉️ Написать мне", callback_data="feedback")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            CHANNELS_LIST,
            parse_mode="HTML",
            reply_markup=reply_markup,
            disable_web_page_preview=True
        )
    else:
        keyboard = [
            [InlineKeyboardButton("📊 Статистика", callback_data="stats"),
             InlineKeyboardButton("🔒 Закрытый клуб", callback_data="club")],
            [InlineKeyboardButton("📺 Мой канал с прогнозами", url=MY_CHANNEL)],
            [InlineKeyboardButton("✉️ Написать мне", callback_data="feedback")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "Напиши слово <b>давай</b> — и получишь список каналов 👇",
            parse_mode="HTML",
            reply_markup=reply_markup
        )

# ===== КНОПКИ =====
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    add_user(user_id)
    await query.answer()

    if query.data == "stats":
        await query.message.reply_text(
            STATS_TEXT,
            parse_mode="HTML"
        )

    elif query.data == "club":
        await query.message.reply_text(
            CLUB_TEXT,
            parse_mode="HTML"
        )

    elif query.data == "feedback":
        await query.message.reply_text("✉️ Обратная связь: @vm_N17")

# ===== ЗАПУСК =====
def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(MessageHandler(filters.PHOTO & filters.User(ADMIN_ID), broadcast_photo))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
