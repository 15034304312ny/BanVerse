"""离线生成内置角色的原创矢量头像资源。"""

from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QConicalGradient,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)

SIZE = 512
OUTPUT = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "deepseek_cli"
    / "desktop"
    / "resources"
    / "builtin_avatars"
)


def color(value: str, alpha: int = 255) -> QColor:
    result = QColor(value)
    result.setAlpha(alpha)
    return result


def line(painter: QPainter, start, end, value: str, width: float = 3) -> None:
    painter.setPen(QPen(color(value), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawLine(QPointF(*start), QPointF(*end))


def glow(painter: QPainter, center, radius: float, value: str, alpha: int = 150) -> None:
    gradient = QRadialGradient(QPointF(*center), radius)
    gradient.setColorAt(0, color(value, alpha))
    gradient.setColorAt(0.45, color(value, alpha // 3))
    gradient.setColorAt(1, color(value, 0))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(gradient)
    painter.drawEllipse(QPointF(*center), radius, radius)


def background(painter: QPainter, top: str, bottom: str, accent: str) -> None:
    gradient = QLinearGradient(0, 0, SIZE, SIZE)
    gradient.setColorAt(0, color(top))
    gradient.setColorAt(0.62, color(bottom))
    gradient.setColorAt(1, color("#0B1018"))
    painter.fillRect(0, 0, SIZE, SIZE, gradient)
    glow(painter, (360, 125), 220, accent, 110)
    painter.setPen(QPen(color(accent, 38), 1))
    for radius in (92, 145, 205):
        painter.drawEllipse(QPointF(365, 133), radius, radius)


def shoulders(painter: QPainter, primary: str, secondary: str, collar: str) -> None:
    body = QPainterPath()
    body.moveTo(72, 512)
    body.cubicTo(92, 390, 170, 362, 256, 362)
    body.cubicTo(346, 362, 426, 394, 445, 512)
    gradient = QLinearGradient(90, 370, 420, 500)
    gradient.setColorAt(0, color(primary))
    gradient.setColorAt(0.55, color(secondary))
    gradient.setColorAt(1, color("#111722"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(gradient)
    painter.drawPath(body)

    painter.setBrush(color(collar))
    left = QPainterPath()
    left.moveTo(194, 363)
    left.lineTo(252, 430)
    left.lineTo(221, 475)
    left.lineTo(157, 383)
    left.closeSubpath()
    painter.drawPath(left)
    right = QPainterPath()
    right.moveTo(318, 363)
    right.lineTo(258, 430)
    right.lineTo(294, 475)
    right.lineTo(359, 385)
    right.closeSubpath()
    painter.drawPath(right)


def neck_and_face(
    painter: QPainter,
    skin: str,
    shadow: str,
    face_rect: QRectF,
    eye: str,
    expression: str = "calm",
) -> None:
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color(shadow))
    painter.drawRoundedRect(QRectF(218, 319, 76, 82), 28, 28)

    face = QPainterPath()
    face.moveTo(face_rect.center().x(), face_rect.top())
    face.cubicTo(face_rect.right() + 8, face_rect.top() + 10, face_rect.right(), face_rect.center().y() + 35, face_rect.center().x() + 42, face_rect.bottom() - 13)
    face.cubicTo(face_rect.center().x() + 16, face_rect.bottom() + 7, face_rect.center().x() - 16, face_rect.bottom() + 7, face_rect.center().x() - 42, face_rect.bottom() - 13)
    face.cubicTo(face_rect.left(), face_rect.center().y() + 35, face_rect.left() - 8, face_rect.top() + 10, face_rect.center().x(), face_rect.top())
    face.closeSubpath()
    gradient = QLinearGradient(face_rect.left(), face_rect.top(), face_rect.right(), face_rect.bottom())
    gradient.setColorAt(0, color("#FFE2C8"))
    gradient.setColorAt(0.45, color(skin))
    gradient.setColorAt(1, color(shadow))
    painter.setBrush(gradient)
    painter.drawPath(face)

    # Nose and soft facial planes.
    line(painter, (255, 224), (250, 275), shadow, 2.2)
    line(painter, (250, 275), (262, 278), shadow, 2)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color("#E78E87", 34))
    painter.drawEllipse(QRectF(175, 266, 52, 24))
    painter.drawEllipse(QRectF(286, 266, 52, 24))

    # Brows and eyes.
    brow_y = 229 if expression != "cheerful" else 224
    line(painter, (193, brow_y), (230, brow_y - 5), "#332A2B", 4)
    line(painter, (282, brow_y - 5), (319, brow_y), "#332A2B", 4)
    for x in (212, 300):
        painter.setBrush(color("#F8F2E8"))
        painter.setPen(QPen(color("#3A3032"), 2))
        painter.drawEllipse(QPointF(x, 249), 18, 10)
        painter.setBrush(color(eye))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(x, 249), 7, 7)
        painter.setBrush(color("#10141A"))
        painter.drawEllipse(QPointF(x, 249), 3, 3)
        painter.setBrush(color("#FFFFFF", 210))
        painter.drawEllipse(QPointF(x - 2, 246), 1.5, 1.5)

    # Mouth.
    mouth = QPainterPath()
    if expression == "cheerful":
        mouth.moveTo(233, 311)
        mouth.quadTo(256, 326, 280, 309)
    elif expression == "gentle":
        mouth.moveTo(236, 312)
        mouth.quadTo(256, 319, 277, 309)
    else:
        mouth.moveTo(237, 312)
        mouth.quadTo(256, 315, 276, 311)
    painter.setPen(QPen(color("#8C4F55"), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(mouth)


def long_hair(painter: QPainter, main: str, highlight: str, style: str) -> None:
    painter.setPen(Qt.PenStyle.NoPen)
    hair = QPainterPath()
    hair.moveTo(151, 359)
    hair.cubicTo(119, 282, 127, 117, 256, 99)
    hair.cubicTo(383, 115, 395, 285, 359, 399)
    hair.cubicTo(333, 371, 309, 355, 289, 350)
    hair.lineTo(218, 352)
    hair.cubicTo(192, 360, 170, 375, 151, 359)
    hair.closeSubpath()
    gradient = QLinearGradient(130, 120, 376, 390)
    gradient.setColorAt(0, color(highlight))
    gradient.setColorAt(0.45, color(main))
    gradient.setColorAt(1, color("#101321"))
    painter.setBrush(gradient)
    painter.drawPath(hair)

    fringe = QPainterPath()
    fringe.moveTo(160, 220)
    fringe.cubicTo(165, 120, 223, 105, 278, 113)
    fringe.cubicTo(342, 121, 357, 164, 351, 225)
    fringe.cubicTo(316, 202, 299, 171, 287, 139)
    fringe.cubicTo(269, 183, 224, 212, 160, 220)
    fringe.closeSubpath()
    painter.setBrush(color(main))
    painter.drawPath(fringe)

    painter.setPen(QPen(color(highlight, 95), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    for offset in (0, 18, 36):
        painter.drawArc(QRectF(142 + offset, 128, 215, 270), 85 * 16, 105 * 16)

    if style == "ponytail":
        tail = QPainterPath()
        tail.moveTo(320, 139)
        tail.cubicTo(419, 130, 432, 233, 394, 315)
        tail.cubicTo(385, 251, 348, 233, 305, 223)
        tail.closeSubpath()
        painter.setBrush(color(main))
        painter.drawPath(tail)
        painter.setBrush(color("#B9362C"))
        painter.drawEllipse(QRectF(306, 129, 38, 30))


def short_hair(painter: QPainter, main: str, highlight: str, masculine: bool = False) -> None:
    painter.setPen(Qt.PenStyle.NoPen)
    hair = QPainterPath()
    hair.moveTo(160, 231)
    hair.cubicTo(151, 154, 189, 104, 257, 103)
    hair.cubicTo(334, 102, 365, 157, 350, 236)
    hair.lineTo(325, 207)
    hair.lineTo(312, 145)
    hair.lineTo(286, 182)
    hair.lineTo(270, 134)
    hair.lineTo(241, 179)
    hair.lineTo(215, 139)
    hair.lineTo(197, 195)
    hair.closeSubpath()
    gradient = QLinearGradient(166, 106, 346, 230)
    gradient.setColorAt(0, color(highlight))
    gradient.setColorAt(0.55, color(main))
    gradient.setColorAt(1, color("#15131A"))
    painter.setBrush(gradient)
    painter.drawPath(hair)
    if not masculine:
        painter.setBrush(color(main))
        painter.drawRoundedRect(QRectF(151, 195, 28, 112), 14, 14)
        painter.drawRoundedRect(QRectF(333, 194, 28, 111), 14, 14)


def draw_xie(painter: QPainter) -> None:
    background(painter, "#160F18", "#33141D", "#E84B32")
    painter.setPen(QPen(color("#D8A64A", 145), 2))
    for angle in range(0, 360, 30):
        rad = math.radians(angle)
        painter.drawLine(QPointF(390, 112), QPointF(390 + 82 * math.cos(rad), 112 + 82 * math.sin(rad)))
    painter.drawEllipse(QPointF(390, 112), 68, 68)
    shoulders(painter, "#641D25", "#21141A", "#C33B31")
    long_hair(painter, "#17151F", "#4A2731", "ponytail")
    neck_and_face(painter, "#F0C5AD", "#C88878", QRectF(157, 126, 198, 224), "#5B2523", "calm")
    painter.setPen(QPen(color("#D6A447"), 3))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(QPointF(371, 412), 42, 42)
    line(painter, (329, 412), (413, 412), "#D6A447", 3)
    line(painter, (371, 370), (371, 454), "#D6A447", 3)


def draw_bai(painter: QPainter) -> None:
    background(painter, "#112A2D", "#173A3B", "#A9E5D2")
    glow(painter, (394, 108), 86, "#E7FFF5", 180)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color("#F5FFF8", 210))
    painter.drawEllipse(QPointF(394, 108), 38, 38)
    for x, y, a in ((93, 86, 70), (126, 136, 45), (423, 265, 55), (77, 271, 45)):
        line(painter, (x, y), (x - 18, y + 52), "#BDE4D9", 2)
        glow(painter, (x, y), 16, "#D8FFF2", a)
    shoulders(painter, "#315B57", "#173935", "#7FAEA3")
    long_hair(painter, "#D6E1DB", "#FFFFFF", "loose")
    neck_and_face(painter, "#F4D4BD", "#C99587", QRectF(157, 126, 198, 224), "#426C63", "gentle")
    painter.setBrush(color("#E9F4E8"))
    painter.setPen(QPen(color("#91BDB2"), 3))
    painter.drawEllipse(QRectF(329, 410, 101, 39))
    painter.setBrush(color("#B7E6D8", 80))
    painter.drawEllipse(QRectF(340, 420, 80, 20))


def draw_ruan(painter: QPainter) -> None:
    background(painter, "#182934", "#203F49", "#FF8B3D")
    for x, y in ((77, 122), (421, 80), (388, 252), (104, 312)):
        glow(painter, (x, y), 28, "#FF9B45", 120)
        line(painter, (x - 8, y + 18), (x + 10, y - 19), "#FFC071", 2)
    painter.setPen(QPen(color("#54D7D5", 80), 2))
    painter.drawRoundedRect(QRectF(337, 57, 128, 92), 12, 12)
    shoulders(painter, "#C9522E", "#193941", "#E9893D")
    short_hair(painter, "#B84B24", "#F28B45")
    neck_and_face(painter, "#EFC2A3", "#B97867", QRectF(157, 126, 198, 224), "#2D7272", "cheerful")
    # Goggles.
    painter.setBrush(color("#1C3139"))
    painter.setPen(QPen(color("#56CFCC"), 4))
    painter.drawRoundedRect(QRectF(181, 134, 58, 34), 14, 14)
    painter.drawRoundedRect(QRectF(274, 134, 58, 34), 14, 14)
    line(painter, (239, 150), (274, 150), "#E77A39", 5)
    # Mechanical shoulder/arm.
    painter.setPen(QPen(color("#61D2D0"), 3))
    painter.setBrush(color("#344D55"))
    painter.drawRoundedRect(QRectF(337, 404, 71, 108), 18, 18)
    painter.drawEllipse(QPointF(372, 435), 18, 18)
    line(painter, (354, 474), (390, 474), "#FF9B45", 4)


def draw_luo(painter: QPainter) -> None:
    background(painter, "#101A3E", "#1D315C", "#64E8E0")
    conical = QConicalGradient(QPointF(390, 122), 15)
    conical.setColorAt(0, color("#72F0DF", 100))
    conical.setColorAt(0.5, color("#805DD7", 20))
    conical.setColorAt(1, color("#72F0DF", 100))
    painter.setBrush(conical)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(QPointF(390, 122), 94, 94)
    for x, y, r in ((86, 82, 8), (117, 176, 5), (421, 287, 10), (69, 330, 6)):
        painter.setBrush(color("#79F5E3", 130))
        painter.drawEllipse(QPointF(x, y), r, r)
    shoulders(painter, "#293F76", "#17264B", "#7556A6")
    long_hair(painter, "#273472", "#6756AD", "loose")
    neck_and_face(painter, "#E8C7BC", "#A8778D", QRectF(157, 126, 198, 224), "#42BFC3", "gentle")
    # Fins and luminous markings.
    painter.setBrush(color("#70EAD9", 105))
    painter.setPen(QPen(color("#A7FFF3", 130), 2))
    painter.drawPolygon([QPointF(158, 235), QPointF(119, 209), QPointF(145, 277)])
    painter.drawPolygon([QPointF(354, 235), QPointF(393, 209), QPointF(367, 277)])
    painter.setPen(QPen(color("#70F2DF", 185), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawArc(QRectF(181, 270, 53, 53), 195 * 16, 104 * 16)
    painter.drawArc(QRectF(278, 270, 53, 53), 242 * 16, 104 * 16)
    glow(painter, (256, 342), 34, "#71EADD", 100)


def draw_zhou(painter: QPainter) -> None:
    background(painter, "#101E2C", "#243A4D", "#FF8A3B")
    painter.setPen(QPen(color("#82B6C9", 65), 2))
    for offset in range(-40, 520, 36):
        painter.drawLine(QPointF(offset, 0), QPointF(offset - 110, 512))
    glow(painter, (412, 106), 55, "#FF5138", 150)
    painter.setBrush(color("#FF593F", 205))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(QPointF(412, 106), 9, 9)
    shoulders(painter, "#1E4260", "#142B3E", "#E66F2F")
    short_hair(painter, "#1A2028", "#495464", masculine=True)
    neck_and_face(painter, "#D8AA8F", "#A56D5E", QRectF(157, 126, 198, 224), "#344F58", "calm")
    # Radio headset and rescue details.
    painter.setPen(QPen(color("#1A2027"), 8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawArc(QRectF(155, 139, 201, 158), 20 * 16, 140 * 16)
    painter.setBrush(color("#222B34"))
    painter.setPen(QPen(color("#EF7A37"), 3))
    painter.drawRoundedRect(QRectF(337, 226, 28, 64), 10, 10)
    line(painter, (354, 282), (382, 303), "#242B32", 5)
    painter.setPen(QPen(color("#F7A05E"), 4))
    painter.drawLine(QPointF(111, 445), QPointF(187, 386))
    painter.drawLine(QPointF(401, 445), QPointF(325, 386))
    # Barometer on wrist/shoulder edge.
    painter.setBrush(color("#D9E1E2"))
    painter.setPen(QPen(color("#233442"), 4))
    painter.drawEllipse(QPointF(389, 445), 28, 28)
    line(painter, (389, 445), (400, 430), "#E04A36", 3)


def render(name: str, draw) -> None:
    image = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    draw(painter)
    painter.end()
    target = OUTPUT / f"{name}.png"
    if not image.save(str(target), "PNG"):
        raise RuntimeError(f"无法保存头像：{target}")


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, draw in (
        ("xie_zhaoning", draw_xie),
        ("bai_tu", draw_bai),
        ("ruan_xingyao", draw_ruan),
        ("luo_misha", draw_luo),
        ("zhou_jiming", draw_zhou),
    ):
        render(name, draw)


if __name__ == "__main__":
    main()
