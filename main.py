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

# ============================================================
# ВСТАВЬ СЮДА ТОКЕН ОТ @BotFather
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
    """
    Текущее время UTC.
    """

    return datetime.now(
        timezone.utc
    ).isoformat()


def escape_text(text):
    """
    Экранируем пользовательский текст
    перед отправкой с parse_mode=HTML.
    """

    return html.escape(
        str(text or "")
    )


def get_message_text(message):
    """
    Получаем текст сообщения или подпись к медиа.
    """

    if message.text:
        return message.text

    if message.caption:
        return message.caption

    return ""


def get_sender_info(message):
    """
    Возвращает:
        sender_id
        sender_name
        sender_username
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
    Определяем тип сообщения.
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


def is_private_message(message):
    """
    Жёсткая проверка.

    Разрешаем только обычные личные чаты.
    """

    return (
        message.chat is not None
        and message.chat.type == "private"
    )


# ============================================================
# BUSINESS CONNECTION
# ============================================================

def save_business_connection(connection):
    """
    Сохраняем подключение Business-аккаунта.

    Один бот может быть подключён к нескольким аккаунтам.
    Поэтому каждое подключение имеет свой connection_id.
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

        now_iso(),
        now_iso()
    ))

    db.commit()


def get_connection(connection_id):
    """
    Получаем Business Connection из БД.
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


async def get_business_connection(
    connection_id
):
    """
    Получаем Business Connection.

    Сначала пытаемся взять его из БД.
    Если нет — запрашиваем у Telegram.
    """

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

    except Exception as e:

        logger.error(
            "Не удалось получить Business Connection %s: %s",
            connection_id,
            e
        )

        return None


async def get_owner_id(
    connection_id
):
    """
    Возвращает Telegram ID владельца
    конкретного Business Connection.
    """

    connection = await get_business_connection(
        connection_id
    )

    if not connection:
        return None

    return connection[1]


async def get_log_chat_id(
    connection_id
):
    """
    Возвращает приватный chat_id владельца.

    Логи каждого аккаунта отправляются
    только владельцу этого подключения.
    """

    connection = await get_business_connection(
        connection_id
    )

    if not connection:
        return None

    return connection[2]


# ============================================================
# MESSAGE DATABASE
# ============================================================

def save_message(
    connection_id,
    message
):
    """
    Сохраняем сообщение.

    Нам нужны эти данные для восстановления
    удалённых сообщений и фотографий.
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

    # --------------------------------------------------------
    # ФОТО
    # --------------------------------------------------------

    if message.photo:

        # Самое большое доступное разрешение.
        photo_file_id = (
            message.photo[-1].file_id
        )

        # Проверяем наличие Telegram spoiler.
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
    Ищем ранее сохранённое сообщение.
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
# SENDER FORMAT
# ============================================================

