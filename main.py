import asyncio
import logging
import random
import html
import os
import sqlite3
from aiohttp import web

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command, ChatMemberUpdatedFilter, ADMINISTRATOR, KICKED, LEFT, CREATOR
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, 
    KeyboardButton, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    CallbackQuery,
    ChatMemberUpdated,
    ReplyKeyboardRemove
)

# === НАСТРОЙКИ (считываются из Render / Environment Variables) ===
BOT_TOKEN = os.getenv("BOT_TOKEN", "ТВОЙ_ТОКЕН_БОТА")
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))  # Твой Telegram ID

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Глобальные переменные
BOT_USERNAME = ""
giveaways = {}
giveaway_counter = 1
channels = {}  # {chat_id: title}

# === МИНИ БАЗА ДАННЫХ (SQLite) ===
DB_PATH = "bot_data.db"

def init_db():
    """Создаёт таблицы, если их ещё нет"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            chat_id INTEGER PRIMARY KEY,
            title TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS giveaways (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            button_text TEXT NOT NULL,
            forced_winner INTEGER,
            winner_id INTEGER,
            is_active INTEGER NOT NULL DEFAULT 1,
            target_chat_id INTEGER,
            msg_id INTEGER
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS participants (
            giveaway_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            username TEXT,
            PRIMARY KEY (giveaway_id, user_id),
            FOREIGN KEY (giveaway_id) REFERENCES giveaways(id) ON DELETE CASCADE
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    conn.close()

def load_data():
    """Загружает данные из БД в глобальные переменные"""
    global giveaways, channels, giveaway_counter
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Каналы
    channels.clear()
    for row in cur.execute("SELECT chat_id, title FROM channels"):
        channels[row[0]] = row[1]

    # Розыгрыши
    giveaways.clear()
    for row in cur.execute(
        "SELECT id, title, description, button_text, forced_winner, winner_id, is_active, target_chat_id, msg_id FROM giveaways"
    ):
        g_id = row[0]
        giveaways[g_id] = {
            "title": row[1],
            "description": row[2],
            "button_text": row[3],
            "forced_winner": row[4],
            "winner_id": row[5],
            "is_active": bool(row[6]),
            "target_chat_id": row[7],
            "msg_id": row[8],
            "participants": set()
        }

    # Участники
    for row in cur.execute("SELECT giveaway_id, user_id FROM participants"):
        g_id, user_id = row
        if g_id in giveaways:
            giveaways[g_id]["participants"].add(user_id)

    # Счётчик
    cur.execute("SELECT value FROM meta WHERE key = 'giveaway_counter'")
    row = cur.fetchone()
    if row:
        giveaway_counter = int(row[0])
    else:
        giveaway_counter = max(giveaways.keys(), default=0) + 1

    conn.close()
    logging.info(f"Данные загружены из БД: {len(channels)} каналов, {len(giveaways)} розыгрышей")

def save_channels():
    """Сохраняет каналы в БД"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM channels")
    for chat_id, title in channels.items():
        cur.execute("INSERT INTO channels (chat_id, title) VALUES (?, ?)", (chat_id, title))
    conn.commit()
    conn.close()

def save_giveaway(g_id: int):
    """Сохраняет/обновляет один розыгрыш и его участников"""
    if g_id not in giveaways:
        return
    g = giveaways[g_id]
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO giveaways
        (id, title, description, button_text, forced_winner, winner_id, is_active, target_chat_id, msg_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        g_id,
        g["title"],
        g["description"],
        g["button_text"],
        g["forced_winner"],
        g.get("winner_id"),
        1 if g["is_active"] else 0,
        g.get("target_chat_id"),
        g.get("msg_id")
    ))
    # Участники
    cur.execute("DELETE FROM participants WHERE giveaway_id = ?", (g_id,))
    for uid in g["participants"]:
        # username сохраняем если есть в кэше (опционально)
        cur.execute(
            "INSERT INTO participants (giveaway_id, user_id, username) VALUES (?, ?, ?)",
            (g_id, uid, None)
        )
    conn.commit()
    conn.close()

def save_participant(g_id: int, user_id: int, username: str | None = None):
    """Добавляет участника в БД"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO participants (giveaway_id, user_id, username) VALUES (?, ?, ?)",
        (g_id, user_id, username)
    )
    if username:
        cur.execute(
            "UPDATE participants SET username = ? WHERE giveaway_id = ? AND user_id = ?",
            (username, g_id, user_id)
        )
    conn.commit()
    conn.close()

def delete_giveaway_from_db(g_id: int):
    """Удаляет розыгрыш и его участников из БД"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM participants WHERE giveaway_id = ?", (g_id,))
    cur.execute("DELETE FROM giveaways WHERE id = ?", (g_id,))
    conn.commit()
    conn.close()

