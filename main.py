import asyncio
import logging
import random
import html
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    ReplyKeyboardMarkup, 
    KeyboardButton, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    CallbackQuery
)

# === НАСТРОЙКИ ===
BOT_TOKEN = "ТВОЙ_ТОКЕН_БОТА"
ADMIN_ID = 123456789  # Укажи свой Telegram ID

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Хранилище данных (в оперативной памяти)
giveaways = {}
giveaway_counter = 1

# === КЛАВИАТУРЫ ===
main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Создать розыгрыш"), KeyboardButton(text="✏️ Редактировать розыгрыш")],
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="ℹ️ Информация")]
    ],
    resize_keyboard=True
)

# === ОБРАБОТЧИКИ КОМАНД И МЕНЮ ===

@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    welcome_text = (
        "🪁 <b>Добро пожаловать!</b>\n\n"
        "Это бот для создания и проведения розыгрышей в Telegram.\n\n"
        "✨ Создавай розыгрыши и делись ссылкой!\n\n"
        "Выбери действие в меню ниже 👇"
    )
    await message.answer(welcome_text, reply_markup=main_menu, parse_mode="HTML")

@dp.message(F.text == "ℹ️ Информация")
async def info_cmd(message: types.Message):
    await message.answer("ℹ️ Этот бот помогает легко проводить розыгрыши среди ваших подписчиков.")

@dp.message(F.text == "👤 Профиль")
async def profile_cmd(message: types.Message):
    user_id = message.from_user.id
    # html.escape защищает от падения бота, если у юзера в имени есть спецсимволы (<, >, &)
    full_name = html.escape(message.from_user.full_name)
    
    text = f"👤 <b>Ваш профиль</b>\nID: <code>{user_id}</code>\nИмя: {full_name}"
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
        "2. Завершить розыгрыш и подвести итоги:\n"
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
        "is_active": True
    }
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎉 Участвовать", callback_data=f"join_{g_id}")]
    ])
    
    await message.answer(
        f"🎁 <b>Розыгрыш #{g_id}</b>\n\n"
        f"Нажмите кнопку ниже, чтобы принять участие!",
        reply_markup=kb,
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

# 1. Задать подставного победителя: /setwinner <id_розыгрыша> <id_пользователя>
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

# 2. Скрытое подведение итогов админом: /finish <id_розыгрыша>
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
        
        if not participants and g_data["forced_winner"] is None:
            await message.answer(f"🎰 Розыгрыш #{g_id} завершен, но участников не было.")
            g_data["is_active"] = False
            return

        # Логика определения победителя
        if g_data["forced_winner"] is not None:
            winner_id = g_data["forced_winner"]
        else:
            winner_id = random.choice(participants)

        g_data["is_active"] = False
        
        await message.answer(
            f"🏆 <b>Итоги розыгрыша #{g_id}!</b>\n\n"
            f"Всего участников: {len(participants)}\n"
            f"🎉 <b>Победитель:</b> <a href='tg://user?id={winner_id}'>пользователь (ID: {winner_id})</a>",
            parse_mode="HTML"
        )
    except Exception:
        await message.answer("⚠️ Ошибка. Формат команды: <code>/finish ID_розыгрыша</code>", parse_mode="HTML")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
