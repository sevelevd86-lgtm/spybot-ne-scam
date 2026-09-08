import asyncio
import html
import logging
import sqlite3
from datetime import datetime, timezone, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    LabeledPrice,
    PreCheckoutQuery,
)


# ============================================================
# CONFIG
# ============================================================

# ВСТАВЬ СЮДА ТОКЕН БОТА ИЗ BOTFATHER
BOT_TOKEN = "8893376358:AAHVWJwm8GLJjqz_BWZiFV3CAsquDGsf44c"

DATABASE_FILE = "business_monitor.db"


# ============================================================
# PREMIUM
# ============================================================

PLANS = {
    "day": {
        "name": "1 день",
        "days": 1,
        "price": 5,
    },

    "week": {
        "name": "1 неделя",
        "days": 7,
        "price": 25,
    },

    "month": {
        "name": "1 месяц",
        "days": 30,
        "price": 67,
    },

    "half_year": {
        "name": "6 месяцев",
        "days": 180,
        "price": 360,
    },

    "year": {
        "name": "1 год",
        "days": 365,
        "price": 550,
    },
}


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


# ============================================================
# TELEGRAM
# ============================================================

bot = Bot(token=BOT_TOKEN)

dp = Dispatcher(
    storage=MemoryStorage()
)


# ============================================================
# FSM
# ============================================================

class PromoStates(StatesGroup):
    waiting_code = State()


# ============================================================
# DATABASE
# ============================================================

db = sqlite3.connect(
    DATABASE_FILE,
    check_same_thread=False
)

db.execute("""
CREATE TABLE IF NOT EXISTS business_connections (
    connection_id TEXT PRIMARY KEY,

    user_id INTEGER NOT NULL,

    user_chat_id INTEGER NOT NULL,

    first_name TEXT,

    last_name TEXT,

    username TEXT,

    is_enabled INTEGER DEFAULT 1,

    created_at TEXT,

    updated_at TEXT
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    connection_id TEXT NOT NULL,

    chat_id INTEGER NOT NULL,

    message_id INTEGER NOT NULL,

    sender_id INTEGER,

    sender_name TEXT,

    sender_username TEXT,

    text TEXT,

    message_type TEXT,

    photo_file_id TEXT,

    photo_has_spoiler INTEGER DEFAULT 0,

    caption TEXT,

    created_at TEXT,

    updated_at TEXT,

    UNIQUE (
        connection_id,
        chat_id,
        message_id
    )
)
""")

db.execute("""
CREATE INDEX IF NOT EXISTS idx_messages_lookup
ON messages (
    connection_id,
    chat_id,
    message_id
)
""")

db.execute("""
CREATE INDEX IF NOT EXISTS idx_connections_user
ON business_connections (
    user_id
)
""")

# ============================================================
# USERS / PREMIUM
# ============================================================