def save_counter():
    """Сохраняет счётчик розыгрышей"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('giveaway_counter', ?)", (str(giveaway_counter),))
    conn.commit()
    conn.close()

def get_participants_with_usernames(g_id: int) -> list[tuple[int, str | None]]:
    """Возвращает список (user_id, username) участников из БД"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id, username FROM participants WHERE giveaway_id = ?", (g_id,))
    rows = cur.fetchall()
    conn.close()
    return rows

PAID_BOT_TEXT = (
    "❗Этот бот для розыгрышей является платным. "
    "Стоимость бота на 1 месяц - 300₽. "
    "Купив доступ вы получите доступ к созданию розыгрышей, рандомному выбору победителей. "
    "Для покупки писать @cera_code."
)

# === СОСТОЯНИЯ (FSM) ДЛЯ ПОШАГОВОГО СОЗДАНИЯ И РЕДАКТИРОВАНИЯ ===
class CreateGiveawayState(StatesGroup):
    waiting_for_title = State()
    waiting_for_description = State()
    waiting_for_button_text = State()

class EditFieldState(StatesGroup):
    waiting_for_new_title = State()
    waiting_for_new_desc = State()
    waiting_for_new_btn = State()
    waiting_for_forced_winner = State()

# === ВЕБ-СЕРВЕР ДЛЯ RENDER И UPTIMEROBOT ===
async def handle_ping(request):
    """Эндпоинт для проверки работоспособности (Health Check)"""
    return web.Response(text="Bot is running and healthy!", status=200)

async def start_web_server():
    """Запуск фонового веб-сервера на порту Render"""
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/ping", handle_ping)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Веб-сервер успешно запущен на порту {port}")

# === АВТОМАТИЧЕСКОЕ ОТСЛЕЖИВАНИЕ КАНАЛОВ ===
@dp.my_chat_member()
async def on_my_chat_member_update(update: ChatMemberUpdated):
    """Отслеживает добавление и удаление бота из администраторов каналов"""
    chat_id = update.chat.id
    chat_title = update.chat.title or f"Чат {chat_id}"
    
    new_status = str(update.new_chat_member.status).lower()

    if "administrator" in new_status or "creator" in new_status:
        channels[chat_id] = chat_title
        save_channels()
        logging.info(f"Бот добавлен как админ в: {chat_title} ({chat_id})")
    elif "kicked" in new_status or "left" in new_status:
        if chat_id in channels:
            del channels[chat_id]
            save_channels()
            logging.info(f"Бот удален из: {chat_title} ({chat_id})")

# === КЛАВИАТУРЫ ===
admin_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Создать розыгрыш"), KeyboardButton(text="✏️ Редактировать розыгрыш")],
        [KeyboardButton(text="📢 Мои каналы"), KeyboardButton(text="👤 Профиль")],
        [KeyboardButton(text="ℹ️ Информация")]
    ],
    resize_keyboard=True
)

user_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="ℹ️ Информация")]
    ],
    resize_keyboard=True
)

