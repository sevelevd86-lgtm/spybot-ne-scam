import asyncio
import math
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    LabeledPrice,
    Message,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder


# ============================================================
# НАСТРОЙКИ
# ============================================================

BOT_TOKEN = ""

ADMIN_IDS = {
    123456789,
}

DB_NAME = "business_monitor.db"


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


LEGACY_PROMOS = {
    "N1": {
        "type": "free",
        "days": 7,
        "max_uses": 25,
    },
    "DAVE100": {
        "type": "forever",
        "days": 0,
        "max_uses": None,
    },
    "MET200$": {
        "type": "discount",
        "discount": 10,
        "days": 0,
        "max_uses": None,
    },
}


# ============================================================
# BOT
# ============================================================

if not BOT_TOKEN:
    raise RuntimeError(
        "Укажи BOT_TOKEN в начале файла."
    )


bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    )
)

dp = Dispatcher()


# ============================================================
# LOCK
# ============================================================

db_lock = asyncio.Lock()


# ============================================================
# FSM
# ============================================================

class PromoStates(StatesGroup):
    waiting_code = State()
    waiting_type = State()
    waiting_value = State()
    waiting_days = State()
    waiting_max_uses = State()


class AdminStates(StatesGroup):
    waiting_user_id = State()
    waiting_duration = State()
    waiting_remove_user_id = State()


class AdminSearchStates(StatesGroup):
    waiting_user_id = State()


# ============================================================
# DATABASE
# ============================================================

db = sqlite3.connect(
    DB_NAME,
    check_same_thread=False
)

db.row_factory = sqlite3.Row


def now_ts() -> int:
    return int(time.time())


def format_datetime(ts: Optional[int]) -> str:
    if not ts:
        return "—"

    dt = datetime.fromtimestamp(
        ts,
        tz=timezone.utc
    )

    return dt.strftime(
        "%d.%m.%Y %H:%M"
    )


def format_duration(seconds: int) -> str:
    if seconds <= 0:
        return "0 минут"

    days = seconds // 86400
    seconds %= 86400

    hours = seconds // 3600
    seconds %= 3600

    minutes = seconds // 60

    parts = []

    if days:
        parts.append(f"{days} д.")

    if hours:
        parts.append(f"{hours} ч.")

    if minutes:
        parts.append(f"{minutes} мин.")

    return " ".join(parts[:3])


def normalize_username(
    username: Optional[str]
) -> str:

    if not username:
        return "—"

    return f"@{username}"


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def init_db():
    cur = db.cursor()

    # ========================================================
    # BUSINESS CONNECTIONS
    # ========================================================

    cur.execute("""
        CREATE TABLE IF NOT EXISTS business_connections (
            business_connection_id TEXT PRIMARY KEY,
            user_chat_id INTEGER NOT NULL,
            can_reply INTEGER DEFAULT 0,
            is_enabled INTEGER DEFAULT 1,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
    """)

    # ========================================================
    # MESSAGES
    # ========================================================

    cur.execute("""
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
    """)

    # ========================================================
    # USERS
    # ========================================================

    cur.execute("""
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
    """)

    # ========================================================
    # PAYMENTS
    # ========================================================

    cur.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            payload TEXT NOT NULL,
            plan TEXT,
            stars INTEGER NOT NULL,
            telegram_payment_charge_id TEXT,
            created_at INTEGER NOT NULL
        )
    """)

    # ========================================================
    # CUSTOM PROMO CODES
    # ========================================================

    cur.execute("""
        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY,
            promo_type TEXT NOT NULL,
            discount_percent INTEGER DEFAULT 0,
            premium_days INTEGER DEFAULT 0,
            max_uses INTEGER,
            uses INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_at INTEGER NOT NULL,
            created_by INTEGER
        )
    """)

    # ========================================================
    # PROMO USES
    # ========================================================

    cur.execute("""
        CREATE TABLE IF NOT EXISTS promo_uses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            promo_code TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            UNIQUE(user_id, promo_code)
        )
    """)

    # ========================================================
    # ADMIN ACTIONS
    # ========================================================

    cur.execute("""
        CREATE TABLE IF NOT EXISTS admin_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER NOT NULL,
            target_user_id INTEGER,
            action TEXT NOT NULL,
            value TEXT,
            created_at INTEGER NOT NULL
        )
    """)

    db.commit()


init_db()


# ============================================================
# USERS
# ============================================================

def ensure_user(
    user_id: int,
    username: Optional[str],
    first_name: Optional[str]
):
    ts = now_ts()

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
        user_id,
        username,
        first_name,
        ts,
        ts
    ))

    db.commit()


def get_user(user_id: int):
    return db.execute("""
        SELECT *
        FROM users
        WHERE user_id = ?
    """, (
        user_id,
    )).fetchone()


def get_premium_active(user_id: int) -> bool:
    user = get_user(user_id)

    if not user:
        return False

    if user["premium_forever"]:
        return True

    if not user["premium_until"]:
        return False

    return user["premium_until"] > now_ts()


def premium_remaining(user_id: int) -> int:
    user = get_user(user_id)

    if not user:
        return 0

    if user["premium_forever"]:
        return -1

    if not user["premium_until"]:
        return 0

    return max(
        0,
        user["premium_until"] - now_ts()
    )


def has_discount(user_id: int) -> bool:

    row = db.execute("""
        SELECT id
        FROM promo_uses
        WHERE user_id = ?
          AND promo_code = ?
        LIMIT 1
    """, (
        user_id,
        "MET200$"
    )).fetchone()

    if row:
        return True

    row = db.execute("""
        SELECT pu.id
        FROM promo_uses pu
        JOIN promo_codes pc
            ON pc.code = pu.promo_code
        WHERE pu.user_id = ?
          AND pc.promo_type = 'discount'
        LIMIT 1
    """, (
        user_id,
    )).fetchone()

    return bool(row)


def discounted_price(
    user_id: int,
    original_price: int
) -> int:

    if not has_discount(user_id):
        return original_price

    return max(
        1,
        math.floor(
            original_price * 0.9
        )
    )


# ============================================================
# PREMIUM
# ============================================================

def activate_premium_days(
    user_id: int,
    days: int
):
    user = get_user(user_id)

    if not user:
        return

    if user["premium_forever"]:
        return

    current = user["premium_until"] or 0

    current = max(
        current,
        now_ts()
    )

    new_until = (
        current
        + days * 86400
    )

    db.execute("""
        UPDATE users
        SET premium_until = ?,
            updated_at = ?
        WHERE user_id = ?
    """, (
        new_until,
        now_ts(),
        user_id
    ))

    db.commit()


def activate_premium_hours(
    user_id: int,
    hours: int
):
    user = get_user(user_id)

    if not user:
        return

    if user["premium_forever"]:
        return

    current = user["premium_until"] or 0

    current = max(
        current,
        now_ts()
    )

    new_until = (
        current
        + hours * 3600
    )

    db.execute("""
        UPDATE users
        SET premium_until = ?,
            updated_at = ?
        WHERE user_id = ?
    """, (
        new_until,
        now_ts(),
        user_id
    ))

    db.commit()


def activate_premium_forever(
    user_id: int
):
    db.execute("""
        UPDATE users
        SET premium_forever = 1,
            premium_until = NULL,
            updated_at = ?
        WHERE user_id = ?
    """, (
        now_ts(),
        user_id
    ))

    db.commit()


def remove_premium(user_id: int):
    db.execute("""
        UPDATE users
        SET premium_until = NULL,
            premium_forever = 0,
            updated_at = ?
        WHERE user_id = ?
    """, (
        now_ts(),
        user_id
    ))

    db.commit()


# ============================================================
# BOTTOM MENU
# ============================================================

def bottom_menu():

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
                    text="🔗 Подключение"
                )
            ]
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выберите действие"
    )


# ============================================================
# START
# ============================================================

START_TEXT = """
<b>🐻‍❄️ SPY</b>

