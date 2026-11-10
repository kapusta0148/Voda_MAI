"""Логика бота без привязки к Telegram: всё, что читает и меняет базу.

Функции получают открытую сессию SQLAlchemy, поэтому их легко проверять тестами.
"""
import csv
import io
from datetime import datetime, timedelta

from sqlalchemy import func, select

from .models import (
    Building, Cooler, Feedback, Report, ServiceLog, Setting, SurveyAnswer, User,
    REPORT_KINDS, ROLE_ADMIN, ROLE_RESPONSIBLE, ROLE_STUDENT, SERVICE_ACTIONS,
    STATUS_EMPTY, STATUS_PLANNED, STATUS_SERVICE, STATUS_WORKING,
)

# Регламент обслуживания
BOTTLE_MAX_DAYS = 7       # бутыль меняют не реже раза в неделю
SANITIZE_MAX_DAYS = 92    # дезинфекция не реже раза в квартал
REACTION_HOURS = 24       # обращение должно быть закрыто за сутки


# --- пользователи ----------------------------------------------------------

def get_or_create_user(session, tg_id: int, name: str = "", admin_ids=()) -> User:
    """Находит пользователя по Telegram-ID или создаёт нового."""
    user = session.scalar(select(User).where(User.tg_id == tg_id))
    if user is None:
        user = User(tg_id=tg_id, name=name or str(tg_id))
        session.add(user)
    if tg_id in admin_ids:
        user.role = ROLE_ADMIN            # администраторы задаются в .env
    session.commit()
    return user


def set_role(session, tg_id: int, role: str) -> User | None:
    if role not in (ROLE_STUDENT, ROLE_RESPONSIBLE, ROLE_ADMIN):
        raise ValueError("Неизвестная роль")
    user = session.scalar(select(User).where(User.tg_id == tg_id))
    if user:
        user.role = role
        session.commit()
    return user


def set_state(session, user: User, state: str = "") -> None:
    """Запоминает, какого ввода бот ждёт от пользователя (пустая строка — ничего)."""
    user.state = state
    session.commit()


def set_subscription(session, user: User, on: bool) -> None:
    user.subscribed = on
    session.commit()


def subscriber_ids(session) -> list[int]:
    return list(session.scalars(select(User.tg_id).where(User.subscribed.is_(True))))


def staff_ids_for(session, cooler: Cooler) -> list[int]:
    """Кому отправить обращение: ответственному за кулер и всем администраторам."""
    ids = set(session.scalars(select(User.tg_id).where(User.role == ROLE_ADMIN)))
    if cooler.responsible:
        ids.add(cooler.responsible.tg_id)
    return sorted(ids)


# --- корпуса и кулеры ---------------------------------------------------------

def list_buildings(session) -> list[Building]:
    return list(session.scalars(select(Building).order_by(Building.id)))


def add_building(session, name: str, address: str = "") -> Building:
    building = Building(name=name, address=address)
    session.add(building)
    session.commit()
    return building


def add_cooler(session, building_id: int, floor: str, place: str) -> Cooler:
    """Добавляет кулер со статусом «планируется»."""
    building = session.get(Building, building_id)
    if building is None:
        raise ValueError("Нет корпуса с таким номером")
    cooler = Cooler(building=building, floor=floor, place=place, status=STATUS_PLANNED)
    session.add(cooler)
    session.commit()
    return cooler


def launch_cooler(session, cooler: Cooler, now: datetime | None = None) -> list[int]:
    """Вводит кулер в работу. Возвращает ID подписчиков, которых нужно уведомить."""
    first_launch = cooler.installed_at is None
    cooler.status = STATUS_WORKING
    if first_launch:
        cooler.installed_at = now or datetime.now()
    session.commit()
    return subscriber_ids(session) if first_launch else []


def set_status(session, cooler: Cooler, status: str) -> None:
    cooler.status = status
    session.commit()


def assign_responsible(session, cooler: Cooler, tg_id: int) -> User | None:
    user = session.scalar(select(User).where(User.tg_id == tg_id))
    if user:
        if user.role == ROLE_STUDENT:
            user.role = ROLE_RESPONSIBLE
        cooler.responsible = user
        session.commit()
    return user


# --- обращения о проблемах -------------------------------------------------------

def create_report(session, cooler: Cooler, user: User, kind: str, comment: str = "",
                  now: datetime | None = None) -> Report:
    """Сохраняет обращение и сразу меняет статус кулера, чтобы его видели все."""
    if kind not in REPORT_KINDS:
        raise ValueError("Неизвестный тип обращения")
    report = Report(cooler=cooler, user=user, kind=kind, comment=comment,
                    created_at=now or datetime.now())
    session.add(report)
    if cooler.status == STATUS_WORKING:
        if kind == "empty":
            cooler.status = STATUS_EMPTY
        elif kind == "broken":
            cooler.status = STATUS_SERVICE
    session.commit()
    return report


