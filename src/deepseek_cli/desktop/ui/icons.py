"""不依赖平台字体的轻量线性图标。"""

from __future__ import annotations

from math import cos, pi, sin

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from ..theme.tokens import LIGHT


def line_icon(
    name: str,
    *,
    size: int = 24,
    color: str = LIGHT["primary"],
) -> QIcon:
    """生成线性图标，保证 Windows 与 Android 上外观一致。"""

    pixmap = QPixmap(size * 2, size * 2)
    pixmap.setDevicePixelRatio(2.0)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    icon_color = QColor(color)
    pen = QPen(icon_color, max(1.8, size / 12))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    scale = size / 24.0
    painter.scale(scale, scale)
    if name == "messages":
        painter.drawRoundedRect(QRectF(3.0, 3.5, 18.0, 14.0), 4.0, 4.0)
        tail = QPainterPath(QPointF(8.0, 17.0))
        tail.lineTo(6.4, 21.0)
        tail.lineTo(12.0, 17.5)
        painter.drawPath(tail)
        painter.drawLine(QPointF(7.5, 9.0), QPointF(16.5, 9.0))
        painter.drawLine(QPointF(7.5, 12.5), QPointF(13.5, 12.5))
    elif name == "characters":
        painter.drawEllipse(QRectF(8.0, 3.0, 8.0, 8.0))
        painter.drawArc(QRectF(5.0, 10.0, 14.0, 11.0), 15 * 16, 150 * 16)
        painter.drawEllipse(QRectF(2.5, 6.0, 5.5, 5.5))
        painter.drawArc(QRectF(1.0, 11.0, 8.5, 8.0), 30 * 16, 110 * 16)
    elif name == "settings":
        painter.drawEllipse(QRectF(8.3, 8.3, 7.4, 7.4))
        painter.drawEllipse(QRectF(4.2, 4.2, 15.6, 15.6))
        center = QPointF(12.0, 12.0)
        for index in range(8):
            angle = index * pi / 4
            start = QPointF(
                center.x() + cos(angle) * 8.0,
                center.y() + sin(angle) * 8.0,
            )
            end = QPointF(
                center.x() + cos(angle) * 10.2,
                center.y() + sin(angle) * 10.2,
            )
            painter.drawLine(start, end)
    elif name == "back":
        path = QPainterPath(QPointF(15.5, 4.0))
        path.lineTo(7.5, 12.0)
        path.lineTo(15.5, 20.0)
        painter.drawPath(path)
    elif name == "more":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(icon_color)
        for x in (5.0, 12.0, 19.0):
            painter.drawEllipse(QPointF(x, 12.0), 1.7, 1.7)
    elif name == "add":
        painter.drawEllipse(QRectF(3.0, 3.0, 18.0, 18.0))
        painter.drawLine(QPointF(7.5, 12.0), QPointF(16.5, 12.0))
        painter.drawLine(QPointF(12.0, 7.5), QPointF(12.0, 16.5))
    elif name == "smile":
        painter.drawEllipse(QRectF(3.0, 3.0, 18.0, 18.0))
        painter.drawPoint(QPointF(8.5, 9.5))
        painter.drawPoint(QPointF(15.5, 9.5))
        painter.drawArc(QRectF(7.0, 9.0, 10.0, 8.0), 200 * 16, 140 * 16)
    else:
        painter.drawEllipse(QRectF(4.0, 4.0, 16.0, 16.0))

    painter.end()
    return QIcon(pixmap)


def navigation_icon(name: str, *, size: int = 24) -> QIcon:
    """生成适合深色导航栏的浅色图标。"""

    return line_icon(name, size=size, color=LIGHT["nav_text"])
