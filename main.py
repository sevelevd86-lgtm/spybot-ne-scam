import asyncio
import html
import logging
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from aiogram.types import (
    Message,
    CallbackQuery,
    PreCheckoutQuery,
    LabeledPrice,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    BotCommand,
)

from aiogram.utils.keyboard import InlineKeyboardBuilder


# ============================================================
# CONFIG
# ============================================================

# ВСТАВЬ СЮДА ТОКЕН БОТА
BOT_TOKEN = ""

DATABASE_FILE = "business_monitor.db"


# ============================================================
# PREMIUM PLANS
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
# PROMO
# ============================================================

PROMO_N1 = "N1"
PROMO_N1_LIMIT = 25
PROMO_N1_DAYS = 7

PROMO_FOREVER = "DAVE100"

PROMO_DISCOUNT = "MET200$"
DISCOUNT_PERCENT = 10


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("SpyNeScamBot")


# ============================================================
# TELEGRAM
# ============================================================

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN пустой. Вставь токен бота в переменную BOT_TOKEN."
    )


bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML,
    ),
)

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
    check_same_thread=False,
)

db.row_factory = sqlite3.Row

db_lock = asyncio.Lock()


# ============================================================
# TIME
# ============================================================

def now_ts() -> int:
    return int(time.time())


def now_iso() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


# ============================================================
# DATABASE INIT
# ============================================================

