"""Табличка с QR-кодом для кулера (PNG формата A5, можно сразу печатать)."""
import io
from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont

ASSETS = Path(__file__).parent / "assets"
WIDTH, HEIGHT = 874, 1240          # A5 при 150 точках на дюйм
BLUE = (20, 80, 160)


def cooler_link(bot_username: str, cooler_id: int) -> str:
    """Ссылка, которая открывает в боте карточку конкретного кулера."""
    return f"https://t.me/{bot_username}?start=cooler_{cooler_id}"


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(str(ASSETS / name), size)


def _center(draw: ImageDraw.ImageDraw, y: int, text: str, font, fill="black") -> int:
    """Пишет строку по центру и возвращает координату Y следующей строки."""
    width = draw.textlength(text, font=font)
    draw.text(((WIDTH - width) / 2, y), text, font=font, fill=fill)
    return y + int(font.size * 1.35)


def make_sign(cooler_id: int, location_lines: list[str], link: str) -> bytes:
    """Рисует табличку и возвращает её как PNG."""
    image = Image.new("RGB", (WIDTH, HEIGHT), "white")
    draw = ImageDraw.Draw(image)

    draw.rectangle([0, 0, WIDTH, 150], fill=BLUE)
    _center(draw, 30, "ЧИСТАЯ ВОДА", _font(50, bold=True), fill="white")
    _center(draw, 95, "в аудиториях МАИ", _font(30), fill="white")

    y = _center(draw, 185, f"Кулер № {cooler_id}", _font(46, bold=True))
    for line in location_lines:
        y = _center(draw, y, line, _font(28))

    qr = qrcode.QRCode(border=2, box_size=10)
    qr.add_data(link)
    qr.make(fit=True)
    code = qr.make_image(fill_color="black", back_color="white").convert("RGB").resize((480, 480))
    image.paste(code, ((WIDTH - 480) // 2, y + 20))
    y += 520

    y = _center(draw, y, "Наведите камеру телефона на код", _font(30, bold=True), fill=BLUE)
    for line in ("• закончилась вода или кулер не работает — сообщите",
                 "• оставьте отзыв о кулере",
                 "• узнайте, где стоят другие кулеры"):
        y = _center(draw, y, line, _font(24))

    _center(draw, y + 25, "Наливайте воду в свою бутылку или кружку", _font(26, bold=True))

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
