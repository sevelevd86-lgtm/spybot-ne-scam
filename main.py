import asyncio
import html
import logging
import sqlite3
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message


# ============================================================
#                         CONFIG
# ============================================================

BOT_TOKEN = "8893376358:AAGJ6VaHZqRAyX9CIiu6GOStcet9yg0hL7M"


# ============================================================
# ФАЙЛ БАЗЫ ДАННЫХ
# ============================================================

DATABASE_FILE = "business_monitor.db"


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

bot = Bot(
    token=BOT_TOKEN
)

dp = Dispatcher()


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

db.commit()


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def now_iso():
    return datetime.now(timezone.utc).isoformat()


def escape_text(text):
    return html.escape(str(text or ""))


def get_message_text(message):
    if message.text:
        return message.text
    if message.caption:
        return message.caption
    return ""


def get_sender_info(message):
    if not message.from_user:
        return (None, "Неизвестный пользователь", None)
    user = message.from_user
    return (user.id, user.full_name or "Без имени", user.username)


def get_message_type(message):
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


def is_private_message(message):
    return message.chat is not None and message.chat.type == "private"


# ============================================================
# BUSINESS CONNECTION
# ============================================================

def save_business_connection(connection):
    user = connection.user
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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        now_iso(),
        now_iso()
    ))
    db.commit()


def get_connection(connection_id):
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
    """, (connection_id,))
    return cursor.fetchone()


async def get_business_connection(connection_id):
    saved = get_connection(connection_id)
    if saved:
        return saved
    try:
        connection = await bot.get_business_connection(
            business_connection_id=connection_id
        )
        save_business_connection(connection)
        return get_connection(connection_id)
    except Exception as e:
        logger.error("Не удалось получить Business Connection %s: %s", connection_id, e)
        return None


async def get_owner_id(connection_id):
    connection = await get_business_connection(connection_id)
    if not connection:
        return None
    return connection[1]


async def get_log_chat_id(connection_id):
    connection = await get_business_connection(connection_id)
    if not connection:
        return None
    return connection[2]


# ============================================================
# MESSAGE DATABASE
# ============================================================

def save_message(connection_id, message):
    sender_id, sender_name, sender_username = get_sender_info(message)
    message_type = get_message_type(message)
    text = get_message_text(message)
    photo_file_id = None
    photo_has_spoiler = 0
    if message.photo:
        photo_file_id = message.photo[-1].file_id
        photo_has_spoiler = 1 if message.has_media_spoiler else 0
    caption = message.caption or ""
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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(connection_id, chat_id, message_id)
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


def get_saved_message(connection_id, chat_id, message_id):
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
    """, (connection_id, chat_id, message_id))
    return cursor.fetchone()


# ============================================================
# SENDER FORMAT
# ============================================================

def format_sender(sender_id, sender_name, sender_username):
    result = f"<b>{escape_text(sender_name)}</b>"
    if sender_username:
        result += f" (@{escape_text(sender_username)})"
    if sender_id:
        result += f"\nID: <code>{sender_id}</code>"
    return result


# ============================================================
# /START  (ИЗМЕНЕН)
# ============================================================

@dp.message(CommandStart())
async def start_handler(message: Message):
    await message.answer(
        "🕵️ <b>Business Message Monitor</b>\n\n"
        "✅ Бот работает.\n\n"
        "Подключи меня через:\n"
        "<b>Настройки Telegram → "
        "Telegram Business → "
        "Автоматизация чатов</b>\n\n"
        "После подключения я буду обрабатывать разрешённые личные чаты.\n\n"
        "📋 <b>Возможности:</b>\n"
        "🗑 Удалённые сообщения\n"
        "✏️ Редактирование\n"
        "📷 Фотографии\n"
        "🫥 Фото со спойлером\n"
        "🔓 <b>Показывает одноразовые (view‑once) фото</b> — "
        "автоматически присылает их владельцу как обычные.",
        parse_mode="HTML"
    )
    logger.info("/start: %s", message.from_user.id)


# ============================================================
# BUSINESS CONNECTION
# ============================================================

