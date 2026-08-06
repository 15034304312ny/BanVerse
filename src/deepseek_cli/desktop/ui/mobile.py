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
    viewport.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
    QScroller.grabGesture(
        viewport,
        QScroller.ScrollerGestureType.TouchGesture,
    )
    scroller = QScroller.scroller(viewport)
    properties = scroller.scrollerProperties()
    # 滚动手感优化（参考原生安卓应用）：
    # - DecelerationFactor 0.12 → 0.4：更高的减速因子让甩动滑得更远更
    #   顺滑，避免几乎瞬时刹停的生涩感；
    # - MaximumVelocity 0.5 → 1.2：支持更快的甩动速度；
    # - AxisLockThreshold：斜向滑动更容易锁定到主轴，减少误触发；
    # - OvershootWhenScrollable：到顶/到底时可回弹的橡皮筋手感；
    # - ScrollingCurve OutQuad：松手后先快后慢的自然减速。
    properties.setScrollMetric(
        QScrollerProperties.ScrollMetric.ScrollingCurve,
        QEasingCurve(QEasingCurve.Type.OutQuad),
    )
    properties.setScrollMetric(
        QScrollerProperties.ScrollMetric.DecelerationFactor, 0.4
    )
    properties.setScrollMetric(
        QScrollerProperties.ScrollMetric.MaximumVelocity, 1.2
    )
    properties.setScrollMetric(
        QScrollerProperties.ScrollMetric.MinimumVelocity, 0.0
    )
    properties.setScrollMetric(
        QScrollerProperties.ScrollMetric.AxisLockThreshold, 0.7
    )
    properties.setScrollMetric(
        QScrollerProperties.ScrollMetric.DragStartDistance, 8.0
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
