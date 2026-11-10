"""Подключение к базе данных и первичное заполнение."""
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from .models import Base, Building, Cooler, STATUS_PLANNED

# Пилотные точки: 6 кулеров в двух корпусах. Места выбраны по результатам опроса студентов.
# Пока кулеры не установлены, у них статус «планируется».
PILOT = [
    ("ГУК", "Волоколамское шоссе, 4", [
        ("4 этаж", "зона Б, у лекторной Б-460"),
        ("IT-этаж", "институт № 8, у коворкинга"),
        ("2 этаж", "зона В, у аудиторий 221–228"),
        ("1 этаж", "холл у лекционных аудиторий"),
    ]),
    ("Корпус на Оршанской", "ул. Оршанская, 3", [
        ("1 этаж", "холл у входа"),
        ("этаж лекционных", "у лекционных аудиторий"),
    ]),
]


def make_session_factory(database_url: str):
    """Создаёт таблицы (если их ещё нет) и возвращает фабрику сессий."""
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def seed(session) -> None:
    """Заполняет пустую базу корпусами и пилотными кулерами."""
    if session.scalar(select(Building).limit(1)):
        return
    for name, address, places in PILOT:
        building = Building(name=name, address=address)
        session.add(building)
        for floor, place in places:
            session.add(Cooler(building=building, floor=floor, place=place, status=STATUS_PLANNED))
    session.commit()
