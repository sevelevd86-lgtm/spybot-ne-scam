import asyncio
import html
import logging
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from typing import Optional

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    ReplyKeyboardMarkup,
)

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------
# The hosting control panel must contain API_TOKEN=<token received from BotFather>.
API_TOKEN = os.getenv("API_TOKEN", "").strip()

# Only this Telegram account can open /admin and use every administrator action.
# Replace the number with your own Telegram ID before deployment.
ADMIN_ID = 5018476227

DB_NAME = os.getenv("DB_NAME", "business_monitor.db")
SETTINGS_URL = "tg://settings/edit"

PLANS = {
    "day": {"name": "1 день", "days": 1, "stars": 5},
    "week": {"name": "1 неделя", "days": 7, "stars": 25},
    "month": {"name": "1 месяц", "days": 30, "stars": 67},
    "6months": {"name": "6 месяцев", "days": 180, "stars": 360},
    "year": {"name": "1 год", "days": 365, "stars": 550},
}

# Built-in promo codes retained from the previous version.  The administrator
# can add additional codes from the control panel.
BUILTIN_PROMOS = {
    "N1": {"promo_type": "days", "value": 7, "max_uses": 25},
    "DAVE100": {"promo_type": "forever", "value": 0, "max_uses": 0},
    "MET200$": {"promo_type": "discount", "value": 10, "max_uses": 0},
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

if not API_TOKEN:
    raise RuntimeError("Set API_TOKEN in the hosting environment before starting the bot.")

bot = Bot(API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Username is received from Telegram at startup. Do not hard-code a bot username.
bot_mention = "бота"
admin_states: dict[int, dict] = {}


# ---------------------------------------------------------------------------
# DATABASE
# ---------------------------------------------------------------------------
def db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_NAME, timeout=30, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection


def now_ts() -> int:
    return int(time.time())


def init_db() -> None:
    with db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                premium_until INTEGER,
                premium_forever INTEGER NOT NULL DEFAULT 0,
                purchases_count INTEGER NOT NULL DEFAULT 0,
                stars_spent INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS business_connections (
                business_connection_id TEXT PRIMARY KEY,
                user_chat_id INTEGER NOT NULL,
                is_enabled INTEGER NOT NULL DEFAULT 1,
                premium_offer_sent INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS saved_messages (
                business_connection_id TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                from_user_id INTEGER,
                from_username TEXT,
                from_first_name TEXT,
                text TEXT,
                caption TEXT,
                message_type TEXT NOT NULL,
                photo_file_id TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (business_connection_id, chat_id, message_id)
            );

            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                payload TEXT NOT NULL UNIQUE,
                plan TEXT NOT NULL,
                stars INTEGER NOT NULL,
                telegram_payment_charge_id TEXT UNIQUE,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS promo_codes (
                code TEXT PRIMARY KEY,
                promo_type TEXT NOT NULL CHECK(promo_type IN ('days', 'forever', 'discount')),
                value INTEGER NOT NULL DEFAULT 0,
                max_uses INTEGER NOT NULL DEFAULT 0,
                uses INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS promo_uses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                promo_code TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                UNIQUE(user_id, promo_code)
            );
        """)
        # Existing installations used the same connections table without this
        # column. Migrate it in place instead of requiring a new database.
        connection_columns = {row["name"] for row in conn.execute("PRAGMA table_info(business_connections)")}
        if "premium_offer_sent" not in connection_columns:
            conn.execute("ALTER TABLE business_connections ADD COLUMN premium_offer_sent INTEGER NOT NULL DEFAULT 0")

        # Preserve messages collected by the previous version when its archive
        # table exists, so pending deletion events can still be shown.
        old_messages = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'messages'").fetchone()
        if old_messages:
            conn.execute("""
                INSERT OR IGNORE INTO saved_messages (
                    business_connection_id, chat_id, message_id, from_user_id,
                    from_username, from_first_name, text, caption, message_type,
                    photo_file_id, created_at, updated_at
                )
                SELECT business_connection_id, chat_id, message_id, from_user_id,
                    from_username, from_first_name, text, caption, message_type,
                    photo_file_id, created_at, updated_at
                FROM messages
            """)


def ensure_user(user_id: int, username: Optional[str] = None, first_name: Optional[str] = None) -> None:
    ts = now_ts()
    with db() as conn:
        conn.execute("""
            INSERT INTO users (user_id, username, first_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = COALESCE(excluded.username, users.username),
                first_name = COALESCE(excluded.first_name, users.first_name),
                updated_at = excluded.updated_at
        """, (user_id, username, first_name, ts, ts))


def get_user(user_id: int):
    with db() as conn:
        return conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()


def has_premium(user_id: int) -> bool:
    user = get_user(user_id)
    return bool(user and (user["premium_forever"] or (user["premium_until"] or 0) > now_ts()))


def premium_expiry(user_id: int) -> Optional[int]:
    user = get_user(user_id)
    if not user or user["premium_forever"]:
        return None
    return user["premium_until"]


def grant_premium(user_id: int, days: int = 0, forever: bool = False) -> None:
    ensure_user(user_id)
    with db() as conn:
        if forever:
            conn.execute("UPDATE users SET premium_forever = 1, premium_until = NULL, updated_at = ? WHERE user_id = ?", (now_ts(), user_id))
            return
        row = conn.execute("SELECT premium_until FROM users WHERE user_id = ?", (user_id,)).fetchone()
        until = max(int(row["premium_until"] or 0), now_ts()) + days * 86400
        conn.execute("UPDATE users SET premium_forever = 0, premium_until = ?, updated_at = ? WHERE user_id = ?", (until, now_ts(), user_id))


def revoke_premium(user_id: int) -> None:
    with db() as conn:
        conn.execute("UPDATE users SET premium_forever = 0, premium_until = NULL, updated_at = ? WHERE user_id = ?", (now_ts(), user_id))


# ---------------------------------------------------------------------------
# PROMO CODES
# ---------------------------------------------------------------------------
def user_discount(user_id: int) -> int:
    """Return the largest activated discount; 0 means no discount."""
    with db() as conn:
        custom = conn.execute("""
            SELECT MAX(pc.value) AS discount
            FROM promo_uses pu JOIN promo_codes pc ON pc.code = pu.promo_code
            WHERE pu.user_id = ? AND pc.promo_type = 'discount'
        """, (user_id,)).fetchone()["discount"]
        builtin = conn.execute("SELECT 1 FROM promo_uses WHERE user_id = ? AND promo_code = 'MET200$'", (user_id,)).fetchone()
    return max(int(custom or 0), 10 if builtin else 0)


def promo_usage_count(code: str) -> int:
    with db() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM promo_uses WHERE promo_code = ?", (code,)).fetchone()[0])


def activate_promo(user_id: int, raw_code: str) -> tuple[bool, str]:
    code = raw_code.strip().upper()
    if not code:
        return False, "❌ Введите промокод."
    builtin = BUILTIN_PROMOS.get(code)
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        already = conn.execute("SELECT 1 FROM promo_uses WHERE user_id = ? AND promo_code = ?", (user_id, code)).fetchone()
        if already:
            return False, "❌ Этот промокод уже был использован."
        if builtin:
            promo_type, value, max_uses = builtin["promo_type"], builtin["value"], builtin["max_uses"]
            uses = int(conn.execute("SELECT COUNT(*) FROM promo_uses WHERE promo_code = ?", (code,)).fetchone()[0])
        else:
            promo = conn.execute("SELECT * FROM promo_codes WHERE code = ? AND is_active = 1", (code,)).fetchone()
            if not promo:
                return False, "❌ Такого активного промокода нет."
            promo_type, value, max_uses, uses = promo["promo_type"], int(promo["value"]), int(promo["max_uses"]), int(promo["uses"])
        if max_uses > 0 and uses >= max_uses:
            return False, "❌ Лимит активаций этого промокода исчерпан."
        conn.execute("INSERT INTO promo_uses (user_id, promo_code, created_at) VALUES (?, ?, ?)", (user_id, code, now_ts()))
        if not builtin:
            conn.execute("UPDATE promo_codes SET uses = uses + 1 WHERE code = ?", (code,))
    if promo_type == "days":
        grant_premium(user_id, days=value)
        return True, f"🎁 <b>Промокод активирован!</b>\n\nPremium выдан на <b>{value}</b> дн."
    if promo_type == "forever":
        grant_premium(user_id, forever=True)
        return True, "🎁 <b>Промокод активирован!</b>\n\n♾ Premium выдан навсегда."
    return True, f"🎁 <b>Промокод активирован!</b>\n\n🔥 Скидка на Premium: <b>{value}%</b>."


def create_promo(code: str, promo_type: str, value: int, max_uses: int) -> tuple[bool, str]:
    code = code.strip().upper()
    valid, reason = validate_promo_code(code, check_database=False)
    if not valid:
        return False, reason
    if promo_type not in {"days", "forever", "discount"}:
        return False, "Неизвестный тип промокода."
    with db() as conn:
        try:
            conn.execute("""
                INSERT INTO promo_codes (code, promo_type, value, max_uses, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (code, promo_type, value, max_uses, now_ts()))
        except sqlite3.IntegrityError:
            return False, "Такой промокод уже существует."
    return True, "Промокод создан."


def validate_promo_code(code: str, check_database: bool = True) -> tuple[bool, str]:
    if len(code) < 2 or len(code) > 64 or not code.replace("_", "").replace("-", "").isalnum():
        return False, "Код должен состоять из 2–64 букв, цифр, «_» или «-»."
    if code in BUILTIN_PROMOS:
        return False, "Этот встроенный промокод уже существует."
    if check_database:
        with db() as conn:
            exists = conn.execute("SELECT 1 FROM promo_codes WHERE code = ?", (code,)).fetchone()
        if exists:
            return False, "Такой промокод уже существует."
    return True, ""


def calculate_price(user_id: int, plan_id: str) -> int:
    plan = PLANS[plan_id]
    discount = user_discount(user_id)
    return max(1, round(plan["stars"] * (100 - discount) / 100))


# ---------------------------------------------------------------------------
# BUSINESS CONNECTIONS AND MESSAGE ARCHIVE
# ---------------------------------------------------------------------------
def save_connection(connection: types.BusinessConnection) -> None:
    ts = now_ts()
    with db() as conn:
        conn.execute("""
            INSERT INTO business_connections (business_connection_id, user_chat_id, is_enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(business_connection_id) DO UPDATE SET
                user_chat_id = excluded.user_chat_id,
                is_enabled = excluded.is_enabled,
                updated_at = excluded.updated_at
        """, (connection.id, connection.user.id, int(bool(connection.is_enabled)), ts, ts))
    ensure_user(connection.user.id, connection.user.username, connection.user.first_name)


async def resolve_connection(connection_id: str):
    with db() as conn:
        saved = conn.execute("SELECT * FROM business_connections WHERE business_connection_id = ?", (connection_id,)).fetchone()
    if saved:
        return saved
    try:
        connection = await bot.get_business_connection(business_connection_id=connection_id)
    except Exception:
        logger.exception("Cannot resolve business connection %s", connection_id)
        return None
    save_connection(connection)
    with db() as conn:
        return conn.execute("SELECT * FROM business_connections WHERE business_connection_id = ?", (connection_id,)).fetchone()


def message_type(message: Message) -> str:
    for attr in ("text", "photo", "video", "document", "audio", "voice", "sticker", "animation", "video_note", "contact", "location", "poll"):
        if getattr(message, attr, None):
            return attr
    return "other"


def save_message(message: Message) -> None:
    if not message.business_connection_id:
        return
    sender = message.from_user
    photo_id = message.photo[-1].file_id if message.photo else None
    ts = now_ts()
    with db() as conn:
        conn.execute("""
            INSERT INTO saved_messages (
                business_connection_id, chat_id, message_id, from_user_id, from_username,
                from_first_name, text, caption, message_type, photo_file_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(business_connection_id, chat_id, message_id) DO UPDATE SET
                from_user_id = excluded.from_user_id,
                from_username = excluded.from_username,
                from_first_name = excluded.from_first_name,
                text = excluded.text, caption = excluded.caption,
                message_type = excluded.message_type, photo_file_id = excluded.photo_file_id,
                updated_at = excluded.updated_at
        """, (
            message.business_connection_id, message.chat.id, message.message_id,
            sender.id if sender else None, sender.username if sender else None,
            sender.first_name if sender else None, message.text, message.caption,
            message_type(message), photo_id, ts, ts,
        ))


def get_saved_message(connection_id: str, chat_id: int, message_id: int):
    with db() as conn:
        return conn.execute("""
            SELECT * FROM saved_messages
            WHERE business_connection_id = ? AND chat_id = ? AND message_id = ?
        """, (connection_id, chat_id, message_id)).fetchone()


def esc(value: object) -> str:
    return html.escape(str(value or ""))


def truncate(value: str, limit: int = 3500) -> str:
    return value if len(value) <= limit else value[:limit - 1] + "…"


def sender_from_row(row: sqlite3.Row) -> str:
    if row["from_username"]:
        return f"@{esc(row['from_username'])}"
    if row["from_first_name"]:
        return esc(row["from_first_name"])
    return f"<code>{row['from_user_id']}</code>" if row["from_user_id"] else "Неизвестно"


def sender_from_message(message: Message) -> str:
    user = message.from_user
    if not user:
        return "Неизвестно"
    return f"@{esc(user.username)}" if user.username else esc(user.first_name or user.id)


async def notify_deleted(owner_id: int, row: sqlite3.Row) -> None:
    header = f"🗑 <b>СООБЩЕНИЕ УДАЛЕНО</b>\n\n👤 От: {sender_from_row(row)}"
    content = row["text"] or row["caption"] or ""
    if row["message_type"] == "photo" and row["photo_file_id"]:
        caption = truncate(header + (f"\n\n{esc(content)}" if content else ""), 1024)
        await bot.send_photo(owner_id, row["photo_file_id"], caption=caption)
        return
    text = header + f"\n📎 Тип: <code>{esc(row['message_type'])}</code>"
    if content:
        text += f"\n\n{esc(content)}"
    await bot.send_message(owner_id, truncate(text))


async def notify_edited(owner_id: int, old: sqlite3.Row, new: Message) -> None:
    before = old["text"] or old["caption"] or ""
    after = new.text or new.caption or ""
    if before == after:
        return
    text = (
        "✏️ <b>СООБЩЕНИЕ ИЗМЕНЕНО</b>\n\n"
        f"👤 От: {sender_from_message(new)}\n\n"
        f"<b>Было:</b>\n{esc(before) if before else '—'}\n\n"
        f"<b>Стало:</b>\n{esc(after) if after else '—'}"
    )
    await bot.send_message(owner_id, truncate(text))


# ---------------------------------------------------------------------------
# USER INTERFACE AND PAYMENTS
# ---------------------------------------------------------------------------
def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⭐ Premium"), KeyboardButton(text="👤 Профиль")],
            [KeyboardButton(text="🎟 Промокод"), KeyboardButton(text="🔗 Подключение")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def premium_keyboard(user_id: int) -> InlineKeyboardMarkup:
    discount = user_discount(user_id)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{plan['name']} — ⭐{calculate_price(user_id, key)}" + (" 🔥" if discount else ""),
            callback_data=f"buy:{key}",
        )]
        for key, plan in PLANS.items()
    ])


async def send_premium_offer(user_id: int) -> None:
    discount = user_discount(user_id)
    discount_text = f"\n\n🔥 Ваша скидка: <b>{discount}%</b>." if discount else ""
    await bot.send_message(
        user_id,
        "⭐ <b>Premium нужен для мониторинга</b>\n\n"
        "Подключение Business активно, но уведомления об удалённых и изменённых сообщениях доступны только с Premium.\n\n"
        "Выбери срок подписки:" + discount_text,
        reply_markup=premium_keyboard(user_id),
    )


def has_enabled_connection(user_id: int) -> bool:
    with db() as conn:
        return conn.execute("SELECT 1 FROM business_connections WHERE user_chat_id = ? AND is_enabled = 1 LIMIT 1", (user_id,)).fetchone() is not None


async def offer_once_for_connection(connection_id: str, user_id: int) -> None:
    if has_premium(user_id):
        return
    with db() as conn:
        row = conn.execute("SELECT premium_offer_sent FROM business_connections WHERE business_connection_id = ?", (connection_id,)).fetchone()
        if not row or row["premium_offer_sent"]:
            return
        conn.execute("UPDATE business_connections SET premium_offer_sent = 1, updated_at = ? WHERE business_connection_id = ?", (now_ts(), connection_id))
    try:
        await send_premium_offer(user_id)
    except Exception:
        logger.info("Premium offer cannot be delivered to %s yet", user_id)


@dp.message(CommandStart())
async def start_handler(message: Message) -> None:
    user = message.from_user
    ensure_user(user.id, user.username, user.first_name)
    await message.answer(
        "🐻‍❄️ <b>SpyNeScamBot</b>\n\n"
        "Бот для мониторинга сообщений подключённого Telegram Business.\n\n"
        "Он сохраняет новые сообщения, чтобы затем показать только их изменения или удаление. "
        "Уведомлений о новых сообщениях нет.\n\n"
        "Выберите нужный раздел ниже.",
        reply_markup=main_keyboard(),
    )
    if has_enabled_connection(user.id) and not has_premium(user.id):
        await send_premium_offer(user.id)


@dp.message(F.text == "⭐ Premium")
async def premium_handler(message: Message) -> None:
    ensure_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    discount = user_discount(message.from_user.id)
    await message.answer(
        "⭐ <b>PREMIUM</b>\n\n"
        "Premium открывает мониторинг Business-сообщений.\n\n"
        "Вы получите уведомления о:\n"
        "✏️ изменённых сообщениях\n"
        "🗑 удалённых сообщениях\n"
        "📷 удалённых фотографиях\n\n"
        + (f"🔥 <b>У вас активна скидка {discount}%!</b>\n\n" if discount else "")
        + "Выберите срок подписки:",
        reply_markup=premium_keyboard(message.from_user.id),
    )


@dp.message(F.text == "👤 Профиль")
async def profile_handler(message: Message) -> None:
    ensure_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    user = get_user(message.from_user.id)
    if user["premium_forever"]:
        status = "♾ Навсегда"
    elif has_premium(message.from_user.id):
        status = "⭐ До " + datetime.fromtimestamp(user["premium_until"], timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    else:
        status = "❌ Не активен"
    discount = user_discount(message.from_user.id)
    await message.answer(
        f"👤 <b>ПРОФИЛЬ</b>\n\n🆔 <code>{message.from_user.id}</code>\n"
        f"⭐ Premium: {status}\n🎟 Скидка: {f'{discount}%' if discount else 'нет'}\n\n"
        f"🛒 Покупок: <b>{user['purchases_count']}</b>\n⭐ Потрачено Stars: <b>{user['stars_spent']}</b>",
        reply_markup=main_keyboard(),
    )


@dp.message(F.text == "🎟 Промокод")
async def promo_handler(message: Message) -> None:
    ensure_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    admin_states[message.from_user.id] = {"state": "user_promo"}
    await message.answer("🎟 <b>ПРОМОКОД</b>\n\nОтправьте промокод следующим сообщением.", reply_markup=main_keyboard())


@dp.message(F.text == "🔗 Подключение")
async def connection_help_handler(message: Message) -> None:
    await message.answer(
        "🔗 <b>Подключение Telegram Business</b>\n\n"
        "1️⃣ Откройте настройки Telegram.\n"
        "2️⃣ Откройте свой профиль.\n"
        "3️⃣ Нажмите «Изменить».\n"
        "4️⃣ Найдите «Автоматизация чатов».\n"
        f"5️⃣ Найдите {esc(bot_mention)}.\n"
        "6️⃣ Подключите бота и выдайте нужные права.\n\n"
        "После подключения бот предложит Premium, если он ещё не активен.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⚙️ Открыть настройки Telegram", url=SETTINGS_URL)]]),
    )


