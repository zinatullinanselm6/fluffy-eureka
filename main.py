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
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))  # Твой Telegram ID

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Глобальные переменные
BOT_USERNAME = ""
giveaways = {}
giveaway_counter = 1
channels = {}  # {chat_id: title}

PAID_BOT_TEXT = (
    "❗Этот бот для розыгрышей является платным. "
    "Стоимость бота на 1 месяц - 300₽. "
    "Купив доступ вы получите доступ к созданию розыгрышей, рандомному выбору победителей. "
    "Для покупки писать @cera_code."
)

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
        logging.info(f"Бот добавлен как админ в: {chat_title} ({chat_id})")
    elif "kicked" in new_status or "left" in new_status:
        if chat_id in channels:
            del channels[chat_id]
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

# === ОБРАБОТЧИКИ КОМАНД И МЕНЮ ===

@dp.message(CommandStart())
async def start_cmd(message: types.Message):
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
                await message.answer(
                    f"Вы успешно зарегистрированы в розыгрыше «<b>{html.escape(g_data['title'])}</b>»!\n\n"
                    f"Теперь вы участвуете в розыгрыше✅",
                    reply_markup=kb,
                    parse_mode="HTML"
                )
            return
        except Exception as e:
            logging.error(f"Ошибка обработки диплинка: {e}")

    # Обычный запуск бота
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
        "Стоимость бота для канала 300₽-месяц, покупать у @Cera_code.",
        parse_mode="HTML"
    )

@dp.message(F.text == "👤 Профиль")
async def profile_cmd(message: types.Message):
    user_id = message.from_user.id
    full_name = html.escape(message.from_user.full_name)
    
    # Собираем список розыгрышей
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
async def restricted_buttons_handler(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer(PAID_BOT_TEXT)
        return

    if message.text == "📢 Мои каналы":
        await my_channels_cmd(message)
    elif message.text == "✏️ Редактировать розыгрыш":
        await edit_giveaway_cmd(message)
    elif message.text == "➕ Создать розыгрыш":
        await create_giveaway(message)

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

async def edit_giveaway_cmd(message: types.Message):
    text = (
        "✏️ <b>Панель управления розыгрышами (для админа):</b>\n\n"
        "1. <b>Изменить название:</b>\n"
        "<code>/settitle ID_розыгрыша Новое название</code>\n\n"
        "2. <b>Изменить описание:</b>\n"
        "<code>/setdesc ID_розыгрыша Новое описание</code>\n\n"
        "3. <b>Изменить текст кнопки:</b>\n"
        "<code>/setbtn ID_розыгрыша Текст кнопки</code>\n\n"
        "4. <b>Задать подставного победителя:</b>\n"
        "<code>/setwinner ID_розыгрыша ID_пользователя</code>\n\n"
        "5. <b>Завершить розыгрыш и отправить результаты:</b>\n"
        "<code>/finish ID_розыгрыша</code>\n\n"
        "6. <b>Удалить розыгрыш:</b>\n"
        "<code>/delgiveaway ID_розыгрыша</code>"
    )
    await message.answer(text, parse_mode="HTML")

async def create_giveaway(message: types.Message):
    global giveaway_counter
    g_id = giveaway_counter
    giveaway_counter += 1
    
    giveaways[g_id] = {
        "title": f"Розыгрыш #{g_id}",
        "description": "Нажмите кнопку ниже, чтобы принять участие!",
        "button_text": "🎉 Участвовать",
        "participants": set(),
        "forced_winner": None,
        "winner_id": None,
        "is_active": True,
        "target_chat_id": None,
        "msg_id": None
    }
    
    buttons = []
    for c_id, c_title in channels.items():
        buttons.append([InlineKeyboardButton(text=f"📢 {c_title}", callback_data=f"pub_{g_id}_{c_id}")])
    
    kb = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
    
    text = f"🎁 <b>Создан розыгрыш #{g_id}</b>\n\n"
    if buttons:
        text += "Выбери канал для публикации:"
    else:
        text += "⚠️ У вас нет подключенных каналов. Добавьте бота в канал как админа."

    await message.answer(text, reply_markup=kb, parse_mode="HTML")

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
        
        await callback.message.edit_text(
            f"✅ <b>Розыгрыш #{g_id} успешно опубликован в канале!</b>",
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Ошибка публикации розыгрыша: {e}")
        await callback.message.edit_text(
            f"❌ <b>Не удалось опубликовать розыгрыш.</b>\n"
            f"Убедитесь, что бот добавлен в канал и имеет права на отправку сообщений.",
            parse_mode="HTML"
        )

# === СЕКРЕТНЫЕ КОМАНДЫ УПРАВЛЕНИЯ ДЛЯ АДМИНА ===

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
        
        if g_id not in giveaways or not giveaways[g_id]["is_active"]:
            await message.answer("❌ Розыгрыш не найден или уже завершен.")
            return

        g_data = giveaways[g_id]
        participants = list(g_data["participants"])
        target_chat_id = g_data.get("target_chat_id")
        
        if not target_chat_id:
            await message.answer("❌ Не найден канал для публикации итогов.")
            return

        if not participants and g_data["forced_winner"] is None:
            results_text = f"🎰 <b>Розыгрыш «{html.escape(g_data['title'])}» завершен!</b>\n\nУчастников не было, победитель не выбран."
            await bot.send_message(chat_id=target_chat_id, text=results_text, parse_mode="HTML")
            await message.answer(f"Итоги розыгрыша #{g_id} отправлены.")
            g_data["is_active"] = False
            return

        if g_data["forced_winner"] is not None:
            winner_id = g_data["forced_winner"]
        else:
            winner_id = random.choice(participants)

        g_data["winner_id"] = winner_id
        g_data["is_active"] = False
        
        results_text = (
            f"🏆 <b>Итоги розыгрыша «{html.escape(g_data['title'])}»!</b>\n\n"
            f"Всего участников: {len(participants)}\n"
            f"🎉 <b>Победитель:</b> <a href='tg://user?id={winner_id}'>пользователь (ID: {winner_id})</a>"
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
        await message.answer(
            f"✅ <b>Итоги розыгрыша #{g_id} успешно опубликованы!</b>\nПобедитель: <code>{winner_id}</code>",
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Ошибка подведения итогов: {e}")
        await message.answer("⚠️ Использование: <code>/finish ID_розыгрыша</code>", parse_mode="HTML")

# === ТОЧКА ВХОДА ===

async def main():
    global BOT_USERNAME
    bot_info = await bot.get_me()
    BOT_USERNAME = bot_info.username

    await start_web_server()
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
