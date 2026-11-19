import asyncio
import hashlib
import json
import logging
import os
import re
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
BOT_USERNAME = "vladmorozov_bot"  # имя бота для кнопок-ссылок в рассылке (без @)
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

# Lava.top — зарубежные карты. Автосписания целиком на стороне Lava, бот только реагирует на вебхуки.
LAVA_API_KEY = os.environ.get("LAVA_API_KEY", "").strip()
LAVA_OFFER_ID = os.environ.get("LAVA_OFFER_ID", "").strip()
LAVA_WEBHOOK_SECRET = os.environ.get("LAVA_WEBHOOK_SECRET", "").strip()
LAVA_CURRENCY = os.environ.get("LAVA_CURRENCY", "USD").strip()
LAVA_PAYMENT_METHOD = os.environ.get("LAVA_PAYMENT_METHOD", "").strip()  # пусто = Lava сама покажет способы
LAVA_INVOICE_URL = "https://gate.lava.top/api/v2/invoice"
LAVA_PRODUCTS_URL = "https://gate.lava.top/api/v2/products"
LAVA_ENABLED = bool(LAVA_API_KEY and LAVA_OFFER_ID)
# Новые продажи через Lava. Пока выключены (проблема с выводом денег) — новые зарубежные клиенты платят
# переводом на Freedom. Продления СТАРЫХ подписчиков Lava бот принимает всегда (вебхук работает).
# Включить обратно: в Railway → Variables добавить LAVA_NEW_SALES = 1
LAVA_NEW_SALES = os.environ.get("LAVA_NEW_SALES", "0").strip() == "1"

# Клуб: ID закрытого канала и чата комментариев (узнаём через бота, см. /help)
CLUB_CHANNEL_ID = os.environ.get("CLUB_CHANNEL_ID", "").strip()
CLUB_CHAT_ID = os.environ.get("CLUB_CHAT_ID", "").strip()

WEB_PORT = 8080  # тот же порт, что указан в Railway → Networking

# ===== ТАРИФЫ =====
# Цены для НОВЫХ подписчиков. Кто уже подписан с автопродлением — продлевается по своей прежней цене
# (бот берёт сумму из первого платежа человека, см. charge_recurring).
TARIFFS = {
    "m": {"price": 9000, "usd": 115, "days": 30, "lava_period": "MONTHLY",
          "name": "месяц", "per": "в месяц", "every": "каждые 30 дней"},
    "y": {"price": 79000, "usd": 950, "days": 365, "lava_period": "PERIOD_YEAR",
          "name": "год", "per": "за год", "every": "раз в год (каждые 365 дней)"},
}
PERIOD_DAYS = 30            # срок по умолчанию (ручная выдача /grant и старые подписки)
LEGACY_PRICE = 3000         # цена старых подписок, у которых сумма не записана


def rub(n):
    """9000 -> «9 000»"""
    return f"{int(n):,}".replace(",", " ")


def year_saving():
    m, y = TARIFFS["m"], TARIFFS["y"]
    return m["price"] * 12 - y["price"], m["usd"] * 12 - y["usd"]


def describe(days):
    return f"Подписка на Клуб «СВОИ», {days} дней"


