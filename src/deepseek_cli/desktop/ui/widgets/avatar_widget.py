"""圆形头像组件。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QWidget


class AvatarWidget(QWidget):
    def __init__(self, size: int = 40, parent=None) -> None:
        super().__init__(parent)
        self._size = size
        self._name = "?"
        self._path = ""
        self.setFixedSize(size, size)

    def set_avatar(self, name: str, path: str = "") -> None:
        self._name = name or "?"
        self._path = path
        self.setAccessibleName(f"{self._name}的头像")
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        circle = QPainterPath()
        circle.addEllipse(0, 0, self._size, self._size)
        painter.setClipPath(circle)
        if self._path and Path(self._path).is_file():
            pixmap = QPixmap(self._path).scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            painter.drawPixmap(0, 0, pixmap)
        else:
            painter.fillPath(circle, QColor("#128A4C"))
            painter.setPen(Qt.GlobalColor.white)
            font = painter.font()
            font.setBold(True)
            font.setPointSize(max(10, self._size // 3))
            painter.setFont(font)
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, self._name[:1].upper()
            )
        painter.setClipping(False)
        painter.setPen(QPen(QColor(255, 255, 255, 105), 1))
        painter.drawEllipse(1, 1, self._size - 2, self._size - 2)
