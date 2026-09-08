import asyncio
import html
import logging
import sqlite3
import time
from datetime import datetime, timezone
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from aiogram.utils.keyboard import (
    InlineKeyboardBuilder,
    ReplyKeyboardBuilder,
)


# ============================================================
# НАСТРОЙКИ
# ============================================================

BOT_TOKEN = ""

DB_PATH = "business_monitor.db"


# ============================================================
# PREMIUM
# ============================================================

PLANS = {
    "day": {
        "name": "1 день",
        "days": 1,
        "stars": 5,
    },

    "week": {
        "name": "1 неделя",
        "days": 7,
        "stars": 25,
    },

    "month": {
        "name": "1 месяц",
        "days": 30,
        "stars": 67,
    },

    "6months": {
        "name": "6 месяцев",
        "days": 180,
        "stars": 360,
    },

    "year": {
        "name": "1 год",
        "days": 365,
        "stars": 550,
    },
}


# ============================================================
# ПРОМОКОДЫ
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
# BOT
# ============================================================

if not BOT_TOKEN:
    raise RuntimeError(
        "Укажи токен бота в переменной BOT_TOKEN."
    )


bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML,
    ),
)

dp = Dispatcher()


# ============================================================
# FSM
# ============================================================

class PromoStates(StatesGroup):
    waiting_code = State()


# ============================================================
# DATABASE
# ============================================================

db = sqlite3.connect(
    DB_PATH,
    check_same_thread=False,
)

db.row_factory = sqlite3.Row

db_lock = asyncio.Lock()


def now_ts() -> int:
    return int(time.time())


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

            created_at INTEGER NOT NULL,

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

            premium_until INTEGER,
            premium_forever INTEGER DEFAULT 0,

            promo_code TEXT,

            purchases_count INTEGER DEFAULT 0,
            stars_spent INTEGER DEFAULT 0,

            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
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

            created_at INTEGER NOT NULL
        )
        """
    )

    # ========================================================
    # PROMO USES
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

    db.commit()

    # ========================================================
    # МИГРАЦИЯ СТАРОЙ promo_uses
    # ========================================================

    cursor.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table'
        AND name = 'promo_uses'
        """
    )

    table_info = cursor.fetchone()

    if table_info and table_info["sql"]:

        sql = table_info["sql"].upper()

        old_unique = (
            "USER_ID INTEGER NOT NULL UNIQUE"
            in sql
        )

        correct_unique = (
            "UNIQUE(USER_ID, PROMO_CODE)"
            in sql
            or
            "UNIQUE (USER_ID, PROMO_CODE)"
            in sql
        )

        if old_unique or not correct_unique:

            logger.info(
                "Миграция promo_uses..."
            )

            cursor.execute(
                """
                ALTER TABLE promo_uses
                RENAME TO promo_uses_old
                """
            )

            cursor.execute(
                """
                CREATE TABLE promo_uses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    promo_code TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    UNIQUE(user_id, promo_code)
                )
                """
            )

            cursor.execute(
                """
                INSERT OR IGNORE INTO promo_uses (
                    user_id,
                    promo_code,
                    created_at
                )
                SELECT
                    user_id,
                    promo_code,
                    created_at
                FROM promo_uses_old
                """
            )

            cursor.execute(
                """
                DROP TABLE promo_uses_old
                """
            )

            db.commit()

    # ========================================================
    # INDEXES
    # ========================================================

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_messages_chat
        ON messages(chat_id)
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_messages_business
        ON messages(business_connection_id)
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
    value: Optional[str],
) -> str:

    if not value:
        return ""

    return html.escape(
        str(value)
    )


def format_datetime(
    timestamp: Optional[int],
) -> str:

    if not timestamp:
        return "—"

    dt = datetime.fromtimestamp(
        timestamp,
        timezone.utc,
    )

    return dt.strftime(
        "%d.%m.%Y %H:%M"
    )


def format_remaining(
    timestamp: Optional[int],
) -> str:

    if not timestamp:
        return "—"

    remaining = timestamp - now_ts()

    if remaining <= 0:
        return "истёк"

    days = remaining // 86400
    remaining %= 86400

    hours = remaining // 3600
    remaining %= 3600

    minutes = remaining // 60

    parts = []

    if days:
        parts.append(
            f"{days} д."
        )

    if hours:
        parts.append(
            f"{hours} ч."
        )

    if minutes and len(parts) < 2:
        parts.append(
            f"{minutes} мин."
        )

    if not parts:
        return "меньше минуты"

    return " ".join(parts)


# ============================================================
# MAIN KEYBOARD
# ============================================================

def main_keyboard():

    builder = ReplyKeyboardBuilder()

    builder.button(
        text="⭐ Premium"
    )

    builder.button(
        text="👤 Профиль"
    )

    builder.button(
        text="🎟 Промокод"
    )

    builder.adjust(
        2,
        1,
    )

    return builder.as_markup(
        resize_keyboard=True,
        is_persistent=True,
    )


