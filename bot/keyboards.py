"""Клавиатуры: главное меню и кнопки под сообщениями."""
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

from . import texts
from .models import REPORT_KINDS, STATUS_PLANNED, STATUS_SERVICE, STATUS_TITLES


def inline(rows) -> InlineKeyboardMarkup:
    """rows — список строк, каждая строка — список пар (подпись, callback_data)."""
    markup = InlineKeyboardMarkup()
    for row in rows:
        markup.row(*[InlineKeyboardButton(text, callback_data=data) for text, data in row])
    return markup


def main_menu(user) -> ReplyKeyboardMarkup:
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(texts.BTN_COOLERS, texts.BTN_REPORT)
    markup.row(texts.BTN_FEEDBACK, texts.BTN_NOTIFY)
    markup.row(texts.BTN_RULES, texts.BTN_SURVEY)
    if user.is_staff:
        markup.row(texts.BTN_SERVICE)
    return markup


def buildings(items) -> InlineKeyboardMarkup:
    return inline([[(f"🏛 {b.name} — кулеров: {len(b.coolers)}", f"b:{b.id}")] for b in items])


def coolers(building) -> InlineKeyboardMarkup:
    rows = [[(f"{STATUS_TITLES[c.status].split()[0]} {c.floor}, {c.place}", f"c:{c.id}")]
            for c in building.coolers]
    rows.append([("⬅️ К списку корпусов", "bl")])
    return inline(rows)


def cooler_card(cooler, user, reviews: int = 0) -> InlineKeyboardMarkup:
    """reviews — сколько у кулера отзывов с текстом; если есть, показываем кнопку «Читать отзывы»."""
    rows = []
    if cooler.status != STATUS_PLANNED:
        rows.append([("⚠️ Сообщить о проблеме", f"rp:{cooler.id}")])
        rows.append([("⭐ Оставить отзыв", f"fb:{cooler.id}")])
    if reviews:
        rows.append([(f"💬 Читать отзывы ({reviews})", f"fv:{cooler.id}")])
    if user.can_service(cooler):
        rows.append([("🛠 Обслуживание", f"sv:{cooler.id}")])
    rows.append([("⬅️ Назад", f"b:{cooler.building_id}")])
    return inline(rows)


def report_kinds(cooler_id: int) -> InlineKeyboardMarkup:
    rows = [[(title, f"rk:{cooler_id}:{kind}")] for kind, title in REPORT_KINDS.items()]
    rows.append([("⬅️ Отмена", f"c:{cooler_id}")])
    return inline(rows)


def rating(cooler_id: int) -> InlineKeyboardMarkup:
    return inline([[(f"{n} ⭐", f"fr:{cooler_id}:{n}") for n in range(1, 6)]])


def subscription(is_on: bool) -> InlineKeyboardMarkup:
    if is_on:
        return inline([[("🔕 Отписаться", "sub:0")]])
    return inline([[("🔔 Подписаться", "sub:1")]])


def survey_start() -> InlineKeyboardMarkup:
    return inline([[("📝 Пройти опрос (2 вопроса)", "s0")]])


def survey_q1() -> InlineKeyboardMarkup:
    return inline([[(text, f"s1:{i}")] for i, text in enumerate(texts.DRINK_OPTIONS)])


def survey_q2(drink: int) -> InlineKeyboardMarkup:
    return inline([[(str(n), f"s2:{drink}:{n}") for n in range(1, 6)]])


def staff_coolers(items) -> InlineKeyboardMarkup:
    return inline([[(f"№{c.id} · {c.building.name}, {c.floor}", f"sv:{c.id}")] for c in items])


def service(cooler, reports) -> InlineKeyboardMarkup:
    """Кнопки ответственного для одного кулера."""
    if cooler.status == STATUS_PLANNED:
        rows = [[("🚀 Ввести в работу", f"st:{cooler.id}:launch")]]
    else:
        rows = [
            [("💧 Заменил бутыль", f"sa:{cooler.id}:bottle")],
            [("🧴 Провёл дезинфекцию", f"sa:{cooler.id}:sanitize")],
            [("🔧 Ремонт выполнен", f"sa:{cooler.id}:repair")],
        ]
        if cooler.status == STATUS_SERVICE:
            rows.append([("✅ Вернуть в работу", f"st:{cooler.id}:working")])
        else:
            rows.append([("⛔ Вывести на обслуживание", f"st:{cooler.id}:service")])
    rows += [[(f"Закрыть обращение #{r.id}", f"rc:{r.id}")] for r in reports]
    rows.append([("📖 Журнал", f"sj:{cooler.id}")])
    return inline(rows)
