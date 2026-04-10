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

STATS_TEXT = """🗓С 11 июля по 11 августа 
2.04 ✅ 1%
1.78 ✅ 1%
1.81 ✅ 2%
1.64 ✅ 1%
1.90 ✅ 1%
1.78 ✅ 2%
1.73 ✅ 2%
1.75 ✅ 2%
1.70 ✅ 2%
2.85 ❌ 1%
1.65 ✅ 2%
1.71 ❌ 2%
1.78 ✅ 2%
1.84 ❌ 2%
2.23 ❌ 1%
1.62 ✅ 6%
1.62 ✅ 2%
1.78 ✅ 2%
1.76 ✅ 2%
2.80 ❌ 1%
1.75 ❌ 2%
1.76 ❌2%
1.75 ✅ 2%
2.13 ❌ 1%
3.31 ✅ 2%
2.08 ✅ 1%
1.84 ✅ 1%
1.62 ✅ 2%
1.65 ❌ 2%
2.68 ✅ 4%
1.72 ❌ 2%
2.55 ✅ 2%
👉Чистая прибыль +124,500₽ (+24.90%)


🗓С 11 августа по 11 сентября
1.76 ✅ 1%
1.74 ❌ 4%
1.85 ✅ 4%
1.55 ✅ 2%
2.05 ✅ 6%
1.97 ✅ 2%
1.80 ❌ 4%
3.80 ✅ 1%
1.63 ✅ 1%
1.80 ✅ 2%
2.13 ❌ 2%
1.70 ✅ 2%
1.65 ✅ 2%
1.79 ✅ 2%
1.81 ✅ 2%
2.23 ✅ 1%
1.60 ✅ 1%
2.10 ✅ 1%
1.67 ❌ 1%
1.62 ✅ 2%
👉 Чистая прибыль +88,000₽ (+28.60%)


🗓С 11 сентября по 11 октября
3.80 ❌ 2%
1.71 ✅ 2%
2.16 ✅ 3%
1.77 ✅ 2%
1.70 ✅ 2%
1.59 ✅ 2%
1.60 ✅ 2%
3.71 ❌ 1%
1.93 ❌ 2%
3.12 ❌ 2%
1.72 ✅ 2%
1.62 ❌ 2%
1.73 ✅ 2%
7.12 ❌ 1%
1.68 ✅ 2%
2.60 ❌ 2%
1.65 ✅ 2%
1.72 ❌ 2%
1.70 ✅ 4%
1.60 ✅ 4%
1.70 ✅ 2%
3.03 ✅ 2%
1.75 ✅ 2%
2.62 ❌ 2%
1.53 ✅ 2%
1.75 ✅ 2%
2.00 ❌ 2%
1.75 ✅ 2%
20.44 ❌ 1%
1.60 ✅ 2%
1.67 ❌ 2%
👉Чистая прибыль +61,000₽ (+12.20%)


🗓С 11 октября по 11 ноября 
1.62 ✅ 2%
1.60 ✅ 2%
1.82 ❌ 2%
4.20 ✅ 1%
1.63 ✅ 2%
1.70 ✅ 2%
2.01 ✅ 2%
1.67 ✅ 2%
1.87 ✅ 2%
1.84 ✅ 2%
1.75 ❌ 1%
1.70 ✅ 3%
1.62 ✅ 5%
1.70 ✅ 2%
1.85 ✅ 5%
1.75 ✅ 2%
1.85 ✅ 2%
1.85 ✅ 4%
1.65 ✅ 2%
1.70 ✅ 2%
8.30 ✅ 2%
1.70 ✅ 4%
1.80 ❌ 1%
4.30 ✅ 1%
1.76 ✅ 5%
1.70 ✅ 4%
1.75 ❌ 2%
1.85 ✅ 2%
1.70 ✅ 3%
2.40 ❌ 2%
👉 Чистая прибыль +291,650₽ (+58.33%)


🗓С 11 ноября по 11 декабря 
1.70 ❌ 1%
1.70 ✅ 3%
1.62 ✅ 3%
1.72 ❌ 2%
1.85 ✅ 4%
2.19 ✅ 2%
2.69 ✅ 4%
2.10 ❌ 4%
2.04 ✅ 4%
1.60 ✅ 2%
1.90 ❌ 1%
1.75 ✅ 4%
1.80 ✅ 4%
1.65 ❌ 4%
2.03 ❌ 4%
1.62 ✅ 4%
1.62 ✅ 4%
1.63 ✅ 2%
1.88 ✅ 2%
2.65 ✅ 4%
1.90 ✅ 4%
2.65 ✅ 2%
👉 Чистая прибыль +167,700₽ (+33.54%)


🗓С 11 декабря по 11 января 
2.65 ✅ 2%
1.78 ✅ 4%
2.60 ❌ 4%
1.81 ✅ 2%
1.80 ❌ 2%
1.70 ✅ 2%
1.75 ❌ 2%
1.60 ✅ 4%
2.10 ✅ 2%
1.62 ❌ 2%
24.73 ❌ 1%
2.00 ❌ 2%
1.84 ✅ 2%
1.84 ❌ 2%
3.70 ❌ 1%
1.66 ✅ 2%
1.66 ❌ 2%
1.88 ✅ 2%
1.42 ✅ 2%
👉Чистая прибыль +8,200₽ (+1.64%)


🗓С 11 января по 11 февраля
1.61 ✅ 4%
1.65 ✅ 4%
1.62 ❌ 2%
1.51 ✅ 2%
1.54 ✅ 2%
1.71 ❌ 2%
1.61 ✅ 1%
2.01 ✅ 2%
1.82 ❌ 2%
1.62 ❌ 2%
1.57 ✅ 2% 
1.85 ✅ 2% 
1.45 ✅ 1%
1.69 ✅ 2% 
2.04 ✅ 2% 
4.84 ❌ 1%
1.63 ✅ 2% 
2.37 ❌ 2%
1.85 ✅ 2% 
1.93 ❌ 2% 
1.50 ✅ 1%
👉Чистая прибыль +34,900₽ (+6.98%)


🗓С 11 февраля по 11 марта
1.94 ❌ 2%
1.71 ❌ 2%
1.85 ❌ 2%
1.76 ✅ 5%
1.70 ✅ 2%
1.75 ✅ 2%
1.55 ❌ 2%
1.76 ✅ 2%
2.00 ✅ 2%
1.64 ❌ 0.7%
4.20 ✅ 1%
1.65 ✅ 4%
1.83 ✅ 1%
1.60 ❌ 2%
1.87 ❌2%
1.77 ✅ 2%
1.78 ❌ 5%
1.64 ❌ 3%
2.65 ❌ 4%
1.74 ✅ 3%
1.76 ❌ 2%
1.55 ✅ 2%
👉Чистый убыток -24,950₽ (-4.99%)

🗓С 11 марта по 11 апреля
Заполняю… ✍️ 


🧮 ИТОГ ЗА 8 МЕСЯЦЕВ: 
Чистая прибыль +751,000₽ (+150.20%)

Каждая ставка - в открытом доступе. Никаких догонов, которые убивают банк! Да, людям проще увидеть один минус и забыть работу за пол года. Так устроена психология большинства. Но если вы смотрите шире, чем один день - вы увидите, какой колоссальный объём работы я делаю."""

