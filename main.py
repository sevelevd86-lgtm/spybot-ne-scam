import asyncio
import html
import logging
import sqlite3
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message


# ============================================================
#                     НАСТРОЙКИ
# ============================================================

# ============================================================
# ВСТАВЬ СЮДА ТОКЕН ОТ @BotFather
# ============================================================

BOT_TOKEN = "8893376358:AAGJ6VaHZqRAyX9CIiu6GOStcet9yg0hL7M"


# ============================================================
# БАЗА ДАННЫХ
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
    """
    Возвращает текущее время в ISO формате.
    """

    return datetime.now(
        timezone.utc
    ).isoformat()


def escape_text(text):
    """
    Экранирует HTML,
    чтобы пользовательский текст
    не ломал сообщения бота.
    """

    return html.escape(
        str(text or "")
    )


def get_message_text(message):
    """
    Получает текст или caption сообщения.
    """

    if message.text:
        return message.text

    if message.caption:
        return message.caption

    return ""


def get_sender_info(message):
    """
    Получает информацию об отправителе.
    """

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


def get_message_type(message):
    """
    Определяет тип сообщения.
    """

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


# ============================================================
# BUSINESS CONNECTION DATABASE
# ============================================================

def save_business_connection(connection):
    """
    Сохраняет Business Connection.

    Один бот может быть подключён к множеству
    Telegram Business аккаунтов.

    Для каждого подключения используется
    отдельный connection_id.
    """

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
    """
    Получает сохранённое Business Connection.
    """

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


async def get_log_chat_id(connection_id):
    """
    Возвращает Telegram chat_id владельца
    Business Connection.

    Если соединение ещё не было сохранено,
    пытаемся получить его напрямую через Bot API.
    """

    connection = get_connection(
        connection_id
    )

    if connection:
        return connection[2]

    try:

        telegram_connection = (
            await bot.get_business_connection(
                business_connection_id=connection_id
            )
        )

        save_business_connection(
            telegram_connection
        )

        return telegram_connection.user_chat_id

    except Exception as e:

        logger.error(
            "Не удалось получить Business Connection %s: %s",
            connection_id,
            e
        )

        return None


# ============================================================
# СООБЩЕНИЯ DATABASE
# ============================================================

def save_message(
    connection_id,
    message
):
    """
    Сохраняет сообщение.

    Особенно важно сохранять:
    - message_id
    - текст
    - фото
    - file_id
    - spoiler
    - отправителя

    Это позволяет восстановить информацию
    после удаления сообщения.
    """

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

        # Берём фотографию максимального размера.
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
            ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?
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
    """
    Ищет сообщение в базе.
    """

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
# FORMAT SENDER
# ============================================================

def format_sender(
    sender_id,
    sender_name,
    sender_username
):
    """
    Красивый формат отправителя.
    """

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
# /START
# ============================================================

@dp.message(
    CommandStart()
)
async def start_handler(
    message: Message
):
    """
    Обычный /start бота.

    Также полезен для того, чтобы владелец
    имел приватный чат с ботом.
    """

    await message.answer(
        "🕵️ <b>Business Message Monitor</b>\n\n"

        "Бот работает.\n\n"

        "Для подключения:\n"

        "Настройки Telegram → "
        "Telegram Business → "
        "Автоматизация чатов\n\n"

        "Добавь этого бота и выбери нужные "
        "личные чаты.\n\n"

        "После подключения бот будет "
        "сохранять сообщения и отслеживать:\n"

        "🗑 удаления\n"
        "✏️ редактирования\n"
        "🫥 фотографии со спойлером\n"
        "📷 ответы на скрытые фотографии",
        
        parse_mode="HTML"
    )

    logger.info(
        "/start от пользователя %s",
        message.from_user.id
    )


# ============================================================
# BUSINESS CONNECTION
# ============================================================

