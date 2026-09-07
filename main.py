import asyncio
import html
import logging
import sqlite3
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message


# ============================================================
#                    НАСТРОЙКИ
# ============================================================

# Токен, который выдал @BotFather
BOT_TOKEN = "8893376358:AAGJ6VaHZqRAyX9CIiu6GOStcet9yg0hL7M"

# Telegram ID, куда отправлять логи.
#
# Например:
# LOG_CHAT_ID = 123456789
#
# Узнать свой ID можно через специального Telegram-бота
# или временно вывести message.from_user.id в консоль.
LOG_CHAT_ID = 5018476227


# Файл базы данных.
#
# В ней будут храниться сообщения, чтобы после их удаления
# мы могли восстановить старый текст.
DATABASE_FILE = "messages.db"


# ============================================================
#                    LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


# ============================================================
#                    TELEGRAM
# ============================================================

bot = Bot(
    token=BOT_TOKEN
)

dp = Dispatcher()


# ============================================================
#                    DATABASE
# ============================================================

db = sqlite3.connect(
    DATABASE_FILE,
    check_same_thread=False
)

db.execute("""
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    business_connection_id TEXT NOT NULL,

    chat_id INTEGER NOT NULL,

    message_id INTEGER NOT NULL,

    sender_id INTEGER,

    sender_name TEXT,

    sender_username TEXT,

    text TEXT,

    message_type TEXT,

    created_at TEXT,

    updated_at TEXT
)
""")

# Индекс для быстрого поиска сообщения
db.execute("""
CREATE INDEX IF NOT EXISTS idx_messages_lookup
ON messages (
    business_connection_id,
    chat_id,
    message_id
)
""")

db.commit()


# ============================================================
#                    DATABASE FUNCTIONS
# ============================================================

def save_message(
    business_connection_id: str,
    message: Message
):
    """
    Сохраняет сообщение в SQLite.

    Это особенно важно для удаления:

    Telegram сообщает нам ID удалённого сообщения,
    но текст удалённого сообщения уже отсутствует.

    Поэтому текст необходимо сохранить заранее.
    """

    sender_id = None
    sender_name = None
    sender_username = None

    if message.from_user:

        sender_id = message.from_user.id

        sender_name = (
            message.from_user.full_name
            or ""
        )

        sender_username = (
            message.from_user.username
        )

    # --------------------------------------------------------
    # Определяем содержимое сообщения
    # --------------------------------------------------------

    text = message.text

    message_type = "text"

    if message.photo:
        message_type = "photo"

    elif message.video:
        message_type = "video"

    elif message.document:
        message_type = "document"

    elif message.voice:
        message_type = "voice"

    elif message.audio:
        message_type = "audio"

    elif message.sticker:
        message_type = "sticker"

    elif message.animation:
        message_type = "animation"

    elif message.location:
        message_type = "location"

    elif message.contact:
        message_type = "contact"

    elif message.poll:
        message_type = "poll"

    elif message.text:
        message_type = "text"

    # Если текста нет, но есть caption
    if not text and message.caption:
        text = message.caption

    if not text:
        text = ""

    now = datetime.now().isoformat()

    db.execute(
        """
        INSERT OR REPLACE INTO messages (
            id,
            business_connection_id,
            chat_id,
            message_id,
            sender_id,
            sender_name,
            sender_username,
            text,
            message_type,
            created_at,
            updated_at
        )

        VALUES (
            COALESCE(
                (
                    SELECT id
                    FROM messages
                    WHERE business_connection_id = ?
                    AND chat_id = ?
                    AND message_id = ?
                ),
                NULL
            ),

            ?,
            ?,
            ?,
            ?,
            ?,
            ?,
            ?,
            ?,

            COALESCE(
                (
                    SELECT created_at
                    FROM messages
                    WHERE business_connection_id = ?
                    AND chat_id = ?
                    AND message_id = ?
                ),
                ?
            ),

            ?
        )
        """,
        (
            business_connection_id,
            message.chat.id,
            message.message_id,

            business_connection_id,
            message.chat.id,
            message.message_id,

            sender_id,
            sender_name,
            sender_username,
            text,
            message_type,

            business_connection_id,
            message.chat.id,
            message.message_id,

            now,

            now
        )
    )

    db.commit()


