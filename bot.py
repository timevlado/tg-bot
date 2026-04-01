import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# ===== НАСТРОЙКИ =====
TOKEN = "8660586485:AAG-m5LuMYPYSeq9H1IV9sAIgUHogRIzF44"
MY_CHANNEL = "https://t.me/+xaluK6hROws0Zjdi"

# ===== СПИСОК КАНАЛОВ БУКМЕКЕРОВ =====
CHANNELS_LIST = """
📋 <b>Каналы букмекеров — читай наоборот:</b>

1. <a href="https://t.me/betcity">BetCity</a>
2. <a href="https://t.me/fonbet_official">Fonbet</a>
3. <a href="https://t.me/winline">Winline</a>
4. <a href="https://t.me/bc_pari">Pari</a>
5. <a href="https://t.me/olimpbet">Olimpbet</a>

⚠️ Помни: эти каналы публикуют прогнозы в своих интересах. Как читать их правильно — я объяснил в видео.
"""

logging.basicConfig(level=logging.INFO)

# ===== СТАРТ =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

# ===== ОБРАБОТКА СЛОВА "ДАВАЙ" =====
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower().strip()

    if "давай" in text:
        keyboard = [
            [InlineKeyboardButton("📊 Мой канал с прогнозами", url=MY_CHANNEL)],
            [InlineKeyboardButton("✉️ Написать мне", callback_data="feedback")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            CHANNELS_LIST,
            parse_mode="HTML",
            reply_markup=reply_markup
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
            reply_markup=reply_markup
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
