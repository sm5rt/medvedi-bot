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
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))
NORM = int(os.getenv("NORM", "3000"))

if not all([BOT_TOKEN, BRAWL_API_TOKEN, CLUB_TAG]):
    logger.error("❌ Отсутствуют обязательные переменные окружения")
    raise ValueError("❌ Отсутствуют обязательные переменные окружения")

headers = {"Authorization": f"Bearer {BRAWL_API_TOKEN}"}
logger.info(f"✅ Загружены настройки. CLUB_TAG: {CLUB_TAG}")

# ================== MongoDB: СИНХРОННЫЙ КЛИЕНТ ==================
MONGO_URI = os.getenv("MONGO_URI")
if not MONGO_URI:
    logger.error("❌ MONGO_URI не задан")
    raise ValueError("❌ MONGO_URI не задан")

try:
    mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    mongo_client.admin.command('ping')
    logger.info("✅ Успешное подключение к MongoDB Atlas")
except Exception as e:
    logger.error(f"❌ Ошибка подключения к MongoDB: {e}")
    raise

db = mongo_client["brawl_club_bot"]
users_col = db["users"]
club_history_col = db["club_history"]
season_config_col = db["season_config"]
last_state_col = db["last_state"]

# ================== УТИЛИТЫ ==================
def days_since(join_datetime_str: str) -> int:
    try:
        join_dt = datetime.fromisoformat(join_datetime_str)
        return (datetime.now(timezone.utc) - join_dt).days
    except Exception as e:
        logger.error(f"Ошибка вычисления дней с регистрации: {e}")
        return 0

def get_player_norm(user_data: dict) -> int:
    return user_data.get("custom_norm", NORM)

def get_club_members():
    try:
        # ИСПРАВЛЕНО: Убраны лишние пробелы в URL
        r = requests.get(
            f"https://api.brawlstars.com/v1/clubs/%23{CLUB_TAG}",
            headers=headers,
            timeout=10
        )
        logger.info(f"Клуб API запрос: https://api.brawlstars.com/v1/clubs/%23{CLUB_TAG}")
        logger.info(f"Клуб API статус: {r.status_code}")
        
        if r.status_code != 200:
            logger.error(f"[Клуб] Ошибка API: {r.status_code}, ответ: {r.text}")
            return []
        
        club_data = r.json()
        logger.info(f"Клуб найден: {club_data.get('name', 'Неизвестно')}")
        members = club_data.get("members", [])
        logger.info(f"Получено участников клуба: {len(members)}")
        
        # Отладка: выводим первые 3 тега для проверки
        sample_tags = [m.get("tag", "") for m in members[:3]]
        logger.info(f"Примеры тегов в клубе: {sample_tags}")
        
        return members
    except Exception as e:
        logger.exception(f"[Клуб] Исключение: {e}")
        return []

def season_time_left():
    try:
        doc = season_config_col.find_one({"_id": "season"})
        if not doc:
            end = datetime(2026, 1, 1, 11, 59, tzinfo=timezone.utc)
        else:
            try:
                end = datetime.fromisoformat(doc["end"])
            except:
                end = datetime(2026, 1, 1, 11, 59, tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = end - now
        if delta.total_seconds() < 0:
            return 0, 0
        return delta.days, delta.seconds // 3600
    except Exception as e:
        logger.error(f"Ошибка в season_time_left: {e}")
        return 0, 0

# ================== РАБОТА С БД (через asyncio.to_thread) ==================
async def load_users() -> Dict[str, Any]:
    try:
        loop = asyncio.get_event_loop()
        docs = await loop.run_in_executor(None, lambda: list(users_col.find({})))
        users = {}
        for doc in docs:
            uid = str(doc["_id"])
            users[uid] = {k: v for k, v in doc.items() if k != "_id"}
        return users
    except Exception as e:
        logger.error(f"Ошибка загрузки пользователей: {e}")
        return {}

async def save_users(data: Dict[str, Any]) -> None:
    try:
        loop = asyncio.get_event_loop()
        for uid, user_data in data.items():
            await loop.run_in_executor(
                None,
                lambda u=uid, d=user_data: users_col.replace_one({"_id": u}, d, upsert=True)
            )
        logger.info(f"✅ Сохранено пользователей: {len(data)}")
    except Exception as e:
        logger.error(f"Ошибка сохранения пользователей: {e}")

async def load_club_history() -> list:
    try:
        loop = asyncio.get_event_loop()
        docs = await loop.run_in_executor(None, lambda: list(club_history_col.find({})))
        history = []
        for doc in docs:
            doc.pop("_id", None)
            history.append(doc)
        return history
    except Exception as e:
        logger.error(f"Ошибка загрузки истории клуба: {e}")
        return []

async def save_club_history(history: list):
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: club_history_col.delete_many({}))
        if history:
            await loop.run_in_executor(None, lambda: club_history_col.insert_many(history))
        logger.info(f"✅ Сохранено записей в истории: {len(history)}")
    except Exception as e:
        logger.error(f"Ошибка сохранения истории клуба: {e}")