db.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,

    username TEXT,

    first_name TEXT,

    premium_until TEXT,

    premium_forever INTEGER DEFAULT 0,

    promo_code TEXT,

    purchases_count INTEGER DEFAULT 0,

    stars_spent INTEGER DEFAULT 0,

    created_at TEXT,

    updated_at TEXT
)
""")

# ============================================================
# PAYMENTS
# ============================================================

db.execute("""
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    user_id INTEGER NOT NULL,

    payload TEXT NOT NULL,

    plan TEXT NOT NULL,

    stars INTEGER NOT NULL,

    telegram_payment_charge_id TEXT,

    created_at TEXT
)
""")

db.execute("""
CREATE INDEX IF NOT EXISTS idx_payments_user
ON payments (
    user_id
)
""")

db.commit()


# ============================================================
# HELPERS
# ============================================================

def now_iso():
    return datetime.now(
        timezone.utc
    ).isoformat()


def escape_text(text):
    return html.escape(
        str(text or "")
    )


def get_message_text(message: Message):

    if message.text:
        return message.text

    if message.caption:
        return message.caption

    return ""


def get_sender_info(message: Message):

    if not message.from_user:

        return (
            None,
            "Неизвестный пользователь",
            None
        )

    user = message.from_user

    return (
        user.id,
        user.full_name or "Без имени",
        user.username
    )


def get_message_type(message: Message):

    if message.photo:
        return "photo"

    if message.video:
        return "video"

    if message.animation:
        return "animation"

    if message.document:
        return "document"

    if message.audio:
        return "audio"

    if message.voice:
        return "voice"

    if message.video_note:
        return "video_note"

    if message.sticker:
        return "sticker"

    if message.location:
        return "location"

    if message.contact:
        return "contact"

    if message.poll:
        return "poll"

    if message.text:
        return "text"

    return "other"


def is_private_message(message: Message):

    return (
        message.chat is not None
        and message.chat.type == "private"
    )


# ============================================================
# USER DATABASE
# ============================================================

def ensure_user(user):

    if not user:
        return

    timestamp = now_iso()

    db.execute("""
        INSERT INTO users (
            user_id,
            username,
            first_name,
            created_at,
            updated_at
        )

        VALUES (?, ?, ?, ?, ?)

        ON CONFLICT(user_id)

        DO UPDATE SET

            username = excluded.username,
            first_name = excluded.first_name,
            updated_at = excluded.updated_at
    """, (
        user.id,
        user.username,
        user.first_name,
        timestamp,
        timestamp
    ))

    db.commit()


def get_user(user_id):

    cursor = db.execute("""
        SELECT
            user_id,
            username,
            first_name,
            premium_until,
            premium_forever,
            promo_code,
            purchases_count,
            stars_spent,
            created_at,
            updated_at

        FROM users

        WHERE user_id = ?

        LIMIT 1
    """, (
        user_id,
    ))

    return cursor.fetchone()


def get_premium_status(user_id):

    user = get_user(user_id)

    if not user:
        return False, None, False

    premium_until = user[3]
    premium_forever = bool(user[4])

    if premium_forever:
        return True, None, True

    if not premium_until:
        return False, None, False

    try:

        expires = datetime.fromisoformat(
            premium_until
        )

        if expires > datetime.now(timezone.utc):

            return True, expires, False

    except Exception:

        logger.exception(
            "Ошибка проверки Premium user=%s",
            user_id
        )

    return False, None, False


def has_premium(user_id):

    active, _, _ = get_premium_status(
        user_id
    )

    return active


def get_active_promo(user_id):

    user = get_user(user_id)

    if not user:
        return None

    return user[5]


def set_promo_code(
    user_id,
    promo_code
):

    ensure_user_by_id(user_id)

    db.execute("""
        UPDATE users

        SET promo_code = ?,
            updated_at = ?

        WHERE user_id = ?
    """, (
        promo_code,
        now_iso(),
        user_id
    ))

    db.commit()


def ensure_user_by_id(user_id):

    existing = get_user(user_id)

    if existing:
        return

    timestamp = now_iso()

    db.execute("""
        INSERT INTO users (
            user_id,
            created_at,
            updated_at
        )

        VALUES (?, ?, ?)
    """, (
        user_id,
        timestamp,
        timestamp
    ))

    db.commit()


# ============================================================
# PREMIUM PRICE
# ============================================================

def get_plan_price(
    user_id,
    plan_key
):

    plan = PLANS[plan_key]

    price = plan["price"]

    promo = get_active_promo(
        user_id
    )

    if promo == "met200$":

        price = int(
            price * 0.90
        )

    return price


def get_prices_text(user_id):

    promo = get_active_promo(
        user_id
    )

    prices = []

    for key, plan in PLANS.items():

        price = get_plan_price(
            user_id,
            key
        )

        prices.append(
            f"• {plan['name']} — ⭐ <b>{price}</b>"
        )

    text = "\n".join(prices)

    if promo == "met200$":

        text = (
            "🏷 <b>Промокод met200$ активирован</b>\n"
            "Скидка: <b>10%</b>\n\n"
            + text
        )

    return text


# ============================================================
# PREMIUM EXPIRATION
# ============================================================

def activate_premium(
    user_id,
    plan_key,
    stars
):

    ensure_user_by_id(
        user_id
    )

    plan = PLANS[plan_key]

    user = get_user(
        user_id
    )

    if not user:
        return

    # Если пользователь уже имеет вечный Premium,
    # ничего продлевать не нужно.
    if bool(user[4]):

        return

    current_until = None

    if user[3]:

        try:

            current_until = datetime.fromisoformat(
                user[3]
            )

        except Exception:

            current_until = None

    now = datetime.now(
        timezone.utc
    )

    if current_until and current_until > now:

        start = current_until

    else:

        start = now

    new_until = (
        start
        + timedelta(
            days=plan["days"]
        )
    )

    timestamp = now_iso()

    db.execute("""
        UPDATE users

        SET premium_until = ?,
            premium_forever = 0,
            purchases_count = purchases_count + 1,
            stars_spent = stars_spent + ?,
            updated_at = ?

        WHERE user_id = ?
    """, (
        new_until.isoformat(),
        stars,
        timestamp,
        user_id
    ))

    db.commit()


def activate_forever(
    user_id
):

    ensure_user_by_id(
        user_id
    )

    db.execute("""
        UPDATE users

        SET premium_forever = 1,
            premium_until = NULL,
            updated_at = ?

        WHERE user_id = ?
    """, (
        now_iso(),
        user_id
    ))

    db.commit()


# ============================================================
# PROFILE FORMAT
# ============================================================

def format_remaining(
    expires
):

    if not expires:
        return "—"

    now = datetime.now(
        timezone.utc
    )

    difference = expires - now

    total_seconds = int(
        difference.total_seconds()
    )

    if total_seconds <= 0:
        return "истёк"

    days = total_seconds // 86400

    hours = (
        total_seconds % 86400
    ) // 3600

    minutes = (
        total_seconds % 3600
    ) // 60

    if days > 0:

        return (
            f"{days} д. "
            f"{hours} ч."
        )

    if hours > 0:

        return (
            f"{hours} ч. "
            f"{minutes} мин."
        )

    return (
        f"{minutes} мин."
    )


def profile_text(
    user_id
):

    user = get_user(
        user_id
    )

    if not user:

        return (
            "👤 <b>ПРОФИЛЬ</b>\n\n"
            f"🆔 ID: <code>{user_id}</code>\n\n"
            "💎 Premium: ❌"
        )

    username = user[1]
    first_name = user[2]

    active, expires, forever = (
        get_premium_status(
            user_id
        )
    )

    purchases = user[6] or 0
    stars_spent = user[7] or 0

    name = (
        first_name
        or username
        or "Пользователь"
    )

    text = (
        "👤 <b>МОЙ ПРОФИЛЬ</b>\n\n"

        f"👋 {escape_text(name)}\n"

        f"🆔 ID: <code>{user_id}</code>\n"
    )

    if username:

        text += (
            f"🔗 Username: "
            f"@{escape_text(username)}\n"
        )

    text += "\n"

    if active and forever:

        text += (
            "💎 Premium: <b>АКТИВЕН ♾️</b>\n\n"
            "⏳ Срок: <b>навсегда</b>\n"
        )

    elif active and expires:

        text += (
            "💎 Premium: <b>АКТИВЕН 🟢</b>\n\n"

            f"⏳ Осталось: "
            f"<b>{format_remaining(expires)}</b>\n\n"

            f"📅 До:\n"
            f"<code>"
            f"{expires.strftime('%d.%m.%Y %H:%M UTC')}"
            f"</code>\n"
        )

    else:

        text += (
            "💎 Premium: <b>НЕ АКТИВЕН 🔴</b>\n\n"

            "🔒 Доступ к истории сообщений "
            "ограничен.\n"
        )

    text += (
        "\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        f"🛒 Покупок: <b>{purchases}</b>\n"
        f"⭐ Потрачено: <b>{stars_spent}</b>\n"
    )

    promo = user[5]

    if promo:

        text += (
            f"\n🎟 Промокод: "
            f"<code>{escape_text(promo)}</code>\n"
        )

    return text


# ============================================================
# KEYBOARDS
# ============================================================

def get_main_keyboard():

    return ReplyKeyboardMarkup(
        keyboard=[

            [
                KeyboardButton(
                    text="⭐ Premium"
                ),
                KeyboardButton(
                    text="👤 Профиль"
                )
            ],

            [
                KeyboardButton(
                    text="🎟 Промокод"
                )
            ]

        ],
        resize_keyboard=True,
        is_persistent=True
    )


def get_start_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="⚙️ Открыть настройки Telegram",
                    url="tg://settings"
                )
            ],

            [
                InlineKeyboardButton(
                    text="⭐ Premium",
                    callback_data="premium"
                ),

                InlineKeyboardButton(
                    text="👤 Профиль",
                    callback_data="profile"
                )
            ]

        ]
    )


def get_premium_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="⭐ 1 день",
                    callback_data="buy:day"
                ),

                InlineKeyboardButton(
                    text="⭐ Неделя",
                    callback_data="buy:week"
                )
            ],

            [
                InlineKeyboardButton(
                    text="⭐ Месяц",
                    callback_data="buy:month"
                )
            ],

            [
                InlineKeyboardButton(
                    text="⭐ 6 месяцев",
                    callback_data="buy:half_year"
                ),

                InlineKeyboardButton(
                    text="⭐ Год",
                    callback_data="buy:year"
                )
            ],

            [
                InlineKeyboardButton(
                    text="🎟 Ввести промокод",
                    callback_data="promo"
                )
            ],

            [
                InlineKeyboardButton(
                    text="👤 Мой профиль",
                    callback_data="profile"
                )
            ]

        ]
    )


def get_buy_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="⭐ КУПИТЬ PREMIUM",
                    callback_data="premium"
                )
            ]

        ]
    )


# ============================================================
# PREMIUM SCREEN
# ============================================================

async def send_premium_menu(
    chat_id,
    user_id,
    message=None
):

    ensure_user_by_id(
        user_id
    )

    active, expires, forever = (
        get_premium_status(
            user_id
        )
    )

    if active and forever:

        status = (
            "💎 <b>Premium активен навсегда</b>\n\n"
        )

    elif active and expires:

        status = (
            "💎 <b>Premium активен</b>\n"
            f"⏳ Осталось: "
            f"<b>{format_remaining(expires)}</b>\n\n"
        )

    else:

        status = (
            "💎 <b>Premium не активен</b>\n\n"
        )

    text = (

        "⭐ <b>PREMIUM</b>\n\n"

        + status

        + "Выбери срок подписки:\n\n"

        + get_prices_text(
            user_id
        )

        + "\n\n"
        "После выбора откроется оплата "
        "Telegram Stars ⭐."
    )

    if message:

        await message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=get_premium_keyboard()
        )

    else:

        await bot.send_message(
            chat_id,
            text,
            parse_mode="HTML",
            reply_markup=get_premium_keyboard()
        )


# ============================================================
# /START
# ============================================================

@dp.message(
    CommandStart()
)
async def start_handler(
    message: Message
):

    ensure_user(
        message.from_user
    )

    text = (

        "🕵️ <b>Business Message Monitor</b>\n\n"

        "✅ <b>Бот запущен.</b>\n\n"

        "Этот бот работает через "
        "<b>Telegram Business</b>.\n\n"

        "━━━━━━━━━━━━━━━━━━\n"

        "📲 <b>КАК ПОДКЛЮЧИТЬ</b>\n"

        "━━━━━━━━━━━━━━━━━━\n\n"

        "1️⃣ Нажми кнопку "
        "<b>«Открыть настройки Telegram»</b>.\n\n"

        "2️⃣ Открой "
        "<b>Telegram Business</b>.\n\n"

        "3️⃣ Найди "
        "<b>Подключённые боты / Чат-боты</b> "
        "и выбери этого бота.\n\n"

        "4️⃣ Разреши боту работать "
        "с нужными личными чатами.\n\n"

        "5️⃣ После подключения бот "
        "автоматически начнёт получать "
        "Business-сообщения.\n\n"

        "━━━━━━━━━━━━━━━━━━\n"

        "💎 <b>PREMIUM</b>\n"

        "━━━━━━━━━━━━━━━━━━\n\n"

        "Без Premium при удалении сообщения "
        "ты получишь уведомление "
        "с кнопкой покупки.\n\n"

        "С Premium бот сможет отправлять "
        "сохранённую информацию "
        "об удалённых сообщениях.\n\n"

        "Используй кнопки снизу 👇"
    )

    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=get_start_keyboard()
    )

    await message.answer(
        "👇 <b>МЕНЮ</b>",
        parse_mode="HTML",
        reply_markup=get_main_keyboard()
    )

    logger.info(
        "/start: %s",
        message.from_user.id
    )


# ============================================================
# MAIN KEYBOARD — PREMIUM
# ============================================================

@dp.message(
    F.text == "⭐ Premium"
)
async def premium_button(
    message: Message
):

    ensure_user(
        message.from_user
    )

    await send_premium_menu(
        chat_id=message.chat.id,
        user_id=message.from_user.id
    )


@dp.message(
    Command("premium")
)
async def premium_command(
    message: Message
):

    ensure_user(
        message.from_user
    )

    await send_premium_menu(
        chat_id=message.chat.id,
        user_id=message.from_user.id
    )


# ============================================================
# PROFILE
# ============================================================

@dp.message(
    F.text == "👤 Профиль"
)
async def profile_button(
    message: Message
):

    ensure_user(
        message.from_user
    )

    await message.answer(
        profile_text(
            message.from_user.id
        ),
        parse_mode="HTML",
        reply_markup=get_premium_keyboard()
    )


@dp.message(
    Command("profile")
)
async def profile_command(
    message: Message
):

    ensure_user(
        message.from_user
    )

    await message.answer(
        profile_text(
            message.from_user.id
        ),
        parse_mode="HTML",
        reply_markup=get_premium_keyboard()
    )


# ============================================================
# PROMO BUTTON
# ============================================================

@dp.message(
    F.text == "🎟 Промокод"
)
async def promo_button(
    message: Message,
    state: FSMContext
):

    ensure_user(
        message.from_user
    )

    await state.set_state(
        PromoStates.waiting_code
    )

    await message.answer(
        "🎟 <b>ПРОМОКОД</b>\n\n"
        "Отправь промокод одним сообщением.\n\n"
        "Например:\n"
        "<code>Dave100</code>\n"
        "<code>met200$</code>",
        parse_mode="HTML"
    )


# ============================================================
# PROMO CALLBACK
# ============================================================

@dp.callback_query(
    F.data == "promo"
)
async def promo_callback(
    callback,
    state: FSMContext
):

    ensure_user(
        callback.from_user
    )

    await state.set_state(
        PromoStates.waiting_code
    )

    await callback.answer()

    await callback.message.answer(
        "🎟 <b>ПРОМОКОД</b>\n\n"
        "Отправь промокод одним сообщением.\n\n"
        "Доступные коды:\n"
        "<code>Dave100</code>\n"
        "<code>met200$</code>",
        parse_mode="HTML"
    )


# ============================================================
# PROMO PROCESSOR
# ============================================================

@dp.message(
    PromoStates.waiting_code
)
async def process_promo(
    message: Message,
    state: FSMContext
):

    ensure_user(
        message.from_user
    )

    code = (
        message.text
        or ""
    ).strip()

    code_lower = code.lower()

    # --------------------------------------------------------
    # DAVE100
    # --------------------------------------------------------

    if code_lower == "dave100":

        activate_forever(
            message.from_user.id
        )

        await state.clear()

        await message.answer(
            "🎉 <b>ПРОМОКОД АКТИВИРОВАН!</b>\n\n"

            "💎 Premium активирован "
            "<b>НАВСЕГДА ♾️</b>\n\n"

            "Теперь тебе доступны "
            "все Premium-функции.",
            parse_mode="HTML",
            reply_markup=get_main_keyboard()
        )

        return

    # --------------------------------------------------------
    # MET200$
    # --------------------------------------------------------

    if code_lower == "met200$":

        set_promo_code(
            message.from_user.id,
            "met200$"
        )

        await state.clear()

        await message.answer(
            "🏷 <b>ПРОМОКОД АКТИВИРОВАН</b>\n\n"

            "🎁 Скидка: <b>10%</b>\n\n"

            "Новые цены:\n\n"

            + get_prices_text(
                message.from_user.id
            ),

            parse_mode="HTML",
            reply_markup=get_premium_keyboard()
        )

        return

    # --------------------------------------------------------
    # UNKNOWN
    # --------------------------------------------------------

    await message.answer(
        "❌ <b>Промокод не найден.</b>\n\n"
        "Проверь правильность написания.",
        parse_mode="HTML"
    )


# ============================================================
# PREMIUM CALLBACK
# ============================================================

@dp.callback_query(
    F.data == "premium"
)
async def premium_callback(
    callback
):

    ensure_user(
        callback.from_user
    )

    await callback.answer()

    await send_premium_menu(
        chat_id=callback.message.chat.id,
        user_id=callback.from_user.id,
        message=callback.message
    )


# ============================================================
# PROFILE CALLBACK
# ============================================================

@dp.callback_query(
    F.data == "profile"
)
async def profile_callback(
    callback
):

    ensure_user(
        callback.from_user
    )

    await callback.answer()

    await callback.message.edit_text(
        profile_text(
            callback.from_user.id
        ),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⭐ Premium",
                        callback_data="premium"
                    )
                ]
            ]
        )
    )


# ============================================================
# CREATE INVOICE
# ============================================================

@dp.callback_query(
    F.data.startswith("buy:")
)
async def buy_callback(
    callback
):

    ensure_user(
        callback.from_user
    )

    plan_key = callback.data.split(
        ":",
        1
    )[1]

    if plan_key not in PLANS:

        await callback.answer(
            "❌ Неизвестный тариф.",
            show_alert=True
        )

        return

    plan = PLANS[plan_key]

    price = get_plan_price(
        callback.from_user.id,
        plan_key
    )

    promo = get_active_promo(
        callback.from_user.id
    )

    payload = (
        f"premium:"
        f"{plan_key}:"
        f"{callback.from_user.id}"
    )

    description = (
        f"Premium на {plan['name']}."
    )

    if promo == "met200$":

        description += (
            "\nПрименена скидка 10%."
        )

    try:

        # Telegram Stars / XTR.
        # Для цифровых товаров provider_token
        # не требуется.
        await bot.send_invoice(

            chat_id=callback.from_user.id,

            title=f"Premium — {plan['name']}",

            description=description,

            payload=payload,

            currency="XTR",

            prices=[
                LabeledPrice(
                    label=f"Premium {plan['name']}",
                    amount=price
                )
            ]
        )

        await callback.answer()

    except Exception as e:

        logger.exception(
            "Ошибка создания invoice: %s",
            e
        )

        await callback.answer(
            "❌ Не удалось создать оплату.",
            show_alert=True
        )


# ============================================================
# PRE-CHECKOUT
# ============================================================

@dp.pre_checkout_query()
async def pre_checkout_handler(
    query: PreCheckoutQuery
):

    payload = query.invoice_payload

    parts = payload.split(
        ":"
    )

    if len(parts) != 3:

        await query.answer(
            ok=False,
            error_message="Некорректный платёж."
        )

        return

    prefix = parts[0]
    plan_key = parts[1]

    try:

        payload_user_id = int(
            parts[2]
        )

    except ValueError:

        await query.answer(
            ok=False,
            error_message="Некорректный пользователь."
        )

        return

    if prefix != "premium":

        await query.answer(
            ok=False,
            error_message="Некорректный товар."
        )

        return

    if plan_key not in PLANS:

        await query.answer(
            ok=False,
            error_message="Тариф не найден."
        )

        return

    if payload_user_id != query.from_user.id:

        await query.answer(
            ok=False,
            error_message="Этот счёт принадлежит другому пользователю."
        )

        return

    expected_price = get_plan_price(
        query.from_user.id,
        plan_key
    )

    if query.currency != "XTR":

        await query.answer(
            ok=False,
            error_message="Неверная валюта платежа."
        )

        return

    if query.total_amount != expected_price:

        await query.answer(
            ok=False,
            error_message="Сумма платежа изменилась. Создай новый счёт."
        )

        return

    await query.answer(
        ok=True
    )


# ============================================================
# SUCCESSFUL PAYMENT
# ============================================================

@dp.message(
    F.successful_payment
)
async def successful_payment_handler(
    message: Message
):

    payment = message.successful_payment

    if not payment:
        return

    user_id = message.from_user.id

    payload = payment.invoice_payload

    parts = payload.split(
        ":"
    )

    if len(parts) != 3:
        return

    prefix = parts[0]
    plan_key = parts[1]

    try:

        payload_user_id = int(
            parts[2]
        )

    except ValueError:

        return

    if prefix != "premium":
        return

    if payload_user_id != user_id:
        return

    if plan_key not in PLANS:
        return

    expected_price = get_plan_price(
        user_id,
        plan_key
    )

    if payment.currency != "XTR":
        return

    # Защита от неправильной суммы.
    if payment.total_amount != expected_price:

        await message.answer(
            "⚠️ <b>Платёж получен, но сумма "
            "не совпала с тарифом.</b>\n\n"
            "Обратись к администратору.",
            parse_mode="HTML"
        )

        logger.error(
            "PRICE MISMATCH user=%s expected=%s received=%s",
            user_id,
            expected_price,
            payment.total_amount
        )

        return

    activate_premium(
        user_id,
        plan_key,
        payment.total_amount
    )

    # Сохраняем платёж.
    db.execute("""
        INSERT INTO payments (
            user_id,
            payload,
            plan,
            stars,
            telegram_payment_charge_id,
            created_at
        )

        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        user_id,
        payload,
        plan_key,
        payment.total_amount,
        payment.telegram_payment_charge_id,
        now_iso()
    ))

    db.commit()

    active, expires, forever = (
        get_premium_status(
            user_id
        )
    )

    if forever:

        expiration_text = (
            "♾️ <b>Навсегда</b>"
        )

    else:

        expiration_text = (
            f"📅 До: "
            f"<code>"
            f"{expires.strftime('%d.%m.%Y %H:%M UTC')}"
            f"</code>\n\n"

            f"⏳ Осталось: "
            f"<b>{format_remaining(expires)}</b>"
        )

    await message.answer(
        "🎉 <b>ОПЛАТА УСПЕШНА!</b>\n\n"

        f"💎 Premium: <b>{PLANS[plan_key]['name']}</b>\n"

        f"⭐ Оплачено: "
        f"<b>{payment.total_amount}</b>\n\n"

        + expiration_text

        + "\n\n"
        "Теперь Premium активен 🟢",

        parse_mode="HTML",

        reply_markup=get_main_keyboard()
    )

    logger.info(
        "PAYMENT SUCCESS | user=%s plan=%s stars=%s",
        user_id,
        plan_key,
        payment.total_amount
    )


