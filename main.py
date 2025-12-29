import os
import re
import requests
from datetime import datetime, timezone
from typing import Dict
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters
)
from pymongo import MongoClient

# ================== НАСТРОЙКИ ==================
BOT_TOKEN = os.getenv("BOT_TOKEN", "8550823542:AAEl-zvRJF8Yhah1L-pXlDuW-TewpliazYk")
BRAWL_API_TOKEN = os.getenv("BRAWL_API_TOKEN", "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiIsImtpZCI6IjI4YTMxOGY3LTAwMDAtYTFlYi03ZmExLTJjNzQzM2M2Y2NhNSJ9.eyJpc3MiOiJzdXBlcmNlbGwiLCJhdWQiOiJzdXBlcmNlbGw6Z2FtZWFwaSIsImp0aSI6IjU2NmYyODU4LTNlNmEtNDk5Yi1iYzU1LTU1M2Q4ZDEyMzk2NyIsImlhdCI6MTc2NjkxODQzMiwic3ViIjoiZGV2ZWxvcGVyL2YzNmNkOTMyLTU0NTktNGNiNy0yMzc3LWQ3NmZiMWZlMmZlZCIsInNjb3BlcyI6WyJicmF3bHN0YXJzIl0sImxpbWl0cyI6W3sidGllciI6ImRldmVsb3Blci9zaWx2ZXIiLCJ0eXBlIjoidGhyb3R0bGluZyJ9LHsiY2lkcnMiOlsiMTQ3LjQ1LjIxNy4zOSJdLCJ0eXBlIjoiY2xpZW50In1dfQ.Dm_WrpEo9dNs9-yV0ZIUO4V5D068AnWd28pfLjX3vl6MCuxcBhxL6Vm0D_JmnrKF4mYFgeektNjC1paIlmwDsQ")
CLUB_TAG = os.getenv("CLUB_TAG", "C2GPGU90")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "1309867056"))

# ================== ПОДКЛЮЧЕНИЕ К MONGODB ==================
MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://kolaprokin0_db_user:<Tankist123.>@medvedi-bot.z5qlfsb.mongodb.net/?appName=medvedi-bot")
client = MongoClient(MONGO_URI)
db = client["medvedi_bot"]
users_col = db["users"]
club_history_col = db["club_history"]
config_col = db["config"]

NORM = 3000

headers = {
    "Authorization": f"Bearer {BRAWL_API_TOKEN}"
}

# ================== РАБОТА С БД ==================

def load_users() -> dict:
    users = {}
    for doc in users_col.find():
        uid = str(doc["_id"])
        doc.pop("_id", None)
        users[uid] = doc
    return users

def save_users( dict):
    users_col.delete_many({})
    if not 
        return
    docs = [{"_id": uid, **user} for uid, user in data.items()]
    users_col.insert_many(docs)

def load_club_history() -> list:
    doc = club_history_col.find_one({"_id": "history"})
    return doc.get("data", []) if doc else []

def save_club_history(history: list):
    club_history_col.update_one(
        {"_id": "history"},
        {"$set": {"data": history}},
        upsert=True
    )

def load_season_config():
    doc = config_col.find_one({"_id": "season_config"})
    if not doc:
        default_start = datetime(2025, 12, 4, 12, 0, tzinfo=timezone.utc)
        default_end = datetime(2026, 1, 1, 11, 59, tzinfo=timezone.utc)
        save_season_config(default_start, default_end)
        return default_start, default_end
    start = datetime.fromisoformat(doc["start"])
    end = datetime.fromisoformat(doc["end"])
    return start, end

def save_season_config(start: datetime, end: datetime):
    config_col.update_one(
        {"_id": "season_config"},
        {"$set": {"start": start.isoformat(), "end": end.isoformat()}},
        upsert=True
    )

def get_last_club_state():
    doc = config_col.find_one({"_id": "last_club_state"})
    return set(doc.get("tags", [])) if doc else set()

def save_last_club_state(tags: set):
    config_col.update_one(
        {"_id": "last_club_state"},
        {"$set": {"tags": list(tags)}},
        upsert=True
    )

# ================== УТИЛИТЫ: BRAWL STARS ==================
def get_player_norm(user_ dict) -> int:
    return user_data.get("custom_norm", NORM)

