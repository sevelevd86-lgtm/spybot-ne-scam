import asyncio
import logging
import sqlite3
import time

from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = ""

DB_NAME = "business_monitor.db"

# Обычные Telegram ID администраторов.
# Также администратор может получить доступ через Dave200$.
ADMIN_IDS = {
    5018476227,
}

BOT_USERNAME = "SpyNeScamBot"

SETTINGS_URL = "tg://settings/edit"

# Код входа в админ-панель
ADMIN_ACCESS_CODE = "Dave200$"


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("spy_bot")


# =========================================================
# PREMIUM
# =========================================================

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


# =========================================================
# DATABASE
# =========================================================

db = sqlite3.connect(
    DB_NAME,
    check_same_thread=False,
)

db.row_factory = sqlite3.Row

db_lock = asyncio.Lock()


def init_db():

    cursor = db.cursor()

    # =====================================================
    # USERS
    # =====================================================

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
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )

    # =====================================================
    # PAYMENTS
    # =====================================================

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            payload TEXT NOT NULL UNIQUE,
            plan TEXT NOT NULL,
            stars INTEGER NOT NULL,
            telegram_payment_charge_id TEXT,
            created_at INTEGER NOT NULL
        )
        """
    )

    # =====================================================
    # BUSINESS CONNECTIONS
    # =====================================================

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

    # =====================================================
    # MESSAGES
    # =====================================================

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

    # =====================================================
    # PROMO
    # =====================================================

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

    # =====================================================
    # ADMIN ACCESS
    # =====================================================

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_access (
            user_id INTEGER PRIMARY KEY,
            created_at INTEGER NOT NULL
        )
        """
    )

    db.commit()

    # =====================================================
    # PROMO MIGRATION
    # =====================================================

    try:

        row = cursor.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type='table'
            AND name='promo_uses'
            """
        ).fetchone()

        if row and row["sql"]:

            schema = row["sql"].upper()

            old_schema = (
                "USER_ID INTEGER NOT NULL UNIQUE" in schema
                or "UNIQUE(USER_ID)" in schema
                or "UNIQUE (USER_ID)" in schema
            )

            if old_schema:

                logger.warning(
                    "Old promo_uses schema detected. Migrating..."
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
                    INSERT OR IGNORE INTO promo_uses
                    (
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
                    "promo_uses migration completed"
                )

    except Exception:

        logger.exception(
            "promo migration failed"
        )


# =========================================================
# USER FUNCTIONS
# =========================================================

def ensure_user(
    user_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
):

    now = int(time.time())

    db.execute(
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
            now,
            now,
        ),
    )

    db.commit()


def get_user(user_id: int):

    return db.execute(
        """
        SELECT *
        FROM users
        WHERE user_id = ?
        """,
        (user_id,),
    ).fetchone()


def has_premium(user_id: int) -> bool:

    user = get_user(user_id)

    if not user:
        return False

    if int(user["premium_forever"] or 0) == 1:
        return True

    premium_until = user["premium_until"]

    if not premium_until:
        return False

    try:

        until = datetime.fromisoformat(
            premium_until
        )

        if until.tzinfo is None:
            until = until.replace(
                tzinfo=timezone.utc
            )

        return until > datetime.now(timezone.utc)

    except Exception:

        logger.exception(
            "Premium date parsing error user=%s",
            user_id,
        )

        return False


def premium_until_text(user_id: int):

    user = get_user(user_id)

    if not user:
        return "—"

    if int(user["premium_forever"] or 0) == 1:
        return "Навсегда ♾️"

    premium_until = user["premium_until"]

    if not premium_until:
        return "—"

    try:

        until = datetime.fromisoformat(
            premium_until
        )

        if until.tzinfo is None:
            until = until.replace(
                tzinfo=timezone.utc
            )

        if until <= datetime.now(timezone.utc):
            return "Истёк"

        return until.strftime(
            "%d.%m.%Y %H:%M UTC"
        )

    except Exception:

        return "—"


def remaining_text(user_id: int):

    user = get_user(user_id)

    if not user:
        return "—"

    if int(user["premium_forever"] or 0) == 1:
        return "Навсегда ♾️"

    premium_until = user["premium_until"]

    if not premium_until:
        return "—"

    try:

        until = datetime.fromisoformat(
            premium_until
        )

        if until.tzinfo is None:
            until = until.replace(
                tzinfo=timezone.utc
            )

        now = datetime.now(timezone.utc)

        if until <= now:
            return "Истёк"

        seconds = int(
            (until - now).total_seconds()
        )

        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        minutes = (seconds % 3600) // 60

        if days:
            return f"{days} д. {hours} ч."

        if hours:
            return f"{hours} ч. {minutes} мин."

        return f"{minutes} мин."

    except Exception:

        return "—"


def has_discount(user_id: int):

    row = db.execute(
        """
        SELECT 1
        FROM promo_uses
        WHERE user_id = ?
        AND promo_code = 'MET200$'
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()

    return row is not None


def get_plan_price(
    user_id: int,
    plan_key: str,
):

    base_price = PLANS[plan_key]["stars"]

    if has_discount(user_id):

        return max(
            1,
            int(base_price * 0.9),
        )

    return base_price


def activate_premium(
    user_id: int,
    days: int,
):

    now = datetime.now(timezone.utc)

    user = get_user(user_id)

    if not user:
        return None

    if int(user["premium_forever"] or 0) == 1:
        return "forever"

    current_until = None

    if user["premium_until"]:

        try:

            current_until = datetime.fromisoformat(
                user["premium_until"]
            )

            if current_until.tzinfo is None:
                current_until = current_until.replace(
                    tzinfo=timezone.utc
                )

        except Exception:

            current_until = None

    if current_until and current_until > now:

        start = current_until

    else:

        start = now

    new_until = start + timedelta(
        days=days
    )

    db.execute(
        """
        UPDATE users
        SET premium_until = ?,
            updated_at = ?
        WHERE user_id = ?
        """,
        (
            new_until.isoformat(),
            int(time.time()),
            user_id,
        ),
    )

    db.commit()

    return new_until


def deactivate_premium(user_id: int):

    db.execute(
        """
        UPDATE users
        SET premium_until = NULL,
            premium_forever = 0,
            updated_at = ?
        WHERE user_id = ?
        """,
        (
            int(time.time()),
            user_id,
        ),
    )

    db.commit()


# =========================================================
# PROMO
# =========================================================

def use_promo(
    user_id: int,
    code: str,
):

    code = code.strip().upper()

    allowed_codes = {
        "N1",
        "DAVE100",
        "MET200$",
    }

    if code not in allowed_codes:

        return (
            False,
            "❌ Промокод не найден."
        )

    existing = db.execute(
        """
        SELECT id
        FROM promo_uses
        WHERE user_id = ?
        AND promo_code = ?
        """,
        (
            user_id,
            code,
        ),
    ).fetchone()

    if existing:

        return (
            False,
            "❌ Вы уже использовали этот промокод."
        )

    # =====================================================
    # N1
    # =====================================================

    if code == "N1":

        try:

            db.execute(
                "BEGIN IMMEDIATE"
            )

            count = db.execute(
                """
                SELECT COUNT(*)
                FROM promo_uses
                WHERE promo_code = 'N1'
                """
            ).fetchone()[0]

            if count >= 25:

                db.rollback()

                return (
                    False,
                    "❌ Промокод закончился."
                )

            db.execute(
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
                    code,
                    int(time.time()),
                ),
            )

            db.commit()

        except Exception:

            db.rollback()

            logger.exception(
                "N1 promo error"
            )

            return (
                False,
                "❌ Не удалось активировать промокод."
            )

        activate_premium(
            user_id,
            7,
        )

        return (
            True,
            "🎁 Промокод активирован!\n\n"
            "⭐ Вам начислено <b>7 дней Premium</b>."
        )

    # =====================================================
    # DAVE100
    # =====================================================

    if code == "DAVE100":

        try:

            db.execute(
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
                    code,
                    int(time.time()),
                ),
            )

            db.execute(
                """
                UPDATE users
                SET premium_forever = 1,
                    premium_until = NULL,
                    updated_at = ?
                WHERE user_id = ?
                """,
                (
                    int(time.time()),
                    user_id,
                ),
            )

            db.commit()

        except Exception:

            db.rollback()

            return (
                False,
                "❌ Не удалось активировать промокод."
            )

        return (
            True,
            "🎁 Промокод активирован!\n\n"
            "♾️ Premium теперь действует <b>навсегда</b>."
        )

    # =====================================================
    # MET200$
    # =====================================================

    if code == "MET200$":

        try:

            db.execute(
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
                    code,
                    int(time.time()),
                ),
            )

            db.execute(
                """
                UPDATE users
                SET promo_code = ?,
                    updated_at = ?
                WHERE user_id = ?
                """,
                (
                    code,
                    int(time.time()),
                    user_id,
                ),
            )

            db.commit()

        except Exception:

            db.rollback()

            return (
                False,
                "❌ Не удалось активировать промокод."
            )

        return (
            True,
            "🎁 Промокод активирован!\n\n"
            "🔥 Вам доступна <b>скидка 10%</b> на Premium."
        )

    return (
        False,
        "❌ Ошибка."
    )