# ============================================================
# BUSINESS CONNECTION
# ============================================================

def save_business_connection(
    connection
):

    user = connection.user
    timestamp = now_iso()

    db.execute("""
        INSERT INTO business_connections (
            connection_id,
            user_id,
            user_chat_id,
            first_name,
            last_name,
            username,
            is_enabled,
            created_at,
            updated_at
        )

        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?
        )

        ON CONFLICT(connection_id)

        DO UPDATE SET

            user_id = excluded.user_id,
            user_chat_id = excluded.user_chat_id,

            first_name = excluded.first_name,
            last_name = excluded.last_name,
            username = excluded.username,

            is_enabled = excluded.is_enabled,

            updated_at = excluded.updated_at
    """, (
        connection.id,

        user.id,

        connection.user_chat_id,

        user.first_name,
        user.last_name,
        user.username,

        1 if connection.is_enabled else 0,

        timestamp,
        timestamp
    ))

    db.commit()

    ensure_user(
        user
    )


def get_connection(
    connection_id
):

    cursor = db.execute("""
        SELECT

            connection_id,
            user_id,
            user_chat_id,

            first_name,
            last_name,
            username,

            is_enabled

        FROM business_connections

        WHERE connection_id = ?

        LIMIT 1
    """, (
        connection_id,
    ))

    return cursor.fetchone()


