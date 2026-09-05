"""紧凑显示实际模型名称，同时保留下拉列表的完整平台说明。"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QPaintEvent, QPalette, QPen
from PySide6.QtWidgets import QComboBox, QStyle, QStyleOptionComboBox, QStylePainter


class ModelSelector(QComboBox):
    def display_text(self) -> str:
        # GRS AI 的 itemData 是内部兼容别名；实际模型必须取平台生成的标签。
        return self.currentText().rsplit(" · ", 1)[-1]

    def paintEvent(self, event: QPaintEvent) -> None:
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        text_rect = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox,
            option,
            QStyle.SubControl.SC_ComboBoxEditField,
            self,
        )
        option.currentText = option.fontMetrics.elidedText(
            self.display_text(), Qt.TextElideMode.ElideMiddle, text_rect.width()
        )
        painter = QStylePainter(self)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option)
        # 自绘箭头避免部分 Android / Windows 样式只显示空白下拉区域。
        arrow = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox,
            option,
            QStyle.SubControl.SC_ComboBoxArrow,
            self,
        ).center()
        painter.setPen(QPen(option.palette.color(QPalette.ColorRole.ButtonText), 1.5))
        painter.drawLine(arrow.x() - 4, arrow.y() - 2, arrow.x(), arrow.y() + 2)
        painter.drawLine(arrow.x(), arrow.y() + 2, arrow.x() + 4, arrow.y() - 2)