def receipt_item(days):
    return f"Информационно-консультационные услуги (подписка на Клуб «СВОИ», {days} дней)"

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
        db("""
            CREATE TABLE IF NOT EXISTS consents (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                text TEXT NOT NULL,
                agreed_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        # Счета Lava: связь контракта Lava с пользователем Telegram
        db("""
            CREATE TABLE IF NOT EXISTS lava_invoices (
                contract_id TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                email TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        # Кто провайдер подписки: rk (Робокасса/ручная) или lava
        db("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS provider TEXT DEFAULT 'rk'")
        # Тарифы: срок каждого платежа, цена и период подписки (у старых подписок — пусто = 3000 ₽ / 30 дней)
        db("ALTER TABLE payments ADD COLUMN IF NOT EXISTS days INTEGER DEFAULT 30")
        db("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS price NUMERIC")
        db("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS period_days INTEGER")
        db("ALTER TABLE lava_invoices ADD COLUMN IF NOT EXISTS days INTEGER DEFAULT 30")
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
            "SELECT status, paid_until, auto_renew, first_inv_id, "
            "COALESCE(provider, 'rk'), price, period_days FROM subscriptions WHERE user_id = %s",
            (user_id,), fetch="one",
        )
    except Exception as e:
        print(f"DB error (get_sub): {e}")
        return None
    if not row:
        return None
    return {"status": row[0], "paid_until": row[1], "auto_renew": row[2],
            "first_inv_id": row[3], "provider": row[4],
            "price": row[5], "period_days": row[6]}


def sub_terms(sub):
    """Цена и срок, по которым продлевается подписка человека (старые — по старой цене)."""
    days = (sub or {}).get("period_days") or PERIOD_DAYS
    price = (sub or {}).get("price")
    if not price and sub and sub.get("first_inv_id") and sub["first_inv_id"] > 0:
        try:
            row = db("SELECT amount FROM payments WHERE inv_id = %s", (sub["first_inv_id"],), fetch="one")
            price = row[0] if row else None
        except Exception as e:
            log.error("sub_terms: %s", e)
    return int(price or LEGACY_PRICE), int(days)


def is_active(user_id):
    sub = get_sub(user_id)
    return bool(sub and sub["status"] == "active")


# ===== РОБОКАССА =====
def rk_passwords(test):
    return (RK_TEST_PASS1, RK_TEST_PASS2) if test else (RK_PASS1, RK_PASS2)


def md5(s):
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def receipt_encoded(amount, days):
    """Состав чека (номенклатура) для Робочеков СМЗ, в виде, нужном для подписи."""
    receipt = {
        "items": [{
            "name": receipt_item(days),
            "quantity": 1,
            "sum": int(amount),
            "payment_method": "full_payment",
            "payment_object": "service",
            "tax": "none",
        }]
    }
    return quote(json.dumps(receipt, ensure_ascii=False, separators=(",", ":")), safe="")


def create_payment(user_id, kind, amount, days, parent_inv_id=None):
    row = db(
        "INSERT INTO payments (user_id, amount, kind, parent_inv_id, is_test, days) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING inv_id",
        (user_id, amount, kind, parent_inv_id, RK_TEST, days), fetch="one",
    )
    return row[0]


def payment_link(user_id, tariff="m", amount=None, days=None):
    """Создаёт платёж в базе и возвращает ссылку на страницу оплаты Робокассы.
    По умолчанию — цена тарифа; amount/days передаём, когда продлевает старый подписчик."""
    t = TARIFFS.get(tariff, TARIFFS["m"])
    amount = int(amount or t["price"])
    days = int(days or t["days"])
    out_sum = f"{amount}.00"
    inv_id = create_payment(user_id, "initial", amount, days)
    pass1, _ = rk_passwords(RK_TEST)
    receipt = receipt_encoded(amount, days)
    signature = md5(f"{RK_LOGIN}:{out_sum}:{inv_id}:{receipt}:{pass1}")
    params = {
        "MerchantLogin": RK_LOGIN,
        "OutSum": out_sum,
        "InvId": inv_id,
        "Description": describe(days),
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
    """Автосписание за следующий период — по цене и сроку самого человека
    (старые подписчики остаются на прежней цене). Результат придёт на Result URL."""
    amount, days = sub_terms(get_sub(user_id))
    out_sum = f"{amount}.00"
    inv_id = create_payment(user_id, "recurring", amount, days, parent_inv_id=first_inv_id)
    pass1, _ = rk_passwords(False)
    receipt = receipt_encoded(amount, days)
    data = {
        "MerchantLogin": RK_LOGIN,
        "InvoiceID": inv_id,
        "PreviousInvoiceID": first_inv_id,
        "OutSum": out_sum,
        "Description": describe(days),
        "Receipt": receipt,
        "SignatureValue": md5(f"{RK_LOGIN}:{out_sum}:{inv_id}:{receipt}:{pass1}"),
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(RK_RECURRING_URL, data=data)
    log.info("Recurring %s for user %s: %s %s", inv_id, user_id, resp.status_code, resp.text[:200])
    return resp.text


# ===== LAVA.TOP (зарубежные карты) =====
async def lava_create_invoice(user_id, email, tariff="m"):
    """Создаёт счёт в Lava и возвращает ссылку на оплату (или None).
    Контракт запоминаем в lava_invoices, чтобы по вебхуку понять, кого впускать и на сколько дней."""
    if not LAVA_ENABLED:
        return None
    t = TARIFFS.get(tariff, TARIFFS["m"])
    body = {
        "email": email,
        "offerId": LAVA_OFFER_ID,
        "periodicity": t["lava_period"],
        "currency": LAVA_CURRENCY,
        "buyerLanguage": "RU",
    }
    if LAVA_PAYMENT_METHOD:
        body["paymentMethod"] = LAVA_PAYMENT_METHOD
    headers = {"X-Api-Key": LAVA_API_KEY, "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(LAVA_INVOICE_URL, json=body, headers=headers)
        log.info("Lava invoice for %s: %s %s", user_id, resp.status_code, resp.text[:300])
        if resp.status_code not in (200, 201):
            return None
        data = resp.json()
        contract_id = data.get("id")
        pay_url = data.get("paymentUrl")
        if not contract_id or not pay_url:
            return None
        db("INSERT INTO lava_invoices (contract_id, user_id, email, days) VALUES (%s, %s, %s, %s) "
           "ON CONFLICT (contract_id) DO NOTHING", (str(contract_id), user_id, email, t["days"]))
        return pay_url
    except Exception as e:
        log.error("lava_create_invoice: %s", e)
        return None


async def lava_list_offers():
    """Возвращает текст со списком продуктов и offerId (для команды /lavaoffers)."""
    if not LAVA_API_KEY:
        return "LAVA_API_KEY не задан."
    headers = {"X-Api-Key": LAVA_API_KEY}
    # feedVisibility=ALL — показать и скрытые; showAllSubscriptionPeriods — все периоды подписок
    params = {"feedVisibility": "ALL", "showAllSubscriptionPeriods": "true"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(LAVA_PRODUCTS_URL, headers=headers, params=params)
        if resp.status_code != 200:
            return f"Ошибка {resp.status_code}: {resp.text[:300]}"
        items = resp.json().get("items", [])
        lines = []
        for it in items:
            d = it.get("data", {})
            title = d.get("title", "—")
            ptype = d.get("type", "")
            for off in d.get("offers", []) or []:
                prices = off.get("prices", []) or []
                pr = ", ".join(
                    f"{p.get('amount')} {p.get('currency')}/{p.get('periodicity')}" for p in prices
                ) or "—"
                lines.append(
                    f"• <b>{title}</b> ({ptype})\n  offerId: <code>{off.get('id')}</code>\n  цены: {pr}"
                )
        if lines:
            return "📦 <b>Продукты Lava</b>\n\n" + "\n\n".join(lines)
        raw = json.dumps(items, ensure_ascii=False)[:1200]
        return ("Офферы не распознаны. Вот что вернула Lava (покажи это Клоду):\n\n"
                f"<code>{raw}</code>")
    except Exception as e:
        return f"Ошибка запроса к Lava: {e}"


def lava_find_user(contract_id, parent_contract_id):
    """Находит Telegram ID и срок тарифа (дней) по контракту Lava (первый платёж или продление)."""
    for cid in (contract_id, parent_contract_id):
        if not cid:
            continue
        row = db("SELECT user_id, COALESCE(days, 30) FROM lava_invoices WHERE contract_id = %s",
                 (str(cid),), fetch="one")
        if row:
            return row[0], row[1]
    return None, PERIOD_DAYS


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


def extend_subscription(user_id, first_inv_id=None, days=PERIOD_DAYS, auto_renew=None, provider=None,
                        price=None, period_days=None):
    """Продлевает подписку от текущей даты окончания (или от сегодня).
    price/period_days — запоминаем тариф (передаём при первой оплате)."""
    sub = get_sub(user_id)
    start = now_utc()
    if sub and sub["status"] == "active" and sub["paid_until"] > start:
        start = sub["paid_until"]
    paid_until = start + timedelta(days=days)
    if sub:
        db(
            "UPDATE subscriptions SET status = 'active', paid_until = %s, "
            "auto_renew = COALESCE(%s, auto_renew), first_inv_id = COALESCE(%s, first_inv_id), "
            "provider = COALESCE(%s, provider), price = COALESCE(%s, price), "
            "period_days = COALESCE(%s, period_days) "
            "WHERE user_id = %s",
            (paid_until, auto_renew, first_inv_id, provider, price, period_days, user_id),
        )
    else:
        db(
            "INSERT INTO subscriptions (user_id, status, paid_until, auto_renew, first_inv_id, provider, "
            "price, period_days) VALUES (%s, 'active', %s, %s, %s, %s, %s, %s)",
            (user_id, paid_until, bool(auto_renew), first_inv_id, provider or 'rk', price, period_days),
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

    row = db("SELECT user_id, kind, status, parent_inv_id, amount, COALESCE(days, 30) "
             "FROM payments WHERE inv_id = %s", (int(inv_id),), fetch="one")
    if not row:
        return web.Response(text="unknown invoice", status=404)
    user_id, kind, status, parent_inv_id, amount, days = row

    if status == "paid":  # повторное уведомление — просто подтверждаем
        return web.Response(text=f"OK{inv_id}")

    try:
        # сверяем с суммой именно этого счёта (у старых подписчиков она своя, прежняя)
        if float(out_sum) < float(amount):
            log.warning("Сумма меньше счёта: %s < %s", out_sum, amount)
            return web.Response(text="bad sum", status=400)
    except ValueError:
        return web.Response(text="bad sum", status=400)

    db("UPDATE payments SET status = 'paid', paid_at = now() WHERE inv_id = %s", (int(inv_id),))

    bot = request.app["bot"]
    was_active = is_active(user_id)
    if kind == "initial":
        paid_until = extend_subscription(
            user_id,
            days=days,
            first_inv_id=int(inv_id) if RK_RECURRING else None,
            auto_renew=True if RK_RECURRING else None,
            provider="rk",
            price=int(amount),
            period_days=days,
        )
    else:
        paid_until = extend_subscription(user_id, days=days)
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
    period = "год" if days >= 365 else f"{days} дней"
    await notify_admin_bot(
        bot,
        f"{label}{test_mark}: {rub(float(out_sum))} ₽ / {period}\nID: <code>{user_id}</code>\nДо: {fmt_date(paid_until)}",
    )
    return web.Response(text=f"OK{inv_id}")


async def health(request):
    return web.Response(text="ok")


# ===== ПРИЁМ УВЕДОМЛЕНИЙ ОТ LAVA (webhook) =====
async def lava_webhook(request):
    # Проверка ключа (Lava шлёт его в X-Api-Key, значение задаём в настройках вебхука = LAVA_WEBHOOK_SECRET)
    if LAVA_WEBHOOK_SECRET and request.headers.get("X-Api-Key", "") != LAVA_WEBHOOK_SECRET:
        log.warning("Lava webhook: неверный ключ")
        return web.Response(text="bad key", status=403)
    try:
        data = await request.json()
    except Exception:
        return web.Response(text="bad json", status=400)

    event = data.get("eventType") or data.get("event_type") or ""
    contract_id = data.get("contractId")
    parent_id = data.get("parentContractId")
    email = (data.get("buyer") or {}).get("email", "")
    status = data.get("status", "")
    log.info("Lava webhook: event=%s contract=%s parent=%s status=%s", event, contract_id, parent_id, status)

    user_id, lava_days = lava_find_user(contract_id, parent_id)
    if not user_id:
        log.warning("Lava webhook: не нашёл пользователя по контракту %s / %s (email=%s)",
                    contract_id, parent_id, email)
        await notify_admin_bot(request.app["bot"],
                               f"⚠️ Lava: оплата {email}, но пользователь не найден в базе. Событие: {event}")
        return web.Response(text="ok")

    bot = request.app["bot"]
    ev = event.lower()

    # Успех: первая оплата или продление
    if ev in ("payment.success", "subscription.recurring.payment.success") or status in ("completed", "subscription-active"):
        was_active = is_active(user_id)
        paid_until = extend_subscription(user_id, days=lava_days, auto_renew=True, provider="lava",
                                         period_days=lava_days)
        # на продлении привяжем дочерний контракт к тому же пользователю (на будущее)
        if contract_id:
            db("INSERT INTO lava_invoices (contract_id, user_id, email, days) VALUES (%s, %s, %s, %s) "
               "ON CONFLICT (contract_id) DO NOTHING", (str(contract_id), user_id, email, lava_days))
        try:
            await grant_access(bot, user_id, paid_until, first_payment=not was_active, auto_renew=True)
        except Exception as e:
            log.error("lava grant_access %s: %s", user_id, e)
        label = "💰 Новая оплата (Lava)" if not was_active else "🔁 Продление (Lava)"
        period = "год" if lava_days >= 365 else f"{lava_days} дней"
        await notify_admin_bot(bot, f"{label}, {period}: {email}\nID: <code>{user_id}</code>\nДо: {fmt_date(paid_until)}")
        return web.Response(text="ok")

    # Отмена подписки — доступ оставляем до конца оплаченного периода, дальше billing_loop удалит
    if ev == "subscription.cancelled" or data.get("subscription_cancelled"):
        db("UPDATE subscriptions SET auto_renew = FALSE WHERE user_id = %s", (user_id,))
        await notify_admin_bot(bot, f"⏸ Lava: отменил автопродление\nID: <code>{user_id}</code>")
        return web.Response(text="ok")

    # Возврат или чарджбэк — закрываем доступ сразу
    if ev in ("refund.success", "chargeback.initiated") or data.get("event_type") in ("refund.success", "chargeback.initiated"):
        db("UPDATE subscriptions SET status = 'expired', auto_renew = FALSE WHERE user_id = %s", (user_id,))
        await revoke_access(bot, user_id)
        await notify_admin_bot(bot, f"↩️ Lava: возврат/чарджбэк, доступ закрыт\nID: <code>{user_id}</code>")
        return web.Response(text="ok")

    # Неуспешное списание — Lava сама повторит, доступ не трогаем
    if ev == "subscription.recurring.payment.failed" or ev == "payment.failed":
        await notify_admin_bot(bot, f"⚠️ Lava: списание не прошло (Lava повторит)\nID: <code>{user_id}</code>")
        return web.Response(text="ok")

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


MSK = timedelta(hours=3)
CHARGE_FROM_HOUR, CHARGE_TO_HOUR = 10, 22   # автосписания только с 10:00 до 22:00 МСК (п. 2.4 оферты)
NOTICE_MARGIN = timedelta(hours=1)          # запас, чтобы напоминание точно пришло раньше 24 ч / 3 дней


def charge_window_open(now):
    return CHARGE_FROM_HOUR <= (now + MSK).hour < CHARGE_TO_HOUR


def charge_time(paid_until):
    """Когда реально пройдёт списание: в момент окончания периода, но не раньше 10:00 и не позже 22:00 МСК."""
    msk = paid_until + MSK
    if msk.hour < CHARGE_FROM_HOUR:
        msk = msk.replace(hour=CHARGE_FROM_HOUR, minute=0, second=0, microsecond=0)
    elif msk.hour >= CHARGE_TO_HOUR:
        msk = (msk + timedelta(days=1)).replace(hour=CHARGE_FROM_HOUR, minute=0, second=0, microsecond=0)
    return msk - MSK


async def billing_tick(bot):
    now = now_utc()

    # 1. Напоминание до окончания периода: не позднее чем за сутки (месяц) или за 3 дня (год)
    rows = db(
        "SELECT user_id, paid_until, auto_renew AND first_inv_id IS NOT NULL FROM subscriptions "
        "WHERE status = 'active' AND paid_until > %s AND paid_until <= %s "
        "AND COALESCE(provider, 'rk') <> 'lava' "
        "AND (reminded_for IS NULL OR reminded_for <> paid_until)",
        (now, now + timedelta(days=3) + NOTICE_MARGIN), fetch="all",
    )
    for user_id, paid_until, auto in rows:
        sub = get_sub(user_id)
        price, days = sub_terms(sub)
        notice = timedelta(days=3 if days >= 365 else 1) + NOTICE_MARGIN
        if paid_until > now + notice:
            continue  # для месячной подписки ещё рано
        db("UPDATE subscriptions SET reminded_for = paid_until WHERE user_id = %s", (user_id,))
        if user_id == ADMIN_ID:
            continue
        period = "следующий год" if days >= 365 else f"следующие {days} дней"
        try:
            if auto:
                await bot.send_message(
                    user_id,
                    f"🔔 {fmt_date(charge_time(paid_until))} подписка на Клуб «СВОИ» продлится автоматически: "
                    f"спишется {rub(price)} ₽ за {period}.\n\n"
                    "Отключить автопродление можно в разделе «👤 Моя подписка» (/start).",
                )
            elif sub and sub.get("price"):
                # платил картой РФ без автопродления — продление по его цене
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("💳 Продлить картой РФ / РБ",
                                          url=payment_link(user_id, amount=price, days=days))],
                    [InlineKeyboardButton("🌍 Продлить картой не РФ", callback_data="pay")],
                ])
                await bot.send_message(
                    user_id,
                    f"🔔 Подписка на Клуб «СВОИ» заканчивается {fmt_date(paid_until)}.\n\n"
                    f"Чтобы остаться в клубе, продлите её — {rub(price)} ₽ за {period} 👇",
                    reply_markup=kb,
                )
            else:
                # доступ выдан вручную (/grant) — показываем тарифы
                kb = InlineKeyboardMarkup([[InlineKeyboardButton("💳 Продлить подписку", callback_data="pay")]])
                await bot.send_message(
                    user_id,
                    f"🔔 Подписка на Клуб «СВОИ» заканчивается {fmt_date(paid_until)}.\n\n"
                    "Чтобы остаться в клубе, продлите её 👇",
                    reply_markup=kb,
                )
        except Exception as e:
            log.warning("reminder %s: %s", user_id, e)

    # 2. Автосписание, когда период закончился — только с 10:00 до 22:00 МСК (ночью ждём утра;
    #    доступ при этом не закрывается: закрытие ждёт сутки после окончания периода, см. шаг 3)
    rows = db(
        "SELECT user_id, first_inv_id FROM subscriptions WHERE status = 'active' AND auto_renew "
        "AND first_inv_id IS NOT NULL AND paid_until <= %s "
        "AND (last_charge_at IS NULL OR last_charge_at < paid_until)",
        (now,), fetch="all",
    ) if charge_window_open(now) else []
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

💰 <b>Стоимость — {m} ₽ (${mu}) в месяц или {y} ₽ (${yu}) за год.</b>""".format(
    m=rub(TARIFFS["m"]["price"]), mu=TARIFFS["m"]["usd"],
    y=rub(TARIFFS["y"]["price"]), yu=TARIFFS["y"]["usd"],
)

OFFER_URL = "https://telegra.ph/Publichnaya-oferta--Klub-SVOI-11-19"  # редакция от 19.11.2026

_SAVE_RUB, _SAVE_USD = year_saving()

PAYMENT_TEXT = f"""💳 <b>Тарифы клуба «СВОИ»</b>

📅 <b>Месяц</b> — {rub(TARIFFS['m']['price'])} ₽ (${TARIFFS['m']['usd']})
🗓 <b>Год</b> — {rub(TARIFFS['y']['price'])} ₽ (${TARIFFS['y']['usd']}) — выгода {rub(_SAVE_RUB)} ₽ (${_SAVE_USD})

Картой РФ / РБ — с автопродлением, отключить можно в любой момент.
{"Картой не РФ — тоже с автопродлением (через Lava)." if LAVA_NEW_SALES else "Картой не РФ — разовая оплата за выбранный срок."}

<i>Нажимая кнопку оплаты, вы принимаете условия <a href="{OFFER_URL}">публичной оферты</a>.</i>

⏳ Доступ в клуб откроется в течение нескольких минут после оплаты.

Выберите тариф и способ оплаты 👇"""


def pay_foreign_text(tariff):
    """Ручная оплата картой не РФ (если Lava не подключена)."""
    t = TARIFFS[tariff]
    return f"""🌍 <b>Оплата картой не РФ — ${t['usd']} / {t['name']}</b>
Разовый платёж за {t['days']} дней, без автопродления.

Freedom bank: <code>4002890062233725</code>
(нажмите на номер, чтобы скопировать)

После оплаты отправьте скриншот чека — откроем доступ в клуб в течение нескольких минут 👇"""


def _consent_body(tariff):
    t = TARIFFS[tariff]
    notice = "За 3 дня" if t["days"] >= 365 else "За день"
    return (
        f"💳 <b>Подписка {rub(t['price'])} ₽ / {t['name']}, с автопродлением</b>\n\n"
        f"Списывается автоматически {t['every']} по {rub(t['price'])} ₽. "
        f"{notice} до списания придёт напоминание. "
        "Отключить можно в любой момент в «👤 Моя подписка».\n\n"
        f"Условия — в <a href=\"{OFFER_URL}\">публичной оферте</a>."
    )


# Текст согласия на автосписания (показывается, когда включены рекуррентные платежи)
def consent_text(tariff):
    return (_consent_body(tariff) + "\n\nНажимая кнопку, вы подтверждаете:\n"
            "<b>«Я согласен на автоматические списания согласно условиям оферты».</b>")


def consent_done_text(tariff):
    return _consent_body(tariff) + "\n\n✅ Согласие зафиксировано. Нажмите «Оплатить» 👇"

CONSENT_LABEL = "Я согласен на автоматические списания согласно условиям оферты"


def tariff_from(callback_data):
    """'pay_foreign_y' -> 'y'. Старые кнопки без тарифа ('pay_foreign') -> месяц."""
    suffix = callback_data.rsplit("_", 1)[-1]
    return suffix if suffix in TARIFFS else "m"


# ===== КЛАВИАТУРЫ =====
def main_keyboard(user_id):
    first = (InlineKeyboardButton("👤 Моя подписка", callback_data="my_sub")
             if is_active(user_id) else
             InlineKeyboardButton("🔥 Оформить подписку", callback_data="pay"))
    return InlineKeyboardMarkup([
        [first],
        [InlineKeyboardButton("↗️ Служба заботы", url=f"https://t.me/{CONTACT}")],
    ])


def rf_button(user_id, tariff):
    t = TARIFFS[tariff]
    label = f"💳 {t['name'].capitalize()} · {rub(t['price'])} ₽ — карта РФ / РБ"
    # Когда включены автосписания — РФ-оплата идёт через шаг согласия (требование Робокассы)
    if RK_RECURRING:
        return InlineKeyboardButton(label, callback_data=f"pay_rf_consent_{tariff}")
    try:
        return InlineKeyboardButton(label, url=payment_link(user_id, tariff))
    except Exception as e:
        log.error("payment_link: %s", e)
        return InlineKeyboardButton(label, url=f"https://t.me/{CONTACT}")


def foreign_button(tariff):
    t = TARIFFS[tariff]
    return InlineKeyboardButton(f"🌍 {t['name'].capitalize()} · ${t['usd']} — карта не РФ",
                                callback_data=f"pay_foreign_{tariff}")


def payment_keyboard(user_id):
    return InlineKeyboardMarkup([
        [rf_button(user_id, "m")],
        [rf_button(user_id, "y")],
        [foreign_button("m")],
        [foreign_button("y")],
        [InlineKeyboardButton("↗️ Служба заботы", url=f"https://t.me/{CONTACT}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back")],
    ])


def consent_keyboard(tariff="m"):
    """Экран согласия: кнопка-согласие (callback — чтобы записать дату), оферта, назад."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Я согласен", callback_data=f"consent_ok_{tariff}")],
        [InlineKeyboardButton("📄 Оферта", url=OFFER_URL)],
        [InlineKeyboardButton("⬅️ Назад", callback_data="pay")],
    ])