CLUB_TEXT = """Если ты уже следишь за каналом, ты и сам видишь мой подход, логику и то, как я работаю.

Закрытый клуб - это формат для тех, кто хочет быть внутри всей этой системы, а не просто наблюдать со стороны.

Что получает каждый участник закрытого клуба ❓

- ставки с полной аналитикой;
- закрытый чат единомышленников;
- рекомендации по распределению банка и советы по психологии беттинга;
 
➕ все платные услуги автоматически попадают в закрытый клуб (одиночки, экспрессы, марафоны, комбо и тд);

💰Стоимость:
Бессрочно 100.000₽ (1270$)
1 год 49.000₽ (625$)
1 месяц 19.000₽ (245$)
1 неделя 9.000₽ (115$)

Почему выгодно ❓

Приобретая доступ на 1 месяц, вы получаете все материалы менее чем за 500₽ (6$) в день.

Если смотреть на дистанции, годовой доступ - это самый выгодный формат: меньше 130₽ (1.5$) в день за полный доступ ко всей системе.

Для вступления: лс @vm_N17

Добро пожаловать в клуб 🤝"""

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

# ===== СТАТИСТИКА АДМИНА =====
async def stats_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    keyboard = [
        [InlineKeyboardButton("📊 Статистика", callback_data="stats"),
         InlineKeyboardButton("🔒 Закрытый клуб", callback_data="club")],
        [InlineKeyboardButton("📺 Мой канал с прогнозами", url=MY_CHANNEL)],
        [InlineKeyboardButton("✉️ Написать мне", callback_data="feedback")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if "давай" in text:
        await update.message.reply_text(
            CHANNELS_LIST,
            parse_mode="HTML",
            reply_markup=reply_markup,
            disable_web_page_preview=True
        )
    else:
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
        await query.message.reply_text(STATS_TEXT)

    elif query.data == "club":
        await query.message.reply_text(CLUB_TEXT)

    elif query.data == "feedback":
        await query.message.reply_text("✉️ Обратная связь: @vm_N17")

# ===== ЗАПУСК =====
def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats_admin))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(MessageHandler(filters.PHOTO & filters.User(ADMIN_ID), broadcast_photo))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