Минималистичный мониторинг ваших чатов.

Подключите бота через Telegram Business,
и он будет работать автоматически.

<b>Статус:</b> 🟢 готов
"""


def main_inline_keyboard():

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="⭐ Premium",
            callback_data="premium"
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="👤 Профиль",
            callback_data="profile"
        ),
        InlineKeyboardButton(
            text="🔗 Подключить",
            callback_data="connect"
        )
    )

    return builder.as_markup()


@dp.message(CommandStart())
async def start_handler(
    message: Message
):

    ensure_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    await message.answer(
        START_TEXT,
        reply_markup=main_inline_keyboard()
    )

    await message.answer(
        "Меню",
        reply_markup=bottom_menu()
    )


# ============================================================
# PROFILE
# ============================================================

def profile_text(user_id: int) -> str:

    user = get_user(user_id)

    if not user:
        return "Профиль не найден."

    if user["premium_forever"]:

        status = "🟢 Активен"
        remaining = "Навсегда"
        expires = "Навсегда"

    elif get_premium_active(user_id):

        status = "🟢 Активен"

        remaining = format_duration(
            premium_remaining(user_id)
        )

        expires = format_datetime(
            user["premium_until"]
        )

    else:

        status = "⚪ Не активен"
        remaining = "—"
        expires = "—"

    discount = (
        "10% активна"
        if has_discount(user_id)
        else "Нет"
    )

    return f"""
<b>👤 Профиль</b>

ID: <code>{user_id}</code>
Username: {normalize_username(user["username"])}

<b>Premium</b>
Статус: {status}
Осталось: {remaining}
До: {expires}

<b>Аккаунт</b>
Покупок: {user["purchases_count"]}
Потрачено: ⭐ {user["stars_spent"]}
Скидка: {discount}
"""


@dp.callback_query(F.data == "profile")
async def profile_callback(
    callback: CallbackQuery
):

    ensure_user(
        callback.from_user.id,
        callback.from_user.username,
        callback.from_user.first_name
    )

    await callback.answer()

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="⭐ Premium",
            callback_data="premium"
        )
    )

    await callback.message.answer(
        profile_text(
            callback.from_user.id
        ),
        reply_markup=builder.as_markup()
    )


@dp.message(F.text == "👤 Профиль")
async def profile_message(
    message: Message
):

    ensure_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    await message.answer(
        profile_text(
            message.from_user.id
        )
    )


# ============================================================
# CONNECTION
# ============================================================

CONNECT_TEXT = """
<b>🔗 Подключение</b>

1. Откройте настройки Telegram.
2. Откройте свой профиль.
3. Нажмите <b>«Изменить»</b>.
4. Откройте <b>«Автоматизация чатов»</b>.
5. Найдите <code>@SpyNeScamBot</code>.
6. Добавьте бота и выдайте необходимые разрешения.

После этого мониторинг начнёт работать автоматически.
"""


def connect_keyboard():

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="⚙️ Открыть настройки",
            url="tg://settings/edit"
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="🤖 Открыть бота",
            url="https://t.me/SpyNeScamBot"
        )
    )

    return builder.as_markup()


@dp.callback_query(F.data == "connect")
async def connect_callback(
    callback: CallbackQuery
):

    await callback.answer()

    await callback.message.answer(
        CONNECT_TEXT,
        reply_markup=connect_keyboard()
    )


@dp.message(F.text == "🔗 Подключение")
async def connect_message(
    message: Message
):

    await message.answer(
        CONNECT_TEXT,
        reply_markup=connect_keyboard()
    )


# ============================================================
# PREMIUM MENU
# ============================================================

def premium_text(user_id: int) -> str:

    if has_discount(user_id):

        return """
<b>⭐ Premium</b>

Расширенный доступ к мониторингу.

🏷 <b>Скидка 10% активна</b>

Выберите период:
"""

    return """
<b>⭐ Premium</b>

Расширенный доступ к мониторингу.

Выберите период:
"""


def premium_keyboard():

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="1 день  ·  ⭐ 5",
            callback_data="buy:day"
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="1 неделя  ·  ⭐ 25",
            callback_data="buy:week"
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="1 месяц  ·  ⭐ 67",
            callback_data="buy:month"
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="6 месяцев  ·  ⭐ 360",
            callback_data="buy:6months"
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="1 год  ·  ⭐ 550",
            callback_data="buy:year"
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="🎟 Промокод",
            callback_data="promo"
        )
    )

    return builder.as_markup()


@dp.callback_query(F.data == "premium")
async def premium_callback(
    callback: CallbackQuery
):

    ensure_user(
        callback.from_user.id,
        callback.from_user.username,
        callback.from_user.first_name
    )

    await callback.answer()

    await callback.message.answer(
        premium_text(
            callback.from_user.id
        ),
        reply_markup=premium_keyboard()
    )


@dp.message(F.text == "⭐ Premium")
async def premium_message(
    message: Message
):

    ensure_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    await message.answer(
        premium_text(
            message.from_user.id
        ),
        reply_markup=premium_keyboard()
    )


# ============================================================
# PAYMENT
# ============================================================

def payment_keyboard(price: int):

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text=f"💳 Оплатить ⭐{price}",
            pay=True
        )
    )

    return builder.as_markup()


@dp.callback_query(F.data.startswith("buy:"))
async def buy_plan(
    callback: CallbackQuery
):

    plan_key = callback.data.split(
        ":",
        1
    )[1]

    if plan_key not in PLANS:

        await callback.answer(
            "План не найден.",
            show_alert=True
        )

        return

    ensure_user(
        callback.from_user.id,
        callback.from_user.username,
        callback.from_user.first_name
    )

    plan = PLANS[plan_key]

    price = discounted_price(
        callback.from_user.id,
        plan["stars"]
    )

    payload = (
        f"premium:"
        f"{plan_key}:"
        f"{callback.from_user.id}:"
        f"{uuid.uuid4().hex}"
    )

    await callback.answer()

    try:

        await bot.send_invoice(
            chat_id=callback.from_user.id,
            title=f"Premium — {plan['name']}",
            description=(
                f"Premium на {plan['name']}."
                + (
                    " Применена скидка 10%."
                    if price != plan["stars"]
                    else ""
                )
            ),
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=[
                LabeledPrice(
                    label=f"Premium — {plan['name']}",
                    amount=price
                )
            ],
            reply_markup=payment_keyboard(price)
        )

    except Exception as e:

        print(
            "SEND INVOICE ERROR:",
            repr(e)
        )

        await callback.message.answer(
            "❌ Не удалось создать счёт.\n"
            "Попробуйте ещё раз."
        )


@dp.pre_checkout_query()
async def pre_checkout_handler(
    pre_checkout_query
):

    try:

        payload = (
            pre_checkout_query.invoice_payload
        )

        parts = payload.split(":")

        if len(parts) != 4:

            await pre_checkout_query.answer(
                ok=False,
                error_message="Некорректный счёт."
            )

            return

        prefix = parts[0]
        plan_key = parts[1]
        user_id = int(parts[2])

        if prefix != "premium":

            await pre_checkout_query.answer(
                ok=False,
                error_message="Некорректный платёж."
            )

            return

        if user_id != pre_checkout_query.from_user.id:

            await pre_checkout_query.answer(
                ok=False,
                error_message="Платёж принадлежит другому пользователю."
            )

            return

        if plan_key not in PLANS:

            await pre_checkout_query.answer(
                ok=False,
                error_message="План больше недоступен."
            )

            return

        expected_price = discounted_price(
            user_id,
            PLANS[plan_key]["stars"]
        )

        if (
            pre_checkout_query.currency != "XTR"
            or
            pre_checkout_query.total_amount != expected_price
        ):

            await pre_checkout_query.answer(
                ok=False,
                error_message="Сумма платежа изменилась."
            )

            return

        await pre_checkout_query.answer(
            ok=True
        )

    except Exception as e:

        print(
            "PRECHECKOUT ERROR:",
            repr(e)
        )

        try:

            await pre_checkout_query.answer(
                ok=False,
                error_message="Ошибка проверки платежа."
            )

        except Exception:
            pass


@dp.message(F.successful_payment)
async def successful_payment_handler(
    message: Message
):

    payment = message.successful_payment

    if not payment:
        return

    ensure_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    payload = payment.invoice_payload

    parts = payload.split(":")

    if len(parts) != 4:
        return

    if parts[0] != "premium":
        return

    plan_key = parts[1]

    try:
        user_id = int(parts[2])
    except ValueError:
        return

    if user_id != message.from_user.id:
        return

    if plan_key not in PLANS:
        return

    charge_id = (
        payment.telegram_payment_charge_id
    )

    existing = db.execute("""
        SELECT id
        FROM payments
        WHERE telegram_payment_charge_id = ?
        LIMIT 1
    """, (
        charge_id,
    )).fetchone()

    if existing:
        return

    stars_paid = payment.total_amount

    plan = PLANS[plan_key]

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
        stars_paid,
        charge_id,
        now_ts()
    ))

    db.execute("""
        UPDATE users
        SET purchases_count = purchases_count + 1,
            stars_spent = stars_spent + ?,
            updated_at = ?
        WHERE user_id = ?
    """, (
        stars_paid,
        now_ts(),
        user_id
    ))

    db.commit()

    activate_premium_days(
        user_id,
        plan["days"]
    )

    user = get_user(user_id)

    await message.answer(
        f"""
