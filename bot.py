import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

import httpx
import psycopg2
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ContextTypes, CallbackQueryHandler,
)

# ===== НАСТРОЙКИ =====
TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = 1546392669
CONTACT = "vm_N17"  # Служба заботы (без @)
DATABASE_URL = os.environ.get("DATABASE_URL")

# Робокасса
RK_LOGIN = os.environ.get("ROBOKASSA_LOGIN", "club_svoi").strip()
RK_TEST = os.environ.get("ROBOKASSA_TEST_MODE", "1").strip() == "1"
# Автосписания: 1 — включены (после одобрения Робокассой), 0 — человек продлевает сам по напоминанию
RK_RECURRING = os.environ.get("ROBOKASSA_RECURRING", "0").strip() == "1"
RK_PASS1 = os.environ.get("ROBOKASSA_PASS1", "").strip()
RK_PASS2 = os.environ.get("ROBOKASSA_PASS2", "").strip()
RK_TEST_PASS1 = os.environ.get("ROBOKASSA_TEST_PASS1", "").strip()
RK_TEST_PASS2 = os.environ.get("ROBOKASSA_TEST_PASS2", "").strip()
RK_PAY_URL = "https://auth.robokassa.ru/Merchant/Index.aspx"
RK_RECURRING_URL = "https://auth.robokassa.ru/Merchant/Recurring"

# Клуб: ID закрытого канала и чата комментариев (узнаём через бота, см. /help)
CLUB_CHANNEL_ID = os.environ.get("CLUB_CHANNEL_ID", "").strip()
CLUB_CHAT_ID = os.environ.get("CLUB_CHAT_ID", "").strip()

WEB_PORT = 8080  # тот же порт, что указан в Railway → Networking

PRICE = 3000
PERIOD_DAYS = 30
OUT_SUM = f"{PRICE}.00"
DESCRIPTION = "Подписка на Клуб «СВОИ», 30 дней"
RECEIPT_ITEM = "Информационно-консультационные услуги (подписка на Клуб «СВОИ», 30 дней)"

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("svoi")


def now_utc():
    return datetime.now(timezone.utc)


def fmt_date(dt):
    # Показываем дату по Москве
    return (dt + timedelta(hours=3)).strftime("%d.%m.%Y")


# ===== БАЗА ДАННЫХ =====
def get_conn():
    return psycopg2.connect(DATABASE_URL)