def open_reports(session, cooler: Cooler | None = None) -> list[Report]:
    query = select(Report).where(Report.is_open.is_(True)).order_by(Report.created_at)
    if cooler is not None:
        query = query.where(Report.cooler_id == cooler.id)
    return list(session.scalars(query))


def close_reports(session, cooler: Cooler, kinds: tuple[str, ...], now: datetime | None = None) -> list[Report]:
    """Закрывает открытые обращения по кулеру заданных типов. Возвращает закрытые."""
    closed = [r for r in open_reports(session, cooler) if r.kind in kinds]
    for report in closed:
        report.is_open = False
        report.closed_at = now or datetime.now()
    session.commit()
    return closed


def close_report(session, report: Report, now: datetime | None = None) -> None:
    """Закрывает одно обращение вручную (кнопка «Закрыть» у ответственного)."""
    report.is_open = False
    report.closed_at = now or datetime.now()
    session.commit()


# --- журнал обслуживания ----------------------------------------------------------

# Какие обращения закрывает каждое действие
CLOSES = {
    "bottle": ("empty",),
    "repair": ("broken",),
    "sanitize": ("dirty",),
}


def log_service(session, cooler: Cooler, user: User, action: str,
                now: datetime | None = None) -> list[Report]:
    """Записывает действие в журнал, возвращает кулер в работу и закрывает обращения.

    Возвращает закрытые обращения — их авторам бот отправит «спасибо, исправили».
    """
    if action not in SERVICE_ACTIONS:
        raise ValueError("Неизвестное действие")
    now = now or datetime.now()
    session.add(ServiceLog(cooler=cooler, user=user, action=action, created_at=now))
    if cooler.status in (STATUS_EMPTY, STATUS_SERVICE):
        cooler.status = STATUS_WORKING
    session.commit()
    return close_reports(session, cooler, CLOSES[action], now)


def last_service(session, cooler: Cooler, action: str) -> datetime | None:
    return session.scalar(
        select(func.max(ServiceLog.created_at))
        .where(ServiceLog.cooler_id == cooler.id, ServiceLog.action == action)
    )


def journal(session, cooler: Cooler, limit: int = 10) -> list[ServiceLog]:
    return list(session.scalars(
        select(ServiceLog).where(ServiceLog.cooler_id == cooler.id)
        .order_by(ServiceLog.created_at.desc()).limit(limit)
    ))


def overdue(session, now: datetime | None = None) -> list[tuple[Cooler, str]]:
    """Работающие кулеры, по которым нарушен регламент (бутыль > 7 дней, дезинфекция > квартала)."""
    now = now or datetime.now()
    result = []
    for cooler in session.scalars(select(Cooler).where(Cooler.status != STATUS_PLANNED)):
        start = cooler.installed_at or now
        bottle = last_service(session, cooler, "bottle") or start
        sanitize = last_service(session, cooler, "sanitize") or start
        if now - bottle > timedelta(days=BOTTLE_MAX_DAYS):
            result.append((cooler, f"бутыль не меняли {(now - bottle).days} дн."))
        if now - sanitize > timedelta(days=SANITIZE_MAX_DAYS):
            result.append((cooler, f"дезинфекции не было {(now - sanitize).days} дн."))
    return result


# --- отзывы -------------------------------------------------------------------

def add_feedback(session, cooler: Cooler, user: User, rating: int) -> Feedback:
    if not 1 <= rating <= 5:
        raise ValueError("Оценка должна быть от 1 до 5")
    feedback = Feedback(cooler=cooler, user_id=user.id, rating=rating)
    session.add(feedback)
    session.commit()
    return feedback


def set_feedback_comment(session, feedback_id: int, comment: str) -> None:
    feedback = session.get(Feedback, feedback_id)
    if feedback:
        feedback.comment = comment[:1000]
        session.commit()


def feedback_with_comments(session, cooler: Cooler, limit: int | None = None) -> list[Feedback]:
    """Отзывы о кулере, в которых есть текст. Новые — первыми."""
    query = (select(Feedback)
             .where(Feedback.cooler_id == cooler.id, Feedback.comment != "")
             .order_by(Feedback.created_at.desc(), Feedback.id.desc()))
    if limit:
        query = query.limit(limit)
    return list(session.scalars(query))


def count_feedback_with_comments(session, cooler: Cooler) -> int:
    return session.scalar(select(func.count(Feedback.id))
                          .where(Feedback.cooler_id == cooler.id, Feedback.comment != "")) or 0


# --- повторный опрос -------------------------------------------------------------

