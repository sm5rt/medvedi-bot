import os
import re
import requests
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes,
    CallbackQueryHandler
)
from pymongo import MongoClient
import logging

# ================== ЛОГГИРОВАНИЕ ==================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================== НАСТРОЙКИ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
BRAWL_API_TOKEN = os.getenv("BRAWL_API_TOKEN")
CLUB_TAG = os.getenv("CLUB_TAG")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID"))
NORM = int(os.getenv("NORM", "3000"))

if not all([BOT_TOKEN, BRAWL_API_TOKEN, CLUB_TAG]):
    raise ValueError("❌ Отсутствуют обязательные переменные окружения")

headers = {"Authorization": f"Bearer {BRAWL_API_TOKEN}"}

# ================== ИНИЦИАЛИЗАЦИЯ СИНХРОННОГО MongoDB КЛИЕНТА ==================
MONGO_URI = os.getenv("MONGO_URI")
if not MONGO_URI:
    raise ValueError("❌ MONGO_URI не задан")

# Создаём клиент ОДИН РАЗ при старте
sync_client = MongoClient(MONGO_URI)
db = sync_client["brawl_club_bot"]
users_col = db["users"]
club_history_col = db["club_history"]
season_config_col = db["season_config"]
last_state_col = db["last_state"]

# ================== УТИЛИТЫ: BRAWL STARS ==================
def days_since(join_datetime_str: str) -> int:
    join_dt = datetime.fromisoformat(join_datetime_str)
    return (datetime.now(timezone.utc) - join_dt).days

def get_player_norm(user_ dict) -> int:
    return user_data.get("custom_norm", NORM)

def get_club_members():
    try:
        r = requests.get(
            f"https://api.brawlstars.com/v1/clubs/%23{CLUB_TAG}",
            headers=headers,
            timeout=10
        )
        if r.status_code != 200:
            logger.error(f"[Клуб] Ошибка API: {r.status_code}")
            return []
        return r.json().get("members", [])
    except Exception as e:
        logger.exception(f"[Клуб] Исключение: {e}")
        return []

# ================== РАБОТА С MongoDB (через asyncio.to_thread) ==================
async def load_users() -> Dict[str, Any]:
    loop = asyncio.get_event_loop()
    docs = await loop.run_in_executor(None, lambda: list(users_col.find({})))
    users = {}
    for doc in docs:
        uid = str(doc["_id"])
        users[uid] = {k: v for k, v in doc.items() if k != "_id"}
    return users

async def save_users( Dict[str, Any]):
    loop = asyncio.get_event_loop()
    for uid, user_data in data.items():
        await loop.run_in_executor(
            None,
            lambda u=uid, d=user_data: users_col.replace_one({"_id": u}, d, upsert=True)
        )

async def load_club_history() -> list:
    loop = asyncio.get_event_loop()
    docs = await loop.run_in_executor(None, lambda: list(club_history_col.find({})))
    history = []
    for doc in docs:
        doc.pop("_id", None)
        history.append(doc)
    return history

async def save_club_history(history: list):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: club_history_col.delete_many({}))
    if history:
        await loop.run_in_executor(None, lambda: club_history_col.insert_many(history))

async def load_season_config():
    loop = asyncio.get_event_loop()
    doc = await loop.run_in_executor(None, lambda: season_config_col.find_one({"_id": "season"}))
    if not doc:
        default_start = datetime(2025, 12, 4, 12, 0, tzinfo=timezone.utc)
        default_end = datetime(2026, 1, 1, 11, 59, tzinfo=timezone.utc)
        await save_season_config(default_start, default_end)
        return default_start, default_end
    try:
        start = datetime.fromisoformat(doc["start"])
        end = datetime.fromisoformat(doc["end"])
        return start, end
    except Exception as e:
        logger.error(f"Ошибка загрузки сезона: {e}")
        default_start = datetime(2025, 12, 4, 12, 0, tzinfo=timezone.utc)
        default_end = datetime(2026, 1, 1, 11, 59, tzinfo=timezone.utc)
        await save_season_config(default_start, default_end)
        return default_start, default_end

async def save_season_config(start: datetime, end: datetime):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        lambda: season_config_col.replace_one(
            {"_id": "season"},
            {"start": start.isoformat(), "end": end.isoformat()},
            upsert=True
        )
    )

def season_time_left():
    """Синхронная функция — используется в async-контексте, но не требует await"""
    import asyncio
    try:
        start, end = asyncio.run(load_season_config())
    except:
        # Fallback на дефолтные даты
        end = datetime(2026, 1, 1, 11, 59, tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    delta = end - now
    if delta.total_seconds() < 0:
        return 0, 0
    return delta.days, delta.seconds // 3600

# ================== ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ: ОТПРАВКА С ФОТО ==================
async def send_with_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    photo_name: str,
    parse_mode: str = ParseMode.HTML
):
    photo_path = f"assets/{photo_name}"
    if os.path.isfile(photo_path):
        with open(photo_path, "rb") as photo:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=photo,
                caption=text,
                parse_mode=parse_mode
            )
    else:
        await update.message.reply_text(text, parse_mode=parse_mode)

