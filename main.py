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

BOT_TOKEN = "8893376358:AAHVWJwm8GLJjqz_BWZiFV3CAsquDGsf44c"

DB_PATH = "business_monitor.db"

BOT_USERNAME = "SpyNeScamBot"


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

# N1
# 25 активаций на всех
# Каждая активация даёт 7 дней Premium

PROMO_N1 = "N1"
PROMO_N1_LIMIT = 25
PROMO_N1_DAYS = 7


# Premium навсегда
PROMO_FOREVER = "DAVE100"


# Скидка 10%
PROMO_DISCOUNT = "MET200$"
DISCOUNT_PERCENT = 10


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger(__name__)


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
        parse_mode=ParseMode.HTML
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
                "Миграция старой таблицы promo_uses..."
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

            logger.info(
                "Миграция promo_uses завершена."
            )

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


# ============================================================
# BASIC HELPERS
# ============================================================

def now_ts() -> int:
    return int(time.time())


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
        tz=timezone.utc,
    )

    return dt.strftime(
        "%d.%m.%Y %H:%M"
    )


def format_remaining(
    timestamp: Optional[int],
) -> str:

    if not timestamp:
        return "—"

    remaining = (
        timestamp - now_ts()
    )

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
# НИЖНЕЕ МЕНЮ
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
# КНОПКИ ПОДКЛЮЧЕНИЯ
# ============================================================

def connect_keyboard():

    builder = InlineKeyboardBuilder()

    # Открываем обычные настройки Telegram,
    # а не сразу раздел Business/Secretary.

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
# ПРОВЕРКА СКИДКИ
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


# ============================================================
# ЦЕНА ТАРИФА
# ============================================================

def get_plan_price(
    user_id: int,
    plan_key: str,
) -> int:

    plan = PLANS[plan_key]

    price = plan["stars"]

    if has_discount(
        user_id
    ):

        price = max(
            1,
            int(
                price
                * (
                    100
                    - DISCOUNT_PERCENT
                )
                / 100
            ),
        )

    return price


# ============================================================
# ПРОМОКОДЫ
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

        # ====================================================
        # ВАЖНО:
        #
        # Один пользователь может использовать несколько
        # разных промокодов.
        #
        # Но одну пару:
        #
        # user_id + promo_code
        #
        # повторить нельзя.
        # ====================================================

        cursor.execute(
            "BEGIN IMMEDIATE"
        )

        try:

            # ------------------------------------------------
            # Проверяем именно ЭТОТ промокод
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

            already_used = (
                cursor.fetchone()
            )

            if already_used:

                db.rollback()

                return (
                    False,
                    "❌ Вы уже использовали этот промокод."
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

                # ------------------------------------------------
                # Проверяем общий лимит
                # ------------------------------------------------

                if used >= PROMO_N1_LIMIT:

                    db.rollback()

                    return (
                        False,
                        "❌ Промокод закончился."
                    )

                # ------------------------------------------------
                # Регистрируем активацию
                # ------------------------------------------------

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

                # ------------------------------------------------
                # Получаем Premium
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
                        + PROMO_N1_DAYS
                        * 86400
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

                activation_number = (
                    used + 1
                )

                return (
                    True,
                    (
                        "🎉 <b>Промокод активирован!</b>\n\n"

                        "⭐ Premium: "
                        "<b>7 дней</b>\n"

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
                            plan["stars"]
                            * 0.9
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

                        "⭐ Скидка: "
                        "<b>10%</b>\n\n"

                        + "\n".join(prices)
                    ),
                )

            # =================================================
            # НЕИЗВЕСТНЫЙ ПРОМОКОД
            # =================================================

            db.rollback()

            return (
                False,
                "❌ Промокод не найден."
            )

        except Exception:

            db.rollback()

            raise


# ============================================================
# START
# ============================================================

START_TEXT = """
<b>🐻‍❄️ SpyNeScamBot</b>

Бот поможет получать уведомления о действиях в подключённых чатах.

<b>🔗 Как подключить бота</b>

Для работы необходимо подключить <b>@SpyNeScamBot</b> через настройки Telegram.

<b>📱 Пошаговая инструкция:</b>

<b>1.</b> Нажмите кнопку
<b>«⚙️ Открыть настройки»</b> ниже.

Откроются обычные настройки Telegram.

<b>2.</b> В настройках откройте свой профиль.

Найдите кнопку <b>«Изменить»</b> и нажмите на неё.

<b>3.</b> Найдите пункт:

<b>«Автоматизация чатов»</b>

и откройте его.

<b>4.</b> В поле поиска введите:

<code>@SpyNeScamBot</code>

<b>5.</b> Выберите бота:

<b>my spy bot 🤖</b>
<code>@SpyNeScamBot</code>

<b>6.</b> Выберите чаты, к которым бот должен иметь доступ.

<b>7.</b> Нажмите <b>«Добавить»</b>.

✅ После этого подключение завершено.

Если Telegram не открыл нужный раздел автоматически, просто выполните шаги вручную:

<b>Профиль → Изменить → Автоматизация чатов → @SpyNeScamBot → Добавить</b>
"""


@dp.message(
    CommandStart()
)
async def cmd_start(
    message: Message,
):

    await ensure_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
    )

    # --------------------------------------------------------
    # Нижнее меню отправляется отдельно,
    # потому что Telegram не позволяет одновременно
    # поставить ReplyKeyboard и InlineKeyboard
    # на одно сообщение.
    # --------------------------------------------------------

    await message.answer(
        "🐻‍❄️ <b>SpyNeScamBot</b>",
        reply_markup=main_keyboard(),
    )

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
        "Выберите необходимый срок подписки:"
    )

    if has_discount(
        user_id
    ):

        text += (
            "\n\n"
            "🎟 У вас активна "
            "<b>скидка 10%</b>."
        )

    await message.answer(
        text,
        reply_markup=premium_keyboard(
            user_id
        ),
    )


