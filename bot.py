import logging
import json
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# ===== НАСТРОЙКИ =====
TOKEN = "8660586485:AAG-m5LuMYPYSeq9H1IV9sAIgUHogRIzF44"
MY_CHANNEL = "https://t.me/+xaluK6hROws0Zjdi"
ADMIN_ID = 1546392669  # твой Telegram ID — замени на свой

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

# ===== СПИСОК КАНАЛОВ БУКМЕКЕРОВ =====
CHANNELS_LIST = """
📋 <b>Каналы букмекеров — читай наоборот:</b>

1. <a href="https://t.me/betcity">BetCity</a>
2. <a href="https://t.me/fonbet_official">Fonbet</a>
3. <a href="https://t.me/winline">Winline</a>
4. <a href="https://t.me/bc_pari">Pari</a>
5. <a href="https://t.me/olimpbet">Olimpbet</a>

⚠️ Помни: эти каналы публикуют прогнозы в своих интересах. Слепо брать каждый их прогноз в обратку — тоже ошибка. Как читать их правильно — я объяснил в видео.
"""

logging.basicConfig(level=logging.INFO)

# ===== СТАРТ =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    add_user(user_id)

    keyboard = [
        [InlineKeyboardButton("📋 Получить список каналов", callback_data="get_list")],
        [InlineKeyboardButton("📊 Мой канал с прогнозами", url=MY_CHANNEL)],
        [InlineKeyboardButton("✉️ Написать мне", callback_data="feedback")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "👋 Доброго времени!\n\n"
        "Выбери что тебя интересует:",
        reply_markup=reply_markup
    )

# ===== ОБРАБОТКА СООБЩЕНИЙ =====
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    add_user(user_id)
    text = update.message.text.lower().strip()

    if user_id == ADMIN_ID and text.startswith("/broadcast "):
        message = update.message.text[11:]
        users = load_users()
        success = 0
        for uid in users:
            try:
                await context.bot.send_message(chat_id=uid, text=message)
                success += 1
            except:
                pass
        await update.message.reply_text(f"✅ Рассылка отправлена {success} пользователям")
        return

    if user_id == ADMIN_ID and text == "/stats":
        users = load_users()
        await update.message.reply_text(f"👥 В базе: {len(users)} пользователей")
        return

    if "давай" in text:
        keyboard = [
            [InlineKeyboardButton("📊 Мой канал с прогнозами", url=MY_CHANNEL)],
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
            [InlineKeyboardButton("📋 Получить список каналов", callback_data="get_list")],
            [InlineKeyboardButton("📊 Мой канал с прогнозами", url=MY_CHANNEL)],
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

    if query.data == "get_list":
        keyboard = [
            [InlineKeyboardButton("📊 Мой канал с прогнозами", url=MY_CHANNEL)],
            [InlineKeyboardButton("✉️ Написать мне", callback_data="feedback")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.reply_text(
            CHANNELS_LIST,
            parse_mode="HTML",
            reply_markup=reply_markup,
            disable_web_page_preview=True
        )

    elif query.data == "feedback":
        await query.message.reply_text(
            "✉️ Обратная связь: @vm_N17"
        )

# ===== ЗАПУСК =====
def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
