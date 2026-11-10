"""Настройки бота. Значения берутся из файла .env (см. .env)."""
import os

from dotenv import load_dotenv

load_dotenv()

# Токен, который выдаёт @BotFather
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Имя бота без @ — нужно для ссылок в QR-кодах (https://t.me/<имя>?start=cooler_1)
BOT_USERNAME = os.getenv("BOT_USERNAME", "")

# Telegram-ID администраторов через запятую. Свой ID показывает команда /id
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}

# Где хранится база. По умолчанию — файл voda.db рядом с кодом
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///voda.db")
