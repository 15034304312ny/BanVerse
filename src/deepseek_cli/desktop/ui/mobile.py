"""Android 触控滚动与紧凑表单适配。"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QFormLayout,
    QHBoxLayout,
    QLayout,
    QScroller,
    QScrollerProperties,
    QVBoxLayout,
)

from ..platform import is_android_platform


def enable_touch_scrolling(
    area: QAbstractScrollArea,
    *,
    allow_horizontal: bool = False,
) -> None:
    """让 Qt 桌面滚动容器在 Android 上支持单指拖动与惯性滚动。"""

    if not is_android_platform():
        return
    viewport = area.viewport()
    # Qt/Android maps the primary finger to mouse events for QWidget controls.
    # Grabbing TouchGesture leaves that first finger available to child labels
    # (where it selects text) and only starts scrolling for multi-touch input.
    viewport.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, False)
    QScroller.grabGesture(
        viewport,
        QScroller.ScrollerGestureType.LeftMouseButtonGesture,
    )
    scroller = QScroller.scroller(viewport)
    properties = scroller.scrollerProperties()
    # QScroller 的距离单位是米而不是像素。旧值 8.0 相当于需要拖动
    # 8 米才起滚，单指拖动因此落到了消息标签并触发文本选择。
    properties.setScrollMetric(
        QScrollerProperties.ScrollMetric.MousePressEventDelay, 0.08
    )
    properties.setScrollMetric(
        QScrollerProperties.ScrollMetric.DragStartDistance, 0.0015
    )
    properties.setScrollMetric(
        QScrollerProperties.ScrollMetric.DragVelocitySmoothingFactor, 0.6
    )
    properties.setScrollMetric(
        QScrollerProperties.ScrollMetric.ScrollingCurve,
        QEasingCurve(QEasingCurve.Type.OutQuad),
    )
    properties.setScrollMetric(
        QScrollerProperties.ScrollMetric.DecelerationFactor, 0.12
    )
    properties.setScrollMetric(
        QScrollerProperties.ScrollMetric.MaximumVelocity, 1.5
    )
    properties.setScrollMetric(
        QScrollerProperties.ScrollMetric.MinimumVelocity, 0.05
    )
    properties.setScrollMetric(
        QScrollerProperties.ScrollMetric.AxisLockThreshold, 0.7
    )
    properties.setScrollMetric(
        QScrollerProperties.ScrollMetric.MaximumClickThroughVelocity, 0.05
    )
    properties.setScrollMetric(
        QScrollerProperties.ScrollMetric.FrameRate,
        QScrollerProperties.FrameRates.Fps60,
    )
    properties.setScrollMetric(
        QScrollerProperties.ScrollMetric.HorizontalOvershootPolicy,
        QScrollerProperties.OvershootPolicy.OvershootWhenScrollable,
    )
    properties.setScrollMetric(
        QScrollerProperties.ScrollMetric.VerticalOvershootPolicy,
        QScrollerProperties.OvershootPolicy.OvershootWhenScrollable,
    )
    scroller.setScrollerProperties(properties)
    area.setVerticalScrollBarPolicy(
        Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    if not allow_horizontal:
        area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
    if isinstance(area, QAbstractItemView):
        area.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )


def configure_mobile_form(form: QFormLayout) -> None:
    """在窄屏上将桌面双列表单变为标签在上的单列表单。"""

    if not is_android_platform():
        return
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
    form.setFieldGrowthPolicy(
        QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
    )
    form.setLabelAlignment(
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
    )
    form.setFormAlignment(
        Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
    )
    form.setHorizontalSpacing(8)
    form.setVerticalSpacing(10)
    form.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)


def responsive_row_layout() -> QHBoxLayout | QVBoxLayout:
    """移动端将容易横向溢出的操作行堆叠显示。"""

    layout = QVBoxLayout() if is_android_platform() else QHBoxLayout()
    if is_android_platform():
        layout.setSpacing(8)
    return layout
