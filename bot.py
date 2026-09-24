import logging
import os
import psycopg2
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# ===== НАСТРОЙКИ =====
# Токен берётся из переменных окружения Railway (переменная BOT_TOKEN).
# Так токен не лежит в коде и его нельзя угнать из GitHub.
TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = 1546392669
CONTACT = "vm_N17"  # личка для скриншотов и службы заботы (без @)
DATABASE_URL = os.environ.get("DATABASE_URL")

# ===== БАЗА ДАННЫХ =====
def get_conn():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    try:
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
        print("База данных готова.")
    except Exception as e:
        print(f"Не удалось подключиться к базе при старте (бот продолжит работу): {e}")

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
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM users")
        users = [row[0] for row in cur.fetchall()]
        cur.close()
        conn.close()
        return users
    except Exception as e:
        print(f"DB error (get_all_users): {e}")
        return []

def count_users():
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users")
        count = cur.fetchone()[0]
        cur.close()
        conn.close()
        return count
    except Exception as e:
        print(f"DB error (count_users): {e}")
        return 0

# ===== ТЕКСТЫ =====
WELCOME_TEXT = """<b>Ты в одном шаге от входа в клуб «СВОИ»</b>

<b>Что будет внутри?</b>

🔥 <b>Прогнозы</b>
По линии и в лайве.

👀 <b>Наблюдения</b>
Мысли, идеи и интересные моменты в процессе работы.

🍳 <b>Кухня</b>
Как проходит разбор матчей и подготовка прогнозов.

🎥 <b>Стримы</b>
Совместный просмотр и обсуждение матчей в прямом эфире.

🤝 <b>Общение</b>
Чат для единомышленников — обсуждения и вопросы.

🎮 <b>Интерактивы</b>
Голосования, совместные решения и другие интересные форматы.

🎓 <b>Академия</b>
Большой многочасовой видеокурс о том, как самостоятельно анализировать матчи.

🤖 <b>AI и инструменты</b>
Как применять ИИ и другие полезные инструменты в нашей сфере.

<b>И это только начало.</b>
Клуб будет постепенно развиваться и дополняться новыми материалами, возможностями и инструментами.

💰 <b>Стоимость — 3 000 ₽ в месяц.</b>"""

OFFER_URL = "https://telegra.ph/Publichnaya-oferta--Klub-SVOI-09-24"

PAYMENT_TEXT = f"""💳 <b>Стоимость: 3 000 ₽ / 30 дней</b>
Формат: ежемесячная подписка. Отписаться можно в любой момент.

<i>Нажимая «Оплатить картой РФ» или «Оплатить картой не РФ», вы принимаете условия <a href="{OFFER_URL}">публичной оферты</a>.</i>

⏳ Доступ в клуб откроется в течение нескольких минут после оплаты.

Выберите способ оплаты 👇"""

# Пока Робокасса не подключена: оплата через Службу заботы
PAY_MANUAL_TEXT = """Чтобы оплатить, напишите в Службу заботы 👇

Пришлём реквизиты и откроем доступ в клуб сразу после оплаты."""



logging.basicConfig(level=logging.INFO)

def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 Оформить подписку", callback_data="pay")],
        [InlineKeyboardButton("↗️ Служба заботы", url=f"https://t.me/{CONTACT}")],
    ])

def payment_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Оплатить картой РФ", callback_data="pay_rf")],
        [InlineKeyboardButton("🌍 Оплатить картой не РФ", callback_data="pay_foreign")],
        [InlineKeyboardButton("↗️ Служба заботы", url=f"https://t.me/{CONTACT}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back")],
    ])

def support_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("↗️ Служба заботы", url=f"https://t.me/{CONTACT}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="pay")],
    ])

def user_info(user):
    username = f"@{user.username}" if user.username else "нет username"
    name = user.full_name or "без имени"
    return f"Имя: {name}\nUsername: {username}\nID: <code>{user.id}</code>"

async def notify_admin(context, text):
    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=text, parse_mode="HTML")
    except Exception as e:
        print(f"Не удалось отправить уведомление админу: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    add_user(update.effective_user.id)
    await update.message.reply_text(
        WELCOME_TEXT,
        parse_mode="HTML",
        reply_markup=main_keyboard(),
        disable_web_page_preview=True
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    add_user(query.from_user.id)
    await query.answer()
    user = query.from_user

    if query.data == "pay":
        await notify_admin(context, f"🔔 <b>Новый интерес к подписке!</b>\n\n{user_info(user)}")
        await query.message.reply_text(
            PAYMENT_TEXT,
            parse_mode="HTML",
            reply_markup=payment_keyboard(),
            disable_web_page_preview=True
        )
    elif query.data in ("pay_rf", "pay_foreign"):
        method = "картой РФ" if query.data == "pay_rf" else "картой не РФ"
        await notify_admin(context, f"🔥 <b>Хочет оплатить {method}!</b>\n\n{user_info(user)}")
        await query.message.reply_text(
            PAY_MANUAL_TEXT,
            parse_mode="HTML",
            reply_markup=support_keyboard(),
            disable_web_page_preview=True
        )
    elif query.data == "back":
        await query.message.reply_text(
            WELCOME_TEXT,
            parse_mode="HTML",
            reply_markup=main_keyboard(),
            disable_web_page_preview=True
        )

# ===== АДМИН: статистика и рассылки =====
async def help_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    help_text = (
        "🛠 <b>Команды админа</b>\n\n"
        "/stats — сколько людей в базе бота\n\n"
        "/broadcast <i>текст</i> — разослать текст всем пользователям\n"
        "Пример: <code>/broadcast Завтра стрим в 20:00</code>\n\n"
        "📸 Чтобы разослать фото всем — просто пришли фото сюда с подписью.\n\n"
        "🔔 Когда кто-то нажимает «Оформить подписку» — тебе придёт уведомление с его данными.\n"
        "🔥 Когда кто-то нажимает «Оплатить картой РФ / не РФ» — придёт уведомление, что он готов платить. Напиши ему первым."
    )
    await update.message.reply_text(help_text, parse_mode="HTML")

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
    await update.message.reply_text(
        "Нажми кнопку ниже, чтобы оформить подписку в клуб «СВОИ» 👇",
        reply_markup=main_keyboard()
    )

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_admin))
    app.add_handler(CommandHandler("stats", stats_admin))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(MessageHandler(filters.PHOTO & filters.User(ADMIN_ID), broadcast_photo))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