def get_saved_message(
    business_connection_id: str,
    chat_id: int,
    message_id: int
):
    """
    Ищет сообщение в базе по Business Connection,
    chat_id и message_id.
    """

    cursor = db.execute(
        """
        SELECT
            sender_id,
            sender_name,
            sender_username,
            text,
            message_type,
            created_at
        FROM messages
        WHERE business_connection_id = ?
        AND chat_id = ?
        AND message_id = ?
        LIMIT 1
        """,
        (
            business_connection_id,
            chat_id,
            message_id
        )
    )

    return cursor.fetchone()


def update_message(
    business_connection_id: str,
    message: Message
):
    """
    Обновляет сохранённое сообщение после редактирования.
    """

    text = message.text

    if not text and message.caption:
        text = message.caption

    if not text:
        text = ""

    now = datetime.now().isoformat()

    db.execute(
        """
        UPDATE messages
        SET
            text = ?,
            updated_at = ?
        WHERE business_connection_id = ?
        AND chat_id = ?
        AND message_id = ?
        """,
        (
            text,
            now,

            business_connection_id,
            message.chat.id,
            message.message_id
        )
    )

    db.commit()


# ============================================================
#                    HTML HELPERS
# ============================================================

def escape(text: str) -> str:
    """
    Защищает текст пользователя перед отправкой
    с parse_mode=HTML.
    """

    return html.escape(
        text or ""
    )


def format_sender(
    name,
    username,
    user_id
):
    """
    Формирует красивое отображение пользователя.
    """

    result = escape(
        name or "Неизвестный пользователь"
    )

    if username:
        result += (
            f" (@{escape(username)})"
        )

    result += (
        f"\nID: <code>{user_id}</code>"
    )

    return result


# ============================================================
#                    /START
# ============================================================

@dp.message(CommandStart())
async def start_handler(message: Message):
    """
    Команда /start.

    Нужна также для того, чтобы бот мог отправлять
    владельцу уведомления в этот чат.
    """

    await message.answer(
        "🕵️ <b>Business Message Monitor</b>\n\n"
        "Бот запущен.\n\n"
        "Теперь подключи меня к своему Telegram Business "
        "аккаунту через:\n\n"
        "Настройки → Telegram Business → "
        "Автоматизация чатов.\n\n"
        "После подключения я буду сохранять сообщения "
        "из разрешённых личных чатов и отслеживать "
        "их редактирование и удаление.",
        parse_mode="HTML"
    )

    logger.info(
        "Пользователь запустил бота. ID: %s",
        message.from_user.id
    )


# ============================================================
#                    BUSINESS CONNECTION
# ============================================================

@dp.business_connection()
async def business_connection_handler(
    connection
):
    """
    Срабатывает, когда пользователь:

    - подключил бота;
    - изменил настройки подключения;
    - отключил бота.

    BusinessConnection содержит connection_id,
    user_chat_id и права бота.
    """

    logger.info(
        "BUSINESS CONNECTION"
    )

    logger.info(
        "connection_id: %s",
        connection.id
    )

    logger.info(
        "user_id: %s",
        connection.user.id
    )

    logger.info(
        "is_enabled: %s",
        connection.is_enabled
    )

    if connection.rights:

        logger.info(
            "rights: %s",
            connection.rights
        )

    # --------------------------------------------------------
    # Отправляем уведомление владельцу
    # --------------------------------------------------------

    try:

        if connection.is_enabled:

            await bot.send_message(
                LOG_CHAT_ID,

                "🟢 <b>Business Bot подключён</b>\n\n"

                f"👤 Аккаунт: "
                f"{escape(connection.user.full_name)}\n"

                f"🆔 ID: "
                f"<code>{connection.user.id}</code>\n\n"

                f"🔑 Connection ID:\n"
                f"<code>{escape(connection.id)}</code>",

                parse_mode="HTML"
            )

        else:

            await bot.send_message(
                LOG_CHAT_ID,

                "🔴 <b>Business Bot отключён</b>\n\n"

                f"👤 Аккаунт: "
                f"{escape(connection.user.full_name)}\n"

                f"🆔 ID: "
                f"<code>{connection.user.id}</code>",

                parse_mode="HTML"
            )

    except Exception as e:

        logger.error(
            "Не удалось отправить лог подключения: %s",
            e
        )