<b>✅ Оплата прошла успешно</b>

⭐ Получено: <b>{stars_paid}</b>
⭐ Premium: <b>{plan["name"]}</b>

📅 Действует до:
<b>{format_datetime(user["premium_until"])}</b>
""",
        reply_markup=bottom_menu()
    )


# ============================================================
# PROMO
# ============================================================

def promo_keyboard():

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="🎟 Ввести промокод",
            callback_data="promo_enter"
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="← Назад",
            callback_data="premium"
        )
    )

    return builder.as_markup()


@dp.callback_query(F.data == "promo")
async def promo_callback(
    callback: CallbackQuery
):

    await callback.answer()

    await callback.message.answer(
        """
<b>🎟 Промокод</b>

Введите промокод следующим сообщением.
""",
        reply_markup=promo_keyboard()
    )


@dp.callback_query(F.data == "promo_enter")
async def promo_enter_callback(
    callback: CallbackQuery,
    state: FSMContext
):

    await callback.answer()

    await state.clear()

    await state.set_state(
        PromoStates.waiting_code
    )

    await callback.message.answer(
        "Введите промокод:"
    )


@dp.message(
    PromoStates.waiting_code,
    F.text
)
async def promo_code_handler(
    message: Message,
    state: FSMContext
):

    data = await state.get_data()

    # ========================================================
    # ADMIN CREATE
    # ========================================================

    if data.get("admin_create"):

        if not is_admin(
            message.from_user.id
        ):

            await state.clear()
            return

        code = message.text.strip().upper()

        if not code:

            await message.answer(
                "❌ Промокод не может быть пустым."
            )

            return

        if len(code) > 32:

            await message.answer(
                "❌ Максимальная длина — 32 символа."
            )

            return

        if code in LEGACY_PROMOS:

            await message.answer(
                "❌ Это системный промокод.\n\n"
                "Придумайте другое название."
            )

            return

        exists = db.execute("""
            SELECT code
            FROM promo_codes
            WHERE code = ?
            LIMIT 1
        """, (
            code,
        )).fetchone()

        if exists:

            await message.answer(
                "❌ Такой промокод уже существует.\n\n"
                "Введите другое название:"
            )

            return

        await state.update_data(
            promo_code=code
        )

        await state.set_state(
            PromoStates.waiting_type
        )

        builder = InlineKeyboardBuilder()

        builder.row(
            InlineKeyboardButton(
                text="🎁 Бесплатный Premium",
                callback_data="admin:promotype:free"
            )
        )

        builder.row(
            InlineKeyboardButton(
                text="🏷 Скидка",
                callback_data="admin:promotype:discount"
            )
        )

        await message.answer(
            f"""
<b>🎟 Создание промокода</b>

Код:
<code>{code}</code>

Выберите тип:
""",
            reply_markup=builder.as_markup()
        )

        return

    # ========================================================
    # USER PROMO
    # ========================================================

    code = message.text.strip().upper()

    ensure_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    used = db.execute("""
        SELECT id
        FROM promo_uses
        WHERE user_id = ?
          AND promo_code = ?
        LIMIT 1
    """, (
        message.from_user.id,
        code
    )).fetchone()

    if used:

        await state.clear()

        await message.answer(
            "❌ Вы уже использовали этот промокод."
        )

        return

    # ========================================================
    # N1
    # ========================================================

    if code == "N1":

        async with db_lock:

            db.execute(
                "BEGIN IMMEDIATE"
            )

            try:

                used_count = db.execute("""
                    SELECT COUNT(*)
                    FROM promo_uses
                    WHERE promo_code = ?
                """, (
                    code,
                )).fetchone()[0]

                if used_count >= 25:

                    db.rollback()

                    await state.clear()

                    await message.answer(
                        "❌ Промокод закончился."
                    )

                    return

                db.execute("""
                    INSERT INTO promo_uses (
                        user_id,
                        promo_code,
                        created_at
                    )
                    VALUES (?, ?, ?)
                """, (
                    message.from_user.id,
                    code,
                    now_ts()
                ))

                db.commit()

            except Exception:

                db.rollback()
                raise

        activate_premium_days(
            message.from_user.id,
            7
        )

        await state.clear()

        user = get_user(
            message.from_user.id
        )

        await message.answer(
            f"""
<b>🎉 Промокод активирован</b>

⭐ Premium: <b>7 дней</b>

📅 До:
<b>{format_datetime(user["premium_until"])}</b>
"""
        )

        return

    # ========================================================
    # DAVE100
    # ========================================================

    if code == "DAVE100":

        db.execute("""
            INSERT INTO promo_uses (
                user_id,
                promo_code,
                created_at
            )
            VALUES (?, ?, ?)
        """, (
            message.from_user.id,
            code,
            now_ts()
        ))

        db.commit()

        activate_premium_forever(
            message.from_user.id
        )

        await state.clear()

        await message.answer(
            """
<b>🎉 Промокод активирован</b>

⭐ Premium: <b>навсегда</b>
"""
        )

        return

    # ========================================================
    # MET200$
    # ========================================================

    if code == "MET200$":

        db.execute("""
            INSERT INTO promo_uses (
                user_id,
                promo_code,
                created_at
            )
            VALUES (?, ?, ?)
        """, (
            message.from_user.id,
            code,
            now_ts()
        ))

        db.commit()

        await state.clear()

        await message.answer(
            """
<b>🎉 Промокод активирован</b>

🏷 Скидка: <b>10%</b>