@dp.callback_query(F.data.startswith("buy:"))
async def buy_handler(callback: CallbackQuery) -> None:
    plan_id = callback.data.split(":", 1)[1]
    if plan_id not in PLANS:
        await callback.answer("Тариф не найден.", show_alert=True)
        return
    user_id = callback.from_user.id
    ensure_user(user_id, callback.from_user.username, callback.from_user.first_name)
    plan = PLANS[plan_id]
    price = calculate_price(user_id, plan_id)
    payload = f"premium:{user_id}:{plan_id}:{secrets.token_hex(8)}"
    with db() as conn:
        conn.execute("INSERT INTO payments (user_id, payload, plan, stars, created_at) VALUES (?, ?, ?, ?, ?)", (user_id, payload, plan_id, price, now_ts()))
    await callback.answer()
    await bot.send_invoice(
        chat_id=user_id,
        title=f"Premium — {plan['name']}",
        description="Уведомления об удалённых и изменённых Telegram Business-сообщениях.",
        payload=payload,
        currency="XTR",
        prices=[LabeledPrice(label=f"Premium {plan['name']}", amount=price)],
        provider_token="",
    )


@dp.pre_checkout_query()
async def pre_checkout_handler(query: PreCheckoutQuery) -> None:
    with db() as conn:
        payment = conn.execute("SELECT * FROM payments WHERE payload = ?", (query.invoice_payload,)).fetchone()
    valid = bool(payment and payment["user_id"] == query.from_user.id and query.currency == "XTR" and payment["stars"] == query.total_amount)
    await query.answer(ok=valid, error_message=None if valid else "Счёт недействителен. Создайте новый.")


