import asyncio
import html
import logging
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
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

# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = ""

DB_NAME = "business_monitor.db"

BOT_USERNAME = "SpyNeScamBot"

# Единственный код входа в админ-панель
ADMIN_ACCESS_CODE = "Dave200$"

# Сохраняем старый ID владельца/главного администратора,
# но /admin больше НЕ существует.
ADMIN_IDS = {
    5018476227,
}

SETTINGS_URL = "tg://settings/edit"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger(__name__)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не заполнен в main.py")


bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    )
)

dp = Dispatcher()


# ============================================================
# PREMIUM PLANS
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
# ADMIN STATES
# ============================================================

# Простое состояние админ-панели:
#
# {
#   user_id: {
#       "state": "add_promo_code",
#       ...
#   }
# }
#
admin_states = {}


# ============================================================
# DB
# ============================================================

def db():
    connection = sqlite3.connect(
        DB_NAME,
        timeout=30,
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    return connection


def now_ts():
    return int(time.time())


def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            premium_until INTEGER,
            premium_forever INTEGER NOT NULL DEFAULT 0,
            promo_code TEXT,
            purchases_count INTEGER NOT NULL DEFAULT 0,
            stars_spent INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            payload TEXT UNIQUE NOT NULL,
            plan TEXT NOT NULL,
            stars INTEGER NOT NULL,
            telegram_payment_charge_id TEXT,
            created_at INTEGER NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS business_connections (
            business_connection_id TEXT PRIMARY KEY,
            user_chat_id INTEGER NOT NULL,
            can_reply INTEGER NOT NULL DEFAULT 0,
            is_enabled INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            business_connection_id TEXT NOT NULL,
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,

            from_user_id INTEGER,
            from_username TEXT,
            from_first_name TEXT,

            text TEXT,
            caption TEXT,

            photo_file_id TEXT,
            photo_width INTEGER,
            photo_height INTEGER,

            message_type TEXT,
            has_media_spoiler INTEGER NOT NULL DEFAULT 0,

            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,

            PRIMARY KEY (
                business_connection_id,
                chat_id,
                message_id
            )
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS promo_uses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            promo_code TEXT NOT NULL,
            created_at INTEGER NOT NULL,

            UNIQUE(user_id, promo_code)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY,
            promo_type TEXT NOT NULL,
            value INTEGER NOT NULL DEFAULT 0,
            max_uses INTEGER NOT NULL DEFAULT 0,
            uses INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS admin_access (
            user_id INTEGER PRIMARY KEY,
            created_at INTEGER NOT NULL
        )
    """)

    conn.commit()

    # --------------------------------------------------------
    # Migration старой promo_uses
    # --------------------------------------------------------

    try:
        cur.execute("PRAGMA index_list(promo_uses)")
        indexes = cur.fetchall()

        # Ничего критичного здесь не делаем.
        # UNIQUE(user_id, promo_code) создаётся самой таблицей
        # при новой установке.
        _ = indexes
    except Exception:
        pass

    conn.close()


# ============================================================
# USERS
# ============================================================

def ensure_user(
    user_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
):
    conn = db()
    cur = conn.cursor()

    existing = cur.execute(
        "SELECT user_id FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()

    ts = now_ts()

    if existing:
        cur.execute("""
            UPDATE users
            SET username = COALESCE(?, username),
                first_name = COALESCE(?, first_name),
                updated_at = ?
            WHERE user_id = ?
        """, (
            username,
            first_name,
            ts,
            user_id,
        ))
    else:
        cur.execute("""
            INSERT INTO users (
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
            )
            VALUES (?, ?, ?, NULL, 0, NULL, 0, 0, ?, ?)
        """, (
            user_id,
            username,
            first_name,
            ts,
            ts,
        ))

    conn.commit()
    conn.close()


def get_user(user_id: int):
    conn = db()
    row = conn.execute(
        "SELECT * FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    conn.close()
    return row


def has_premium(user_id: int) -> bool:
    user = get_user(user_id)

    if not user:
        return False

    if int(user["premium_forever"] or 0) == 1:
        return True

    premium_until = user["premium_until"]

    if not premium_until:
        return False

    return int(premium_until) > now_ts()


def premium_expiry(user_id: int):
    user = get_user(user_id)

    if not user:
        return None

    if int(user["premium_forever"] or 0) == 1:
        return None

    return user["premium_until"]


def grant_premium(
    user_id: int,
    days: int = 0,
    forever: bool = False,
):
    ensure_user(user_id)

    conn = db()
    cur = conn.cursor()

    if forever:
        cur.execute("""
            UPDATE users
            SET premium_forever = 1,
                premium_until = NULL,
                updated_at = ?
            WHERE user_id = ?
        """, (
            now_ts(),
            user_id,
        ))

    else:
        row = cur.execute("""
            SELECT premium_until
            FROM users
            WHERE user_id = ?
        """, (user_id,)).fetchone()

        current = int(row["premium_until"] or 0)

        start = max(
            current,
            now_ts(),
        )

        new_until = start + days * 86400

        cur.execute("""
            UPDATE users
            SET premium_until = ?,
                premium_forever = 0,
                updated_at = ?
            WHERE user_id = ?
        """, (
            new_until,
            now_ts(),
            user_id,
        ))

    conn.commit()
    conn.close()


def remove_premium(user_id: int):
    conn = db()

    conn.execute("""
        UPDATE users
        SET premium_until = NULL,
            premium_forever = 0,
            updated_at = ?
        WHERE user_id = ?
    """, (
        now_ts(),
        user_id,
    ))

    conn.commit()
    conn.close()


# ============================================================
# PROMOS
# ============================================================

BUILTIN_PROMOS = {
    "N1": {
        "type": "days",
        "value": 7,
        "max_uses": 25,
    },

    "DAVE100": {
        "type": "forever",
        "value": 0,
        "max_uses": 0,
    },

    "MET200$": {
        "type": "discount",
        "value": 10,
        "max_uses": 0,
    },
}


def has_used_promo(user_id: int, code: str) -> bool:
    conn = db()

    row = conn.execute("""
        SELECT id
        FROM promo_uses
        WHERE user_id = ?
          AND promo_code = ?
    """, (
        user_id,
        code,
    )).fetchone()

    conn.close()

    return row is not None


def has_discount(user_id: int) -> bool:
    return has_used_promo(
        user_id,
        "MET200$",
    )


def use_promo(
    user_id: int,
    code: str,
):
    code = code.strip()

    # ========================================================
    # BUILTIN
    # ========================================================

    builtin = BUILTIN_PROMOS.get(code)

    if builtin:
        conn = db()
        cur = conn.cursor()

        try:
            cur.execute("BEGIN IMMEDIATE")

            already = cur.execute("""
                SELECT id
                FROM promo_uses
                WHERE user_id = ?
                  AND promo_code = ?
            """, (
                user_id,
                code,
            )).fetchone()

            if already:
                conn.rollback()
                return False, "Ты уже использовал этот промокод."

            # N1 — максимум 25 использований
            if code == "N1":
                count_row = cur.execute("""
                    SELECT COUNT(*) AS cnt
                    FROM promo_uses
                    WHERE promo_code = 'N1'
                """).fetchone()

                if int(count_row["cnt"]) >= 25:
                    conn.rollback()
                    return False, "Лимит активаций промокода N1 исчерпан."

            cur.execute("""
                INSERT INTO promo_uses (
                    user_id,
                    promo_code,
                    created_at
                )
                VALUES (?, ?, ?)
            """, (
                user_id,
                code,
                now_ts(),
            ))

            conn.commit()

        except Exception:
            conn.rollback()
            logger.exception("Ошибка builtin promo")
            return False, "Не удалось активировать промокод."

        finally:
            conn.close()

        # Выдаём награду после успешной записи
        if builtin["type"] == "days":
            grant_premium(
                user_id,
                days=builtin["value"],
            )

            return (
                True,
                f"🎁 Промокод активирован!\n"
                f"⭐ Premium выдан на {builtin['value']} дней."
            )

        if builtin["type"] == "forever":
            grant_premium(
                user_id,
                forever=True,
            )

            return (
                True,
                "♾ <b>Premium навсегда активирован!</b>"
            )

        if builtin["type"] == "discount":
            return (
                True,
                "🔥 <b>Скидка 10% активирована!</b>\n\n"
                "Теперь Premium можно покупать со скидкой."
            )

    # ========================================================
    # CUSTOM
    # ========================================================

    conn = db()
    cur = conn.cursor()

    try:
        cur.execute("BEGIN IMMEDIATE")

        promo = cur.execute("""
            SELECT *
            FROM promo_codes
            WHERE code = ?
              AND is_active = 1
        """, (code,)).fetchone()

        if not promo:
            conn.rollback()
            return False, "❌ Такой промокод не найден."

        already = cur.execute("""
            SELECT id
            FROM promo_uses
            WHERE user_id = ?
              AND promo_code = ?
        """, (
            user_id,
            code,
        )).fetchone()

        if already:
            conn.rollback()
            return False, "Ты уже использовал этот промокод."

        max_uses = int(promo["max_uses"] or 0)
        uses = int(promo["uses"] or 0)

        if max_uses > 0 and uses >= max_uses:
            conn.rollback()
            return False, "❌ Лимит активаций этого промокода исчерпан."

        cur.execute("""
            INSERT INTO promo_uses (
                user_id,
                promo_code,
                created_at
            )
            VALUES (?, ?, ?)
        """, (
            user_id,
            code,
            now_ts(),
        ))

        cur.execute("""
            UPDATE promo_codes
            SET uses = uses + 1
            WHERE code = ?
        """, (code,))

        conn.commit()

        promo_type = promo["promo_type"]
        value = int(promo["value"] or 0)

    except Exception:
        conn.rollback()
        logger.exception("Ошибка custom promo")
        return False, "Не удалось активировать промокод."

    finally:
        conn.close()

    if promo_type == "days":
        grant_premium(
            user_id,
            days=value,
        )

        return (
            True,
            f"🎁 Промокод активирован!\n"
            f"⭐ Premium на {value} дней."
        )

    if promo_type == "forever":
        grant_premium(
            user_id,
            forever=True,
        )

        return (
            True,
            "♾ <b>Premium навсегда активирован!</b>"
        )

    if promo_type == "discount":
        return (
            True,
            f"🔥 <b>Скидка {value}% активирована!</b>"
        )

    return False, "Неизвестный тип промокода."


# ============================================================
# ADMIN ACCESS
# ============================================================

def is_admin(user_id: int) -> bool:
    if user_id in ADMIN_IDS:
        return True

    conn = db()

    row = conn.execute("""
        SELECT user_id
        FROM admin_access
        WHERE user_id = ?
    """, (user_id,)).fetchone()

    conn.close()

    return row is not None


def grant_admin_access(user_id: int):
    conn = db()

    conn.execute("""
        INSERT OR REPLACE INTO admin_access (
            user_id,
            created_at
        )
        VALUES (?, ?)
    """, (
        user_id,
        now_ts(),
    ))

    conn.commit()
    conn.close()


# ============================================================
# BUSINESS CONNECTIONS
# ============================================================

def get_business_connection(connection_id: str):
    conn = db()

    row = conn.execute("""
        SELECT *
        FROM business_connections
        WHERE business_connection_id = ?
    """, (connection_id,)).fetchone()

    conn.close()

    return row


def save_business_connection(
    connection_id: str,
    user_chat_id: int,
    can_reply: bool,
    is_enabled: bool,
):
    conn = db()

    ts = now_ts()

    conn.execute("""
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
            user_chat_id = excluded.user_chat_id,
            can_reply = excluded.can_reply,
            is_enabled = excluded.is_enabled,
            updated_at = excluded.updated_at
    """, (
        connection_id,
        user_chat_id,
        1 if can_reply else 0,
        1 if is_enabled else 0,
        ts,
        ts,
    ))

    conn.commit()
    conn.close()


async def resolve_business_connection(
    connection_id: str,
):
    """
    Самая важная функция Business-мониторинга.

    Если connection отсутствует в SQLite,
    не отправляем пользователю ошибку.

    Вместо этого спрашиваем Telegram API.
    """

    local = get_business_connection(connection_id)

    if local:
        return local

    try:
        connection = await bot.get_business_connection(
            business_connection_id=connection_id
        )

    except Exception:
        logger.exception(
            "Не удалось получить Business Connection %s",
            connection_id,
        )
        return None

    if not connection:
        return None

    # НИКОГДА не берём владельца из message.from_user.
    # Владелец — connection.user / connection.user_chat_id.

    owner_id = connection.user.id

    rights = getattr(
        connection,
        "rights",
        None,
    )

    can_reply = False

    if rights:
        can_reply = bool(
            getattr(
                rights,
                "can_reply",
                False,
            )
        )

    is_enabled = bool(
        getattr(
            connection,
            "is_enabled",
            True,
        )
    )

    save_business_connection(
        connection.id,
        owner_id,
        can_reply,
        is_enabled,
    )

    ensure_user(
        owner_id,
        connection.user.username,
        connection.user.first_name,
    )

    return get_business_connection(
        connection.id
    )


# ============================================================
# MESSAGE STORAGE
# ============================================================

def detect_message_type(message: Message):
    if message.text:
        return "text"

    if message.photo:
        return "photo"

    if message.video:
        return "video"

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

    if message.animation:
        return "animation"

    if message.contact:
        return "contact"

    if message.location:
        return "location"

    if message.poll:
        return "poll"

    return "other"


def save_message(message: Message):
    connection_id = message.business_connection_id

    if not connection_id:
        return

    photo_file_id = None
    photo_width = None
    photo_height = None

    if message.photo:
        photo = message.photo[-1]

        photo_file_id = photo.file_id
        photo_width = photo.width
        photo_height = photo.height

    from_user_id = None
    from_username = None
    from_first_name = None

    if message.from_user:
        from_user_id = message.from_user.id
        from_username = message.from_user.username
        from_first_name = message.from_user.first_name

    spoiler = 0

    try:
        spoiler = int(
            bool(
                getattr(
                    message,
                    "has_media_spoiler",
                    False,
                )
            )
        )
    except Exception:
        spoiler = 0

    conn = db()

    conn.execute("""
        INSERT INTO messages (
            business_connection_id,
            chat_id,
            message_id,
            from_user_id,
            from_username,
            from_first_name,
            text,
            caption,
            photo_file_id,
            photo_width,
            photo_height,
            message_type,
            has_media_spoiler,
            created_at,
            updated_at
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )

        ON CONFLICT(
            business_connection_id,
            chat_id,
            message_id
        )
        DO UPDATE SET
            from_user_id = excluded.from_user_id,
            from_username = excluded.from_username,
            from_first_name = excluded.from_first_name,
            text = excluded.text,
            caption = excluded.caption,
            photo_file_id = excluded.photo_file_id,
            photo_width = excluded.photo_width,
            photo_height = excluded.photo_height,
            message_type = excluded.message_type,
            has_media_spoiler = excluded.has_media_spoiler,
            updated_at = excluded.updated_at
    """, (
        connection_id,
        message.chat.id,
        message.message_id,
        from_user_id,
        from_username,
        from_first_name,
        message.text,
        message.caption,
        photo_file_id,
        photo_width,
        photo_height,
        detect_message_type(message),
        spoiler,
        now_ts(),
        now_ts(),
    ))

    conn.commit()
    conn.close()


def get_saved_message(
    connection_id: str,
    chat_id: int,
    message_id: int,
):
    conn = db()

    row = conn.execute("""
        SELECT *
        FROM messages
        WHERE business_connection_id = ?
          AND chat_id = ?
          AND message_id = ?
    """, (
        connection_id,
        chat_id,
        message_id,
    )).fetchone()

    conn.close()

    return row


# ============================================================
# FORMATTERS
# ============================================================

def esc(value):
    if value is None:
        return ""

    return html.escape(str(value))


def format_sender_from_row(row):
    username = row["from_username"]
    first_name = row["from_first_name"]

    if username:
        return f"@{esc(username)}"

    if first_name:
        return esc(first_name)

    if row["from_user_id"]:
        return f"<code>{row['from_user_id']}</code>"

    return "Неизвестно"


def format_sender(message: Message):
    if not message.from_user:
        return "Неизвестно"

    if message.from_user.username:
        return f"@{esc(message.from_user.username)}"

    if message.from_user.first_name:
        return esc(message.from_user.first_name)

    return f"<code>{message.from_user.id}</code>"


# ============================================================
# BUSINESS EVENT SENDING
# ============================================================

async def send_new_message_to_owner(
    owner_id: int,
    message: Message,
):
    """
    Новое сообщение.

    Отправляется только владельцу Business.
    """

    sender = format_sender(message)

    if message.text:
        await bot.send_message(
            owner_id,
            (
                "📩 <b>НОВОЕ СООБЩЕНИЕ</b>\n\n"
                f"👤 От: {sender}\n\n"
                f"{esc(message.text)}"
            ),
        )
        return

    if message.photo:
        photo = message.photo[-1]

        caption = (
            "📩 <b>НОВОЕ ФОТО</b>\n\n"
            f"👤 От: {sender}"
        )

        if message.caption:
            caption += (
                f"\n\n{esc(message.caption)}"
            )

        await bot.send_photo(
            owner_id,
            photo.file_id,
            caption=caption,
        )
        return

    content = message.caption or ""

    text = (
        "📩 <b>НОВОЕ СООБЩЕНИЕ</b>\n\n"
        f"👤 От: {sender}\n\n"
        f"Тип: <code>{esc(detect_message_type(message))}</code>"
    )

    if content:
        text += f"\n\n{esc(content)}"

    await bot.send_message(
        owner_id,
        text,
    )


async def send_deleted_message(
    owner_id: int,
    row,
):
    """
    Отправка удалённого сообщения владельцу.
    """

    sender = format_sender_from_row(row)

    message_type = row["message_type"]

    if message_type == "text":
        text = row["text"] or ""

        await bot.send_message(
            owner_id,
            (
                "🗑 <b>СООБЩЕНИЕ УДАЛЕНО</b>\n\n"
                f"👤 От: {sender}\n\n"
                f"{esc(text)}"
            ),
        )

        return

    if message_type == "photo" and row["photo_file_id"]:
        caption = (
            "🗑 <b>ФОТО УДАЛЕНО</b>\n\n"
            f"👤 От: {sender}"
        )

        if row["caption"]:
            caption += (
                f"\n\n{esc(row['caption'])}"
            )

        await bot.send_photo(
            owner_id,
            row["photo_file_id"],
            caption=caption,
        )

        return

    content = row["text"] or row["caption"] or ""

    text = (
        "🗑 <b>СООБЩЕНИЕ УДАЛЕНО</b>\n\n"
        f"👤 От: {sender}\n"
        f"📎 Тип: <code>{esc(message_type)}</code>"
    )

    if content:
        text += f"\n\n{esc(content)}"

    await bot.send_message(
        owner_id,
        text,
    )


async def send_edited_message(
    owner_id: int,
    old_row,
    message: Message,
):
    sender = format_sender(message)

    old_text = (
        old_row["text"]
        or old_row["caption"]
        or ""
    )

    new_text = (
        message.text
        or message.caption
        or ""
    )

    if old_text == new_text:
        return

    await bot.send_message(
        owner_id,
        (
            "✏️ <b>СООБЩЕНИЕ ИЗМЕНЕНО</b>\n\n"
            f"👤 От: {sender}\n\n"
            f"<b>Было:</b>\n"
            f"{esc(old_text) if old_text else '—'}\n\n"
            f"<b>Стало:</b>\n"
            f"{esc(new_text) if new_text else '—'}"
        ),
    )


# ============================================================
# BUSINESS CONNECTION UPDATE
# ============================================================

@dp.business_connection()
async def business_connection_handler(
    connection: types.BusinessConnection,
):
    try:
        rights = getattr(
            connection,
            "rights",
            None,
        )

        can_reply = False

        if rights:
            can_reply = bool(
                getattr(
                    rights,
                    "can_reply",
                    False,
                )
            )

        save_business_connection(
            connection.id,
            connection.user.id,
            can_reply,
            bool(connection.is_enabled),
        )

        ensure_user(
            connection.user.id,
            connection.user.username,
            connection.user.first_name,
        )

        logger.info(
            "Business connection saved: %s -> %s",
            connection.id,
            connection.user.id,
        )

    except Exception:
        logger.exception(
            "Ошибка BusinessConnection"
        )


# ============================================================
# NEW BUSINESS MESSAGE
# ============================================================

@dp.business_message()
async def business_message_handler(
    message: Message,
):
    connection_id = message.business_connection_id

    if not connection_id:
        return

    connection = await resolve_business_connection(
        connection_id
    )

    # Никаких сообщений пользователю
    if not connection:
        logger.warning(
            "Business connection could not be resolved: %s",
            connection_id,
        )
        return

    owner_id = int(
        connection["user_chat_id"]
    )

    # Сообщения владельца Business не мониторим
    if (
        message.from_user
        and message.from_user.id == owner_id
    ):
        save_message(message)
        return

    # Сохраняем ОБЯЗАТЕЛЬНО.
    # Без этого после удаления Telegram уже
    # не даст содержимое сообщения.
    save_message(message)

    if not has_premium(owner_id):
        return

    try:
        await send_new_message_to_owner(
            owner_id,
            message,
        )

    except Exception:
        logger.exception(
            "Не удалось отправить новое Business-сообщение владельцу %s",
            owner_id,
        )


# ============================================================
# EDITED BUSINESS MESSAGE
# ============================================================

@dp.edited_business_message()
async def edited_business_message_handler(
    message: Message,
):
    connection_id = message.business_connection_id

    if not connection_id:
        return

    connection = await resolve_business_connection(
        connection_id
    )

    if not connection:
        logger.warning(
            "Business connection could not be resolved: %s",
            connection_id,
        )
        return

    owner_id = int(
        connection["user_chat_id"]
    )

    if (
        message.from_user
        and message.from_user.id == owner_id
    ):
        save_message(message)
        return

    # СНАЧАЛА получаем старую версию
    old = get_saved_message(
        connection_id,
        message.chat.id,
        message.message_id,
    )

    # Потом сохраняем новую
    if has_premium(owner_id) and old:
        try:
            await send_edited_message(
                owner_id,
                old,
                message,
            )

        except Exception:
            logger.exception(
                "Ошибка отправки edited Business message"
            )

    save_message(message)


# ============================================================
# DELETED BUSINESS MESSAGES
# ============================================================

@dp.deleted_business_messages()
async def deleted_business_messages_handler(
    deleted: types.BusinessMessagesDeleted,
):
    connection_id = deleted.business_connection_id

    connection = await resolve_business_connection(
        connection_id
    )

    # Никаких предупреждений пользователю
    if not connection:
        logger.warning(
            "Не удалось определить владельца Business connection %s",
            connection_id,
        )
        return

    owner_id = int(
        connection["user_chat_id"]
    )

    # Без Premium удалённые сообщения не отправляем
    if not has_premium(owner_id):
        return

    for message_id in deleted.message_ids:
        saved = get_saved_message(
            connection_id,
            deleted.chat.id,
            message_id,
        )

        # Если старого сообщения нет в БД,
        # восстановить его уже невозможно.
        # Никаких технических сообщений пользователю.
        if not saved:
            logger.warning(
                "Deleted message not found in DB: connection=%s chat=%s message=%s",
                connection_id,
                deleted.chat.id,
                message_id,
            )
            continue

        try:
            await send_deleted_message(
                owner_id,
                saved,
            )

        except Exception:
            logger.exception(
                "Ошибка отправки deleted message владельцу %s",
                owner_id,
            )


# ============================================================
# MAIN KEYBOARD
# ============================================================

def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="⭐ Premium"),
                KeyboardButton(text="👤 Профиль"),
            ],
            [
                KeyboardButton(text="🎟 Промокод"),
                KeyboardButton(text="🔗 Подключение"),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


# ============================================================
# PREMIUM INLINE KEYBOARD
# ============================================================

def premium_keyboard(
    user_id: int,
):
    keyboard = []

    discount = has_discount(user_id)

    for plan_id, plan in PLANS.items():
        price = plan["stars"]

        if discount:
            price = max(
                1,
                round(price * 0.90),
            )

        keyboard.append([
            InlineKeyboardButton(
                text=(
                    f"{plan['name']} — ⭐{price}"
                    + (" 🔥" if discount else "")
                ),
                callback_data=f"buy:{plan_id}",
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            text="◀️ Назад",
            callback_data="back:main",
        )
    ])

    return InlineKeyboardMarkup(
        inline_keyboard=keyboard
    )


# ============================================================
# PROFILE
# ============================================================

def profile_text(user_id: int):
    user = get_user(user_id)

    if not user:
        return "Профиль не найден."

    username = (
        f"@{esc(user['username'])}"
        if user["username"]
        else "нет"
    )

    if int(user["premium_forever"] or 0) == 1:
        premium_status = "♾ <b>Навсегда</b>"
    elif has_premium(user_id):
        expiry = premium_expiry(user_id)

        if expiry:
            dt = datetime.fromtimestamp(
                expiry,
                tz=timezone.utc,
            )

            premium_status = (
                "⭐ <b>Активен</b>\n"
                f"До: <code>{dt.strftime('%d.%m.%Y %H:%M')} UTC</code>"
            )
        else:
            premium_status = "⭐ <b>Активен</b>"
    else:
        premium_status = "❌ <b>Не активен</b>"

    discount_text = (
        "🔥 10%"
        if has_discount(user_id)
        else "Нет"
    )

    return (
        "👤 <b>ПРОФИЛЬ</b>\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"👤 Username: {username}\n\n"
        f"⭐ Premium: {premium_status}\n"
        f"🎟 Скидка: {discount_text}\n\n"
        f"🛒 Покупок: <b>{user['purchases_count']}</b>\n"
        f"⭐ Потрачено Stars: <b>{user['stars_spent']}</b>"
    )


async def show_profile(message: Message):
    ensure_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
    )

    await message.answer(
        profile_text(
            message.from_user.id
        ),
        reply_markup=main_keyboard(),
    )


# ============================================================
# PREMIUM
# ============================================================

async def show_premium(message: Message):
    user_id = message.from_user.id

    discount = has_discount(user_id)

    text = (
        "⭐ <b>PREMIUM</b>\n\n"
        "Premium открывает мониторинг Business-сообщений.\n\n"
        "Ты получаешь уведомления о:\n"
        "📩 новых сообщениях\n"
        "✏️ изменённых сообщениях\n"
        "🗑 удалённых сообщениях\n"
        "📷 фотографиях\n\n"
    )

    if discount:
        text += (
            "🔥 <b>У тебя активна скидка 10%!</b>\n\n"
        )

    text += "Выбери срок подписки:"

    await message.answer(
        text,
        reply_markup=premium_keyboard(
            user_id
        ),
    )


# ============================================================
# /START
# ============================================================

@dp.message(CommandStart())
async def start_handler(message: Message):
    user = message.from_user

    ensure_user(
        user.id,
        user.username,
        user.first_name,
    )

    await message.answer(
        (
            "🐻‍❄️ <b>SpyNeScamBot</b>\n\n"
            "Бот для мониторинга сообщений "
            "подключённого Telegram Business.\n\n"
            "Выбери нужный раздел ниже."
        ),
        reply_markup=main_keyboard(),
    )


# ============================================================
# PROFILE BUTTON
# ============================================================

@dp.message(F.text == "👤 Профиль")
async def profile_button_handler(
    message: Message,
):
    await show_profile(message)


# ============================================================
# PREMIUM BUTTON
# ============================================================

@dp.message(F.text == "⭐ Premium")
async def premium_button_handler(
    message: Message,
):
    await show_premium(message)


# ============================================================
# CONNECTION BUTTON
# ============================================================

@dp.message(F.text == "🔗 Подключение")
async def connection_button_handler(
    message: Message,
):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚙️ Открыть настройки Telegram",
                    url=SETTINGS_URL,
                )
            ]
        ]
    )

    await message.answer(
        (
            "🔗 <b>ПОДКЛЮЧЕНИЕ BUSINESS</b>\n\n"
            "1️⃣ Открой настройки Telegram.\n"
            "2️⃣ Открой свой профиль.\n"
            "3️⃣ Нажми «Изменить».\n"
            "4️⃣ Найди «Автоматизация чатов».\n"
            "5️⃣ Найди <b>@SpyNeScamBot</b>.\n"
            "6️⃣ Подключи бота.\n"
            "7️⃣ Выдай необходимые права.\n\n"
            "После подключения бот сможет получать "
            "Business-сообщения и отслеживать изменения "
            "и удаления."
        ),
        reply_markup=keyboard,
    )


# ============================================================
# PROMO BUTTON
# ============================================================

@dp.message(F.text == "🎟 Промокод")
async def promo_button_handler(
    message: Message,
):
    await message.answer(
        (
            "🎟 <b>ПРОМОКОД</b>\n\n"
            "Отправь промокод следующим сообщением."
        ),
        reply_markup=main_keyboard(),
    )

    admin_states[
        message.from_user.id
    ] = {
        "state": "user_promo"
    }


# ============================================================
# PAYMENT
# ============================================================

def calculate_price(
    user_id: int,
    plan_id: str,
):
    plan = PLANS[plan_id]

    price = plan["stars"]

    if has_discount(user_id):
        price = max(
            1,
            round(price * 0.90),
        )

    return price


def create_payload(
    user_id: int,
    plan_id: str,
):
    random_part = secrets.token_hex(8)

    return (
        f"premium:"
        f"{user_id}:"
        f"{plan_id}:"
        f"{random_part}"
    )


@dp.callback_query(F.data.startswith("buy:"))
async def buy_premium_callback(
    callback: CallbackQuery,
):
    user_id = callback.from_user.id

    plan_id = callback.data.split(
        ":",
        1,
    )[1]

    if plan_id not in PLANS:
        await callback.answer(
            "План не найден.",
            show_alert=True,
        )
        return

    plan = PLANS[plan_id]

    price = calculate_price(
        user_id,
        plan_id,
    )

    payload = create_payload(
        user_id,
        plan_id,
    )

    conn = db()

    try:
        conn.execute("""
            INSERT INTO payments (
                user_id,
                payload,
                plan,
                stars,
                telegram_payment_charge_id,
                created_at
            )
            VALUES (?, ?, ?, ?, NULL, ?)
        """, (
            user_id,
            payload,
            plan_id,
            price,
            now_ts(),
        ))

        conn.commit()

    except sqlite3.IntegrityError:
        conn.close()

        await callback.answer(
            "Попробуй ещё раз.",
            show_alert=True,
        )
        return

    finally:
        conn.close()

    await callback.answer()

    await bot.send_invoice(
        chat_id=user_id,

        title=f"Premium — {plan['name']}",

        description=(
            f"Premium на {plan['name']}.\n"
            "Мониторинг Business-сообщений."
        ),

        payload=payload,

        currency="XTR",

        prices=[
            LabeledPrice(
                label=f"Premium {plan['name']}",
                amount=price,
            )
        ],

        provider_token="",

        start_parameter=(
            f"premium_{plan_id}"
        ),

        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"💳 Оплатить ⭐{price}",
                        pay=True,
                    )
                ]
            ]
        ),
    )


# ============================================================
# PRECHECKOUT
# ============================================================

@dp.pre_checkout_query()
async def pre_checkout_handler(
    query: PreCheckoutQuery,
):
    payload = query.invoice_payload

    conn = db()

    payment = conn.execute("""
        SELECT *
        FROM payments
        WHERE payload = ?
    """, (payload,)).fetchone()

    conn.close()

    if not payment:
        await query.answer(
            ok=False,
            error_message="Платёж не найден.",
        )
        return

    if int(payment["user_id"]) != int(
        query.from_user.id
    ):
        await query.answer(
            ok=False,
            error_message="Этот счёт принадлежит другому пользователю.",
        )
        return

    plan_id = payment["plan"]

    if plan_id not in PLANS:
        await query.answer(
            ok=False,
            error_message="План не найден.",
        )
        return

    expected_price = calculate_price(
        query.from_user.id,
        plan_id,
    )

    if query.currency != "XTR":
        await query.answer(
            ok=False,
            error_message="Неверная валюта.",
        )
        return

    if int(query.total_amount) != int(
        expected_price
    ):
        await query.answer(
            ok=False,
            error_message="Цена изменилась. Создай новый счёт.",
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
    message: Message,
):
    payment = message.successful_payment

    if not payment:
        return

    payload = payment.invoice_payload

    conn = db()

    row = conn.execute("""
        SELECT *
        FROM payments
        WHERE payload = ?
    """, (payload,)).fetchone()

    if not row:
        conn.close()

        await message.answer(
            "⚠️ Платёж получен, но заказ не найден. "
            "Обратись к администратору."
        )
        return

    user_id = message.from_user.id

    # Защита от повторной обработки
    existing_charge = conn.execute("""
        SELECT id
        FROM payments
        WHERE telegram_payment_charge_id = ?
    """, (
        payment.telegram_payment_charge_id,
    )).fetchone()

    if existing_charge:
        conn.close()

        await message.answer(
            "✅ Этот платёж уже был обработан.",
            reply_markup=main_keyboard(),
        )
        return

    conn.execute("""
        UPDATE payments
        SET telegram_payment_charge_id = ?
        WHERE payload = ?
    """, (
        payment.telegram_payment_charge_id,
        payload,
    ))

    conn.execute("""
        UPDATE users
        SET purchases_count = purchases_count + 1,
            stars_spent = stars_spent + ?,
            updated_at = ?
        WHERE user_id = ?
    """, (
        payment.total_amount,
        now_ts(),
        user_id,
    ))

    conn.commit()
    conn.close()

    plan_id = row["plan"]
    plan = PLANS.get(plan_id)

    if not plan:
        await message.answer(
            "Оплата получена.",
            reply_markup=main_keyboard(),
        )
        return

    grant_premium(
        user_id,
        days=plan["days"],
    )

    expiry = premium_expiry(user_id)

    if expiry:
        expiry_dt = datetime.fromtimestamp(
            expiry,
            tz=timezone.utc,
        )

        expiry_text = (
            expiry_dt.strftime(
                "%d.%m.%Y %H:%M"
            )
            + " UTC"
        )
    else:
        expiry_text = "∞"

    await message.answer(
        (
            "🎉 <b>ОПЛАТА УСПЕШНА!</b>\n\n"
            f"⭐ Premium: <b>{plan['name']}</b>\n"
            f"📅 До: <b>{expiry_text}</b>\n\n"
            "Теперь мониторинг Business-сообщений "
            "активирован."
        ),
        reply_markup=main_keyboard(),
    )


# ============================================================
# ADMIN KEYBOARD
# ============================================================

def admin_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📊 Статистика",
                    callback_data="admin:stats",
                ),
                InlineKeyboardButton(
                    text="👥 Пользователи",
                    callback_data="admin:users",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="💳 Платежи",
                    callback_data="admin:payments",
                ),
                InlineKeyboardButton(
                    text="🔗 Connections",
                    callback_data="admin:connections",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🎟 Промокоды",
                    callback_data="admin:promos",
                ),
                InlineKeyboardButton(
                    text="➕ Добавить промокод",
                    callback_data="admin:addpromo",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⭐ Выдать Premium",
                    callback_data="admin:give",
                ),
                InlineKeyboardButton(
                    text="❌ Забрать Premium",
                    callback_data="admin:remove",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📢 Рассылка",
                    callback_data="admin:broadcast",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Обновить",
                    callback_data="admin:menu",
                ),
            ],
        ]
    )


async def show_admin_panel(
    target: Message | CallbackQuery,
):
    text = (
        "🔐 <b>АДМИН-ПАНЕЛЬ</b>\n\n"
        "Выбери действие:"
    )

    if isinstance(target, CallbackQuery):
        await target.message.edit_text(
            text,
            reply_markup=admin_keyboard(),
        )
    else:
        await target.answer(
            text,
            reply_markup=admin_keyboard(),
        )


# ============================================================
# ADMIN STATS
# ============================================================

def admin_stats_text():
    conn = db()

    users = conn.execute("""
        SELECT COUNT(*) AS c
        FROM users
    """).fetchone()["c"]

    premium = conn.execute("""
        SELECT COUNT(*) AS c
        FROM users
        WHERE premium_forever = 1
           OR premium_until > ?
    """, (
        now_ts(),
    )).fetchone()["c"]

    payments = conn.execute("""
        SELECT COUNT(*) AS c
        FROM payments
        WHERE telegram_payment_charge_id IS NOT NULL
    """).fetchone()["c"]

    stars = conn.execute("""
        SELECT COALESCE(SUM(stars_spent), 0) AS s
        FROM users
    """).fetchone()["s"]

    connections = conn.execute("""
        SELECT COUNT(*) AS c
        FROM business_connections
        WHERE is_enabled = 1
    """).fetchone()["c"]

    messages = conn.execute("""
        SELECT COUNT(*) AS c
        FROM messages
    """).fetchone()["c"]

    conn.close()

    return (
        "📊 <b>СТАТИСТИКА</b>\n\n"
        f"👥 Пользователей: <b>{users}</b>\n"
        f"⭐ Premium: <b>{premium}</b>\n"
        f"💳 Платежей: <b>{payments}</b>\n"
        f"⭐ Stars: <b>{stars}</b>\n"
        f"🔗 Connections: <b>{connections}</b>\n"
        f"💬 Сохранённых сообщений: <b>{messages}</b>"
    )


# ============================================================
# ADMIN CALLBACKS
# ============================================================

@dp.callback_query(
    F.data.startswith("admin:")
)
async def admin_callback(
    callback: CallbackQuery,
):
    user_id = callback.from_user.id

    if not is_admin(user_id):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    action = callback.data.split(
        ":",
        1,
    )[1]

    await callback.answer()

    if action == "menu":
        await show_admin_panel(callback)
        return

    if action == "stats":
        await callback.message.edit_text(
            admin_stats_text(),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="◀️ Назад",
                            callback_data="admin:menu",
                        )
                    ]
                ]
            ),
        )
        return

    if action == "users":
        conn = db()

        rows = conn.execute("""
            SELECT
                user_id,
                username,
                first_name,
                premium_forever,
                premium_until,
                purchases_count,
                stars_spent
            FROM users
            ORDER BY updated_at DESC
            LIMIT 30
        """).fetchall()

        conn.close()

        text = "👥 <b>ПОСЛЕДНИЕ ПОЛЬЗОВАТЕЛИ</b>\n\n"

        if not rows:
            text += "Пользователей пока нет."

        for row in rows:
            premium = has_premium(
                int(row["user_id"])
            )

            text += (
                f"🆔 <code>{row['user_id']}</code> "
                f"{'⭐' if premium else '▫️'}\n"
                f"👤 {esc(row['username'] or row['first_name'] or '—')}\n"
                f"💳 {row['purchases_count']} покупок\n"
                f"⭐ {row['stars_spent']} Stars\n\n"
            )

        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="◀️ Назад",
                            callback_data="admin:menu",
                        )
                    ]
                ]
            ),
        )
        return

    if action == "payments":
        conn = db()

        rows = conn.execute("""
            SELECT
                p.*,
                u.username
            FROM payments p
            LEFT JOIN users u
                ON u.user_id = p.user_id
            ORDER BY p.created_at DESC
            LIMIT 30
        """).fetchall()

        conn.close()

        text = "💳 <b>ПЛАТЕЖИ</b>\n\n"

        if not rows:
            text += "Платежей нет."

        for row in rows:
            status = (
                "✅"
                if row["telegram_payment_charge_id"]
                else "⏳"
            )

            text += (
                f"{status} "
                f"<code>{row['user_id']}</code> "
                f"{esc(row['plan'])} — "
                f"⭐{row['stars']}\n"
            )

        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="◀️ Назад",
                            callback_data="admin:menu",
                        )
                    ]
                ]
            ),
        )
        return

    if action == "connections":
        conn = db()

        rows = conn.execute("""
            SELECT *
            FROM business_connections
            ORDER BY updated_at DESC
            LIMIT 30
        """).fetchall()

        conn.close()

        text = "🔗 <b>BUSINESS CONNECTIONS</b>\n\n"

        if not rows:
            text += "Подключений нет."

        for row in rows:
            text += (
                f"👤 Owner: "
                f"<code>{row['user_chat_id']}</code>\n"
                f"🔗 <code>{esc(row['business_connection_id'])}</code>\n"
                f"🟢 {'Да' if row['is_enabled'] else 'Нет'}\n\n"
            )

        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="◀️ Назад",
                            callback_data="admin:menu",
                        )
                    ]
                ]
            ),
        )
        return

    if action == "promos":
        conn = db()

        rows = conn.execute("""
            SELECT *
            FROM promo_codes
            ORDER BY created_at DESC
        """).fetchall()

        conn.close()

        text = "🎟 <b>ПРОМОКОДЫ</b>\n\n"

        text += "<b>Встроенные:</b>\n"
        text += "N1 — 7 дней Premium, максимум 25\n"
        text += "DAVE100 — Premium навсегда\n"
        text += "MET200$ — скидка 10%\n\n"

        text += "<b>Созданные в админке:</b>\n"

        if not rows:
            text += "Пока нет.\n"
        else:
            for row in rows:
                if row["promo_type"] == "days":
                    description = (
                        f"{row['value']} дней"
                    )
                elif row["promo_type"] == "forever":
                    description = "навсегда"
                else:
                    description = (
                        f"скидка {row['value']}%"
                    )

                limit = (
                    "∞"
                    if int(row["max_uses"]) == 0
                    else str(row["max_uses"])
                )

                text += (
                    f"\n🎟 <code>{esc(row['code'])}</code>\n"
                    f"Тип: {description}\n"
                    f"Использований: "
                    f"{row['uses']}/{limit}\n"
                )

        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="➕ Добавить",
                            callback_data="admin:addpromo",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="◀️ Назад",
                            callback_data="admin:menu",
                        )
                    ]
                ]
            ),
        )
        return

    if action == "addpromo":
        admin_states[user_id] = {
            "state": "add_promo_code"
        }

        await callback.message.edit_text(
            (
                "➕ <b>ДОБАВЛЕНИЕ ПРОМОКОДА</b>\n\n"
                "Отправь код промокода одним сообщением.\n\n"
                "Например:\n"
                "<code>WELCOME50</code>"
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="❌ Отмена",
                            callback_data="admin:menu",
                        )
                    ]
                ]
            ),
        )
        return

    if action == "give":
        admin_states[user_id] = {
            "state": "give_premium_user"
        }

        await callback.message.edit_text(
            (
                "⭐ <b>ВЫДАТЬ PREMIUM</b>\n\n"
                "Отправь Telegram ID пользователя."
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="❌ Отмена",
                            callback_data="admin:menu",
                        )
                    ]
                ]
            ),
        )
        return

    if action == "remove":
        admin_states[user_id] = {
            "state": "remove_premium_user"
        }

        await callback.message.edit_text(
            (
                "❌ <b>ЗАБРАТЬ PREMIUM</b>\n\n"
                "Отправь Telegram ID пользователя."
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="❌ Отмена",
                            callback_data="admin:menu",
                        )
                    ]
                ]
            ),
        )
        return

    if action == "broadcast":
        admin_states[user_id] = {
            "state": "broadcast"
        }

        await callback.message.edit_text(
            (
                "📢 <b>РАССЫЛКА</b>\n\n"
                "Отправь текст, который нужно разослать."
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="❌ Отмена",
                            callback_data="admin:menu",
                        )
                    ]
                ]
            ),
        )
        return


# ============================================================
# ADMIN PROMO TYPE
# ============================================================

def admin_promo_type_keyboard(code: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⭐ Дни Premium",
                    callback_data=f"promo_type:days:{code}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="♾ Premium навсегда",
                    callback_data=f"promo_type:forever:{code}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔥 Скидка %",
                    callback_data=f"promo_type:discount:{code}",
                )
            ],
        ]
    )


@dp.callback_query(
    F.data.startswith("promo_type:")
)
async def promo_type_callback(
    callback: CallbackQuery,
):
    user_id = callback.from_user.id

    if not is_admin(user_id):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    parts = callback.data.split(
        ":",
        2,
    )

    if len(parts) != 3:
        await callback.answer(
            "Ошибка.",
            show_alert=True,
        )
        return

    promo_type = parts[1]
    code = parts[2]

    if promo_type == "days":
        admin_states[user_id] = {
            "state": "add_promo_days",
            "code": code,
        }

        await callback.message.edit_text(
            (
                f"🎟 Код: <code>{esc(code)}</code>\n\n"
                "Сколько дней Premium выдавать?"
            )
        )

    elif promo_type == "forever":
        admin_states[user_id] = {
            "state": "add_promo_limit",
            "code": code,
            "promo_type": "forever",
            "value": 0,
        }

        await callback.message.edit_text(
            (
                f"🎟 Код: <code>{esc(code)}</code>\n\n"
                "Сколько раз можно использовать?\n\n"
                "Напиши <code>0</code> для бесконечного лимита."
            )
        )

    elif promo_type == "discount":
        admin_states[user_id] = {
            "state": "add_promo_discount",
            "code": code,
        }

        await callback.message.edit_text(
            (
                f"🎟 Код: <code>{esc(code)}</code>\n\n"
                "Какой процент скидки?\n\n"
                "Например: <code>20</code>"
            )
        )

    await callback.answer()


# ============================================================
# SAVE CUSTOM PROMO
# ============================================================

def create_custom_promo(
    code: str,
    promo_type: str,
    value: int,
    max_uses: int,
):
    code = code.strip()

    if not code:
        return False, "Пустой код."

    if len(code) > 64:
        return False, "Код слишком длинный."

    if code in BUILTIN_PROMOS:
        return False, "Такой код уже является встроенным."

    conn = db()

    try:
        conn.execute("""
            INSERT INTO promo_codes (
                code,
                promo_type,
                value,
                max_uses,
                uses,
                created_at,
                is_active
            )
            VALUES (?, ?, ?, ?, 0, ?, 1)
        """, (
            code,
            promo_type,
            value,
            max_uses,
            now_ts(),
        ))

        conn.commit()

    except sqlite3.IntegrityError:
        conn.close()
        return False, "Такой промокод уже существует."

    except Exception:
        conn.rollback()
        conn.close()
        logger.exception("create promo error")
        return False, "Ошибка создания промокода."

    conn.close()

    return True, "Промокод создан."


# ============================================================
# ADMIN TEXT ROUTER
#
# ВАЖНО:
# Здесь ОДИН F.text handler.
# Нет нескольких конкурирующих универсальных handlers.
# ============================================================

@dp.message(F.text)
async def text_router(
    message: Message,
):
    user_id = message.from_user.id
    text = message.text.strip()

    ensure_user(
        user_id,
        message.from_user.username,
        message.from_user.first_name,
    )

    # ========================================================
    # ADMIN ACCESS CODE
    # ========================================================

    if text == ADMIN_ACCESS_CODE:
        grant_admin_access(user_id)

        admin_states.pop(
            user_id,
            None,
        )

        await message.answer(
            "🔐 <b>Доступ к админ-панели предоставлен.</b>",
            reply_markup=main_keyboard(),
        )

        await message.answer(
            "🔐 <b>АДМИН-ПАНЕЛЬ</b>\n\n"
            "Выбери действие:",
            reply_markup=admin_keyboard(),
        )

        return

    # ========================================================
    # ADMIN STATE
    # ========================================================

    state_data = admin_states.get(
        user_id
    )

    if state_data and is_admin(user_id):
        state = state_data.get(
            "state"
        )

        # ----------------------------------------------------
        # ADD PROMO CODE
        # ----------------------------------------------------

        if state == "add_promo_code":
            code = text.upper()

            if len(code) < 2:
                await message.answer(
                    "❌ Код слишком короткий."
                )
                return

            if code in BUILTIN_PROMOS:
                await message.answer(
                    "❌ Этот код уже встроен в бота."
                )
                return

            conn = db()

            exists = conn.execute("""
                SELECT code
                FROM promo_codes
                WHERE code = ?
            """, (code,)).fetchone()

            conn.close()

            if exists:
                await message.answer(
                    "❌ Такой промокод уже существует."
                )
                return

            admin_states[user_id] = {
                "state": "select_promo_type",
                "code": code,
            }

            await message.answer(
                (
                    f"🎟 Код: <code>{esc(code)}</code>\n\n"
                    "Выбери тип промокода:"
                ),
                reply_markup=admin_promo_type_keyboard(
                    code
                ),
            )

            return

        # ----------------------------------------------------
        # DAYS
        # ----------------------------------------------------

        if state == "add_promo_days":
            try:
                days = int(text)
            except ValueError:
                await message.answer(
                    "❌ Введи число дней."
                )
                return

            if days <= 0 or days > 3650:
                await message.answer(
                    "❌ Количество дней должно быть от 1 до 3650."
                )
                return

            admin_states[user_id] = {
                "state": "add_promo_limit",
                "code": state_data["code"],
                "promo_type": "days",
                "value": days,
            }

            await message.answer(
                (
                    "📊 Сколько раз можно использовать "
                    "промокод?\n\n"
                    "Напиши <code>0</code> "
                    "для бесконечного количества."
                )
            )

            return

        # ----------------------------------------------------
        # DISCOUNT
        # ----------------------------------------------------

        if state == "add_promo_discount":
            try:
                percent = int(text)
            except ValueError:
                await message.answer(
                    "❌ Введи процент числом."
                )
                return

            if percent <= 0 or percent >= 100:
                await message.answer(
                    "❌ Скидка должна быть от 1 до 99%."
                )
                return

            admin_states[user_id] = {
                "state": "add_promo_limit",
                "code": state_data["code"],
                "promo_type": "discount",
                "value": percent,
            }

            await message.answer(
                (
                    "📊 Сколько раз можно использовать "
                    "промокод?\n\n"
                    "Напиши <code>0</code> "
                    "для бесконечного количества."
                )
            )

            return

        # ----------------------------------------------------
        # LIMIT
        # ----------------------------------------------------

        if state == "add_promo_limit":
            try:
                max_uses = int(text)
            except ValueError:
                await message.answer(
                    "❌ Введи число."
                )
                return

            if max_uses < 0:
                await message.answer(
                    "❌ Лимит не может быть отрицательным."
                )
                return

            code = state_data["code"]
            promo_type = state_data["promo_type"]
            value = state_data["value"]

            ok, result = create_custom_promo(
                code,
                promo_type,
                value,
                max_uses,
            )

            admin_states.pop(
                user_id,
                None,
            )

            await message.answer(
                (
                    "✅ " + result
                    if ok
                    else "❌ " + result
                ),
                reply_markup=main_keyboard(),
            )

            if ok:
                await message.answer(
                    "🔐 Админ-панель:",
                    reply_markup=admin_keyboard(),
                )

            return

        # ----------------------------------------------------
        # GIVE PREMIUM
        # ----------------------------------------------------

        if state == "give_premium_user":
            try:
                target_id = int(text)
            except ValueError:
                await message.answer(
                    "❌ Неверный Telegram ID."
                )
                return

            ensure_user(target_id)

            admin_states[user_id] = {
                "state": "give_premium_days",
                "target_id": target_id,
            }

            await message.answer(
                (
                    f"👤 Пользователь: "
                    f"<code>{target_id}</code>\n\n"
                    "Сколько дней Premium выдать?\n\n"
                    "Напиши <code>0</code> для Premium навсегда."
                )
            )

            return

        # ----------------------------------------------------
        # GIVE PREMIUM DAYS
        # ----------------------------------------------------

        if state == "give_premium_days":
            try:
                days = int(text)
            except ValueError:
                await message.answer(
                    "❌ Введи число."
                )
                return

            target_id = state_data["target_id"]

            if days == 0:
                grant_premium(
                    target_id,
                    forever=True,
                )

                result = "♾ Premium навсегда выдан."

            elif days > 0:
                grant_premium(
                    target_id,
                    days=days,
                )

                result = (
                    f"⭐ Premium на {days} дней выдан."
                )

            else:
                await message.answer(
                    "❌ Число не может быть отрицательным."
                )
                return

            admin_states.pop(
                user_id,
                None,
            )

            await message.answer(
                f"✅ {result}",
                reply_markup=main_keyboard(),
            )

            await message.answer(
                "🔐 Админ-панель:",
                reply_markup=admin_keyboard(),
            )

            return

        # ----------------------------------------------------
        # REMOVE PREMIUM
        # ----------------------------------------------------

        if state == "remove_premium_user":
            try:
                target_id = int(text)
            except ValueError:
                await message.answer(
                    "❌ Неверный Telegram ID."
                )
                return

            remove_premium(
                target_id
            )

            admin_states.pop(
                user_id,
                None,
            )

            await message.answer(
                (
                    "✅ Premium удалён у пользователя "
                    f"<code>{target_id}</code>."
                ),
                reply_markup=main_keyboard(),
            )

            await message.answer(
                "🔐 Админ-панель:",
                reply_markup=admin_keyboard(),
            )

            return

        # ----------------------------------------------------
        # BROADCAST
        # ----------------------------------------------------

        if state == "broadcast":
            admin_states.pop(
                user_id,
                None,
            )

            conn = db()

            rows = conn.execute("""
                SELECT user_id
                FROM users
            """).fetchall()

            conn.close()

            success = 0
            failed = 0

            for row in rows:
                target_id = int(
                    row["user_id"]
                )

                try:
                    await bot.send_message(
                        target_id,
                        text,
                    )

                    success += 1

                except Exception:
                    failed += 1

                await asyncio.sleep(
                    0.04
                )

            await message.answer(
                (
                    "📢 <b>РАССЫЛКА ЗАВЕРШЕНА</b>\n\n"
                    f"✅ Отправлено: {success}\n"
                    f"❌ Ошибок: {failed}"
                ),
                reply_markup=main_keyboard(),
            )

            await message.answer(
                "🔐 Админ-панель:",
                reply_markup=admin_keyboard(),
            )

            return

    # ========================================================
    # USER PROMO
    # ========================================================

    if state_data and state_data.get(
        "state"
    ) == "user_promo":
        admin_states.pop(
            user_id,
            None,
        )

        ok, result = use_promo(
            user_id,
            text,
        )

        await message.answer(
            result,
            reply_markup=main_keyboard(),
        )

        return

    # ========================================================
    # UNKNOWN TEXT
    # ========================================================

    # Не спамим пользователя ошибками.
    # Просто возвращаем основную клавиатуру.
    await message.answer(
        "Выбери нужный раздел кнопками ниже.",
        reply_markup=main_keyboard(),
    )


# ============================================================
# ADMIN ACCESS AFTER /START
# ============================================================

# Никаких:
# /admin
# /debug
# /test
#
# Вход только через:
# Dave200$


# ============================================================
# ERROR HANDLER
# ============================================================

@dp.errors()
async def global_error_handler(
    event,
):
    logger.exception(
        "Unhandled bot error: %s",
        event.exception,
    )


# ============================================================
# STARTUP
# ============================================================

async def main():
    init_db()

    logger.info(
        "Database initialized"
    )

    logger.info(
        "Starting bot..."
    )

    # Удаляем старый webhook, но НЕ удаляем pending updates.
    await bot.delete_webhook(
        drop_pending_updates=False
    )

    allowed_updates = [
        "message",
        "callback_query",
        "pre_checkout_query",

        # Telegram Business
        "business_connection",
        "business_message",
        "edited_business_message",
        "deleted_business_messages",
    ]

    await dp.start_polling(
        bot,
        allowed_updates=allowed_updates,
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        logger.info(
            "Bot stopped by user"
        )

    except Exception:
        logger.exception(
            "Fatal bot error"
        )