# ============================================================
#                    НОВОЕ BUSINESS-СООБЩЕНИЕ
# ============================================================

@dp.business_message()
async def business_message_handler(
    message: Message
):
    """
    Получает новое сообщение из подключённого
    Telegram Business аккаунта.

    ВАЖНО:

    Здесь проверяем тип чата.

    Нам нужны ТОЛЬКО личные чаты.

    Поэтому:

        message.chat.type == "private"

    """

    # --------------------------------------------------------
    # ЖЁСТКИЙ ФИЛЬТР ЛИЧНЫХ ЧАТОВ
    # --------------------------------------------------------

    if message.chat.type != "private":

        logger.info(
            "Пропущен не-личный чат: %s",
            message.chat.type
        )

        return

    # --------------------------------------------------------
    # Business Connection ID
    # --------------------------------------------------------

    business_connection_id = (
        message.business_connection_id
    )

    if not business_connection_id:

        logger.warning(
            "У сообщения отсутствует "
            "business_connection_id"
        )

        return

    # --------------------------------------------------------
    # Сохраняем сообщение
    # --------------------------------------------------------

    save_message(
        business_connection_id,
        message
    )

    logger.info(
        "Сохранено сообщение: "
        "chat=%s message=%s",
        message.chat.id,
        message.message_id
    )


# ============================================================
#                    РЕДАКТИРОВАНИЕ
# ============================================================

