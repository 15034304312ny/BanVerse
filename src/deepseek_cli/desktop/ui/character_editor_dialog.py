"""Character Card V2 角色编辑器。"""

from __future__ import annotations

import copy
import json

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...character_cards import CharacterCardError, empty_card, normalize_card
from ...tts import (
    EMOTION_PRESETS,
    TtsProfile,
    read_tts_profile,
    write_tts_profile,
)
from ..assets import AvatarError, import_avatar
from ..data.repositories import Character
from ..index_tts2 import INDEXTTS2_BUILTIN_PRESETS
from ..platform import is_android_platform
from .file_dialogs import open_mobile_file_dialog
from .mobile import configure_mobile_form, enable_touch_scrolling
from .widgets.avatar_widget import AvatarWidget


class CharacterEditorDialog(QDialog):
    def __init__(self, character: Character | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑角色卡" if character else "新建角色卡")
        self._mobile = is_android_platform()
        self.resize(356, 700) if self._mobile else self.resize(720, 680)
        self.card = copy.deepcopy(character.card if character else empty_card())
        self.avatar_path = character.avatar_path if character else ""
        self._file_dialog: QFileDialog | None = None
        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        data = self.card["data"]

        basic = QWidget()
        basic_form = QFormLayout(basic)
        configure_mobile_form(basic_form)
        avatar_row = QHBoxLayout()
        self.avatar = AvatarWidget(72)
        self.avatar.set_avatar(data["name"], self.avatar_path)
        choose = QPushButton("选择头像")
        choose.clicked.connect(self._choose_avatar)
        clear = QPushButton("清除")
        clear.clicked.connect(self._clear_avatar)
        avatar_row.addWidget(self.avatar)
        avatar_row.addWidget(choose)
        avatar_row.addWidget(clear)
        avatar_row.addStretch(1)
        basic_form.addRow("头像", avatar_row)
        self.name = QLineEdit(data["name"])
        self.name.setMaxLength(80)
        basic_form.addRow("角色名称 *", self.name)
        self.description = self._text(data["description"])
        basic_form.addRow("角色描述", self.description)
        self.personality = self._text(data["personality"])
        basic_form.addRow("性格摘要", self.personality)
        self.scenario = self._text(data["scenario"])
        basic_form.addRow("场景", self.scenario)
        self.first_mes = self._text(data["first_mes"])
        basic_form.addRow("首条消息", self.first_mes)
        self._add_tab(basic, "基础")

        dialogue = QWidget()
        dialogue_form = QFormLayout(dialogue)
        configure_mobile_form(dialogue_form)
        self.mes_example = self._text(data["mes_example"])
        dialogue_form.addRow("示例对话", self.mes_example)
        self.alternate = self._text("\n---\n".join(data["alternate_greetings"]))
        dialogue_form.addRow("备用开场白（用 --- 分隔）", self.alternate)
        self._add_tab(dialogue, "对话")

        advanced = QWidget()
        advanced_form = QFormLayout(advanced)
        configure_mobile_form(advanced_form)
        self.system_prompt = self._text(data["system_prompt"])
        advanced_form.addRow("系统提示", self.system_prompt)
        self.post_history = self._text(data["post_history_instructions"])
        advanced_form.addRow("历史后指令", self.post_history)
        self.creator_notes = self._text(data["creator_notes"])
        advanced_form.addRow("作者说明", self.creator_notes)
        self.creator = QLineEdit(data["creator"])
        advanced_form.addRow("作者", self.creator)
        self.version = QLineEdit(data["character_version"])
        advanced_form.addRow("角色版本", self.version)
        self.tags = QLineEdit(", ".join(data["tags"]))
        advanced_form.addRow("标签（逗号分隔）", self.tags)
        book = data.get("character_book")
        self.character_book = self._text(
            json.dumps(book, ensure_ascii=False, indent=2) if book is not None else ""
        )
        advanced_form.addRow("Character Book JSON", self.character_book)
        self._add_tab(advanced, "高级")

        voice_page = QWidget()
        voice_form = QFormLayout(voice_page)
        configure_mobile_form(voice_form)
        tts_profile = read_tts_profile(self.card)
        self.tts_voice = QComboBox()
        self.tts_voice.setEditable(True)
        for voice_id, label in (
            ("zh-CN-XiaoxiaoNeural", "晓晓（女声，通用）"),
            ("zh-CN-XiaoyiNeural", "晓伊（女声，活泼）"),
            ("zh-CN-YunxiNeural", "云希（男声，青年）"),
            ("zh-CN-YunjianNeural", "云健（男声，沉稳）"),
            ("zh-CN-YunyangNeural", "云扬（男声，专业）"),
            ("zh-CN-liaoning-XiaobeiNeural", "晓北（东北女声）"),
            ("zh-CN-shaanxi-XiaoniNeural", "晓妮（陕西女声）"),
        ):
            self.tts_voice.addItem(f"{label} · {voice_id}", voice_id)
        index = self.tts_voice.findData(tts_profile.voice)
        if index >= 0:
            self.tts_voice.setCurrentIndex(index)
        else:
            self.tts_voice.setEditText(tts_profile.voice)
        self.tts_voice.setAccessibleName("角色 Edge TTS 音色")
        voice_form.addRow("角色音色", self.tts_voice)
        self.index_tts2_preset = QComboBox()
        self.index_tts2_preset.setEditable(True)
        self.index_tts2_preset.addItem("使用设置页默认预设", "")
        for preset_name in INDEXTTS2_BUILTIN_PRESETS:
            self.index_tts2_preset.addItem(preset_name, preset_name)
        preset_index = self.index_tts2_preset.findData(
            tts_profile.index_tts2_preset
        )
        if preset_index >= 0:
            self.index_tts2_preset.setCurrentIndex(preset_index)
        else:
            self.index_tts2_preset.setEditText(
                tts_profile.index_tts2_preset
            )
        self.index_tts2_preset.setAccessibleName(
            "角色 IndexTTS2 克隆预设"
        )
        voice_form.addRow("IndexTTS2 预设", self.index_tts2_preset)
        self.tts_rate = self._tts_spin(tts_profile.rate, "%")
        self.tts_pitch = self._tts_spin(tts_profile.pitch, " Hz")
        self.tts_volume = self._tts_spin(tts_profile.volume, "%")
        voice_form.addRow("语速调整", self.tts_rate)
        voice_form.addRow("音调调整", self.tts_pitch)
        voice_form.addRow("音量调整", self.tts_volume)
        self.tts_emotion = QComboBox()
        emotion_labels = {
            "neutral": "中性",
            "gentle": "温柔",
            "cheerful": "活泼",
            "calm": "沉稳",
            "serious": "严肃",
            "sad": "悲伤",
        }
        for value in EMOTION_PRESETS:
            self.tts_emotion.addItem(emotion_labels[value], value)
        self.tts_emotion.setCurrentIndex(
            max(0, self.tts_emotion.findData(tts_profile.emotion_preset))
        )
        voice_form.addRow("情感基调", self.tts_emotion)
        self.tts_auto_emotion = QCheckBox("根据 AI 回复内容自动微调情绪")
        self.tts_auto_emotion.setChecked(tts_profile.auto_emotion)
        voice_form.addRow("", self.tts_auto_emotion)
        voice_note = QLabel(
            "这里保留角色的基础音色、语速、音调、音量和情感。选择科大讯飞或"
            "硅基流动时，会按 Edge 音色自动映射男/女发音人，也可在全局设置中"
            "指定统一音色。IndexTTS2 优先使用本角色的克隆预设，留空时"
            "使用设置页默认预设。自动情感会结合台词附近的动作分段调整；动作、旁白和"
            "思考过程本身不会朗读。"
        )
        voice_note.setWordWrap(True)
        voice_note.setProperty("muted", True)
        voice_form.addRow("", voice_note)
        self._add_tab(voice_page, "语音")

        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setProperty("muted", True)
        root.addWidget(self.error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        root.addWidget(buttons)

    def _add_tab(self, page: QWidget, title: str) -> None:
        if not self._mobile:
            self.tabs.addTab(page, title)
            return
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(page)
        enable_touch_scrolling(scroll)
        self.tabs.addTab(scroll, title)

    @staticmethod
    def _text(value: str) -> QTextEdit:
        editor = QTextEdit(value)
        editor.setMinimumHeight(72)
        return editor

    @staticmethod
    def _tts_spin(value: int, suffix: str) -> QSpinBox:
        control = QSpinBox()
        control.setRange(-50, 50)
        control.setValue(value)
        control.setSuffix(suffix)
        control.setMinimumHeight(40)
        return control

    def _choose_avatar(self) -> None:
        if self._mobile:
            self._file_dialog = open_mobile_file_dialog(
                self,
                "选择角色头像",
                "图片 (*.png *.jpg *.jpeg *.webp)",
                self._avatar_selected,
            )
            self._file_dialog.finished.connect(self._file_dialog_finished)
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "选择角色头像", "", "图片 (*.png *.jpg *.jpeg *.webp)"
        )
        if path:
            self._avatar_selected(path)

    def _avatar_selected(self, path: str) -> None:
        try:
            self.avatar_path = import_avatar(path)
        except AvatarError as exc:
            self.error.setText(str(exc))
            return
        self.avatar.set_avatar(self.name.text(), self.avatar_path)

    def _file_dialog_finished(self, _result: int) -> None:
        self._file_dialog = None

    def _clear_avatar(self) -> None:
        self.avatar_path = ""
        self.avatar.set_avatar(self.name.text())

    def _save(self) -> None:
        data = self.card["data"]
        values = {
            "name": self.name.text().strip(),
            "description": self.description.toPlainText(),
            "personality": self.personality.toPlainText(),
            "scenario": self.scenario.toPlainText(),
            "first_mes": self.first_mes.toPlainText(),
            "mes_example": self.mes_example.toPlainText(),
            "system_prompt": self.system_prompt.toPlainText(),
            "post_history_instructions": self.post_history.toPlainText(),
            "creator_notes": self.creator_notes.toPlainText(),
            "creator": self.creator.text(),
            "character_version": self.version.text(),
            "tags": [item.strip() for item in self.tags.text().split(",") if item.strip()],
            "alternate_greetings": [
                item.strip() for item in self.alternate.toPlainText().split("\n---\n") if item.strip()
            ],
        }
        data.update(values)
        book_text = self.character_book.toPlainText().strip()
        try:
            if book_text:
                data["character_book"] = json.loads(book_text)
            else:
                data.pop("character_book", None)
            voice = self.tts_voice.currentData()
            if not voice:
                voice = self.tts_voice.currentText().strip()
            index_tts2_preset = (
                self.index_tts2_preset.currentData()
                if self.index_tts2_preset.currentIndex() >= 0
                else self.index_tts2_preset.currentText().strip()
            )
            self.card = write_tts_profile(
                self.card,
                TtsProfile(
                    voice=voice,
                    rate=self.tts_rate.value(),
                    pitch=self.tts_pitch.value(),
                    volume=self.tts_volume.value(),
                    emotion_preset=self.tts_emotion.currentData(),
                    auto_emotion=self.tts_auto_emotion.isChecked(),
                    index_tts2_preset=str(index_tts2_preset or "").strip(),
                ),
            )
            self.card = normalize_card(self.card)
        except (json.JSONDecodeError, CharacterCardError) as exc:
            self.error.setText(f"无法保存：{exc}")
            return
        self.accept()