# ============================================================
# CONNECT KEYBOARD
# ============================================================

def connect_keyboard():

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="⚙️ Открыть настройки",
            url="tg://settings",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="🤖 Открыть @SpyNeScamBot",
            url="https://t.me/SpyNeScamBot",
        )
    )

    return builder.as_markup()


# ============================================================
# PREMIUM KEYBOARD
# ============================================================

def premium_keyboard(
    user_id: int,
):

    builder = InlineKeyboardBuilder()

    for plan_key, plan in PLANS.items():

        price = get_plan_price(
            user_id,
            plan_key,
        )

        builder.row(
            InlineKeyboardButton(
                text=(
                    f"{plan['name']} — "
                    f"{price}⭐"
                ),
                callback_data=(
                    f"buy:{plan_key}"
                ),
            )
        )

    return builder.as_markup()


def buy_premium_keyboard():

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="⭐ Купить Premium",
            callback_data="premium",
        )
    )

    return builder.as_markup()


# ============================================================
# USERS
# ============================================================

async def ensure_user(
    user_id: int,
    username: Optional[str],
    first_name: Optional[str],
):

    async with db_lock:

        cursor = db.cursor()

        current_time = now_ts()

        cursor.execute(
            """
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
            """,
            (
                user_id,
                username,
                first_name,
                current_time,
                current_time,
            ),
        )

        db.commit()


def get_user(
    user_id: int,
):

    cursor = db.cursor()

    cursor.execute(
        """
        SELECT *
        FROM users
        WHERE user_id = ?
        """,
        (user_id,),
    )

    return cursor.fetchone()


def is_premium(
    user_id: int,
) -> bool:

    user = get_user(
        user_id
    )

    if not user:
        return False

    if user["premium_forever"]:
        return True

    premium_until = (
        user["premium_until"]
    )

    if not premium_until:
        return False

    return premium_until > now_ts()


# ============================================================
# DISCOUNT
# ============================================================

def has_discount(
    user_id: int,
) -> bool:

    cursor = db.cursor()

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
            PROMO_DISCOUNT,
        ),
    )

    return cursor.fetchone() is not None


def get_plan_price(
    user_id: int,
    plan_key: str,
) -> int:

    plan = PLANS[plan_key]

    price = plan["stars"]

    if has_discount(user_id):

        price = max(
            1,
            int(
                price * 0.90
            ),
        )

    return price


# ============================================================
# PROMO
# ============================================================

async def use_promo(
    user_id: int,
    code: str,
):

    code = (
        code.strip()
        .upper()
    )

    async with db_lock:

        cursor = db.cursor()

        cursor.execute(
            "BEGIN IMMEDIATE"
        )

        try:

            # ------------------------------------------------
            # Этот пользователь уже использовал этот код?
            # ------------------------------------------------

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
                    code,
                ),
            )

            if cursor.fetchone():

                db.rollback()

                return (
                    False,
                    "❌ Вы уже использовали этот промокод.",
                )

            # =================================================
            # N1
            # =================================================

            if code == PROMO_N1:

                cursor.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM promo_uses
                    WHERE promo_code = ?
                    """,
                    (
                        PROMO_N1,
                    ),
                )

                used = int(
                    cursor.fetchone()["count"]
                )

                if used >= PROMO_N1_LIMIT:

                    db.rollback()

                    return (
                        False,
                        "❌ Промокод закончился.",
                    )

                cursor.execute(
                    """
                    INSERT INTO promo_uses (
                        user_id,
                        promo_code,
                        created_at
                    )
                    VALUES (?, ?, ?)
                    """,
                    (
                        user_id,
                        PROMO_N1,
                        now_ts(),
                    ),
                )

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
                    ),
                )

                user = cursor.fetchone()

                if user and not user["premium_forever"]:

                    current_time = now_ts()

                    old_until = (
                        user["premium_until"]
                        or 0
                    )

                    base = max(
                        current_time,
                        old_until,
                    )

                    new_until = (
                        base
                        + PROMO_N1_DAYS * 86400
                    )

                    cursor.execute(
                        """
                        UPDATE users
                        SET
                            premium_until = ?,
                            updated_at = ?
                        WHERE user_id = ?
                        """,
                        (
                            new_until,
                            current_time,
                            user_id,
                        ),
                    )

                db.commit()

                activation_number = used + 1

                return (
                    True,
                    (
                        "🎉 <b>Промокод активирован!</b>\n\n"
                        "⭐ Premium: <b>7 дней</b>\n"
                        f"🎟 Активация: "
                        f"<b>{activation_number}/"
                        f"{PROMO_N1_LIMIT}</b>"
                    ),
                )

            # =================================================
            # DAVE100
            # =================================================

            if code == PROMO_FOREVER:

                cursor.execute(
                    """
                    INSERT INTO promo_uses (
                        user_id,
                        promo_code,
                        created_at
                    )
                    VALUES (?, ?, ?)
                    """,
                    (
                        user_id,
                        PROMO_FOREVER,
                        now_ts(),
                    ),
                )

                cursor.execute(
                    """
                    UPDATE users
                    SET
                        premium_forever = 1,
                        premium_until = NULL,
                        updated_at = ?
                    WHERE user_id = ?
                    """,
                    (
                        now_ts(),
                        user_id,
                    ),
                )

                db.commit()

                return (
                    True,
                    (
                        "🎉 <b>Промокод активирован!</b>\n\n"
                        "⭐ Premium активирован "
                        "<b>навсегда</b>."
                    ),
                )

            # =================================================
            # MET200$
            # =================================================

            if code == PROMO_DISCOUNT:

                cursor.execute(
                    """
                    INSERT INTO promo_uses (
                        user_id,
                        promo_code,
                        created_at
                    )
                    VALUES (?, ?, ?)
                    """,
                    (
                        user_id,
                        PROMO_DISCOUNT,
                        now_ts(),
                    ),
                )

                db.commit()

                prices = []

                for plan in PLANS.values():

                    discounted = max(
                        1,
                        int(
                            plan["stars"] * 0.90
                        ),
                    )

                    prices.append(
                        f"• {plan['name']}: "
                        f"<s>{plan['stars']}⭐</s> "
                        f"<b>{discounted}⭐</b>"
                    )

                return (
                    True,
                    (
                        "🎉 <b>Скидка активирована!</b>\n\n"
                        "⭐ Скидка: <b>10%</b>\n\n"
                        + "\n".join(prices)
                    ),
                )

            db.rollback()

            return (
                False,
                "❌ Промокод не найден.",
            )

        except Exception:

            db.rollback()

            raise


# ============================================================
# START
# ============================================================

START_TEXT = """
<b>🔗 Подключение</b>