@dp.message(
    F.text == "⭐ Premium"
)
async def premium_button(
    message: Message,
):

    await show_premium(
        message
    )


@dp.message(
    Command("premium")
)
async def premium_command(
    message: Message,
):

    await show_premium(
        message
    )


# ============================================================
# PREMIUM CALLBACK
# ============================================================

@dp.callback_query(
    F.data == "premium"
)
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
        "Выберите необходимый срок подписки:"
    )

    if has_discount(
        user_id
    ):

        text += (
            "\n\n"
            "🎟 У вас активна "
            "<b>скидка 10%</b>."
        )

    await callback.message.answer(
        text,
        reply_markup=premium_keyboard(
            user_id
        ),
    )


# ============================================================
# BUY PREMIUM
# ============================================================

@dp.callback_query(
    F.data.startswith("buy:")
)
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

    # --------------------------------------------------------
    # Проверяем тариф
    # --------------------------------------------------------

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
    # Уникальный payload
    # --------------------------------------------------------

    payload = (
        f"premium:"
        f"{plan_key}:"
        f"{user_id}:"
        f"{int(time.time())}"
    )

    logger.info(
        "Создание invoice: "
        "user=%s plan=%s price=%s payload=%s",
        user_id,
        plan_key,
        price,
        payload,
    )

    try:

        # Telegram Stars
        # currency = XTR

        prices = [
            LabeledPrice(
                label=(
                    f"Premium "
                    f"{plan['name']}"
                ),
                amount=price,
            )
        ]

        await bot.send_invoice(
            chat_id=user_id,

            title=(
                f"⭐ Premium — "
                f"{plan['name']}"
            ),

            description=(
                f"Premium подписка "
                f"на {plan['name']}."
            ),

            payload=payload,

            currency="XTR",

            prices=prices,
        )

        await callback.answer(
            "💫 Счёт отправлен!"
        )

        logger.info(
            "Invoice отправлен: user=%s",
            user_id,
        )

    except Exception as e:

        logger.exception(
            "ОШИБКА send_invoice: %s",
            e,
        )

        await callback.answer(
            "❌ Не удалось создать счёт.",
            show_alert=True,
        )

        try:

            await bot.send_message(
                chat_id=user_id,
                text=(
                    "❌ <b>Не удалось создать счёт.</b>\n\n"
                    "Попробуйте нажать на тариф ещё раз."
                ),
                reply_markup=main_keyboard(),
            )

        except Exception:
            pass