def format_sender(
    sender_id,
    sender_name,
    sender_username
):
    """
    Красивое отображение отправителя.
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
    Команда /start.
    """

    await message.answer(
        "🕵️ <b>Business Message Monitor</b>\n\n"

        "Бот работает.\n\n"

        "Подключи меня через:\n"

        "<b>Настройки Telegram → "
        "Telegram Business → "
        "Автоматизация чатов</b>\n\n"

        "После подключения я буду "
        "обрабатывать разрешённые личные чаты.\n\n"

        "🗑 Удалённые сообщения\n"
        "✏️ Редактирование\n"
        "📷 Фотографии\n"
        "🫥 Фото со спойлером",

        parse_mode="HTML"
    )

    logger.info(
        "/start: %s",
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
    Подключение / изменение / отключение
    Business аккаунта.
    """

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

    # --------------------------------------------------------
    # Сохраняем подключение
    # --------------------------------------------------------

    save_business_connection(
        connection
    )

    # --------------------------------------------------------
    # Уведомляем владельца
    # --------------------------------------------------------

    try:

        if connection.is_enabled:

            text = (
                "🟢 <b>Business Bot подключён</b>\n\n"

                f"👤 Аккаунт:\n"
                f"{escape_text(connection.user.full_name)}\n\n"

                f"🆔 ID:\n"
                f"<code>{connection.user.id}</code>\n\n"

                "Теперь я буду обрабатывать "
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
            "Ошибка connection notification: %s",
            e
        )


# ============================================================
# NEW BUSINESS MESSAGE
# ============================================================

@dp.business_message()
async def business_message_handler(
    message: Message
):
    """
    Новое сообщение в подключённом Business аккаунте.
    """

    # --------------------------------------------------------
    # Только ЛС
    # --------------------------------------------------------

    if not is_private_message(message):

        return

    connection_id = (
        message.business_connection_id
    )

    if not connection_id:

        return

    # --------------------------------------------------------
    # Сохраняем сообщение
    # --------------------------------------------------------

    save_message(
        connection_id,
        message
    )

    logger.info(
        "NEW | connection=%s chat=%s message=%s type=%s",
        connection_id,
        message.chat.id,
        message.message_id,
        get_message_type(message)
    )

    # ========================================================
    # ПРОВЕРКА REPLY
    # ========================================================

    if not message.reply_to_message:

        return

    replied_message_id = (
        message.reply_to_message.message_id
    )

    # --------------------------------------------------------
    # Ищем сообщение, на которое ответили
    # --------------------------------------------------------

    saved = get_saved_message(
        connection_id,
        message.chat.id,
        replied_message_id
    )

    if not saved:

        return

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

    # ========================================================
    # СКРЫТОЕ ФОТО
    # ========================================================

    if (
        message_type == "photo"
        and photo_file_id
        and photo_has_spoiler
    ):

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

        caption_text = (
            caption
            or "Без подписи"
        )

        log_caption = (
            "🫥 <b>СКРЫТОЕ ФОТО</b>\n\n"

            f"👤 <b>Отправитель:</b>\n"
            f"{sender_info}\n\n"

            f"💬 <b>Чат:</b> "
            f"<code>{message.chat.id}</code>\n"

            f"🆔 <b>Message ID:</b> "
            f"<code>{replied_message_id}</code>\n\n"

            "📷 На скрытое фото ответили.\n\n"

            f"📝 <b>Подпись:</b>\n"
            f"{escape_text(caption_text)}"
        )

        try:

            # ------------------------------------------------
            # Отправляем обычное фото БЕЗ спойлера.
            #
            # Это относится только к обычному Telegram
            # media spoiler, а не к исчезающим фотографиям.
            # ------------------------------------------------

            await bot.send_photo(
                log_chat_id,

                photo_file_id,

                caption=log_caption,

                parse_mode="HTML",

                has_spoiler=False
            )

            logger.info(
                "Spoiler photo sent: message=%s owner=%s",
                replied_message_id,
                log_chat_id
            )

        except Exception as e:

            logger.error(
                "Ошибка отправки spoiler photo: %s",
                e
            )


# ============================================================
# EDITED BUSINESS MESSAGE
# ============================================================

@dp.edited_business_message()
async def edited_business_message_handler(
    message: Message
):
    """
    Сообщение было изменено.

    ВАЖНО:

    Если сообщение принадлежит владельцу
    Business аккаунта — ничего не отправляем.

    Логируем только изменения собеседника.
    """

    # --------------------------------------------------------
    # Только ЛС
    # --------------------------------------------------------

    if not is_private_message(message):

        return

    connection_id = (
        message.business_connection_id
    )

    if not connection_id:

        return

    # --------------------------------------------------------
    # Ищем старую версию
    # --------------------------------------------------------

    old = get_saved_message(
        connection_id,
        message.chat.id,
        message.message_id
    )

    # --------------------------------------------------------
    # Получаем ID владельца
    # --------------------------------------------------------

    owner_id = await get_owner_id(
        connection_id
    )

    # --------------------------------------------------------
    # Если сообщение есть в БД
    # --------------------------------------------------------

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

    # ========================================================
    # НЕ ЛОГИРУЕМ СОБСТВЕННЫЕ СООБЩЕНИЯ
    # ========================================================

    if (
        owner_id is not None
        and sender_id == owner_id
    ):

        # Обновляем запись,
        # но НЕ отправляем лог.
        save_message(
            connection_id,
            message
        )

        logger.info(
            "EDIT IGNORED: owner message=%s",
            message.message_id
        )

        return

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
    # Получаем чат владельца
    # --------------------------------------------------------

    log_chat_id = await get_log_chat_id(
        connection_id
    )

    if not log_chat_id:

        return

    # --------------------------------------------------------
    # Если содержимое не изменилось
    # --------------------------------------------------------

    if old_text == new_text:

        save_message(
            connection_id,
            message
        )

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

        logger.info(
            "EDIT LOG: connection=%s message=%s",
            connection_id,
            message.message_id
        )

    except Exception as e:

        logger.error(
            "Ошибка edit log: %s",
            e
        )

    # --------------------------------------------------------
    # Обновляем БД
    # --------------------------------------------------------

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
    """
    Обработка удалённых сообщений.

    ВАЖНО:

    Telegram сообщает только ID удалённых сообщений.

    Поэтому текст/фото берём из SQLite.

    Также проверяем sender_id:
    если сообщение было отправлено владельцем
    Business аккаунта — лог НЕ отправляется.
    """

    # --------------------------------------------------------
    # Только ЛС
    # --------------------------------------------------------

    if event.chat.type != "private":

        return

    connection_id = (
        event.business_connection_id
    )

    chat_id = event.chat.id

    # --------------------------------------------------------
    # Получаем ID владельца
    # --------------------------------------------------------

    owner_id = await get_owner_id(
        connection_id
    )

    # --------------------------------------------------------
    # Получаем чат владельца
    # --------------------------------------------------------

    log_chat_id = await get_log_chat_id(
        connection_id
    )

    if not log_chat_id:

        return

    # ========================================================
    # ОБРАБОТКА ВСЕХ УДАЛЁННЫХ MESSAGE ID
    # ========================================================

    for message_id in event.message_ids:

        saved = get_saved_message(
            connection_id,
            chat_id,
            message_id
        )

        # ====================================================
        # НЕТ В БАЗЕ
        # ====================================================

        if not saved:

            # ------------------------------------------------
            # Если Telegram не дал нам данные отправителя,
            # мы не можем надёжно определить, был ли это
            # владелец.
            #
            # В этом случае отправляем технический лог.
            # ------------------------------------------------

            log_text = (
                "🗑 <b>СООБЩЕНИЕ УДАЛЕНО</b>\n\n"

                f"💬 Чат: "
                f"<code>{chat_id}</code>\n"

                f"🆔 Message ID: "
                f"<code>{message_id}</code>\n\n"

                "⚠️ <b>Текст не удалось восстановить.</b>\n\n"

                "Сообщение отсутствовало в локальной базе."
            )

            try:

                await bot.send_message(
                    log_chat_id,
                    log_text,
                    parse_mode="HTML"
                )

            except Exception as e:

                logger.error(
                    "Ошибка unknown delete log: %s",
                    e
                )

            continue

        # ====================================================
        # ЕСТЬ В БАЗЕ
        # ====================================================

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

        # ----------------------------------------------------
        # НЕ ЛОГИРУЕМ СОБСТВЕННОЕ УДАЛЕНИЕ
        # ----------------------------------------------------

        if (
            owner_id is not None
            and sender_id == owner_id
        ):

            logger.info(
                "DELETE IGNORED: owner message=%s",
                message_id
            )

            continue

        sender_info = format_sender(
            sender_id,
            sender_name,
            sender_username
        )

        # ====================================================
        # УДАЛЁННАЯ ФОТОГРАФИЯ
        # ====================================================

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

                    # В логе показываем обычное фото.
                    has_spoiler=False
                )

                logger.info(
                    "DELETE PHOTO LOG: %s",
                    message_id
                )

            except Exception as e:

                logger.error(
                    "Ошибка отправки deleted photo: %s",
                    e
                )

            continue

        # ====================================================
        # УДАЛЁННОЕ ТЕКСТОВОЕ СООБЩЕНИЕ
        # ====================================================

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

            logger.info(
                "DELETE LOG: connection=%s message=%s",
                connection_id,
                message_id
            )

        except Exception as e:

            logger.error(
                "Ошибка delete log: %s",
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
        "Запуск бота..."
    )

    # --------------------------------------------------------
    # Запускаем polling
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