# =========================================================
# BUSINESS CONNECTION DATABASE
# =========================================================

def save_business_connection(
    connection_id: str,
    user_chat_id: int,
    can_reply: bool,
):

    now = int(time.time())

    db.execute(
        """
        INSERT INTO business_connections (
            business_connection_id,
            user_chat_id,
            can_reply,
            is_enabled,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, 1, ?, ?)

        ON CONFLICT(business_connection_id)
        DO UPDATE SET
            user_chat_id = excluded.user_chat_id,
            can_reply = excluded.can_reply,
            is_enabled = 1,
            updated_at = excluded.updated_at
        """,
        (
            connection_id,
            user_chat_id,
            int(can_reply),
            now,
            now,
        ),
    )

    db.commit()


def get_business_connection(
    connection_id: str,
):

    return db.execute(
        """
        SELECT *
        FROM business_connections
        WHERE business_connection_id = ?
        """,
        (connection_id,),
    ).fetchone()


# =========================================================
# PREMIUM FOR BUSINESS OWNER
# =========================================================

def business_owner_id(
    connection_id: str,
) -> Optional[int]:

    connection = get_business_connection(
        connection_id
    )

    if not connection:
        return None

    return int(
        connection["user_chat_id"]
    )


def business_owner_has_premium(
    connection_id: str,
) -> bool:

    owner_id = business_owner_id(
        connection_id
    )

    if owner_id is None:

        logger.warning(
            "[PREMIUM] Owner not found for connection=%s",
            connection_id,
        )

        return False

    result = has_premium(
        owner_id
    )

    logger.info(
        "[PREMIUM] connection=%s owner=%s premium=%s",
        connection_id,
        owner_id,
        result,
    )

    return result


# =========================================================
# SAVE BUSINESS MESSAGE
# =========================================================

def save_business_message(
    message: Message,
):

    connection_id = message.business_connection_id

    if not connection_id:

        logger.warning(
            "[BUSINESS] Message without connection ID"
        )

        return False

    chat_id = message.chat.id

    message_id = message.message_id

    user_id = (
        message.from_user.id
        if message.from_user
        else None
    )

    username = (
        message.from_user.username
        if message.from_user
        else None
    )

    first_name = (
        message.from_user.first_name
        if message.from_user
        else None
    )

    text = message.text

    caption = message.caption

    photo_file_id = None
    photo_width = None
    photo_height = None

    if message.photo:

        photo = message.photo[-1]

        photo_file_id = photo.file_id
        photo_width = photo.width
        photo_height = photo.height

    if message.text:

        message_type = "text"

    elif message.photo:

        message_type = "photo"

    elif message.document:

        message_type = "document"

    elif message.video:

        message_type = "video"

    elif message.voice:

        message_type = "voice"

    elif message.audio:

        message_type = "audio"

    elif message.sticker:

        message_type = "sticker"

    else:

        message_type = "other"

    now = int(time.time())

    db.execute(
        """
        INSERT INTO messages (
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
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?
        )

        ON CONFLICT(
            business_connection_id,
            chat_id,
            message_id
        )

        DO UPDATE SET
            user_id = excluded.user_id,
            username = excluded.username,
            first_name = excluded.first_name,
            text = excluded.text,
            caption = excluded.caption,
            photo_file_id = excluded.photo_file_id,
            photo_width = excluded.photo_width,
            photo_height = excluded.photo_height,
            message_type = excluded.message_type,
            photo_has_spoiler = excluded.photo_has_spoiler,
            updated_at = excluded.updated_at
        """,
        (
            connection_id,
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
            int(
                getattr(
                    message,
                    "has_media_spoiler",
                    False,
                )
            ),
            now,
            now,
        ),
    )

    db.commit()

    logger.info(
        "[BUSINESS] SAVED "
        "connection=%s chat=%s message=%s type=%s",
        connection_id,
        chat_id,
        message_id,
        message_type,
    )

    return True


def get_saved_message(
    connection_id: str,
    chat_id: int,
    message_id: int,
):

    return db.execute(
        """
        SELECT *
        FROM messages
        WHERE business_connection_id = ?
        AND chat_id = ?
        AND message_id = ?
        """,
        (
            connection_id,
            chat_id,
            message_id,
        ),
    ).fetchone()


# =========================================================
# ADMIN ACCESS
# =========================================================

def is_admin(user_id: int) -> bool:

    if user_id in ADMIN_IDS:
        return True

    row = db.execute(
        """
        SELECT user_id
        FROM admin_access
        WHERE user_id = ?
        """,
        (user_id,),
    ).fetchone()

    return row is not None


def grant_admin(user_id: int):

    db.execute(
        """
        INSERT OR IGNORE INTO admin_access (
            user_id,
            created_at
        )
        VALUES (?, ?)
        """,
        (
            user_id,
            int(time.time()),
        ),
    )

    db.commit()


def revoke_admin(user_id: int):

    db.execute(
        """
        DELETE FROM admin_access
        WHERE user_id = ?
        """,
        (user_id,),
    )

    db.commit()


# =========================================================
# ADMIN STATES
# =========================================================

admin_states = {}


def set_admin_state(
    user_id: int,
    state: str,
):

    admin_states[user_id] = state


def get_admin_state(
    user_id: int,
):

    return admin_states.get(user_id)


def clear_admin_state(
    user_id: int,
):

    admin_states.pop(
        user_id,
        None,
    )


# =========================================================
# ADMIN HELPERS
# =========================================================

async def send_to_admins(
    text: str,
    reply_markup=None,
):

    for admin_id in ADMIN_IDS:

        try:

            await bot.send_message(
                chat_id=admin_id,
                text=text,
                reply_markup=reply_markup,
            )

        except Exception:

            logger.exception(
                "[ADMIN] Failed to send message admin=%s",
                admin_id,
            )


async def send_photo_to_admins(
    photo: str,
    caption: str,
):

    for admin_id in ADMIN_IDS:

        try:

            await bot.send_photo(
                chat_id=admin_id,
                photo=photo,
                caption=caption,
            )

        except Exception:

            logger.exception(
                "[ADMIN] Failed to send photo admin=%s",
                admin_id,
            )


# =========================================================
# UI
# =========================================================

def bottom_keyboard():

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="⭐ Premium",
            callback_data="premium",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="👤 Профиль",
            callback_data="profile",
        ),
        InlineKeyboardButton(
            text="🎟 Промокод",
            callback_data="promo",
        ),
    )

    builder.row(
        InlineKeyboardButton(
            text="🔗 Подключение",
            callback_data="connect",
        )
    )

    return builder.as_markup()


def premium_keyboard(
    user_id: Optional[int] = None,
):

    builder = InlineKeyboardBuilder()

    for key, plan in PLANS.items():

        if user_id is not None:

            price = get_plan_price(
                user_id,
                key,
            )

        else:

            price = plan["stars"]

        builder.row(
            InlineKeyboardButton(
                text=(
                    f"⭐ {plan['name']} — {price}"
                ),
                callback_data=f"buy:{key}",
            )
        )

    builder.row(
        InlineKeyboardButton(
            text="👤 Профиль",
            callback_data="profile",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data="home",
        )
    )

    return builder.as_markup()


def payment_keyboard(
    price: int,
):

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text=f"💳 Оплатить ⭐{price}",
            pay=True,
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


def connect_keyboard():

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="⚙️ Открыть настройки Telegram",
            url=SETTINGS_URL,
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="🤖 Открыть бота",
            url=f"https://t.me/{BOT_USERNAME}",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data="home",
        )
    )

    return builder.as_markup()


# =========================================================
# ADMIN KEYBOARDS
# =========================================================

def admin_keyboard():

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="📊 Статистика",
            callback_data="admin:stats",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="👥 Пользователи",
            callback_data="admin:users",
        ),
        InlineKeyboardButton(
            text="⭐ Premium",
            callback_data="admin:premium",
        ),
    )

    builder.row(
        InlineKeyboardButton(
            text="💳 Платежи",
            callback_data="admin:payments",
        ),
        InlineKeyboardButton(
            text="🎟 Промокоды",
            callback_data="admin:promos",
        ),
    )

    builder.row(
        InlineKeyboardButton(
            text="🔗 Подключения",
            callback_data="admin:connections",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="📢 Рассылка",
            callback_data="admin:broadcast",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="🔍 Найти пользователя",
            callback_data="admin:find",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="➕ Выдать Premium",
            callback_data="admin:give",
        ),
        InlineKeyboardButton(
            text="➖ Снять Premium",
            callback_data="admin:remove",
        ),
    )

    builder.row(
        InlineKeyboardButton(
            text="🐛 Debug",
            callback_data="admin:debug",
        ),
        InlineKeyboardButton(
            text="🧪 Test",
            callback_data="admin:test",
        ),
    )

    builder.row(
        InlineKeyboardButton(
            text="⬅️ Закрыть",
            callback_data="home",
        )
    )

    return builder.as_markup()