1️⃣ Откройте настройки Telegram.

2️⃣ Откройте свой профиль → <b>Изменить</b>.

3️⃣ Нажмите <b>«Автоматизация чатов»</b>.

4️⃣ Найдите <code>@SpyNeScamBot</code>.

5️⃣ Выберите бота и нажмите <b>«Добавить»</b>.

✅ После этого подключение готово.
"""


@dp.message(CommandStart())
async def cmd_start(
    message: Message,
):

    await ensure_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
    )

    # Маленькое первое сообщение
    await message.answer(
        (
            "🐻‍❄️ <b>SpyNeScamBot</b>\n\n"
            "Добро пожаловать! 👋\n"
            "Подключите бота к Telegram."
        ),
        reply_markup=main_keyboard(),
    )

    # Компактная инструкция
    await message.answer(
        START_TEXT,
        reply_markup=connect_keyboard(),
    )


# ============================================================
# PREMIUM
# ============================================================

async def show_premium(
    message: Message,
):

    user_id = (
        message.from_user.id
    )

    await ensure_user(
        user_id,
        message.from_user.username,
        message.from_user.first_name,
    )

    text = (
        "<b>⭐ Premium</b>\n\n"
        "Выберите срок подписки:"
    )

    if has_discount(user_id):

        text += (
            "\n\n"
            "🎟 <b>Скидка 10% активна</b>"
        )

    await message.answer(
        text,
        reply_markup=premium_keyboard(
            user_id
        ),
    )


@dp.message(F.text == "⭐ Premium")
async def premium_button(
    message: Message,
):

    await show_premium(
        message
    )


@dp.message(Command("premium"))
async def premium_command(
    message: Message,
):

    await show_premium(
        message
    )


# ============================================================
# PREMIUM CALLBACK
# ============================================================

@dp.callback_query(F.data == "premium")
async def premium_callback(
    callback: CallbackQuery,
):

    await callback.answer()

    user_id = (
        callback.from_user.id
    )

    await ensure_user(
        user_id,
        callback.from_user.username,
        callback.from_user.first_name,
    )

    text = (
        "<b>⭐ Premium</b>\n\n"
        "Выберите срок подписки:"
    )

    if has_discount(user_id):

        text += (
            "\n\n"
            "🎟 <b>Скидка 10% активна</b>"
        )

    await callback.message.answer(
        text,
        reply_markup=premium_keyboard(
            user_id
        ),
    )


# ============================================================
# ⭐ ПРЯМАЯ ОПЛАТА
# ============================================================

@dp.callback_query(F.data.startswith("buy:"))
async def buy_plan(
    callback: CallbackQuery,
):

    user_id = (
        callback.from_user.id
    )

    plan_key = callback.data.split(
        ":",
        1,
    )[1]

    logger.info(
        "BUY BUTTON CLICKED | "
        "user=%s | plan=%s",
        user_id,
        plan_key,
    )

    if plan_key not in PLANS:

        await callback.answer(
            "❌ Тариф не найден.",
            show_alert=True,
        )

        return

    await ensure_user(
        user_id,
        callback.from_user.username,
        callback.from_user.first_name,
    )

    plan = PLANS[plan_key]

    price = get_plan_price(
        user_id,
        plan_key,
    )

    # --------------------------------------------------------
    # Очень важно:
    # сразу подтверждаем нажатие кнопки.
    # --------------------------------------------------------

    await callback.answer()

    # --------------------------------------------------------
    # Уникальный payload
    # --------------------------------------------------------

    payload = (
        f"premium:"
        f"{plan_key}:"
        f"{user_id}:"
        f"{int(time.time())}"
    )

    logger.info(
        "CREATING STARS INVOICE | "
        "user=%s | plan=%s | price=%s | payload=%s",
        user_id,
        plan_key,
        price,
        payload,
    )

    try:

        # ====================================================
        # СТАРАЯ РАБОЧАЯ СХЕМА
        # ====================================================

        await callback.message.answer_invoice(
            title=(
                f"Premium — "
                f"{plan['name']}"
            ),

            description=(
                f"Premium на "
                f"{plan['name']}."
                + (
                    " Применена скидка 10%."
                    if has_discount(user_id)
                    else ""
                )
            ),

            payload=payload,

            currency="XTR",

            prices=[
                LabeledPrice(
                    label=(
                        f"Premium — "
                        f"{plan['name']}"
                    ),
                    amount=price,
                )
            ],
        )

        logger.info(
            "STARS INVOICE SENT | "
            "user=%s | plan=%s",
            user_id,
            plan_key,
        )

    except Exception as e:

        logger.exception(
            "STARS INVOICE ERROR"
        )

        try:

            await callback.message.answer(
                (
                    "❌ <b>Не удалось создать оплату.</b>\n\n"
                    "Попробуйте выбрать тариф ещё раз."
                ),
                reply_markup=main_keyboard(),
            )

        except Exception:

            pass


# ============================================================
# PRE-CHECKOUT
# ============================================================

@dp.pre_checkout_query()
async def pre_checkout_handler(
    query: PreCheckoutQuery,
):

    logger.info(
        "PRE_CHECKOUT RECEIVED | "
        "user=%s | payload=%s | amount=%s | currency=%s",
        query.from_user.id,
        query.invoice_payload,
        query.total_amount,
        query.currency,
    )

    payload = query.invoice_payload

    # --------------------------------------------------------
    # Проверяем payload
    # --------------------------------------------------------

    if not payload.startswith(
        "premium:"
    ):

        logger.error(
            "PRE_CHECKOUT BAD PAYLOAD"
        )

        await query.answer(
            ok=False,
            error_message="Неверный платёж.",
        )

        return

    parts = payload.split(":")

    if len(parts) != 4:

        logger.error(
            "PRE_CHECKOUT BAD PAYLOAD FORMAT"
        )

        await query.answer(
            ok=False,
            error_message="Неверный платёж.",
        )

        return

    plan_key = parts[1]

    try:

        payload_user_id = int(
            parts[2]
        )

    except ValueError:

        await query.answer(
            ok=False,
            error_message="Неверный пользователь.",
        )

        return

    # --------------------------------------------------------
    # Проверяем пользователя
    # --------------------------------------------------------

    if (
        payload_user_id
        != query.from_user.id
    ):

        logger.error(
            "PRE_CHECKOUT WRONG USER"
        )

        await query.answer(
            ok=False,
            error_message=(
                "Этот счёт принадлежит "
                "другому пользователю."
            ),
        )

        return

    # --------------------------------------------------------
    # Проверяем тариф
    # --------------------------------------------------------

    if plan_key not in PLANS:

        await query.answer(
            ok=False,
            error_message="Тариф не найден.",
        )

        return

    # --------------------------------------------------------
    # Проверяем валюту
    # --------------------------------------------------------

    if query.currency != "XTR":

        logger.error(
            "PRE_CHECKOUT WRONG CURRENCY: %s",
            query.currency,
        )

        await query.answer(
            ok=False,
            error_message="Неверная валюта.",
        )

        return

    # --------------------------------------------------------
    # Проверяем цену
    # --------------------------------------------------------

    expected_price = get_plan_price(
        query.from_user.id,
        plan_key,
    )

    if query.total_amount != expected_price:

        logger.error(
            "PRE_CHECKOUT WRONG PRICE | "
            "expected=%s | received=%s",
            expected_price,
            query.total_amount,
        )

        await query.answer(
            ok=False,
            error_message=(
                "Цена изменилась. "
                "Откройте Premium заново."
            ),
        )

        return

    # ========================================================
    # РАЗРЕШАЕМ ОПЛАТУ
    # ========================================================

    await query.answer(
        ok=True
    )

    logger.info(
        "PRE_CHECKOUT APPROVED | "
        "user=%s | plan=%s | amount=%s",
        query.from_user.id,
        plan_key,
        query.total_amount,
    )


# ============================================================
# SUCCESSFUL PAYMENT
# ============================================================

@dp.message(F.successful_payment)
async def successful_payment_handler(
    message: Message,
):

    logger.info(
        "========================================"
    )

    logger.info(
        "SUCCESSFUL PAYMENT RECEIVED"
    )

    logger.info(
        "========================================"
    )

    payment = message.successful_payment

    if payment is None:

        logger.error(
            "successful_payment is None"
        )

        return

    user_id = (
        message.from_user.id
    )

    payload = (
        payment.invoice_payload
    )

    charge_id = (
        payment.telegram_payment_charge_id
    )

    stars_paid = (
        payment.total_amount
    )

    logger.info(
        "PAYMENT DATA | "
        "user=%s | payload=%s | stars=%s | charge=%s",
        user_id,
        payload,
        stars_paid,
        charge_id,
    )

    # --------------------------------------------------------
    # Payload
    # --------------------------------------------------------

    if not payload.startswith(
        "premium:"
    ):

        logger.error(
            "PAYMENT INVALID PAYLOAD"
        )

        return

    parts = payload.split(":")

    if len(parts) != 4:

        logger.error(
            "PAYMENT INVALID PAYLOAD FORMAT"
        )

        return

    plan_key = parts[1]

    try:

        payload_user_id = int(
            parts[2]
        )

    except ValueError:

        logger.error(
            "PAYMENT INVALID USER ID"
        )

        return

    # --------------------------------------------------------
    # User
    # --------------------------------------------------------

    if payload_user_id != user_id:

        logger.error(
            "PAYMENT USER MISMATCH"
        )

        return

    # --------------------------------------------------------
    # Plan
    # --------------------------------------------------------

    if plan_key not in PLANS:

        logger.error(
            "PAYMENT UNKNOWN PLAN: %s",
            plan_key,
        )

        return

    plan = PLANS[plan_key]

    await ensure_user(
        user_id,
        message.from_user.username,
        message.from_user.first_name,
    )

    # ========================================================
    # АКТИВАЦИЯ
    # ========================================================

    try:

        async with db_lock:

            cursor = db.cursor()

            # ------------------------------------------------
            # Проверяем, не обработан ли платёж
            # ------------------------------------------------

            cursor.execute(
                """
                SELECT id
                FROM payments
                WHERE telegram_payment_charge_id = ?
                LIMIT 1
                """,
                (
                    charge_id,
                ),
            )

            if cursor.fetchone():

                logger.warning(
                    "PAYMENT ALREADY PROCESSED | %s",
                    charge_id,
                )

                return

            # ------------------------------------------------
            # Пользователь
            # ------------------------------------------------

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
                ),
            )

            user = cursor.fetchone()

            if not user:

                logger.error(
                    "PAYMENT USER NOT FOUND"
                )

                return

            current_time = now_ts()

            # ------------------------------------------------
            # Если Premium forever
            # ------------------------------------------------

            if user["premium_forever"]:

                cursor.execute(
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
                        stars_paid,
                        current_time,
                        user_id,
                    ),
                )

                expiration_text = (
                    "♾ Навсегда"
                )

            # ------------------------------------------------
            # Обычная подписка
            # ------------------------------------------------

            else:

                old_until = (
                    user["premium_until"]
                    or 0
                )

                base_time = max(
                    current_time,
                    old_until,
                )

                new_until = (
                    base_time
                    + plan["days"] * 86400
                )

                cursor.execute(
                    """
                    UPDATE users
                    SET
                        premium_until = ?,

                        purchases_count =
                            purchases_count + 1,

                        stars_spent =
                            stars_spent + ?,

                        updated_at = ?

                    WHERE user_id = ?
                    """,
                    (
                        new_until,
                        stars_paid,
                        current_time,
                        user_id,
                    ),
                )

                expiration_text = (
                    format_datetime(
                        new_until
                    )
                )

            # ------------------------------------------------
            # Сохраняем платёж
            # ------------------------------------------------

            cursor.execute(
                """
                INSERT INTO payments (
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
                    current_time,
                ),
            )

            db.commit()

        logger.info(
            "PREMIUM ACTIVATED | "
            "user=%s | plan=%s | stars=%s",
            user_id,
            plan_key,
            stars_paid,
        )

    except Exception:

        logger.exception(
            "PAYMENT DATABASE ERROR"
        )

        try:

            await message.answer(
                (
                    "⚠️ <b>Платёж получен</b>\n\n"
                    "Произошла ошибка при "
                    "активации Premium.\n\n"
                    "ID платежа:\n"
                    f"<code>{escape_text(charge_id)}</code>"
                ),
                reply_markup=main_keyboard(),
            )

        except Exception:
            pass

        return

    # ========================================================
    # ПОДТВЕРЖДЕНИЕ
    # ========================================================

    try:

        await message.answer(
            (
                "✅ <b>Оплата прошла успешно!</b>\n\n"

                f"⭐ Premium: "
                f"<b>{plan['name']}</b>\n"

                f"💰 Оплачено: "
                f"<b>{stars_paid} Stars</b>\n\n"

                "🎉 <b>Premium активирован!</b>\n\n"

                f"📅 Действует до: "
                f"<b>{expiration_text}</b>"
            ),
            reply_markup=main_keyboard(),
        )

        logger.info(
            "PAYMENT CONFIRMATION SENT | "
            "user=%s",
            user_id,
        )

    except Exception:

        logger.exception(
            "PAYMENT CONFIRMATION ERROR"
        )