def init_db():

    cursor = db.cursor()

    # ========================================================
    # BUSINESS CONNECTIONS
    # ========================================================

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS business_connections (
            business_connection_id TEXT PRIMARY KEY,

            user_chat_id INTEGER NOT NULL,

            can_reply INTEGER DEFAULT 0,

            is_enabled INTEGER DEFAULT 1,

            created_at INTEGER NOT NULL,

            updated_at INTEGER NOT NULL
        )
        """
    )

    # ========================================================
    # MESSAGES
    # ========================================================

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            business_connection_id TEXT NOT NULL,

            chat_id INTEGER NOT NULL,

            message_id INTEGER NOT NULL,

            user_id INTEGER,

            username TEXT,

            first_name TEXT,

            text TEXT,

            caption TEXT,

            photo_file_id TEXT,

            photo_width INTEGER,

            photo_height INTEGER,

            message_type TEXT,

            photo_has_spoiler INTEGER DEFAULT 0,

            created_at INTEGER NOT NULL,

            updated_at INTEGER NOT NULL,

            PRIMARY KEY (
                business_connection_id,
                chat_id,
                message_id
            )
        )
        """
    )

    # ========================================================
    # USERS
    # ========================================================

    cursor.execute(
        """
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
        """
    )

    # ========================================================
    # PAYMENTS
    # ========================================================

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            user_id INTEGER NOT NULL,

            payload TEXT NOT NULL,

            plan TEXT NOT NULL,

            stars INTEGER NOT NULL,

            telegram_payment_charge_id TEXT,

            created_at TEXT
        )
        """
    )

    # ========================================================
    # PROMO USES
    #
    # Один пользователь может использовать разные промокоды.
    # Но один и тот же промокод повторно использовать нельзя.
    # ========================================================

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS promo_uses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            user_id INTEGER NOT NULL,

            promo_code TEXT NOT NULL,

            created_at INTEGER NOT NULL,

            UNIQUE(user_id, promo_code)
        )
        """
    )

    # ========================================================
    # INDEXES
    # ========================================================

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_messages_lookup
        ON messages(
            business_connection_id,
            chat_id,
            message_id
        )
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_messages_chat
        ON messages(chat_id)
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_payments_user
        ON payments(user_id)
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_connections_user
        ON business_connections(user_chat_id)
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_promo_code
        ON promo_uses(promo_code)
        """
    )

    db.commit()

    logger.info(
        "Database initialized."
    )


# ============================================================
# HELPERS
# ============================================================

def escape_text(
    text: Optional[str]
) -> str:

    return html.escape(
        str(text or "")
    )


def get_message_text(
    message: Message
) -> str:

    if message.text:
        return message.text

    if message.caption:
        return message.caption

    return ""


def get_sender_info(
    message: Message
):

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


def get_message_type(
    message: Message
):

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


def is_private_message(
    message: Message
) -> bool:

    return (
        message.chat is not None
        and message.chat.type == "private"
    )


# ============================================================
# USER DATABASE
# ============================================================

def ensure_user(
    user
):

    if not user:
        return

    timestamp = now_iso()

    db.execute(
        """
        INSERT INTO users(
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
        """,
        (
            user.id,
            user.username,
            user.first_name,
            timestamp,
            timestamp
        )
    )

    db.commit()


def ensure_user_by_id(
    user_id: int
):

    existing = get_user(
        user_id
    )

    if existing:
        return

    timestamp = now_iso()

    db.execute(
        """
        INSERT INTO users(
            user_id,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?)
        """,
        (
            user_id,
            timestamp,
            timestamp
        )
    )

    db.commit()


def get_user(
    user_id: int
):

    cursor = db.execute(
        """
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
        """,
        (
            user_id,
        )
    )

    return cursor.fetchone()


# ============================================================
# PREMIUM STATUS
# ============================================================

def get_premium_status(
    user_id: int
):

    user = get_user(
        user_id
    )

    if not user:
        return False, None, False

    premium_forever = bool(
        user["premium_forever"]
    )

    if premium_forever:
        return True, None, True

    premium_until = user["premium_until"]

    if not premium_until:
        return False, None, False

    try:

        expires = datetime.fromisoformat(
            premium_until
        )

        if expires > datetime.now(
            timezone.utc
        ):

            return True, expires, False

    except Exception:

        logger.exception(
            "Premium status error user=%s",
            user_id
        )

    return False, None, False


def has_premium(
    user_id: int
) -> bool:

    active, _, _ = get_premium_status(
        user_id
    )

    return active


# ============================================================
# PROMO HELPERS
# ============================================================

def has_used_promo(
    user_id: int,
    promo_code: str
) -> bool:

    cursor = db.execute(
        """
        SELECT id
        FROM promo_uses
        WHERE user_id = ?
        AND promo_code = ?
        LIMIT 1
        """,
        (
            user_id,
            promo_code
        )
    )

    return cursor.fetchone() is not None


def get_active_promo(
    user_id: int
):

    user = get_user(
        user_id
    )

    if not user:
        return None

    return user["promo_code"]


def has_discount(
    user_id: int
) -> bool:

    return has_used_promo(
        user_id,
        PROMO_DISCOUNT
    )


def set_promo_code(
    user_id: int,
    promo_code: str
):

    ensure_user_by_id(
        user_id
    )

    db.execute(
        """
        UPDATE users
        SET
            promo_code = ?,
            updated_at = ?
        WHERE user_id = ?
        """,
        (
            promo_code,
            now_iso(),
            user_id
        )
    )

    db.commit()


# ============================================================
# PREMIUM PRICE
# ============================================================

def get_plan_price(
    user_id: int,
    plan_key: str
) -> int:

    plan = PLANS[plan_key]

    price = plan["price"]

    if has_discount(user_id):

        price = int(
            price * 0.90
        )

    return max(
        1,
        price
    )


def get_prices_text(
    user_id: int
):

    prices = []

    discount = has_discount(
        user_id
    )

    if discount:

        prices.append(
            "🏷 <b>Скидка 10% активна</b>\n"
        )

    for key, plan in PLANS.items():

        price = get_plan_price(
            user_id,
            key
        )

        if discount:

            prices.append(
                f"• {plan['name']} — "
                f"<s>{plan['price']}⭐</s> "
                f"<b>{price}⭐</b>"
            )

        else:

            prices.append(
                f"• {plan['name']} — "
                f"⭐ <b>{price}</b>"
            )

    return "\n".join(
        prices
    )


# ============================================================
# PREMIUM ACTIVATION
# ============================================================

def activate_premium(
    user_id: int,
    plan_key: str,
    stars: int
):

    ensure_user_by_id(
        user_id
    )

    plan = PLANS[plan_key]

    user = get_user(
        user_id
    )

    if not user:
        return None

    if bool(user["premium_forever"]):

        db.execute(
            """
            UPDATE users
            SET
                purchases_count =
                    purchases_count + 1,

                stars_spent =
                    stars_spent + ?,

                updated_at = ?

            WHERE user_id = ?
            """,
            (
                stars,
                now_iso(),
                user_id
            )
        )

        db.commit()

        return None

    current_until = None

    if user["premium_until"]:

        try:

            current_until = datetime.fromisoformat(
                user["premium_until"]
            )

        except Exception:

            current_until = None

    now = datetime.now(
        timezone.utc
    )

    if (
        current_until
        and current_until > now
    ):

        start = current_until

    else:

        start = now

    new_until = (
        start
        + timedelta(
            days=plan["days"]
        )
    )

    db.execute(
        """
        UPDATE users

        SET
            premium_until = ?,

            premium_forever = 0,

            purchases_count =
                purchases_count + 1,

            stars_spent =
                stars_spent + ?,

            updated_at = ?

        WHERE user_id = ?
        """,
        (
            new_until.isoformat(),
            stars,
            now_iso(),
            user_id
        )
    )

    db.commit()

    return new_until


def activate_forever(
    user_id: int
):

    ensure_user_by_id(
        user_id
    )

    db.execute(
        """
        UPDATE users

        SET
            premium_forever = 1,

            premium_until = NULL,

            updated_at = ?

        WHERE user_id = ?
        """,
        (
            now_iso(),
            user_id
        )
    )

    db.commit()


# ============================================================
# FORMAT REMAINING
# ============================================================

def format_remaining(
    expires: Optional[datetime]
):

    if not expires:
        return "—"

    now = datetime.now(
        timezone.utc
    )

    difference = (
        expires - now
    )

    seconds = int(
        difference.total_seconds()
    )

    if seconds <= 0:
        return "истёк"

    days = seconds // 86400

    hours = (
        seconds % 86400
    ) // 3600

    minutes = (
        seconds % 3600
    ) // 60

    if days:

        return (
            f"{days} д. "
            f"{hours} ч."
        )

    if hours:

        return (
            f"{hours} ч. "
            f"{minutes} мин."
        )

    return (
        f"{minutes} мин."
    )


# ============================================================
# PROFILE
# ============================================================

def profile_text(
    user_id: int
):

    user = get_user(
        user_id
    )

    if not user:

        return (
            "👤 <b>ПРОФИЛЬ</b>\n\n"
            f"🆔 ID: <code>{user_id}</code>"
        )

    active, expires, forever = (
        get_premium_status(
            user_id
        )
    )

    username = user["username"]

    text = (
        "👤 <b>МОЙ ПРОФИЛЬ</b>\n\n"

        f"🆔 ID: "
        f"<code>{user_id}</code>\n"
    )

    if username:

        text += (
            f"🔗 Username: "
            f"@{escape_text(username)}\n"
        )

    text += "\n"

    if active and forever:

        text += (
            "💎 Premium: "
            "<b>АКТИВЕН ♾️</b>\n\n"

            "⏳ Срок: "
            "<b>навсегда</b>\n"
        )

    elif active and expires:

        text += (
            "💎 Premium: "
            "<b>АКТИВЕН 🟢</b>\n\n"

            f"⏳ Осталось: "
            f"<b>{format_remaining(expires)}</b>\n\n"

            "📅 До:\n"
            f"<code>"
            f"{expires.strftime('%d.%m.%Y %H:%M UTC')}"
            f"</code>\n"
        )

    else:

        text += (
            "💎 Premium: "
            "<b>НЕ АКТИВЕН 🔴</b>\n\n"

            "⏳ Осталось: <b>—</b>\n"
        )

    text += (
        "\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        f"🛒 Покупок: "
        f"<b>{user['purchases_count'] or 0}</b>\n"

        f"⭐ Потрачено Stars: "
        f"<b>{user['stars_spent'] or 0}</b>\n"
    )

    if has_discount(user_id):

        text += (
            "\n🏷 Скидка 10%: "
            "<b>АКТИВНА</b>\n"
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
                    text="⭐ Купить Premium",
                    callback_data="premium"
                )
            ]
        ]
    )


def get_payment_keyboard(
    price: int
):

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text=f"💳 Оплатить ⭐{price}",
            pay=True
        )
    )

    return builder.as_markup()


# ============================================================
# PREMIUM MENU
# ============================================================

async def send_premium_menu(
    chat_id: int,
    user_id: int
):

    ensure_user_by_id(
        user_id
    )

    active, expires, forever = (
        get_premium_status(
            user_id
        )
    )

    if forever:

        status = (
            "💎 <b>Premium активен навсегда ♾️</b>\n\n"
        )

    elif active and expires:

        status = (
            "💎 <b>Premium активен 🟢</b>\n"
            f"⏳ Осталось: "
            f"<b>{format_remaining(expires)}</b>\n\n"
        )

    else:

        status = (
            "💎 <b>Premium не активен 🔴</b>\n\n"
        )

    text = (
        "⭐ <b>PREMIUM</b>\n\n"

        + status

        + "Выбери срок подписки:\n\n"

        + get_prices_text(
            user_id
        )

        + "\n\n"
        "После выбора тарифа бот отправит "
        "отдельный счёт для оплаты Stars."
    )

    await bot.send_message(
        chat_id,
        text,
        parse_mode="HTML",
        reply_markup=get_premium_keyboard()
    )


# ============================================================
# START
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
        "🕵️ <b>SpyNeScamBot</b>\n\n"

        "Добро пожаловать!\n\n"

        "Бот работает через "
        "<b>Telegram Business</b>.\n\n"

        "━━━━━━━━━━━━━━━━━━\n"
        "📲 <b>ПОДКЛЮЧЕНИЕ</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        "1️⃣ Открой настройки Telegram.\n\n"

        "2️⃣ Открой свой профиль → "
        "<b>Изменить</b>.\n\n"

        "3️⃣ Нажми "
        "<b>«Автоматизация чатов»</b>.\n\n"

        "4️⃣ Найди "
        "<code>@SpyNeScamBot</code>.\n\n"

        "5️⃣ Выбери бота и нажми "
        "<b>«Добавить»</b>.\n\n"

        "После подключения бот начнёт "
        "обрабатывать Business-сообщения.\n\n"

        "👇 Используй меню снизу."
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


# ============================================================
# PREMIUM BUTTON
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
        message.chat.id,
        message.from_user.id
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
        message.chat.id,
        message.from_user.id
    )


@dp.callback_query(
    F.data == "premium"
)
async def premium_callback(
    callback: CallbackQuery
):

    ensure_user(
        callback.from_user
    )

    await callback.answer()

    await send_premium_menu(
        callback.message.chat.id,
        callback.from_user.id
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
        reply_markup=get_main_keyboard()
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
        reply_markup=get_main_keyboard()
    )


@dp.callback_query(
    F.data == "profile"
)
async def profile_callback(
    callback: CallbackQuery
):

    ensure_user(
        callback.from_user
    )

    await callback.answer()

    await callback.message.answer(
        profile_text(
            callback.from_user.id
        ),
        parse_mode="HTML",
        reply_markup=get_main_keyboard()
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
        "<code>N1</code>\n"
        "<code>Dave100</code>\n"
        "<code>met200$</code>",
        parse_mode="HTML",
        reply_markup=get_main_keyboard()
    )


@dp.callback_query(
    F.data == "promo"
)
async def promo_callback(
    callback: CallbackQuery,
    state: FSMContext
):

    ensure_user(
        callback.from_user
    )

    await callback.answer()

    await state.set_state(
        PromoStates.waiting_code
    )

    await callback.message.answer(
        "🎟 <b>ПРОМОКОД</b>\n\n"

        "Отправь промокод одним сообщением.\n\n"

        "Доступные промокоды:\n"
        "<code>N1</code>\n"
        "<code>Dave100</code>\n"
        "<code>met200$</code>",
        parse_mode="HTML",
        reply_markup=get_main_keyboard()
    )


# ============================================================
# PROMO PROCESS
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

    code_upper = code.upper()

    user_id = message.from_user.id

    # ========================================================
    # DAVE100
    # ========================================================

    if code_upper == PROMO_FOREVER:

        if has_used_promo(
            user_id,
            PROMO_FOREVER
        ):

            await state.clear()

            await message.answer(
                "❌ <b>Вы уже использовали "
                "этот промокод.</b>",
                parse_mode="HTML",
                reply_markup=get_main_keyboard()
            )

            return

        async with db_lock:

            if has_used_promo(
                user_id,
                PROMO_FOREVER
            ):

                await state.clear()

                await message.answer(
                    "❌ <b>Вы уже использовали "
                    "этот промокод.</b>",
                    parse_mode="HTML",
                    reply_markup=get_main_keyboard()
                )

                return

            db.execute(
                """
                INSERT INTO promo_uses(
                    user_id,
                    promo_code,
                    created_at
                )
                VALUES (?, ?, ?)
                """,
                (
                    user_id,
                    PROMO_FOREVER,
                    now_ts()
                )
            )

            activate_forever(
                user_id
            )

            db.commit()

        await state.clear()

        await message.answer(
            "🎉 <b>ПРОМОКОД АКТИВИРОВАН!</b>\n\n"

            "💎 Premium активирован "
            "<b>НАВСЕГДА ♾️</b>!",
            parse_mode="HTML",
            reply_markup=get_main_keyboard()
        )

        return

    # ========================================================
    # MET200$
    # ========================================================

    if code_upper == PROMO_DISCOUNT:

        if has_used_promo(
            user_id,
            PROMO_DISCOUNT
        ):

            await state.clear()

            await message.answer(
                "❌ <b>Вы уже использовали "
                "этот промокод.</b>",
                parse_mode="HTML",
                reply_markup=get_main_keyboard()
            )

            return

        async with db_lock:

            try:

                db.execute(
                    """
                    INSERT INTO promo_uses(
                        user_id,
                        promo_code,
                        created_at
                    )
                    VALUES (?, ?, ?)
                    """,
                    (
                        user_id,
                        PROMO_DISCOUNT,
                        now_ts()
                    )
                )

                set_promo_code(
                    user_id,
                    PROMO_DISCOUNT
                )

                db.commit()

            except sqlite3.IntegrityError:

                db.rollback()

                await state.clear()

                await message.answer(
                    "❌ <b>Вы уже использовали "
                    "этот промокод.</b>",
                    parse_mode="HTML",
                    reply_markup=get_main_keyboard()
                )

                return

        await state.clear()

        await message.answer(
            "🎉 <b>ПРОМОКОД АКТИВИРОВАН!</b>\n\n"

            "🏷 Скидка: <b>10%</b>\n\n"

            + get_prices_text(
                user_id
            ),
            parse_mode="HTML",
            reply_markup=get_premium_keyboard()
        )

        return

    # ========================================================
    # N1
    # ========================================================

    if code_upper == PROMO_N1:

        async with db_lock:

            cursor = db.cursor()

            cursor.execute(
                "BEGIN IMMEDIATE"
            )

            try:

                # --------------------------------------------
                # Пользователь уже использовал N1
                # --------------------------------------------

                cursor.execute(
                    """
                    SELECT id
                    FROM promo_uses
                    WHERE user_id = ?
                    AND promo_code = ?
                    LIMIT 1
                    """,
                    (
                        user_id,
                        PROMO_N1
                    )
                )

                if cursor.fetchone():

                    db.rollback()

                    await state.clear()

                    await message.answer(
                        "❌ <b>Вы уже использовали "
                        "промокод N1.</b>",
                        parse_mode="HTML",
                        reply_markup=get_main_keyboard()
                    )

                    return

                # --------------------------------------------
                # Проверяем общий лимит
                # --------------------------------------------

                cursor.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM promo_uses
                    WHERE promo_code = ?
                    """,
                    (
                        PROMO_N1,
                    )
                )

                used = int(
                    cursor.fetchone()["count"]
                )

                if used >= PROMO_N1_LIMIT:

                    db.rollback()

                    await state.clear()

                    await message.answer(
                        "❌ <b>Промокод закончился.</b>",
                        parse_mode="HTML",
                        reply_markup=get_main_keyboard()
                    )

                    return

                # --------------------------------------------
                # Записываем использование
                # --------------------------------------------

                cursor.execute(
                    """
                    INSERT INTO promo_uses(
                        user_id,
                        promo_code,
                        created_at
                    )
                    VALUES (?, ?, ?)
                    """,
                    (
                        user_id,
                        PROMO_N1,
                        now_ts()
                    )
                )

                # --------------------------------------------
                # Добавляем 7 дней
                # --------------------------------------------

                cursor.execute(
                    """
                    SELECT
                        premium_until,
                        premium_forever
                    FROM users
                    WHERE user_id = ?
                    """,
                    (
                        user_id,
                    )
                )

                user = cursor.fetchone()

                if user and not user["premium_forever"]:

                    current_until = None

                    if user["premium_until"]:

                        try:

                            current_until = datetime.fromisoformat(
                                user["premium_until"]
                            )

                        except Exception:

                            current_until = None

                    now = datetime.now(
                        timezone.utc
                    )

                    if (
                        current_until
                        and current_until > now
                    ):

                        start = current_until

                    else:

                        start = now

                    new_until = (
                        start
                        + timedelta(
                            days=PROMO_N1_DAYS
                        )
                    )

                    cursor.execute(
                        """
                        UPDATE users
                        SET
                            premium_until = ?,
                            premium_forever = 0,
                            updated_at = ?
                        WHERE user_id = ?
                        """,
                        (
                            new_until.isoformat(),
                            now_iso(),
                            user_id
                        )
                    )

                db.commit()

                activation_number = used + 1

            except Exception:

                db.rollback()

                logger.exception(
                    "N1 promo error"
                )

                await state.clear()

                await message.answer(
                    "❌ Ошибка активации промокода.",
                    reply_markup=get_main_keyboard()
                )

                return

        await state.clear()

        await message.answer(
            "🎉 <b>ПРОМОКОД N1 АКТИВИРОВАН!</b>\n\n"

            "💎 Premium: <b>7 дней</b>\n\n"

            f"🎟 Использований: "
            f"<b>{activation_number}/"
            f"{PROMO_N1_LIMIT}</b>",
            parse_mode="HTML",
            reply_markup=get_main_keyboard()
        )

        return

    # ========================================================
    # UNKNOWN
    # ========================================================

    await state.clear()

    await message.answer(
        "❌ <b>Промокод не найден.</b>\n\n"
        "Проверь правильность написания.",
        parse_mode="HTML",
        reply_markup=get_main_keyboard()
    )