@dp.business_connection()
async def business_connection_handler(
    connection
):
    """
    Срабатывает при:

    - подключении бота;
    - изменении прав;
    - отключении бота.
    """

    logger.info(
        "======================================"
    )

    logger.info(
        "BUSINESS CONNECTION"
    )

    logger.info(
        "Connection ID: %s",
        connection.id
    )

    logger.info(
        "User ID: %s",
        connection.user.id
    )

    logger.info(
        "User chat ID: %s",
        connection.user_chat_id
    )

    logger.info(
        "Enabled: %s",
        connection.is_enabled
    )

    # --------------------------------------------------------
    # Сохраняем connection
    # --------------------------------------------------------

    save_business_connection(
        connection
    )

    # --------------------------------------------------------
    # Отправляем владельцу уведомление
    # --------------------------------------------------------

    try:

        if connection.is_enabled:

            text = (
                "🟢 <b>Business Bot подключён</b>\n\n"

                f"👤 Аккаунт:\n"
                f"{escape_text(connection.user.full_name)}\n\n"

                f"🆔 ID:\n"
                f"<code>{connection.user.id}</code>\n\n"

                f"🔑 Connection ID:\n"
                f"<code>{escape_text(connection.id)}</code>\n\n"

                "Теперь я могу обрабатывать "
                "разрешённые личные чаты."
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

        logger.error(
            "Ошибка отправки connection уведомления: %s",
            e
        )


# ============================================================
# НОВОЕ BUSINESS MESSAGE
# ============================================================

@dp.business_message()
async def business_message_handler(
    message: Message
):
    """
    Обрабатывает новые сообщения
    из подключённого Business аккаунта.
    """

    # --------------------------------------------------------
    # ТОЛЬКО ЛИЧНЫЕ ЧАТЫ
    # --------------------------------------------------------

    if message.chat.type != "private":

        return

    connection_id = (
        message.business_connection_id
    )

    if not connection_id:

        logger.warning(
            "Business message без connection_id"
        )

        return

    # --------------------------------------------------------
    # Сохраняем сообщение
    # --------------------------------------------------------

    save_message(
        connection_id,
        message
    )

    logger.info(
        "Новое сообщение: connection=%s "
        "chat=%s message=%s type=%s",
        connection_id,
        message.chat.id,
        message.message_id,
        get_message_type(message)
    )

    # ========================================================
    # ПРОВЕРЯЕМ ОТВЕТ НА СООБЩЕНИЕ
    # ========================================================

    if message.reply_to_message:

        replied_message_id = (
            message.reply_to_message.message_id
        )

        saved = get_saved_message(
            connection_id,
            message.chat.id,
            replied_message_id
        )

        # ----------------------------------------------------
        # Если ответили на сохранённое сообщение
        # ----------------------------------------------------

        if saved:

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
            ) = saved

            # ------------------------------------------------
            # НАЙДЕНА СКРЫТАЯ ФОТОГРАФИЯ
            # ------------------------------------------------

            if (
                message_type == "photo"
                and photo_file_id
                and photo_has_spoiler
            ):

                log_chat_id = (
                    await get_log_chat_id(
                        connection_id
                    )
                )

                if log_chat_id:

                    sender_info = format_sender(
                        sender_id,
                        sender_name,
                        sender_username
                    )

                    caption_text = (
                        caption
                        or "Без подписи"
                    )

                    log_caption = (
                        "🫥 <b>СКРЫТАЯ ФОТОГРАФИЯ</b>\n\n"

                        "👤 <b>Отправитель:</b>\n"
                        f"{sender_info}\n\n"

                        f"💬 <b>Чат:</b> "
                        f"<code>{message.chat.id}</code>\n"

                        f"🆔 <b>Message ID:</b> "
                        f"<code>{replied_message_id}</code>\n\n"

                        "📷 На фотографию ответили.\n"
                        "Спойлер снят в этом логе.\n\n"

                        f"📝 <b>Подпись:</b>\n"
                        f"{escape_text(caption_text)}"
                    )

                    try:

                        await bot.send_photo(
                            log_chat_id,

                            photo_file_id,

                            caption=log_caption,

                            parse_mode="HTML",

                            # ВАЖНО:
                            # False = фотография будет
                            # показана без спойлера.
                            has_spoiler=False
                        )

                        logger.info(
                            "Раскрыта скрытая фотография "
                            "message=%s для user_chat_id=%s",
                            replied_message_id,
                            log_chat_id
                        )

                    except Exception as e:

                        logger.error(
                            "Ошибка отправки скрытой фотографии: %s",
                            e
                        )


# ============================================================
# РЕДАКТИРОВАНИЕ BUSINESS MESSAGE
# ============================================================

@dp.edited_business_message()
async def edited_business_message_handler(
    message: Message
):
    """
    Обрабатывает редактирование сообщения.

    Показывает:

    🔴 БЫЛО

    🟢 СТАЛО
    """

    # --------------------------------------------------------
    # Только ЛС
    # --------------------------------------------------------

    if message.chat.type != "private":

        return

    connection_id = (
        message.business_connection_id
    )

    if not connection_id:

        return

    # --------------------------------------------------------
    # Получаем старую версию
    # --------------------------------------------------------

    old = get_saved_message(
        connection_id,
        message.chat.id,
        message.message_id
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
            else "Неизвестный пользователь"
        )

        sender_username = (
            message.from_user.username
            if message.from_user
            else None
        )

        old_text = (
            "[Старая версия не сохранена]"
        )

    # --------------------------------------------------------
    # Новая версия
    # --------------------------------------------------------

    new_text = get_message_text(
        message
    )

    if not new_text:

        new_text = "[сообщение без текста]"

    if not old_text:

        old_text = "[сообщение без текста]"

    # --------------------------------------------------------
    # Получаем владельца
    # --------------------------------------------------------

    log_chat_id = await get_log_chat_id(
        connection_id
    )

    if not log_chat_id:

        return

    # --------------------------------------------------------
    # Если текст не изменился
    # --------------------------------------------------------

    if old_text == new_text:

        save_message(
            connection_id,
            message
        )

        return

    # --------------------------------------------------------
    # Формируем лог
    # --------------------------------------------------------

    sender_info = format_sender(
        sender_id,
        sender_name,
        sender_username
    )

    log_text = (
        "✏️ <b>СООБЩЕНИЕ ИЗМЕНЕНО</b>\n\n"

        f"👤 <b>Отправитель:</b>\n"
        f"{sender_info}\n\n"

        f"💬 <b>Чат:</b> "
        f"<code>{message.chat.id}</code>\n"

        f"🆔 <b>Message ID:</b> "
        f"<code>{message.message_id}</code>\n\n"

        "🔴 <b>БЫЛО:</b>\n"
        f"<blockquote>{escape_text(old_text)}</blockquote>\n\n"

        "🟢 <b>СТАЛО:</b>\n"
        f"<blockquote>{escape_text(new_text)}</blockquote>"
    )

    try:

        await bot.send_message(
            log_chat_id,
            log_text,
            parse_mode="HTML"
        )

    except Exception as e:

        logger.error(
            "Ошибка edit-лога: %s",
            e
        )

    # --------------------------------------------------------
    # Обновляем сохранённую версию
    # --------------------------------------------------------

    save_message(
        connection_id,
        message
    )


