"""Android 触控滚动与紧凑表单适配。"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QEvent, QObject, Qt
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


class _TouchScrollBoundaryGuard(QObject):
    """Stop kinetic scrolling as soon as Qt reports boundary overshoot."""

    def __init__(self, area: QAbstractScrollArea) -> None:
        super().__init__(area.viewport())
        self._area = area

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        # 滚动容器销毁时会继续投递 ChildRemoved/Destroy 等事件。
        # 先按类型返回，避免在 C++ area 已销毁后再访问它。
        if event.type() != QEvent.Type.Scroll:
            return super().eventFilter(watched, event)

        overshoot = event.overshootDistance()
        if overshoot.isNull():
            return super().eventFilter(watched, event)

        # Android 上即使 OvershootAlwaysOff 已设置，某些 Qt 平台
        # 路径仍可能投递带 overshootDistance 的 QScrollEvent。若继续
        # 交给 QAbstractScrollArea，视口会先越界再回弹。在事件层
        # 终止该次惯性，并把位置硬夹取到有效范围。
        scroller = QScroller.scroller(watched)
        if scroller.state() != QScroller.State.Inactive:
            scroller.stop()

        position = event.contentPos()
        vertical = self._area.verticalScrollBar()
        horizontal = self._area.horizontalScrollBar()
        vertical.setValue(
            max(
                vertical.minimum(),
                min(vertical.maximum(), round(position.y())),
            )
        )
        horizontal.setValue(
            max(
                horizontal.minimum(),
                min(horizontal.maximum(), round(position.x())),
            )
        )
        event.accept()
        return True


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
        # 快速反向拖动时让最新采样尽快取代旧方向，避免松手后仍按
        # 上一段速度继续滚动。
        QScrollerProperties.ScrollMetric.DragVelocitySmoothingFactor, 0.85
    )
    properties.setScrollMetric(
        QScrollerProperties.ScrollMetric.ScrollingCurve,
        QEasingCurve(QEasingCurve.Type.OutQuad),
    )
    properties.setScrollMetric(
        QScrollerProperties.ScrollMetric.DecelerationFactor, 0.2
    )
    properties.setScrollMetric(
        QScrollerProperties.ScrollMetric.MaximumVelocity, 0.9
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
    # Android 上在惯性滚动尚未结束时快速反向甩动，Qt 默认会把它识别为
    # accelerating flick 并把旧速度再次放大。关闭该机制后，每次手势都
    # 独立计算方向和速度，手指按下也能可靠截停上一段惯性。
    properties.setScrollMetric(
        QScrollerProperties.ScrollMetric.AcceleratingFlickMaximumTime, 0.0
    )
    properties.setScrollMetric(
        QScrollerProperties.ScrollMetric.AcceleratingFlickSpeedupFactor, 1.0
    )
    # 将越界的最大拖动/惯性距离和回弹时间归零。这些
    # 指标与 OvershootAlwaysOff 同时设置，防止 Android 平台风格
    # 仍使用默认距离生成可见的边界位移。
    properties.setScrollMetric(
        QScrollerProperties.ScrollMetric.OvershootDragResistanceFactor, 0.0
    )
    properties.setScrollMetric(
        QScrollerProperties.ScrollMetric.OvershootDragDistanceFactor, 0.0
    )
    properties.setScrollMetric(
        QScrollerProperties.ScrollMetric.OvershootScrollDistanceFactor, 0.0
    )
    properties.setScrollMetric(
        QScrollerProperties.ScrollMetric.OvershootScrollTime, 0.0
    )
    properties.setScrollMetric(
        QScrollerProperties.ScrollMetric.FrameRate,
        QScrollerProperties.FrameRates.Fps60,
    )
    properties.setScrollMetric(
        QScrollerProperties.ScrollMetric.HorizontalOvershootPolicy,
        QScrollerProperties.OvershootPolicy.OvershootAlwaysOff,
    )
    properties.setScrollMetric(
        QScrollerProperties.ScrollMetric.VerticalOvershootPolicy,
        QScrollerProperties.OvershootPolicy.OvershootAlwaysOff,
    )
    scroller.setScrollerProperties(properties)
    guard = getattr(area, "_touch_scroll_boundary_guard", None)
    if not isinstance(guard, _TouchScrollBoundaryGuard):
        guard = _TouchScrollBoundaryGuard(area)
        area._touch_scroll_boundary_guard = guard
        viewport.installEventFilter(guard)
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