async def get_business_connection(
    connection_id
):

    saved = get_connection(
        connection_id
    )

    if saved:
        return saved

    try:

        connection = await bot.get_business_connection(
            business_connection_id=connection_id
        )

        save_business_connection(
            connection
        )

        return get_connection(
            connection_id
        )

    except Exception as e:

        logger.exception(
            "Ошибка получения Business Connection %s: %s",
            connection_id,
            e
        )

        return None


async def get_owner_id(
    connection_id
):

    connection = await get_business_connection(
        connection_id
    )

    if not connection:
        return None

    return connection[1]


async def get_log_chat_id(
    connection_id
):

    connection = await get_business_connection(
        connection_id
    )

    if not connection:
        return None

    return connection[2]


# ============================================================
# BUSINESS CONNECTION HANDLER
# ============================================================

@dp.business_connection()
async def business_connection_handler(
    connection
):

    logger.info(
        "========================================"
    )

    logger.info(
        "BUSINESS CONNECTION"
    )

    logger.info(
        "connection_id=%s",
        connection.id
    )

    logger.info(
        "user_id=%s",
        connection.user.id
    )

    logger.info(
        "user_chat_id=%s",
        connection.user_chat_id
    )

    logger.info(
        "enabled=%s",
        connection.is_enabled
    )

    save_business_connection(
        connection
    )

    try:

        if connection.is_enabled:

            text = (

                "🟢 <b>Business Bot подключён</b>\n\n"

                f"👤 Аккаунт:\n"
                f"{escape_text(connection.user.full_name)}\n\n"

                f"🆔 ID:\n"
                f"<code>{connection.user.id}</code>\n\n"

                "Мониторинг личных чатов включён.\n\n"

                "💎 Premium управляется "
                "через меню бота."
            )

        else:

            text = (

                "🔴 <b>Business Bot отключён</b>\n\n"

                f"👤 Аккаунт:\n"
                f"{escape_text(connection.user.full_name)}\n\n"

                f"🆔 ID:\n"
                f"<code>{connection.user.id}</code>"
            )

        await bot.send_message(
            connection.user_chat_id,
            text,
            parse_mode="HTML"
        )

    except Exception as e:

        logger.exception(
            "Ошибка уведомления Business Connection: %s",
            e
        )