@dp.message(F.successful_payment)
async def successful_payment_handler(message: Message) -> None:
    payment = message.successful_payment
    if not payment:
        return
    with db() as conn:
        row = conn.execute("SELECT * FROM payments WHERE payload = ?", (payment.invoice_payload,)).fetchone()
        already_done = conn.execute("SELECT 1 FROM payments WHERE telegram_payment_charge_id = ?", (payment.telegram_payment_charge_id,)).fetchone()
        if not row or already_done or row["user_id"] != message.from_user.id:
            await message.answer("Этот платёж уже обработан или не найден.")
            return
        conn.execute("UPDATE payments SET telegram_payment_charge_id = ? WHERE id = ?", (payment.telegram_payment_charge_id, row["id"]))
        conn.execute("UPDATE users SET purchases_count = purchases_count + 1, stars_spent = stars_spent + ?, updated_at = ? WHERE user_id = ?", (payment.total_amount, now_ts(), message.from_user.id))
    grant_premium(message.from_user.id, days=PLANS[row["plan"]]["days"])
    until = premium_expiry(message.from_user.id)
    date = datetime.fromtimestamp(until, timezone.utc).strftime("%d.%m.%Y %H:%M UTC") if until else "навсегда"
    await message.answer(f"🎉 <b>Premium активирован!</b>\n\nПодписка действует до: <b>{date}</b>", reply_markup=main_keyboard())