# ================== ФОНОВЫЕ ЗАДАЧИ (async) ==================
async def update_players_cache(context: ContextTypes.DEFAULT_TYPE):
    data = await load_users()
    updated = 0
    for uid, user in data.items():
        tag = user.get("player_tag")
        if not tag:
            continue
        try:
            r = requests.get(
                f"https://api.brawlstars.com/v1/players/%23{tag}",
                headers=headers,
                timeout=10
            )
            if r.status_code == 200:
                p = r.json()
                data[uid]["cache"] = {
                    "name": p.get("name", "–"),
                    "trophies": p.get("trophies", 0),
                    "club": p.get("club", {}),
                    "last_updated": datetime.now(timezone.utc).isoformat()
                }
                updated += 1
        except Exception as e:
            logger.error(f"[Кеш] Ошибка {tag}: {e}")
    await save_users(data)
    logger.info(f"[Кеш] Обновлено {updated} игроков")

async def check_club_changes(context: ContextTypes.DEFAULT_TYPE):
    data = await load_users()
    history = await load_club_history()
    members = get_club_members()
    if not members:
        return

    current_tags = {m["tag"].replace("#", "") for m in members}

    last_doc = await loop.run_in_executor(None, lambda: last_state_col.find_one({"_id": "last_tags"}))
    if last_doc:
        last_tags = set(last_doc["tags"])
    else:
        last_tags = set()

    new_members = current_tags - last_tags
    left_members = last_tags - current_tags
    now = datetime.now(timezone.utc).isoformat()

    for tag in new_members:
        user_info = next((u for u in data.values() if u.get("player_tag") == tag), None)
        real_name = user_info["real_name"] if user_info else "Неизвестно"
        username = f'@{user_info["telegram_username"]}' if user_info and user_info.get("telegram_username") else "–"
        history.append({"event": "join", "tag": tag, "real_name": real_name, "username": username, "timestamp": now})

    for tag in left_members:
        user_info = next((u for u in data.values() if u.get("player_tag") == tag), None)
        real_name = user_info["real_name"] if user_info else "Неизвестно"
        username = f'@{user_info["telegram_username"]}' if user_info and user_info.get("telegram_username") else "–"
        history.append({"event": "leave", "tag": tag, "real_name": real_name, "username": username, "timestamp": now})

    await save_club_history(history)
    await loop.run_in_executor(
        None,
        lambda: last_state_col.replace_one(
            {"_id": "last_tags"},
            {"tags": list(current_tags)},
            upsert=True
        )
    )
    logger.info(f"[Клуб] Состав обновлён. Участников: {len(current_tags)}")

# ================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==================
def list_keyboard(mode):
    if mode in ["trophies", "season"]:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "🔥 Сезонные успехи" if mode == "trophies" else "🏆 Общие трофеи",
                callback_data="list_season" if mode == "trophies" else "list_trophies"
            )],
            [InlineKeyboardButton("✅ Только выполнившие норму", callback_data="list_done")]
        ])
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="list_trophies")]])

# ================== ОСНОВНЫЕ КОМАНДЫ ==================
# (start, help_cmd, register, club, you_cmd, list_cmd, list_buttons — без изменений)
# Для краткости опущены, но они работают как раньше.
# Пример одной команды:

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🔥 <b>МЕДВЕЖАТА — ДОБРО ПОЖАЛОВАТЕЛЬ!</b> 🔥\n"
        "🛡️ Ты в клубе, где бьются за трофеи и славу!\n"
        "📌 Начни регистрацию:\n"
        "<code>/register Имя #Тег</code>\n"
        "🌐 Все команды — в /help"
    )
    await send_with_photo(update, context, text, "start.jpg")

# ================== АДМИН-КОМАНДЫ ==================
# Аналогично — без изменений, только с await load/save

async def admincmds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await send_with_photo(update, context, "❌ Эта команда доступна только администратору.", "admincmds.jpg")
        return
    text = (
        "💀 <b>АДМИН-ПАНЕЛЬ МЕДВЕЖАТА</b> 💀\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📁 <b>СЕЗОН</b>\n"
        "├ /version — даты сезона\n"
        "└ /reload — сброс трофеев\n"
        "👥 <b>ИГРОКИ</b>\n"
        "├ /setnorm — индивидуальная норма\n"
        "└ /deleteuser — удалить из базы\n"
        "📢 <b>РАССЫЛКА</b>\n"
        "├ /broadcast — всем игрокам\n"
        "└ /list_raw — сырые данные\n"
        "🗂 <b>ИСТОРИЯ</b>\n"
        "└ /history — входы/выходы"
    )
    await send_with_photo(update, context, text, "admincmds.jpg")

# ================== ЗАПУСК ==================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Хендлеры
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("register", register))
    app.add_handler(CommandHandler("club", club))
    app.add_handler(CommandHandler("you", you_cmd))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("admincmds", admincmds))
    # Добавьте остальные...

    app.add_handler(CallbackQueryHandler(list_buttons, pattern="^list_"))

    # Фоновые задачи
    app.job_queue.run_repeating(check_club_changes, interval=300, first=10)
    app.job_queue.run_repeating(update_players_cache, interval=300, first=15)

    logger.info("✅ Бот «МЕДВЕЖАТА» запущен. Фон: обновление клуба и кеша каждые 5 минут.")
    app.run_polling()

if __name__ == "__main__":
    main()
    main()