@dp.business_connection()
async def business_connection_handler(connection):
    logger.info("========================================")
    logger.info("BUSINESS CONNECTION")
    logger.info("connection_id=%s", connection.id)
    logger.info("user_id=%s", connection.user.id)
    logger.info("user_chat_id=%s", connection.user_chat_id)
    logger.info("enabled=%s", connection.is_enabled)

    save_business_connection(connection)

    try:
        if connection.is_enabled:
            text = (
                "🟢 <b>Business Bot подключён</b>\n\n"
                f"👤 Аккаунт:\n{escape_text(connection.user.full_name)}\n\n"
                f"🆔 ID:\n<code>{connection.user.id}</code>\n\n"
                "Теперь я буду обрабатывать разрешённые личные чаты."
            )
        else:
            text = (
                "🔴 <b>Business Bot отключён</b>\n\n"
                f"👤 Аккаунт:\n{escape_text(connection.user.full_name)}\n\n"
                f"🆔 ID:\n<code>{connection.user.id}</code>"
            )
        await bot.send_message(connection.user_chat_id, text, parse_mode="HTML")
    except Exception as e:
        logger.error("Ошибка connection notification: %s", e)


# ============================================================
# NEW BUSINESS MESSAGE (основной)
# ============================================================

@dp.business_message()
async def business_message_handler(message: Message):
    if not is_private_message(message):
        return
    connection_id = message.business_connection_id
    if not connection_id:
        return

    save_message(connection_id, message)
    logger.info("NEW | connection=%s chat=%s message=%s type=%s",
                connection_id, message.chat.id, message.message_id, get_message_type(message))

    if not message.reply_to_message:
        return

    replied_message_id = message.reply_to_message.message_id
    saved = get_saved_message(connection_id, message.chat.id, replied_message_id)
    if not saved:
        return

    (sender_id, sender_name, sender_username,
     old_text, message_type, photo_file_id,
     photo_has_spoiler, caption, created_at) = saved

    if message_type == "photo" and photo_file_id and photo_has_spoiler:
        log_chat_id = await get_log_chat_id(connection_id)
        if not log_chat_id:
            return
        sender_info = format_sender(sender_id, sender_name, sender_username)
        caption_text = caption or "Без подписи"
        log_caption = (
            "🫥 <b>СКРЫТОЕ ФОТО</b>\n\n"
            f"👤 <b>Отправитель:</b>\n{sender_info}\n\n"
            f"💬 <b>Чат:</b> <code>{message.chat.id}</code>\n"
            f"🆔 <b>Message ID:</b> <code>{replied_message_id}</code>\n\n"
            "📷 На скрытое фото ответили.\n\n"
            f"📝 <b>Подпись:</b>\n{escape_text(caption_text)}"
        )
        try:
            await bot.send_photo(log_chat_id, photo_file_id,
                                 caption=log_caption,
                                 parse_mode="HTML",
                                 has_spoiler=False)
            logger.info("Spoiler photo sent: message=%s owner=%s", replied_message_id, log_chat_id)
        except Exception as e:
            logger.error("Ошибка отправки spoiler photo: %s", e)


# ============================================================
# EDITED BUSINESS MESSAGE
# ============================================================

@dp.edited_business_message()
async def edited_business_message_handler(message: Message):
    if not is_private_message(message):
        return
    connection_id = message.business_connection_id
    if not connection_id:
        return

    old = get_saved_message(connection_id, message.chat.id, message.message_id)
    owner_id = await get_owner_id(connection_id)

    if old:
        (sender_id, sender_name, sender_username,
         old_text, message_type, photo_file_id,
         photo_has_spoiler, caption, created_at) = old
    else:
        sender_id = message.from_user.id if message.from_user else None
        sender_name = message.from_user.full_name if message.from_user else "Неизвестный пользователь"
        sender_username = message.from_user.username if message.from_user else None
        old_text = "[Старая версия не сохранена]"

    if owner_id is not None and sender_id == owner_id:
        save_message(connection_id, message)
        logger.info("EDIT IGNORED: owner message=%s", message.message_id)
        return

    new_text = get_message_text(message) or "[сообщение без текста]"
    old_text = old_text or "[сообщение без текста]"
    log_chat_id = await get_log_chat_id(connection_id)
    if not log_chat_id:
        return

    if old_text == new_text:
        save_message(connection_id, message)
        return

    sender_info = format_sender(sender_id, sender_name, sender_username)
    log_text = (
        "✏️ <b>СООБЩЕНИЕ ИЗМЕНЕНО</b>\n\n"
        f"👤 <b>Собеседник:</b>\n{sender_info}\n\n"
        f"💬 <b>Чат:</b> <code>{message.chat.id}</code>\n"
        f"🆔 <b>Message ID:</b> <code>{message.message_id}</code>\n\n"
        "🔴 <b>БЫЛО:</b>\n"
        f"<blockquote>{escape_text(old_text)}</blockquote>\n\n"
        "🟢 <b>СТАЛО:</b>\n"
        f"<blockquote>{escape_text(new_text)}</blockquote>"
    )
    try:
        await bot.send_message(log_chat_id, log_text, parse_mode="HTML")
        logger.info("EDIT LOG: connection=%s message=%s", connection_id, message.message_id)
    except Exception as e:
        logger.error("Ошибка edit log: %s", e)

    save_message(connection_id, message)