Скидка будет применяться
к покупкам Premium.
"""
        )

        return

    # ========================================================
    # CUSTOM PROMO
    # ========================================================

    custom = db.execute("""
        SELECT *
        FROM promo_codes
        WHERE code = ?
          AND is_active = 1
        LIMIT 1
    """, (
        code,
    )).fetchone()

    if not custom:

        await state.clear()

        await message.answer(
            "❌ Промокод не найден."
        )

        return

    if (
        custom["max_uses"] is not None
        and custom["uses"] >= custom["max_uses"]
    ):

        await state.clear()

        await message.answer(
            "❌ Промокод закончился."
        )

        return

    db.execute("""
        INSERT INTO promo_uses (
            user_id,
            promo_code,
            created_at
        )
        VALUES (?, ?, ?)
    """, (
        message.from_user.id,
        code,
        now_ts()
    ))

    db.execute("""
        UPDATE promo_codes
        SET uses = uses + 1
        WHERE code = ?
    """, (
        code,
    ))

    db.commit()

    await state.clear()

    if custom["promo_type"] == "free":

        days = custom["premium_days"]

        activate_premium_days(
            message.from_user.id,
            days
        )

        user = get_user(
            message.from_user.id
        )

        await message.answer(
            f"""
<b>🎉 Промокод активирован</b>

⭐ Premium: <b>{days} дней</b>

📅 До:
<b>{format_datetime(user["premium_until"])}</b>
"""
        )

        return

    if custom["promo_type"] == "discount":

        await message.answer(
            f"""
<b>🎉 Промокод активирован</b>

🏷 Скидка: <b>{custom["discount_percent"]}%</b>

Выберите Premium:
""",
            reply_markup=premium_keyboard()
        )


# ============================================================
# ADMIN
# ============================================================

def admin_text() -> str:

    users_count = db.execute("""
        SELECT COUNT(*)
        FROM users
    """).fetchone()[0]

    premium_count = db.execute("""
        SELECT COUNT(*)
        FROM users
        WHERE premium_forever = 1
           OR premium_until > ?
    """, (
        now_ts(),
    )).fetchone()[0]

    purchases = db.execute("""
        SELECT COUNT(*)
        FROM payments
    """).fetchone()[0]

    stars = db.execute("""
        SELECT COALESCE(SUM(stars), 0)
        FROM payments
    """).fetchone()[0]

    return f"""
<b>🔐 Admin</b>

👥 Пользователей: <b>{users_count}</b>
⭐ Premium: <b>{premium_count}</b>
💳 Покупок: <b>{purchases}</b>
💰 Stars: <b>{stars}</b>

Выберите действие:
"""


def admin_keyboard():

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="👥 Пользователи",
            callback_data="admin:users"
        ),
        InlineKeyboardButton(
            text="💳 Покупки",
            callback_data="admin:payments"
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="➕ Выдать Premium",
            callback_data="admin:add"
        ),
        InlineKeyboardButton(
            text="➖ Снять Premium",
            callback_data="admin:remove"
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="🎟 Промокоды",
            callback_data="admin:promos"
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="📊 Статистика",
            callback_data="admin:stats"
        )
    )

    return builder.as_markup()


# ============================================================
# ADMIN ENTRY
# ============================================================

@dp.message(
    F.text.func(
        lambda text:
        text is not None
        and text.strip().lower() == "dave200$"
    )
)
async def admin_entry(
    message: Message
):

    if not is_admin(
        message.from_user.id
    ):
        return

    await message.answer(
        admin_text(),
        reply_markup=admin_keyboard()
    )


@dp.message(Command("Dave200$"))
async def admin_entry_command(
    message: Message
):

    if not is_admin(
        message.from_user.id
    ):
        return

    await message.answer(
        admin_text(),
        reply_markup=admin_keyboard()
    )


# ============================================================
# ADMIN HOME
# ============================================================

@dp.callback_query(F.data == "admin:home")
async def admin_home(
    callback: CallbackQuery
):

    if not is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    await callback.answer()

    await callback.message.answer(
        admin_text(),
        reply_markup=admin_keyboard()
    )


# ============================================================
# ADMIN USERS
# ============================================================

@dp.callback_query(F.data == "admin:users")
async def admin_users(
    callback: CallbackQuery
):

    if not is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    await callback.answer()

    rows = db.execute("""
        SELECT *
        FROM users
        ORDER BY created_at DESC
        LIMIT 30
    """).fetchall()

    if not rows:

        text = (
            "<b>👥 Пользователи</b>\n\n"
            "Пользователей пока нет."
        )

    else:

        lines = [
            "<b>👥 Пользователи</b>",
            ""
        ]

        for user in rows:

            if user["premium_forever"]:

                premium = "♾"

            elif (
                user["premium_until"]
                and
                user["premium_until"] > now_ts()
            ):

                premium = "🟢"

            else:

                premium = "⚪"

            lines.append(
                f"{premium} "
                f"<code>{user['user_id']}</code> "
                f"{normalize_username(user['username'])}"
            )

        text = "\n".join(lines)

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="🔎 Найти пользователя",
            callback_data="admin:find"
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="← Назад",
            callback_data="admin:home"
        )
    )

    await callback.message.answer(
        text,
        reply_markup=builder.as_markup()
    )


# ============================================================
# ADMIN FIND
# ============================================================

@dp.callback_query(F.data == "admin:find")
async def admin_find(
    callback: CallbackQuery,
    state: FSMContext
):

    if not is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    await callback.answer()

    await state.set_state(
        AdminSearchStates.waiting_user_id
    )

    await callback.message.answer(
        "Введите Telegram ID пользователя:"
    )


@dp.message(
    AdminSearchStates.waiting_user_id
)
async def admin_find_user(
    message: Message,
    state: FSMContext
):

    if not is_admin(
        message.from_user.id
    ):

        await state.clear()
        return

    try:

        user_id = int(
            message.text.strip()
        )

    except ValueError:

        await message.answer(
            "❌ ID должен состоять из цифр."
        )

        return

    await state.clear()

    if not get_user(user_id):

        await message.answer(
            "❌ Пользователь не найден."
        )

        return

    await send_admin_user_card(
        message,
        user_id
    )


async def send_admin_user_card(
    message: Message,
    user_id: int
):

    user = get_user(user_id)

    if not user:

        await message.answer(
            "❌ Пользователь не найден."
        )

        return

    if user["premium_forever"]:

        status = "♾ Навсегда"

    elif get_premium_active(user_id):

        status = (
            "🟢 "
            + format_datetime(
                user["premium_until"]
            )
        )

    else:

        status = "⚪ Нет Premium"

    payments_count = db.execute("""
        SELECT COUNT(*)
        FROM payments
        WHERE user_id = ?
    """, (
        user_id,
    )).fetchone()[0]

    text = f"""
<b>👤 Пользователь</b>

ID: <code>{user_id}</code>
Username: {normalize_username(user["username"])}
Имя: {user["first_name"] or "—"}

<b>Premium</b>
{status}

<b>Покупки</b>
Покупок: {payments_count}
Stars: ⭐ {user["stars_spent"]}

Создан:
{format_datetime(user["created_at"])}
"""

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="➕ Выдать",
            callback_data=f"admin:adduser:{user_id}"
        ),
        InlineKeyboardButton(
            text="➖ Снять",
            callback_data=f"admin:removeuser:{user_id}"
        )
    )

    await message.answer(
        text,
        reply_markup=builder.as_markup()
    )


# ============================================================
# ADMIN ADD
# ============================================================

@dp.callback_query(F.data == "admin:add")
async def admin_add(
    callback: CallbackQuery,
    state: FSMContext
):

    if not is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    await callback.answer()

    await state.set_state(
        AdminStates.waiting_user_id
    )

    await callback.message.answer(
        """
<b>➕ Выдать Premium</b>