def pay_now_keyboard(user_id, tariff="m"):
    """После согласия — кнопка оплаты (заменяет кнопку согласия на том же экране)."""
    try:
        pay_url = payment_link(user_id, tariff)
    except Exception as e:
        log.error("payment_link: %s", e)
        pay_url = f"https://t.me/{CONTACT}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Оплатить", url=pay_url)],
        [InlineKeyboardButton("📄 Оферта", url=OFFER_URL)],
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
    if sub.get("provider") == "lava":
        # Подписка через Lava — продлевает и управляет списаниями сама Lava
        text += "Оплата: зарубежная карта (Lava)\nАвтопродление: <b>включено</b>\n\n"
        text += "Чтобы отменить автопродление — напишите в Службу заботы."
        rows.append([InlineKeyboardButton("↗️ Служба заботы", url=f"https://t.me/{CONTACT}")])
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="back")])
        return text, InlineKeyboardMarkup(rows)
    price, days = sub_terms(sub)
    period = "год" if days >= 365 else f"{days} дней"
    if sub["first_inv_id"]:
        text += f"Тариф: <b>{rub(price)} ₽ / {period}</b>\n"
        if sub["auto_renew"]:
            text += "Автопродление: <b>включено</b>"
            rows.append([InlineKeyboardButton("⏸ Отключить автопродление", callback_data="renew_off")])
        else:
            text += "Автопродление: <b>выключено</b> — доступ закроется в дату окончания."
            rows.append([InlineKeyboardButton("▶️ Включить автопродление", callback_data="renew_on")])
    else:
        text += "Автопродления нет — перед окончанием пришлём напоминание."
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
    for k in ("bc_audience", "bc_stage", "bc_from_chat", "bc_message_id", "bc_buttons"):
        context.user_data.pop(k, None)
    # Пришёл по кнопке «Оформить подписку» из рассылки (?start=pay) — сразу на оплату
    if context.args and context.args[0] == "pay":
        await notify_admin(context, f"🔔 <b>Новый интерес к подписке!</b>\n\n{user_info(update.effective_user)}")
        await update.message.reply_text(
            PAYMENT_TEXT, parse_mode="HTML",
            reply_markup=payment_keyboard(user_id), disable_web_page_preview=True,
        )
        return
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
            for k in ("bc_audience", "bc_stage", "bc_from_chat", "bc_message_id", "bc_buttons"):
                context.user_data.pop(k, None)
            await query.message.reply_text("Рассылка отменена.")
        elif query.data.startswith("bc_aud_"):
            await broadcast_pick_audience(update, context, query.data.replace("bc_aud_", ""))
        elif query.data.startswith("bc_btn_"):
            await broadcast_confirm(update, context, query.data.replace("bc_btn_", ""))
        elif query.data == "bc_send":
            await broadcast_send(update, context)
        return

    if query.data == "pay":
        await notify_admin(context, f"🔔 <b>Новый интерес к подписке!</b>\n\n{user_info(user)}")
        await query.message.reply_text(
            PAYMENT_TEXT, parse_mode="HTML",
            reply_markup=payment_keyboard(user.id), disable_web_page_preview=True,
        )
    elif query.data.startswith("pay_rf_consent"):
        # Экран согласия на автосписания (только когда включены рекуррентные платежи)
        tariff = tariff_from(query.data)
        await query.message.reply_text(
            consent_text(tariff), parse_mode="HTML",
            reply_markup=consent_keyboard(tariff), disable_web_page_preview=True,
        )
    elif query.data.startswith("consent_ok"):
        # Фиксируем согласие с датой и МЕНЯЕМ ТОТ ЖЕ экран на кнопку оплаты
        tariff = tariff_from(query.data)
        t = TARIFFS[tariff]
        consent_record = f"{CONSENT_LABEL} (тариф: {rub(t['price'])} ₽ / {t['name']})"
        try:
            db("INSERT INTO consents (user_id, text) VALUES (%s, %s)", (user.id, consent_record))
        except Exception as e:
            log.error("consent save: %s", e)
        await notify_admin(context, f"✍️ Согласие на автосписание — {t['name']}\n\n{user_info(user)}")
        try:
            await query.edit_message_text(
                consent_done_text(tariff), parse_mode="HTML",
                reply_markup=pay_now_keyboard(user.id, tariff), disable_web_page_preview=True,
            )
        except Exception as e:
            # запасной путь, если сообщение не удалось отредактировать
            log.error("consent edit: %s", e)
            await query.message.reply_text(
                consent_done_text(tariff), parse_mode="HTML",
                reply_markup=pay_now_keyboard(user.id, tariff), disable_web_page_preview=True,
            )
    elif query.data.startswith("pay_foreign"):
        tariff = tariff_from(query.data)
        t = TARIFFS[tariff]
        if LAVA_ENABLED and LAVA_NEW_SALES:
            context.user_data["await_lava_email"] = True
            context.user_data["lava_tariff"] = tariff
            await query.message.reply_text(
                f"🌍 <b>Оплата картой не РФ — ${t['usd']} / {t['name']}</b>, с автопродлением.\n\n"
                "Для оплаты и чека укажите ваш <b>email</b> — пришлите его ответным сообщением 👇\n\n"
                f"Оплачивая, вы принимаете условия <a href=\"{OFFER_URL}\">публичной оферты</a>.",
                parse_mode="HTML", disable_web_page_preview=True,
            )
        else:
            await notify_admin(
                context,
                f"🔥 <b>Хочет оплатить картой не РФ ({t['name']}, ${t['usd']})!</b>\n\n{user_info(user)}\n\n"
                f"После скриншота выдай доступ: <code>/grant {user.id} {t['days']}</code>",
            )
            await query.message.reply_text(
                pay_foreign_text(tariff), parse_mode="HTML",
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
        "/post — рассылка с выбором группы (всем / не оплативших / ушедших / активных).\n"
        "Выбери кнопкой кому → пришли сообщение (текст, фото, видео, кружок, голосовое) → подтверди.\n"
        "Оформление (жирный, курсив, ссылки) сохраняется.\n\n"
        "/grant <i>ID дней</i> — выдать доступ вручную (оплата картой не РФ)\n"
        "Пример: <code>/grant 123456789 30</code>\n"
        "/revoke <i>ID</i> — закрыть доступ и удалить из клуба\n\n"
        "/msg <i>ID текст</i> — написать клиенту от имени бота (если у него нет username)\n\n"
        "🆔 Узнать ID канала: перешли сюда любой пост из канала.\n"
        "🆔 Узнать ID чата: напиши в чате <code>/chatid</code>\n\n"
        "/lavaoffers — показать продукты Lava и их offerId (для зарубежных карт)\n\n"
        f"Тарифы для новых: месяц {rub(TARIFFS['m']['price'])} ₽ / ${TARIFFS['m']['usd']}, "
        f"год {rub(TARIFFS['y']['price'])} ₽ / ${TARIFFS['y']['usd']}\n"
        f"Старые подписчики продлеваются по своей прежней цене.\n"
        f"Режим оплаты: {mode}\n"
        f"Автосписания (Робокасса): {'✅ включены' if RK_RECURRING else '⏸ выключены (продление по напоминанию)'}\n"
        f"Lava (зарубежные): {'✅ подключена' if LAVA_ENABLED else '❌ не задана (LAVA_API_KEY/LAVA_OFFER_ID)'}"
        f"{'' if LAVA_NEW_SALES else ' — новые продажи выключены, новые платят на Freedom; старые продлеваются'}\n"
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
        await update.message.reply_text("Используй: /grant ID дней\nПример: /grant 123456789 30 (месяц) или 365 (год)")
        return
    sub = get_sub(user_id)
    has_tariff = bool(sub and (sub.get("first_inv_id") or sub.get("price")))
    if has_tariff:
        # подарок дней тому, кто платил картой (с автосписанием или без): тариф и автопродление не трогаем
        paid_until = extend_subscription(user_id, days=days)
    else:
        paid_until = extend_subscription(user_id, days=days, auto_renew=False, period_days=days)
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


async def lavaoffers_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать продукты Lava и их offerId — чтобы вписать LAVA_OFFER_ID в Railway."""
    if not is_admin(update):
        return
    await update.message.reply_text("Запрашиваю продукты у Lava... ⏳")
    text = await lava_list_offers()
    await update.message.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)


async def preview_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать экран согласия для скриншота (не влияет на оплату)."""
    if not is_admin(update):
        return
    tariff = "y" if context.args and context.args[0] == "y" else "m"
    await update.message.reply_text(
        consent_text(tariff), parse_mode="HTML",
        reply_markup=consent_keyboard(tariff), disable_web_page_preview=True,
    )


async def demosub_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Создать себе демо-подписку с автопродлением для скриншота «Моя подписка». Убрать: /revoke <свой ID>."""
    if not is_admin(update):
        return
    uid = update.effective_user.id
    try:
        paid_until = extend_subscription(uid, first_inv_id=-1, days=PERIOD_DAYS, auto_renew=True)
        text, kb = my_sub_view(uid)
        await update.message.reply_text(
            "Демо-подписка создана для скриншота. Вот как выглядит «Моя подписка»:",
        )
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        await update.message.reply_text(f"Убрать после скриншота: <code>/revoke {uid}</code>", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")


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


def post_keyboard(kind):
    """Кнопки, которые добавятся под пост рассылки."""
    pay = InlineKeyboardButton("🔥 Оформить подписку", url=f"https://t.me/{BOT_USERNAME}?start=pay")
    menu = InlineKeyboardButton("📋 Главное меню", url=f"https://t.me/{BOT_USERNAME}?start=menu")
    if kind == "both":
        return InlineKeyboardMarkup([[pay], [menu]])
    if kind == "pay":
        return InlineKeyboardMarkup([[pay]])
    return None


async def broadcast_preview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг 3: получили сообщение — спрашиваем про кнопки под постом."""
    msg = update.message
    context.user_data["bc_from_chat"] = msg.chat_id
    context.user_data["bc_message_id"] = msg.message_id
    context.user_data["bc_stage"] = "buttons"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 Подписка + 📋 Меню", callback_data="bc_btn_both")],
        [InlineKeyboardButton("🔥 Только «Оформить подписку»", callback_data="bc_btn_pay")],
        [InlineKeyboardButton("Без кнопок", callback_data="bc_btn_none")],
        [InlineKeyboardButton("✖️ Отмена", callback_data="bc_cancel")],
    ])
    await msg.reply_text("Добавить кнопки под пост?", reply_markup=kb)