# ============================================================
# DELETED BUSINESS MESSAGES
# ============================================================

@dp.deleted_business_messages()
async def deleted_business_messages_handler(event):
    if event.chat.type != "private":
        return
    connection_id = event.business_connection_id
    chat_id = event.chat.id
    owner_id = await get_owner_id(connection_id)
    log_chat_id = await get_log_chat_id(connection_id)
    if not log_chat_id:
        return

    for message_id in event.message_ids:
        saved = get_saved_message(connection_id, chat_id, message_id)
        if not saved:
            log_text = (
                "🗑 <b>СООБЩЕНИЕ УДАЛЕНО</b>\n\n"
                f"💬 Чат: <code>{chat_id}</code>\n"
                f"🆔 Message ID: <code>{message_id}</code>\n\n"
                "⚠️ <b>Текст не удалось восстановить.</b>\n\n"
                "Сообщение отсутствовало в локальной базе."
            )
            try:
                await bot.send_message(log_chat_id, log_text, parse_mode="HTML")
            except Exception as e:
                logger.error("Ошибка unknown delete log: %s", e)
            continue

        (sender_id, sender_name, sender_username,
         message_text, message_type, photo_file_id,
         photo_has_spoiler, caption, created_at) = saved

        if owner_id is not None and sender_id == owner_id:
            logger.info("DELETE IGNORED: owner message=%s", message_id)
            continue

        sender_info = format_sender(sender_id, sender_name, sender_username)

        if message_type == "photo" and photo_file_id:
            caption_text = caption or "Без подписи"
            log_caption = (
                "🗑 <b>ФОТО УДАЛЕНО</b>\n\n"
                f"👤 <b>Собеседник:</b>\n{sender_info}\n\n"
                f"💬 <b>Чат:</b> <code>{chat_id}</code>\n"
                f"🆔 <b>Message ID:</b> <code>{message_id}</code>\n\n"
                f"📝 <b>Подпись:</b>\n{escape_text(caption_text)}"
            )
            try:
                await bot.send_photo(log_chat_id, photo_file_id,
                                     caption=log_caption,
                                     parse_mode="HTML",
                                     has_spoiler=False)
                logger.info("DELETE PHOTO LOG: %s", message_id)
            except Exception as e:
                logger.error("Ошибка отправки deleted photo: %s", e)
            continue

        if not message_text:
            message_text = f"[{message_type}] сообщение без текста"
        log_text = (
            "🗑 <b>СООБЩЕНИЕ УДАЛЕНО</b>\n\n"
            f"👤 <b>Собеседник:</b>\n{sender_info}\n\n"
            f"💬 <b>Чат:</b> <code>{chat_id}</code>\n"
            f"🆔 <b>Message ID:</b> <code>{message_id}</code>\n\n"
            "📄 <b>Содержимое:</b>\n"
            f"<blockquote>{escape_text(message_text)}</blockquote>\n\n"
            f"🕒 <b>Сохранено:</b>\n<code>{escape_text(created_at)}</code>"
        )
        try:
            await bot.send_message(log_chat_id, log_text, parse_mode="HTML")
            logger.info("DELETE LOG: connection=%s message=%s", connection_id, message_id)
        except Exception as e:
            logger.error("Ошибка delete log: %s", e)


# ============================================================
# НОВЫЙ ОБРАБОТЧИК: АВТОМАТИЧЕСКАЯ ОТПРАВКА VIEW‑ONCE ФОТО
# (с универсальной проверкой)
# ============================================================