@dp.edited_business_message()
async def edited_business_message_handler(
    message: Message
):
    """
    Обрабатывает редактирование сообщения.

    Сначала берём старую версию из SQLite.

    Потом сравниваем её с новой версией.

    После этого отправляем владельцу:

        🔴 БЫЛО
        🟢 СТАЛО
    """

    # --------------------------------------------------------
    # Только ЛС
    # --------------------------------------------------------

    if message.chat.type != "private":

        return

    business_connection_id = (
        message.business_connection_id
    )

    if not business_connection_id:

        return

    # --------------------------------------------------------
    # Получаем старую версию
    # --------------------------------------------------------

    old_message = get_saved_message(
        business_connection_id,
        message.chat.id,
        message.message_id
    )

    # --------------------------------------------------------
    # Если старой версии нет
    # --------------------------------------------------------

    if old_message is None:

        old_text = (
            "[Старая версия не была сохранена]"
        )

        sender_id = (
            message.from_user.id
            if message.from_user
            else 0
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

    else:

        (
            sender_id,
            sender_name,
            sender_username,
            old_text,
            message_type,
            created_at
        ) = old_message

    # --------------------------------------------------------
    # Получаем новую версию
    # --------------------------------------------------------

    new_text = message.text

    if not new_text and message.caption:
        new_text = message.caption

    if not new_text:
        new_text = "[сообщение без текста]"

    if not old_text:
        old_text = "[сообщение без текста]"

    # --------------------------------------------------------
    # Если текст не изменился
    # --------------------------------------------------------

    if old_text == new_text:

        # Всё равно обновляем запись
        save_message(
            business_connection_id,
            message
        )

        return

    # --------------------------------------------------------
    # Отправляем уведомление
    # --------------------------------------------------------

    try:

        sender_info = format_sender(
            sender_name,
            sender_username,
            sender_id
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
            f"<blockquote>{escape(old_text)}</blockquote>\n\n"

            "🟢 <b>СТАЛО:</b>\n"
            f"<blockquote>{escape(new_text)}</blockquote>"
        )

        await bot.send_message(
            LOG_CHAT_ID,
            log_text,
            parse_mode="HTML"
        )

        logger.info(
            "Сообщение изменено: chat=%s message=%s",
            message.chat.id,
            message.message_id
        )

    except Exception as e:

        logger.error(
            "Ошибка отправки edit-лога: %s",
            e
        )

    # --------------------------------------------------------
    # Обновляем запись
    # --------------------------------------------------------

    save_message(
        business_connection_id,
        message
    )


# ============================================================
#                    УДАЛЕНИЕ
# ============================================================

@dp.deleted_business_messages()
async def deleted_business_messages_handler(
    event
):
    """
    Обрабатывает удаление сообщений
    из Telegram Business аккаунта.

    Telegram присылает:

        business_connection_id
        chat
        message_ids

    Самого текста удалённых сообщений
    в событии НЕТ.

    Поэтому используем SQLite.
    """

    # --------------------------------------------------------
    # Только личные чаты
    # --------------------------------------------------------

    if event.chat.type != "private":

        logger.info(
            "Удаление проигнорировано: "
            "чат не является ЛС (%s)",
            event.chat.type
        )

        return

    business_connection_id = (
        event.business_connection_id
    )

    chat_id = event.chat.id

    # --------------------------------------------------------
    # Обрабатываем каждое удалённое сообщение
    # --------------------------------------------------------

    for message_id in event.message_ids:

        saved = get_saved_message(
            business_connection_id,
            chat_id,
            message_id
        )

        # ----------------------------------------------------
        # Если сообщения нет в БД
        # ----------------------------------------------------

        if saved is None:

            log_text = (
                "🗑 <b>СООБЩЕНИЕ УДАЛЕНО</b>\n\n"

                f"💬 Чат: "
                f"<code>{chat_id}</code>\n"

                f"🆔 Message ID: "
                f"<code>{message_id}</code>\n\n"

                "⚠️ <b>Текст не удалось восстановить.</b>\n\n"

                "Сообщение не было сохранено "
                "до момента удаления."
            )

            try:

                await bot.send_message(
                    LOG_CHAT_ID,
                    log_text,
                    parse_mode="HTML"
                )

            except Exception as e:

                logger.error(
                    "Ошибка отправки delete-лога: %s",
                    e
                )

            continue

        # ----------------------------------------------------
        # Достаём сохранённые данные
        # ----------------------------------------------------

        (
            sender_id,
            sender_name,
            sender_username,
            text,
            message_type,
            created_at
        ) = saved

        # ----------------------------------------------------
        # Если сообщение было отправлено ботом/владельцем,
        # тоже можем его показать.
        #
        # Если тебе нужны ТОЛЬКО удаления собеседника,
        # ниже можно добавить проверку sender_id.
        # ----------------------------------------------------

        sender_info = format_sender(
            sender_name,
            sender_username,
            sender_id
        )

        if not text:
            text = (
                f"[{message_type}] "
                "сообщение без текста"
            )

        # ----------------------------------------------------
        # Формируем лог
        # ----------------------------------------------------

        log_text = (
            "🗑 <b>СООБЩЕНИЕ УДАЛЕНО</b>\n\n"

            f"👤 <b>Отправитель:</b>\n"
            f"{sender_info}\n\n"

            f"💬 <b>Чат:</b> "
            f"<code>{chat_id}</code>\n"

            f"🆔 <b>Message ID:</b> "
            f"<code>{message_id}</code>\n\n"

            "📄 <b>Содержимое:</b>\n"
            f"<blockquote>{escape(text)}</blockquote>\n\n"

            f"🕒 <b>Получено:</b> "
            f"{escape(created_at)}"
        )

        # ----------------------------------------------------
        # Отправляем лог
        # ----------------------------------------------------

        try:

            await bot.send_message(
                LOG_CHAT_ID,
                log_text,
                parse_mode="HTML"
            )

            logger.info(
                "Удалено сообщение: "
                "chat=%s message=%s",
                chat_id,
                message_id
            )

        except Exception as e:

            logger.error(
                "Ошибка отправки delete-лога: %s",
                e
            )


# ============================================================
#                    MAIN
# ============================================================

async def main():

    logger.info(
        "=========================================="
    )

    logger.info(
        "Telegram Business Message Monitor"
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

        # Явно разрешаем только необходимые обновления.
        #
        # Это не позволит боту получать ненужные типы
        # событий.
        allowed_updates=[
            "message",
            "business_connection",
            "business_message",
            "edited_business_message",
            "deleted_business_messages"
        ]
    )


# ============================================================
#                    ENTRY POINT
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