# ============================================================
# PRE-CHECKOUT
# ============================================================

@dp.pre_checkout_query()
async def process_pre_checkout(
    query: PreCheckoutQuery,
):

    logger.info(
        "PRE-CHECKOUT: "
        "user=%s payload=%s amount=%s currency=%s",
        query.from_user.id,
        query.invoice_payload,
        query.total_amount,
        query.currency,
    )

    payload = (
        query.invoice_payload
    )

    # --------------------------------------------------------
    # Payload
    # --------------------------------------------------------

    if not payload.startswith(
        "premium:"
    ):

        await query.answer(
            ok=False,
            error_message=(
                "Неверный платёж."
            ),
        )

        return

    parts = payload.split(
        ":"
    )

    if len(parts) != 4:

        await query.answer(
            ok=False,
            error_message=(
                "Неверный платёж."
            ),
        )

        return

    plan_key = parts[1]

    # --------------------------------------------------------
    # User ID
    # --------------------------------------------------------

    try:

        payload_user_id = int(
            parts[2]
        )

    except ValueError:

        await query.answer(
            ok=False,
            error_message=(
                "Неверный пользователь."
            ),
        )

        return

    # --------------------------------------------------------
    # Проверяем владельца
    # --------------------------------------------------------

    if (
        payload_user_id
        != query.from_user.id
    ):

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
            error_message=(
                "Тариф не найден."
            ),
        )

        return

    # --------------------------------------------------------
    # Проверяем валюту
    # --------------------------------------------------------

    if query.currency != "XTR":

        await query.answer(
            ok=False,
            error_message=(
                "Неверная валюта."
            ),
        )

        return

    # --------------------------------------------------------
    # Проверяем цену
    # --------------------------------------------------------

    expected_price = get_plan_price(
        query.from_user.id,
        plan_key,
    )

    if (
        query.total_amount
        != expected_price
    ):

        logger.warning(
            "Цена не совпала: "
            "expected=%s actual=%s",
            expected_price,
            query.total_amount,
        )

        await query.answer(
            ok=False,
            error_message=(
                "Цена изменилась. "
                "Откройте Premium "
                "и создайте новый счёт."
            ),
        )

        return

    # --------------------------------------------------------
    # Разрешаем оплату
    # --------------------------------------------------------

    await query.answer(
        ok=True
    )

    logger.info(
        "PRE-CHECKOUT APPROVED: "
        "user=%s plan=%s",
        query.from_user.id,
        plan_key,
    )


# ============================================================
# SUCCESSFUL PAYMENT
# ============================================================