# ---------------------------------------------------------------------------
# ADMINISTRATION: ONLY ADMIN_ID, NO PASSWORD OR ACCESS-CODE BACKDOOR
# ---------------------------------------------------------------------------
def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats"),
            InlineKeyboardButton(text="👥 Пользователи", callback_data="admin:users"),
        ],
        [
            InlineKeyboardButton(text="💳 Платежи", callback_data="admin:payments"),
            InlineKeyboardButton(text="🔗 Connections", callback_data="admin:connections"),
        ],
        [
            InlineKeyboardButton(text="🎟 Промокоды", callback_data="admin:promos"),
            InlineKeyboardButton(text="➕ Добавить промокод", callback_data="admin:addpromo"),
        ],
        [
            InlineKeyboardButton(text="⭐ Выдать Premium", callback_data="admin:grant"),
            InlineKeyboardButton(text="❌ Забрать Premium", callback_data="admin:revoke"),
        ],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin:broadcast")],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:menu")],
    ])


def admin_promo_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Дни Premium", callback_data="promo_type:days")],
        [InlineKeyboardButton(text="♾ Premium навсегда", callback_data="promo_type:forever")],
        [InlineKeyboardButton(text="🔥 Скидка %", callback_data="promo_type:discount")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin:menu")],
    ])