Введите Telegram ID:
"""
    )


@dp.callback_query(
    F.data.startswith("admin:adduser:")
)
async def admin_add_existing(
    callback: CallbackQuery,
    state: FSMContext
):

    if not is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    user_id = int(
        callback.data.split(":")[2]
    )

    await callback.answer()

    if not get_user(user_id):

        await callback.message.answer(
            "❌ Пользователь не найден."
        )

        return

    await state.set_state(
        AdminStates.waiting_duration
    )

    await state.update_data(
        target_user_id=user_id
    )

    await callback.message.answer(
        """
<b>➕ Выдать Premium</b>

Введите срок:

<code>7 дней</code>
<code>30 дней</code>
<code>12 часов</code>
<code>2 часа</code>
"""
    )


@dp.message(
    AdminStates.waiting_user_id
)
async def admin_add_user_id(
    message: Message,
    state: FSMContext
):

    if not is_admin(
        message.from_user.id
    ):

        await state.clear()
        return

    try:

        user_id = int(
            message.text.strip()
        )

    except ValueError:

        await message.answer(
            "❌ Неверный Telegram ID."
        )

        return

    ensure_user(
        user_id,
        None,
        None
    )

    await state.update_data(
        target_user_id=user_id
    )

    await state.set_state(
        AdminStates.waiting_duration
    )

    await message.answer(
        """
<b>Срок Premium</b>

Например:

<code>7 дней</code>
<code>30 дней</code>
<code>12 часов</code>
"""
    )


def parse_duration(text: str):

    text = text.lower().strip()

    parts = text.split()

    if len(parts) < 2:
        return None

    try:

        value = int(parts[0])

    except ValueError:

        return None

    unit = parts[1]

    if unit.startswith("д"):
        return value * 86400

    if unit.startswith("час"):
        return value * 3600

    if unit.startswith("мин"):
        return value * 60

    return None


@dp.message(
    AdminStates.waiting_duration
)
async def admin_add_duration(
    message: Message,
    state: FSMContext
):

    if not is_admin(
        message.from_user.id
    ):

        await state.clear()
        return

    seconds = parse_duration(
        message.text
    )

    if not seconds or seconds <= 0:

        await message.answer(
            "❌ Не удалось понять срок.\n\n"
            "Например: <code>7 дней</code>"
        )

        return

    data = await state.get_data()

    user_id = data.get(
        "target_user_id"
    )

    if not user_id:

        await state.clear()
        return

    user = get_user(user_id)

    if not user:

        await state.clear()

        await message.answer(
            "❌ Пользователь не найден."
        )

        return

    if user["premium_forever"]:

        await state.clear()

        await message.answer(
            "ℹ️ У пользователя уже Premium навсегда."
        )

        return

    current = user["premium_until"] or 0

    new_until = (
        max(
            current,
            now_ts()
        )
        + seconds
    )

    db.execute("""
        UPDATE users
        SET premium_until = ?,
            updated_at = ?
        WHERE user_id = ?
    """, (
        new_until,
        now_ts(),
        user_id
    ))

    db.execute("""
        INSERT INTO admin_actions (
            admin_id,
            target_user_id,
            action,
            value,
            created_at
        )
        VALUES (?, ?, ?, ?, ?)
    """, (
        message.from_user.id,
        user_id,
        "add_premium",
        message.text,
        now_ts()
    ))

    db.commit()

    await state.clear()

    await message.answer(
        f"""
<b>✅ Premium выдан</b>

ID:
<code>{user_id}</code>

Добавлено:
<b>{message.text}</b>

Действует до:
<b>{format_datetime(new_until)}</b>
"""
    )

    try:

        await bot.send_message(
            user_id,
            f"""
<b>⭐ Premium активирован</b>

Вам добавили:
<b>{message.text}</b>

Действует до:
<b>{format_datetime(new_until)}</b>
"""
        )

    except Exception:
        pass


# ============================================================
# ADMIN REMOVE
# ============================================================

@dp.callback_query(F.data == "admin:remove")
async def admin_remove(
    callback: CallbackQuery,
    state: FSMContext
):

    if not is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    await callback.answer()

    await state.set_state(
        AdminStates.waiting_remove_user_id
    )

    await callback.message.answer(
        """
<b>➖ Снять Premium</b>

Введите Telegram ID:
"""
    )


@dp.callback_query(
    F.data.startswith("admin:removeuser:")
)
async def admin_remove_existing(
    callback: CallbackQuery
):

    if not is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    user_id = int(
        callback.data.split(":")[2]
    )

    await callback.answer()

    if not get_user(user_id):

        await callback.message.answer(
            "❌ Пользователь не найден."
        )

        return

    remove_premium(
        user_id
    )

    db.execute("""
        INSERT INTO admin_actions (
            admin_id,
            target_user_id,
            action,
            value,
            created_at
        )
        VALUES (?, ?, ?, ?, ?)
    """, (
        callback.from_user.id,
        user_id,
        "remove_premium",
        "button",
        now_ts()
    ))

    db.commit()

    await callback.message.answer(
        f"""
<b>✅ Premium снят</b>

ID:
<code>{user_id}</code>
"""
    )

    try:

        await bot.send_message(
            user_id,
            "⚪ Ваш Premium был отключён."
        )

    except Exception:
        pass


@dp.message(
    AdminStates.waiting_remove_user_id
)
async def admin_remove_user_id(
    message: Message,
    state: FSMContext
):

    if not is_admin(
        message.from_user.id
    ):

        await state.clear()
        return

    try:

        user_id = int(
            message.text.strip()
        )

    except ValueError:

        await message.answer(
            "❌ Неверный Telegram ID."
        )

        return

    if not get_user(user_id):

        await message.answer(
            "❌ Пользователь не найден."
        )

        return

    remove_premium(
        user_id
    )

    db.execute("""
        INSERT INTO admin_actions (
            admin_id,
            target_user_id,
            action,
            value,
            created_at
        )
        VALUES (?, ?, ?, ?, ?)
    """, (
        message.from_user.id,
        user_id,
        "remove_premium",
        "manual",
        now_ts()
    ))

    db.commit()

    await state.clear()

    await message.answer(
        f"""
<b>✅ Premium снят</b>

ID:
<code>{user_id}</code>
"""
    )

    try:

        await bot.send_message(
            user_id,
            "⚪ Ваш Premium был отключён."
        )

    except Exception:
        pass


# ============================================================
# ADMIN PAYMENTS
# ============================================================

@dp.callback_query(F.data == "admin:payments")
async def admin_payments(
    callback: CallbackQuery
):

    if not is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    await callback.answer()

    rows = db.execute("""
        SELECT
            p.*,
            u.username,
            u.first_name
        FROM payments p
        LEFT JOIN users u
            ON u.user_id = p.user_id
        ORDER BY p.created_at DESC
        LIMIT 30
    """).fetchall()

    if not rows:

        text = (
            "<b>💳 Покупки</b>\n\n"
            "Покупок пока нет."
        )

    else:

        lines = [
            "<b>💳 Последние покупки</b>",
            ""
        ]

        for row in rows:

            plan = PLANS.get(
                row["plan"],
                {}
            )

            plan_name = plan.get(
                "name",
                row["plan"] or "—"
            )

            lines.append(
                f"⭐ <b>{row['stars']}</b> · "
                f"{plan_name}\n"
                f"ID: <code>{row['user_id']}</code> · "
                f"{normalize_username(row['username'])}\n"
                f"{format_datetime(row['created_at'])}"
            )

        text = "\n\n".join(lines)

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="← Назад",
            callback_data="admin:home"
        )
    )

    await callback.message.answer(
        text,
        reply_markup=builder.as_markup()
    )


# ============================================================
# ADMIN STATS
# ============================================================

@dp.callback_query(F.data == "admin:stats")
async def admin_stats(
    callback: CallbackQuery
):

    if not is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    await callback.answer()

    users = db.execute("""
        SELECT COUNT(*)
        FROM users
    """).fetchone()[0]

    active = db.execute("""
        SELECT COUNT(*)
        FROM users
        WHERE premium_forever = 1
           OR premium_until > ?
    """, (
        now_ts(),
    )).fetchone()[0]

    purchases = db.execute("""
        SELECT COUNT(*)
        FROM payments
    """).fetchone()[0]

    stars = db.execute("""
        SELECT COALESCE(SUM(stars), 0)
        FROM payments
    """).fetchone()[0]

    promo_uses = db.execute("""
        SELECT COUNT(*)
        FROM promo_uses
    """).fetchone()[0]

    connections = db.execute("""
        SELECT COUNT(*)
        FROM business_connections
        WHERE is_enabled = 1
    """).fetchone()[0]

    text = f"""
