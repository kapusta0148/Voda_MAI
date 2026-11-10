"""Обработчики сообщений и кнопок Telegram.

Здесь только «разговор» с пользователем. Вся работа с базой — в services.py.
"""
import html
import io

from sqlalchemy import select
from telebot import TeleBot
from telebot.apihelper import ApiTelegramException
from telebot.types import InputFile

from . import keyboards, qr, services, texts
from .models import (
    Cooler, Feedback, Report, REPORT_KINDS, ROLE_ADMIN, SERVICE_ACTIONS,
    STATUS_PLANNED, STATUS_SERVICE, STATUS_TITLES, STATUS_WORKING,
)

REVIEWS_SHOWN = 10        # сколько последних отзывов показывать в боте
REVIEW_MAX_CHARS = 300    # длинные комментарии обрезаем, чтобы всё влезло в одно сообщение


def register_handlers(bot: TeleBot, Session, admin_ids=(), bot_username: str = "") -> None:
    """Подключает все обработчики к боту. Session — фабрика сессий SQLAlchemy."""

    # ----- вспомогательные функции -------------------------------------------

    def get_user(session, tg_user):
        name = " ".join(filter(None, [tg_user.first_name, tg_user.last_name]))
        return services.get_or_create_user(session, tg_user.id, name, admin_ids)

    def notify(tg_ids, text, markup=None) -> None:
        """Рассылка. Если человек заблокировал бота — просто пропускаем его."""
        for tg_id in tg_ids:
            try:
                bot.send_message(tg_id, text, reply_markup=markup)
            except ApiTelegramException:
                pass

    def fmt(date) -> str:
        return date.strftime("%d.%m.%Y") if date else "—"

    def card_text(session, cooler: Cooler) -> str:
        lines = [
            f"<b>Кулер №{cooler.id}</b>",
            f"{cooler.building.name}, {cooler.floor}, {cooler.place}",
            f"Адрес: {cooler.building.address}",
            f"Статус: {STATUS_TITLES[cooler.status]}",
        ]
        if cooler.status != STATUS_PLANNED:
            lines.append(f"Бутыль меняли: {fmt(services.last_service(session, cooler, 'bottle'))}")
            lines.append(f"Дезинфекция: {fmt(services.last_service(session, cooler, 'sanitize'))}")
            ratings = list(session.scalars(select(Feedback.rating).where(Feedback.cooler_id == cooler.id)))
            if ratings:
                lines.append(f"Оценка: {sum(ratings) / len(ratings):.1f} из 5 (отзывов: {len(ratings)})")
        return "\n".join(lines)

    def send_card(session, chat_id, cooler, user) -> None:
        reviews = services.count_feedback_with_comments(session, cooler)
        text, markup = card_text(session, cooler), keyboards.cooler_card(cooler, user, reviews)
        if cooler.photo_file_id:
            bot.send_photo(chat_id, cooler.photo_file_id, caption=text, reply_markup=markup)
        else:
            bot.send_message(chat_id, text, reply_markup=markup)

    def send_reviews(session, chat_id, cooler) -> None:
        """Последние отзывы о кулере с текстом. Имена авторов не показываем."""
        reviews = services.feedback_with_comments(session, cooler, limit=REVIEWS_SHOWN)
        total = services.count_feedback_with_comments(session, cooler)
        lines = [f"<b>Отзывы о кулере №{cooler.id}</b>"]
        for f in reviews:
            comment = f.comment if len(f.comment) <= REVIEW_MAX_CHARS else f.comment[:REVIEW_MAX_CHARS] + "…"
            lines.append(f"\n{'⭐' * f.rating}  {fmt(f.created_at)}\n{html.escape(comment)}")
        if not reviews:
            lines.append("Отзывов с комментариями пока нет.")
        elif total > len(reviews):
            lines.append(f"\nПоказаны последние {len(reviews)} из {total}.")
        bot.send_message(chat_id, "\n".join(lines),
                         reply_markup=keyboards.inline([[("⬅️ К кулеру", f"c:{cooler.id}")]]))

    def send_buildings(session, chat_id, title="Выберите корпус:") -> None:
        bot.send_message(chat_id, title, reply_markup=keyboards.buildings(services.list_buildings(session)))

    def send_service(session, chat_id, cooler) -> None:
        reports = services.open_reports(session, cooler)
        text = card_text(session, cooler)
        for r in reports:
            comment = f" — {html.escape(r.comment)}" if r.comment else ""
            text += f"\n\n❗️ Обращение #{r.id} от {fmt(r.created_at)}: {REPORT_KINDS[r.kind]}{comment}"
        bot.send_message(chat_id, text, reply_markup=keyboards.service(cooler, reports))

    def stats_text(session) -> str:
        s = services.stats(session)
        return (
            "<b>Проект в цифрах</b>\n"
            f"Кулеров в работе: {s['coolers_working']} из {s['coolers_total']}\n"
            f"Заменено бутылей: {s['bottles_total']} (за 30 дней: {s['bottles_30d']})\n"
            f"Проведено дезинфекций: {s['sanitize_total']}\n"
            f"Обращений закрыто: {s['reports_closed']} из {s['reports_total']}, "
            f"за сутки: {s['reports_in_time_pct']}%\n"
            f"Средняя оценка: {s['avg_rating']} (отзывов: {s['feedback_count']})\n"
            f"Подписчиков: {s['subscribers']}"
        )

    def admin_only(handler):
        """Обёртка: команду может выполнить только администратор."""
        def wrapper(message):
            with Session() as session:
                user = get_user(session, message.from_user)
                if user.role != ROLE_ADMIN:
                    bot.send_message(message.chat.id, "Эта команда доступна только администратору.")
                    return
                try:
                    handler(message, session, user)
                except (ValueError, IndexError):
                    bot.send_message(message.chat.id, "Не получилось разобрать команду. Формат — в /admin")
        return wrapper

    def args(message) -> str:
        """Текст после команды: «/launch 3» -> «3»."""
        parts = (message.text or message.caption or "").split(maxsplit=1)
        return parts[1].strip() if len(parts) > 1 else ""

    # ----- команды для всех ---------------------------------------------------

    @bot.message_handler(commands=["start"])
    def start(message):
        with Session() as session:
            user = get_user(session, message.from_user)
            services.set_state(session, user)
            bot.send_message(message.chat.id, texts.WELCOME, reply_markup=keyboards.main_menu(user))
            payload = args(message)                      # QR-код передаёт «cooler_3»
            if payload.startswith("cooler_") and payload[7:].isdigit():
                cooler = session.get(Cooler, int(payload[7:]))
                if cooler:
                    send_card(session, message.chat.id, cooler, user)

    @bot.message_handler(commands=["id"])
    def my_id(message):
        bot.send_message(message.chat.id, f"Ваш Telegram-ID: <code>{message.from_user.id}</code>")

    # ----- команды администратора ------------------------------------------------

    @bot.message_handler(commands=["admin"])
    @admin_only
    def admin_help(message, session, user):
        bot.send_message(message.chat.id, texts.ADMIN_HELP)

    @bot.message_handler(commands=["addbuilding"])
    @admin_only
    def add_building(message, session, user):
        name, _, address = args(message).partition("|")
        if not name.strip():
            raise ValueError
        building = services.add_building(session, name.strip(), address.strip())
        bot.send_message(message.chat.id, f"Добавлен корпус №{building.id}: {html.escape(building.name)}")

    @bot.message_handler(commands=["addcooler"])
    @admin_only
    def add_cooler(message, session, user):
        building_id, floor, place = [part.strip() for part in args(message).split("|")]
        cooler = services.add_cooler(session, int(building_id), floor, place)
        bot.send_message(message.chat.id, f"Добавлен кулер {html.escape(cooler.title)}. Статус: планируется.")

    @bot.message_handler(commands=["launch"])
    @admin_only
    def launch(message, session, user):
        cooler = session.get(Cooler, int(args(message)))
        if cooler is None:
            raise ValueError
        launch_and_notify(session, cooler)
        bot.send_message(message.chat.id, f"Кулер №{cooler.id} введён в работу.")

    def launch_and_notify(session, cooler) -> None:
        subscribers = services.launch_cooler(session, cooler)
        notify(subscribers,
               f"💧 Новый кулер!\n{cooler.building.name}, {cooler.floor}, {cooler.place}")

    @bot.message_handler(commands=["assign"])
    @admin_only
    def assign(message, session, user):
        cooler_id, tg_id = args(message).split()
        cooler = session.get(Cooler, int(cooler_id))
        person = services.assign_responsible(session, cooler, int(tg_id)) if cooler else None
        if person is None:
            bot.send_message(message.chat.id, "Кулер или пользователь не найден. "
                                              "Пользователь должен сначала написать боту /start.")
            return
        bot.send_message(message.chat.id, f"Ответственный за кулер №{cooler.id}: {html.escape(person.name)}")
        notify([person.tg_id], f"Вы назначены ответственным за кулер {cooler.title}. Нажмите /start.")

    @bot.message_handler(commands=["setrole"])
    @admin_only
    def set_role(message, session, user):
        tg_id, role = args(message).split()
        person = services.set_role(session, int(tg_id), role)
        text = f"Роль {html.escape(person.name)}: {role}" if person else "Пользователь не найден."
        bot.send_message(message.chat.id, text)

    @bot.message_handler(commands=["qr"])
    @admin_only
    def send_qr(message, session, user):
        cooler = session.get(Cooler, int(args(message)))
        if cooler is None or not bot_username:
            bot.send_message(message.chat.id, "Нет такого кулера или в .env не указан BOT_USERNAME.")
            return
        sign = qr.make_sign(cooler.id, [f"{cooler.building.name}, {cooler.floor}", cooler.place],
                            qr.cooler_link(bot_username, cooler.id))
        bot.send_document(message.chat.id, InputFile(io.BytesIO(sign), file_name=f"cooler_{cooler.id}.png"))

    @bot.message_handler(commands=["survey_start"])
    @admin_only
    def survey_start(message, session, user):
        wave = args(message)
        if not wave:
            raise ValueError
        services.set_wave(session, wave)
        notify(services.subscriber_ids(session),
               "📝 Помогите проекту: ответьте на два вопроса о воде в МАИ.",
               keyboards.survey_start())
        bot.send_message(message.chat.id, f"Опрос «{html.escape(wave)}» запущен.")

    @bot.message_handler(commands=["survey_stop"])
    @admin_only
    def survey_stop(message, session, user):
        services.set_wave(session, "")
        bot.send_message(message.chat.id, "Опрос остановлен.")

    @bot.message_handler(commands=["survey_stats"])
    @admin_only
    def survey_stats(message, session, user):
        wave = args(message) or services.get_wave(session)
        s = services.survey_stats(session, wave)
        bot.send_message(message.chat.id, (
            f"<b>Опрос «{html.escape(wave)}»</b>\n"
            f"Ответов: {s['n']}\n"
            f"Пьют воду регулярно: {s['regular_pct']}%\n"
            f"Оценили удобство на 4–5: {s['convenient_pct']}%\n"
            f"Средняя оценка удобства: {s['mean_convenience']}"))

    @bot.message_handler(commands=["broadcast"])
    @admin_only
    def broadcast(message, session, user):
        text = args(message)
        if not text:
            raise ValueError
        subscribers = services.subscriber_ids(session)
        notify(subscribers, html.escape(text))
        bot.send_message(message.chat.id, f"Отправлено подписчикам: {len(subscribers)}")

    @bot.message_handler(commands=["export"])
    @admin_only
    def export(message, session, user):
        for name, data in services.export_csv(session).items():
            bot.send_document(message.chat.id, InputFile(io.BytesIO(data), file_name=name))

    @bot.message_handler(content_types=["photo"])
    @admin_only
    def cooler_photo(message, session, user):
        caption = (message.caption or "").split()
        if len(caption) != 2 or caption[0] != "/photo":
            raise ValueError
        cooler = session.get(Cooler, int(caption[1]))
        if cooler is None:
            raise ValueError
        cooler.photo_file_id = message.photo[-1].file_id     # последнее фото — самое крупное
        session.commit()
        bot.send_message(message.chat.id, f"Фото кулера №{cooler.id} сохранено.")

    # ----- главное меню и свободный текст ------------------------------------------

    @bot.message_handler(content_types=["text"])
    def text_message(message):
        with Session() as session:
            user = get_user(session, message.from_user)
            chat_id, text = message.chat.id, message.text.strip()

            menu = {
                texts.BTN_COOLERS: lambda: send_buildings(session, chat_id),
                texts.BTN_REPORT: lambda: send_buildings(
                    session, chat_id, "Выберите корпус, затем кулер — и нажмите «Сообщить о проблеме»:"),
                texts.BTN_FEEDBACK: lambda: send_buildings(
                    session, chat_id, "Выберите корпус, затем кулер — и нажмите «Оставить отзыв»:"),
                texts.BTN_NOTIFY: lambda: bot.send_message(
                    chat_id,
                    "Уведомления о новых кулерах и опросах: "
                    + ("<b>включены</b>." if user.subscribed else "<b>выключены</b>."),
                    reply_markup=keyboards.subscription(user.subscribed)),
                texts.BTN_RULES: lambda: bot.send_message(chat_id, texts.RULES + "\n\n" + stats_text(session)),
                texts.BTN_SURVEY: lambda: survey_begin(session, chat_id, user),
                texts.BTN_SERVICE: lambda: service_menu(session, chat_id, user),
            }
            if text in menu:                       # нажата кнопка меню
                services.set_state(session, user)
                menu[text]()
                return

            state, _, value = user.state.partition(":")
            if state == "report_other":            # ждали описание проблемы
                services.set_state(session, user)
                cooler = session.get(Cooler, int(value))
                report = services.create_report(session, cooler, user, "other", text[:1000])
                report_created(session, chat_id, report)
            elif state == "fb_comment":            # ждали комментарий к отзыву
                services.set_state(session, user)
                services.set_feedback_comment(session, int(value), text)
                bot.send_message(chat_id, "Комментарий сохранён. Спасибо!")
            else:
                bot.send_message(chat_id, "Выберите действие в меню.", reply_markup=keyboards.main_menu(user))

    def survey_begin(session, chat_id, user) -> None:
        if not services.get_wave(session):
            bot.send_message(chat_id, "Сейчас опрос не проводится. Подпишитесь на уведомления — мы позовём.")
            return
        bot.send_message(chat_id, texts.SURVEY_Q1, reply_markup=keyboards.survey_q1())

    def service_menu(session, chat_id, user) -> None:
        if not user.is_staff:
            bot.send_message(chat_id, "Раздел доступен ответственным за кулеры.")
            return
        query = select(Cooler).order_by(Cooler.id)
        if user.role != ROLE_ADMIN:                 # ответственный видит только свои кулеры
            query = query.where(Cooler.responsible_id == user.id)
        coolers = list(session.scalars(query))
        ids = {c.id for c in coolers}
        lines = ["<b>Обслуживание</b>"]
        reports = [r for r in services.open_reports(session) if r.cooler_id in ids]
        lines.append(f"Открытых обращений: {len(reports)}")
        lines += [f"❗️ №{r.cooler_id}: {REPORT_KINDS[r.kind]} ({fmt(r.created_at)})" for r in reports]
        lines += [f"⏰ №{c.id}: {why}" for c, why in services.overdue(session) if c.id in ids]
        lines.append("\nВыберите кулер:" if coolers else "\nЗа вами пока не закреплено ни одного кулера.")
        bot.send_message(chat_id, "\n".join(lines), reply_markup=keyboards.staff_coolers(coolers))

    def report_created(session, chat_id, report: Report) -> None:
        """Благодарит автора и отправляет обращение ответственному и администраторам."""
        bot.send_message(chat_id, f"Спасибо! Обращение #{report.id} передано ответственному.")
        cooler = report.cooler
        comment = f"\nКомментарий: {html.escape(report.comment)}" if report.comment else ""
        notify(services.staff_ids_for(session, cooler),
               f"❗️ Обращение #{report.id}\n{cooler.title}\n{REPORT_KINDS[report.kind]}{comment}",
               keyboards.inline([[("🛠 Открыть кулер", f"sv:{cooler.id}")]]))

    # ----- кнопки под сообщениями ------------------------------------------------------

    @bot.callback_query_handler(func=lambda call: True)
    def callback(call):
        with Session() as session:
            user = get_user(session, call.from_user)
            chat_id = call.message.chat.id
            action, *params = call.data.split(":")
            bot.answer_callback_query(call.id)

            # кнопки, которые не относятся к конкретному кулеру
            if action == "bl":
                send_buildings(session, chat_id)
                return
            if action == "b":
                building = next((b for b in services.list_buildings(session) if b.id == int(params[0])), None)
                if building:
                    bot.send_message(chat_id, f"<b>{html.escape(building.name)}</b>\n"
                                              f"{html.escape(building.address)}\nВыберите кулер:",
                                     reply_markup=keyboards.coolers(building))
                return
            if action == "sub":
                services.set_subscription(session, user, params[0] == "1")
                bot.send_message(chat_id, "Вы подписаны на уведомления." if user.subscribed
                                 else "Уведомления выключены.")
                return
            if action == "s0":
                survey_begin(session, chat_id, user)
                return
            if action == "s1":
                bot.send_message(chat_id, texts.SURVEY_Q2, reply_markup=keyboards.survey_q2(int(params[0])))
                return
            if action == "s2":
                saved = services.save_survey(session, user, int(params[0]), int(params[1]))
                bot.send_message(chat_id, "Спасибо, ответ записан!" if saved
                                 else "Вы уже отвечали на этот опрос, или он завершён.")
                return
            if action == "rc":                                   # закрыть обращение вручную
                report = session.get(Report, int(params[0]))
                if report and report.is_open and user.can_service(report.cooler):
                    services.close_report(session, report)
                    bot.send_message(chat_id, f"Обращение #{report.id} закрыто.")
                    notify([report.user.tg_id], f"Ваше обращение #{report.id} по кулеру "
                                                f"№{report.cooler_id} рассмотрено. Спасибо!")
                return

            # всё остальное — действия с кулером, его номер идёт первым параметром
            cooler = session.get(Cooler, int(params[0]))
            if cooler is None:
                bot.send_message(chat_id, "Кулер не найден.")
                return

            if action == "c":
                send_card(session, chat_id, cooler, user)
            elif action == "rp":
                bot.send_message(chat_id, f"Что случилось с кулером №{cooler.id}?",
                                 reply_markup=keyboards.report_kinds(cooler.id))
            elif action == "rk":
                if params[1] == "other":
                    services.set_state(session, user, f"report_other:{cooler.id}")
                    bot.send_message(chat_id, "Опишите проблему одним сообщением.")
                else:
                    report = services.create_report(session, cooler, user, params[1])
                    report_created(session, chat_id, report)
            elif action == "fb":
                bot.send_message(chat_id, f"Оцените кулер №{cooler.id}:", reply_markup=keyboards.rating(cooler.id))
            elif action == "fr":
                feedback = services.add_feedback(session, cooler, user, int(params[1]))
                services.set_state(session, user, f"fb_comment:{feedback.id}")
                bot.send_message(chat_id, "Спасибо за оценку! Если хотите, напишите комментарий одним сообщением.")
            elif action == "fv":
                send_reviews(session, chat_id, cooler)
            elif not user.can_service(cooler):
                bot.send_message(chat_id, "Обслуживать этот кулер может только закреплённый за ним "
                                          "ответственный или администратор.")
            elif action == "sv":
                send_service(session, chat_id, cooler)
            elif action == "sa":
                closed = services.log_service(session, cooler, user, params[1])
                bot.send_message(chat_id, f"Записано в журнал: {SERVICE_ACTIONS[params[1]]}, кулер №{cooler.id}.")
                for report in closed:
                    notify([report.user.tg_id], f"Кулер №{cooler.id}: проблема «{REPORT_KINDS[report.kind]}» "
                                                "устранена. Спасибо за сообщение!")
            elif action == "st":
                if params[1] == "launch":
                    launch_and_notify(session, cooler)
                else:
                    services.set_status(session, cooler,
                                        STATUS_SERVICE if params[1] == "service" else STATUS_WORKING)
                bot.send_message(chat_id, f"Кулер №{cooler.id}: {STATUS_TITLES[cooler.status]}")
            elif action == "sj":
                rows = [f"{l.created_at:%d.%m.%Y %H:%M} — {SERVICE_ACTIONS[l.action]} ({html.escape(l.user.name)})"
                        for l in services.journal(session, cooler)]
                bot.send_message(chat_id, f"<b>Журнал кулера №{cooler.id}</b>\n"
                                 + ("\n".join(rows) if rows else "Записей пока нет."))