def db(query, params=(), fetch=None):
    """Небольшой помощник: выполнить запрос и (если нужно) вернуть результат."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(query, params)
        result = None
        if fetch == "one":
            result = cur.fetchone()
        elif fetch == "all":
            result = cur.fetchall()
        conn.commit()
        cur.close()
        return result
    finally:
        conn.close()


def init_db():
    try:
        db("""CREATE TABLE IF NOT EXISTS users (user_id BIGINT PRIMARY KEY)""")
        db("""
            CREATE TABLE IF NOT EXISTS payments (
                inv_id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                amount NUMERIC NOT NULL,
                kind TEXT NOT NULL,                -- initial / recurring
                parent_inv_id BIGINT,
                status TEXT NOT NULL DEFAULT 'pending',  -- pending / paid
                is_test BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                paid_at TIMESTAMPTZ
            )
        """)
        db("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id BIGINT PRIMARY KEY,
                status TEXT NOT NULL,              -- active / expired
                paid_until TIMESTAMPTZ NOT NULL,
                auto_renew BOOLEAN NOT NULL DEFAULT FALSE,
                first_inv_id BIGINT,               -- первый платёж с картой (для автосписаний)
                last_charge_at TIMESTAMPTZ,
                reminded_for TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        print("База данных готова.")
    except Exception as e:
        print(f"Не удалось подключиться к базе при старте (бот продолжит работу): {e}")


def add_user(user_id):
    try:
        db("INSERT INTO users (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
    except Exception as e:
        print(f"DB error: {e}")


def get_all_users():
    try:
        return [r[0] for r in db("SELECT user_id FROM users", fetch="all")]
    except Exception as e:
        print(f"DB error (get_all_users): {e}")
        return []


def count_users():
    try:
        return db("SELECT COUNT(*) FROM users", fetch="one")[0]
    except Exception as e:
        print(f"DB error (count_users): {e}")
        return 0


def count_active():
    try:
        return db("SELECT COUNT(*) FROM subscriptions WHERE status = 'active'", fetch="one")[0]
    except Exception:
        return 0


def users_by_audience(audience):
    """Список user_id для рассылки по выбранной группе."""
    try:
        if audience == "all":
            return [r[0] for r in db("SELECT user_id FROM users", fetch="all")]
        if audience == "active":
            return [r[0] for r in db(
                "SELECT user_id FROM subscriptions WHERE status = 'active'", fetch="all")]
        if audience == "left":
            # платили хотя бы раз, но сейчас подписка не активна
            return [r[0] for r in db(
                "SELECT user_id FROM subscriptions WHERE status <> 'active'", fetch="all")]
        if audience == "new":
            # есть в базе, но ни разу не было подписки
            return [r[0] for r in db(
                "SELECT user_id FROM users WHERE user_id NOT IN "
                "(SELECT user_id FROM subscriptions)", fetch="all")]
    except Exception as e:
        print(f"DB error (users_by_audience): {e}")
    return []


def get_sub(user_id):
    try:
        row = db(
            "SELECT status, paid_until, auto_renew, first_inv_id FROM subscriptions WHERE user_id = %s",
            (user_id,), fetch="one",
        )
    except Exception as e:
        print(f"DB error (get_sub): {e}")
        return None
    if not row:
        return None
    return {"status": row[0], "paid_until": row[1], "auto_renew": row[2], "first_inv_id": row[3]}


def is_active(user_id):
    sub = get_sub(user_id)
    return bool(sub and sub["status"] == "active")


# ===== РОБОКАССА =====
def rk_passwords(test):
    return (RK_TEST_PASS1, RK_TEST_PASS2) if test else (RK_PASS1, RK_PASS2)


def md5(s):
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def receipt_encoded():
    """Состав чека (номенклатура) для Робочеков СМЗ, в виде, нужном для подписи."""
    receipt = {
        "items": [{
            "name": RECEIPT_ITEM,
            "quantity": 1,
            "sum": PRICE,
            "payment_method": "full_payment",
            "payment_object": "service",
            "tax": "none",
        }]
    }
    return quote(json.dumps(receipt, ensure_ascii=False, separators=(",", ":")), safe="")


def create_payment(user_id, kind, parent_inv_id=None):
    row = db(
        "INSERT INTO payments (user_id, amount, kind, parent_inv_id, is_test) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING inv_id",
        (user_id, PRICE, kind, parent_inv_id, RK_TEST), fetch="one",
    )
    return row[0]


def payment_link(user_id):
    """Создаёт платёж в базе и возвращает ссылку на страницу оплаты Робокассы."""
    inv_id = create_payment(user_id, "initial")
    pass1, _ = rk_passwords(RK_TEST)
    receipt = receipt_encoded()
    signature = md5(f"{RK_LOGIN}:{OUT_SUM}:{inv_id}:{receipt}:{pass1}")
    params = {
        "MerchantLogin": RK_LOGIN,
        "OutSum": OUT_SUM,
        "InvId": inv_id,
        "Description": DESCRIPTION,
        "Receipt": receipt,           # urlencode закодирует его ещё раз — так требует Робокасса
        "SignatureValue": signature,
        "Culture": "ru",
    }
    if RK_RECURRING:
        params["Recurring"] = "true"  # разрешаем последующие автосписания
    if RK_TEST:
        params["IsTest"] = "1"
    return f"{RK_PAY_URL}?{urlencode(params)}"


async def charge_recurring(user_id, first_inv_id):
    """Автосписание за следующий период. Результат придёт на Result URL."""
    inv_id = create_payment(user_id, "recurring", parent_inv_id=first_inv_id)
    pass1, _ = rk_passwords(False)
    receipt = receipt_encoded()
    data = {
        "MerchantLogin": RK_LOGIN,
        "InvoiceID": inv_id,
        "PreviousInvoiceID": first_inv_id,
        "OutSum": OUT_SUM,
        "Description": DESCRIPTION,
        "Receipt": receipt,
        "SignatureValue": md5(f"{RK_LOGIN}:{OUT_SUM}:{inv_id}:{receipt}:{pass1}"),
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(RK_RECURRING_URL, data=data)
    log.info("Recurring %s for user %s: %s %s", inv_id, user_id, resp.status_code, resp.text[:200])
    return resp.text


# ===== ДОСТУП В КЛУБ =====
def club_chats():
    return [c for c in (CLUB_CHANNEL_ID, CLUB_CHAT_ID) if c]


async def grant_access(bot, user_id, paid_until, first_payment, auto_renew=False):
    """Открывает доступ: снимает старый бан и присылает одноразовую ссылку в канал."""
    for chat in club_chats():
        try:
            await bot.unban_chat_member(chat_id=int(chat), user_id=user_id, only_if_banned=True)
        except Exception as e:
            log.warning("unban %s in %s: %s", user_id, chat, e)

    invite = None
    if CLUB_CHANNEL_ID:
        try:
            link = await bot.create_chat_invite_link(
                chat_id=int(CLUB_CHANNEL_ID),
                member_limit=1,
                expire_date=now_utc() + timedelta(days=1),
                name=f"sub {user_id}",
            )
            invite = link.invite_link
        except Exception as e:
            log.error("create invite for %s: %s", user_id, e)

    if first_payment:
        text = (
            "🎉 <b>Оплата прошла! Добро пожаловать в Клуб «СВОИ»</b>\n\n"
            f"Подписка активна до <b>{fmt_date(paid_until)}</b>.\n"
        )
        if auto_renew:
            text += "Автопродление включено — управлять им можно в разделе «👤 Моя подписка».\n\n"
        else:
            text += "За день до окончания пришлём напоминание со ссылкой на продление.\n\n"
        if invite:
            text += "Ссылка-приглашение ниже одноразовая и действует 24 часа 👇"
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🚪 Войти в клуб", url=invite)]])
        else:
            text += "Ссылка-приглашение придёт в течение нескольких минут."
            kb = None
            await notify_admin_bot(bot, f"⚠️ Не удалось создать ссылку в канал для <code>{user_id}</code>. Добавь вручную.")
        await bot.send_message(user_id, text, parse_mode="HTML", reply_markup=kb)
    else:
        await bot.send_message(
            user_id,
            f"✅ Подписка на Клуб «СВОИ» продлена до <b>{fmt_date(paid_until)}</b>.",
            parse_mode="HTML",
        )


async def revoke_access(bot, user_id):
    """Удаляет из канала и чата (бан + сразу разбан = просто исключение)."""
    if user_id == ADMIN_ID:
        return  # владельца клуба не трогаем
    for chat in club_chats():
        try:
            await bot.ban_chat_member(chat_id=int(chat), user_id=user_id)
            await bot.unban_chat_member(chat_id=int(chat), user_id=user_id, only_if_banned=True)
        except Exception as e:
            log.warning("remove %s from %s: %s", user_id, chat, e)
            await notify_admin_bot(bot, f"⚠️ Не удалось удалить <code>{user_id}</code> из <code>{chat}</code>: {e}")


def extend_subscription(user_id, first_inv_id=None, days=PERIOD_DAYS, auto_renew=None):
    """Продлевает подписку от текущей даты окончания (или от сегодня)."""
    sub = get_sub(user_id)
    start = now_utc()
    if sub and sub["status"] == "active" and sub["paid_until"] > start:
        start = sub["paid_until"]
    paid_until = start + timedelta(days=days)
    if sub:
        db(
            "UPDATE subscriptions SET status = 'active', paid_until = %s, "
            "auto_renew = COALESCE(%s, auto_renew), first_inv_id = COALESCE(%s, first_inv_id) "
            "WHERE user_id = %s",
            (paid_until, auto_renew, first_inv_id, user_id),
        )
    else:
        db(
            "INSERT INTO subscriptions (user_id, status, paid_until, auto_renew, first_inv_id) "
            "VALUES (%s, 'active', %s, %s, %s)",
            (user_id, paid_until, bool(auto_renew), first_inv_id),
        )
    return paid_until


# ===== ПРИЁМ УВЕДОМЛЕНИЙ ОТ РОБОКАССЫ (Result URL) =====
async def robokassa_result(request):
    data = await request.post()
    out_sum = data.get("OutSum", "")
    inv_id = data.get("InvId", "")
    signature = data.get("SignatureValue", "").lower()
    log.info("Result URL: InvId=%s OutSum=%s IsTest=%s", inv_id, out_sum, data.get("IsTest"))

    # Подпись проверяем боевым паролем #2, а если не совпала — тестовым
    is_test = None
    for test_flag in (False, True):
        _, pass2 = rk_passwords(test_flag)
        if pass2 and inv_id and md5(f"{out_sum}:{inv_id}:{pass2}").lower() == signature:
            is_test = test_flag
            break
    if is_test is None:
        log.warning("Неверная подпись для InvId=%s", inv_id)
        return web.Response(text="bad sign", status=400)

    row = db("SELECT user_id, kind, status, parent_inv_id FROM payments WHERE inv_id = %s",
             (int(inv_id),), fetch="one")
    if not row:
        return web.Response(text="unknown invoice", status=404)
    user_id, kind, status, parent_inv_id = row

    if status == "paid":  # повторное уведомление — просто подтверждаем
        return web.Response(text=f"OK{inv_id}")

    try:
        if float(out_sum) < PRICE:
            log.warning("Сумма меньше цены: %s", out_sum)
            return web.Response(text="bad sum", status=400)
    except ValueError:
        return web.Response(text="bad sum", status=400)

    db("UPDATE payments SET status = 'paid', paid_at = now() WHERE inv_id = %s", (int(inv_id),))

    bot = request.app["bot"]
    was_active = is_active(user_id)
    if kind == "initial":
        paid_until = extend_subscription(
            user_id,
            first_inv_id=int(inv_id) if RK_RECURRING else None,
            auto_renew=True if RK_RECURRING else None,
        )
    else:
        paid_until = extend_subscription(user_id)
    sub = get_sub(user_id) or {}

    try:
        await grant_access(bot, user_id, paid_until, first_payment=not was_active,
                           auto_renew=bool(sub.get("auto_renew") and sub.get("first_inv_id")))
    except Exception as e:
        log.error("grant_access %s: %s", user_id, e)

    if kind == "recurring":
        label = "🔁 Автопродление"
    elif was_active:
        label = "🔁 Продление вручную"
    else:
        label = "💰 Новая оплата"
    test_mark = " (ТЕСТ)" if is_test else ""
    await notify_admin_bot(
        bot,
        f"{label}{test_mark}: {out_sum} ₽\nID: <code>{user_id}</code>\nДо: {fmt_date(paid_until)}",
    )
    return web.Response(text=f"OK{inv_id}")


async def health(request):
    return web.Response(text="ok")


# ===== ФОНОВАЯ ПРОВЕРКА ПОДПИСОК =====
async def billing_loop(bot):
    await asyncio.sleep(30)
    while True:
        try:
            await billing_tick(bot)
        except Exception as e:
            log.error("billing_tick: %s", e)
        await asyncio.sleep(600)  # каждые 10 минут


async def billing_tick(bot):
    now = now_utc()

    # 1. Напоминание за сутки до окончания периода
    rows = db(
        "SELECT user_id, paid_until, auto_renew AND first_inv_id IS NOT NULL FROM subscriptions "
        "WHERE status = 'active' AND paid_until > %s AND paid_until <= %s "
        "AND (reminded_for IS NULL OR reminded_for <> paid_until)",
        (now, now + timedelta(days=1)), fetch="all",
    )
    for user_id, paid_until, auto in rows:
        db("UPDATE subscriptions SET reminded_for = paid_until WHERE user_id = %s", (user_id,))
        if user_id == ADMIN_ID:
            continue
        try:
            if auto:
                await bot.send_message(
                    user_id,
                    f"🔔 Завтра ({fmt_date(paid_until)}) подписка на Клуб «СВОИ» продлится автоматически: "
                    "спишется 3 000 ₽ за следующие 30 дней.\n\n"
                    "Отключить автопродление можно в разделе «👤 Моя подписка» (/start).",
                )
            else:
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("💳 Продлить картой РФ / РБ", url=payment_link(user_id))],
                    [InlineKeyboardButton("🌍 Продлить картой не РФ", callback_data="pay_foreign")],
                ])
                await bot.send_message(
                    user_id,
                    f"🔔 Подписка на Клуб «СВОИ» заканчивается {fmt_date(paid_until)}.\n\n"
                    "Чтобы остаться в клубе, продлите её — 3 000 ₽ за следующие 30 дней 👇",
                    reply_markup=kb,
                )
        except Exception as e:
            log.warning("reminder %s: %s", user_id, e)

    # 2. Автосписание, когда период закончился
    rows = db(
        "SELECT user_id, first_inv_id FROM subscriptions WHERE status = 'active' AND auto_renew "
        "AND first_inv_id IS NOT NULL AND paid_until <= %s "
        "AND (last_charge_at IS NULL OR last_charge_at < paid_until)",
        (now,), fetch="all",
    )
    for user_id, first_inv_id in rows:
        db("UPDATE subscriptions SET last_charge_at = %s WHERE user_id = %s", (now, user_id))
        if RK_TEST:
            await notify_admin_bot(bot, f"🧪 ТЕСТ: здесь было бы автосписание для <code>{user_id}</code>.")
            continue
        try:
            await charge_recurring(user_id, first_inv_id)
        except Exception as e:
            log.error("charge %s: %s", user_id, e)

    # 3. Закрытие доступа: без автопродления — сразу, с автопродлением — если за сутки оплата не прошла
    rows = db(
        "SELECT user_id FROM subscriptions WHERE status = 'active' AND ("
        "  (NOT auto_renew AND paid_until <= %s) OR "
        "  (auto_renew AND paid_until <= %s))",
        (now, now - timedelta(days=1)), fetch="all",
    )
    for (user_id,) in rows:
        db("UPDATE subscriptions SET status = 'expired' WHERE user_id = %s", (user_id,))
        await revoke_access(bot, user_id)
        try:
            await bot.send_message(
                user_id,
                "Подписка на Клуб «СВОИ» закончилась, доступ закрыт.\n\n"
                "Чтобы вернуться, нажми /start → «Оформить подписку». Будем рады видеть снова 🤝",
            )
        except Exception as e:
            log.warning("expire msg %s: %s", user_id, e)
        await notify_admin_bot(bot, f"⏹ Подписка закончилась: <code>{user_id}</code>")


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

💰 <b>Стоимость — 3 000 ₽ ($35) в месяц.</b>"""