def get_wave(session) -> str:
    setting = session.get(Setting, "survey_wave")
    return setting.value if setting else ""


def set_wave(session, wave: str) -> None:
    """Запускает волну опроса (пустая строка — опрос остановлен)."""
    setting = session.get(Setting, "survey_wave")
    if setting is None:
        setting = Setting(key="survey_wave")
        session.add(setting)
    setting.value = wave
    session.commit()


def save_survey(session, user: User, drink: int, convenience: int) -> bool:
    """Сохраняет ответ на текущую волну. False — если пользователь уже отвечал."""
    wave = get_wave(session)
    exists = session.scalar(select(SurveyAnswer).where(
        SurveyAnswer.wave == wave, SurveyAnswer.user_id == user.id))
    if not wave or exists:
        return False
    session.add(SurveyAnswer(wave=wave, user_id=user.id, drink=drink, convenience=convenience))
    session.commit()
    return True


def survey_stats(session, wave: str) -> dict:
    """Показатели проекта по волне опроса: доля пьющих регулярно и доля оценок 4–5."""
    answers = list(session.scalars(select(SurveyAnswer).where(SurveyAnswer.wave == wave)))
    n = len(answers)
    if n == 0:
        return {"n": 0, "regular_pct": 0.0, "convenient_pct": 0.0, "mean_convenience": 0.0}
    return {
        "n": n,
        "regular_pct": round(100 * sum(a.drink == 0 for a in answers) / n, 1),
        "convenient_pct": round(100 * sum(a.convenience >= 4 for a in answers) / n, 1),
        "mean_convenience": round(sum(a.convenience for a in answers) / n, 2),
    }


# --- открытая статистика ------------------------------------------------------------

def stats(session, now: datetime | None = None) -> dict:
    """Сводка, которую видят все пользователи (раздел «Правила и статистика»)."""
    now = now or datetime.now()
    month_ago = now - timedelta(days=30)
    count = lambda query: session.scalar(query) or 0

    closed = list(session.scalars(select(Report).where(Report.is_open.is_(False))))
    hours = [(r.closed_at - r.created_at).total_seconds() / 3600 for r in closed if r.closed_at]
    in_time = sum(h <= REACTION_HOURS for h in hours)

    return {
        "coolers_total": count(select(func.count(Cooler.id))),
        "coolers_working": count(select(func.count(Cooler.id)).where(Cooler.status == STATUS_WORKING)),
        "bottles_total": count(select(func.count(ServiceLog.id)).where(ServiceLog.action == "bottle")),
        "bottles_30d": count(select(func.count(ServiceLog.id)).where(
            ServiceLog.action == "bottle", ServiceLog.created_at >= month_ago)),
        "sanitize_total": count(select(func.count(ServiceLog.id)).where(ServiceLog.action == "sanitize")),
        "reports_total": count(select(func.count(Report.id))),
        "reports_closed": len(closed),
        "reports_in_time_pct": round(100 * in_time / len(hours), 1) if hours else 0.0,
        "avg_close_hours": round(sum(hours) / len(hours), 1) if hours else 0.0,
        "feedback_count": count(select(func.count(Feedback.id))),
        "avg_rating": round(session.scalar(select(func.avg(Feedback.rating))) or 0, 2),
        "subscribers": count(select(func.count(User.id)).where(User.subscribed.is_(True))),
    }


# --- выгрузка для отчёта ---------------------------------------------------------------

def export_csv(session) -> dict[str, bytes]:
    """Выгружает журнал, обращения и отзывы в CSV (открываются в Excel)."""
    def to_csv(header, rows):
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter=";")
        writer.writerow(header)
        writer.writerows(rows)
        return buffer.getvalue().encode("utf-8-sig")

    fmt = lambda d: d.strftime("%d.%m.%Y %H:%M") if d else ""
    logs = session.scalars(select(ServiceLog).order_by(ServiceLog.created_at))
    reports = session.scalars(select(Report).order_by(Report.created_at))
    feedback = session.scalars(select(Feedback).order_by(Feedback.created_at))
    return {
        "journal.csv": to_csv(
            ["Дата", "Кулер", "Действие", "Кто отметил"],
            [[fmt(l.created_at), l.cooler.title, SERVICE_ACTIONS[l.action], l.user.name] for l in logs]),
        "reports.csv": to_csv(
            ["Создано", "Кулер", "Проблема", "Комментарий", "Закрыто"],
            [[fmt(r.created_at), r.cooler.title, REPORT_KINDS[r.kind], r.comment, fmt(r.closed_at)]
             for r in reports]),
        "feedback.csv": to_csv(
            ["Дата", "Кулер", "Оценка", "Комментарий"],
            [[fmt(f.created_at), f.cooler.title, f.rating, f.comment] for f in feedback]),
    }