@dp.message(Command("admin"))
async def admin_handler(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    admin_states.pop(message.from_user.id, None)
    await message.answer("🔐 <b>АДМИН-ПАНЕЛЬ</b>", reply_markup=admin_keyboard())


@dp.callback_query(F.data.startswith("admin:"))
async def admin_callback(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    action = callback.data.split(":", 1)[1]
    if action in {"menu", "back"}:
        admin_states.pop(callback.from_user.id, None)
        await callback.message.edit_text("🔐 <b>АДМИН-ПАНЕЛЬ</b>\n\nВыберите действие:", reply_markup=admin_keyboard())
    elif action == "stats":
        with db() as conn:
            users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            premium = conn.execute("SELECT COUNT(*) FROM users WHERE premium_forever = 1 OR premium_until > ?", (now_ts(),)).fetchone()[0]
            connections = conn.execute("SELECT COUNT(*) FROM business_connections WHERE is_enabled = 1").fetchone()[0]
            payments = conn.execute("SELECT COUNT(*), COALESCE(SUM(stars), 0) FROM payments WHERE telegram_payment_charge_id IS NOT NULL").fetchone()
        await callback.message.answer(
            f"📊 <b>СТАТИСТИКА</b>\n\n👥 Пользователи: <b>{users}</b>\n⭐ Premium: <b>{premium}</b>\n"
            f"🔗 Активные подключения: <b>{connections}</b>\n💳 Оплат: <b>{payments[0]}</b>\n⭐ Получено Stars: <b>{payments[1]}</b>"
        )
    elif action == "users":
        with db() as conn:
            rows = conn.execute("SELECT user_id, username, premium_until, premium_forever FROM users ORDER BY created_at DESC LIMIT 25").fetchall()
        lines = []
        for row in rows:
            label = "♾" if row["premium_forever"] else ("⭐" if (row["premium_until"] or 0) > now_ts() else "—")
            lines.append(f"{label} <code>{row['user_id']}</code> {('@' + esc(row['username'])) if row['username'] else ''}")
        await callback.message.answer("👥 <b>ПОСЛЕДНИЕ ПОЛЬЗОВАТЕЛИ</b>\n\n" + ("\n".join(lines) if lines else "Пока нет пользователей."))
    elif action == "payments":
        with db() as conn:
            rows = conn.execute("SELECT user_id, plan, stars, created_at FROM payments WHERE telegram_payment_charge_id IS NOT NULL ORDER BY id DESC LIMIT 25").fetchall()
        lines = [f"⭐{row['stars']} — <code>{row['user_id']}</code> ({esc(PLANS.get(row['plan'], {}).get('name', row['plan']))})" for row in rows]
        await callback.message.answer("💳 <b>ПОСЛЕДНИЕ ОПЛАТЫ</b>\n\n" + ("\n".join(lines) if lines else "Оплат пока нет."))
    elif action == "connections":
        with db() as conn:
            rows = conn.execute("SELECT user_chat_id, is_enabled, business_connection_id FROM business_connections ORDER BY updated_at DESC LIMIT 25").fetchall()
        lines = [f"{'✅' if row['is_enabled'] else '❌'} <code>{row['user_chat_id']}</code> — <code>{esc(row['business_connection_id'][:10])}…</code>" for row in rows]
        await callback.message.answer("🔗 <b>BUSINESS CONNECTIONS</b>\n\n" + ("\n".join(lines) if lines else "Подключений пока нет."))
    elif action == "promos":
        with db() as conn:
            custom = conn.execute("SELECT code, promo_type, value, max_uses, uses, is_active FROM promo_codes ORDER BY created_at DESC LIMIT 25").fetchall()
        lines = [f"<code>{code}</code> — {item['promo_type']}, {item['value']}; {promo_usage_count(code)}/{item['max_uses'] or '∞'}" for code, item in BUILTIN_PROMOS.items()]
        lines += [f"<code>{esc(row['code'])}</code> — {esc(row['promo_type'])}, {row['value']}; {row['uses']}/{row['max_uses'] or '∞'} {'✅' if row['is_active'] else '❌'}" for row in custom]
        await callback.message.answer("🎟 <b>ПРОМОКОДЫ</b>\n\n" + ("\n".join(lines) if lines else "Промокодов пока нет."))
    elif action == "addpromo":
        admin_states[callback.from_user.id] = {"state": "promo_code"}
        await callback.message.answer("🎟 Отправьте новый код (2–64 символа: буквы, цифры, _ или -).")
    elif action in {"grant", "revoke"}:
        admin_states[callback.from_user.id] = {"state": action}
        wording = "выдать Premium" if action == "grant" else "забрать Premium"
        await callback.message.answer(f"Отправьте ID пользователя, которому нужно {wording}.")
    elif action == "broadcast":
        admin_states[callback.from_user.id] = {"state": "broadcast"}
        await callback.message.answer("📢 <b>РАССЫЛКА</b>\n\nОтправьте текст, который нужно разослать всем пользователям.")
    await callback.answer()


@dp.message(F.text)
async def text_handler(message: Message) -> None:
    user_id = message.from_user.id
    state = admin_states.get(user_id)
    if not state:
        await message.answer("Выберите нужный раздел кнопками ниже.", reply_markup=main_keyboard())
        return

    if state["state"] == "user_promo":
        admin_states.pop(user_id, None)
        ok, result = activate_promo(user_id, message.text)
        await message.answer(result, reply_markup=main_keyboard())
        return

    # All remaining stateful actions below are administrator-only.
    if not is_admin(user_id):
        admin_states.pop(user_id, None)
        return

    if state["state"] == "grant_days":
        try:
            days = int(message.text.strip())
        except ValueError:
            await message.answer("Отправьте число дней или <code>0</code> для подписки навсегда.")
            return
        if days < 0 or days > 3650:
            await message.answer("Допустимое количество дней: от 0 до 3650.")
            return
        target_id = state["target_id"]
        grant_premium(target_id, days=days, forever=days == 0)
        admin_states.pop(user_id, None)
        label = "навсегда" if days == 0 else f"на {days} дн."
        await message.answer(f"Premium для <code>{target_id}</code> выдан {label}", reply_markup=admin_keyboard())
        return

    if state["state"] == "promo_code":
        code = message.text.strip().upper()
        valid, error = validate_promo_code(code)
        if not valid:
            await message.answer(f"❌ {error}")
            return
        admin_states[user_id] = {"state": "promo_type", "code": code}
        await message.answer(f"🎟 Код: <code>{esc(code)}</code>\n\nВыберите тип промокода:", reply_markup=admin_promo_type_keyboard())
        return

    if state["state"] == "promo_days":
        try:
            value = int(message.text.strip())
        except ValueError:
            await message.answer("Введите количество дней числом.")
            return
        if value < 1 or value > 3650:
            await message.answer("Допустимо от 1 до 3650 дней.")
            return
        admin_states[user_id] = {"state": "promo_limit", "code": state["code"], "promo_type": "days", "value": value}
        await message.answer("Сколько раз можно использовать код? Отправьте <code>0</code> для бесконечного лимита.")
        return

    if state["state"] == "promo_discount":
        try:
            value = int(message.text.strip())
        except ValueError:
            await message.answer("Введите процент числом.")
            return
        if value < 1 or value > 99:
            await message.answer("Скидка должна быть от 1 до 99%.")
            return
        admin_states[user_id] = {"state": "promo_limit", "code": state["code"], "promo_type": "discount", "value": value}
        await message.answer("Сколько раз можно использовать код? Отправьте <code>0</code> для бесконечного лимита.")
        return

    if state["state"] == "promo_limit":
        try:
            max_uses = int(message.text.strip())
        except ValueError:
            await message.answer("Введите лимит числом.")
            return
        if max_uses < 0:
            await message.answer("Лимит не может быть отрицательным.")
            return
        ok, result = create_promo(state["code"], state["promo_type"], state["value"], max_uses)
        admin_states.pop(user_id, None)
        await message.answer(("✅ " if ok else "❌ ") + result, reply_markup=admin_keyboard())
        return

    if state["state"] == "broadcast":
        admin_states.pop(user_id, None)
        with db() as conn:
            recipients = [row["user_id"] for row in conn.execute("SELECT user_id FROM users").fetchall()]
        success = failed = 0
        for recipient in recipients:
            try:
                await bot.send_message(recipient, message.text)
                success += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.04)
        await message.answer(f"📢 <b>РАССЫЛКА ЗАВЕРШЕНА</b>\n\n✅ Отправлено: {success}\n❌ Ошибок: {failed}", reply_markup=admin_keyboard())
        return

    try:
        target_id = int(message.text.strip())
    except ValueError:
        await message.answer("Нужен числовой Telegram ID.")
        return
    if state["state"] == "revoke":
        revoke_premium(target_id)
        admin_states.pop(user_id, None)
        await message.answer(f"Premium у <code>{target_id}</code> отключён.", reply_markup=admin_keyboard())
        return
    admin_states[user_id] = {"state": "grant_days", "target_id": target_id}
    await message.answer("На сколько дней выдать Premium? Отправьте <code>0</code> для подписки навсегда.")


@dp.callback_query(F.data.startswith("promo_type:"))
async def promo_type_callback(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    state = admin_states.get(callback.from_user.id)
    if not state or state.get("state") != "promo_type":
        await callback.answer("Срок создания промокода истёк.", show_alert=True)
        return
    promo_type = callback.data.split(":", 1)[1]
    if promo_type == "days":
        admin_states[callback.from_user.id] = {"state": "promo_days", "code": state["code"]}
        await callback.message.answer("Сколько дней Premium выдавать?")
    elif promo_type == "discount":
        admin_states[callback.from_user.id] = {"state": "promo_discount", "code": state["code"]}
        await callback.message.answer("Какую скидку в процентах выдавать?")
    elif promo_type == "forever":
        admin_states[callback.from_user.id] = {"state": "promo_limit", "code": state["code"], "promo_type": "forever", "value": 0}
        await callback.message.answer("Сколько раз можно использовать код? Отправьте <code>0</code> для бесконечного лимита.")
    else:
        await callback.answer("Неизвестный тип.", show_alert=True)
        return
    await callback.answer()


# ---------------------------------------------------------------------------
# BUSINESS UPDATES
# ---------------------------------------------------------------------------
@dp.business_connection()
async def business_connection_handler(connection: types.BusinessConnection) -> None:
    save_connection(connection)
    if connection.is_enabled:
        await offer_once_for_connection(connection.id, connection.user.id)


@dp.business_message()
async def business_message_handler(message: Message) -> None:
    # New messages are archived only. They are never forwarded to the owner.
    if not message.business_connection_id:
        return
    connection = await resolve_connection(message.business_connection_id)
    if not connection:
        return
    if message.from_user and message.from_user.id == connection["user_chat_id"]:
        return
    save_message(message)


@dp.edited_business_message()
async def edited_business_message_handler(message: Message) -> None:
    if not message.business_connection_id:
        return
    connection = await resolve_connection(message.business_connection_id)
    if not connection:
        return
    owner_id = connection["user_chat_id"]
    if message.from_user and message.from_user.id == owner_id:
        return
    old = get_saved_message(message.business_connection_id, message.chat.id, message.message_id)
    if old and has_premium(owner_id):
        try:
            await notify_edited(owner_id, old, message)
        except Exception:
            logger.exception("Could not send edited-message notification")
    save_message(message)


@dp.deleted_business_messages()
async def deleted_business_messages_handler(deleted: types.BusinessMessagesDeleted) -> None:
    connection = await resolve_connection(deleted.business_connection_id)
    if not connection or not has_premium(connection["user_chat_id"]):
        return
    owner_id = connection["user_chat_id"]
    for message_id in deleted.message_ids:
        saved = get_saved_message(deleted.business_connection_id, deleted.chat.id, message_id)
        if not saved:
            continue
        try:
            await notify_deleted(owner_id, saved)
        except Exception:
            logger.exception("Could not send deleted-message notification")


@dp.errors()
async def error_handler(event: types.ErrorEvent) -> None:
    logger.exception("Unhandled bot error: %s", event.exception)


async def main() -> None:
    global bot_mention
    init_db()
    me = await bot.get_me()
    bot_mention = f"@{me.username}" if me.username else "бота"
    logger.info("Starting %s", bot_mention)
    await bot.delete_webhook(drop_pending_updates=False)
    await dp.start_polling(bot, allowed_updates=[
        "message", "callback_query", "pre_checkout_query",
        "business_connection", "business_message", "edited_business_message", "deleted_business_messages",
    ])


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped")