async def load_season_config():
    try:
        loop = asyncio.get_event_loop()
        doc = await loop.run_in_executor(None, lambda: season_config_col.find_one({"_id": "season"}))
        if not doc:
            start = datetime(2025, 12, 4, 12, 0, tzinfo=timezone.utc)
            end = datetime(2026, 1, 1, 11, 59, tzinfo=timezone.utc)
            await save_season_config(start, end)
            return start, end
        try:
            start = datetime.fromisoformat(doc["start"])
            end = datetime.fromisoformat(doc["end"])
            return start, end
        except Exception as e:
            logger.error(f"Ошибка парсинга даты: {e}")
            start = datetime(2025, 12, 4, 12, 0, tzinfo=timezone.utc)
            end = datetime(2026, 1, 1, 11, 59, tzinfo=timezone.utc)
            await save_season_config(start, end)
            return start, end
    except Exception as e:
        logger.error(f"Ошибка загрузки конфигурации сезона: {e}")
        start = datetime(2025, 12, 4, 12, 0, tzinfo=timezone.utc)
        end = datetime(2026, 1, 1, 11, 59, tzinfo=timezone.utc)
        return start, end

async def save_season_config(start: datetime, end: datetime):
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: season_config_col.replace_one(
                {"_id": "season"},
                {"start": start.isoformat(), "end": end.isoformat()},
                upsert=True
            )
        )
        logger.info(f"✅ Сохранены даты сезона: {start} - {end}")
    except Exception as e:
        logger.error(f"Ошибка сохранения конфигурации сезона: {e}")

# ================== ОТПРАВКА С ФОТО ==================
async def send_with_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    photo_name: str,
    parse_mode: str = ParseMode.HTML
):
    try:
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
            logger.warning(f"Файл фото не найден: {photo_path}")
            await update.message.reply_text(text, parse_mode=parse_mode)
    except Exception as e:
        logger.error(f"Ошибка отправки фото: {e}")
        await update.message.reply_text(text, parse_mode=parse_mode)

# ================== ФОНОВЫЕ ЗАДАЧИ ==================
async def update_players_cache(context: ContextTypes.DEFAULT_TYPE):
    try:
        data = await load_users()
        updated = 0
        for uid, user in data.items():
            tag = user.get("player_tag")
            if not tag:
                continue
            try:
                # ИСПРАВЛЕНО: Убраны лишние пробелы в URL
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
                    logger.info(f"Обновлен кэш для игрока {tag}: {p.get('name', '–')}")
            except Exception as e:
                logger.error(f"[Кеш] Ошибка {tag}: {e}")
        await save_users(data)
        logger.info(f"[Кеш] Обновлено {updated} игроков")
    except Exception as e:
        logger.error(f"Ошибка в фоновой задаче update_players_cache: {e}")

async def check_club_changes(context: ContextTypes.DEFAULT_TYPE):
    try:
        data = await load_users()
        history = await load_club_history()
        members = get_club_members()
        if not members:
            logger.warning("Нет данных о членах клуба")
            return

        current_tags = {m["tag"].replace("#", "").upper() for m in members}
        logger.info(f"Текущие теги в клубе ({len(current_tags)}): {list(current_tags)[:5]}...")
        
        loop = asyncio.get_event_loop()
        last_doc = await loop.run_in_executor(None, lambda: last_state_col.find_one({"_id": "last_tags"}))
        last_tags = set(last_doc["tags"]) if last_doc else set()
        last_tags = {tag.upper() for tag in last_tags}

        new_members = current_tags - last_tags
        left_members = last_tags - current_tags
        now = datetime.now(timezone.utc).isoformat()

        logger.info(f"Изменения в клубе: +{len(new_members)} новых, -{len(left_members)} ушедших")

        for tag in new_members:
            user_info = next((u for u in data.values() if u.get("player_tag", "").upper() == tag), None)
            real_name = user_info["real_name"] if user_info else "Неизвестно"
            username = f'@{user_info["telegram_username"]}' if user_info and user_info.get("telegram_username") else "–"
            history.append({"event": "join", "tag": tag, "real_name": real_name, "username": username, "timestamp": now})
            logger.info(f"Новый участник клуба: {tag}, {real_name}")

        for tag in left_members:
            user_info = next((u for u in data.values() if u.get("player_tag", "").upper() == tag), None)
            real_name = user_info["real_name"] if user_info else "Неизвестно"
            username = f'@{user_info["telegram_username"]}' if user_info and user_info.get("telegram_username") else "–"
            history.append({"event": "leave", "tag": tag, "real_name": real_name, "username": username, "timestamp": now})
            logger.info(f"Покинул клуб: {tag}, {real_name}")

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
    except Exception as e:
        logger.error(f"Ошибка в фоновой задаче check_club_changes: {e}")