def admin_back_keyboard():

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="⬅️ Админ-панель",
            callback_data="admin",
        )
    )

    return builder.as_markup()


# =========================================================
# TEXTS
# =========================================================

START_TEXT = """
<b>🐻‍❄️ SPY BOT</b>

Добро пожаловать.

Бот подключается к Telegram Business и помогает отслеживать сообщения в подключённых чатах.

<b>Что доступно:</b>

📝 новые сообщения
✏️ изменения сообщений
🗑 удалённые сообщения
📷 фотографии
⭐ Premium
🎟 промокоды

Для начала подключите бота к Telegram Business.
"""


def premium_text(
    user_id: int,
):

    active = has_premium(user_id)

    status = (
        "🟢 Активен"
        if active
        else "🔴 Не активен"
    )

    discount = (
        "🟢 10%"
        if has_discount(user_id)
        else "—"
    )

    prices = []

    for key, plan in PLANS.items():

        price = get_plan_price(
            user_id,
            key,
        )

        prices.append(
            f"⭐ {plan['name']} — {price}"
        )

    prices_text = "\n".join(
        prices
    )

    return f"""
<b>⭐ PREMIUM</b>

Статус: <b>{status}</b>

Осталось: <b>{remaining_text(user_id)}</b>

🏷 Скидка: <b>{discount}</b>

<b>Тарифы:</b>

{prices_text}

После оплаты Premium активируется автоматически.
"""


def profile_text(
    user_id: int,
):

    user = get_user(user_id)

    if not user:
        return "Профиль не найден."

    username = user["username"]

    username_text = (
        f"@{username}"
        if username
        else "—"
    )

    active = has_premium(user_id)

    status = (
        "🟢 Активен"
        if active
        else "🔴 Не активен"
    )

    if int(user["premium_forever"] or 0):

        until = "Навсегда ♾️"

    else:

        until = premium_until_text(
            user_id
        )

    discount = (
        "🟢 10%"
        if has_discount(user_id)
        else "—"
    )

    return f"""
<b>👤 ПРОФИЛЬ</b>

🆔 ID: <code>{user_id}</code>

👤 Username: {username_text}

⭐ Premium: <b>{status}</b>

⏳ Осталось: <b>{remaining_text(user_id)}</b>

📅 До: <b>{until}</b>

🏷 Скидка: <b>{discount}</b>

🛒 Покупок: <b>{user['purchases_count']}</b>

⭐ Потрачено: <b>{user['stars_spent']}</b>
"""


# =========================================================
# BOT
# =========================================================

if not BOT_TOKEN:

    raise RuntimeError(
        "Укажи BOT_TOKEN в начале файла."
    )


bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML,
    ),
)

dp = Dispatcher()


# =========================================================
# START
# =========================================================

@dp.message(CommandStart())
async def start_handler(
    message: Message,
):

    if not message.from_user:
        return

    ensure_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
    )

    await message.answer(
        START_TEXT,
        reply_markup=bottom_keyboard(),
    )


# =========================================================
# ADMIN COMMAND
# =========================================================

@dp.message(Command("admin"))
async def admin_command(
    message: Message,
):

    if not message.from_user:
        return

    user_id = message.from_user.id

    if is_admin(user_id):

        await message.answer(
            """
<b>👑 ADMIN PANEL</b>

Доступ разрешён.
""",
            reply_markup=admin_keyboard(),
        )

        return

    set_admin_state(
        user_id,
        "admin_code",
    )

    await message.answer(
        """
<b>🔐 Вход в админ-панель</b>

Введите код доступа следующим сообщением.
"""
    )


# =========================================================
# ADMIN CALLBACK
# =========================================================

@dp.callback_query(F.data == "admin")
async def admin_callback(
    callback: CallbackQuery,
):

    if not is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )

        return

    await callback.answer()

    await callback.message.edit_text(
        """
<b>👑 ADMIN PANEL</b>

Выберите раздел:
""",
        reply_markup=admin_keyboard(),
    )


# =========================================================
# ADMIN CODE HANDLER
# =========================================================

@dp.message(
    F.text.func(
        lambda text: (
            text is not None
            and text.strip() == ADMIN_ACCESS_CODE
        )
    )
)
async def admin_code_handler(
    message: Message,
):

    if not message.from_user:
        return

    user_id = message.from_user.id

    state = get_admin_state(
        user_id
    )

    # Если человек уже админ,
    # код не должен мешать обычным функциям.
    if is_admin(user_id):

        clear_admin_state(
            user_id
        )

        await message.answer(
            "👑 Вы уже являетесь администратором.",
            reply_markup=admin_keyboard(),
        )

        return

    if state != "admin_code":

        return

    grant_admin(
        user_id
    )

    clear_admin_state(
        user_id
    )

    await message.answer(
        """
<b>✅ Доступ разрешён</b>

Вы получили доступ к админ-панели.
""",
        reply_markup=admin_keyboard(),
    )

    logger.info(
        "[ADMIN] New admin access user=%s",
        user_id,
    )


# =========================================================
# HOME
# =========================================================

@dp.callback_query(F.data == "home")
async def home_callback(
    callback: CallbackQuery,
):

    await callback.answer()

    await callback.message.edit_text(
        START_TEXT,
        reply_markup=bottom_keyboard(),
    )


# =========================================================
# PREMIUM
# =========================================================

@dp.message(Command("premium"))
async def premium_command(
    message: Message,
):

    if not message.from_user:
        return

    ensure_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
    )

    await message.answer(
        premium_text(
            message.from_user.id
        ),
        reply_markup=premium_keyboard(
            message.from_user.id
        ),
    )


@dp.callback_query(F.data == "premium")
async def premium_callback(
    callback: CallbackQuery,
):

    ensure_user(
        callback.from_user.id,
        callback.from_user.username,
        callback.from_user.first_name,
    )

    await callback.answer()

    await callback.message.edit_text(
        premium_text(
            callback.from_user.id
        ),
        reply_markup=premium_keyboard(
            callback.from_user.id
        ),
    )


# =========================================================
# BUY PREMIUM
# =========================================================

@dp.callback_query(
    F.data.startswith("buy:")
)
async def buy_plan(
    callback: CallbackQuery,
):

    plan_key = callback.data.split(
        ":",
        1,
    )[1]

    if plan_key not in PLANS:

        await callback.answer(
            "Тариф не найден.",
            show_alert=True,
        )

        return

    user_id = callback.from_user.id

    ensure_user(
        user_id,
        callback.from_user.username,
        callback.from_user.first_name,
    )

    price = get_plan_price(
        user_id,
        plan_key,
    )

    plan = PLANS[plan_key]

    payload = (
        f"premium:"
        f"{plan_key}:"
        f"{user_id}:"
        f"{int(time.time())}:"
        f"{time.time_ns()}"
    )

    await callback.answer()

    try:

        await bot.send_invoice(
            chat_id=user_id,

            title=(
                f"⭐ Premium — "
                f"{plan['name']}"
            ),

            description=(
                "Premium для Telegram Business.\n"
                "Отслеживание изменений и удалений сообщений."
            ),

            payload=payload,

            provider_token="",

            currency="XTR",

            prices=[
                LabeledPrice(
                    label=(
                        f"Premium "
                        f"{plan['name']}"
                    ),
                    amount=price,
                )
            ],

            reply_markup=payment_keyboard(
                price
            ),
        )

        logger.info(
            "[PAYMENT] Invoice sent "
            "user=%s plan=%s price=%s",
            user_id,
            plan_key,
            price,
        )

    except Exception:

        logger.exception(
            "[PAYMENT] Invoice error"
        )

        await callback.message.answer(
            "❌ Не удалось создать оплату.\n"
            "Попробуйте ещё раз."
        )


# =========================================================
# PRE CHECKOUT
# =========================================================

@dp.pre_checkout_query()
async def pre_checkout_handler(
    query: PreCheckoutQuery,
):

    logger.info(
        "[PAYMENT] PRE_CHECKOUT "
        "user=%s payload=%s amount=%s currency=%s",
        query.from_user.id,
        query.invoice_payload,
        query.total_amount,
        query.currency,
    )

    payload = query.invoice_payload

    parts = payload.split(":")

    if len(parts) != 5:

        await query.answer(
            ok=False,
            error_message="Некорректный платёж.",
        )

        return

    prefix = parts[0]
    plan_key = parts[1]
    payload_user_id = parts[2]

    if prefix != "premium":

        await query.answer(
            ok=False,
            error_message="Некорректный платёж.",
        )

        return

    try:

        payload_user_id = int(
            payload_user_id
        )

    except ValueError:

        await query.answer(
            ok=False,
            error_message="Некорректный пользователь.",
        )

        return

    if payload_user_id != query.from_user.id:

        await query.answer(
            ok=False,
            error_message=(
                "Этот платёж принадлежит "
                "другому пользователю."
            ),
        )

        return

    if plan_key not in PLANS:

        await query.answer(
            ok=False,
            error_message="Тариф не найден.",
        )

        return

    if query.currency != "XTR":

        await query.answer(
            ok=False,
            error_message="Неверная валюта.",
        )

        return

    expected_price = get_plan_price(
        query.from_user.id,
        plan_key,
    )

    if query.total_amount != expected_price:

        await query.answer(
            ok=False,
            error_message="Неверная сумма платежа.",
        )

        return

    await query.answer(
        ok=True
    )


