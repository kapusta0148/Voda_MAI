"""Запуск бота: python run.py"""
import sys

from telebot import TeleBot
from telebot.types import BotCommand

import config
from bot.db import make_session_factory, seed
from bot.handlers import register_handlers


def main() -> None:
    if not config.BOT_TOKEN:
        sys.exit("Не указан BOT_TOKEN. Скопируйте .env в .env и вставьте токен от @BotFather.")

    Session = make_session_factory(config.DATABASE_URL)
    with Session() as session:
        seed(session)                       # при первом запуске создаёт корпуса и пилотные кулеры

    bot = TeleBot(config.BOT_TOKEN, parse_mode="HTML")
    username = config.BOT_USERNAME or bot.get_me().username
    register_handlers(bot, Session, config.ADMIN_IDS, username)
    bot.set_my_commands([
        BotCommand("start", "Главное меню"),
        BotCommand("id", "Мой Telegram-ID"),
        BotCommand("admin", "Команды администратора"),
    ])

    print(f"Бот @{username} запущен. Остановить: Ctrl+C")
    bot.infinity_polling()


if __name__ == "__main__":
    main()