async def broadcast_confirm(update, context, btn_kind):
    """Шаг 4: показываем предпросмотр с кнопками и просим подтверждение."""
    context.user_data["bc_buttons"] = btn_kind
    context.user_data["bc_stage"] = "confirm"
    audience = context.user_data.get("bc_audience", "all")
    count = len(users_by_audience(audience))
    from_chat = context.user_data["bc_from_chat"]
    message_id = context.user_data["bc_message_id"]
    q = update.callback_query
    # Предпросмотр — точно с теми же кнопками, что уйдут людям
    await context.bot.copy_message(
        chat_id=q.message.chat_id, from_chat_id=from_chat, message_id=message_id,
        reply_markup=post_keyboard(btn_kind),
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✅ Отправить ({count})", callback_data="bc_send")],
        [InlineKeyboardButton("✖️ Отмена", callback_data="bc_cancel")],
    ])
    await q.message.reply_text(
        f"👆 Вот так увидят люди.\n\nГруппа: <b>{AUDIENCE_TITLES[audience]}</b> — {count} чел.\nОтправляем?",
        parse_mode="HTML", reply_markup=kb,
    )


async def broadcast_send(update, context):
    """Шаг 5: рассылаем, копируя исходное сообщение с выбранными кнопками."""
    audience = context.user_data.get("bc_audience", "all")
    from_chat = context.user_data.get("bc_from_chat")
    message_id = context.user_data.get("bc_message_id")
    btn_kind = context.user_data.get("bc_buttons", "none")
    users = users_by_audience(audience)
    q = update.callback_query
    await q.message.reply_text(f"Отправляю {len(users)} чел... ⏳")
    success = 0
    for uid in users:
        try:
            await context.bot.copy_message(
                chat_id=uid, from_chat_id=from_chat, message_id=message_id,
                reply_markup=post_keyboard(btn_kind),
            )
            success += 1
            await asyncio.sleep(0.05)  # бережём лимиты Telegram
        except Exception:
            pass
    for k in ("bc_audience", "bc_stage", "bc_from_chat", "bc_message_id", "bc_buttons"):
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