# ============================================================
# MESSAGE DATABASE
# ============================================================

def save_message(
    connection_id,
    message: Message
):

    sender_id, sender_name, sender_username = (
        get_sender_info(message)
    )

    message_type = get_message_type(
        message
    )

    text = get_message_text(
        message
    )

    photo_file_id = None

    photo_has_spoiler = 0

    if message.photo:

        photo_file_id = (
            message.photo[-1].file_id
        )

        photo_has_spoiler = (
            1
            if message.has_media_spoiler
            else 0
        )

    caption = (
        message.caption
        or ""
    )

    timestamp = now_iso()

    db.execute("""
        INSERT INTO messages (

            connection_id,
            chat_id,
            message_id,

            sender_id,
            sender_name,
            sender_username,

            text,
            message_type,

            photo_file_id,
            photo_has_spoiler,

            caption,

            created_at,
            updated_at
        )

        VALUES (

            ?, ?, ?,

            ?, ?, ?,

            ?, ?,

            ?, ?,

            ?,

            ?, ?
        )

        ON CONFLICT(
            connection_id,
            chat_id,
            message_id
        )

        DO UPDATE SET

            sender_id = excluded.sender_id,
            sender_name = excluded.sender_name,
            sender_username = excluded.sender_username,

            text = excluded.text,
            message_type = excluded.message_type,

            photo_file_id = excluded.photo_file_id,
            photo_has_spoiler = excluded.photo_has_spoiler,

            caption = excluded.caption,

            updated_at = excluded.updated_at
    """, (

        connection_id,
        message.chat.id,
        message.message_id,

        sender_id,
        sender_name,
        sender_username,

        text,
        message_type,

        photo_file_id,
        photo_has_spoiler,

        caption,

        timestamp,
        timestamp
    ))

    db.commit()