def days_since(join_datetime_str: str) -> int:
    join_dt = datetime.fromisoformat(join_datetime_str)
    return (datetime.now(timezone.utc) - join_dt).days

def get_club_members():
    try:
        r = requests.get(
            f"https://api.brawlstars.com/v1/clubs/%23{CLUB_TAG}",
            headers=headers,
            timeout=10
        )
        if r.status_code != 200:
            print(f"[Клуб] Ошибка API: {r.status_code}")
            return []
        return r.json().get("members", [])
    except Exception as e:
        print(f"[Клуб] Исключение: {e}")
        return []

# ================== ПРОСТАЯ ОТПРАВКА ТЕКСТА ==================
async def send_with_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    photo_name: str,
    parse_mode: str = ParseMode.HTML
):
    await update.message.reply_text(text, parse_mode=parse_mode)

# ================== ФОНОВЫЕ ЗАДАЧИ ==================
async def update_players_cache(context: ContextTypes.DEFAULT_TYPE):
    data = load_users()
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
            print(f"[Кеш] Ошибка {tag}: {e}")
    save_users(data)
    print(f"[Кеш] Обновлено {updated} игроков")

async def check_club_changes(context: ContextTypes.DEFAULT_TYPE):
    data = load_users()
    history = load_club_history()
    members = get_club_members()
    if not members:
        return
    current_tags = {m["tag"].replace("#", "") for m in members}
    last_tags = get_last_club_state()
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
    save_club_history(history)
    save_last_club_state(current_tags)
    print(f"[Клуб] Состав обновлён. Участников: {len(current_tags)}")

# ================== КНОПКИ ==================
def list_keyboard(mode):
    if mode in ["trophies", "season"]:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔥 Сезонные успехи" if mode == "trophies" else "🏆 Общие трофеи",
                    callback_data="list_season" if mode == "trophies" else "list_trophies"
                )
            ],
            [
                InlineKeyboardButton("✅ Только выполнившие норму", callback_data="list_done")
            ]
        ])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Назад", callback_data="list_trophies")]
    ])

# ================== ОСНОВНЫЕ КОМАНДЫ ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🔥 <b>МЕДВЕЖАТА — ДОБРО ПОЖАЛОВАТЕЛЬ!</b> 🔥\n"
        "🛡️ Ты в клубе, где бьются за трофеи и славу!\n"
        "📌 Начни регистрацию:\n"
        "<code>/register Имя #Тег</code>\n"
        "🌐 Все команды — в /help"
    )
    await send_with_photo(update, context, text, "start.jpg")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🌌 <b>КОМАНДЫ МЕДВЕЖАТА</b> 🌌\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📝 <b>/register</b> — зарегистрироваться\n"
        "👤 <b>/club</b> — твой профиль\n"
        "👁️ <b>/you @username</b> — посмотреть другого\n"
        "📊 <b>/list</b> — рейтинг игроков\n"
        "❓ <b>/help</b> — это меню\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔒 Админам: <code>/admincmds</code>"
    )
    if update.effective_user.id == ADMIN_USER_ID:
        text += "\n🛡️ <b>Ты — админ!</b> Все команды доступны."
    await send_with_photo(update, context, text, "help.jpg")

async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) != 2:
        await send_with_photo(update, context, "❌ Формат:\n<code>/register ИмяВЖизни #Тег</code>", "register.jpg")
        return
    real_name, tag = args
    tag = tag.upper().replace("#", "")
    members = get_club_members()
    if not any(m["tag"].replace("#", "") == tag for m in members):
        await send_with_photo(update, context, "❌ Этот тег не найден в клубе «МЕДВЕЖАТА».", "register.jpg")
        return
    data = load_users()
    user = update.effective_user
    uid = str(user.id)
    r = requests.get(f"https://api.brawlstars.com/v1/players/%23{tag}", headers=headers, timeout=10)
    if r.status_code != 200:
        await send_with_photo(update, context, "❌ Не удалось получить данные игрока.", "register.jpg")
        return
    player = r.json()
    if uid not in 
        data[uid] = {
            "real_name": real_name,
            "player_tag": tag,
            "join_bot": datetime.now(timezone.utc).isoformat(),
            "telegram_username": user.username,
            "season_start_trophies": player.get("trophies", 0),
            "cache": {
                "name": player.get("name", "–"),
                "trophies": player.get("trophies", 0),
                "club": player.get("club", {}),
                "last_updated": datetime.now(timezone.utc).isoformat()
            }
        }
        save_users(data)
    text = (
        "💥 <b>ПРИВЕТ, МЕДВЕЖОНОК!</b> 💥\n"
        "Ты официально в клубе!\n"
        "🎯 Теперь ты можешь:\n"
        "• Смотреть свой прогресс — <code>/club</code>\n"
        "• Следить за сезоном — <code>/list</code>\n"
        "🔥 Вперёд к победам!"
    )
    await send_with_photo(update, context, text, "register.jpg")