<b>📊 Статистика</b>

👥 Пользователи
<b>{users}</b>

⭐ Активный Premium
<b>{active}</b>

💳 Покупки
<b>{purchases}</b>

💰 Получено Stars
<b>⭐ {stars}</b>

🎟 Активации промокодов
<b>{promo_uses}</b>

🔗 Business-подключения
<b>{connections}</b>
"""

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="← Назад",
            callback_data="admin:home"
        )
    )

    await callback.message.answer(
        text,
        reply_markup=builder.as_markup()
    )


# ============================================================
# ADMIN PROMOS
# ============================================================

@dp.callback_query(F.data == "admin:promos")
async def admin_promos(
    callback: CallbackQuery
):

    if not is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    await callback.answer()

    rows = db.execute("""
        SELECT *
        FROM promo_codes
        ORDER BY created_at DESC
    """).fetchall()

    lines = [
        "<b>🎟 Промокоды</b>",
        ""
    ]

    if not rows:

        lines.append(
            "Пользовательских промокодов пока нет."
        )

    else:

        for row in rows:

            if row["promo_type"] == "free":

                details = (
                    f"🎁 {row['premium_days']} дней"
                )

            else:

                details = (
                    f"🏷 {row['discount_percent']}%"
                )

            limit = (
                "∞"
                if row["max_uses"] is None
                else str(row["max_uses"])
            )

            status = (
                "🟢"
                if row["is_active"]
                else "⚪"
            )

            lines.append(
                f"{status} "
                f"<code>{row['code']}</code>\n"
                f"{details} · "
                f"{row['uses']} / {limit}"
            )

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="➕ Создать промокод",
            callback_data="admin:createpromo"
        )
    )

    if rows:

        builder.row(
            InlineKeyboardButton(
                text="🔴 Отключить промокод",
                callback_data="admin:disablepromo"
            )
        )

    builder.row(
        InlineKeyboardButton(
            text="← Назад",
            callback_data="admin:home"
        )
    )

    await callback.message.answer(
        "\n\n".join(lines),
        reply_markup=builder.as_markup()
    )


# ============================================================
# ADMIN CREATE PROMO
# ============================================================

@dp.callback_query(
    F.data == "admin:createpromo"
)
async def admin_create_promo(
    callback: CallbackQuery,
    state: FSMContext
):

    if not is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    await callback.answer()

    await state.clear()

    await state.update_data(
        admin_create=True
    )

    await state.set_state(
        PromoStates.waiting_code
    )

    await callback.message.answer(
        """
<b>🎟 Новый промокод</b>

Введите название нового промокода.

Например:

<code>SUMMER50</code>
"""
    )


@dp.callback_query(
    F.data.startswith("admin:promotype:")
)
async def admin_promo_type(
    callback: CallbackQuery,
    state: FSMContext
):

    if not is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    promo_type = callback.data.split(
        ":"
    )[2]

    await callback.answer()

    data = await state.get_data()

    if not data.get("admin_create"):

        await callback.message.answer(
            "❌ Сессия создания промокода истекла."
        )

        return

    await state.update_data(
        promo_type=promo_type
    )

    if promo_type == "discount":

        await state.set_state(
            PromoStates.waiting_value
        )

        await callback.message.answer(
            """
<b>🏷 Скидка</b>

Введите размер скидки в процентах.

Например:

<code>25</code>
"""
        )

    else:

        await state.set_state(
            PromoStates.waiting_days
        )

        await callback.message.answer(
            """
<b>🎁 Бесплатный Premium</b>

Введите срок Premium в днях.

Например:

<code>30</code>
"""
        )


@dp.message(
    PromoStates.waiting_value
)
async def admin_promo_discount(
    message: Message,
    state: FSMContext
):

    if not is_admin(
        message.from_user.id
    ):

        await state.clear()
        return

    try:

        discount = int(
            message.text.strip()
        )

    except ValueError:

        await message.answer(
            "❌ Введите число."
        )

        return

    if discount < 1 or discount > 100:

        await message.answer(
            "❌ Процент должен быть от 1 до 100."
        )

        return

    await state.update_data(
        discount=discount
    )

    await state.set_state(
        PromoStates.waiting_days
    )

    await message.answer(
        """
<b>⏳ Срок Premium</b>

Введите количество дней.

Например:

<code>30</code>
"""
    )


@dp.message(
    PromoStates.waiting_days
)
async def admin_promo_days(
    message: Message,
    state: FSMContext
):

    if not is_admin(
        message.from_user.id
    ):

        await state.clear()
        return

    try:

        days = int(
            message.text.strip()
        )

    except ValueError:

        await message.answer(
            "❌ Введите количество дней."
        )

        return

    if days <= 0 or days > 3650:

        await message.answer(
            "❌ Срок должен быть от 1 до 3650 дней."
        )

        return

    await state.update_data(
        days=days
    )

    await state.set_state(
        PromoStates.waiting_max_uses
    )

    await message.answer(
        """
<b>🔢 Количество активаций</b>

Введите количество использований.

Например:

<code>100</code>

Или:

<code>0</code>

если количество не ограничено.
"""
    )


@dp.message(
    PromoStates.waiting_max_uses
)
async def admin_promo_max_uses(
    message: Message,
    state: FSMContext
):

    if not is_admin(
        message.from_user.id
    ):

        await state.clear()
        return

    try:

        max_uses = int(
            message.text.strip()
        )

    except ValueError:

        await message.answer(
            "❌ Введите число."
        )

        return

    if max_uses < 0:

        await message.answer(
            "❌ Число не может быть отрицательным."
        )

        return

    data = await state.get_data()

    code = data.get(
        "promo_code"
    )

    promo_type = data.get(
        "promo_type"
    )

    days = data.get(
        "days"
    )

    discount = data.get(
        "discount",
        0
    )

    if not code or not promo_type or not days:

        await state.clear()

        await message.answer(
            "❌ Ошибка создания промокода."
        )

        return

    max_uses_db = (
        None
        if max_uses == 0
        else max_uses
    )

    db.execute("""
        INSERT INTO promo_codes (
            code,
            promo_type,
            discount_percent,
            premium_days,
            max_uses,
            uses,
            is_active,
            created_at,
            created_by
        )
        VALUES (?, ?, ?, ?, ?, 0, 1, ?, ?)
    """, (
        code,
        promo_type,
        discount,
        days,
        max_uses_db,
        now_ts(),
        message.from_user.id
    ))

    db.commit()

    await state.clear()

    if promo_type == "free":

        type_text = (
            f"🎁 Бесплатный Premium · {days} дней"
        )

    else:

        type_text = (
            f"🏷 Скидка · {discount}%"
        )

    limit_text = (
        "∞"
        if max_uses_db is None
        else str(max_uses_db)
    )

    await message.answer(
        f"""
<b>✅ Промокод создан</b>

Код:
<code>{code}</code>

Тип:
{type_text}