# ============================================================
# PROFILE
# ============================================================

async def show_profile(
    message: Message,
):

    user_id = (
        message.from_user.id
    )

    await ensure_user(
        user_id,
        message.from_user.username,
        message.from_user.first_name,
    )

    user = get_user(
        user_id
    )

    if not user:
        return

    if user["premium_forever"]:

        premium_status = (
            "⭐ Premium навсегда"
        )

        remaining = "∞"

        expires = "Никогда"

    elif (
        user["premium_until"]
        and
        user["premium_until"] > now_ts()
    ):

        premium_status = (
            "⭐ Premium активен"
        )

        remaining = format_remaining(
            user["premium_until"]
        )

        expires = format_datetime(
            user["premium_until"]
        )

    else:

        premium_status = (
            "❌ Premium не активен"
        )

        remaining = "—"

        expires = "—"

    username = (
        f"@{user['username']}"
        if user["username"]
        else "не указан"
    )

    discount_status = (
        "активна"
        if has_discount(user_id)
        else "нет"
    )

    text = (
        "<b>👤 Профиль</b>\n\n"

        f"🆔 ID: "
        f"<code>{user_id}</code>\n"

        f"👤 Username: "
        f"{escape_text(username)}\n\n"

        f"{premium_status}\n"

        f"⏳ Осталось: "
        f"<b>{remaining}</b>\n"

        f"📅 До: "
        f"<b>{expires}</b>\n\n"

        f"🎟 Скидка 10%: "
        f"<b>{discount_status}</b>\n\n"

        f"🛒 Покупок: "
        f"<b>{user['purchases_count']}</b>\n"

        f"⭐ Потрачено Stars: "
        f"<b>{user['stars_spent']}</b>"
    )

    await message.answer(
        text,
        reply_markup=main_keyboard(),
    )