cancel_keyboard = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="❌ Отмена")]],
    resize_keyboard=True
)

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===
async def refresh_channel_message(g_id: int):
    """Обновляет пост розыгрыша в канале при изменении названия, описания или кнопки"""
    g_data = giveaways.get(g_id)
    if not g_data or not g_data.get("target_chat_id") or not g_data.get("msg_id"):
        return

    text = f"🎁 <b>{html.escape(g_data['title'])}</b>\n\n{html.escape(g_data['description'])}"
    deep_link = f"https://t.me/{BOT_USERNAME}?start=join_{g_id}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=g_data["button_text"], url=deep_link)]
    ])

    try:
        await bot.edit_message_text(
            chat_id=g_data["target_chat_id"],
            message_id=g_data["msg_id"],
            text=text,
            reply_markup=kb,
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Не удалось обновить пост розыгрыша #{g_id}: {e}")

async def get_user_mention(chat_id: int, user_id: int) -> str:
    """Получает @юзернейм или кликабельную ссылку/ID пользователя"""
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        if member.user.username:
            return f"@{member.user.username}"
        elif member.user.full_name:
            return f"<a href='tg://user?id={user_id}'>{html.escape(member.user.full_name)}</a>"
    except Exception:
        pass
    return f"<code>{user_id}</code>"

async def execute_finish_giveaway(g_id: int, message_or_query_obj):
    """Единая логика завершения розыгрыша"""
    if g_id not in giveaways or not giveaways[g_id]["is_active"]:
        text = "❌ Розыгрыш не найден или уже завершен."
        if isinstance(message_or_query_obj, types.Message):
            await message_or_query_obj.answer(text)
        else:
            await message_or_query_obj.answer(text, show_alert=True)
        return

    g_data = giveaways[g_id]
    participants = list(g_data["participants"])
    target_chat_id = g_data.get("target_chat_id")
    
    if not target_chat_id:
        text = "❌ Не найден канал для публикации итогов."
        if isinstance(message_or_query_obj, types.Message):
            await message_or_query_obj.answer(text)
        else:
            await message_or_query_obj.answer(text, show_alert=True)
        return

    if not participants and g_data["forced_winner"] is None:
        results_text = (
            f"🎁<b>Итоги розыгрыша #{g_id}</b>\n\n"
            f"🥇<b>Победитель:</b> Участников не было, победитель не выбран.\n\n"
            f"Бот: @Winer_bot_randombot"
        )
        await bot.send_message(chat_id=target_chat_id, text=results_text, parse_mode="HTML")
        g_data["is_active"] = False
        save_giveaway(g_id)
        msg = f"Итоги розыгрыша #{g_id} отправлены (нет участников)."
        if isinstance(message_or_query_obj, types.Message):
            await message_or_query_obj.answer(msg)
        else:
            await message_or_query_obj.message.answer(msg)
        return

    if g_data["forced_winner"] is not None:
        winner_id = g_data["forced_winner"]
    else:
        winner_id = random.choice(participants)

    g_data["winner_id"] = winner_id
    g_data["is_active"] = False
    save_giveaway(g_id)

    winner_mention = await get_user_mention(target_chat_id, winner_id)

    # Точный формат сообщения об итогах в канал
    results_text = (
        f"🎁<b>Итоги розыгрыша #{g_id}</b>\n\n"
        f"🥇<b>Победитель:</b> {winner_mention}\n\n"
        f"Бот: @Winer_bot_randombot"
    )
    
    # Публикация в канал
    await bot.send_message(chat_id=target_chat_id, text=results_text, parse_mode="HTML")
    
    # Личное сообщение победителю
    try:
        await bot.send_message(
            chat_id=winner_id,
            text=f"🎉 <b>ПОЗДРАВЛЯЕМ!</b> Вы победили в розыгрыше «<b>{html.escape(g_data['title'])}</b>»!",
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Не удалось отправить сообщение победителю в ЛС: {e}")

    # Подтверждение админу
    confirm_text = f"✅ <b>Итоги розыгрыша #{g_id} успешно опубликованы в канале!</b>\nПобедитель: {winner_mention}"
    if isinstance(message_or_query_obj, types.Message):
        await message_or_query_obj.answer(confirm_text, parse_mode="HTML")
    else:
        await message_or_query_obj.message.answer(confirm_text, parse_mode="HTML")

# === ОБРАБОТЧИКИ КОМАНД И МЕНЮ ===

@dp.message(F.text == "❌ Отмена")
async def cancel_handler(message: types.Message, state: FSMContext):
    await state.clear()
    kb = admin_menu if message.from_user.id == ADMIN_ID else user_menu
    await message.answer("❌ Действие отменено.", reply_markup=kb)

@dp.message(CommandStart())
async def start_cmd(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    args = message.text.split()
    
    # Режим участия через кнопку в канале (Диплинк: /start join_1)
    if len(args) > 1 and args[1].startswith("join_"):
        try:
            g_id = int(args[1].split("_")[1])
            kb = admin_menu if user_id == ADMIN_ID else user_menu
            
            if g_id not in giveaways or not giveaways[g_id]["is_active"]:
                await message.answer("❌ Этот розыгрыш не существует или уже завершен.", reply_markup=kb)
                return
            
            g_data = giveaways[g_id]
            if user_id in g_data["participants"]:
                await message.answer(
                    f"Вы уже являетесь участником розыгрыша «<b>{html.escape(g_data['title'])}</b>»! 😉",
                    reply_markup=kb,
                    parse_mode="HTML"
                )
            else:
                g_data["participants"].add(user_id)
                username = message.from_user.username
                save_participant(g_id, user_id, username)
                save_giveaway(g_id)
                await message.answer(
                    f"Вы успешно зарегистрированы в розыгрыше «<b>{html.escape(g_data['title'])}</b>»!\n\n"
                    f"Теперь вы участвуете в розыгрыше✅",
                    reply_markup=kb,
                    parse_mode="HTML"
                )
            return
        except Exception as e:
            logging.error(f"Ошибка обработки диплинка: {e}")

    kb = admin_menu if user_id == ADMIN_ID else user_menu
    welcome_text = (
        "🪁 <b>Добро пожаловать!</b>\n\n"
        "Это бот для проведения и участия в розыгрышах в Telegram.\n\n"
        "Выбери действие в меню ниже 👇"
    )
    await message.answer(welcome_text, reply_markup=kb, parse_mode="HTML")

@dp.message(F.text == "ℹ️ Информация")
async def info_cmd(message: types.Message):
    await message.answer(
        "ℹ️ <b>Информация:</b>\n\n"
        "Бот позволяет принимать участие в конкурсах и розыгрышах Telegram-каналов.\n"
        "Принимайте участие в каналах, переходите в бота и побеждайте!",
        parse_mode="HTML"
    )

@dp.message(F.text == "👤 Профиль")
async def profile_cmd(message: types.Message):
    user_id = message.from_user.id
    full_name = html.escape(message.from_user.full_name)
    
    participating = [
        f"• #{g_id} «<b>{html.escape(g['title'])}</b>»" 
        for g_id, g in giveaways.items() 
        if user_id in g["participants"] and g["is_active"]
    ]
    
    won = [
        f"• #{g_id} «<b>{html.escape(g['title'])}</b>»" 
        for g_id, g in giveaways.items() 
        if g.get("winner_id") == user_id
    ]

    part_text = "\n".join(participating) if participating else "<i>Нет активных участий</i>"
    won_text = "\n".join(won) if won else "<i>Пока нет побед</i>"
    
    text = (
        f"👤 <b>Ваш профиль</b>\n"
        f"ID: <code>{user_id}</code>\n"
        f"Имя: {full_name}\n\n"
        f"🎯 <b>Розыгрыши, в которых вы участвуете:</b>\n{part_text}\n\n"
        f"🏆 <b>Выигранные розыгрыши:</b>\n{won_text}"
    )
    await message.answer(text, parse_mode="HTML")

# Ограничение доступа к кнопкам для не-админов
@dp.message(F.text.in_({"➕ Создать розыгрыш", "✏️ Редактировать розыгрыш", "📢 Мои каналы"}))
async def restricted_buttons_handler(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.answer(PAID_BOT_TEXT)
        return

    if message.text == "📢 Мои каналы":
        await my_channels_cmd(message)
    elif message.text == "✏️ Редактировать розыгрыш":
        await edit_giveaway_cmd(message)
    elif message.text == "➕ Создать розыгрыш":
        await create_giveaway_start(message, state)

async def my_channels_cmd(message: types.Message):
    if not channels:
        await message.answer(
            "📢 <b>У вас пока нет подключенных каналов.</b>\n\n"
            "Чтобы добавить канал, назначьте бота <b>администратором</b> в вашем канале.",
            parse_mode="HTML"
        )
        return

    text = "📢 <b>Каналы, в которых бот назначен администратором:</b>\n\n"
    for c_id, c_title in channels.items():
        text += f"• <b>{html.escape(c_title)}</b> (ID: <code>{c_id}</code>)\n"
    
    await message.answer(text, parse_mode="HTML")

# === УДОБНОЕ ИНТЕРАКТИВНОЕ СОЗДАНИЕ РОЗЫГРЫША ===

async def create_giveaway_start(message: types.Message, state: FSMContext):
    if not channels:
        await message.answer(
            "⚠️ <b>У вас нет подключенных каналов.</b>\n\n"
            "Сначала добавьте бота в ваш Telegram-канал как администратора!",
            parse_mode="HTML"
        )
        return

    await state.set_state(CreateGiveawayState.waiting_for_title)
    
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=f"Использовать «Розыгрыш #{giveaway_counter}»")],
            [KeyboardButton(text="❌ Отмена")]
        ],
        resize_keyboard=True
    )
    
    await message.answer(
        f"📝 <b>Шаг 1 из 3: Название розыгрыша</b>\n\n"
        f"Введите название для нового розыгрыша #{giveaway_counter} "
        f"или нажмите кнопку ниже, чтобы использовать название по умолчанию:",
        reply_markup=kb,
        parse_mode="HTML"
    )

