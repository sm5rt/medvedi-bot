import os
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any

import requests
from pymongo import MongoClient
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes
)

# ================== НАСТРОЙКИ ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
BRAWL_API_TOKEN = os.getenv("BRAWL_API_TOKEN")
CLUB_TAG = os.getenv("CLUB_TAG")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))
NORM = int(os.getenv("NORM", "3000"))
MONGO_URI = os.getenv("MONGO_URI")

if not all([BOT_TOKEN, BRAWL_API_TOKEN, CLUB_TAG, MONGO_URI]):
    raise RuntimeError("❌ Не заданы переменные окружения")

headers = {"Authorization": f"Bearer {BRAWL_API_TOKEN}"}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("BRAWL-BOT")

# ================== БАЗА ДАННЫХ ==================
mongo = MongoClient(MONGO_URI)
db = mongo["brawl_club_bot"]
users_col = db["users"]
history_col = db["history"]
state_col = db["state"]

# ================== ВСПОМОГАТЕЛЬНОЕ ==================
def get_player_norm(user: Dict[str, Any]) -> int:
    return user.get("custom_norm", NORM)


def days_since(iso: str) -> int:
    return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).days


def get_club_members():
    try:
        r = requests.get(
            f"https://api.brawlstars.com/v1/clubs/%23{CLUB_TAG}",
            headers=headers,
            timeout=10
        )
        if r.status_code != 200:
            return []
        return r.json().get("members", [])
    except Exception:
        return []


async def load_users():
    return {str(u["_id"]): u for u in users_col.find()}


async def save_users(data: Dict[str, Any]):
    for uid, user in data.items():
        users_col.replace_one({"_id": uid}, user, upsert=True)


# ================== КОМАНДЫ ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🐻 Добро пожаловать в клуб *МЕДВЕЖАТА!*\n\n"
        "📌 /register Имя #Тег\n"
        "📊 /club — твой профиль\n"
        "📈 /list — рейтинг\n",
        parse_mode=ParseMode.MARKDOWN
    )


async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 2:
        await update.message.reply_text("Используй: /register Имя #TAG")
        return

    real_name, tag = context.args
    tag = tag.replace("#", "").upper()

    r = requests.get(f"https://api.brawlstars.com/v1/players/%23{tag}", headers=headers)
    if r.status_code != 200:
        await update.message.reply_text("❌ Тег не найден в Brawl Stars.")
        return

    player = r.json()
    uid = str(update.effective_user.id)

    users = await load_users()
    users[uid] = {
        "real_name": real_name,
        "player_tag": tag,
        "telegram_username": update.effective_user.username,
        "join_bot": datetime.now(timezone.utc).isoformat(),
        "season_start_trophies": player.get("trophies", 0),
        "cache": {
            "name": player.get("name"),
            "trophies": player.get("trophies"),
            "club": player.get("club", {})
        }
    }

    await save_users(users)
    await update.message.reply_text("✅ Ты успешно зарегистрирован!")


async def club(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = await load_users()
    uid = str(update.effective_user.id)

    if uid not in users:
        await update.message.reply_text("❌ Ты не зарегистрирован.")
        return

    u = users[uid]
    cache = u.get("cache", {})
    trophies = cache.get("trophies", 0)
    progress = trophies - u.get("season_start_trophies", 0)
    norm = get_player_norm(u)
    percent = int(progress / norm * 100) if norm > 0 else 0

    await update.message.reply_text(
        f"🏅 <b>{cache.get('name')}</b>\n"
        f"🏆 Трофеи: {trophies}\n"
        f"📈 Прогресс: {progress} / {norm} ({percent}%)",
        parse_mode=ParseMode.HTML
    )


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = await load_users()
    rows = []

    for u in users.values():
        cache = u.get("cache")
        if not cache:
            continue
        trophies = cache.get("trophies", 0)
        progress = trophies - u.get("season_start_trophies", 0)
        rows.append((cache.get("name"), trophies, progress))

    rows.sort(key=lambda x: x[1], reverse=True)

    text = "🏆 ТОП ИГРОКОВ\n\n"
    for i, r in enumerate(rows[:20], 1):
        text += f"{i}. {r[0]} — {r[1]} 🏆 (+{r[2]})\n"

    await update.message.reply_text(text)


# ================== ЗАПУСК ==================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("register", register))
    app.add_handler(CommandHandler("club", club))
    app.add_handler(CommandHandler("list", list_cmd))

    logger.info("✅ Бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()