@dp.message(F.text == "👤 Профиль")
async def profile_button(
    message: Message,
):

    await show_profile(
        message
    )


@dp.message(Command("profile"))
async def profile_command(
    message: Message,
):

    await show_profile(
        message
    )


# ============================================================
# PROMO
# ============================================================

@dp.message(F.text == "🎟 Промокод")
async def promo_button(
    message: Message,
    state: FSMContext,
):

    await state.set_state(
        PromoStates.waiting_code
    )

    await message.answer(
        (
            "<b>🎟 Промокод</b>\n\n"
            "Введите промокод:"
        ),
        reply_markup=main_keyboard(),
    )


@dp.message(PromoStates.waiting_code)
async def process_promo(
    message: Message,
    state: FSMContext,
):

    code = (
        message.text or ""
    ).strip()

    if not code:

        await message.answer(
            "❌ Введите промокод.",
            reply_markup=main_keyboard(),
        )

        return

    await ensure_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
    )

    try:

        success, result = await use_promo(
            message.from_user.id,
            code,
        )

    except Exception:

        logger.exception(
            "PROMO ERROR"
        )

        await state.clear()

        await message.answer(
            (
                "❌ Ошибка при активации "
                "промокода."
            ),
            reply_markup=main_keyboard(),
        )

        return

    await state.clear()

    await message.answer(
        result,
        reply_markup=main_keyboard(),
    )


