import logging
import os
import psycopg2
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# ===== НАСТРОЙКИ =====
TOKEN = "8660586485:AAG-m5LuMYPYSeq9H1IV9sAIgUHogRIzF44"
MY_CHANNEL = "https://t.me/+xaluK6hROws0Zjdi"
ADMIN_ID = 1546392669
DATABASE_URL = os.environ.get("DATABASE_URL")

# ===== БАЗА ДАННЫХ =====
def get_conn():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY
        )
    """)
    conn.commit()
    cur.close()
    conn.close()

def add_user(user_id):
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("INSERT INTO users (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"DB error: {e}")

def get_all_users():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users")
    users = [row[0] for row in cur.fetchall()]
    cur.close()
    conn.close()
    return users

def count_users():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    count = cur.fetchone()[0]
    cur.close()
    conn.close()
    return count

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
👉 <b>Чистая прибыль +124,500₽ (+24.90%)</b>


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
👉 <b>Чистая прибыль +88,000₽ (+28.60%)</b>


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
👉 <b>Чистая прибыль +61,000₽ (+12.20%)</b>


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
👉 <b>Чистая прибыль +291,650₽ (+58.33%)</b>


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
👉 <b>Чистая прибыль +167,700₽ (+33.54%)</b>


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
👉 <b>Чистая прибыль +8,200₽ (+1.64%)</b>


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
👉 <b>Чистая прибыль +34,900₽ (+6.98%)</b>


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
👉 <b>Чистый убыток -24,950₽ (-4.99%)</b>

🗓С 11 марта по 11 апреля
Заполняю… ✍️ 


🧮 <b>ИТОГ ЗА 8 МЕСЯЦЕВ:</b> 
<b>Чистая прибыль +751,000₽ (+150.20%)</b>

Каждая ставка - в открытом доступе. Никаких догонов, которые убивают банк! Да, людям проще увидеть один минус и забыть работу за пол года. Так устроена психология большинства. Но если вы смотрите шире, чем один день - вы увидите, какой колоссальный объём работы я делаю."""

CLUB_TEXT = """Если ты уже следишь за каналом, ты и сам видишь мой подход, логику и то, как я работаю.

<b>Закрытый клуб</b> - это формат для тех, кто хочет быть внутри всей этой системы, а не просто наблюдать со стороны.

<b>Что получает каждый участник закрытого клуба ❓</b>

- ставки с полной аналитикой;
- закрытый чат единомышленников;
- рекомендации по распределению банка и советы по психологии беттинга;

➕ все платные услуги автоматически попадают в закрытый клуб (одиночки, экспрессы, марафоны, комбо и тд);

<b>💰Стоимость:</b>
Бессрочно <b>100.000₽</b> (1270$)
1 год <b>49.000₽</b> (625$)
1 месяц <b>19.000₽</b> (245$)
1 неделя <b>9.000₽</b> (115$)

<b>Почему выгодно ❓</b>

Приобретая доступ на 1 месяц, вы получаете все материалы менее чем за 500₽ (6$) в день.

Если смотреть на дистанции, годовой доступ - это самый выгодный формат: меньше 130₽ (1.5$) в день за полный доступ ко всей системе.

<b>Для вступления: лс @vm_N17</b>

Добро пожаловать в клуб 🤝"""

logging.basicConfig(level=logging.INFO)

def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📱 Влад Морозов | Канал", url="https://t.me/vladmorozov_tv")],
        [InlineKeyboardButton("🎯 Прогнозы", url=MY_CHANNEL),
         InlineKeyboardButton("📊 Статистика", callback_data="stats")],
        [InlineKeyboardButton("💎 Доступ в закрытый клуб", callback_data="club")],
        [InlineKeyboardButton("✉️ Обратная связь", url="https://t.me/vm_N17")],
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    add_user(update.effective_user.id)
    await update.message.reply_text(
        "👋 Доброго времени!\n\nВыбери что тебя интересует:",
        reply_markup=main_keyboard()
    )

async def stats_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text(f"👥 В базе: {count_users()} пользователей")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Используй: /broadcast Текст сообщения")
        return
    message = " ".join(context.args)
    users = get_all_users()
    success = 0
    for uid in users:
        try:
            await context.bot.send_message(chat_id=uid, text=message)
            success += 1
        except:
            pass
    await update.message.reply_text(f"✅ Рассылка отправлена {success} пользователям")

async def broadcast_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    caption = update.message.caption or ""
    photo = update.message.photo[-1].file_id
    users = get_all_users()
    success = 0
    for uid in users:
        try:
            await context.bot.send_photo(chat_id=uid, photo=photo, caption=caption)
            success += 1
        except:
            pass
    await update.message.reply_text(f"✅ Фото разослано {success} пользователям")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    add_user(update.effective_user.id)
    text = update.message.text.lower().strip()
    if "давай" in text:
        await update.message.reply_text(
            CHANNELS_LIST,
            parse_mode="HTML",
            reply_markup=main_keyboard(),
            disable_web_page_preview=True
        )
    else:
        await update.message.reply_text(
            "Напиши слово <b>давай</b> — и получишь список каналов 👇",
            parse_mode="HTML",
            reply_markup=main_keyboard()
        )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    add_user(query.from_user.id)
    await query.answer()

    if query.data == "stats":
        await query.message.reply_text(STATS_TEXT, parse_mode="HTML", reply_markup=main_keyboard())
    elif query.data == "club":
        await query.message.reply_text(CLUB_TEXT, parse_mode="HTML", reply_markup=main_keyboard())
    elif query.data == "feedback":
        await query.message.reply_text("✉️ Обратная связь: @vm_N17", reply_markup=main_keyboard())

def main():
    init_db()
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