@dp.message(
    F.successful_payment
)
async def successful_payment(
    message: Message,
):

    payment = (
        message.successful_payment
    )

    if not payment:
        return

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
        "SUCCESSFUL PAYMENT: "
        "user=%s payload=%s amount=%s charge=%s",
        message.from_user.id,
        payload,
        stars_paid,
        charge_id,
    )

    # --------------------------------------------------------
    # Проверяем payload
    # --------------------------------------------------------

    if not payload.startswith(
        "premium:"
    ):
        return

    parts = payload.split(
        ":"
    )

    if len(parts) != 4:
        return

    plan_key = parts[1]

    try:

        payload_user_id = int(
            parts[2]
        )

    except ValueError:
        return

    if (
        payload_user_id
        != message.from_user.id
    ):

        logger.warning(
            "Неверный user_id платежа."
        )

        return

    if plan_key not in PLANS:
        return

    plan = PLANS[plan_key]

    user_id = (
        message.from_user.id
    )

    await ensure_user(
        user_id,
        message.from_user.username,
        message.from_user.first_name,
    )

    # ========================================================
    # ОБРАБОТКА ПЛАТЕЖА
    # ========================================================

    async with db_lock:

        cursor = db.cursor()

        # ----------------------------------------------------
        # Защита от повторной выдачи Premium
        # ----------------------------------------------------

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

        existing = (
            cursor.fetchone()
        )

        if existing:

            logger.warning(
                "Платёж уже обработан: %s",
                charge_id,
            )

            return

        # ----------------------------------------------------
        # Получаем пользователя
        # ----------------------------------------------------

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
                "Пользователь не найден: %s",
                user_id,
            )

            return

        # ----------------------------------------------------
        # Premium навсегда
        # ----------------------------------------------------

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
                    now_ts(),
                    user_id,
                ),
            )

        # ----------------------------------------------------
        # Обычный Premium
        # ----------------------------------------------------

        else:

            current_time = now_ts()

            old_until = (
                user["premium_until"]
                or 0
            )

            # Если Premium уже есть,
            # добавляем новый срок к старому.

            base = max(
                current_time,
                old_until,
            )

            new_until = (
                base
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

        # ----------------------------------------------------
        # Сохраняем платёж
        # ----------------------------------------------------

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
                now_ts(),
            ),
        )

        db.commit()

    # ========================================================
    # Получаем актуальный срок
    # ========================================================

    updated_user = get_user(
        user_id
    )

    if updated_user:

        if updated_user["premium_forever"]:

            expiration_text = (
                "♾ Навсегда"
            )

        else:

            expiration_text = (
                format_datetime(
                    updated_user[
                        "premium_until"
                    ]
                )
            )

    else:

        expiration_text = "—"

    # ========================================================
    # ПОДТВЕРЖДЕНИЕ
    # ========================================================

    await message.answer(
        (
            "✅ <b>Оплата прошла успешно!</b>\n\n"

            f"⭐ Тариф: "
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
        "Premium активирован: "
        "user=%s plan=%s",
        user_id,
        plan_key,
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

    # --------------------------------------------------------
    # Premium status
    # --------------------------------------------------------

    if user["premium_forever"]:

        premium_status = (
            "⭐ Premium навсегда"
        )

        remaining = "∞"

        expires = "Никогда"

    elif (
        user["premium_until"]
        and
        user["premium_until"]
        > now_ts()
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


@dp.message(
    F.text == "👤 Профиль"
)
async def profile_button(
    message: Message,
):

    await show_profile(
        message
    )


@dp.message(
    Command("profile")
)
async def profile_command(
    message: Message,
):

    await show_profile(
        message
    )


# ============================================================
# PROMO BUTTON
# ============================================================

@dp.message(
    F.text == "🎟 Промокод"
)
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
            "Введите промокод одним сообщением."
        ),
        reply_markup=main_keyboard(),
    )


# ============================================================
# PROMO PROCESS
# ============================================================

@dp.message(
    PromoStates.waiting_code
)
async def process_promo(
    message: Message,
    state: FSMContext,
):

    code = (
        message.text
        or ""
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

    except Exception as e:

        logger.exception(
            "Ошибка обработки промокода: %s",
            e,
        )

        await state.clear()

        await message.answer(
            (
                "❌ Произошла ошибка "
                "при активации промокода."
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

            ON CONFLICT(
                business_connection_id
            )

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
        "Business connection: "
        "id=%s user=%s enabled=%s can_reply=%s",
        connection.id,
        connection.user.id,
        connection.is_enabled,
        connection.can_reply,
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

    chat = message.chat

    if chat.type != "private":
        return

    # --------------------------------------------------------
    # Находим владельца подключения
    # --------------------------------------------------------

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

    owner_id = (
        connection["user_chat_id"]
    )

    # --------------------------------------------------------
    # Не сохраняем сообщения самого владельца
    # --------------------------------------------------------

    if message.from_user:

        if (
            message.from_user.id
            == owner_id
        ):
            return

    # --------------------------------------------------------
    # PHOTO
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # TEXT
    # --------------------------------------------------------

    text = message.text

    caption = message.caption

    username = None
    first_name = None
    user_id = None

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

    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

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
                chat.id,
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

    except Exception as e:

        logger.exception(
            "Ошибка сохранения business message: %s",
            e,
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

    owner_id = (
        connection["user_chat_id"]
    )

    # --------------------------------------------------------
    # Не логируем собственные сообщения
    # --------------------------------------------------------

    if (
        message.from_user
        and
        message.from_user.id
        == owner_id
    ):
        return

    # --------------------------------------------------------
    # Получаем старую версию
    # --------------------------------------------------------

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

    old_text = ""

    if old_message:

        old_text = (
            old_message["text"]
            or old_message["caption"]
            or ""
        )

    # --------------------------------------------------------
    # Новая версия
    # --------------------------------------------------------

    new_text = (
        message.text
        or message.caption
        or ""
    )

    # --------------------------------------------------------
    # Сохраняем
    # --------------------------------------------------------

    await save_business_message(
        message
    )

    # --------------------------------------------------------
    # Имя пользователя
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Уведомление
    # --------------------------------------------------------

    text = (
        "✏️ <b>Сообщение изменено</b>\n\n"

        f"👤 {escape_text(sender_name)}\n\n"

        "<b>Было:</b>\n"

        f"{escape_text(old_text) "
        f"if old_text else '—'}\n\n"

        "<b>Стало:</b>\n"

        f"{escape_text(new_text) "
        f"if new_text else '—'}"
    )

    try:

        await bot.send_message(
            chat_id=owner_id,
            text=text,
            reply_markup=main_keyboard(),
        )

    except Exception as e:

        logger.exception(
            "Ошибка edit notification: %s",
            e,
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

    owner_id = (
        connection["user_chat_id"]
    )

    message_ids = list(
        update.message_ids
    )

    if not message_ids:
        return

    # ========================================================
    # БЕЗ PREMIUM
    # ========================================================

    if not is_premium(
        owner_id
    ):

        count = len(
            message_ids
        )

        if count == 1:

            text = (
                "🗑 <b>Пользователь "
                "удалил сообщение</b>\n\n"

                "Чтобы получать информацию "
                "об удалённых сообщениях, "
                "необходим Premium."
            )

        else:

            text = (
                f"🗑 <b>Пользователь "
                f"удалил {count} сообщений</b>\n\n"

                "Чтобы получать информацию "
                "об удалённых сообщениях, "
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

    records = (
        await get_deleted_message_records(
            business_connection_id,
            chat.id,
            message_ids,
        )
    )

    if not records:

        await bot.send_message(
            chat_id=owner_id,
            text=(
                "🗑 <b>Сообщение удалено</b>\n\n"
                "Сохранённых данных этого "
                "сообщения нет."
            ),
            reply_markup=main_keyboard(),
        )

        return

    # ========================================================
    # ВОССТАНОВЛЕНИЕ
    # ========================================================

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

                f"👤 "
                f"{escape_text(sender_name)}"
            )

            if caption:

                notification += (
                    "\n\n"
                    f"💬 "
                    f"{escape_text(caption)}"
                )

            try:

                await bot.send_photo(
                    chat_id=owner_id,
                    photo=record["photo_file_id"],
                    caption=notification,
                )

            except Exception as e:

                logger.exception(
                    "Ошибка отправки удалённого фото: %s",
                    e,
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

            f"👤 "
            f"{escape_text(sender_name)}\n\n"

            f"💬 "
            f"{escape_text(message_text)}"
        )

        await bot.send_message(
            chat_id=owner_id,
            text=text,
            reply_markup=main_keyboard(),
        )


# ============================================================
# HELP
# ============================================================

@dp.message(
    Command("help")
)
async def help_command(
    message: Message,
):

    await message.answer(
        """
<b>🐻‍❄️ SpyNeScamBot</b>

<b>Основные команды:</b>

/start — запустить бота
/premium — Premium
/profile — профиль
/help — помощь

<b>Подключение:</b>

Откройте:

<b>Профиль → Изменить → Автоматизация чатов</b>

Затем найдите:

<code>@SpyNeScamBot</code>

и нажмите <b>«Добавить»</b>.
""",
        reply_markup=connect_keyboard(),
    )


# ============================================================
# UNKNOWN MESSAGE
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
# BOT COMMANDS
# ============================================================

async def set_commands():

    commands = [
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

    await bot.set_my_commands(
        commands
    )


# ============================================================
# MAIN
# ============================================================

async def main():

    init_db()

    await set_commands()

    logger.info(
        "======================================"
    )

    logger.info(
        "SpyNeScamBot запускается"
    )

    logger.info(
        "Premium + Stars + Promo + Business"
    )

    logger.info(
        "======================================"
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
            "Бот остановлен."
        )

    finally:

        db.close()