# =========================================================
# SUCCESSFUL PAYMENT
# =========================================================

@dp.message(
    F.successful_payment
)
async def successful_payment_handler(
    message: Message,
):

    payment = message.successful_payment

    if not payment:
        return

    if not message.from_user:
        return

    user_id = message.from_user.id

    if payment.currency != "XTR":
        return

    payload = payment.invoice_payload

    parts = payload.split(":")

    if len(parts) != 5:
        return

    prefix = parts[0]
    plan_key = parts[1]
    payload_user_id = parts[2]

    if prefix != "premium":
        return

    if str(user_id) != payload_user_id:
        return

    if plan_key not in PLANS:
        return

    charge_id = (
        payment.telegram_payment_charge_id
    )

    exists = db.execute(
        """
        SELECT id
        FROM payments
        WHERE telegram_payment_charge_id = ?
        """,
        (charge_id,),
    ).fetchone()

    if exists:

        logger.warning(
            "[PAYMENT] Duplicate ignored: %s",
            charge_id,
        )

        return

    stars_paid = int(
        payment.total_amount
    )

    plan = PLANS[plan_key]

    activated = activate_premium(
        user_id,
        plan["days"],
    )

    if activated == "forever":

        until_text = "Навсегда ♾️"

    else:

        until_text = (
            activated.strftime(
                "%d.%m.%Y %H:%M UTC"
            )
            if activated
            else "—"
        )

    db.execute(
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
            int(time.time()),
        ),
    )

    db.execute(
        """
        UPDATE users
        SET purchases_count =
                purchases_count + 1,
            stars_spent =
                stars_spent + ?,
            updated_at = ?
        WHERE user_id = ?
        """,
        (
            stars_paid,
            int(time.time()),
            user_id,
        ),
    )

    db.commit()

    logger.info(
        "[PAYMENT] SUCCESS "
        "user=%s plan=%s stars=%s charge=%s",
        user_id,
        plan_key,
        stars_paid,
        charge_id,
    )

    await message.answer(
        f"""
<b>✅ Оплата прошла успешно!</b>

⭐ Premium: <b>{plan['name']}</b>

💰 Оплачено: <b>{stars_paid} Stars</b>

🎉 Premium активирован!

📅 Действует до:
<b>{until_text}</b>
""",
        reply_markup=bottom_keyboard(),
    )

    await send_to_admins(
        f"""
💰 <b>Новая покупка Premium</b>

👤 User ID:
<code>{user_id}</code>

⭐ Тариф:
<b>{plan['name']}</b>

💰 Оплачено:
<b>{stars_paid} Stars</b>

🧾 Charge ID:
<code>{charge_id}</code>
"""
    )


# =========================================================
# PROFILE
# =========================================================

@dp.callback_query(F.data == "profile")
async def profile_callback(
    callback: CallbackQuery,
):

    ensure_user(
        callback.from_user.id,
        callback.from_user.username,
        callback.from_user.first_name,
    )

    await callback.answer()

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="⭐ Premium",
            callback_data="premium",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data="home",
        )
    )

    await callback.message.edit_text(
        profile_text(
            callback.from_user.id
        ),
        reply_markup=builder.as_markup(),
    )


# =========================================================
# CONNECT
# =========================================================

@dp.callback_query(F.data == "connect")
async def connect_callback(
    callback: CallbackQuery,
):

    await callback.answer()

    text = """
<b>🔗 ПОДКЛЮЧЕНИЕ</b>

Чтобы бот начал отслеживать ваши Business-чаты:

<b>1.</b> Откройте настройки Telegram.

<b>2.</b> Откройте свой профиль → <b>Изменить</b>.

<b>3.</b> Найдите раздел
<b>«Автоматизация чатов»</b>.

<b>4.</b> Найдите нашего бота
<b>@SpyNeScamBot</b>.

<b>5.</b> Выберите его и нажмите
<b>«Добавить»</b>.

<b>6.</b> Разрешите боту необходимые права
для работы с сообщениями.

После подключения новые сообщения будут
приходить боту через Telegram Business.
"""

    await callback.message.edit_text(
        text,
        reply_markup=connect_keyboard(),
    )


# =========================================================
# PROMO MENU
# =========================================================

@dp.callback_query(F.data == "promo")
async def promo_callback(
    callback: CallbackQuery,
):

    await callback.answer()

    await callback.message.answer(
        """
<b>🎟 ПРОМОКОД</b>

Отправьте промокод следующим сообщением.

Если код действителен, Premium или скидка
активируются автоматически.
"""
    )


# =========================================================
# PROMO MESSAGE
# =========================================================

@dp.message(
    F.text.func(
        lambda text: (
            text is not None
            and text.strip().upper()
            in {
                "N1",
                "DAVE100",
                "MET200$",
            }
        )
    )
)
async def promo_message_handler(
    message: Message,
):

    if not message.from_user:
        return

    # Если человек сейчас вводит код админки,
    # сначала проверяем именно админское состояние.
    if (
        get_admin_state(
            message.from_user.id
        )
        == "admin_code"
    ):

        return

    ensure_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
    )

    success, result = use_promo(
        message.from_user.id,
        message.text,
    )

    await message.answer(
        result,
        reply_markup=bottom_keyboard(),
    )


# =========================================================
# ADMIN STATS
# =========================================================

async def show_admin_stats(
    target: Message,
):

    users_count = db.execute(
        """
        SELECT COUNT(*)
        FROM users
        """
    ).fetchone()[0]

    premium_count = db.execute(
        """
        SELECT COUNT(*)
        FROM users
        WHERE premium_forever = 1
        OR (
            premium_until IS NOT NULL
            AND premium_until > ?
        )
        """,
        (
            datetime.now(timezone.utc).isoformat(),
        ),
    ).fetchone()[0]

    connections_count = db.execute(
        """
        SELECT COUNT(*)
        FROM business_connections
        WHERE is_enabled = 1
        """
    ).fetchone()[0]

    messages_count = db.execute(
        """
        SELECT COUNT(*)
        FROM messages
        """
    ).fetchone()[0]

    payments_count = db.execute(
        """
        SELECT COUNT(*)
        FROM payments
        """
    ).fetchone()[0]

    stars_total = db.execute(
        """
        SELECT COALESCE(SUM(stars), 0)
        FROM payments
        """
    ).fetchone()[0]

    n1_count = db.execute(
        """
        SELECT COUNT(*)
        FROM promo_uses
        WHERE promo_code = 'N1'
        """
    ).fetchone()[0]

    promo_total = db.execute(
        """
        SELECT COUNT(*)
        FROM promo_uses
        """
    ).fetchone()[0]

    text = f"""
<b>📊 СТАТИСТИКА</b>

👥 Пользователей: <b>{users_count}</b>

⭐ Активный Premium: <b>{premium_count}</b>

🔗 Business-подключений: <b>{connections_count}</b>

💬 Сохранено сообщений: <b>{messages_count}</b>

💳 Платежей: <b>{payments_count}</b>

⭐ Получено Stars: <b>{stars_total}</b>

🎟 Использований промокодов: <b>{promo_total}</b>

🎁 N1: <b>{n1_count}/25</b>
"""

    await target.answer(
        text,
        reply_markup=admin_back_keyboard(),
    )


# =========================================================
# ADMIN CALLBACKS
# =========================================================

@dp.callback_query(F.data == "admin:stats")
async def admin_stats_callback(
    callback: CallbackQuery,
):

    if not is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )

        return

    await callback.answer()

    await show_admin_stats(
        callback.message
    )


# =========================================================
# ADMIN USERS
# =========================================================

@dp.callback_query(F.data == "admin:users")
async def admin_users_callback(
    callback: CallbackQuery,
):

    if not is_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    await callback.answer()

    users = db.execute(
        """
        SELECT *
        FROM users
        ORDER BY updated_at DESC
        LIMIT 20
        """
    ).fetchall()

    if not users:

        text = "👥 Пользователей пока нет."

    else:

        lines = [
            "<b>👥 ПОСЛЕДНИЕ ПОЛЬЗОВАТЕЛИ</b>\n"
        ]

        for user in users:

            premium = (
                "🟢"
                if has_premium(
                    user["user_id"]
                )
                else "🔴"
            )

            name = (
                user["first_name"]
                or user["username"]
                or "Без имени"
            )

            lines.append(
                f"{premium} "
                f"<b>{name}</b> — "
                f"<code>{user['user_id']}</code>"
            )

        text = "\n".join(lines)

    await callback.message.edit_text(
        text,
        reply_markup=admin_back_keyboard(),
    )