async def club(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    data = load_users()
    if uid not in 
        await send_with_photo(update, context, "❌ Сначала зарегистрируйся в клубе: <code>/register</code>", "club.jpg")
        return
    u = data[uid]
    cache = u.get("cache")
    if not cache:
        await send_with_photo(update, context, "⏳ Данные ещё не загружены. Попробуй через минуту.", "club.jpg")
        return
    trophies = cache["trophies"]
    progress = trophies - u["season_start_trophies"]
    norm = get_player_norm(u)
    percent = min(int(progress / norm * 100), 100)
    done = "✅ Да" if progress >= norm else "❌ Нет"
    days, hours = season_time_left()
    days_in_bot = days_since(u["join_bot"])
    text = (
        "🛡️ <b>ПРОФИЛЬ МЕДВЕЖОНКА</b> 🛡️\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Имя:</b> {update.effective_user.first_name}\n"
        f"👨‍💼 <b>В жизни:</b> {u['real_name']}\n"
        f"🆔 <b>ID:</b> {uid}\n"
        f"📅 <b>В боте:</b> {days_in_bot} дн.\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🎮 <b>ИГРА:</b>\n"
        f"🏷️ <b>Ник:</b> {cache['name']}\n"
        f"#️⃣ <b>Тег:</b> #{u['player_tag']}\n"
        f"🏠 <b>Клуб:</b> {cache['club'].get('name', '-')} ❮МЕДВЕЖАТА❯\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📈 <b>СЕЗОННЫЙ ПРОГРЕСС</b>\n"
        f"🏆 <b>Норма:</b> {norm}\n"
        f"📈 <b>Прогресс:</b> +{progress} ({percent}%)\n"
        f"✅ <b>Выполнено:</b> {done}\n"
        f"⏳ <b>До конца сезона:</b> {days} дн. {hours} ч."
    )
    await send_with_photo(update, context, text, "club.jpg")

async def you_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_users()
    if not 
        await send_with_photo(update, context, "❌ В боте ещё никто не зарегистрирован.", "you.jpg")
        return
    if not context.args:
        await send_with_photo(update, context, "🔍 Используйте:\n<code>/you @username</code>\nили\n<code>/you #Тег</code>", "you.jpg")
        return
    query = context.args[0].strip()
    target_user = None
    if query.startswith("@"):
        username = query[1:].lower()
        for uid, u in data.items():
            if u.get("telegram_username", "").lower() == username:
                target_user = u
                break
    elif query.startswith("#"):
        tag = query[1:].upper()
        for uid, u in data.items():
            if u.get("player_tag", "") == tag:
                target_user = u
                break
    if not target_user or "cache" not in target_user:
        await send_with_photo(update, context, "❌ Игрок не найден или данные не загружены.", "you.jpg")
        return
    cache = target_user["cache"]
    trophies = cache["trophies"]
    progress = trophies - target_user["season_start_trophies"]
    norm = get_player_norm(target_user)
    percent = min(int(progress / norm * 100), 100)
    done = "✅ Да" if progress >= norm else "❌ Нет"
    days, hours = season_time_left()
    days_in_bot = days_since(target_user["join_bot"])
    real_name = target_user["real_name"]
    username_display = f"@{target_user.get('telegram_username')}" if target_user.get("telegram_username") else "–"
    text = (
        "🔭 <b>ПРОФИЛЬ МЕДВЕЖОНКА</b> 🔭\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👨‍💼 <b>Имя в жизни:</b> {real_name}\n"
        f"🔖 <b>Username:</b> {username_display}\n"
        f"📅 <b>В боте:</b> {days_in_bot} дн.\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🎮 <b>ИГРА:</b>\n"
        f"🏷️ <b>Ник:</b> {cache['name']}\n"
        f"#️⃣ <b>Тег:</b> #{target_user['player_tag']}\n"
        f"🏠 <b>Клуб:</b> {cache['club'].get('name', '-')} ❮МЕДВЕЖАТА❯\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📈 <b>СЕЗОННЫЙ ПРОГРЕСС</b>\n"
        f"🏆 <b>Норма:</b> {norm}\n"
        f"📈 <b>Прогресс:</b> +{progress} ({percent}%)\n"
        f"✅ <b>Выполнено:</b> {done}\n"
        f"⏳ <b>До конца сезона:</b> {days} дн. {hours} ч."
    )
    await send_with_photo(update, context, text, "you.jpg")

async def build_list(sort_mode):
    data = load_users()
    players = []
    for uid, u in data.items():
        cache = u.get("cache")
        if not cache:
            continue
        trophies = cache["trophies"]
        progress = trophies - u["season_start_trophies"]
        norm = get_player_norm(u)
        percent = min(int(progress / norm * 100), 100)
        done = progress >= norm
        name = cache["name"]
        if u.get("telegram_username"):
            name = f'<a href="https://t.me/{u["telegram_username"]}">{name}</a>'
        players.append({
            "name": name,
            "trophies": trophies,
            "progress": progress,
            "percent": percent,
            "done": done
        })
    if sort_mode == "season":
        players.sort(key=lambda x: x["progress"], reverse=True)
        title = "🔥 <b>ТОП МЕДВЕЖАТА — СЕЗОННЫЙ РЕЙТИНГ</b> 🔥\n"
    elif sort_mode == "done":
        players = [p for p in players if p["done"]]
        players.sort(key=lambda x: x["progress"], reverse=True)
        title = "✅ <b>ВЫПОЛНИЛИ НОРМУ — ГЕРОИ КЛУБА</b> ✅\n"
    else:
        players.sort(key=lambda x: x["trophies"], reverse=True)
        title = "🏆 <b>ТОП МЕДВЕЖАТА — ОБЩИЕ ТРОФЕИ</b> 🏆\n"
    lines = []
    for i, p in enumerate(players[:30], 1):
        medal = "🥇" if i <= 3 else "🥈" if i <= 6 else "🥉" if i <= 10 else "  "
        status = "✅" if p["done"] else "❌"
        lines.append(
            f"{medal} <b>{i}) {p['name']}</b> — {p['trophies']} 🏆\n"
            f"   📈 +{p['progress']} | {p['percent']}% {status}"
        )
    return title + "\n".join(lines)

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = await build_list("trophies")
    msg = await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=list_keyboard("trophies")
    )
    context.user_data["list_message_id"] = msg.message_id