# ============================================================
# УДАЛЕНИЕ BUSINESS MESSAGES
# ============================================================

@dp.deleted_business_messages()
async def deleted_business_messages_handler(
    event
):
    """
    Обрабатывает удаление сообщений.

    Telegram передаёт только:

        connection_id
        chat
        message_ids

    Поэтому старое содержимое берём из SQLite.
    """

    # --------------------------------------------------------
    # Только личные чаты
    # --------------------------------------------------------

    if event.chat.type != "private":

        return

    connection_id = (
        event.business_connection_id
    )

    chat_id = event.chat.id

    # --------------------------------------------------------
    # Получаем владельца
    # --------------------------------------------------------

    log_chat_id = await get_log_chat_id(
        connection_id
    )

    if not log_chat_id:

        return

    # --------------------------------------------------------
    # Каждое удалённое сообщение
    # --------------------------------------------------------

    for message_id in event.message_ids:

        saved = get_saved_message(
            connection_id,
            chat_id,
            message_id
        )

        # ====================================================
        # СООБЩЕНИЕ ЕСТЬ В БАЗЕ
        # ====================================================

        if saved:

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

            sender_info = format_sender(
                sender_id,
                sender_name,
                sender_username
            )

            # ------------------------------------------------
            # Удалённая фотография
            # ------------------------------------------------

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

                    f"👤 <b>Отправитель:</b>\n"
                    f"{sender_info}\n\n"

                    f"💬 <b>Чат:</b> "
                    f"<code>{chat_id}</code>\n"

                    f"🆔 <b>Message ID:</b> "
                    f"<code>{message_id}</code>\n\n"

                    f"📝 <b>Подпись:</b>\n"
                    f"{escape_text(caption_text)}"
                )

                try:

                    # Отправляем без спойлера,
                    # даже если исходное фото было скрытым.
                    await bot.send_photo(
                        log_chat_id,
                        photo_file_id,
                        caption=log_caption,
                        parse_mode="HTML",
                        has_spoiler=False
                    )

                except Exception as e:

                    logger.error(
                        "Ошибка отправки удалённого фото: %s",
                        e
                    )

                continue

            # ------------------------------------------------
            # Обычное текстовое сообщение
            # ------------------------------------------------

            if not message_text:

                message_text = (
                    f"[{message_type}] "
                    "сообщение без текста"
                )

            log_text = (
                "🗑 <b>СООБЩЕНИЕ УДАЛЕНО</b>\n\n"

                f"👤 <b>Отправитель:</b>\n"
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

        # ====================================================
        # СООБЩЕНИЯ НЕТ В БАЗЕ
        # ====================================================

        else:

            log_text = (
                "🗑 <b>СООБЩЕНИЕ УДАЛЕНО</b>\n\n"

                f"💬 <b>Чат:</b> "
                f"<code>{chat_id}</code>\n"

                f"🆔 <b>Message ID:</b> "
                f"<code>{message_id}</code>\n\n"

                "⚠️ <b>Текст не удалось восстановить.</b>\n\n"

                "Сообщение не было сохранено "
                "до момента удаления."
            )

        # ----------------------------------------------------
        # Отправляем лог
        # ----------------------------------------------------

        try:

            await bot.send_message(
                log_chat_id,
                log_text,
                parse_mode="HTML"
            )

            logger.info(
                "Удалено сообщение: "
                "connection=%s chat=%s message=%s",
                connection_id,
                chat_id,
                message_id
            )

        except Exception as e:

            logger.error(
                "Ошибка delete-лога: %s",
                e
            )


# ============================================================
# MAIN
# ============================================================

async def main():

    logger.info(
        "=========================================="
    )

    logger.info(
        "Telegram Business Message Monitor"
    )

    logger.info(
        "Multi-account version"
    )

    logger.info(
        "=========================================="
    )

    logger.info(
        "Бот запускается..."
    )

    # --------------------------------------------------------
    # Запуск polling
    # --------------------------------------------------------

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