# =========================================================
# ADMIN PREMIUM
# =========================================================

@dp.callback_query(F.data == "admin:premium")
async def admin_premium_callback(
    callback: CallbackQuery,
):

    if not is_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    await callback.answer()

    users = db.execute(
        """
        SELECT *
        FROM users
        WHERE premium_forever = 1
        OR (
            premium_until IS NOT NULL
            AND premium_until > ?
        )
        ORDER BY updated_at DESC
        LIMIT 30
        """,
        (
            datetime.now(timezone.utc).isoformat(),
        ),
    ).fetchall()

    if not users:

        text = (
            "<b>⭐ PREMIUM USERS</b>\n\n"
            "Активных Premium пользователей нет."
        )

    else:

        lines = [
            "<b>⭐ PREMIUM USERS</b>\n"
        ]

        for user in users:

            lines.append(
                f"👤 <code>{user['user_id']}</code> — "
                f"<b>{remaining_text(user['user_id'])}</b>"
            )

        text = "\n".join(lines)

    await callback.message.edit_text(
        text,
        reply_markup=admin_back_keyboard(),
    )


# =========================================================
# ADMIN PAYMENTS
# =========================================================

@dp.callback_query(F.data == "admin:payments")
async def admin_payments_callback(
    callback: CallbackQuery,
):

    if not is_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    await callback.answer()

    payments = db.execute(
        """
        SELECT *
        FROM payments
        ORDER BY created_at DESC
        LIMIT 20
        """
    ).fetchall()

    if not payments:

        text = (
            "<b>💳 ПЛАТЕЖИ</b>\n\n"
            "Платежей пока нет."
        )

    else:

        lines = [
            "<b>💳 ПОСЛЕДНИЕ ПЛАТЕЖИ</b>\n"
        ]

        for payment in payments:

            date = datetime.fromtimestamp(
                payment["created_at"],
                timezone.utc,
            ).strftime(
                "%d.%m %H:%M"
            )

            plan = PLANS.get(
                payment["plan"],
                {},
            ).get(
                "name",
                payment["plan"],
            )

            lines.append(
                f"⭐ <b>{payment['stars']}</b> — "
                f"{plan}\n"
                f"👤 <code>{payment['user_id']}</code> "
                f"• {date}"
            )

        text = "\n\n".join(lines)

    await callback.message.edit_text(
        text,
        reply_markup=admin_back_keyboard(),
    )


# =========================================================
# ADMIN PROMOS
# =========================================================

@dp.callback_query(F.data == "admin:promos")
async def admin_promos_callback(
    callback: CallbackQuery,
):

    if not is_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    await callback.answer()

    n1_count = db.execute(
        """
        SELECT COUNT(*)
        FROM promo_uses
        WHERE promo_code = 'N1'
        """
    ).fetchone()[0]

    dave_count = db.execute(
        """
        SELECT COUNT(*)
        FROM promo_uses
        WHERE promo_code = 'DAVE100'
        """
    ).fetchone()[0]

    met_count = db.execute(
        """
        SELECT COUNT(*)
        FROM promo_uses
        WHERE promo_code = 'MET200$'
        """
    ).fetchone()[0]

    text = f"""
<b>🎟 ПРОМОКОДЫ</b>

<b>N1</b>
7 дней Premium
Использований: <b>{n1_count}/25</b>

<b>DAVE100</b>
Premium навсегда
Использований: <b>{dave_count}</b>

<b>MET200$</b>
Скидка 10%
Использований: <b>{met_count}</b>
"""

    await callback.message.edit_text(
        text,
        reply_markup=admin_back_keyboard(),
    )


# =========================================================
# ADMIN CONNECTIONS
# =========================================================

@dp.callback_query(F.data == "admin:connections")
async def admin_connections_callback(
    callback: CallbackQuery,
):

    if not is_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    await callback.answer()

    connections = db.execute(
        """
        SELECT *
        FROM business_connections
        ORDER BY updated_at DESC
        """
    ).fetchall()

    if not connections:

        text = (
            "<b>🔗 BUSINESS CONNECTIONS</b>\n\n"
            "Подключений нет."
        )

    else:

        lines = [
            "<b>🔗 BUSINESS CONNECTIONS</b>\n"
        ]

        for connection in connections:

            premium = has_premium(
                connection["user_chat_id"]
            )

            status = (
                "🟢 Premium"
                if premium
                else "🔴 Без Premium"
            )

            enabled = (
                "🟢"
                if connection["is_enabled"]
                else "🔴"
            )

            lines.append(
                f"{enabled} "
                f"<b>User:</b> "
                f"<code>{connection['user_chat_id']}</code>\n"
                f"🔗 <code>"
                f"{connection['business_connection_id']}"
                f"</code>\n"
                f"⭐ {status}\n"
                f"💬 Can reply: "
                f"<b>{connection['can_reply']}</b>"
            )

        text = "\n\n".join(lines)

    await callback.message.edit_text(
        text,
        reply_markup=admin_back_keyboard(),
    )


# =========================================================
# ADMIN FIND USER
# =========================================================

@dp.callback_query(F.data == "admin:find")
async def admin_find_callback(
    callback: CallbackQuery,
):

    if not is_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    set_admin_state(
        callback.from_user.id,
        "find_user",
    )

    await callback.answer()

    await callback.message.answer(
        """
<b>🔍 ПОИСК ПОЛЬЗОВАТЕЛЯ</b>

Отправьте Telegram ID пользователя.
"""
    )


# =========================================================
# ADMIN GIVE PREMIUM
# =========================================================

@dp.callback_query(F.data == "admin:give")
async def admin_give_callback(
    callback: CallbackQuery,
):

    if not is_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    set_admin_state(
        callback.from_user.id,
        "give_premium",
    )

    await callback.answer()

    await callback.message.answer(
        """
<b>➕ ВЫДАТЬ PREMIUM</b>

Отправьте в одном сообщении:

<code>ID дни</code>

Например:

<code>123456789 30</code>

Для Premium навсегда:

<code>123456789 forever</code>
"""
    )


# =========================================================
# ADMIN REMOVE PREMIUM
# =========================================================

@dp.callback_query(F.data == "admin:remove")
async def admin_remove_callback(
    callback: CallbackQuery,
):

    if not is_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    set_admin_state(
        callback.from_user.id,
        "remove_premium",
    )

    await callback.answer()

    await callback.message.answer(
        """
<b>➖ СНЯТЬ PREMIUM</b>

Отправьте Telegram ID пользователя.
"""
    )


# =========================================================
# ADMIN BROADCAST
# =========================================================

@dp.callback_query(F.data == "admin:broadcast")
async def admin_broadcast_callback(
    callback: CallbackQuery,
):

    if not is_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    set_admin_state(
        callback.from_user.id,
        "broadcast",
    )

    await callback.answer()

    await callback.message.answer(
        """
<b>📢 РАССЫЛКА</b>

Отправьте сообщение, которое нужно
разослать всем пользователям.

Текст, фото с подписью и другие типы
сообщений можно переслать через
«Переслать» или отправить напрямую.
"""
    )


# =========================================================
# ADMIN DEBUG CALLBACK
# =========================================================

@dp.callback_query(F.data == "admin:debug")
async def admin_debug_callback(
    callback: CallbackQuery,
):

    if not is_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    await callback.answer()

    connections = db.execute(
        """
        SELECT *
        FROM business_connections
        ORDER BY updated_at DESC
        """
    ).fetchall()

    messages_count = db.execute(
        """
        SELECT COUNT(*)
        FROM messages
        """
    ).fetchone()[0]

    users_count = db.execute(
        """
        SELECT COUNT(*)
        FROM users
        """
    ).fetchone()[0]

    premium_count = db.execute(
        """
        SELECT COUNT(*)
        FROM users
        WHERE premium_forever = 1
        OR (
            premium_until IS NOT NULL
            AND premium_until > ?
        )
        """,
        (
            datetime.now(timezone.utc).isoformat(),
        ),
    ).fetchone()[0]

    text = (
        "<b>🔧 SPY BOT DEBUG</b>\n\n"
        f"👥 Users: <b>{users_count}</b>\n"
        f"⭐ Premium: <b>{premium_count}</b>\n"
        f"🔗 Connections: <b>{len(connections)}</b>\n"
        f"💬 Saved messages: <b>{messages_count}</b>\n\n"
    )

    for connection in connections:

        owner_id = connection["user_chat_id"]

        text += (
            "━━━━━━━━━━━━━━\n"
            f"🆔 Connection:\n"
            f"<code>"
            f"{connection['business_connection_id']}"
            f"</code>\n"
            f"👤 User:\n"
            f"<code>{owner_id}</code>\n"
            f"⭐ Premium:\n"
            f"<b>{has_premium(owner_id)}</b>\n"
            f"💬 Can reply: "
            f"<b>{connection['can_reply']}</b>\n"
            f"🟢 Enabled: "
            f"<b>{connection['is_enabled']}</b>\n\n"
        )

    chunks = []

    while len(text) > 3800:

        cut = text.rfind(
            "\n",
            0,
            3800,
        )

        if cut <= 0:
            cut = 3800

        chunks.append(
            text[:cut]
        )

        text = text[cut:]

    chunks.append(text)

    for chunk in chunks:

        await callback.message.answer(
            chunk,
            reply_markup=admin_back_keyboard(),
        )