@dp.business_message()
async def view_once_auto_handler(message: Message):
    """
    Автоматически отправляет владельцу view‑once фото как обычное.
    Проверяет как self_destruct_timer, так и has_protected_content.
    """
    # 1. Только фото, только если есть признак одноразовости
    if not message.photo:
        return

    # Проверяем оба возможных поля
    is_view_once = (
        message.self_destruct_timer is not None
        or message.has_protected_content
    )
    if not is_view_once:
        return

    connection_id = message.business_connection_id
    if not connection_id:
        return

    # 2. Игнорируем ответы (чтобы не дублировать с reply‑обработчиком)
    if message.reply_to_message:
        return

    # 3. Владелец не должен получать свои же фото
    owner_id = await get_owner_id(connection_id)
    if owner_id is None:
        return
    if message.from_user and message.from_user.id == owner_id:
        return

    log_chat_id = await get_log_chat_id(connection_id)
    if not log_chat_id:
        return

    # 4. Сохраняем (на всякий случай, если основной хендлер ещё не сработал)
    save_message(connection_id, message)

    # 5. Отправляем обычное фото
    try:
        await bot.send_photo(
            chat_id=log_chat_id,
            photo=message.photo[-1].file_id,
            caption=(
                f"🔓 <b>View‑once фото (автоматически)</b>\n"
                f"От: {escape_text(message.from_user.full_name)}\n"
                f"Одноразовое фото сохранено как обычное."
            ),
            parse_mode="HTML"
        )
        logger.info(
            "✅ AUTO: view‑once photo sent to owner %s (msg %s)",
            log_chat_id, message.message_id
        )
    except Exception as e:
        logger.error("Ошибка автоматической отправки view‑once: %s", e)


# ============================================================
# НОВЫЙ ОБРАБОТЧИК: ОТПРАВКА VIEW‑ONCE ПО ОТВЕТУ
# ============================================================

@dp.business_message()
async def view_once_reply_handler(message: Message):
    """
    Отправляет владельцу view‑once фото, если он отвечает на него.
    """
    if not message.reply_to_message:
        return

    replied = message.reply_to_message
    if not replied.photo:
        return

    is_view_once = (
        replied.self_destruct_timer is not None
        or replied.has_protected_content
    )
    if not is_view_once:
        return

    connection_id = message.business_connection_id
    if not connection_id:
        return

    owner_id = await get_owner_id(connection_id)
    if owner_id is None:
        return
    if message.from_user.id != owner_id:
        return

    saved = get_saved_message(
        connection_id,
        replied.chat.id,
        replied.message_id
    )
    if not saved:
        logger.warning("View‑once photo not found in DB, msg_id=%s", replied.message_id)
        return

    (sender_id, sender_name, sender_username,
     text, msg_type, photo_file_id,
     photo_has_spoiler, caption, created_at) = saved

    if not photo_file_id:
        logger.warning("No photo_file_id for view‑once msg %s", replied.message_id)
        return

    log_chat_id = await get_log_chat_id(connection_id)
    if not log_chat_id:
        return

    try:
        await bot.send_photo(
            chat_id=log_chat_id,
            photo=photo_file_id,
            caption=(
                f"🔓 <b>View‑once фото (запрошено ответом)</b>\n"
                f"От: {escape_text(sender_name)}\n"
                f"Оригинал был одноразовым, но сохранён."
            ),
            parse_mode="HTML"
        )
        logger.info(
            "✅ REPLY: view‑once photo sent to owner %s (orig msg %s)",
            log_chat_id, replied.message_id
        )
    except Exception as e:
        logger.error("Ошибка отправки view‑once по ответу: %s", e)


# ============================================================
# MAIN
# ============================================================

async def main():
    logger.info("==========================================")
    logger.info("Telegram Business Message Monitor")
    logger.info("Multi-account version")
    logger.info("==========================================")
    logger.info("Запуск бота...")

    await dp.start_polling(
        bot,
        allowed_updates=[
            "message",
            "business_connection",
            "business_message",
            "edited_business_message",
            "deleted_business_messages"
        ]
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен.")
    except Exception as e:
        logger.exception("Критическая ошибка: %s", e)
    finally:
        db.close()