OFFER_URL = "https://telegra.ph/Publichnaya-oferta--Klub-SVOI-09-24"

PAYMENT_TEXT = f"""💳 <b>Стоимость: 3 000 ₽ ($35) / 30 дней</b>
Формат: ежемесячная подписка. Отписаться можно в любой момент.

<i>Нажимая «Оплатить картой РФ / РБ» или «Оплатить картой не РФ», вы принимаете условия <a href="{OFFER_URL}">публичной оферты</a>.</i>

⏳ Доступ в клуб откроется в течение нескольких минут после оплаты.

Выберите способ оплаты 👇"""

PAY_FOREIGN_TEXT = """🌍 <b>Оплата картой не РФ — $35 / 30 дней</b>

Freedom bank: <code>4002890062233725</code>
(нажмите на номер, чтобы скопировать)

После оплаты отправьте скриншот чека — откроем доступ в клуб в течение нескольких минут 👇"""


# ===== КЛАВИАТУРЫ =====
def main_keyboard(user_id):
    first = (InlineKeyboardButton("👤 Моя подписка", callback_data="my_sub")
             if is_active(user_id) else
             InlineKeyboardButton("🔥 Оформить подписку", callback_data="pay"))
    return InlineKeyboardMarkup([
        [first],
        [InlineKeyboardButton("↗️ Служба заботы", url=f"https://t.me/{CONTACT}")],
    ])


