import asyncio
import logging
import random
import html
import os
from aiohttp import web

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command, ChatMemberUpdatedFilter, ADMINISTRATOR, KICKED, LEFT, CREATOR
from aiogram.types import (
    ReplyKeyboardMarkup, 
    KeyboardButton, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    CallbackQuery,
    ChatMemberUpdated
)

# === НАСТРОЙКИ (считываются из Render / Environment Variables) ===
BOT_TOKEN = os.getenv("BOT_TOKEN", "ТВОЙ_ТОКЕН_БОТА")
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))  # Укажи свой Telegram ID по умолчанию

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Хранилище данных (в оперативной памяти)
giveaways = {}
giveaway_counter = 1
channels = {}  # {chat_id: title}

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
    
    # Render передает порт через переменную окружения PORT
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
    new_status = update.new_chat_member.status

    if new_status in [ADMINISTRATOR, CREATOR]:
        channels[chat_id] = chat_title
        logging.info(f"Бот добавлен как админ в: {chat_title} ({chat_id})")
    elif new_status in [KICKED, LEFT]:
        if chat_id in channels:
            del channels[chat_id]
            logging.info(f"Бот удален из: {chat_title} ({chat_id})")

# === КЛАВИАТУРЫ ===
main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Создать розыгрыш"), KeyboardButton(text="✏️ Редактировать розыгрыш")],
        [KeyboardButton(text="📢 Мои каналы"), KeyboardButton(text="👤 Профиль")],
        [KeyboardButton(text="ℹ️ Информация")]
    ],
    resize_keyboard=True
)

# === ОБРАБОТЧИКИ КОМАНД И МЕНЮ ===

@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    welcome_text = (
        "🪁 <b>Добро пожаловать!</b>\n\n"
        "Это бот для создания и проведения розыгрышей в Telegram.\n\n"
        "✨ Создавай розыгрыши, публикой их в свои каналы и выбирай победителей!\n\n"
        "Выбери действие в меню ниже 👇"
    )
    await message.answer(welcome_text, reply_markup=main_menu, parse_mode="HTML")

@dp.message(F.text == "ℹ️ Информация")
async def info_cmd(message: types.Message):
    await message.answer(
        "ℹ️ <b>Как проводить розыгрыши в каналах:</b>\n\n"
        "1. Добавьте бота в ваш Telegram-канал как администратора с правом публикации сообщений.\n"
        "2. Нажмите кнопку <b>«➕ Создать розыгрыш»</b>.\n"
        "3. Выберите канал из списка подключенных, и бот сам опубликует туда пост с кнопкой участия!",
        parse_mode="HTML"
    )

@dp.message(F.text == "👤 Профиль")
async def profile_cmd(message: types.Message):
    user_id = message.from_user.id
    full_name = html.escape(message.from_user.full_name)
    
    text = f"👤 <b>Ваш профиль</b>\nID: <code>{user_id}</code>\nИмя: {full_name}"
    await message.answer(text, parse_mode="HTML")

@dp.message(F.text == "📢 Мои каналы")
async def my_channels_cmd(message: types.Message):
    if not channels:
        await message.answer(
            "📢 <b>У вас пока нет подключенных каналов.</b>\n\n"
            "Чтобы добавить канал, назначьте бота <b>администратором</b> в вашем канале. "
            "После этого он автоматически появится в этом списке!",
            parse_mode="HTML"
        )
        return

    text = "📢 <b>Каналы, в которых бот назначен администратором:</b>\n\n"
    for c_id, c_title in channels.items():
        text += f"• <b>{html.escape(c_title)}</b> (ID: <code>{c_id}</code>)\n"
    
    await message.answer(text, parse_mode="HTML")

@dp.message(F.text == "✏️ Редактировать розыгрыш")
async def edit_giveaway_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⚠️ Эта функция доступна только администратору.")
        return

    text = (
        "✏️ <b>Панель управления розыгрышами:</b>\n\n"
        "1. Задать подставного победителя:\n"
        "<code>/setwinner ID_розыгрыша ID_пользователя</code>\n\n"
        "2. Завершить розыгрыш и отправить результаты в канал:\n"
        "<code>/finish ID_розыгрыша</code>"
    )
    await message.answer(text, parse_mode="HTML")