@dp.message(CreateGiveawayState.waiting_for_title)
async def process_create_title(message: types.Message, state: FSMContext):
    if message.text.startswith("Использовать «Розыгрыш #"):
        title = f"Розыгрыш #{giveaway_counter}"
    else:
        title = message.text.strip()

    await state.update_data(title=title)
    await state.set_state(CreateGiveawayState.waiting_for_description)

    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Использовать стандартное описание")],
            [KeyboardButton(text="❌ Отмена")]
        ],
        resize_keyboard=True
    )

    await message.answer(
        f"✅ Название сохранено: <b>{html.escape(title)}</b>\n\n"
        f"📝 <b>Шаг 2 из 3: Описание розыгрыша</b>\n\n"
        f"Отправьте текст описания или нажмите кнопку использования стандартного описания:",
        reply_markup=kb,
        parse_mode="HTML"
    )

@dp.message(CreateGiveawayState.waiting_for_description)
async def process_create_desc(message: types.Message, state: FSMContext):
    if message.text == "Использовать стандартное описание":
        desc = "Нажмите кнопку ниже, чтобы принять участие!"
    else:
        desc = message.text.strip()

    await state.update_data(description=desc)
    await state.set_state(CreateGiveawayState.waiting_for_button_text)

    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎉 Участвовать")],
            [KeyboardButton(text="❌ Отмена")]
        ],
        resize_keyboard=True
    )

    await message.answer(
        f"✅ Описание сохранено!\n\n"
        f"📝 <b>Шаг 3 из 3: Текст на кнопке участия</b>\n\n"
        f"Введите текст для кнопки (например: <i>🎉 Принять участие</i>) или нажмите «🎉 Участвовать»:",
        reply_markup=kb,
        parse_mode="HTML"
    )

