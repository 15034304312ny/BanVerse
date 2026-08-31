"""单个角色的关系边界、静音与主动联系策略。"""

from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import QDateTime, QTime
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
)

from ...relationship_policy import (
    RelationshipPolicy,
    character_policy_key,
    relationship_policy_for,
    serialize_character_policy,
)
from ..data.repositories import Character, SettingsRepository
from ..platform import is_android_platform
from .mobile import configure_mobile_form, responsive_row_layout


class RelationshipPolicyDialog(QDialog):
    def __init__(
        self,
        character: Character,
        settings: SettingsRepository,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._character = character
        self._settings = settings
        self._policy = relationship_policy_for(settings, character.id)
        self._paused_until = self._policy.paused_until
        self.setWindowTitle(f"{character.name} · 关系与主动联系")
        self.resize(360, 680) if is_android_platform() else self.resize(620, 600)

        root = QVBoxLayout(self)
        intro = QLabel(
            "这些是用户策略，不是角色的隐藏亲密度。角色卡、世界书和历史内容不能绕过这里的边界。"
        )
        intro.setWordWrap(True)
        intro.setProperty("muted", True)
        root.addWidget(intro)

        form = QFormLayout()
        configure_mobile_form(form)
        root.addLayout(form)
        self.inherit = QCheckBox("关系、话题与频率跟随全局默认值")
        self.inherit.setChecked(self._policy.inherited)
        self.inherit.toggled.connect(self._update_controls)
        form.addRow("策略来源", self.inherit)

        self.pace = QComboBox()
        for label, value in (
            ("慢热", "slow"),
            ("自然", "natural"),
            ("较快", "fast"),
        ):
            self.pace.addItem(label, value)
        self.pace.setCurrentIndex(max(0, self.pace.findData(self._policy.pace)))
        form.addRow("关系发展", self.pace)

        self.preferred_address = QLineEdit(self._policy.preferred_address)
        self.preferred_address.setMaxLength(40)
        self.preferred_address.setPlaceholderText("留空表示不指定")
        form.addRow("偏好称呼", self.preferred_address)
        self.allowed_topics = QLineEdit("、".join(self._policy.allowed_topics))
        self.allowed_topics.setPlaceholderText("用逗号或顿号分隔")
        form.addRow("欢迎话题", self.allowed_topics)
        self.blocked_topics = QLineEdit("、".join(self._policy.blocked_topics))
        self.blocked_topics.setPlaceholderText("角色不得主动展开")
        form.addRow("禁止话题", self.blocked_topics)

        self.frequency = QComboBox()
        for label, value in (
            ("不主动联系", "off"),
            ("偶尔", "low"),
            ("适中", "normal"),
            ("较频繁", "high"),
        ):
            self.frequency.addItem(label, value)
        self.frequency.setCurrentIndex(
            max(0, self.frequency.findData(self._policy.proactive_frequency))
        )
        self.frequency.currentIndexChanged.connect(self._update_controls)
        form.addRow("联系频率", self.frequency)
        self.daily_limit = QSpinBox()
        self.daily_limit.setRange(0, 12)
        self.daily_limit.setSuffix(" 条/天")
        self.daily_limit.setValue(self._policy.daily_limit)
        form.addRow("每日上限", self.daily_limit)

        quiet = responsive_row_layout()
        self.quiet_start = QTimeEdit(
            QTime.fromString(self._policy.quiet_start, "HH:mm")
        )
        self.quiet_end = QTimeEdit(
            QTime.fromString(self._policy.quiet_end, "HH:mm")
        )
        self.quiet_start.setDisplayFormat("HH:mm")
        self.quiet_end.setDisplayFormat("HH:mm")
        quiet.addWidget(self.quiet_start)
        quiet.addWidget(QLabel("至"))
        quiet.addWidget(self.quiet_end)
        quiet.addStretch(1)
        form.addRow("静默时段", quiet)

        self.muted = QCheckBox("完全静音这个角色的主动消息")
        self.muted.setChecked(self._policy.muted)
        form.addRow("角色静音", self.muted)

        pause_row = responsive_row_layout()
        pause_moment = self._pause_moment()
        self.pause_until = QDateTimeEdit(QDateTime(pause_moment))
        self.pause_until.setCalendarPopup(True)
        self.pause_until.setDisplayFormat("yyyy-MM-dd HH:mm")
        pause_apply = QPushButton("暂停至此")
        pause_apply.clicked.connect(self._apply_pause)
        pause_day = QPushButton("暂停一天")
        pause_day.clicked.connect(self._pause_day)
        pause_clear = QPushButton("恢复")
        pause_clear.clicked.connect(self._clear_pause)
        for widget in (self.pause_until, pause_apply, pause_day, pause_clear):
            pause_row.addWidget(widget)
        form.addRow("临时暂停", pause_row)
        self.pause_status = QLabel()
        self.pause_status.setWordWrap(True)
        self.pause_status.setProperty("muted", True)
        form.addRow("", self.pause_status)

        reason = settings.get(
            f"proactive_last_status_{character.id}",
            "尚未产生主动消息；每次到期都会先检查开关、静默、冷却、每日上限和用户边界。",
        )
        self.last_reason = QLabel(reason)
        self.last_reason.setWordWrap(True)
        form.addRow("为什么收到/没收到", self.last_reason)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._update_pause_status()
        self._update_controls()

    def _pause_moment(self) -> datetime:
        try:
            value = datetime.fromisoformat(self._paused_until)
        except (TypeError, ValueError):
            value = datetime.now().astimezone() + timedelta(days=1)
        if value.tzinfo is None:
            value = value.astimezone()
        return value

    def _apply_pause(self) -> None:
        value = self.pause_until.dateTime().toPython()
        if value.tzinfo is None:
            value = value.astimezone()
        self._paused_until = value.isoformat(timespec="minutes")
        self._update_pause_status()

    def _pause_day(self) -> None:
        value = datetime.now().astimezone() + timedelta(days=1)
        self.pause_until.setDateTime(QDateTime(value))
        self._paused_until = value.isoformat(timespec="minutes")
        self._update_pause_status()

    def _clear_pause(self) -> None:
        self._paused_until = ""
        self._update_pause_status()

    def _update_pause_status(self) -> None:
        self.pause_status.setText(
            f"已暂停至 {self._pause_moment():%Y-%m-%d %H:%M}"
            if self._paused_until
            else "当前没有临时暂停。"
        )

    def _update_controls(self, *_args) -> None:
        custom = not self.inherit.isChecked()
        frequency_enabled = custom and self.frequency.currentData() != "off"
        for control in (
            self.pace,
            self.preferred_address,
            self.allowed_topics,
            self.blocked_topics,
            self.frequency,
        ):
            control.setEnabled(custom)
        for control in (
            self.daily_limit,
            self.quiet_start,
            self.quiet_end,
        ):
            control.setEnabled(frequency_enabled)

    def _save(self) -> None:
        policy = RelationshipPolicy(
            pace=str(self.pace.currentData()),
            preferred_address=self.preferred_address.text(),
            allowed_topics=tuple(
                part.strip()
                for part in self.allowed_topics.text().replace("、", ",").split(",")
                if part.strip()
            ),
            blocked_topics=tuple(
                part.strip()
                for part in self.blocked_topics.text().replace("、", ",").split(",")
                if part.strip()
            ),
            proactive_frequency=str(self.frequency.currentData()),
            daily_limit=self.daily_limit.value(),
            quiet_start=self.quiet_start.time().toString("HH:mm"),
            quiet_end=self.quiet_end.time().toString("HH:mm"),
            muted=self.muted.isChecked(),
            paused_until=self._paused_until,
            inherited=self.inherit.isChecked(),
        )
        self._settings.set(
            character_policy_key(self._character.id),
            serialize_character_policy(policy),
        )
        self.accept()