# =========================================================
# ADMIN TEST CALLBACK
# =========================================================

@dp.callback_query(F.data == "admin:test")
async def admin_test_callback(
    callback: CallbackQuery,
):

    if not is_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    await callback.answer()

    await callback.message.answer(
        """
<b>🟢 SPY BOT TEST</b>

Bot: 🟢
Business monitoring: 🟢
Deleted messages: 🟢
Edited messages: 🟢
Premium: 🟢
Stars: 🟢
Promo: 🟢
Admin: 🟢
""",
        reply_markup=admin_back_keyboard(),
    )


# =========================================================
# ADMIN TEXT ACTIONS
# =========================================================

@dp.message()
async def admin_text_actions(
    message: Message,
):

    if not message.from_user:
        return

    user_id = message.from_user.id

    if not is_admin(user_id):
        return

    state = get_admin_state(
        user_id
    )

    if not state:
        return

    # =====================================================
    # FIND USER
    # =====================================================

    if state == "find_user":

        clear_admin_state(
            user_id
        )

        try:

            target_id = int(
                message.text.strip()
            )

        except Exception:

            await message.answer(
                "❌ Неверный Telegram ID.",
                reply_markup=admin_keyboard(),
            )

            return

        user = get_user(
            target_id
        )

        if not user:

            await message.answer(
                f"""
<b>👤 Пользователь не найден</b>

ID:
<code>{target_id}</code>
""",
                reply_markup=admin_keyboard(),
            )

            return

        username = (
            f"@{user['username']}"
            if user["username"]
            else "—"
        )

        await message.answer(
            f"""
<b>👤 ПОЛЬЗОВАТЕЛЬ</b>

🆔 ID:
<code>{target_id}</code>

👤 Username:
{username}

⭐ Premium:
<b>{"🟢 Активен" if has_premium(target_id) else "🔴 Не активен"}</b>

⏳ Осталось:
<b>{remaining_text(target_id)}</b>

📅 До:
<b>{premium_until_text(target_id)}</b>

🏷 Скидка:
<b>{"🟢 10%" if has_discount(target_id) else "—"}</b>

🛒 Покупок:
<b>{user['purchases_count']}</b>

⭐ Потрачено:
<b>{user['stars_spent']}</b>
""",
            reply_markup=admin_keyboard(),
        )

        return

    # =====================================================
    # GIVE PREMIUM
    # =====================================================

    if state == "give_premium":

        clear_admin_state(
            user_id
        )

        parts = message.text.strip().split()

        if len(parts) != 2:

            await message.answer(
                "❌ Формат: <code>ID дни</code>",
                reply_markup=admin_keyboard(),
            )

            return

        try:

            target_id = int(
                parts[0]
            )

        except Exception:

            await message.answer(
                "❌ Неверный ID.",
                reply_markup=admin_keyboard(),
            )

            return

        ensure_user(
            target_id
        )

        if parts[1].lower() == "forever":

            db.execute(
                """
                UPDATE users
                SET premium_forever = 1,
                    premium_until = NULL,
                    updated_at = ?
                WHERE user_id = ?
                """,
                (
                    int(time.time()),
                    target_id,
                ),
            )

            db.commit()

            result = (
                "♾️ Premium выдан навсегда."
            )

        else:

            try:

                days = int(
                    parts[1]
                )

                if days <= 0:
                    raise ValueError

            except Exception:

                await message.answer(
                    "❌ Количество дней должно быть положительным.",
                    reply_markup=admin_keyboard(),
                )

                return

            activated = activate_premium(
                target_id,
                days,
            )

            result = (
                f"⭐ Premium выдан на "
                f"<b>{days} дней</b>.\n\n"
                f"До: <b>"
                f"{premium_until_text(target_id)}"
                f"</b>"
            )

        await message.answer(
            f"""
<b>✅ ГОТОВО</b>

Пользователь:
<code>{target_id}</code>

{result}
""",
            reply_markup=admin_keyboard(),
        )

        try:

            await bot.send_message(
                chat_id=target_id,
                text=(
                    "⭐ <b>Premium активирован</b>\n\n"
                    "Администратор активировал для вас Premium.\n\n"
                    f"⏳ {remaining_text(target_id)}"
                ),
                reply_markup=bottom_keyboard(),
            )

        except Exception:

            logger.exception(
                "Could not notify user about admin Premium"
            )

        return

    # =====================================================
    # REMOVE PREMIUM
    # =====================================================

    if state == "remove_premium":

        clear_admin_state(
            user_id
        )

        try:

            target_id = int(
                message.text.strip()
            )

        except Exception:

            await message.answer(
                "❌ Неверный Telegram ID.",
                reply_markup=admin_keyboard(),
            )

            return

        deactivate_premium(
            target_id
        )

        await message.answer(
            f"""
<b>✅ PREMIUM СНЯТ</b>

Пользователь:
<code>{target_id}</code>
""",
            reply_markup=admin_keyboard(),
        )

        try:

            await bot.send_message(
                chat_id=target_id,
                text=(
                    "ℹ️ Ваш Premium был отключён "
                    "администратором."
                ),
                reply_markup=bottom_keyboard(),
            )

        except Exception:

            logger.exception(
                "Could not notify user about Premium removal"
            )

        return

    # =====================================================
    # BROADCAST
    # =====================================================

    if state == "broadcast":

        clear_admin_state(
            user_id
        )

        users = db.execute(
            """
            SELECT user_id
            FROM users
            """
        ).fetchall()

        sent = 0
        failed = 0

        await message.answer(
            f"📢 Начинаю рассылку для {len(users)} пользователей..."
        )

        for user in users:

            target_id = user["user_id"]

            try:

                await bot.copy_message(
                    chat_id=target_id,
                    from_chat_id=message.chat.id,
                    message_id=message.message_id,
                )

                sent += 1

            except Exception:

                failed += 1

            await asyncio.sleep(
                0.05
            )

        await message.answer(
            f"""
<b>📢 РАССЫЛКА ЗАВЕРШЕНА</b>

✅ Отправлено: <b>{sent}</b>

❌ Ошибок: <b>{failed}</b>
""",
            reply_markup=admin_keyboard(),
        )

        return


# =========================================================
# BUSINESS CONNECTION
# =========================================================

@dp.business_connection()
async def business_connection_handler(
    connection,
):

    try:

        connection_id = connection.id

        user_chat_id = connection.user.id

        rights = getattr(
            connection,
            "rights",
            None,
        )

        can_reply = False

        if rights is not None:

            can_reply = bool(
                getattr(
                    rights,
                    "can_reply",
                    False,
                )
            )

        old_can_reply = getattr(
            connection,
            "can_reply",
            None,
        )

        if old_can_reply is not None:

            can_reply = bool(
                old_can_reply
            )

        save_business_connection(
            connection_id,
            user_chat_id,
            can_reply,
        )

        ensure_user(
            user_chat_id
        )

        logger.info(
            "[BUSINESS CONNECTION] "
            "CONNECTED id=%s user=%s "
            "can_reply=%s",
            connection_id,
            user_chat_id,
            can_reply,
        )

        await send_to_admins(
            (
                "🔗 <b>Business подключение</b>\n\n"
                f"🆔 Connection: "
                f"<code>{connection_id}</code>\n"
                f"👤 User ID: "
                f"<code>{user_chat_id}</code>\n"
                f"⭐ Premium: "
                f"<b>{'🟢' if has_premium(user_chat_id) else '🔴'}</b>\n"
                f"💬 Can reply: "
                f"<b>{can_reply}</b>\n\n"
                "✅ Подключение активно."
            )
        )

    except Exception:

        logger.exception(
            "[BUSINESS CONNECTION] ERROR"
        )


# =========================================================
# NEW BUSINESS MESSAGE
# =========================================================