import re
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    add_user(user_id)

    # Ждём email для оплаты через Lava (зарубежные карты)
    if context.user_data.get("await_lava_email"):
        email = (update.message.text or "").strip()
        if not EMAIL_RE.match(email):
            await update.message.reply_text("Похоже, это не email. Пришлите адрес вида name@mail.com 👇")
            return
        context.user_data.pop("await_lava_email", None)
        tariff = context.user_data.pop("lava_tariff", "m")
        await update.message.reply_text("Создаю ссылку на оплату... ⏳")
        pay_url = await lava_create_invoice(user_id, email, tariff)
        if pay_url:
            t = TARIFFS[tariff]
            await notify_admin_bot(
                context.bot,
                f"🌍 Оформляет подписку через Lava ({t['name']}, ${t['usd']})\n"
                f"{user_info(update.effective_user)}\nEmail: {email}",
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 Оплатить", url=pay_url)],
                [InlineKeyboardButton("↗️ Служба заботы", url=f"https://t.me/{CONTACT}")],
            ])
            await update.message.reply_text(
                "Готово! Нажмите «Оплатить» — email уже подставлен, введите только данные карты.\n\n"
                "После оплаты доступ в клуб откроется автоматически 👇",
                reply_markup=kb,
            )
        else:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("↗️ Служба заботы", url=f"https://t.me/{CONTACT}")]])
            await update.message.reply_text(
                "Не удалось создать ссылку на оплату. Напишите в Службу заботы — поможем 👇",
                reply_markup=kb,
            )
        return

    await update.message.reply_text(
        "Нажми кнопку ниже 👇", reply_markup=main_keyboard(user_id)
    )


# ===== ЗАПУСК =====
async def on_startup(application: Application):
    webapp = web.Application()
    webapp["bot"] = application.bot
    webapp.router.add_post("/robokassa/result", robokassa_result)
    webapp.router.add_post("/lava/webhook", lava_webhook)
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
    app.add_handler(CommandHandler(["post", "broadcast"], broadcast_start, filters=private & admin))
    app.add_handler(CommandHandler("grant", grant_cmd, filters=private & admin))
    app.add_handler(CommandHandler("revoke", revoke_cmd, filters=private & admin))
    app.add_handler(CommandHandler("msg", msg_cmd, filters=private & admin))
    app.add_handler(CommandHandler("chatid", chatid_cmd, filters=admin))
    app.add_handler(CommandHandler("lavaoffers", lavaoffers_cmd, filters=private & admin))
    app.add_handler(CommandHandler("preview", preview_cmd, filters=private & admin))
    app.add_handler(CommandHandler("demosub", demosub_cmd, filters=private & admin))
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