def get_saved_message(
    connection_id,
    chat_id,
    message_id
):

    cursor = db.execute("""
        SELECT

            sender_id,
            sender_name,
            sender_username,

            text,
            message_type,

            photo_file_id,
            photo_has_spoiler,

            caption,
            created_at

        FROM messages

        WHERE connection_id = ?

        AND chat_id = ?

        AND message_id = ?

        LIMIT 1
    """, (
        connection_id,
        chat_id,
        message_id
    ))

    return cursor.fetchone()


# ============================================================
# SENDER FORMAT
# ============================================================

def format_sender(
    sender_id,
    sender_name,
    sender_username
):

    result = (
        f"<b>{escape_text(sender_name)}</b>"
    )

    if sender_username:

        result += (
            f" (@{escape_text(sender_username)})"
        )

    if sender_id:

        result += (
            f"\nID: <code>{sender_id}</code>"
        )

    return result


# ============================================================
# NEW BUSINESS MESSAGE
# ============================================================

@dp.business_message()
async def business_message_handler(
    message: Message
):

    if not is_private_message(message):
        return

    connection_id = (
        message.business_connection_id
    )

    if not connection_id:
        return

    owner_id = await get_owner_id(
        connection_id
    )

    if owner_id is None:
        return

    sender_id = (

        message.from_user.id

        if message.from_user

        else None
    )

    # --------------------------------------------------------
    # Сохраняем сообщение
    # --------------------------------------------------------

    save_message(
        connection_id,
        message
    )

    logger.info(
        "NEW | conn=%s chat=%s msg=%s sender=%s type=%s reply=%s",
        connection_id,
        message.chat.id,
        message.message_id,
        sender_id,
        get_message_type(message),
        bool(message.reply_to_message)
    )

    # --------------------------------------------------------
    # Сообщения владельца не логируем
    # --------------------------------------------------------

    if (

        sender_id is not None

        and sender_id == owner_id

    ):

        return

    # ========================================================
    # REPLY
    # ========================================================

    if not message.reply_to_message:

        return

    replied_message_id = (
        message.reply_to_message.message_id
    )

    saved = get_saved_message(

        connection_id,

        message.chat.id,

        replied_message_id
    )

    if not saved:

        return

    (
        saved_sender_id,
        saved_sender_name,
        saved_sender_username,

        old_text,
        message_type,

        photo_file_id,
        photo_has_spoiler,

        caption,
        created_at

    ) = saved

    # ========================================================
    # REPLY НА СОХРАНЁННОЕ ФОТО
    # ========================================================

    if (

        message_type != "photo"

        or not photo_file_id

    ):

        return

    log_chat_id = await get_log_chat_id(
        connection_id
    )

    if not log_chat_id:
        return

    sender_info = format_sender(

        saved_sender_id,
        saved_sender_name,
        saved_sender_username
    )

    caption_text = (
        caption
        or "Без подписи"
    )

    log_caption = (

        "🔗 <b>ФОТО — REPLY</b>\n\n"

        f"👤 <b>Отправитель:</b>\n"
        f"{sender_info}\n\n"

        f"💬 <b>Чат:</b> "
        f"<code>{message.chat.id}</code>\n"

        f"🆔 <b>Message ID:</b> "
        f"<code>{replied_message_id}</code>\n\n"

        f"📝 <b>Подпись:</b>\n"
        f"{escape_text(caption_text)}"
    )

    try:

        await bot.send_photo(

            chat_id=log_chat_id,

            photo=photo_file_id,

            caption=log_caption,

            parse_mode="HTML",

            has_spoiler=False
        )

        logger.info(
            "REPLY PHOTO SENT | original=%s",
            replied_message_id
        )

    except Exception as e:

        logger.exception(
            "Ошибка отправки фото по Reply: %s",
            e
        )