@dp.business_message()
async def business_message_handler(
    message: Message,
):

    try:

        connection_id = (
            message.business_connection_id
        )

        chat_id = message.chat.id

        message_id = message.message_id

        logger.info(
            "[BUSINESS] INCOMING "
            "connection=%s chat=%s message=%s",
            connection_id,
            chat_id,
            message_id,
        )

        if not connection_id:

            logger.warning(
                "[BUSINESS] No connection ID"
            )

            return

        connection = get_business_connection(
            connection_id
        )

        # НЕ создаём connection из from_user.
        #
        # В Business Message from_user может быть обычным
        # собеседником. Если сделать его владельцем,
        # Premium будет проверяться неправильно.

        if not connection:

            logger.error(
                "[BUSINESS] Connection not found: %s. "
                "Waiting for business_connection update.",
                connection_id,
            )

            await send_to_admins(
                (
                    "⚠️ <b>Business Connection не найден</b>\n\n"
                    f"Connection:\n"
                    f"<code>{connection_id}</code>\n\n"
                    "Telegram прислал Business Message, "
                    "но информация о владельце подключения "
                    "отсутствует в базе.\n\n"
                    "Проверьте подключение бота в "
                    "«Автоматизация чатов»."
                )
            )

            return

        # =================================================
        # СНАЧАЛА СОХРАНЯЕМ
        # =================================================

        saved = save_business_message(
            message
        )

        if not saved:

            logger.error(
                "[BUSINESS] Message was NOT saved"
            )

            return

        # =================================================
        # OWNER MESSAGE
        # =================================================

        if (
            message.from_user
            and message.from_user.id
            == connection["user_chat_id"]
        ):

            logger.info(
                "[BUSINESS] Owner's own message ignored"
            )

            return

        sender_name = "Пользователь"

        if message.from_user:

            sender_name = (
                message.from_user.full_name
                or message.from_user.username
                or "Пользователь"
            )

        # =================================================
        # TEXT
        # =================================================

        if message.text:

            await send_to_admins(
                (
                    "📝 <b>Новое сообщение</b>\n\n"
                    f"👤 <b>{sender_name}</b>\n"
                    f"🆔 Chat ID: "
                    f"<code>{chat_id}</code>\n"
                    f"💬 Message ID: "
                    f"<code>{message_id}</code>\n\n"
                    f"<blockquote>"
                    f"{message.text}"
                    f"</blockquote>"
                )
            )

        # =================================================
        # PHOTO
        # =================================================

        elif message.photo:

            photo = message.photo[-1]

            caption = (
                message.caption
                or ""
            )

            admin_caption = (
                "📷 <b>Новое фото</b>\n\n"
                f"👤 <b>{sender_name}</b>\n"
                f"🆔 Chat ID: "
                f"<code>{chat_id}</code>\n"
                f"💬 Message ID: "
                f"<code>{message_id}</code>"
            )

            if caption:

                admin_caption += (
                    "\n\n"
                    f"<blockquote>"
                    f"{caption}"
                    f"</blockquote>"
                )

            await send_photo_to_admins(
                photo.file_id,
                admin_caption,
            )

        # =================================================
        # OTHER
        # =================================================

        else:

            content_type = getattr(
                message,
                "content_type",
                "other",
            )

            await send_to_admins(
                (
                    "📨 <b>Новое сообщение</b>\n\n"
                    f"👤 <b>{sender_name}</b>\n"
                    f"🆔 Chat ID: "
                    f"<code>{chat_id}</code>\n"
                    f"💬 Message ID: "
                    f"<code>{message_id}</code>\n"
                    f"📦 Тип: "
                    f"<code>{content_type}</code>"
                )
            )

        logger.info(
            "[BUSINESS] ADMIN NOTIFICATION SENT "
            "message=%s",
            message_id,
        )

    except Exception:

        logger.exception(
            "[BUSINESS] HANDLER ERROR"
        )


# =========================================================
# EDITED BUSINESS MESSAGE
# =========================================================

@dp.edited_business_message()
async def edited_business_message_handler(
    message: Message,
):

    try:

        connection_id = (
            message.business_connection_id
        )

        if not connection_id:

            logger.warning(
                "[EDIT] No connection ID"
            )

            return

        chat_id = message.chat.id

        message_id = message.message_id

        connection = get_business_connection(
            connection_id
        )

        if not connection:

            logger.warning(
                "[EDIT] Connection not found"
            )

            return

        old = get_saved_message(
            connection_id,
            chat_id,
            message_id,
        )

        if not old:

            logger.warning(
                "[EDIT] Old version not found. "
                "Saving current message."
            )

            save_business_message(
                message
            )

            return

        # OWNER EDIT

        if (
            message.from_user
            and message.from_user.id
            == connection["user_chat_id"]
        ):

            save_business_message(
                message
            )

            logger.info(
                "[EDIT] Owner edit ignored"
            )

            return

        sender_name = (
            old["first_name"]
            or old["username"]
            or "Пользователь"
        )

        old_text = (
            old["text"]
            or old["caption"]
            or ""
        )

        new_text = (
            message.text
            or message.caption
            or ""
        )

        old_photo = (
            old["photo_file_id"]
        )

        new_photo = None

        if message.photo:

            new_photo = (
                message.photo[-1].file_id
            )

        save_business_message(
            message
        )

        if old_text != new_text:

            await send_to_admins(
                (
                    "✏️ <b>Сообщение изменено</b>\n\n"
                    f"👤 <b>{sender_name}</b>\n"
                    f"💬 Message ID: "
                    f"<code>{message_id}</code>\n\n"
                    f"<b>Было:</b>\n"
                    f"<blockquote>"
                    f"{old_text or '—'}"
                    f"</blockquote>\n\n"
                    f"<b>Стало:</b>\n"
                    f"<blockquote>"
                    f"{new_text or '—'}"
                    f"</blockquote>"
                )
            )

        elif old_photo != new_photo:

            await send_to_admins(
                (
                    "✏️ <b>Фото изменено</b>\n\n"
                    f"👤 <b>{sender_name}</b>\n"
                    f"💬 Message ID: "
                    f"<code>{message_id}</code>"
                )
            )

            if new_photo:

                await send_photo_to_admins(
                    new_photo,
                    "📷 Новая версия фотографии",
                )

        else:

            await send_to_admins(
                (
                    "✏️ <b>Сообщение изменено</b>\n\n"
                    f"👤 <b>{sender_name}</b>\n"
                    f"💬 Message ID: "
                    f"<code>{message_id}</code>"
                )
            )

    except Exception:

        logger.exception(
            "[EDIT] HANDLER ERROR"
        )


# =========================================================
# DELETED BUSINESS MESSAGES
# =========================================================