def payment_keyboard(user_id):
    try:
        rf_button = InlineKeyboardButton("💳 Оплатить картой РФ / РБ", url=payment_link(user_id))
    except Exception as e:
        log.error("payment_link: %s", e)
        rf_button = InlineKeyboardButton("💳 Оплатить картой РФ / РБ", url=f"https://t.me/{CONTACT}")
    return InlineKeyboardMarkup([
        [rf_button],
        [InlineKeyboardButton("🌍 Оплатить картой не РФ", callback_data="pay_foreign")],
        [InlineKeyboardButton("↗️ Служба заботы", url=f"https://t.me/{CONTACT}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back")],
    ])


def foreign_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 Отправить скриншот", url=f"https://t.me/{CONTACT}")],
        [InlineKeyboardButton("↗️ Служба заботы", url=f"https://t.me/{CONTACT}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="pay")],
    ])


def support_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("↗️ Служба заботы", url=f"https://t.me/{CONTACT}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="pay")],
    ])


def my_sub_view(user_id):
    sub = get_sub(user_id)
    if not sub or sub["status"] != "active":
        return "Активной подписки нет.", main_keyboard(user_id)
    text = f"👤 <b>Моя подписка</b>\n\nСтатус: активна\nОплачено до: <b>{fmt_date(sub['paid_until'])}</b>\n"
    rows = []
    if sub["first_inv_id"]:
        if sub["auto_renew"]:
            text += "Автопродление: <b>включено</b>"
            rows.append([InlineKeyboardButton("⏸ Отключить автопродление", callback_data="renew_off")])
        else:
            text += "Автопродление: <b>выключено</b> — доступ закроется в дату окончания."
            rows.append([InlineKeyboardButton("▶️ Включить автопродление", callback_data="renew_on")])
    else:
        text += "Автопродления нет — за день до окончания пришлём напоминание."
        rows.append([InlineKeyboardButton("💳 Продлить подписку", callback_data="pay")])
    rows.append([InlineKeyboardButton("↗️ Служба заботы", url=f"https://t.me/{CONTACT}")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="back")])
    return text, InlineKeyboardMarkup(rows)