# ============================================================
# EDITED BUSINESS MESSAGE
# ============================================================

@dp.edited_business_message()
async def edited_business_message_handler(
    message: Message
):

    if not is_private_message(message):
        return

    connection_id = (
        message.business_connection_id
    )

    if not connection_id:
        return

    old = get_saved_message(

        connection_id,

        message.chat.id,

        message.message_id
    )

    owner_id = await get_owner_id(
        connection_id
    )

    if old:

        (
            sender_id,
            sender_name,
            sender_username,

            old_text,
            message_type,

            photo_file_id,
            photo_has_spoiler,

            caption,
            created_at

        ) = old

    else:

        sender_id = (

            message.from_user.id

            if message.from_user

            else None
        )

        sender_name = (

            message.from_user.full_name

            if message.from_user

            else "Неизвестный"
        )

        sender_username = (

            message.from_user.username

            if message.from_user

            else None
        )

        old_text = (
            "[Старая версия не сохранена]"
        )

    if (

        owner_id is not None

        and sender_id == owner_id

    ):

        save_message(
            connection_id,
            message
        )

        return

    new_text = (
        get_message_text(message)
        or "[сообщение без текста]"
    )

    old_text = (
        old_text
        or "[сообщение без текста]"
    )

    if old_text == new_text:

        save_message(
            connection_id,
            message
        )

        return

    log_chat_id = await get_log_chat_id(
        connection_id
    )

    if not log_chat_id:
        return

    sender_info = format_sender(

        sender_id,
        sender_name,
        sender_username
    )

    log_text = (

        "✏️ <b>СООБЩЕНИЕ ИЗМЕНЕНО</b>\n\n"

        f"👤 <b>Собеседник:</b>\n"
        f"{sender_info}\n\n"

        f"💬 <b>Чат:</b> "
        f"<code>{message.chat.id}</code>\n"

        f"🆔 <b>Message ID:</b> "
        f"<code>{message.message_id}</code>\n\n"

        "🔴 <b>БЫЛО:</b>\n"

        f"<blockquote>"
        f"{escape_text(old_text)}"
        f"</blockquote>\n\n"

        "🟢 <b>СТАЛО:</b>\n"

        f"<blockquote>"
        f"{escape_text(new_text)}"
        f"</blockquote>"
    )

    try:

        await bot.send_message(

            log_chat_id,

            log_text,

            parse_mode="HTML"
        )

    except Exception as e:

        logger.exception(
            "Ошибка edit log: %s",
            e
        )

    save_message(
        connection_id,
        message
    )


# ============================================================
# DELETED BUSINESS MESSAGES
# ============================================================