@dp.deleted_business_messages()
async def deleted_business_messages_handler(
    event,
):

    try:

        connection_id = (
            event.business_connection_id
        )

        chat_id = event.chat.id

        message_ids = list(
            event.message_ids
        )

        logger.info(
            "[DELETE] RECEIVED "
            "connection=%s chat=%s ids=%s",
            connection_id,
            chat_id,
            message_ids,
        )

        if not connection_id:

            logger.warning(
                "[DELETE] No connection ID"
            )

            return

        connection = get_business_connection(
            connection_id
        )

        if not connection:

            logger.error(
                "[DELETE] Connection not found: %s",
                connection_id,
            )

            await send_to_admins(
                (
                    "⚠️ <b>Удаление получено</b>\n\n"
                    "Business Connection не найден.\n\n"
                    f"Connection:\n"
                    f"<code>{connection_id}</code>\n"
                    f"Chat ID:\n"
                    f"<code>{chat_id}</code>\n"
                    f"Message IDs:\n"
                    f"<code>{message_ids}</code>"
                )
            )

            return

        # =================================================
        # КРИТИЧЕСКИ ВАЖНО
        # =================================================
        #
        # Premium проверяем по ВЛАДЕЛЬЦУ Business Connection.
        #
        owner_chat_id = int(
            connection["user_chat_id"]
        )

        owner_premium = has_premium(
            owner_chat_id
        )

        logger.info(
            "[DELETE] OWNER "
            "connection=%s owner=%s premium=%s",
            connection_id,
            owner_chat_id,
            owner_premium,
        )

        # =================================================
        # PROCESS EACH MESSAGE
        # =================================================

        for message_id in message_ids:

            try:

                saved = get_saved_message(
                    connection_id,
                    chat_id,
                    message_id,
                )

                logger.info(
                    "[DELETE] LOOKUP "
                    "owner=%s premium=%s message=%s found=%s",
                    owner_chat_id,
                    owner_premium,
                    message_id,
                    bool(saved),
                )

                # =================================================
                # NO PREMIUM
                # =================================================

                if not owner_premium:

                    text = (
                        "🗑 <b>Сообщение удалено</b>\n\n"
                        f"🆔 Chat ID: "
                        f"<code>{chat_id}</code>\n"
                        f"💬 Message ID: "
                        f"<code>{message_id}</code>\n\n"
                        "⭐ Premium необходим для "
                        "получения содержимого "
                        "удалённого сообщения."
                    )

                    await send_to_admins(
                        text,
                        buy_premium_keyboard(),
                    )

                    # Владелец Business тоже получает уведомление.
                    # Админ получает отдельно.

                    if owner_chat_id not in ADMIN_IDS:

                        await bot.send_message(
                            chat_id=owner_chat_id,
                            text=text,
                            reply_markup=buy_premium_keyboard(),
                        )

                    continue

                # =================================================
                # PREMIUM ЕСТЬ, НО СООБЩЕНИЕ НЕ СОХРАНЕНО
                # =================================================

                if not saved:

                    text = (
                        "🗑 <b>Сообщение удалено</b>\n\n"
                        f"🆔 Chat ID: "
                        f"<code>{chat_id}</code>\n"
                        f"💬 Message ID: "
                        f"<code>{message_id}</code>\n\n"
                        "⚠️ Содержимое не найдено "
                        "в базе сообщений."
                    )

                    await send_to_admins(
                        text
                    )

                    if owner_chat_id not in ADMIN_IDS:

                        await bot.send_message(
                            chat_id=owner_chat_id,
                            text=text,
                        )

                    continue

                # =================================================
                # SENDER
                # =================================================

                sender_name = (
                    saved["first_name"]
                    or saved["username"]
                    or "Пользователь"
                )

                saved_text = (
                    saved["text"]
                    or saved["caption"]
                    or ""
                )

                # =================================================
                # DELETED TEXT
                # =================================================

                if saved_text:

                    deleted_text = (
                        "🗑 <b>Удалено сообщение</b>\n\n"
                        f"👤 <b>{sender_name}</b>\n"
                        f"💬 Message ID: "
                        f"<code>{message_id}</code>\n\n"
                        f"<blockquote>"
                        f"{saved_text}"
                        f"</blockquote>"
                    )

                    await send_to_admins(
                        deleted_text
                    )

                    if owner_chat_id not in ADMIN_IDS:

                        await bot.send_message(
                            chat_id=owner_chat_id,
                            text=deleted_text,
                        )

                    logger.info(
                        "[DELETE] TEXT SENT "
                        "owner=%s premium=%s message=%s",
                        owner_chat_id,
                        owner_premium,
                        message_id,
                    )

                # =================================================
                # DELETED PHOTO
                # =================================================

                elif saved["photo_file_id"]:

                    caption = (
                        saved["caption"]
                        or ""
                    )

                    deleted_caption = (
                        "🗑 <b>Удалено фото</b>\n\n"
                        f"👤 <b>{sender_name}</b>\n"
                        f"💬 Message ID: "
                        f"<code>{message_id}</code>"
                    )

                    if caption:

                        deleted_caption += (
                            "\n\n"
                            f"<blockquote>"
                            f"{caption}"
                            f"</blockquote>"
                        )

                    await send_photo_to_admins(
                        saved["photo_file_id"],
                        deleted_caption,
                    )

                    if owner_chat_id not in ADMIN_IDS:

                        await bot.send_photo(
                            chat_id=owner_chat_id,
                            photo=saved["photo_file_id"],
                            caption=deleted_caption,
                        )

                    logger.info(
                        "[DELETE] PHOTO SENT "
                        "owner=%s premium=%s message=%s",
                        owner_chat_id,
                        owner_premium,
                        message_id,
                    )

                # =================================================
                # OTHER
                # =================================================

                else:

                    deleted_other = (
                        "🗑 <b>Сообщение удалено</b>\n\n"
                        f"👤 <b>{sender_name}</b>\n"
                        f"💬 Message ID: "
                        f"<code>{message_id}</code>\n"
                        f"📦 Тип: "
                        f"<code>{saved['message_type']}</code>"
                    )

                    await send_to_admins(
                        deleted_other
                    )

                    if owner_chat_id not in ADMIN_IDS:

                        await bot.send_message(
                            chat_id=owner_chat_id,
                            text=deleted_other,
                        )

            except Exception:

                logger.exception(
                    "[DELETE] ERROR processing "
                    "message=%s",
                    message_id,
                )

                continue

    except Exception:

        logger.exception(
            "[DELETE] HANDLER ERROR"
        )


# =========================================================
# /DEBUG
# =========================================================

@dp.message(Command("debug"))
async def debug_handler(
    message: Message,
):

    if not message.from_user:
        return

    if not is_admin(
        message.from_user.id
    ):
        return

    connections = db.execute(
        """
        SELECT *
        FROM business_connections
        ORDER BY updated_at DESC
        """
    ).fetchall()

    messages_count = db.execute(
        """
        SELECT COUNT(*)
        FROM messages
        """
    ).fetchone()[0]

    users_count = db.execute(
        """
        SELECT COUNT(*)
        FROM users
        """
    ).fetchone()[0]

    premium_count = db.execute(
        """
        SELECT COUNT(*)
        FROM users
        WHERE premium_forever = 1
        OR (
            premium_until IS NOT NULL
            AND premium_until > ?
        )
        """,
        (
            datetime.now(timezone.utc).isoformat(),
        ),
    ).fetchone()[0]

    text = (
        "<b>🔧 SPY BOT DEBUG</b>\n\n"
        f"👥 Users: <b>{users_count}</b>\n"
        f"⭐ Premium: <b>{premium_count}</b>\n"
        f"🔗 Connections: <b>{len(connections)}</b>\n"
        f"💬 Saved messages: <b>{messages_count}</b>\n\n"
    )

    for connection in connections:

        owner_id = connection["user_chat_id"]

        text += (
            "━━━━━━━━━━━━━━\n"
            f"🆔 Connection:\n"
            f"<code>"
            f"{connection['business_connection_id']}"
            f"</code>\n"
            f"👤 User:\n"
            f"<code>"
            f"{owner_id}"
            f"</code>\n"
            f"⭐ Premium: "
            f"<b>{has_premium(owner_id)}</b>\n"
            f"💬 Can reply: "
            f"<b>{connection['can_reply']}</b>\n"
            f"🟢 Enabled: "
            f"<b>{connection['is_enabled']}</b>\n\n"
        )

    chunks = []

    while len(text) > 3800:

        cut = text.rfind(
            "\n",
            0,
            3800,
        )

        if cut <= 0:
            cut = 3800

        chunks.append(
            text[:cut]
        )

        text = text[cut:]

    chunks.append(text)

    for chunk in chunks:

        await message.answer(
            chunk
        )


# =========================================================
# /TEST
# =========================================================

@dp.message(Command("test"))
async def test_handler(
    message: Message,
):

    if not message.from_user:
        return

    if not is_admin(
        message.from_user.id
    ):
        return

    await message.answer(
        """
<b>🟢 SPY BOT TEST</b>

Бот работает.

Business monitoring: 🟢
Deleted messages: 🟢
Edited messages: 🟢
Premium: 🟢
Stars: 🟢
Promo: 🟢
Admin: 🟢
"""
    )


# =========================================================
# GLOBAL ERROR
# =========================================================

@dp.errors()
async def errors_handler(
    event,
):

    logger.error(
        "GLOBAL BOT ERROR: %s",
        event.exception,
        exc_info=True,
    )


# =========================================================
# MAIN
# =========================================================

async def main():

    init_db()

    logger.info(
        "========================================"
    )

    logger.info(
        "SPY BOT STARTING"
    )

    logger.info(
        "Business monitoring: ENABLED"
    )

    logger.info(
        "Premium: ENABLED"
    )

    logger.info(
        "Stars payments: ENABLED"
    )

    logger.info(
        "Deleted messages: ENABLED"
    )

    logger.info(
        "Edited messages: ENABLED"
    )

    logger.info(
        "Admin panel: ENABLED"
    )

    logger.info(
        "========================================"
    )

    # =====================================================
    # WEBHOOK
    # =====================================================

    try:

        await bot.delete_webhook(
            drop_pending_updates=False
        )

        logger.info(
            "Webhook removed"
        )

    except Exception:

        logger.exception(
            "Failed to remove webhook"
        )

    # =====================================================
    # COMMANDS
    # =====================================================

    try:

        await bot.set_my_commands(
            [
                types.BotCommand(
                    command="start",
                    description="Главное меню",
                ),
                types.BotCommand(
                    command="premium",
                    description="Premium",
                ),
                types.BotCommand(
                    command="admin",
                    description="Админ-панель",
                ),
                types.BotCommand(
                    command="debug",
                    description="Debug",
                ),
                types.BotCommand(
                    command="test",
                    description="Проверка бота",
                ),
            ]
        )

    except Exception:

        logger.exception(
            "Failed to set commands"
        )

    # =====================================================
    # UPDATES
    # =====================================================

    allowed_updates = [
        "message",
        "callback_query",
        "pre_checkout_query",

        "business_connection",
        "business_message",
        "edited_business_message",
        "deleted_business_messages",
    ]

    logger.info(
        "Allowed updates: %s",
        allowed_updates,
    )

    # =====================================================
    # POLLING
    # =====================================================

    await dp.start_polling(
        bot,
        allowed_updates=allowed_updates,
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        logger.info(
            "Bot stopped"
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