async def list_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    msg_id = context.user_data.get("list_message_id")
    if msg_id:
        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=msg_id
            )
        except:
            pass
    mode = "trophies"
    if query.data == "list_season":
        mode = "season"
    elif query.data == "list_done":
        mode = "done"
    text = await build_list(mode)
    new_msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=list_keyboard(mode)
    )
    context.user_data["list_message_id"] = new_msg.message_id

# ================== АДМИН-КОМАНДЫ ==================
async def version(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await send_with_photo(update, context, "❌ Нет доступа.", "admincmds.jpg")
        return
    args = context.args
    if len(args) == 0:
        SEASON_START, SEASON_END = get_season_dates()
        text = (
            "📅 <b>ТЕКУЩИЙ СЕЗОН МЕДВЕЖАТА</b>\n"
            f"Начало: {SEASON_START.strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"Конец:   {SEASON_END.strftime('%Y-%m-%d %H:%M UTC')}\n"
            "<i>Чтобы изменить — отправьте:</i>\n"
            "<code>/version ГГГГ-ММ-ДД ГГГГ-ММ-ДД</code>"
        )
        await send_with_photo(update, context, text, "admincmds.jpg")
        return
    if len(args) != 2:
        await update.message.reply_text("❌ Используйте: <code>/version 2025-12-04 2026-01-01</code>", parse_mode=ParseMode.HTML)
        return
    try:
        start_str, end_str = args
        start_date = datetime.fromisoformat(start_str).replace(tzinfo=timezone.utc)
        end_date = datetime.fromisoformat(end_str).replace(tzinfo=timezone.utc)
        start_date = start_date.replace(hour=12, minute=0, second=0, microsecond=0)
        end_date = end_date.replace(hour=11, minute=59, second=0, microsecond=0)
        if start_date >= end_date:
            await update.message.reply_text("❌ Дата начала должна быть раньше даты окончания.")
            return
        save_season_config(start_date, end_date)
        text = (
            "✅ <b>СЕЗОН ОБНОВЛЁН!</b>\n"
            f"Началo: {start_date.strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"Конец:   {end_date.strftime('%Y-%m-%d %H:%M UTC')}"
        )
        await send_with_photo(update, context, text, "admincmds.jpg")
    except ValueError:
        await update.message.reply_text(
            "❌ Ошибка формата даты.\n"
            "Пример: <code>/version 2025-12-04 2026-01-01</code>",
            parse_mode=ParseMode.HTML
        )

async def reload_season(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await send_with_photo(update, context, "❌ Доступ запрещён.", "admincmds.jpg")
        return
    data = load_users()
    updated = 0
    for uid, user in data.items():
        tag = user.get("player_tag")
        if not tag:
            continue
        r = requests.get(f"https://api.brawlstars.com/v1/players/%23{tag}", headers=headers, timeout=10)
        if r.status_code == 200:
            trophies = r.json().get("trophies", 0)
            data[uid]["season_start_trophies"] = trophies
            updated += 1
    save_users(data)
    await send_with_photo(update, context, f"✅ Обновлено {updated} игроков: трофеи сезона сброшены.", "admincmds.jpg")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await send_with_photo(update, context, "❌ Доступ запрещён.", "admincmds.jpg")
        return
    if not context.args:
        await update.message.reply_text("Используйте: <code>/broadcast Текст сообщения</code>", parse_mode=ParseMode.HTML)
        return
    message = "📣 <b>ОПОВЕЩЕНИЕ ОТ АДМИНИСТРАЦИИ МЕДВЕЖАТА:</b>\n" + " ".join(context.args)
    data = load_users()
    success = 0
    for uid in data.keys():
        try:
            await context.bot.send_message(chat_id=int(uid), text=message, parse_mode=ParseMode.HTML)
            success += 1
        except Exception as e:
            print(f"Не удалось отправить {uid}: {e}")
    await update.message.reply_text(f"✅ Сообщение отправлено {success} пользователям.")

async def list_raw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await send_with_photo(update, context, "❌ Доступ запрещён.", "admincmds.jpg")
        return
    data = load_users()
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if len(text) > 4096:
        await update.message.reply_document(document=bytes(text, "utf-8"), filename="users_raw.json")
    else:
        await update.message.reply_text(f"<pre>{text}</pre>", parse_mode=ParseMode.HTML)

async def setnorm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await send_with_photo(update, context, "❌ Только администратор может использовать эту команду.", "admincmds.jpg")
        return
    if len(context.args) != 2:
        await update.message.reply_text(
            "Используйте:\n"
            "<code>/setnorm #ТЕГ 2500</code>\n"
            "или\n"
            "<code>/setnorm @username 3000</code>",
            parse_mode=ParseMode.HTML
        )
        return
    target_query, norm_str = context.args
    try:
        new_norm = int(norm_str)
        if new_norm < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Норма должна быть целым неотрицательным числом.")
        return
    data = load_users()
    target_uid = None
    target_info = None
    if target_query.startswith("#"):
        tag = target_query[1:].upper()
        for uid, user in data.items():
            if user.get("player_tag") == tag:
                target_uid = uid
                target_info = user
                break
    elif target_query.startswith("@"):
        username = target_query[1:].lower()
        for uid, user in data.items():
            if user.get("telegram_username", "").lower() == username:
                target_uid = uid
                target_info = user
                break
    else:
        await update.message.reply_text("❌ Укажите тег (#ТЕГ) или username (@name).")
        return
    if not target_uid:
        await update.message.reply_text("❌ Игрок не найден в боте.")
        return
    if new_norm == NORM:
        target_info.pop("custom_norm", None)
    else:
        target_info["custom_norm"] = new_norm
    data[target_uid] = target_info
    save_users(data)
    display_name = target_info.get("real_name", "–")
    if target_info.get("telegram_username"):
        display_name += f" (@{target_info['telegram_username']})"
    if "player_tag" in target_info:
        display_name += f" (#{target_info['player_tag']})"
    await send_with_photo(
        update, context,
        f"✅ Установлена индивидуальная норма для:\n"
        f"{display_name}\n"
        f"Новая норма: {new_norm} кубков",
        "admincmds.jpg"
    )

async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await send_with_photo(update, context, "❌ Эта команда доступна только администратору.", "admincmds.jpg")
        return
    history = load_club_history()
    if not history:
        await send_with_photo(update, context, "🗂 История пуста.", "admincmds.jpg")
        return
    lines = []
    for entry in reversed(history[-50:]):
        dt = datetime.fromisoformat(entry["timestamp"]).strftime("%d.%m %H:%M")
        event = "📥 Вход" if entry["event"] == "join" else "📤 Выход"
        name = entry["real_name"] or "Неизвестно"
        username = entry["username"] or "–"
        tag = entry["tag"]
        lines.append(f"{dt} | {event} | {name} ({username}) | #{tag}")
    text = "📋 <b>ИСТОРИЯ ВХОДОВ/ВЫХОДОВ ИЗ МЕДВЕЖАТА</b>\n" + "\n".join(lines)
    await send_with_photo(update, context, text, "admincmds.jpg")

async def deleteuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await send_with_photo(update, context, "❌ Только администратор может использовать эту команду.", "admincmds.jpg")
        return
    if not context.args:
        await update.message.reply_text(
            "Используйте:\n"
            "/deleteuser 123456789  — по Telegram ID\n"
            "/deleteuser #ТЕГ        — по тегу Brawl Stars\n"
            "/deleteuser @username   — по Telegram username"
        )
        return
    query = context.args[0].strip()
    data = load_users()
    target_uid = None
    target_info = None
    if query.isdigit():
        if query in 
            target_uid = query
            target_info = data[query]
    elif query.startswith("#"):
        tag = query[1:].upper()
        for uid, user in data.items():
            if user.get("player_tag") == tag:
                target_uid = uid
                target_info = user
                break
    elif query.startswith("@"):
        username = query[1:].lower()
        for uid, user in data.items():
            if user.get("telegram_username", "").lower() == username:
                target_uid = uid
                target_info = user
                break
    else:
        await update.message.reply_text("❌ Неверный формат. Укажите ID, #ТЕГ или @username.")
        return
    if not target_uid:
        await update.message.reply_text("❌ Пользователь не найден в базе.")
        return
    real_name = target_info.get("real_name", "–")
    username = target_info.get("telegram_username")
    tag = target_info.get("player_tag", "–")
    display = f"{real_name}"
    if username:
        display += f" (@{username})"
    if tag:
        display += f" (#{tag})"
    del data[target_uid]
    save_users(data)
    await send_with_photo(
        update, context,
        f"🗑️ Пользователь удалён из базы:\n"
        f"{display}\n"
        f"(ID: {target_uid})",
        "admincmds.jpg"
    )

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
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("register", register))
    app.add_handler(CommandHandler("club", club))
    app.add_handler(CommandHandler("you", you_cmd))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("version", version))
    app.add_handler(CommandHandler("history", history_cmd))
    app.add_handler(CommandHandler("admincmds", admincmds))
    app.add_handler(CommandHandler("reload", reload_season))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("list_raw", list_raw))
    app.add_handler(CommandHandler("setnorm", setnorm))
    app.add_handler(CommandHandler("deleteuser", deleteuser))
    app.add_handler(CallbackQueryHandler(list_buttons, pattern="^list_"))
    app.job_queue.run_repeating(check_club_changes, interval=300, first=10)
    app.job_queue.run_repeating(update_players_cache, interval=300, first=15)
    print("✅ Бот «МЕДВЕЖАТА» запущен. Данные хранятся в MongoDB.")
    app.run_polling()

if __name__ == "__main__":
    main()