"""角色库页面。"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ....character_cards import CharacterCardError, load_card, save_card
from ...builtin_characters import BuiltinCharacterError, BuiltinCharacterManager
from ...data.repositories import (
    Character,
    CharacterRepository,
    SettingsRepository,
)
from ...platform import is_android_platform
from ..character_editor_dialog import CharacterEditorDialog
from ..file_dialogs import open_mobile_file_dialog
from ..mobile import enable_touch_scrolling
from ..relationship_policy_dialog import RelationshipPolicyDialog
from ..widgets.avatar_widget import AvatarWidget


class CharacterRow(QWidget):
    MINIMUM_HEIGHT = 104
    DESCRIPTION_LIMIT = 150
    SOURCE_LABELS = {
        "built_in": "内置",
        "imported": "导入",
        "ai_generated": "AI 生成",
        "synced": "同步",
    }

    def __init__(self, character: Character) -> None:
        super().__init__()
        self._mobile = is_android_platform()
        self.minimum_row_height = 128 if self._mobile else self.MINIMUM_HEIGHT
        source_type = character.source_type
        builtin = source_type == "built_in" or character.id.startswith("builtin:")
        source_label = self.SOURCE_LABELS.get(source_type, "")
        if builtin:
            source_label = "内置"
        self.setMinimumHeight(self.minimum_row_height)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAccessibleName(
            f"角色：{character.name}"
            + (f"，{source_label}角色" if source_label else "，用户创建角色")
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        self.avatar = AvatarWidget(56)
        self.avatar.set_avatar(character.name, character.avatar_path)
        layout.addWidget(self.avatar)

        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(4)
        title = QHBoxLayout()
        self.name = QLabel(character.name)
        self.name.setObjectName("characterName")
        title.addWidget(self.name)
        if source_label:
            self.badge = QLabel(source_label)
            self.badge.setObjectName("builtinBadge")
            self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            title.addWidget(self.badge)
        else:
            self.badge = None
        title.addStretch(1)
        body.addLayout(title)

        tags = [str(tag) for tag in character.card["data"].get("tags", [])]
        if tags:
            self.tags = QLabel(" · ".join(tags[:3]))
            self.tags.setObjectName("characterTag")
            self.tags.setToolTip("、".join(tags))
            body.addWidget(self.tags)
        else:
            self.tags = None

        full_description = " ".join(
            character.card["data"]["description"].split()
        ) or "尚未填写角色描述"
        description_limit = 52 if self._mobile else self.DESCRIPTION_LIMIT
        visible = (
            full_description
            if len(full_description) <= description_limit
            else f"{full_description[:description_limit]}…"
        )
        self.description = QLabel(visible)
        self.description.setObjectName("characterDescription")
        self.description.setProperty("muted", True)
        self.description.setWordWrap(True)
        self.description.setToolTip(full_description)
        self.description.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        body.addWidget(self.description)
        layout.addLayout(body, 1)


class CharactersPage(QWidget):
    start_chat_requested = Signal(str)
    changed = Signal()
    policy_changed = Signal()

    def __init__(
        self,
        repository: CharacterRepository,
        *,
        builtins: BuiltinCharacterManager | None = None,
        settings: SettingsRepository | None = None,
    ) -> None:
        super().__init__()
        self.setObjectName("charactersPage")
        self._repository = repository
        self._builtins = builtins
        self._settings = settings
        self._mobile = is_android_platform()
        self._file_dialog: QFileDialog | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            16 if self._mobile else 24,
            14 if self._mobile else 18,
            16 if self._mobile else 24,
            16 if self._mobile else 24,
        )

        header = QHBoxLayout()
        title = QLabel("角色卡")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch(1)
        for label, handler in (
            ("新建", self._create),
            ("导入" if self._mobile else "导入 JSON", self._import),
            ("开始聊天", self._start_chat),
        ):
            button = QPushButton(label)
            if label == "开始聊天":
                button.setObjectName("primaryButton")
            button.setMinimumHeight(42)
            button.clicked.connect(handler)
            header.addWidget(button)
        layout.addLayout(header)

        actions = QHBoxLayout()
        if not self._mobile:
            actions.addStretch(1)
        action_specs = (
            ("编辑", self._edit),
            (
                "联系策略" if self._mobile else "关系与联系",
                self._relationship_policy,
            ),
            ("复制", self._duplicate),
            ("导出", self._export),
            ("删除", self._delete),
        )
        for index, (label, handler) in enumerate(action_specs):
            button = QPushButton(label)
            button.setObjectName(
                "dangerButton" if label == "删除" else "quietButton"
            )
            button.setMinimumHeight(42)
            button.clicked.connect(handler)
            actions.addWidget(button, 1 if self._mobile else 0)
            if self._mobile and index == 2:
                layout.addLayout(actions)
                actions = QHBoxLayout()
        self.restore_button = QPushButton(
            "恢复内置" if self._mobile else "恢复内置角色"
        )
        self.restore_button.setAccessibleName("恢复缺失的内置角色")
        self.restore_button.setMinimumHeight(42)
        self.restore_button.setEnabled(builtins is not None)
        self.restore_button.clicked.connect(self._restore_builtins)
        actions.addWidget(self.restore_button, 1 if self._mobile else 0)
        layout.addLayout(actions)

        self.search = QLineEdit()
        self.search.setObjectName("searchInput")
        self.search.setPlaceholderText("搜索角色")
        self.search.setMinimumHeight(42)
        self.search.textChanged.connect(self.refresh)
        layout.addWidget(self.search)
        self.list = QListWidget()
        self.list.setObjectName("characterList")
        self.list.setAccessibleName("角色列表")
        enable_touch_scrolling(self.list)
        self.list.itemDoubleClicked.connect(lambda _item: self._edit())
        layout.addWidget(self.list, 1)
        self.refresh()

    def refresh(self, *_args, select_id: str | None = None) -> None:
        current = select_id or self.current_id()
        self.list.clear()
        for character in self._repository.list(self.search.text()):
            row = CharacterRow(character)
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, character.id)
            item.setToolTip(row.description.toolTip())
            item.setSizeHint(
                QSize(row.sizeHint().width(), row.minimum_row_height)
            )
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
            if character.id == current:
                self.list.setCurrentItem(item)

    def current_id(self) -> str | None:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _create(self) -> None:
        dialog = CharacterEditorDialog(parent=self)
        if dialog.exec():
            character = self._repository.create(dialog.card, dialog.avatar_path)
            self.refresh(select_id=character.id)
            self.changed.emit()

    def _edit(self) -> None:
        character_id = self.current_id()
        character = self._repository.get(character_id) if character_id else None
        if character is None:
            return
        dialog = CharacterEditorDialog(character, self)
        if dialog.exec():
            self._repository.update(character.id, dialog.card, dialog.avatar_path)
            self.refresh(select_id=character.id)
            self.changed.emit()

    def _duplicate(self) -> None:
        character_id = self.current_id()
        if character_id:
            duplicate = self._repository.duplicate(character_id)
            self.refresh(select_id=duplicate.id)
            self.changed.emit()

    def _relationship_policy(self) -> None:
        character_id = self.current_id()
        character = self._repository.get(character_id) if character_id else None
        if character is None or self._settings is None:
            return
        dialog = RelationshipPolicyDialog(character, self._settings, self)
        if dialog.exec():
            self.policy_changed.emit()

    def _import(self) -> None:
        if self._mobile:
            self._file_dialog = open_mobile_file_dialog(
                self,
                "导入角色卡",
                "角色卡 JSON (*.json)",
                self._import_path,
            )
            self._file_dialog.finished.connect(self._file_dialog_finished)
            return
        path, _ = QFileDialog.getOpenFileName(self, "导入角色卡", "", "角色卡 JSON (*.json)")
        if path:
            self._import_path(path)

    def _import_path(self, path: str) -> None:
        try:
            card = load_card(path)
        except (OSError, CharacterCardError) as exc:
            QMessageBox.warning(self, "无法导入", str(exc))
            return
        character = self._repository.create(card, source_type="imported")
        self.refresh(select_id=character.id)
        self.changed.emit()

    def _export(self) -> None:
        character_id = self.current_id()
        character = self._repository.get(character_id) if character_id else None
        if character is None:
            return
        if self._mobile:
            self._file_dialog = open_mobile_file_dialog(
                self,
                "导出角色卡",
                "角色卡 JSON (*.json)",
                lambda path: save_card(path, character.card),
                save=True,
                initial_path=f"{character.name}.json",
            )
            self._file_dialog.finished.connect(self._file_dialog_finished)
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出角色卡", f"{character.name}.json", "角色卡 JSON (*.json)"
        )
        if path:
            save_card(path, character.card)

    def _file_dialog_finished(self, _result: int) -> None:
        self._file_dialog = None

    def _start_chat(self) -> None:
        character_id = self.current_id()
        if character_id:
            self.start_chat_requested.emit(character_id)

    def _delete(self) -> None:
        character_id = self.current_id()
        if not character_id:
            return
        answer = QMessageBox.question(self, "删除角色", "删除角色卡？已有关联会话会保留并解除绑定。")
        if answer == QMessageBox.StandardButton.Yes:
            self._repository.delete(character_id)
            self.refresh()
            self.changed.emit()

    def _restore_builtins(self) -> None:
        if self._builtins is None:
            return
        try:
            result = self._builtins.restore_missing()
        except (BuiltinCharacterError, OSError) as exc:
            QMessageBox.warning(self, "无法恢复", str(exc))
            return
        if not result.created_ids:
            QMessageBox.information(self, "内置角色", "所有内置角色均已存在。")
            return
        self.refresh(select_id=result.created_ids[0])
        self.changed.emit()
        QMessageBox.information(
            self,
            "恢复完成",
            f"已恢复 {len(result.created_ids)} 个缺失的内置角色。",
        )