def user_info(user):
    username = f"@{user.username}" if user.username else "нет username"
    name = user.full_name or "без имени"
    return f"Имя: {name}\nUsername: {username}\nID: <code>{user.id}</code>"


async def notify_admin_bot(bot, text):
    try:
        await bot.send_message(chat_id=ADMIN_ID, text=text, parse_mode="HTML")
    except Exception as e:
        print(f"Не удалось отправить уведомление админу: {e}")


async def notify_admin(context, text):
    await notify_admin_bot(context.bot, text)


# ===== ОБРАБОТЧИКИ =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    add_user(user_id)
    # Сброс режима рассылки, если админ передумал
    for k in ("bc_audience", "bc_stage", "bc_from_chat", "bc_message_id"):
        context.user_data.pop(k, None)
    await update.message.reply_text(
        WELCOME_TEXT, parse_mode="HTML",
        reply_markup=main_keyboard(user_id), disable_web_page_preview=True,
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    add_user(user.id)
    await query.answer()

    # --- Рассылка (только админ) ---
    if query.data.startswith("bc_") and user.id == ADMIN_ID:
        if query.data == "bc_cancel":
            for k in ("bc_audience", "bc_stage", "bc_from_chat", "bc_message_id"):
                context.user_data.pop(k, None)
            await query.message.reply_text("Рассылка отменена.")
        elif query.data.startswith("bc_aud_"):
            await broadcast_pick_audience(update, context, query.data.replace("bc_aud_", ""))
        elif query.data == "bc_send":
            await broadcast_send(update, context)
        return

    if query.data == "pay":
        await notify_admin(context, f"🔔 <b>Новый интерес к подписке!</b>\n\n{user_info(user)}")
        await query.message.reply_text(
            PAYMENT_TEXT, parse_mode="HTML",
            reply_markup=payment_keyboard(user.id), disable_web_page_preview=True,
        )
    elif query.data == "pay_foreign":
        await notify_admin(
            context,
            f"🔥 <b>Хочет оплатить картой не РФ!</b>\n\n{user_info(user)}\n\n"
            f"После скриншота выдай доступ: <code>/grant {user.id} 30</code>",
        )
        await query.message.reply_text(
            PAY_FOREIGN_TEXT, parse_mode="HTML",
            reply_markup=foreign_keyboard(), disable_web_page_preview=True,
        )
    elif query.data == "my_sub":
        text, kb = my_sub_view(user.id)
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
    elif query.data in ("renew_off", "renew_on"):
        on = query.data == "renew_on"
        try:
            db("UPDATE subscriptions SET auto_renew = %s WHERE user_id = %s", (on, user.id))
        except Exception as e:
            log.error("renew toggle: %s", e)
        await notify_admin(context, f"{'▶️ Включил' if on else '⏸ Отключил'} автопродление\n\n{user_info(user)}")
        text, kb = my_sub_view(user.id)
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
    elif query.data == "back":
        await query.message.reply_text(
            WELCOME_TEXT, parse_mode="HTML",
            reply_markup=main_keyboard(user.id), disable_web_page_preview=True,
        )


# ===== АДМИН =====
def is_admin(update):
    return update.effective_user and update.effective_user.id == ADMIN_ID


async def help_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    mode = "🧪 ТЕСТОВЫЙ (деньги не списываются)" if RK_TEST else "💳 БОЕВОЙ"
    help_text = (
        "🛠 <b>Команды админа</b>\n\n"
        "/stats — люди в базе и активные подписки\n\n"
        "/рассылка — рассылка с выбором группы (всем / не оплативших / ушедших / активных).\n"
        "Выбери кнопкой кому → пришли сообщение (текст, фото, видео, кружок, голосовое) → подтверди.\n"
        "Оформление (жирный, курсив, ссылки) сохраняется.\n\n"
        "/grant <i>ID дней</i> — выдать доступ вручную (оплата картой не РФ)\n"
        "Пример: <code>/grant 123456789 30</code>\n"
        "/revoke <i>ID</i> — закрыть доступ и удалить из клуба\n\n"
        "/msg <i>ID текст</i> — написать клиенту от имени бота (если у него нет username)\n\n"
        "🆔 Узнать ID канала: перешли сюда любой пост из канала.\n"
        "🆔 Узнать ID чата: напиши в чате <code>/chatid</code>\n\n"
        f"Режим оплаты: {mode}\n"
        f"Автосписания: {'✅ включены' if RK_RECURRING else '⏸ выключены (продление по напоминанию)'}\n"
        f"Канал: {CLUB_CHANNEL_ID or '❌ не задан'}\n"
        f"Чат: {CLUB_CHAT_ID or '❌ не задан'}"
    )
    await update.message.reply_text(help_text, parse_mode="HTML")


async def stats_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    await update.message.reply_text(
        f"👥 В базе: {count_users()} пользователей\n✅ Активных подписок: {count_active()}"
    )


async def grant_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    try:
        user_id = int(context.args[0])
        days = int(context.args[1]) if len(context.args) > 1 else PERIOD_DAYS
    except (IndexError, ValueError):
        await update.message.reply_text("Используй: /grant ID дней\nПример: /grant 123456789 30")
        return
    paid_until = extend_subscription(user_id, days=days, auto_renew=False)
    try:
        await grant_access(context.bot, user_id, paid_until, first_payment=True)
        await update.message.reply_text(f"✅ Доступ выдан до {fmt_date(paid_until)}, ссылка отправлена.")
    except Exception as e:
        await update.message.reply_text(f"Подписка записана до {fmt_date(paid_until)}, но сообщение не ушло: {e}")


async def revoke_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    try:
        user_id = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("Используй: /revoke ID")
        return
    db("UPDATE subscriptions SET status = 'expired', auto_renew = FALSE WHERE user_id = %s", (user_id,))
    await revoke_access(context.bot, user_id)
    await update.message.reply_text("⏹ Доступ закрыт.")


async def msg_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Написать клиенту от имени бота: /msg ID текст"""
    if not is_admin(update):
        return
    try:
        user_id = int(context.args[0])
        text = update.message.text.split(maxsplit=2)[2]
    except (IndexError, ValueError):
        await update.message.reply_text("Используй: /msg ID текст\nПример: /msg 123456789 Привет! Ссылка в клуб выше 👆")
        return
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("↗️ Ответить в Службу заботы", url=f"https://t.me/{CONTACT}")]])
    try:
        await context.bot.send_message(user_id, text, reply_markup=kb)
        await update.message.reply_text("✅ Сообщение отправлено.")
    except Exception as e:
        await update.message.reply_text(f"❌ Не отправилось: {e}")


async def chatid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    chat = update.effective_chat
    await update.message.reply_text(f"ID этого чата: <code>{chat.id}</code>", parse_mode="HTML")


async def forwarded_from_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    origin = update.message.forward_origin
    chat = getattr(origin, "chat", None)
    if chat:
        await update.message.reply_text(
            f"ID канала «{chat.title}»: <code>{chat.id}</code>", parse_mode="HTML"
        )
    else:
        await update.message.reply_text("Не вижу канал. Перешли пост именно из канала.")


# ===== РАССЫЛКА (кнопки + предпросмотр + сохранение оформления) =====
AUDIENCE_TITLES = {
    "all": "📣 Всем",
    "new": "🆕 Не оплативших",
    "left": "💔 Ушедших",
    "active": "✅ Активных подписчиков",
}


async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг 1: показываем кнопки выбора группы."""
    if not is_admin(update):
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📣 Всем ({count_users()})", callback_data="bc_aud_all")],
        [InlineKeyboardButton(f"🆕 Не оплативших ({len(users_by_audience('new'))})", callback_data="bc_aud_new")],
        [InlineKeyboardButton(f"💔 Ушедших ({len(users_by_audience('left'))})", callback_data="bc_aud_left")],
        [InlineKeyboardButton(f"✅ Активных ({count_active()})", callback_data="bc_aud_active")],
        [InlineKeyboardButton("✖️ Отмена", callback_data="bc_cancel")],
    ])
    await update.message.reply_text(
        "📨 <b>Новая рассылка</b>\n\nКому отправляем?", parse_mode="HTML", reply_markup=kb
    )