Активаций:
<b>0 / {limit_text}</b>

Теперь этот промокод доступен
всем пользователям бота.
"""
    )


# ============================================================
# ADMIN DISABLE PROMO
# ============================================================

@dp.callback_query(
    F.data == "admin:disablepromo"
)
async def admin_disable_promo(
    callback: CallbackQuery
):

    if not is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    await callback.answer()

    rows = db.execute("""
        SELECT code, uses, max_uses
        FROM promo_codes
        WHERE is_active = 1
        ORDER BY created_at DESC
    """).fetchall()

    if not rows:

        await callback.message.answer(
            "Нет активных пользовательских промокодов."
        )

        return

    builder = InlineKeyboardBuilder()

    for row in rows:

        builder.row(
            InlineKeyboardButton(
                text=f"🔴 {row['code']}",
                callback_data=(
                    f"admin:disable:{row['code']}"
                )
            )
        )

    builder.row(
        InlineKeyboardButton(
            text="← Назад",
            callback_data="admin:promos"
        )
    )

    await callback.message.answer(
        "<b>Выберите промокод для отключения:</b>",
        reply_markup=builder.as_markup()
    )


@dp.callback_query(
    F.data.startswith("admin:disable:")
)
async def admin_disable_selected(
    callback: CallbackQuery
):

    if not is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    code = callback.data.split(
        "admin:disable:",
        1
    )[1]

    db.execute("""
        UPDATE promo_codes
        SET is_active = 0
        WHERE code = ?
    """, (
        code,
    ))

    db.commit()

    await callback.answer(
        "Промокод отключён."
    )

    await callback.message.answer(
        f"""
<b>🔴 Промокод отключён</b>

<code>{code}</code>
"""
    )


# ============================================================
# ============================================================
# BUSINESS CONNECTION
# ============================================================
# ============================================================

@dp.business_connection()
async def business_connection_handler(
    event
):

    ts = now_ts()

    try:

        db.execute("""
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
        """, (
            event.id,
            event.user.id,
            int(event.can_reply),
            ts,
            ts
        ))

        db.commit()

        print(
            f"[BUSINESS CONNECTION] "
            f"id={event.id} "
            f"user={event.user.id} "
            f"can_reply={event.can_reply}"
        )

    except Exception as e:

        print(
            "[BUSINESS CONNECTION ERROR]",
            repr(e)
        )


# ============================================================
# SAVE BUSINESS MESSAGE
# ============================================================

def save_business_message(
    message: Message
):
    connection_id = (
        message.business_connection_id
    )

    if not connection_id:
        return

    user = message.from_user

    text = message.text
    caption = message.caption

    photo_file_id = None
    photo_width = None
    photo_height = None
    photo_spoiler = 0

    message_type = "text"

    if message.photo:

        photo = message.photo[-1]

        photo_file_id = photo.file_id
        photo_width = photo.width
        photo_height = photo.height

        photo_spoiler = int(
            bool(
                getattr(
                    message,
                    "has_media_spoiler",
                    False
                )
            )
        )

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

    db.execute("""
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
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        ON CONFLICT(
            business_connection_id,
            chat_id,
            message_id
        )
        DO UPDATE SET
            text = excluded.text,
            caption = excluded.caption,
            photo_file_id = excluded.photo_file_id,
            photo_width = excluded.photo_width,
            photo_height = excluded.photo_height,
            message_type = excluded.message_type,
            photo_has_spoiler = excluded.photo_has_spoiler,
            updated_at = excluded.updated_at
    """, (
        connection_id,
        message.chat.id,
        message.message_id,
        user.id if user else None,
        user.username if user else None,
        user.first_name if user else None,
        text,
        caption,
        photo_file_id,
        photo_width,
        photo_height,
        message_type,
        photo_spoiler,
        now_ts(),
        now_ts()
    ))

    db.commit()


# ============================================================
# BUSINESS MESSAGE
# ============================================================

@dp.business_message()
async def business_message_handler(
    message: Message
):

    print(
        f"[BUSINESS MESSAGE] "
        f"connection={message.business_connection_id} "
        f"chat={message.chat.id} "
        f"message={message.message_id}"
    )

    if not message.business_connection_id:
        return

    try:

        save_business_message(
            message
        )

    except Exception as e:

        print(
            "[SAVE BUSINESS MESSAGE ERROR]",
            repr(e)
        )

        return

    connection = db.execute("""
        SELECT user_chat_id
        FROM business_connections
        WHERE business_connection_id = ?
    """, (
        message.business_connection_id,
    )).fetchone()

    if not connection:
        print(
            "[BUSINESS MESSAGE] connection not found"
        )
        return

    owner_id = connection["user_chat_id"]

    if (
        message.from_user
        and
        message.from_user.id == owner_id
    ):
        return

    if message.chat.type != "private":
        return

    username = normalize_username(
        message.from_user.username
        if message.from_user
        else None
    )

    name = (
        message.from_user.first_name
        if message.from_user
        else "Пользователь"
    )

    # ========================================================
    # PHOTO
    # ========================================================

    if message.photo:

        if not get_premium_active(owner_id):

            await bot.send_message(
                owner_id,
                """
<b>🗑 Сообщение удалено</b>

Для просмотра содержимого
нужен Premium.
""",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="⭐ Купить Premium",
                                callback_data="premium"
                            )
                        ]
                    ]
                )
            )

            return

        photo = message.photo[-1]

        caption = (
            message.caption
            or ""
        )

        text = (
            f"<b>📩 Новое сообщение</b>\n\n"
            f"<b>{name}</b> "
            f"{username}\n\n"
            f"{caption}"
        )

        try:

            await bot.send_photo(
                owner_id,
                photo=photo.file_id,
                caption=text
            )

        except Exception as e:

            print(
                "[SEND PHOTO ERROR]",
                repr(e)
            )

        return

    # ========================================================
    # TEXT
    # ========================================================

    if message.text:

        await bot.send_message(
            owner_id,
            f"""
<b>📩 Новое сообщение</b>

<b>{name}</b> {username}

{message.text}
"""
        )


# ============================================================
# EDITED BUSINESS MESSAGE
# ============================================================

@dp.edited_business_message()
async def edited_business_message_handler(
    message: Message
):

    print(
        f"[EDITED BUSINESS MESSAGE] "
        f"connection={message.business_connection_id} "
        f"chat={message.chat.id} "
        f"message={message.message_id}"
    )

    if not message.business_connection_id:
        return

    connection = db.execute("""
        SELECT user_chat_id
        FROM business_connections
        WHERE business_connection_id = ?
    """, (
        message.business_connection_id,
    )).fetchone()

    if not connection:

        print(
            "[EDITED] connection not found"
        )

        return

    owner_id = connection["user_chat_id"]

    # --------------------------------------------------------
    # ВАЖНО:
    # Сначала достаём старую версию.
    # Раньше код сначала перезаписывал её новой.
    # --------------------------------------------------------

    old_message = db.execute("""
        SELECT *
        FROM messages
        WHERE business_connection_id = ?
          AND chat_id = ?
          AND message_id = ?
        LIMIT 1
    """, (
        message.business_connection_id,
        message.chat.id,
        message.message_id
    )).fetchone()

    # Сохраняем новую версию
    try:

        save_business_message(
            message
        )

    except Exception as e:

        print(
            "[EDIT SAVE ERROR]",
            repr(e)
        )

    if (
        message.from_user
        and
        message.from_user.id == owner_id
    ):
        return

    if message.chat.type != "private":
        return

    # Для уведомлений об изменениях нужен Premium
    if not get_premium_active(owner_id):
        return

    sender_name = (
        message.from_user.first_name
        if message.from_user
        else (
            old_message["first_name"]
            if old_message
            else "Пользователь"
        )
    )

    username = normalize_username(
        message.from_user.username
        if message.from_user
        else (
            old_message["username"]
            if old_message
            else None
        )
    )

    # ========================================================
    # TEXT
    # ========================================================

    if message.text:

        old_text = (
            old_message["text"]
            if old_message
            else None
        )

        if old_text and old_text != message.text:

            text = f"""
