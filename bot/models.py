"""Таблицы базы данных (SQLAlchemy ORM)."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# --- справочные значения -------------------------------------------------

ROLE_STUDENT = "student"          # обычный пользователь
ROLE_RESPONSIBLE = "responsible"  # ответственный за кулеры
ROLE_ADMIN = "admin"              # администратор проекта

STATUS_PLANNED = "planned"   # кулер запланирован, ещё не установлен
STATUS_WORKING = "working"   # работает
STATUS_EMPTY = "empty"       # закончилась вода
STATUS_SERVICE = "service"   # неисправен или на обслуживании

STATUS_TITLES = {
    STATUS_PLANNED: "🕓 планируется",
    STATUS_WORKING: "✅ работает",
    STATUS_EMPTY: "💧 нет воды",
    STATUS_SERVICE: "🛠 на обслуживании",
}

REPORT_KINDS = {
    "empty": "Закончилась вода",
    "broken": "Кулер не работает",
    "dirty": "Грязно / нет стаканов",
    "other": "Другое",
}

SERVICE_ACTIONS = {
    "bottle": "Замена бутыли",
    "sanitize": "Дезинфекция",
    "repair": "Ремонт",
}


# --- таблицы --------------------------------------------------------------

class Building(Base):
    """Учебный корпус."""
    __tablename__ = "buildings"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    address: Mapped[str] = mapped_column(String(200), default="")

    coolers: Mapped[list["Cooler"]] = relationship(back_populates="building", order_by="Cooler.id")


class User(Base):
    """Пользователь бота."""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100), default="")
    role: Mapped[str] = mapped_column(String(20), default=ROLE_STUDENT)
    subscribed: Mapped[bool] = mapped_column(Boolean, default=False)   # подписка на уведомления
    state: Mapped[str] = mapped_column(String(100), default="")        # чего бот ждёт от пользователя
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    @property
    def is_staff(self) -> bool:
        return self.role in (ROLE_RESPONSIBLE, ROLE_ADMIN)

    def can_service(self, cooler) -> bool:
        """Администратор обслуживает любой кулер, ответственный — только закреплённые за ним."""
        if self.role == ROLE_ADMIN:
            return True
        return self.role == ROLE_RESPONSIBLE and cooler.responsible_id == self.id


class Cooler(Base):
    """Кулер: где стоит и в каком состоянии."""
    __tablename__ = "coolers"

    id: Mapped[int] = mapped_column(primary_key=True)
    building_id: Mapped[int] = mapped_column(ForeignKey("buildings.id"))
    floor: Mapped[str] = mapped_column(String(30))
    place: Mapped[str] = mapped_column(String(200))                    # ориентир: «у лекторной Б-460»
    status: Mapped[str] = mapped_column(String(20), default=STATUS_PLANNED)
    photo_file_id: Mapped[str] = mapped_column(String(200), default="")
    responsible_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    installed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    building: Mapped[Building] = relationship(back_populates="coolers")
    responsible: Mapped[User | None] = relationship()

    @property
    def title(self) -> str:
        return f"№{self.id} · {self.building.name}, {self.floor}, {self.place}"


class Report(Base):
    """Обращение пользователя о проблеме с кулером."""
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    cooler_id: Mapped[int] = mapped_column(ForeignKey("coolers.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(20))
    comment: Mapped[str] = mapped_column(Text, default="")
    is_open: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    cooler: Mapped[Cooler] = relationship()
    user: Mapped[User] = relationship()


class ServiceLog(Base):
    """Журнал обслуживания: замена бутыли, дезинфекция, ремонт."""
    __tablename__ = "service_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    cooler_id: Mapped[int] = mapped_column(ForeignKey("coolers.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    cooler: Mapped[Cooler] = relationship()
    user: Mapped[User] = relationship()


class Feedback(Base):
    """Отзыв о кулере: оценка 1–5 и комментарий."""
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    cooler_id: Mapped[int] = mapped_column(ForeignKey("coolers.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    rating: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    cooler: Mapped[Cooler] = relationship()


class SurveyAnswer(Base):
    """Ответ на повторный опрос. Вопросы те же, что в стартовом опросе (№4 и №6)."""
    __tablename__ = "survey_answers"
    __table_args__ = (UniqueConstraint("wave", "user_id"),)   # один ответ на волну

    id: Mapped[int] = mapped_column(primary_key=True)
    wave: Mapped[str] = mapped_column(String(50))             # название волны: «март 2027»
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    drink: Mapped[int] = mapped_column(Integer)               # 0..3, см. texts.DRINK_OPTIONS
    convenience: Mapped[int] = mapped_column(Integer)         # 1..5
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Setting(Base):
    """Простое хранилище «ключ — значение» (например, текущая волна опроса)."""
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(String(200), default="")