@dp.message(F.text == "➕ Создать розыгрыш")
async def create_giveaway(message: types.Message):
    global giveaway_counter
    g_id = giveaway_counter
    giveaway_counter += 1
    
    giveaways[g_id] = {
        "title": f"Розыгрыш #{g_id}",
        "participants": set(),
        "forced_winner": None,
        "is_active": True,
        "target_chat_id": None,
        "msg_id": None
    }
    
    # Формируем список кнопок для выбора места публикации
    buttons = []
    for c_id, c_title in channels.items():
        buttons.append([InlineKeyboardButton(text=f"📢 {c_title}", callback_data=f"pub_{g_id}_{c_id}")])
    
    buttons.append([InlineKeyboardButton(text="💬 Опубликовать прямо здесь (в ЛС)", callback_data=f"pub_{g_id}_pm")])
    
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    await message.answer(
        f"🎁 <b>Создание розыгрыша #{g_id}</b>\n\n"
        f"Выбери канал, в который бот должен выложить розыгрыш:",
        reply_markup=kb,
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("pub_"))
async def publish_giveaway_callback(callback: CallbackQuery):
    parts = callback.data.split("_")
    g_id = int(parts[1])
    target_str = parts[2]
    
    if g_id not in giveaways:
        await callback.answer("❌ Розыгрыш не найден.", show_alert=True)
        return

    if target_str == "pm":
        target_chat_id = callback.message.chat.id
        location_name = "личные сообщения"
    else:
        target_chat_id = int(target_str)
        location_name = f"канал <b>{html.escape(channels.get(target_chat_id, 'Канал'))}</b>"

    giveaways[g_id]["target_chat_id"] = target_chat_id
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎉 Участвовать", callback_data=f"join_{g_id}")]
    ])
    
    try:
        sent_msg = await bot.send_message(
            chat_id=target_chat_id,
            text=f"🎁 <b>Розыгрыш #{g_id}</b>\n\nНажмите кнопку ниже, чтобы принять участие!",
            reply_markup=kb,
            parse_mode="HTML"
        )
        giveaways[g_id]["msg_id"] = sent_msg.message_id
        
        await callback.message.edit_text(
            f"✅ <b>Розыгрыш #{g_id} успешно опубликован в {location_name}!</b>",
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Ошибка публикации розыгрыша: {e}")
        await callback.message.edit_text(
            f"❌ <b>Не удалось опубликовать розыгрыш.</b>\n"
            f"Убедитесь, что бот добавлен в канал и имеет права на отправку сообщений.",
            parse_mode="HTML"
        )

@dp.callback_query(F.data.startswith("join_"))
async def join_giveaway(callback: CallbackQuery):
    g_id = int(callback.data.split("_")[1])
    
    if g_id not in giveaways or not giveaways[g_id]["is_active"]:
        await callback.answer("❌ Этот розыгрыш уже завершен.", show_alert=True)
        return
        
    user_id = callback.from_user.id
    if user_id in giveaways[g_id]["participants"]:
        await callback.answer("Вы уже участвуете!", show_alert=True)
    else:
        giveaways[g_id]["participants"].add(user_id)
        await callback.answer("✅ Вы успешно зарегистрировались в розыгрыше!", show_alert=True)

# === СЕКРЕТНЫЕ КОМАНДЫ ДЛЯ АДМИНА ===

@dp.message(Command("setwinner"))
async def secret_set_winner(message: types.Message):
    if message.from_user.id != ADMIN_ID:
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
        await message.answer(
            f"🤫 <b>Настройка сохранена:</b> Победителем розыгрыша #{g_id} заранее назначен ID <code>{target_user_id}</code>.",
            parse_mode="HTML"
        )
    except Exception:
        await message.answer("⚠️ Ошибка. Формат команды: <code>/setwinner ID_розыгрыша ID_пользователя</code>", parse_mode="HTML")

@dp.message(Command("finish"))
async def finish_giveaway_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    try:
        args = message.text.split()
        g_id = int(args[1])
        
        if g_id not in giveaways or not giveaways[g_id]["is_active"]:
            await message.answer("❌ Розыгрыш не найден или уже завершен.")
            return

        g_data = giveaways[g_id]
        participants = list(g_data["participants"])
        target_chat_id = g_data.get("target_chat_id")
        
        if not target_chat_id:
            await message.answer("❌ Не найден чат/канал для публикации итогов.")
            return

        if not participants and g_data["forced_winner"] is None:
            results_text = f"🎰 <b>Розыгрыш #{g_id} завершен!</b>\n\nУчастников не было, победитель не выбран."
            await bot.send_message(chat_id=target_chat_id, text=results_text, parse_mode="HTML")
            await message.answer(f"Итоги розыгрыша #{g_id} отправлены.")
            g_data["is_active"] = False
            return

        if g_data["forced_winner"] is not None:
            winner_id = g_data["forced_winner"]
        else:
            winner_id = random.choice(participants)

        g_data["is_active"] = False
        
        results_text = (
            f"🏆 <b>Итоги розыгрыша #{g_id}!</b>\n\n"
            f"Всего участников: {len(participants)}\n"
            f"🎉 <b>Победитель:</b> <a href='tg://user?id={winner_id}'>пользователь (ID: {winner_id})</a>"
        )
        
        # Отправляем результаты от лица бота прямо в канал
        await bot.send_message(chat_id=target_chat_id, text=results_text, parse_mode="HTML")
        
        # Подтверждение администратору в ЛС
        await message.answer(
            f"✅ <b>Итоги розыгрыша #{g_id} успешно опубликованы в канале!</b>\nПобедитель: <code>{winner_id}</code>",
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Ошибка подведения итогов: {e}")
        await message.answer("⚠️ Ошибка. Формат команды: <code>/finish ID_розыгрыша</code>", parse_mode="HTML")

# === ТОЧКА ВХОДА ===

async def main():
    # Запускаем одновременно веб-сервер для пинга и самого бота
    await start_web_server()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