<b>✏️ Сообщение изменено</b>

<b>{sender_name}</b> {username}

<b>Было:</b>
{old_text}

<b>Стало:</b>
{message.text}
"""

        else:

            text = f"""
<b>✏️ Сообщение изменено</b>

<b>{sender_name}</b> {username}

{message.text}
"""

        try:

            await bot.send_message(
                owner_id,
                text
            )

        except Exception as e:

            print(
                "[EDIT SEND ERROR]",
                repr(e)
            )

        return

    # ========================================================
    # PHOTO / CAPTION
    # ========================================================

    if message.photo:

        old_caption = (
            old_message["caption"]
            if old_message
            else None
        )

        new_caption = (
            message.caption
            or ""
        )

        if old_caption and old_caption != new_caption:

            caption = (
                f"<b>✏️ Фото изменено</b>\n\n"
                f"<b>{sender_name}</b> {username}\n\n"
                f"<b>Было:</b>\n"
                f"{old_caption}\n\n"
                f"<b>Стало:</b>\n"
                f"{new_caption}"
            )

        else:

            caption = (
                f"<b>✏️ Фото изменено</b>\n\n"
                f"<b>{sender_name}</b> {username}\n\n"
                f"{new_caption}"
            )

        try:

            await bot.send_photo(
                owner_id,
                photo=message.photo[-1].file_id,
                caption=caption
            )

        except Exception as e:

            print(
                "[EDIT PHOTO SEND ERROR]",
                repr(e)
            )

        return

    # ========================================================
    # OTHER
    # ========================================================

    try:

        await bot.send_message(
            owner_id,
            f"""
<b>✏️ Сообщение изменено</b>

<b>{sender_name}</b> {username}

Тип: {message.content_type}
"""
        )

    except Exception as e:

        print(
            "[EDIT OTHER SEND ERROR]",
            repr(e)
        )


# ============================================================
# DELETED BUSINESS MESSAGES
# ============================================================

@dp.deleted_business_messages()
async def deleted_business_messages_handler(
    event
):

    print(
        f"[DELETED BUSINESS MESSAGES] "
        f"connection={event.business_connection_id} "
        f"ids={event.message_ids}"
    )

    connection_id = (
        event.business_connection_id
    )

    connection = db.execute("""
        SELECT user_chat_id
        FROM business_connections
        WHERE business_connection_id = ?
    """, (
        connection_id,
    )).fetchone()

    if not connection:

        print(
            "[DELETE] connection not found"
        )

        return

    owner_id = connection["user_chat_id"]

    for message_id in event.message_ids:

        # ====================================================
        # Ищем сохранённое сообщение
        # ====================================================

        saved = db.execute("""
            SELECT *
            FROM messages
            WHERE business_connection_id = ?
              AND message_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
        """, (
            connection_id,
            message_id
        )).fetchone()

        if not saved:

            print(
                f"[DELETE] saved message not found: "
                f"{message_id}"
            )

            # Даже если содержимое не сохранилось,
            # Premium-владельцу всё равно сообщаем
            # об удалении.
            if get_premium_active(owner_id):

                try:

                    await bot.send_message(
                        owner_id,
                        f"""
<b>🗑 Сообщение удалено</b>

ID сообщения:
<code>{message_id}</code>

Содержимое не было сохранено.
"""
                    )

                except Exception as e:

                    print(
                        "[DELETE UNKNOWN SEND ERROR]",
                        repr(e)
                    )

            else:

                try:

                    await bot.send_message(
                        owner_id,
                        """
<b>🗑 Сообщение удалено</b>

Для просмотра содержимого
нужен Premium.
""",
                        reply_markup=InlineKeyboardMarkup(
                            inline_keyboard=[
                                [
                                    InlineKeyboardButton(
                                        text="⭐ Купить Premium",
                                        callback_data="premium"
                                    )
                                ]
                            ]
                        )
                    )

                except Exception as e:

                    print(
                        "[DELETE FREE SEND ERROR]",
                        repr(e)
                    )

            continue

        # ====================================================
        # Не показываем собственные сообщения владельца
        # ====================================================

        if saved["user_id"] == owner_id:
            continue

        # ====================================================
        # NO PREMIUM
        # ====================================================

        if not get_premium_active(owner_id):

            try:

                await bot.send_message(
                    owner_id,
                    """
<b>🗑 Сообщение удалено</b>

Для просмотра содержимого
нужен Premium.
""",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text="⭐ Купить Premium",
                                    callback_data="premium"
                                )
                            ]
                        ]
                    )
                )

            except Exception as e:

                print(
                    "[DELETE FREE SEND ERROR]",
                    repr(e)
                )

            continue

        sender_name = (
            saved["first_name"]
            or "Пользователь"
        )

        username = normalize_username(
            saved["username"]
        )

        # ====================================================
        # PHOTO
        # ====================================================

        if saved["photo_file_id"]:

            caption = (
                saved["caption"]
                or ""
            )

            text = (
                f"<b>🗑 Удалено</b>\n\n"
                f"<b>{sender_name}</b> "
                f"{username}\n\n"
                f"{caption}"
            )

            try:

                await bot.send_photo(
                    owner_id,
                    photo=saved["photo_file_id"],
                    caption=text
                )

            except Exception as e:

                print(
                    "[DELETE PHOTO SEND ERROR]",
                    repr(e)
                )

            continue

        # ====================================================
        # TEXT
        # ====================================================

        if saved["text"]:

            try:

                await bot.send_message(
                    owner_id,
                    f"""
<b>🗑 Удалено</b>

<b>{sender_name}</b> {username}

{saved["text"]}
"""
                )

            except Exception as e:

                print(
                    "[DELETE TEXT SEND ERROR]",
                    repr(e)
                )

            continue

        # ====================================================
        # OTHER
        # ====================================================

        try:

            await bot.send_message(
                owner_id,
                f"""
<b>🗑 Сообщение удалено</b>

<b>{sender_name}</b> {username}

Тип: {saved["message_type"]}
"""
            )

        except Exception as e:

            print(
                "[DELETE OTHER SEND ERROR]",
                repr(e)
            )


# ============================================================
# ADMIN CALLBACK PROTECTION
# ============================================================

@dp.callback_query(
    F.data.startswith("admin:")
)
async def admin_fallback(
    callback: CallbackQuery
):

    if not is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "Нет доступа.",
            show_alert=True
        )


# ============================================================
# MAIN
# ============================================================

async def main():

    print(
        "======================================"
    )

    print(
        "🐻‍❄️ SPY BOT STARTED"
    )

    print(
        "Business message monitoring: ON"
    )

    print(
        "Edited messages: ON"
    )

    print(
        "Deleted messages: ON"
    )

    print(
        "Premium: ON"
    )

    print(
        "======================================"
    )

    # ========================================================
    # ВАЖНОЕ ИСПРАВЛЕНИЕ
    #
    # Не перечисляем вручную update types.
    # Aiogram сам получает ВСЕ типы обновлений,
    # для которых зарегистрированы handlers.
    # ========================================================

    allowed_updates = (
        dp.resolve_used_update_types()
    )

    print(
        "Allowed updates:"
    )

    print(
        allowed_updates
    )

    await dp.start_polling(
        bot,
        allowed_updates=allowed_updates
    )


if __name__ == "__main__":
    asyncio.run(main())