# ============================================================
# BUSINESS CONNECTION
# ============================================================

@dp.business_connection()
async def business_connection_handler(
    message: Message,
):

    connection = (
        message.business_connection
    )

    current_time = now_ts()

    async with db_lock:

        cursor = db.cursor()

        cursor.execute(
            """
            INSERT INTO business_connections (
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
                connection.user.id,
                1 if connection.can_reply else 0,
                1 if connection.is_enabled else 0,
                current_time,
                current_time,
            ),
        )

        db.commit()

    logger.info(
        "Business connection | "
        "id=%s | user=%s",
        connection.id,
        connection.user.id,
    )


# ============================================================
# SAVE BUSINESS MESSAGE
# ============================================================

async def save_business_message(
    message: Message,
):

    business_connection_id = (
        message.business_connection_id
    )

    if not business_connection_id:
        return

    if message.chat.type != "private":
        return

    cursor = db.cursor()

    cursor.execute(
        """
        SELECT user_chat_id
        FROM business_connections
        WHERE business_connection_id = ?
        """,
        (
            business_connection_id,
        ),
    )

    connection = cursor.fetchone()

    if not connection:
        return

    owner_id = connection["user_chat_id"]

    if (
        message.from_user
        and
        message.from_user.id == owner_id
    ):
        return

    photo_file_id = None
    photo_width = None
    photo_height = None

    if message.photo:

        largest_photo = (
            message.photo[-1]
        )

        photo_file_id = (
            largest_photo.file_id
        )

        photo_width = (
            largest_photo.width
        )

        photo_height = (
            largest_photo.height
        )

    text = message.text

    caption = message.caption

    user_id = None
    username = None
    first_name = None

    if message.from_user:

        user_id = (
            message.from_user.id
        )

        username = (
            message.from_user.username
        )

        first_name = (
            message.from_user.first_name
        )

    async with db_lock:

        cursor = db.cursor()

        cursor.execute(
            """
            INSERT OR REPLACE INTO messages (
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

                created_at
            )
            VALUES (
                ?, ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?
            )
            """,
            (
                business_connection_id,
                message.chat.id,
                message.message_id,

                user_id,
                username,
                first_name,

                text,
                caption,

                photo_file_id,
                photo_width,
                photo_height,

                now_ts(),
            ),
        )

        db.commit()


# ============================================================
# BUSINESS MESSAGE
# ============================================================

@dp.business_message()
async def business_message_handler(
    message: Message,
):

    try:

        await save_business_message(
            message
        )

    except Exception:

        logger.exception(
            "BUSINESS MESSAGE ERROR"
        )


# ============================================================
# EDITED BUSINESS MESSAGE
# ============================================================

@dp.edited_business_message()
async def edited_business_message_handler(
    message: Message,
):

    business_connection_id = (
        message.business_connection_id
    )

    if not business_connection_id:
        return

    if message.chat.type != "private":
        return

    cursor = db.cursor()

    cursor.execute(
        """
        SELECT user_chat_id
        FROM business_connections
        WHERE business_connection_id = ?
        """,
        (
            business_connection_id,
        ),
    )

    connection = cursor.fetchone()

    if not connection:
        return

    owner_id = connection["user_chat_id"]

    if (
        message.from_user
        and
        message.from_user.id == owner_id
    ):
        return

    cursor.execute(
        """
        SELECT *
        FROM messages
        WHERE
            business_connection_id = ?
            AND chat_id = ?
            AND message_id = ?
        """,
        (
            business_connection_id,
            message.chat.id,
            message.message_id,
        ),
    )

    old_message = cursor.fetchone()

    if old_message:

        old_text = (
            old_message["text"]
            or old_message["caption"]
            or ""
        )

    else:

        old_text = ""

    new_text = (
        message.text
        or message.caption
        or ""
    )

    await save_business_message(
        message
    )

    sender_name = "Пользователь"

    if message.from_user:

        if message.from_user.first_name:

            sender_name = (
                message.from_user.first_name
            )

        if message.from_user.username:

            sender_name += (
                f" (@{message.from_user.username})"
            )

    old_display = (
        escape_text(old_text)
        if old_text
        else "—"
    )

    new_display = (
        escape_text(new_text)
        if new_text
        else "—"
    )

    text = (
        "✏️ <b>Сообщение изменено</b>\n\n"
        f"👤 {escape_text(sender_name)}\n\n"
        "<b>Было:</b>\n"
        f"{old_display}\n\n"
        "<b>Стало:</b>\n"
        f"{new_display}"
    )

    try:

        await bot.send_message(
            chat_id=owner_id,
            text=text,
            reply_markup=main_keyboard(),
        )

    except Exception:

        logger.exception(
            "EDIT NOTIFICATION ERROR"
        )


# ============================================================
# DELETED MESSAGE RECORDS
# ============================================================

async def get_deleted_message_records(
    business_connection_id: str,
    chat_id: int,
    message_ids: list[int],
):

    if not message_ids:
        return []

    placeholders = ",".join(
        "?"
        for _ in message_ids
    )

    query = f"""
        SELECT *
        FROM messages
        WHERE
            business_connection_id = ?
            AND chat_id = ?
            AND message_id IN ({placeholders})
        ORDER BY message_id ASC
    """

    cursor = db.cursor()

    cursor.execute(
        query,
        (
            business_connection_id,
            chat_id,
            *message_ids,
        ),
    )

    return cursor.fetchall()


# ============================================================
# DELETED BUSINESS MESSAGES
# ============================================================

@dp.deleted_business_messages()
async def deleted_business_messages_handler(
    update,
):

    business_connection_id = (
        update.business_connection_id
    )

    if not business_connection_id:
        return

    chat = update.chat

    if chat.type != "private":
        return

    cursor = db.cursor()

    cursor.execute(
        """
        SELECT user_chat_id
        FROM business_connections
        WHERE business_connection_id = ?
        """,
        (
            business_connection_id,
        ),
    )

    connection = cursor.fetchone()

    if not connection:
        return

    owner_id = connection["user_chat_id"]

    message_ids = list(
        update.message_ids
    )

    if not message_ids:
        return

    # ========================================================
    # БЕЗ PREMIUM
    # ========================================================

    if not is_premium(owner_id):

        count = len(
            message_ids
        )

        if count == 1:

            text = (
                "🗑 <b>Пользователь удалил сообщение</b>\n\n"
                "Для получения информации "
                "об удалённых сообщениях "
                "необходим Premium."
            )

        else:

            text = (
                f"🗑 <b>Пользователь удалил "
                f"{count} сообщений</b>\n\n"
                "Для получения информации "
                "об удалённых сообщениях "
                "необходим Premium."
            )

        await bot.send_message(
            chat_id=owner_id,
            text=text,
            reply_markup=buy_premium_keyboard(),
        )

        return

    # ========================================================
    # PREMIUM
    # ========================================================

    records = await get_deleted_message_records(
        business_connection_id,
        chat.id,
        message_ids,
    )

    if not records:

        await bot.send_message(
            chat_id=owner_id,
            text=(
                "🗑 <b>Сообщение удалено</b>\n\n"
                "Сохранённых данных "
                "этого сообщения нет."
            ),
            reply_markup=main_keyboard(),
        )

        return

    for record in records:

        sender_name = (
            record["first_name"]
            or "Пользователь"
        )

        if record["username"]:

            sender_name += (
                f" (@{record['username']})"
            )

        # ----------------------------------------------------
        # PHOTO
        # ----------------------------------------------------

        if record["photo_file_id"]:

            caption = (
                record["caption"]
                or ""
            )

            notification = (
                "🗑 <b>Сообщение удалено</b>\n\n"
                f"👤 {escape_text(sender_name)}"
            )

            if caption:

                notification += (
                    "\n\n"
                    f"💬 {escape_text(caption)}"
                )

            try:

                await bot.send_photo(
                    chat_id=owner_id,
                    photo=record["photo_file_id"],
                    caption=notification,
                )

            except Exception:

                logger.exception(
                    "DELETED PHOTO ERROR"
                )

            continue

        # ----------------------------------------------------
        # TEXT
        # ----------------------------------------------------

        message_text = (
            record["text"]
            or record["caption"]
            or ""
        )

        text = (
            "🗑 <b>Сообщение удалено</b>\n\n"
            f"👤 {escape_text(sender_name)}\n\n"
            f"💬 {escape_text(message_text)}"
        )

        await bot.send_message(
            chat_id=owner_id,
            text=text,
            reply_markup=main_keyboard(),
        )


# ============================================================
# HELP
# ============================================================

@dp.message(Command("help"))
async def help_command(
    message: Message,
):

    await message.answer(
        """
<b>🐻‍❄️ SpyNeScamBot</b>

<b>Команды:</b>

/start — запуск
/premium — Premium
/profile — профиль
/help — помощь

<b>Подключение:</b>

Профиль → Изменить →
Автоматизация чатов →
@SpyNeScamBot → Добавить
""",
        reply_markup=connect_keyboard(),
    )


# ============================================================
# UNKNOWN
# ============================================================

@dp.message()
async def unknown_message(
    message: Message,
):

    await message.answer(
        "Выберите нужный раздел.",
        reply_markup=main_keyboard(),
    )


# ============================================================
# COMMANDS
# ============================================================

async def set_commands():

    await bot.set_my_commands(
        [
            BotCommand(
                command="start",
                description="Запустить бота",
            ),

            BotCommand(
                command="premium",
                description="Купить Premium",
            ),

            BotCommand(
                command="profile",
                description="Мой профиль",
            ),

            BotCommand(
                command="help",
                description="Помощь",
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
        "SpyNeScamBot STARTED"
    )

    logger.info(
        "Stars payment system enabled"
    )

    logger.info(
        "========================================"
    )

    await dp.start_polling(
        bot,

        allowed_updates=[
            "message",
            "business_connection",
            "business_message",
            "edited_business_message",
            "deleted_business_messages",
            "pre_checkout_query",
        ],
    )


# ============================================================
# RUN
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
            "FATAL ERROR"
        )

    finally:

        try:
            db.close()
        except Exception:
            pass