async def broadcast_pick_audience(update, context, audience):
    """Шаг 2: группа выбрана, ждём сообщение."""
    context.user_data["bc_audience"] = audience
    context.user_data["bc_stage"] = "await_message"
    count = len(users_by_audience(audience))
    await update.callback_query.message.reply_text(
        f"Группа: <b>{AUDIENCE_TITLES[audience]}</b> — {count} чел.\n\n"
        "Теперь пришли сообщение для рассылки: текст, фото, видео, кружок или голосовое. "
        "Оформление (жирный, курсив, ссылки) сохранится как есть.\n\n"
        "Для отмены — /start",
        parse_mode="HTML",
    )


async def broadcast_preview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг 3: получили сообщение — показываем предпросмотр и кнопку подтверждения."""
    msg = update.message
    context.user_data["bc_from_chat"] = msg.chat_id
    context.user_data["bc_message_id"] = msg.message_id
    context.user_data["bc_stage"] = "confirm"
    audience = context.user_data.get("bc_audience", "all")
    count = len(users_by_audience(audience))
    # Показываем, как это увидят люди
    await context.bot.copy_message(chat_id=msg.chat_id, from_chat_id=msg.chat_id, message_id=msg.message_id)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✅ Отправить ({count})", callback_data="bc_send")],
        [InlineKeyboardButton("✖️ Отмена", callback_data="bc_cancel")],
    ])
    await msg.reply_text(
        f"👆 Вот так увидят сообщение.\n\nГруппа: <b>{AUDIENCE_TITLES[audience]}</b> — {count} чел.\n"
        "Отправляем?",
        parse_mode="HTML", reply_markup=kb,
    )


async def broadcast_send(update, context):
    """Шаг 4: рассылаем, копируя исходное сообщение (оформление сохраняется)."""
    audience = context.user_data.get("bc_audience", "all")
    from_chat = context.user_data.get("bc_from_chat")
    message_id = context.user_data.get("bc_message_id")
    users = users_by_audience(audience)
    q = update.callback_query
    await q.message.reply_text(f"Отправляю {len(users)} чел... ⏳")
    success = 0
    for uid in users:
        try:
            await context.bot.copy_message(chat_id=uid, from_chat_id=from_chat, message_id=message_id)
            success += 1
            await asyncio.sleep(0.05)  # бережём лимиты Telegram
        except Exception:
            pass
    for k in ("bc_audience", "bc_stage", "bc_from_chat", "bc_message_id"):
        context.user_data.pop(k, None)
    await q.message.reply_text(f"✅ Готово. Доставлено: {success} из {len(users)}.")


async def forwarded_or_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Пересланное сообщение: если идёт рассылка — это контент, иначе показываем ID канала."""
    if context.user_data.get("bc_stage") == "await_message":
        await broadcast_preview(update, context)
        return
    await forwarded_from_channel(update, context)