# ================== КНОПКИ ==================
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

# ================== КОМАНДЫ ==================
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
    try:
        logger.info(f"Запуск команды /register. Пользователь: {update.effective_user.id}, args: {context.args}")
        args = context.args
        if len(args) != 2:
            await send_with_photo(update, context, "❌ Формат:\n<code>/register ИмяВЖизни #Тег</code>", "register.jpg")
            return
        
        real_name, tag = args
        tag_clean = tag.upper().replace("#", "")
        logger.info(f"Регистрация: имя={real_name}, тег=#{tag_clean}")

        # ИСПРАВЛЕНО: Получаем актуальных членов клуба
        members = get_club_members()
        if not members:
            await send_with_photo(update, context, "❌ Не удалось загрузить состав клуба. Попробуйте позже.", "register.jpg")
            return
        
        # Создаем множество тегов из клуба (в верхнем регистре)
        club_tags = {m["tag"].replace("#", "").upper() for m in members}
        logger.info(f"Теги в клубе ({len(club_tags)}): {list(club_tags)[:10]}")
        
        # Проверяем наличие тега
        if tag_clean not in club_tags:
            # Показываем 5 ближайших тегов для отладки
            sample_club_tags = ", ".join(list(club_tags)[:5])
            await send_with_photo(
                update, 
                context, 
                f"❌ Тег #{tag_clean} не найден в клубе «МЕДВЕЖАТА».\n"
                f"Доступные теги (пример): {sample_club_tags}...\n"
                "Проверьте правильность написания тега.",
                "register.jpg"
            )
            logger.warning(f"Тег #{tag_clean} не найден в клубе. Доступные теги: {club_tags}")
            return

        data = await load_users()
        user = update.effective_user
        uid = str(user.id)

        # ИСПРАВЛЕНО: Убраны лишние пробелы в URL
        r = requests.get(f"https://api.brawlstars.com/v1/players/%23{tag_clean}", headers=headers, timeout=10)
        logger.info(f"Запрос данных игрока: https://api.brawlstars.com/v1/players/%23{tag_clean}, статус: {r.status_code}")
        
        if r.status_code != 200:
            logger.error(f"Ошибка API при загрузке данных игрока: {r.status_code}, {r.text}")
            await send_with_photo(update, context, "❌ Не удалось получить данные игрока. Проверьте тег.", "register.jpg")
            return

        player = r.json()
        logger.info(f"Данные игрока: {player.get('name', '–')}, {player.get('trophies', 0)} трофеев")
        
        if uid not in data:
            data[uid] = {
                "real_name": real_name,
                "player_tag": tag_clean,
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
            await save_users(data)
            logger.info(f"✅ Новый пользователь зарегистрирован: {uid}, {real_name}, #{tag_clean}")
        else:
            logger.info(f"Пользователь уже зарегистрирован: {uid}")

        text = (
            "💥 <b>ПРИВЕТ, МЕДВЕЖОНОК!</b> 💥\n"
            "Ты официально в клубе!\n"
            "🎯 Теперь ты можешь:\n"
            "• Смотреть свой прогресс — <code>/club</code>\n"
            "• Следить за сезоном — <code>/list</code>\n"
            "🔥 Вперёд к победам!"
        )
        await send_with_photo(update, context, text, "register.jpg")
    except Exception as e:
        logger.error(f"Ошибка в команде /register: {e}")
        await send_with_photo(update, context, "❌ Произошла ошибка при регистрации. Обратитесь к админу.", "register.jpg")

async def club(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = str(update.effective_user.id)
        data = await load_users()
        if uid not in data:
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
        percent = min(int(progress / norm * 100), 100) if norm > 0 else 0
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
            f"🏠 <b>Клуб:</b> {(cache.get('club') or {}).get('name', '-')} ❮МЕДВЕЖАТА❯\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📈 <b>СЕЗОННЫЙ ПРОГРЕСС</b>\n"
            f"🏆 <b>Норма:</b> {norm}\n"
            f"📈 <b>Прогресс:</b> +{progress} ({percent}%)\n"
            f"✅ <b>Выполнено:</b> {done}\n"
            f"⏳ <b>До конца сезона:</b> {days} дн. {hours} ч."
        )
        await send_with_photo(update, context, text, "club.jpg")
    except Exception as e:
        logger.error(f"Ошибка в команде /club: {e}")
        await send_with_photo(update, context, "❌ Ошибка при загрузке профиля. Попробуйте позже.", "club.jpg")

async def you_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        data = await load_users()
        if not data:
            await send_with_photo(update, context, "❌ В боте ещё никто не зарегистрирован.", "you.jpg")
            return
        if not context.args:
            await send_with_photo(update, context, "🔍 Используйте:\n<code>/you @username</code>\nили\n<code>/you #Тег</code>", "you.jpg")
            return

        query = context.args[0].strip()
        target_user = None
        if query.startswith("@"):
            username = query[1:].lower()
            for u in data.values():
                if u.get("telegram_username", "").lower() == username:
                    target_user = u
                    break
        elif query.startswith("#"):
            tag = query[1:].upper()
            for u in data.values():
                if u.get("player_tag", "").upper() == tag:
                    target_user = u
                    break

        if not target_user or "cache" not in target_user:
            await send_with_photo(update, context, "❌ Игрок не найден или данные не загружены.", "you.jpg")
            return

        cache = target_user["cache"]
        trophies = cache["trophies"]
        progress = trophies - target_user["season_start_trophies"]
        norm = get_player_norm(target_user)
       percent = min(int(progress / norm * 100), 100) if norm > 0 else 0
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
            f"🏠 <b>Клуб:</b> {(cache.get('club') or {}).get('name', '-')} ❮МЕДВЕЖАТА❯\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📈 <b>СЕЗОННЫЙ ПРОГРЕСС</b>\n"
            f"🏆 <b>Норма:</b> {norm}\n"
            f"📈 <b>Прогресс:</b> +{progress} ({percent}%)\n"
            f"✅ <b>Выполнено:</b> {done}\n"
            f"⏳ <b>До конца сезона:</b> {days} дн. {hours} ч."
        )
        await send_with_photo(update, context, text, "you.jpg")
    except Exception as e:
        logger.error(f"Ошибка в команде /you: {e}")
        await send_with_photo(update, context, "❌ Ошибка при просмотре профиля.", "you.jpg")