# ============================================================
# BUY PREMIUM
# ============================================================

@dp.callback_query(
    F.data.startswith("buy:")
)
async def buy_callback(
    callback: CallbackQuery
):

    user_id = callback.from_user.id

    ensure_user(
        callback.from_user
    )

    plan_key = callback.data.split(
        ":",
        1
    )[1]

    if plan_key not in PLANS:

        await callback.answer(
            "❌ Тариф не найден.",
            show_alert=True
        )

        return

    plan = PLANS[plan_key]

    price = get_plan_price(
        user_id,
        plan_key
    )

    payload = (
        f"premium:"
        f"{plan_key}:"
        f"{user_id}:"
        f"{int(time.time() * 1000)}"
    )

    description = (
        f"Premium на {plan['name']}."
    )

    if has_discount(user_id):

        description += (
            "\nПрименена скидка 10%."
        )

    logger.info(
        "Creating Stars invoice | "
        "user=%s | plan=%s | price=%s",
        user_id,
        plan_key,
        price
    )

    try:

        # ====================================================
        # НОВОЕ СООБЩЕНИЕ С INVOICE
        # ====================================================

        await bot.send_invoice(

            chat_id=user_id,

            title=(
                f"Premium — "
                f"{plan['name']}"
            ),

            description=description,

            payload=payload,

            currency="XTR",

            # Telegram Stars
            provider_token="",

            prices=[
                LabeledPrice(
                    label=(
                        f"Premium — "
                        f"{plan['name']}"
                    ),
                    amount=price
                )
            ],

            reply_markup=get_payment_keyboard(
                price
            )
        )

        await callback.answer()

    except Exception:

        logger.exception(
            "Stars invoice error"
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

    logger.info(
        "PRE-CHECKOUT | user=%s | amount=%s | currency=%s | payload=%s",
        query.from_user.id,
        query.total_amount,
        query.currency,
        query.invoice_payload
    )

    payload = query.invoice_payload

    parts = payload.split(
        ":"
    )

    if len(parts) != 4:

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

    if query.currency != "XTR":

        await query.answer(
            ok=False,
            error_message="Неверная валюта."
        )

        return

    expected_price = get_plan_price(
        query.from_user.id,
        plan_key
    )

    if query.total_amount != expected_price:

        logger.error(
            "PRICE MISMATCH | expected=%s | received=%s",
            expected_price,
            query.total_amount
        )

        await query.answer(
            ok=False,
            error_message=(
                "Цена изменилась. "
                "Создай новый счёт."
            )
        )

        return

    await query.answer(
        ok=True
    )

    logger.info(
        "PRE-CHECKOUT APPROVED | "
        "user=%s | plan=%s | stars=%s",
        query.from_user.id,
        plan_key,
        query.total_amount
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

    if len(parts) != 4:

        logger.error(
            "Invalid payment payload: %s",
            payload
        )

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

    if payment.currency != "XTR":

        logger.error(
            "Wrong payment currency: %s",
            payment.currency
        )

        return

    # ========================================================
    # РЕАЛЬНО ОПЛАЧЕННАЯ СУММА
    # ========================================================

    stars_paid = payment.total_amount

    expected_price = get_plan_price(
        user_id,
        plan_key
    )

    if stars_paid != expected_price:

        logger.error(
            "PAYMENT PRICE MISMATCH | "
            "user=%s | expected=%s | received=%s",
            user_id,
            expected_price,
            stars_paid
        )

        await message.answer(
            "⚠️ <b>Платёж получен.</b>\n\n"
            "Но сумма платежа не совпала "
            "с текущей ценой тарифа.\n\n"
            "Обратись к администратору.",
            parse_mode="HTML",
            reply_markup=get_main_keyboard()
        )

        return

    charge_id = (
        payment.telegram_payment_charge_id
    )

    # ========================================================
    # ЗАЩИТА ОТ ПОВТОРНОЙ ОБРАБОТКИ
    # ========================================================

    cursor = db.execute(
        """
        SELECT id
        FROM payments
        WHERE telegram_payment_charge_id = ?
        LIMIT 1
        """,
        (
            charge_id,
        )
    )

    if cursor.fetchone():

        logger.warning(
            "Payment already processed: %s",
            charge_id
        )

        return

    # ========================================================
    # АКТИВИРУЕМ PREMIUM
    # ========================================================

    try:

        new_until = activate_premium(
            user_id,
            plan_key,
            stars_paid
        )

        # ====================================================
        # СОХРАНЯЕМ ПЛАТЁЖ
        # ====================================================

        db.execute(
            """
            INSERT INTO payments(
                user_id,
                payload,
                plan,
                stars,
                telegram_payment_charge_id,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                payload,
                plan_key,
                stars_paid,
                charge_id,
                now_iso()
            )
        )

        db.commit()

    except Exception:

        logger.exception(
            "Payment activation error"
        )

        await message.answer(
            "⚠️ <b>Платёж получен.</b>\n\n"
            "Произошла ошибка при активации "
            "Premium.\n\n"
            f"ID платежа:\n"
            f"<code>{escape_text(charge_id)}</code>",
            parse_mode="HTML",
            reply_markup=get_main_keyboard()
        )

        return

    # ========================================================
    # РЕЗУЛЬТАТ
    # ========================================================

    if new_until:

        expiration = (
            new_until.strftime(
                "%d.%m.%Y %H:%M UTC"
            )
        )

        remaining = format_remaining(
            new_until
        )

        expiration_text = (
            f"📅 До: "
            f"<code>{expiration}</code>\n\n"

            f"⏳ Осталось: "
            f"<b>{remaining}</b>"
        )

    else:

        expiration_text = (
            "♾️ <b>Premium навсегда</b>"
        )

    await message.answer(
        "🎉 <b>ОПЛАТА УСПЕШНА!</b>\n\n"

        f"💎 Premium: "
        f"<b>{PLANS[plan_key]['name']}</b>\n"

        f"⭐ Оплачено: "
        f"<b>{stars_paid} Stars</b>\n\n"

        f"{expiration_text}\n\n"

        "✅ <b>Premium активирован!</b>",
        parse_mode="HTML",
        reply_markup=get_main_keyboard()
    )

    logger.info(
        "PAYMENT SUCCESS | "
        "user=%s | plan=%s | stars=%s | charge=%s",
        user_id,
        plan_key,
        stars_paid,
        charge_id
    )


# ============================================================
# BUSINESS CONNECTION
# ============================================================

def save_business_connection(
    connection
):

    user = connection.user

    timestamp = now_ts()

    db.execute(
        """
        INSERT INTO business_connections(
            business_connection_id,
            user_chat_id,
            can_reply,
            is_enabled,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?)

        ON CONFLICT(business_connection_id)
        DO UPDATE SET

            user_chat_id =
                excluded.user_chat_id,

            can_reply =
                excluded.can_reply,

            is_enabled =
                excluded.is_enabled,

            updated_at =
                excluded.updated_at
        """,
        (
            connection.id,
            connection.user_chat_id,
            1 if connection.can_reply else 0,
            1 if connection.is_enabled else 0,
            timestamp,
            timestamp
        )
    )

    db.commit()

    ensure_user(
        user
    )


def get_connection(
    connection_id: str
):

    cursor = db.execute(
        """
        SELECT *
        FROM business_connections
        WHERE business_connection_id = ?
        LIMIT 1
        """,
        (
            connection_id,
        )
    )

    return cursor.fetchone()


async def get_business_connection(
    connection_id: str
):

    saved = get_connection(
        connection_id
    )

    if saved:
        return saved

    try:

        connection = (
            await bot.get_business_connection(
                business_connection_id=connection_id
            )
        )

        save_business_connection(
            connection
        )

        return get_connection(
            connection_id
        )

    except Exception:

        logger.exception(
            "Business connection lookup error"
        )

        return None


async def get_owner_id(
    connection_id: str
):

    connection = await get_business_connection(
        connection_id
    )

    if not connection:
        return None

    return connection["user_chat_id"]


async def get_log_chat_id(
    connection_id: str
):

    connection = await get_business_connection(
        connection_id
    )

    if not connection:
        return None

    return connection["user_chat_id"]


# ============================================================
# BUSINESS CONNECTION EVENT
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

                "Мониторинг личных чатов включён."
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
            parse_mode="HTML",
            reply_markup=get_main_keyboard()
        )

    except Exception:

        logger.exception(
            "Business connection notification error"
        )


# ============================================================
# SAVE BUSINESS MESSAGE
# ============================================================

def save_message(
    connection_id: str,
    message: Message
):

    sender_id, sender_name, sender_username = (
        get_sender_info(
            message
        )
    )

    message_type = get_message_type(
        message
    )

    text = get_message_text(
        message
    )

    caption = (
        message.caption
        or ""
    )

    photo_file_id = None
    photo_width = None
    photo_height = None

    photo_has_spoiler = 0

    if message.photo:

        photo = message.photo[-1]

        photo_file_id = photo.file_id

        photo_width = photo.width

        photo_height = photo.height

        photo_has_spoiler = (
            1
            if message.has_media_spoiler
            else 0
        )

    timestamp = now_ts()

    db.execute(
        """
        INSERT INTO messages(

            business_connection_id,

            chat_id,

            message_id,

            user_id,

            username,

            first_name,

            text,

            caption,

            photo_file_id,

            photo_width,

            photo_height,

            message_type,

            photo_has_spoiler,

            created_at,

            updated_at
        )

        VALUES(

            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?
        )

        ON CONFLICT(
            business_connection_id,
            chat_id,
            message_id
        )

        DO UPDATE SET

            user_id =
                excluded.user_id,

            username =
                excluded.username,

            first_name =
                excluded.first_name,

            text =
                excluded.text,

            caption =
                excluded.caption,

            photo_file_id =
                excluded.photo_file_id,

            photo_width =
                excluded.photo_width,

            photo_height =
                excluded.photo_height,

            message_type =
                excluded.message_type,

            photo_has_spoiler =
                excluded.photo_has_spoiler,

            updated_at =
                excluded.updated_at
        """,
        (
            connection_id,

            message.chat.id,

            message.message_id,

            sender_id,

            sender_username,

            sender_name,

            text,

            caption,

            photo_file_id,

            photo_width,

            photo_height,

            message_type,

            photo_has_spoiler,

            timestamp,

            timestamp
        )
    )

    db.commit()


def get_saved_message(
    connection_id: str,
    chat_id: int,
    message_id: int
):

    cursor = db.execute(
        """
        SELECT
            user_id,
            username,
            first_name,

            text,
            caption,

            photo_file_id,
            photo_width,
            photo_height,

            message_type,
            photo_has_spoiler,

            created_at
        FROM messages
        WHERE
            business_connection_id = ?
            AND chat_id = ?
            AND message_id = ?
        LIMIT 1
        """,
        (
            connection_id,
            chat_id,
            message_id
        )
    )

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

    if not is_private_message(
        message
    ):
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

    # ========================================================
    # СОХРАНЯЕМ ВСЁ
    # ========================================================

    try:

        save_message(
            connection_id,
            message
        )

    except Exception:

        logger.exception(
            "Save business message error"
        )

        return

    logger.info(
        "NEW | conn=%s | chat=%s | msg=%s | sender=%s | type=%s | reply=%s",
        connection_id,
        message.chat.id,
        message.message_id,
        sender_id,
        get_message_type(message),
        bool(message.reply_to_message)
    )

    # ========================================================
    # СВОИ СООБЩЕНИЯ НЕ ЛОГИРУЕМ
    # ========================================================

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

    # ========================================================
    # REPLY НА СОХРАНЁННОЕ ФОТО
    # ========================================================

    photo_file_id = saved["photo_file_id"]

    if not photo_file_id:
        return

    log_chat_id = await get_log_chat_id(
        connection_id
    )

    if not log_chat_id:
        return

    sender_info = format_sender(
        saved["user_id"],
        saved["first_name"],
        saved["username"]
    )

    caption = (
        saved["caption"]
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
        f"{escape_text(caption)}"
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
            "Reply photo sent | original=%s",
            replied_message_id
        )

    except Exception:

        logger.exception(
            "Reply photo send error"
        )


# ============================================================
# EDITED BUSINESS MESSAGE
# ============================================================

@dp.edited_business_message()
async def edited_business_message_handler(
    message: Message
):

    if not is_private_message(
        message
    ):
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

        sender_id = old["user_id"]

        sender_name = old["first_name"]

        sender_username = old["username"]

        old_text = (
            old["text"]
            or old["caption"]
            or ""
        )

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

    # ========================================================
    # СВОИ СООБЩЕНИЯ
    # ========================================================

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
        get_message_text(
            message
        )
        or "[сообщение без текста]"
    )

    old_text = (
        old_text
        or "[сообщение без текста]"
    )

    # ========================================================
    # Если фактически текст не изменился
    # ========================================================

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

    except Exception:

        logger.exception(
            "Edited message notification error"
        )

    # ========================================================
    # ОБНОВЛЯЕМ СОХРАНЁННУЮ ВЕРСИЮ
    # ========================================================

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

    connection_id = (
        event.business_connection_id
    )

    if not connection_id:
        return

    if event.chat.type != "private":
        return

    owner_id = await get_owner_id(
        connection_id
    )

    log_chat_id = await get_log_chat_id(
        connection_id
    )

    if not owner_id or not log_chat_id:
        return

    # ========================================================
    # PREMIUM CHECK
    # ========================================================

    premium_active = has_premium(
        owner_id
    )

    logger.info(
        "DELETE EVENT | conn=%s | chat=%s | ids=%s | premium=%s",
        connection_id,
        event.chat.id,
        event.message_ids,
        premium_active
    )

    # ========================================================
    # ОБРАБАТЫВАЕМ КАЖДОЕ УДАЛЁННОЕ СООБЩЕНИЕ
    # ========================================================

    for message_id in event.message_ids:

        # ====================================================
        # БЕЗ PREMIUM
        # ====================================================

        if not premium_active:

            try:

                await bot.send_message(
                    log_chat_id,

                    "🗑 <b>ПОЛЬЗОВАТЕЛЬ УДАЛИЛ СООБЩЕНИЕ</b>\n\n"

                    "🔒 Содержимое сообщения "
                    "доступно только с Premium.\n\n"

                    "⭐ Оформи Premium, чтобы "
                    "получать сохранённую "
                    "информацию об удалённых сообщениях.",

                    parse_mode="HTML",

                    reply_markup=get_buy_keyboard()
                )

            except Exception:

                logger.exception(
                    "Free delete notification error"
                )

            continue

        # ====================================================
        # PREMIUM
        # ====================================================

        saved = get_saved_message(
            connection_id,
            event.chat.id,
            message_id
        )

        if not saved:

            try:

                await bot.send_message(
                    log_chat_id,

                    "🗑 <b>СООБЩЕНИЕ УДАЛЕНО</b>\n\n"

                    f"💬 Чат: "
                    f"<code>{event.chat.id}</code>\n"

                    f"🆔 Message ID: "
                    f"<code>{message_id}</code>\n\n"

                    "⚠️ Сохранённой копии "
                    "этого сообщения нет.",

                    parse_mode="HTML"
                )

            except Exception:

                logger.exception(
                    "Delete empty notification error"
                )

            continue

        sender_info = format_sender(
            saved["user_id"],
            saved["first_name"],
            saved["username"]
        )

        # ====================================================
        # УДАЛЁННОЕ ФОТО
        # ====================================================

        if saved["photo_file_id"]:

            caption = (
                saved["caption"]
                or "Без подписи"
            )

            log_caption = (
                "🗑 <b>ФОТО УДАЛЕНО</b>\n\n"

                f"👤 <b>Собеседник:</b>\n"
                f"{sender_info}\n\n"

                f"💬 <b>Чат:</b> "
                f"<code>{event.chat.id}</code>\n"

                f"🆔 <b>Message ID:</b> "
                f"<code>{message_id}</code>\n\n"

                f"📝 <b>Подпись:</b>\n"
                f"{escape_text(caption)}"
            )

            try:

                await bot.send_photo(
                    chat_id=log_chat_id,

                    photo=saved["photo_file_id"],

                    caption=log_caption,

                    parse_mode="HTML",

                    has_spoiler=False
                )

            except Exception:

                logger.exception(
                    "Deleted photo send error"
                )

            continue

        # ====================================================
        # УДАЛЁННЫЙ ТЕКСТ
        # ====================================================

        message_text = (
            saved["text"]
            or saved["caption"]
            or ""
        )

        if not message_text:

            message_text = (
                f"[{saved['message_type']}] "
                "сообщение без текста"
            )

        log_text = (
            "🗑 <b>СООБЩЕНИЕ УДАЛЕНО</b>\n\n"

            f"👤 <b>Собеседник:</b>\n"
            f"{sender_info}\n\n"

            f"💬 <b>Чат:</b> "
            f"<code>{event.chat.id}</code>\n"

            f"🆔 <b>Message ID:</b> "
            f"<code>{message_id}</code>\n\n"

            "📄 <b>Содержимое:</b>\n"

            f"<blockquote>"
            f"{escape_text(message_text)}"
            f"</blockquote>\n\n"

            f"🕒 <b>Сохранено:</b>\n"
            f"<code>{escape_text(str(saved['created_at']))}</code>"
        )

        try:

            await bot.send_message(
                log_chat_id,
                log_text,
                parse_mode="HTML"
            )

        except Exception:

            logger.exception(
                "Deleted text send error"
            )


# ============================================================
# HELP
# ============================================================

@dp.message(
    Command("help")
)
async def help_handler(
    message: Message
):

    await message.answer(
        "🕵️ <b>SpyNeScamBot</b>\n\n"

        "Бот работает через Telegram Business.\n\n"

        "<b>Команды:</b>\n"
        "/start — запуск\n"
        "/premium — Premium\n"
        "/profile — профиль\n"
        "/help — помощь\n\n"

        "<b>Подключение:</b>\n"
        "Профиль → Изменить → "
        "Автоматизация чатов → "
        "@SpyNeScamBot → Добавить",
        parse_mode="HTML",
        reply_markup=get_main_keyboard()
    )


# ============================================================
# BOT COMMANDS
# ============================================================

async def set_commands():

    await bot.set_my_commands(
        [
            BotCommand(
                command="start",
                description="Запустить бота"
            ),

            BotCommand(
                command="premium",
                description="Купить Premium"
            ),

            BotCommand(
                command="profile",
                description="Мой профиль"
            ),

            BotCommand(
                command="help",
                description="Помощь"
            ),
        ]
    )


# ============================================================
# MAIN
# ============================================================

async def main():

    init_db()

    await set_commands()

    logger.info(
        "========================================"
    )

    logger.info(
        "SpyNeScamBot started"
    )

    logger.info(
        "Business Monitor: ENABLED"
    )

    logger.info(
        "Edited messages: ENABLED"
    )

    logger.info(
        "Deleted messages: ENABLED"
    )

    logger.info(
        "Photo saving: ENABLED"
    )

    logger.info(
        "Reply photo logging: ENABLED"
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
        "========================================"
    )

    await dp.start_polling(
        bot,

        allowed_updates=[
            "message",
            "callback_query",

            "business_connection",
            "business_message",
            "edited_business_message",
            "deleted_business_messages",

            "pre_checkout_query",
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
            "Bot stopped."
        )

    except Exception:

        logger.exception(
            "Fatal error"
        )

    finally:

        try:
            db.close()
        except Exception:
            pass