async def broadcast_catch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сообщение админа: если идёт рассылка — это её контент, иначе обычный ответ."""
    if context.user_data.get("bc_stage") == "await_message":
        await broadcast_preview(update, context)
        return
    # не в режиме рассылки — ведём себя как обычно
    await handle_message(update, context)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    add_user(user_id)
    await update.message.reply_text(
        "Нажми кнопку ниже 👇", reply_markup=main_keyboard(user_id)
    )


# ===== ЗАПУСК =====
async def on_startup(application: Application):
    webapp = web.Application()
    webapp["bot"] = application.bot
    webapp.router.add_post("/robokassa/result", robokassa_result)
    webapp.router.add_get("/health", health)
    runner = web.AppRunner(webapp)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", WEB_PORT).start()
    application.bot_data["web_runner"] = runner
    application.bot_data["billing_task"] = asyncio.create_task(billing_loop(application.bot))
    print(f"Приём уведомлений Робокассы запущен на порту {WEB_PORT}. Тестовый режим: {RK_TEST}")


def main():
    init_db()
    app = Application.builder().token(TOKEN).post_init(on_startup).build()
    private = filters.ChatType.PRIVATE
    admin = filters.User(ADMIN_ID)

    app.add_handler(CommandHandler("start", start, filters=private))
    # Админские команды: доступны только ADMIN_ID (фильтр admin) + проверка is_admin внутри
    app.add_handler(CommandHandler("help", help_admin, filters=private & admin))
    app.add_handler(CommandHandler("stats", stats_admin, filters=private & admin))
    app.add_handler(CommandHandler(["рассылка", "post", "broadcast"], broadcast_start, filters=private & admin))
    app.add_handler(CommandHandler("grant", grant_cmd, filters=private & admin))
    app.add_handler(CommandHandler("revoke", revoke_cmd, filters=private & admin))
    app.add_handler(CommandHandler("msg", msg_cmd, filters=private & admin))
    app.add_handler(CommandHandler("chatid", chatid_cmd, filters=admin))
    app.add_handler(CallbackQueryHandler(button_handler))
    # Пересланный пост из канала — показать ID (только когда НЕ идёт рассылка)
    app.add_handler(MessageHandler(private & admin & filters.FORWARDED, forwarded_or_broadcast))
    # Любое сообщение админа (контент рассылки, если она идёт, иначе обычный ответ)
    app.add_handler(MessageHandler(private & admin & ~filters.COMMAND, broadcast_catch))
    app.add_handler(MessageHandler(private & filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущен...")
    app.run_polling()


if __name__ == "__main__":
    main()