async def build_list(sort_mode):
    try:
        data = await load_users()
        players = []
        for uid, u in data.items():
            cache = u.get("cache")
            if not cache:
                continue
            trophies = cache["trophies"]
            progress = trophies - u["season_start_trophies"]
            norm = get_player_norm(u)
           percent = min(int(progress / norm * 100), 100) if norm > 0 else 0
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
            medal = "🥇" if i <= 3 else "🥈" if i <= 6 else "🥉" if i <= 10 else " "
            status = "✅" if p["done"] else "❌"
            lines.append(
                f"{medal} <b>{i}) {p['name']}</b> — {p['trophies']} 🏆\n"
                f" 📈 +{p['progress']} | {p['percent']}% {status}"
            )
        return title + "\n".join(lines)
    except Exception as e:
        logger.error(f"Ошибка в build_list: {e}")
        return "❌ Ошибка при генерации рейтинга."

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = await build_list("trophies")
        photo_path = "assets/list.jpg"
        if os.path.isfile(photo_path):
            with open(photo_path, "rb") as photo:
                msg = await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=photo,
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=list_keyboard("trophies")
                )
        else:
            msg = await update.message.reply_text(
                text, parse_mode=ParseMode.HTML, reply_markup=list_keyboard("trophies")
            )
        context.user_data["list_message_id"] = msg.message_id
    except Exception as e:
        logger.error(f"Ошибка в команде /list: {e}")
        await update.message.reply_text("❌ Ошибка при загрузке рейтинга.", parse_mode=ParseMode.HTML)

async def list_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        msg_id = context.user_data.get("list_message_id")
        if msg_id:
            try:
                await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=msg_id)
            except:
                pass

        mode = "trophies"
        if query.data == "list_season":
            mode = "season"
        elif query.data == "list_done":
            mode = "done"

        text = await build_list(mode)
        photo_path = "assets/list.jpg"
        if os.path.isfile(photo_path):
            with open(photo_path, "rb") as photo:
                new_msg = await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=photo,
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=list_keyboard(mode)
                )
        else:
            new_msg = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=list_keyboard(mode)
            )
        context.user_data["list_message_id"] = new_msg.message_id
    except Exception as e:
        logger.error(f"Ошибка в обработчике кнопок списка: {e}")

# ================== АДМИН-КОМАНДЫ (минимум) ==================
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
    app.add_handler(CallbackQueryHandler(list_buttons, pattern="^list_"))

    # Фоновые задачи
    app.job_queue.run_repeating(check_club_changes, interval=300, first=10)
    app.job_queue.run_repeating(update_players_cache, interval=300, first=15)

    logger.info("✅ Бот «МЕДВЕЖАТА» запущен. Фоновые задачи активны.")
    app.run_polling()

if __name__ == "__main__":
    main()