@dp.message(CreateGiveawayState.waiting_for_button_text)
async def process_create_button_text(message: types.Message, state: FSMContext):
    button_text = message.text.strip()
    data = await state.get_data()
    await state.clear()

    global giveaway_counter
    g_id = giveaway_counter
    giveaway_counter += 1
    save_counter()

    giveaways[g_id] = {
        "title": data["title"],
        "description": data["description"],
        "button_text": button_text,
        "participants": set(),
        "forced_winner": None,
        "winner_id": None,
        "is_active": True,
        "target_chat_id": None,
        "msg_id": None
    }
    save_giveaway(g_id)

    # Формируем список каналов для публикации
    buttons = []
    for c_id, c_title in channels.items():
        buttons.append([InlineKeyboardButton(text=f"📢 {c_title}", callback_data=f"pub_{g_id}_{c_id}")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    preview_text = (
        f"🎉 <b>Предпросмотр розыгрыша #{g_id}</b>\n\n"
        f"<b>Название:</b> {html.escape(data['title'])}\n"
        f"<b>Описание:</b>\n{html.escape(data['description'])}\n"
        f"<b>Текст кнопки:</b> {html.escape(button_text)}\n\n"
        f"👇 <b>Выберите канал, в который опубликовать этот розыгрыш:</b>"
    )

    await message.answer("✅ Настройки розыгрыша сохранены!", reply_markup=admin_menu)
    await message.answer(preview_text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("pub_"))
async def publish_giveaway_callback(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Ошибка доступа", show_alert=True)
        return

    parts = callback.data.split("_")
    g_id = int(parts[1])
    target_chat_id = int(parts[2])
    
    if g_id not in giveaways:
        await callback.answer("❌ Розыгрыш не найден.", show_alert=True)
        return

    g_data = giveaways[g_id]
    g_data["target_chat_id"] = target_chat_id
    
    deep_link = f"https://t.me/{BOT_USERNAME}?start=join_{g_id}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=g_data["button_text"], url=deep_link)]
    ])
    
    try:
        sent_msg = await bot.send_message(
            chat_id=target_chat_id,
            text=f"🎁 <b>{html.escape(g_data['title'])}</b>\n\n{html.escape(g_data['description'])}",
            reply_markup=kb,
            parse_mode="HTML"
        )
        g_data["msg_id"] = sent_msg.message_id
        save_giveaway(g_id)
        
        await callback.message.edit_text(
            f"✅ <b>Розыгрыш #{g_id} «{html.escape(g_data['title'])}» успешно опубликован в канале!</b>",
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Ошибка публикации розыгрыша: {e}")
        await callback.message.edit_text(
            f"❌ <b>Не удалось опубликовать розыгрыш.</b>\n"
            f"Убедитесь, что бот добавлен в канал и имеет права на отправку сообщений.",
            parse_mode="HTML"
        )

# === УДОБНОЕ ИНТЕРАКТИВНОЕ РЕДАКТИРОВАНИЕ ===

async def edit_giveaway_cmd(message: types.Message):
    active_giveaways = [g_id for g_id, g in giveaways.items() if g["is_active"]]
    
    text = (
        "✏️ <b>Панель управления розыгрышами:</b>\n\n"
        "Вы можете использовать быстрые кнопки ниже или команды:\n"
        "• <code>/settitle ID Название</code>\n"
        "• <code>/setdesc ID Описание</code>\n"
        "• <code>/setbtn ID ТекстКнопки</code>\n"
        "• <code>/setwinner ID UserID</code>\n"
        "• <code>/finish ID</code>\n"
        "• <code>/delgiveaway ID</code>"
    )

    if not active_giveaways:
        await message.answer(f"{text}\n\n<i>Сейчас нет активных розыгрышей.</i>", parse_mode="HTML")
        return

    buttons = []
    for g_id in active_giveaways:
        g_title = giveaways[g_id]["title"]
        buttons.append([InlineKeyboardButton(text=f"⚙️ Розыгрыш #{g_id}: {g_title}", callback_data=f"manage_g_{g_id}")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(f"{text}\n\n<b>Выберите активный розыгрыш для управления:</b>", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("manage_g_"))
async def manage_giveaway_panel(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Ошибка доступа", show_alert=True)
        return

    g_id = int(callback.data.split("_")[2])
    if g_id not in giveaways:
        await callback.answer("❌ Розыгрыш не найден.", show_alert=True)
        return

    g_data = giveaways[g_id]
    forced_info = f"<code>{g_data['forced_winner']}</code>" if g_data['forced_winner'] else "Не задан"

    text = (
        f"⚙️ <b>Управление розыгрышем #{g_id}</b>\n\n"
        f"<b>Название:</b> {html.escape(g_data['title'])}\n"
        f"<b>Описание:</b> {html.escape(g_data['description'])}\n"
        f"<b>Кнопка:</b> {html.escape(g_data['button_text'])}\n"
        f"<b>Участников:</b> {len(g_data['participants'])}\n"
        f"<b>Подставной победитель:</b> {forced_info}\n"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✏️ Название", callback_data=f"edit_t_{g_id}"),
            InlineKeyboardButton(text="✏️ Описание", callback_data=f"edit_d_{g_id}")
        ],
        [
            InlineKeyboardButton(text="✏️ Кнопка", callback_data=f"edit_b_{g_id}"),
            InlineKeyboardButton(text="🤫 Назначить побед.", callback_data=f"edit_w_{g_id}")
        ],
        [
            InlineKeyboardButton(text="👥 Участники", callback_data=f"edit_p_{g_id}")
        ],
        [
            InlineKeyboardButton(text="🏆 Завершить и отправить итоги", callback_data=f"edit_f_{g_id}")
        ],
        [
            InlineKeyboardButton(text="🗑️ Удалить розыгрыш", callback_data=f"edit_del_{g_id}")
        ]
    ])

    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("edit_"))
async def inline_edit_actions(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Ошибка доступа", show_alert=True)
        return

    parts = callback.data.split("_")
    action = parts[1]
    g_id = int(parts[2])

    if g_id not in giveaways:
        await callback.answer("❌ Розыгрыш не найден.", show_alert=True)
        return

    await state.update_data(editing_g_id=g_id)

    if action == "t":
        await state.set_state(EditFieldState.waiting_for_new_title)
        await callback.message.answer(f"Введите новое <b>название</b> для розыгрыша #{g_id}:", reply_markup=cancel_keyboard, parse_mode="HTML")
        await callback.answer()
    elif action == "d":
        await state.set_state(EditFieldState.waiting_for_new_desc)
        await callback.message.answer(f"Введите новое <b>описание</b> для розыгрыша #{g_id}:", reply_markup=cancel_keyboard, parse_mode="HTML")
        await callback.answer()
    elif action == "b":
        await state.set_state(EditFieldState.waiting_for_new_btn)
        await callback.message.answer(f"Введите новый <b>текст кнопки</b> для розыгрыша #{g_id}:", reply_markup=cancel_keyboard, parse_mode="HTML")
        await callback.answer()
    elif action == "w":
        await state.set_state(EditFieldState.waiting_for_forced_winner)
        await callback.message.answer(f"Введите <b>ID пользователя</b>, который должен победить в розыгрыше #{g_id}:", reply_markup=cancel_keyboard, parse_mode="HTML")
        await callback.answer()
    elif action == "p":
        # Просмотр участников
        await callback.answer()
        g_data = giveaways[g_id]
        participants = list(g_data["participants"])
        if not participants:
            await callback.message.answer(f"👥 В розыгрыше #{g_id} пока нет участников.")
            return

        # Получаем username из БД + пробуем подтянуть актуальные
        db_parts = {uid: uname for uid, uname in get_participants_with_usernames(g_id)}
        lines = []
        for uid in participants:
            uname = db_parts.get(uid)
            if uname:
                lines.append(f"• @{html.escape(uname)} (<code>{uid}</code>)")
            else:
                # Пробуем получить актуальный username
                try:
                    chat = await bot.get_chat(uid)
                    if chat.username:
                        lines.append(f"• @{html.escape(chat.username)} (<code>{uid}</code>)")
                        save_participant(g_id, uid, chat.username)
                    elif chat.full_name:
                        lines.append(f"• {html.escape(chat.full_name)} (<code>{uid}</code>)")
                    else:
                        lines.append(f"• <code>{uid}</code>")
                except Exception:
                    lines.append(f"• <code>{uid}</code>")

        text = (
            f"👥 <b>Участники розыгрыша #{g_id}</b> "
            f"(всего: {len(participants)}):\n\n" + "\n".join(lines)
        )
        # Telegram лимит ~4096 символов, разбиваем при необходимости
        if len(text) > 4000:
            chunks = []
            current = f"👥 <b>Участники розыгрыша #{g_id}</b> (всего: {len(participants)}):\n\n"
            for line in lines:
                if len(current) + len(line) + 1 > 4000:
                    chunks.append(current)
                    current = line + "\n"
                else:
                    current += line + "\n"
            if current.strip():
                chunks.append(current)
            for chunk in chunks:
                await callback.message.answer(chunk, parse_mode="HTML")
        else:
            await callback.message.answer(text, parse_mode="HTML")
    elif action == "f":
        await callback.answer()
        await execute_finish_giveaway(g_id, callback)
    elif action == "del":
        await callback.answer()
        g_data = giveaways[g_id]
        if g_data.get("target_chat_id") and g_data.get("msg_id"):
            try:
                await bot.delete_message(chat_id=g_data["target_chat_id"], message_id=g_data["msg_id"])
            except Exception as e:
                logging.error(f"Не удалось удалить сообщение: {e}")
        del giveaways[g_id]
        delete_giveaway_from_db(g_id)
        await callback.message.edit_text(f"🗑️ Розыгрыш #{g_id} успешно удален!")

@dp.message(EditFieldState.waiting_for_new_title)
async def process_new_title(message: types.Message, state: FSMContext):
    data = await state.get_data()
    g_id = data["editing_g_id"]
    await state.clear()

    if g_id in giveaways:
        giveaways[g_id]["title"] = message.text.strip()
        save_giveaway(g_id)
        await refresh_channel_message(g_id)
        await message.answer(f"✅ Название розыгрыша #{g_id} обновлено!", reply_markup=admin_menu)

@dp.message(EditFieldState.waiting_for_new_desc)
async def process_new_desc(message: types.Message, state: FSMContext):
    data = await state.get_data()
    g_id = data["editing_g_id"]
    await state.clear()

    if g_id in giveaways:
        giveaways[g_id]["description"] = message.text.strip()
        save_giveaway(g_id)
        await refresh_channel_message(g_id)
        await message.answer(f"✅ Описание розыгрыша #{g_id} обновлено!", reply_markup=admin_menu)

@dp.message(EditFieldState.waiting_for_new_btn)
async def process_new_btn(message: types.Message, state: FSMContext):
    data = await state.get_data()
    g_id = data["editing_g_id"]
    await state.clear()

    if g_id in giveaways:
        giveaways[g_id]["button_text"] = message.text.strip()
        save_giveaway(g_id)
        await refresh_channel_message(g_id)
        await message.answer(f"✅ Текст кнопки розыгрыша #{g_id} обновлен!", reply_markup=admin_menu)

@dp.message(EditFieldState.waiting_for_forced_winner)
async def process_forced_winner(message: types.Message, state: FSMContext):
    data = await state.get_data()
    g_id = data["editing_g_id"]
    await state.clear()

    try:
        winner_id = int(message.text.strip())
        if g_id in giveaways:
            giveaways[g_id]["forced_winner"] = winner_id
            save_giveaway(g_id)
            await message.answer(f"🤫 Победителем розыгрыша #{g_id} назначен ID <code>{winner_id}</code>", reply_markup=admin_menu, parse_mode="HTML")
    except ValueError:
        await message.answer("⚠️ Некорректный ID. Отмена.", reply_markup=admin_menu)

# === СЕКРЕТНЫЕ КОМАНДЫ УПРАВЛЕНИЯ ДЛЯ АДМИНА (СЛАШ-КОМАНДЫ) ===

@dp.message(Command("settitle"))
async def secret_set_title(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer(PAID_BOT_TEXT)
        return

    try:
        parts = message.text.split(maxsplit=2)
        g_id = int(parts[1])
        new_title = parts[2]

        if g_id not in giveaways:
            await message.answer("❌ Розыгрыш не найден.")
            return

        giveaways[g_id]["title"] = new_title
        save_giveaway(g_id)
        await refresh_channel_message(g_id)
        await message.answer(f"✅ Название розыгрыша #{g_id} изменено на: <b>{html.escape(new_title)}</b>", parse_mode="HTML")
    except Exception:
        await message.answer("⚠️ Использование: <code>/settitle ID_розыгрыша Новое название</code>", parse_mode="HTML")

@dp.message(Command("setdesc"))
async def secret_set_desc(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer(PAID_BOT_TEXT)
        return

    try:
        parts = message.text.split(maxsplit=2)
        g_id = int(parts[1])
        new_desc = parts[2]

        if g_id not in giveaways:
            await message.answer("❌ Розыгрыш не найден.")
            return

        giveaways[g_id]["description"] = new_desc
        save_giveaway(g_id)
        await refresh_channel_message(g_id)
        await message.answer(f"✅ Описание розыгрыша #{g_id} обновлено!", parse_mode="HTML")
    except Exception:
        await message.answer("⚠️ Использование: <code>/setdesc ID_розыгрыша Новое описание</code>", parse_mode="HTML")

@dp.message(Command("setbtn"))
async def secret_set_btn(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer(PAID_BOT_TEXT)
        return

    try:
        parts = message.text.split(maxsplit=2)
        g_id = int(parts[1])
        new_btn = parts[2]

        if g_id not in giveaways:
            await message.answer("❌ Розыгрыш не найден.")
            return

        giveaways[g_id]["button_text"] = new_btn
        save_giveaway(g_id)
        await refresh_channel_message(g_id)
        await message.answer(f"✅ Текст кнопки розыгрыша #{g_id} изменен на: <b>{html.escape(new_btn)}</b>", parse_mode="HTML")
    except Exception:
        await message.answer("⚠️ Использование: <code>/setbtn ID_розыгрыша Текст кнопки</code>", parse_mode="HTML")

@dp.message(Command("delgiveaway"))
async def secret_del_giveaway(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer(PAID_BOT_TEXT)
        return

    try:
        g_id = int(message.text.split()[1])
        if g_id not in giveaways:
            await message.answer("❌ Розыгрыш не найден.")
            return

        g_data = giveaways[g_id]
        if g_data.get("target_chat_id") and g_data.get("msg_id"):
            try:
                await bot.delete_message(chat_id=g_data["target_chat_id"], message_id=g_data["msg_id"])
            except Exception as e:
                logging.error(f"Не удалось удалить сообщение в канале: {e}")

        del giveaways[g_id]
        delete_giveaway_from_db(g_id)
        await message.answer(f"🗑️ Розыгрыш #{g_id} успешно удален!", parse_mode="HTML")
    except Exception:
        await message.answer("⚠️ Использование: <code>/delgiveaway ID_розыгрыша</code>", parse_mode="HTML")

@dp.message(Command("setwinner"))
async def secret_set_winner(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer(PAID_BOT_TEXT)
        return

    try:
        args = message.text.split()
        g_id = int(args[1])
        target_user_id = int(args[2])
        
        if g_id not in giveaways:
            await message.answer("❌ Розыгрыш с таким ID не найден.")
            return

        if not giveaways[g_id]["is_active"]:
            await message.answer("❌ Этот розыгрыш уже завершен.")
            return

        giveaways[g_id]["forced_winner"] = target_user_id
        save_giveaway(g_id)
        await message.answer(
            f"🤫 <b>Настройка сохранена:</b> Победителем розыгрыша #{g_id} заранее назначен ID <code>{target_user_id}</code>.",
            parse_mode="HTML"
        )
    except Exception:
        await message.answer("⚠️ Использование: <code>/setwinner ID_розыгрыша ID_пользователя</code>", parse_mode="HTML")

@dp.message(Command("finish"))
async def finish_giveaway_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer(PAID_BOT_TEXT)
        return

    try:
        args = message.text.split()
        g_id = int(args[1])
        await execute_finish_giveaway(g_id, message)
    except Exception as e:
        logging.error(f"Ошибка подведения итогов: {e}")
        await message.answer("⚠️ Использование: <code>/finish ID_розыгрыша</code>", parse_mode="HTML")

# === ТОЧКА ВХОДА ===

async def main():
    global BOT_USERNAME
    init_db()
    load_data()
    bot_info = await bot.get_me()
    BOT_USERNAME = bot_info.username

    await start_web_server()
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