@dp.deleted_business_messages()
async def deleted_business_messages_handler(
    event
):

    if event.chat.type != "private":
        return

    connection_id = (
        event.business_connection_id
    )

    chat_id = event.chat.id

    owner_id = await get_owner_id(
        connection_id
    )

    log_chat_id = await get_log_chat_id(
        connection_id
    )

    if not log_chat_id:
        return

    # Проверяем Premium владельца Business-аккаунта.
    premium_active = has_premium(
        owner_id
    )

    for message_id in event.message_ids:

        # ====================================================
        # БЕЗ PREMIUM
        # ====================================================

        if not premium_active:

            try:

                await bot.send_message(

                    log_chat_id,

                    "🗑 <b>ПОЛЬЗОВАТЕЛЬ УДАЛИЛ СООБЩЕНИЕ</b>\n\n"

                    "🔒 Содержимое сообщения доступно "
                    "только пользователям с активным Premium.\n\n"

                    "⭐ Оформи Premium, чтобы получить "
                    "доступ к сохранённой истории.",

                    parse_mode="HTML",

                    reply_markup=get_buy_keyboard()
                )

            except Exception as e:

                logger.exception(
                    "Ошибка Premium delete notification: %s",
                    e
                )

            continue

        # ====================================================
        # PREMIUM
        # ====================================================

        saved = get_saved_message(

            connection_id,

            chat_id,

            message_id
        )

        if not saved:

            log_text = (

                "🗑 <b>СООБЩЕНИЕ УДАЛЕНО</b>\n\n"

                f"💬 <b>Чат:</b> "
                f"<code>{chat_id}</code>\n"

                f"🆔 <b>Message ID:</b> "
                f"<code>{message_id}</code>\n\n"

                "⚠️ Содержимое отсутствует "
                "в локальной базе."
            )

            try:

                await bot.send_message(

                    log_chat_id,

                    log_text,

                    parse_mode="HTML"
                )

            except Exception as e:

                logger.exception(
                    "Ошибка delete log: %s",
                    e
                )

            continue

        (
            sender_id,
            sender_name,
            sender_username,

            message_text,
            message_type,

            photo_file_id,
            photo_has_spoiler,

            caption,
            created_at

        ) = saved

        if (

            owner_id is not None

            and sender_id == owner_id

        ):

            continue

        sender_info = format_sender(

            sender_id,
            sender_name,
            sender_username
        )

        # ----------------------------------------------------
        # УДАЛЁННОЕ ФОТО
        # ----------------------------------------------------

        if (

            message_type == "photo"

            and photo_file_id

        ):

            caption_text = (
                caption
                or "Без подписи"
            )

            log_caption = (

                "🗑 <b>ФОТО УДАЛЕНО</b>\n\n"

                f"👤 <b>Собеседник:</b>\n"
                f"{sender_info}\n\n"

                f"💬 <b>Чат:</b> "
                f"<code>{chat_id}</code>\n"

                f"🆔 <b>Message ID:</b> "
                f"<code>{message_id}</code>\n\n"

                f"📝 <b>Подпись:</b>\n"
                f"{escape_text(caption_text)}"
            )

            try:

                await bot.send_photo(

                    log_chat_id,

                    photo_file_id,

                    caption=log_caption,

                    parse_mode="HTML",

                    has_spoiler=False
                )

            except Exception as e:

                logger.exception(
                    "Ошибка отправки удалённого фото: %s",
                    e
                )

            continue

        # ----------------------------------------------------
        # УДАЛЁННЫЙ ТЕКСТ
        # ----------------------------------------------------

        if not message_text:

            message_text = (

                f"[{message_type}] "
                "сообщение без текста"
            )

        log_text = (

            "🗑 <b>СООБЩЕНИЕ УДАЛЕНО</b>\n\n"

            f"👤 <b>Собеседник:</b>\n"
            f"{sender_info}\n\n"

            f"💬 <b>Чат:</b> "
            f"<code>{chat_id}</code>\n"

            f"🆔 <b>Message ID:</b> "
            f"<code>{message_id}</code>\n\n"

            "📄 <b>Содержимое:</b>\n"

            f"<blockquote>"
            f"{escape_text(message_text)}"
            f"</blockquote>\n\n"

            f"🕒 <b>Сохранено:</b>\n"
            f"<code>{escape_text(created_at)}</code>"
        )

        try:

            await bot.send_message(

                log_chat_id,

                log_text,

                parse_mode="HTML"
            )

        except Exception as e:

            logger.exception(
                "Ошибка delete text log: %s",
                e
            )


# ============================================================
# MAIN
# ============================================================

async def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN не указан! "
            "Вставь токен в переменную BOT_TOKEN."
        )

    logger.info(
        "=========================================="
    )

    logger.info(
        "Telegram Business Message Monitor"
    )

    logger.info(
        "Premium: ENABLED"
    )

    logger.info(
        "Telegram Stars: ENABLED"
    )

    logger.info(
        "Promo codes: ENABLED"
    )

    logger.info(
        "Profile: ENABLED"
    )

    logger.info(
        "Delete monitor: ENABLED"
    )

    logger.info(
        "=========================================="
    )

    await dp.start_polling(

        bot,

        allowed_updates=[

            "message",

            "business_connection",

            "business_message",

            "edited_business_message",

            "deleted_business_messages",

            "callback_query",

            "pre_checkout_query"
        ]
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        logger.info(
            "Бот остановлен."
        )

    except Exception as e:

        logger.exception(
            "Критическая ошибка: %s",
            e
        )

    finally:

        db.close()