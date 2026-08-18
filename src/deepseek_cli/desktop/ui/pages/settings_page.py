"""应用设置页。"""

from __future__ import annotations

import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ....branding import PRODUCT_NAME, PRODUCT_VERSION
from ....grsai_gateway import (
    DEFAULT_GRSAI_API_BASE_URL,
    DEFAULT_GRSAI_TEXT_MODEL,
    normalize_grsai_base_url,
)
from ....model_catalog import MODEL_CHAT, text_provider_models
from ...data.repositories import SettingsRepository
from ...image_service import (
    DEFAULT_GRSAI_IMAGE_MODEL,
    DEFAULT_GRSAI_IMAGE_SIZE,
    DEFAULT_GRSAI_VISION_MODEL,
    DEFAULT_SILICONFLOW_IMAGE_MODEL,
    DEFAULT_SILICONFLOW_IMAGE_SIZE,
    DEFAULT_SILICONFLOW_VISION_MODEL,
)
from ...index_tts2 import (
    DEFAULT_INDEXTTS2_BASE_URL,
    DEFAULT_INDEXTTS2_PRESET,
    INDEXTTS2_BUILTIN_PRESETS,
    deserialize_index_tts2_presets,
    discover_index_tts2_root,
    launch_index_tts2_service,
    normalize_index_tts2_base_url,
    serialize_index_tts2_presets,
)
from ...model_discovery import (
    ProviderModel,
    ProviderModelCatalog,
    deserialize_models,
    models_for_capability,
    serialize_models,
)
from ...platform import is_android_platform
from ...security.credentials import CredentialStore
from ...tts import (
    DEFAULT_SILICONFLOW_TTS_MODEL,
    DEFAULT_XFYUN_TTS_VOICE,
    SILICONFLOW_VOICE_OPTIONS,
    XFYUN_TTS_VOICE_OPTIONS,
)
from ...workers import (
    IndexTts2CatalogWorker,
    XfyunSuperTtsSynthesisWorker,
)
from ...xfyun_catalog import (
    XFYUN_VOICES,
    available_voice_options,
    deserialize_available_voices,
    serialize_available_voices,
)
from ..mobile import (
    configure_mobile_form,
    enable_touch_scrolling,
    responsive_row_layout,
)


class ModelComboBox(QComboBox):
    """Editable model selector with QLineEdit-compatible helpers."""

    editingFinished = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setMinimumContentsLength(
            12 if is_android_platform() else 28
        )
        self.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.lineEdit().editingFinished.connect(self.editingFinished)

    def text(self) -> str:
        current_text = self.currentText().strip()
        index = self.currentIndex()
        if index >= 0 and current_text == self.itemText(index):
            return str(self.itemData(index) or current_text).strip()
        return current_text

    def setText(self, value: str) -> None:
        model = value.strip()
        index = self.findData(model)
        if index >= 0:
            self.setCurrentIndex(index)
        else:
            self.setCurrentIndex(-1)
            self.setEditText(model)


class ModelCatalogWorker(QObject):
    completed = Signal(str, object)
    failed = Signal(str, str)
    finished = Signal()

    def __init__(self, provider: str, api_key: str) -> None:
        super().__init__()
        self._provider = provider
        self._api_key = api_key

    @Slot()
    def run(self) -> None:
        try:
            models = ProviderModelCatalog().fetch(
                self._provider, api_key=self._api_key
            )
        except Exception as exc:
            self.failed.emit(self._provider, str(exc))
        else:
            self.completed.emit(self._provider, models)
        finally:
            self.finished.emit()


class XfyunVoiceCatalogWorker(QObject):
    """以一个测试字符逐项确认当前账号真实开通的讯飞发音人。"""

    progress = Signal(int, int, int)
    completed = Signal(object, object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        app_id: str,
        api_password: str,
        api_key: str,
        api_secret: str,
    ) -> None:
        super().__init__()
        self._app_id = app_id
        self._api_password = api_password
        self._api_key = api_key
        self._api_secret = api_secret

    @Slot()
    def run(self) -> None:
        available: list[str] = []
        errors: Counter = Counter()
        try:
            with tempfile.TemporaryDirectory(
                prefix="banverse-xfyun-voices-"
            ) as directory:
                output = Path(directory) / "probe.mp3"
                total = len(XFYUN_VOICES)
                for index, voice in enumerate(XFYUN_VOICES, 1):
                    if QThread.currentThread().isInterruptionRequested():
                        return
                    output.unlink(missing_ok=True)
                    worker = XfyunSuperTtsSynthesisWorker(
                        index,
                        self._app_id,
                        self._api_password,
                        "好",
                        voice.id,
                        50,
                        50,
                        50,
                        str(output),
                        api_key=self._api_key,
                        api_secret=self._api_secret,
                    )
                    try:
                        worker._synthesize()
                    except Exception as exc:
                        errors[str(getattr(exc, "error_code", "unknown"))] += 1
                    else:
                        available.append(voice.id)
                    if index == 1 or index % 5 == 0 or index == total:
                        self.progress.emit(index, total, len(available))
        except Exception as exc:
            self.failed.emit(str(exc)[:500])
        else:
            self.completed.emit(tuple(available), dict(errors))
        finally:
            self.finished.emit()


class SettingsPage(QWidget):
    theme_changed = Signal(str)
    data_clear_requested = Signal()
    credentials_changed = Signal()
    text_settings_changed = Signal()
    tts_settings_changed = Signal()
    proactive_settings_changed = Signal()
    character_discovery_settings_changed = Signal()
    notification_sound_preview_requested = Signal()

    def __init__(
        self,
        settings: SettingsRepository,
        credentials: CredentialStore,
    ) -> None:
        super().__init__()
        self.setObjectName("settingsPage")
        self._settings = settings
        self._credentials = credentials
        self._model_thread: QThread | None = None
        self._model_worker: ModelCatalogWorker | None = None
        self._model_refresh_provider = ""
        self._model_refresh_buttons: list[QPushButton] = []
        self._model_refresh_queue: list[str] = []
        self._catalog_auto_refresh_started = False
        self._xfyun_voice_thread: QThread | None = None
        self._xfyun_voice_worker: XfyunVoiceCatalogWorker | None = None
        self._indextts2_thread: QThread | None = None
        self._indextts2_worker: IndexTts2CatalogWorker | None = None
        self._mobile = is_android_platform()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        header = QWidget()
        header.setObjectName("header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 14, 24, 14)
        title = QLabel("设置")
        title.setObjectName("pageTitle")
        header_layout.addWidget(title)
        header_layout.addStretch(1)
        root.addWidget(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        enable_touch_scrolling(self.scroll)
        content = QWidget()
        content.setMinimumWidth(0)
        content.setSizePolicy(
            (
                QSizePolicy.Policy.Ignored
                if self._mobile
                else QSizePolicy.Policy.Expanding
            ),
            QSizePolicy.Policy.Preferred,
        )
        layout = QVBoxLayout(content)
        layout.setContentsMargins(
            14 if self._mobile else 32,
            16 if self._mobile else 24,
            14 if self._mobile else 32,
            20 if self._mobile else 32,
        )
        layout.setSpacing(14 if self._mobile else 18)

        account = QGroupBox("文本 AI")
        account_form = QFormLayout(account)
        configure_mobile_form(account_form)
        account_form.setSpacing(12)
        self.text_provider = QComboBox()
        self.text_provider.addItem("DeepSeek Platform", "deepseek")
        self.text_provider.addItem("GRS AI", "grsai")
        text_provider = settings.get("text_provider", "deepseek").lower()
        self.text_provider.setCurrentIndex(
            max(0, self.text_provider.findData(text_provider))
        )
        self.text_provider.currentIndexChanged.connect(
            self._save_text_provider
        )
        account_form.addRow("当前平台", self.text_provider)
        text_deepseek_start = account_form.rowCount()
        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_input.setPlaceholderText(
            "已安全保存" if credentials.get_api_key() else "输入你的 API Key"
        )
        self.key_input.setAccessibleName("DeepSeek API Key")
        account_form.addRow("DeepSeek Key", self.key_input)
        key_actions = responsive_row_layout()
        show = QPushButton("显示")
        show.setCheckable(True)
        show.toggled.connect(self._toggle_key)
        save = QPushButton("保存")
        save.setObjectName("primaryButton")
        save.clicked.connect(self._save_key)
        clear = QPushButton("清除")
        clear.clicked.connect(self._clear_key)
        key_actions.addWidget(show)
        key_actions.addWidget(save)
        key_actions.addWidget(clear)
        key_actions.addStretch(1)
        account_form.addRow("", key_actions)
        self.key_status = QLabel(
            "密钥保存在系统凭据库或应用私有凭据存储中，不会写入聊天数据库。"
        )
        self.key_status.setWordWrap(True)
        self.key_status.setProperty("muted", True)
        account_form.addRow("", self.key_status)
        self._text_deepseek_rows = tuple(
            range(text_deepseek_start, account_form.rowCount())
        )

        text_grsai_start = account_form.rowCount()
        grsai_text_actions = responsive_row_layout()
        grsai_text_portal = QPushButton("GRS AI 控制台")
        grsai_text_portal.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl("https://grsai.com/zh/dashboard")
            )
        )
        grsai_text_docs = QPushButton("接口文档")
        grsai_text_docs.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl("https://qmy27nhsd9.apifox.cn/452418916e0")
            )
        )
        grsai_text_actions.addWidget(grsai_text_portal)
        grsai_text_actions.addWidget(grsai_text_docs)
        self.grsai_text_refresh = QPushButton("刷新模型列表")
        self.grsai_text_refresh.setAccessibleName("刷新 GRS AI 模型列表")
        self.grsai_text_refresh.clicked.connect(
            lambda: self._refresh_models("grsai")
        )
        grsai_text_actions.addWidget(self.grsai_text_refresh)
        self._model_refresh_buttons.append(self.grsai_text_refresh)
        grsai_text_actions.addStretch(1)
        account_form.addRow("GRS AI", grsai_text_actions)
        self.grsai_text_key_input = QLineEdit()
        self.grsai_text_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        grsai_text_key = getattr(
            credentials, "get_grsai_text_api_key", lambda: ""
        )()
        self.grsai_text_key_input.setPlaceholderText(
            "已安全保存" if grsai_text_key else "输入 GRS AI 文本 API Key"
        )
        account_form.addRow("GRS 文本 Key", self.grsai_text_key_input)
        grsai_text_key_actions = responsive_row_layout()
        grsai_text_show = QPushButton("显示")
        grsai_text_show.setCheckable(True)
        grsai_text_show.toggled.connect(self._toggle_grsai_text_key)
        grsai_text_save = QPushButton("保存")
        grsai_text_save.setObjectName("primaryButton")
        grsai_text_save.clicked.connect(self._save_grsai_text_key)
        grsai_text_clear = QPushButton("清除")
        grsai_text_clear.clicked.connect(self._clear_grsai_text_key)
        grsai_text_key_actions.addWidget(grsai_text_show)
        grsai_text_key_actions.addWidget(grsai_text_save)
        grsai_text_key_actions.addWidget(grsai_text_clear)
        grsai_text_key_actions.addStretch(1)
        account_form.addRow("", grsai_text_key_actions)
        grsai_text_base_url = settings.get(
            "grsai_text_base_url", DEFAULT_GRSAI_API_BASE_URL
        )
        try:
            grsai_text_base_url = normalize_grsai_base_url(
                grsai_text_base_url
            )
        except ValueError:
            pass
        else:
            settings.set("grsai_text_base_url", grsai_text_base_url)
        self.grsai_text_base_url = QLineEdit(grsai_text_base_url)
        self.grsai_text_base_url.editingFinished.connect(
            self._save_grsai_text_settings
        )
        account_form.addRow("GRS 文本节点", self.grsai_text_base_url)
        self.grsai_text_model = ModelComboBox()
        self.grsai_text_model.setAccessibleName("GRS AI 文本模型")
        self.grsai_text_model.setText(
            settings.get("grsai_text_model", DEFAULT_GRSAI_TEXT_MODEL)
        )
        self.grsai_text_model.editingFinished.connect(
            self._save_grsai_text_settings
        )
        self.grsai_text_model.currentIndexChanged.connect(
            self._save_grsai_text_settings
        )
        account_form.addRow("GRS 文本模型", self.grsai_text_model)
        self.grsai_text_status = QLabel(
            "DeepSeek 与 GRS AI 文本凭据彼此独立；对话、摘要、主动消息和图片判断共用当前文本平台。"
        )
        self.grsai_text_status.setWordWrap(True)
        self.grsai_text_status.setProperty("muted", True)
        account_form.addRow("", self.grsai_text_status)
        self._text_grsai_rows = tuple(
            range(text_grsai_start, account_form.rowCount())
        )
        self._text_form = account_form
        layout.addWidget(account)

        siliconflow = QGroupBox("图片 AI")
        siliconflow_form = QFormLayout(siliconflow)
        configure_mobile_form(siliconflow_form)
        siliconflow_form.setSpacing(12)
        self.image_provider = QComboBox()
        self.image_provider.addItem("硅基流动", "siliconflow")
        self.image_provider.addItem("GRS AI", "grsai")
        image_provider = settings.get("image_provider", "siliconflow").lower()
        if image_provider not in {"siliconflow", "grsai"}:
            image_provider = "siliconflow"
        self.image_provider.setCurrentIndex(
            max(0, self.image_provider.findData(image_provider))
        )
        self.image_provider.currentIndexChanged.connect(
            self._save_image_provider
        )
        siliconflow_form.addRow("当前平台", self.image_provider)
        siliconflow_note = QLabel(
            "图片理解与角色自主生图共用当前图片平台，但与文本和 TTS 凭据完全分开。"
            "生图结果会在临时链接失效前立即下载到本机。"
        )
        siliconflow_note.setWordWrap(True)
        siliconflow_note.setProperty("muted", True)
        siliconflow_form.addRow("", siliconflow_note)
        image_siliconflow_start = siliconflow_form.rowCount()
        siliconflow_actions = responsive_row_layout()
        siliconflow_key_page = QPushButton("获取 API Key")
        siliconflow_key_page.setAccessibleName("打开硅基流动 API Key 页面")
        siliconflow_key_page.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl("https://cloud.siliconflow.cn/account/ak")
            )
        )
        siliconflow_models = QPushButton("模型与价格")
        siliconflow_models.setAccessibleName("打开硅基流动模型价格页面")
        siliconflow_models.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl("https://siliconflow.cn/pricing")
            )
        )
        siliconflow_actions.addWidget(siliconflow_key_page)
        siliconflow_actions.addWidget(siliconflow_models)
        self.siliconflow_image_refresh = QPushButton("刷新模型列表")
        self.siliconflow_image_refresh.setAccessibleName(
            "刷新硅基流动模型列表"
        )
        self.siliconflow_image_refresh.clicked.connect(
            lambda: self._refresh_models("siliconflow")
        )
        siliconflow_actions.addWidget(self.siliconflow_image_refresh)
        self._model_refresh_buttons.append(self.siliconflow_image_refresh)
        siliconflow_actions.addStretch(1)
        siliconflow_form.addRow("平台", siliconflow_actions)

        self.siliconflow_key_input = QLineEdit()
        self.siliconflow_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        siliconflow_key = getattr(
            credentials,
            "get_siliconflow_image_api_key",
            getattr(credentials, "get_siliconflow_api_key", lambda: ""),
        )()
        self.siliconflow_key_input.setPlaceholderText(
            "已安全保存" if siliconflow_key else "输入 sk- 开头的 API Key"
        )
        self.siliconflow_key_input.setAccessibleName("硅基流动图片 API Key")
        self.siliconflow_image_key_input = self.siliconflow_key_input
        siliconflow_form.addRow("硅基流动图片 Key", self.siliconflow_key_input)
        siliconflow_key_actions = responsive_row_layout()
        siliconflow_show = QPushButton("显示")
        siliconflow_show.setCheckable(True)
        siliconflow_show.toggled.connect(self._toggle_siliconflow_key)
        siliconflow_save = QPushButton("保存")
        siliconflow_save.setObjectName("primaryButton")
        siliconflow_save.clicked.connect(self._save_siliconflow_key)
        siliconflow_clear = QPushButton("清除")
        siliconflow_clear.clicked.connect(self._clear_siliconflow_key)
        siliconflow_key_actions.addWidget(siliconflow_show)
        siliconflow_key_actions.addWidget(siliconflow_save)
        siliconflow_key_actions.addWidget(siliconflow_clear)
        siliconflow_key_actions.addStretch(1)
        siliconflow_form.addRow("", siliconflow_key_actions)

        self.siliconflow_vision_model = ModelComboBox()
        self.siliconflow_vision_model.setAccessibleName(
            "硅基流动多模态识图模型"
        )
        self.siliconflow_vision_model.setText(
            settings.get(
                "siliconflow_vision_model", DEFAULT_SILICONFLOW_VISION_MODEL
            )
        )
        self.siliconflow_vision_model.editingFinished.connect(
            self._save_siliconflow_settings
        )
        self.siliconflow_vision_model.currentIndexChanged.connect(
            self._save_siliconflow_settings
        )
        siliconflow_form.addRow("硅基流动识图模型", self.siliconflow_vision_model)

        self.siliconflow_image_model = ModelComboBox()
        self.siliconflow_image_model.setAccessibleName("硅基流动生图模型")
        for label, value in (
            ("Z-Image Turbo（推荐，快速）", "Tongyi-MAI/Z-Image-Turbo"),
            ("Z-Image（高质量）", "Tongyi-MAI/Z-Image"),
            ("Qwen Image", "Qwen/Qwen-Image"),
            ("Kolors", "Kwai-Kolors/Kolors"),
        ):
            self.siliconflow_image_model.addItem(label, value)
        image_model = settings.get(
            "siliconflow_image_model",
            DEFAULT_SILICONFLOW_IMAGE_MODEL,
        )
        image_model_index = self.siliconflow_image_model.findData(image_model)
        if image_model_index >= 0:
            self.siliconflow_image_model.setCurrentIndex(image_model_index)
        else:
            self.siliconflow_image_model.setText(image_model)
        self.siliconflow_image_model.currentIndexChanged.connect(
            self._save_siliconflow_settings
        )
        self.siliconflow_image_model.editingFinished.connect(
            self._save_siliconflow_settings
        )
        siliconflow_form.addRow("硅基流动生图模型", self.siliconflow_image_model)

        self.siliconflow_image_size = QComboBox()
        for label, value in (
            ("方形 1024 × 1024", "1024x1024"),
            ("横图 1280 × 720", "1280x720"),
            ("竖图 720 × 1280", "720x1280"),
            ("Qwen 方形 1328 × 1328", "1328x1328"),
            ("Qwen 横图 1664 × 928", "1664x928"),
            ("Qwen 竖图 928 × 1664", "928x1664"),
        ):
            self.siliconflow_image_size.addItem(label, value)
        image_size = settings.get(
            "siliconflow_image_size",
            DEFAULT_SILICONFLOW_IMAGE_SIZE,
        )
        self.siliconflow_image_size.setCurrentIndex(
            max(0, self.siliconflow_image_size.findData(image_size))
        )
        self.siliconflow_image_size.currentIndexChanged.connect(
            self._save_siliconflow_settings
        )
        siliconflow_form.addRow("硅基流动图片尺寸", self.siliconflow_image_size)
        self._image_siliconflow_rows = tuple(
            range(image_siliconflow_start, siliconflow_form.rowCount())
        )

        image_grsai_start = siliconflow_form.rowCount()
        grsai_image_actions = responsive_row_layout()
        grsai_image_portal = QPushButton("GRS AI 控制台")
        grsai_image_portal.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl("https://grsai.com/zh/dashboard")
            )
        )
        grsai_image_docs = QPushButton("图片接口文档")
        grsai_image_docs.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl(
                    "https://grsai.com/zh/dashboard/documents/nano-banana"
                )
            )
        )
        grsai_image_actions.addWidget(grsai_image_portal)
        grsai_image_actions.addWidget(grsai_image_docs)
        self.grsai_image_refresh = QPushButton("刷新模型列表")
        self.grsai_image_refresh.setAccessibleName("刷新 GRS AI 模型列表")
        self.grsai_image_refresh.clicked.connect(
            lambda: self._refresh_models("grsai")
        )
        grsai_image_actions.addWidget(self.grsai_image_refresh)
        self._model_refresh_buttons.append(self.grsai_image_refresh)
        grsai_image_actions.addStretch(1)
        siliconflow_form.addRow("GRS AI", grsai_image_actions)
        self.grsai_image_key_input = QLineEdit()
        self.grsai_image_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        grsai_image_key = getattr(
            credentials, "get_grsai_image_api_key", lambda: ""
        )()
        self.grsai_image_key_input.setPlaceholderText(
            "已安全保存" if grsai_image_key else "输入 GRS AI 图片 API Key"
        )
        siliconflow_form.addRow("GRS 图片 Key", self.grsai_image_key_input)
        grsai_image_key_actions = responsive_row_layout()
        grsai_image_show = QPushButton("显示")
        grsai_image_show.setCheckable(True)
        grsai_image_show.toggled.connect(self._toggle_grsai_image_key)
        grsai_image_save = QPushButton("保存")
        grsai_image_save.setObjectName("primaryButton")
        grsai_image_save.clicked.connect(self._save_grsai_image_key)
        grsai_image_clear = QPushButton("清除")
        grsai_image_clear.clicked.connect(self._clear_grsai_image_key)
        grsai_image_key_actions.addWidget(grsai_image_show)
        grsai_image_key_actions.addWidget(grsai_image_save)
        grsai_image_key_actions.addWidget(grsai_image_clear)
        grsai_image_key_actions.addStretch(1)
        siliconflow_form.addRow("", grsai_image_key_actions)
        grsai_image_base_url = settings.get(
            "grsai_image_base_url", DEFAULT_GRSAI_API_BASE_URL
        )
        try:
            grsai_image_base_url = normalize_grsai_base_url(
                grsai_image_base_url
            )
        except ValueError:
            pass
        else:
            settings.set("grsai_image_base_url", grsai_image_base_url)
        self.grsai_image_base_url = QLineEdit(grsai_image_base_url)
        self.grsai_image_base_url.editingFinished.connect(
            self._save_grsai_image_settings
        )
        siliconflow_form.addRow("GRS 图片节点", self.grsai_image_base_url)
        self.grsai_vision_model = ModelComboBox()
        self.grsai_vision_model.setAccessibleName("GRS AI 多模态识图模型")
        self.grsai_vision_model.setText(
            settings.get("grsai_vision_model", DEFAULT_GRSAI_VISION_MODEL)
        )
        self.grsai_vision_model.editingFinished.connect(
            self._save_grsai_image_settings
        )
        self.grsai_vision_model.currentIndexChanged.connect(
            self._save_grsai_image_settings
        )
        siliconflow_form.addRow("GRS 识图模型", self.grsai_vision_model)
        self.grsai_image_model = ModelComboBox()
        self.grsai_image_model.setAccessibleName("GRS AI 生图模型")
        self.grsai_image_model.setText(
            settings.get("grsai_image_model", DEFAULT_GRSAI_IMAGE_MODEL)
        )
        self.grsai_image_model.editingFinished.connect(
            self._save_grsai_image_settings
        )
        self.grsai_image_model.currentIndexChanged.connect(
            self._save_grsai_image_settings
        )
        siliconflow_form.addRow("GRS 生图模型", self.grsai_image_model)
        self.grsai_image_size = QComboBox()
        for label, value in (
            ("方形 1024 × 1024", "1024x1024"),
            ("横图 1536 × 1024", "1536x1024"),
            ("竖图 1024 × 1536", "1024x1536"),
            ("2K 方形 2048 × 2048", "2048x2048"),
            ("2K 横图 2048 × 1152", "2048x1152"),
        ):
            self.grsai_image_size.addItem(label, value)
        grsai_size = settings.get(
            "grsai_image_size", DEFAULT_GRSAI_IMAGE_SIZE
        )
        self.grsai_image_size.setCurrentIndex(
            max(0, self.grsai_image_size.findData(grsai_size))
        )
        self.grsai_image_size.currentIndexChanged.connect(
            self._save_grsai_image_settings
        )
        siliconflow_form.addRow("GRS 图片尺寸", self.grsai_image_size)
        self._image_grsai_rows = tuple(
            range(image_grsai_start, siliconflow_form.rowCount())
        )
        self._image_form = siliconflow_form

        self.autonomous_images_enabled = QCheckBox(
            "允许 AI 角色在合适时机自主生成并发送图片"
        )
        self.autonomous_images_enabled.setChecked(
            settings.get("autonomous_images_enabled", "true").lower()
            in {"1", "true", "yes", "on"}
        )
        self.autonomous_images_enabled.toggled.connect(
            lambda checked: self._settings.set(
                "autonomous_images_enabled",
                "true" if checked else "false",
            )
        )
        siliconflow_form.addRow("角色发图", self.autonomous_images_enabled)
        autonomous_image_note = QLabel(
            "关键词/“发送图片”事件与 AI 语义判断会同时生效；用户明确要求"
            "角色发图片、照片或自拍时也会生成。每轮最多发送一张，避免重复调用。"
            "成功生图及 AI 判断会产生相应 API 用量。"
        )
        autonomous_image_note.setWordWrap(True)
        autonomous_image_note.setProperty("muted", True)
        siliconflow_form.addRow("", autonomous_image_note)

        self.siliconflow_status = QLabel(
            "当前图片提供商同时负责用户图片识别与角色自主生图；文本与语音不会使用这里的密钥。"
        )
        self.siliconflow_status.setWordWrap(True)
        self.siliconflow_status.setProperty("muted", True)
        siliconflow_form.addRow("", self.siliconflow_status)
        layout.addWidget(siliconflow)

        xfyun = QGroupBox("TTS")
        xfyun_form = QFormLayout(xfyun)
        configure_mobile_form(xfyun_form)
        xfyun_form.setSpacing(12)
        self.tts_provider = QComboBox()
        self.tts_provider.addItem(
            (
                "Android 系统 TTS（免费）"
                if is_android_platform()
                else "Edge TTS（免费）"
            ),
            "edge",
        )
        self.tts_provider.addItem("硅基流动 TTS", "siliconflow")
        self.tts_provider.addItem("科大讯飞超拟人 TTS", "xfyun")
        if not is_android_platform():
            self.tts_provider.addItem(
                "本地 IndexTTS2（声音克隆）", "indextts2"
            )
        tts_provider = settings.get("tts_provider", "edge")
        self.tts_provider.setCurrentIndex(
            max(0, self.tts_provider.findData(tts_provider))
        )
        self.tts_provider.currentIndexChanged.connect(
            self._save_tts_provider
        )
        xfyun_form.addRow("当前引擎", self.tts_provider)
        self.tts_provider_status = QLabel()
        self.tts_provider_status.setWordWrap(True)
        self.tts_provider_status.setProperty("muted", True)
        xfyun_form.addRow("", self.tts_provider_status)

        tts_siliconflow_start = xfyun_form.rowCount()
        siliconflow_tts_actions = responsive_row_layout()
        self.siliconflow_tts_refresh = QPushButton("刷新模型列表")
        self.siliconflow_tts_refresh.setAccessibleName(
            "刷新硅基流动 TTS 模型列表"
        )
        self.siliconflow_tts_refresh.clicked.connect(
            lambda: self._refresh_models("siliconflow")
        )
        siliconflow_tts_actions.addWidget(self.siliconflow_tts_refresh)
        siliconflow_tts_actions.addStretch(1)
        self._model_refresh_buttons.append(self.siliconflow_tts_refresh)
        xfyun_form.addRow("硅基流动", siliconflow_tts_actions)
        self.siliconflow_tts_key_input = QLineEdit()
        self.siliconflow_tts_key_input.setEchoMode(
            QLineEdit.EchoMode.Password
        )
        siliconflow_tts_key = getattr(
            credentials,
            "get_siliconflow_tts_api_key",
            getattr(credentials, "get_siliconflow_api_key", lambda: ""),
        )()
        self.siliconflow_tts_key_input.setPlaceholderText(
            "已安全保存"
            if siliconflow_tts_key
            else "输入硅基流动 TTS API Key"
        )
        xfyun_form.addRow("硅基流动 TTS Key", self.siliconflow_tts_key_input)
        siliconflow_tts_key_actions = responsive_row_layout()
        siliconflow_tts_show = QPushButton("显示")
        siliconflow_tts_show.setCheckable(True)
        siliconflow_tts_show.toggled.connect(
            self._toggle_siliconflow_tts_key
        )
        siliconflow_tts_save = QPushButton("保存")
        siliconflow_tts_save.setObjectName("primaryButton")
        siliconflow_tts_save.clicked.connect(
            self._save_siliconflow_tts_key
        )
        siliconflow_tts_clear = QPushButton("清除")
        siliconflow_tts_clear.clicked.connect(
            self._clear_siliconflow_tts_key
        )
        siliconflow_tts_key_actions.addWidget(siliconflow_tts_show)
        siliconflow_tts_key_actions.addWidget(siliconflow_tts_save)
        siliconflow_tts_key_actions.addWidget(siliconflow_tts_clear)
        siliconflow_tts_key_actions.addStretch(1)
        xfyun_form.addRow("", siliconflow_tts_key_actions)
        self.siliconflow_tts_model = ModelComboBox()
        self.siliconflow_tts_model.setAccessibleName("硅基流动 TTS 模型")
        self.siliconflow_tts_model.setText(
            settings.get(
                "siliconflow_tts_model", DEFAULT_SILICONFLOW_TTS_MODEL
            )
        )
        self.siliconflow_tts_model.editingFinished.connect(
            self._save_siliconflow_settings
        )
        self.siliconflow_tts_model.currentIndexChanged.connect(
            self._save_siliconflow_settings
        )
        xfyun_form.addRow("硅基流动 TTS 模型", self.siliconflow_tts_model)
        self.siliconflow_tts_voice = QComboBox()
        self.siliconflow_tts_voice.setEditable(True)
        for label, value in SILICONFLOW_VOICE_OPTIONS:
            self.siliconflow_tts_voice.addItem(label, value)
        tts_voice = settings.get("siliconflow_tts_voice", "auto")
        tts_voice_index = self.siliconflow_tts_voice.findData(tts_voice)
        if tts_voice_index >= 0:
            self.siliconflow_tts_voice.setCurrentIndex(tts_voice_index)
        else:
            self.siliconflow_tts_voice.setEditText(tts_voice)
        self.siliconflow_tts_voice.currentIndexChanged.connect(
            self._save_siliconflow_settings
        )
        self.siliconflow_tts_voice.lineEdit().editingFinished.connect(
            self._save_siliconflow_settings
        )
        xfyun_form.addRow("硅基流动 TTS 音色", self.siliconflow_tts_voice)
        self._tts_siliconflow_rows = tuple(
            range(tts_siliconflow_start, xfyun_form.rowCount())
        )
        tts_indextts2_start = xfyun_form.rowCount()
        indextts2_note = QLabel(
            "IndexTTS2 在本机 GPU 上常驻运行，台词不会上传到云端。"
            "首次启动需加载模型，6 GB 显存设备单条合成可能需数分钟。"
        )
        indextts2_note.setWordWrap(True)
        indextts2_note.setProperty("muted", True)
        xfyun_form.addRow("", indextts2_note)
        detected_root = discover_index_tts2_root(
            settings.get("indextts2_root", "")
        )
        self.indextts2_root = QLineEdit(
            str(detected_root or settings.get("indextts2_root", ""))
        )
        self.indextts2_root.setPlaceholderText("IndexTTS2 项目目录")
        self.indextts2_root.setAccessibleName("IndexTTS2 项目目录")
        self.indextts2_root.editingFinished.connect(
            self._save_indextts2_settings
        )
        xfyun_form.addRow("项目目录", self.indextts2_root)
        self.indextts2_base_url = QLineEdit(
            settings.get("indextts2_base_url", DEFAULT_INDEXTTS2_BASE_URL)
        )
        self.indextts2_base_url.setPlaceholderText(DEFAULT_INDEXTTS2_BASE_URL)
        self.indextts2_base_url.setAccessibleName("IndexTTS2 本地服务地址")
        self.indextts2_base_url.editingFinished.connect(
            self._save_indextts2_settings
        )
        xfyun_form.addRow("服务地址", self.indextts2_base_url)
        self.indextts2_auto_start = QCheckBox(
            "选择 IndexTTS2 引擎时自动启动本地服务"
        )
        self.indextts2_auto_start.setChecked(
            settings.get("indextts2_auto_start", "false").lower()
            in {"1", "true", "yes", "on"}
        )
        self.indextts2_auto_start.toggled.connect(
            self._save_indextts2_settings
        )
        xfyun_form.addRow("自动启动", self.indextts2_auto_start)
        cached_presets = deserialize_index_tts2_presets(
            settings.get("indextts2_presets", "")
        )
        self._indextts2_presets = cached_presets or INDEXTTS2_BUILTIN_PRESETS
        self.indextts2_preset = QComboBox()
        self.indextts2_preset.setEditable(True)
        self.indextts2_preset.setMinimumContentsLength(
            12 if self._mobile else 34
        )
        for preset_name in self._indextts2_presets:
            self.indextts2_preset.addItem(preset_name, preset_name)
        selected_preset = settings.get(
            "indextts2_preset", DEFAULT_INDEXTTS2_PRESET
        )
        selected_index = self.indextts2_preset.findData(selected_preset)
        if selected_index >= 0:
            self.indextts2_preset.setCurrentIndex(selected_index)
        else:
            self.indextts2_preset.setEditText(selected_preset)
        self.indextts2_preset.currentIndexChanged.connect(
            self._save_indextts2_settings
        )
        self.indextts2_preset.lineEdit().editingFinished.connect(
            self._save_indextts2_settings
        )
        xfyun_form.addRow("默认预设", self.indextts2_preset)
        indextts2_actions = responsive_row_layout()
        self.indextts2_start = QPushButton("启动本地服务")
        self.indextts2_start.clicked.connect(
            self._start_indextts2_service
        )
        self.indextts2_refresh = QPushButton("检测服务并刷新预设")
        self.indextts2_refresh.clicked.connect(
            self._refresh_indextts2_presets
        )
        indextts2_actions.addWidget(self.indextts2_start)
        indextts2_actions.addWidget(self.indextts2_refresh)
        indextts2_actions.addStretch(1)
        xfyun_form.addRow("本地服务", indextts2_actions)
        self.indextts2_status = QLabel(
            "尚未检测本地服务。"
        )
        self.indextts2_status.setWordWrap(True)
        self.indextts2_status.setProperty("muted", True)
        xfyun_form.addRow("", self.indextts2_status)
        self._tts_indextts2_rows = tuple(
            range(tts_indextts2_start, xfyun_form.rowCount())
        )
        tts_xfyun_start = xfyun_form.rowCount()
        xfyun_note = QLabel(
            "科大讯飞使用官方超拟人 WebSocket API。可选择 API Password，"
            "或 APPID + APIKey + APISecret 签名鉴权。自动音色优先使用"
            "官方默认免费 _flow 发音人；Pro/Mini 音色需单独开通权限。"
        )
        xfyun_note.setWordWrap(True)
        xfyun_note.setProperty("muted", True)
        xfyun_form.addRow("", xfyun_note)
        xfyun_actions = responsive_row_layout()
        xfyun_console = QPushButton("打开控制台")
        xfyun_console.setAccessibleName(
            "打开科大讯飞超拟人语音合成控制台"
        )
        xfyun_console.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl("https://console.xfyun.cn/services/uts")
            )
        )
        xfyun_docs = QPushButton("接口文档")
        xfyun_docs.setAccessibleName(
            "打开科大讯飞超拟人语音合成文档"
        )
        xfyun_docs.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl(
                    "https://www.xfyun.cn/doc/spark/"
                    "super%20smart-tts.html"
                )
            )
        )
        xfyun_actions.addWidget(xfyun_console)
        xfyun_actions.addWidget(xfyun_docs)
        xfyun_actions.addStretch(1)
        xfyun_form.addRow("平台", xfyun_actions)
        self.xfyun_tts_app_id = QLineEdit(
            settings.get("xfyun_tts_app_id", "")
        )
        self.xfyun_tts_app_id.setMaxLength(50)
        self.xfyun_tts_app_id.setPlaceholderText("输入 APPID")
        self.xfyun_tts_app_id.setAccessibleName("科大讯飞 APPID")
        self.xfyun_tts_app_id.editingFinished.connect(
            self._save_xfyun_settings
        )
        xfyun_form.addRow("APPID", self.xfyun_tts_app_id)
        self.xfyun_tts_auth_method = QComboBox()
        self.xfyun_tts_auth_method.addItem(
            "API Password（简易鉴权）", "password"
        )
        self.xfyun_tts_auth_method.addItem(
            "APIKey + APISecret（签名鉴权）", "hmac"
        )
        auth_method = settings.get(
            "xfyun_tts_auth_method", "password"
        ).lower()
        self.xfyun_tts_auth_method.setCurrentIndex(
            max(0, self.xfyun_tts_auth_method.findData(auth_method))
        )
        self.xfyun_tts_auth_method.currentIndexChanged.connect(
            self._save_xfyun_settings
        )
        xfyun_form.addRow("鉴权方式", self.xfyun_tts_auth_method)
        self.xfyun_tts_password_input = QLineEdit()
        self.xfyun_tts_password_input.setEchoMode(
            QLineEdit.EchoMode.Password
        )
        xfyun_password = getattr(
            credentials, "get_xfyun_tts_api_password", lambda: ""
        )()
        self.xfyun_tts_password_input.setPlaceholderText(
            "已安全保存" if xfyun_password else "输入 API Password"
        )
        self.xfyun_tts_password_input.setAccessibleName(
            "科大讯飞 API Password"
        )
        xfyun_form.addRow("API Password", self.xfyun_tts_password_input)
        xfyun_password_input_row = xfyun_form.rowCount() - 1
        xfyun_password_actions = responsive_row_layout()
        xfyun_show = QPushButton("显示")
        xfyun_show.setCheckable(True)
        xfyun_show.toggled.connect(self._toggle_xfyun_password)
        xfyun_save = QPushButton("保存")
        xfyun_save.setObjectName("primaryButton")
        xfyun_save.clicked.connect(self._save_xfyun_password)
        xfyun_clear = QPushButton("清除")
        xfyun_clear.clicked.connect(self._clear_xfyun_password)
        xfyun_password_actions.addWidget(xfyun_show)
        xfyun_password_actions.addWidget(xfyun_save)
        xfyun_password_actions.addWidget(xfyun_clear)
        xfyun_password_actions.addStretch(1)
        xfyun_form.addRow("", xfyun_password_actions)
        xfyun_password_actions_row = xfyun_form.rowCount() - 1
        self.xfyun_tts_api_key_input = QLineEdit()
        self.xfyun_tts_api_key_input.setEchoMode(
            QLineEdit.EchoMode.Password
        )
        xfyun_api_key = getattr(
            credentials, "get_xfyun_tts_api_key", lambda: ""
        )()
        self.xfyun_tts_api_key_input.setPlaceholderText(
            "已安全保存" if xfyun_api_key else "输入 APIKey"
        )
        self.xfyun_tts_api_key_input.setAccessibleName(
            "科大讯飞 APIKey"
        )
        xfyun_form.addRow("APIKey", self.xfyun_tts_api_key_input)
        xfyun_api_key_row = xfyun_form.rowCount() - 1
        self.xfyun_tts_api_secret_input = QLineEdit()
        self.xfyun_tts_api_secret_input.setEchoMode(
            QLineEdit.EchoMode.Password
        )
        xfyun_api_secret = getattr(
            credentials, "get_xfyun_tts_api_secret", lambda: ""
        )()
        self.xfyun_tts_api_secret_input.setPlaceholderText(
            "已安全保存" if xfyun_api_secret else "输入 APISecret"
        )
        self.xfyun_tts_api_secret_input.setAccessibleName(
            "科大讯飞 APISecret"
        )
        xfyun_form.addRow("APISecret", self.xfyun_tts_api_secret_input)
        xfyun_api_secret_row = xfyun_form.rowCount() - 1
        xfyun_hmac_actions = responsive_row_layout()
        xfyun_hmac_show = QPushButton("显示")
        xfyun_hmac_show.setCheckable(True)
        xfyun_hmac_show.toggled.connect(self._toggle_xfyun_hmac)
        xfyun_hmac_save = QPushButton("保存")
        xfyun_hmac_save.setObjectName("primaryButton")
        xfyun_hmac_save.clicked.connect(self._save_xfyun_hmac)
        xfyun_hmac_clear = QPushButton("清除")
        xfyun_hmac_clear.clicked.connect(self._clear_xfyun_hmac)
        xfyun_hmac_actions.addWidget(xfyun_hmac_show)
        xfyun_hmac_actions.addWidget(xfyun_hmac_save)
        xfyun_hmac_actions.addWidget(xfyun_hmac_clear)
        xfyun_hmac_actions.addStretch(1)
        xfyun_form.addRow("", xfyun_hmac_actions)
        xfyun_hmac_actions_row = xfyun_form.rowCount() - 1
        xfyun_voice = settings.get(
            "xfyun_tts_voice", DEFAULT_XFYUN_TTS_VOICE
        )
        self._xfyun_available_voices = deserialize_available_voices(
            settings.get("xfyun_tts_available_voices", "")
        )
        self.xfyun_tts_voice = QComboBox()
        self.xfyun_tts_voice.setEditable(True)
        self.xfyun_tts_voice.setMinimumContentsLength(
            12 if self._mobile else 34
        )
        options = (
            available_voice_options(
                self._xfyun_available_voices, current=xfyun_voice
            )
            if self._xfyun_available_voices
            else XFYUN_TTS_VOICE_OPTIONS
        )
        for label, value in options:
            self.xfyun_tts_voice.addItem(label, value)
        xfyun_voice_index = self.xfyun_tts_voice.findData(xfyun_voice)
        if xfyun_voice_index >= 0:
            self.xfyun_tts_voice.setCurrentIndex(xfyun_voice_index)
        else:
            self.xfyun_tts_voice.setEditText(xfyun_voice)
        self.xfyun_tts_voice.currentIndexChanged.connect(
            self._save_xfyun_settings
        )
        self.xfyun_tts_voice.lineEdit().editingFinished.connect(
            self._save_xfyun_settings
        )
        xfyun_form.addRow("发音人", self.xfyun_tts_voice)
        xfyun_voice_actions = responsive_row_layout()
        self.xfyun_voice_refresh = QPushButton("检测当前账号可用音色")
        self.xfyun_voice_refresh.setAccessibleName(
            "检测科大讯飞当前账号可用发音人"
        )
        self.xfyun_voice_refresh.setToolTip(
            "逐项合成一个测试字符并缓存可用音色；成功项会消耗极少量额度。"
        )
        self.xfyun_voice_refresh.clicked.connect(
            self._refresh_xfyun_voices
        )
        xfyun_voice_actions.addWidget(self.xfyun_voice_refresh)
        xfyun_voice_actions.addStretch(1)
        xfyun_form.addRow("账号音色", xfyun_voice_actions)
        xfyun_plain_note = QLabel(
            "讯飞按原台词一次合成，固定使用发音人的默认语速、语调和音量；"
            "不会调用文本 AI 或插入额外韵律标记。"
        )
        xfyun_plain_note.setWordWrap(True)
        xfyun_plain_note.setProperty("muted", True)
        xfyun_form.addRow("合成方式", xfyun_plain_note)
        self.xfyun_tts_status = QLabel(

                f"已缓存 {len(self._xfyun_available_voices)} 个当前账号可用音色。"
                if self._xfyun_available_voices
                else "尚未检测当前账号的音色权限。"

        )
        self.xfyun_tts_status.setWordWrap(True)
        self.xfyun_tts_status.setProperty("muted", True)
        xfyun_form.addRow("", self.xfyun_tts_status)
        self._tts_xfyun_rows = tuple(
            range(tts_xfyun_start, xfyun_form.rowCount())
        )
        self._xfyun_password_rows = (
            xfyun_password_input_row,
            xfyun_password_actions_row,
        )
        self._xfyun_hmac_rows = (
            xfyun_api_key_row,
            xfyun_api_secret_row,
            xfyun_hmac_actions_row,
        )
        self._tts_form = xfyun_form
        self._update_tts_provider_status()
        layout.addWidget(xfyun)

        roleplay = QGroupBox("角色扮演")
        roleplay_form = QFormLayout(roleplay)
        configure_mobile_form(roleplay_form)
        roleplay_form.setSpacing(12)
        self.user_name = QLineEdit(settings.get("user_name", "用户"))
        self.user_name.setMaxLength(32)
        self.user_name.setPlaceholderText("用户")
        self.user_name.setAccessibleName("角色对你的称呼")
        self.user_name.editingFinished.connect(
            self._save_roleplay_settings
        )
        roleplay_form.addRow("你的称呼", self.user_name)
        self.user_persona = QTextEdit(settings.get("user_persona", ""))
        self.user_persona.setPlaceholderText(
            "可选：职业、兴趣、相处偏好等。当前对话中的最新表达始终优先。"
        )
        self.user_persona.setAccessibleName("用户人物简介")
        self.user_persona.setFixedHeight(82)
        self.user_persona.textChanged.connect(self._save_roleplay_settings)
        roleplay_form.addRow("人物简介", self.user_persona)
        self.roleplay_temperature = QDoubleSpinBox()
        self.roleplay_temperature.setRange(0.0, 2.0)
        self.roleplay_temperature.setSingleStep(0.1)
        self.roleplay_temperature.setDecimals(1)
        try:
            roleplay_temperature = float(
                settings.get("roleplay_temperature", "1.3")
            )
        except ValueError:
            roleplay_temperature = 1.3
        self.roleplay_temperature.setValue(roleplay_temperature)
        self.roleplay_temperature.setAccessibleName("角色回复创造性")
        self.roleplay_temperature.valueChanged.connect(
            self._save_roleplay_settings
        )
        roleplay_form.addRow("回复创造性", self.roleplay_temperature)
        self.role_memory_enabled = QCheckBox(
            "自动维护场景、关系、共同记忆和未完事件"
        )
        self.role_memory_enabled.setChecked(
            settings.get("role_memory_enabled", "true").lower()
            in {"1", "true", "yes", "on"}
        )
        self.role_memory_enabled.toggled.connect(
            self._save_roleplay_settings
        )
        roleplay_form.addRow("连续性记忆", self.role_memory_enabled)
        roleplay_note = QLabel(
            "默认创造性 1.3 适合日常角色对话；仅作用于 V4 Flash 角色会话。"
            "连续性状态与列表摘要在同一次后台请求中更新，不会额外增加请求次数。"
        )
        roleplay_note.setWordWrap(True)
        roleplay_note.setProperty("muted", True)
        roleplay_form.addRow("", roleplay_note)
        layout.addWidget(roleplay)

        discovery = QGroupBox("新角色发现")
        discovery_form = QFormLayout(discovery)
        configure_mobile_form(discovery_form)
        discovery_form.setSpacing(12)
        self.character_discovery_enabled = QCheckBox(
            "在随机时间生成新角色，并让对方作为新联系人发来第一条消息"
        )
        self.character_discovery_enabled.setChecked(
            settings.get("character_discovery_enabled", "false").lower()
            in {"1", "true", "yes", "on"}
        )
        self.character_discovery_enabled.toggled.connect(
            self._save_character_discovery_settings
        )
        discovery_form.addRow("自动发现", self.character_discovery_enabled)

        discovery_interval_row = responsive_row_layout()
        self.character_discovery_min_minutes = QSpinBox()
        self.character_discovery_min_minutes.setRange(15, 10_080)
        self.character_discovery_min_minutes.setSuffix(" 分钟")
        self.character_discovery_min_minutes.setValue(
            self._bounded_integer_setting(
                "character_discovery_min_minutes", 180, 15, 10_080
            )
        )
        self.character_discovery_max_minutes = QSpinBox()
        self.character_discovery_max_minutes.setRange(15, 10_080)
        self.character_discovery_max_minutes.setSuffix(" 分钟")
        self.character_discovery_max_minutes.setValue(
            max(
                self.character_discovery_min_minutes.value(),
                self._bounded_integer_setting(
                    "character_discovery_max_minutes", 720, 15, 10_080
                ),
            )
        )
        discovery_interval_row.addWidget(self.character_discovery_min_minutes)
        discovery_interval_row.addWidget(QLabel("至"))
        discovery_interval_row.addWidget(self.character_discovery_max_minutes)
        discovery_interval_row.addStretch(1)
        discovery_form.addRow("随机间隔", discovery_interval_row)
        self.character_discovery_min_minutes.valueChanged.connect(
            self._save_character_discovery_settings
        )
        self.character_discovery_max_minutes.valueChanged.connect(
            self._save_character_discovery_settings
        )

        self.character_discovery_daily_limit = QSpinBox()
        self.character_discovery_daily_limit.setRange(1, 10)
        self.character_discovery_daily_limit.setSuffix(" 位/天")
        self.character_discovery_daily_limit.setValue(
            self._bounded_integer_setting(
                "character_discovery_daily_limit", 1, 1, 10
            )
        )
        self.character_discovery_daily_limit.valueChanged.connect(
            self._save_character_discovery_settings
        )
        discovery_form.addRow("每日上限", self.character_discovery_daily_limit)
        discovery_note = QLabel(
            "仅在软件运行时计时。每次生成会调用当前文本平台；成功后角色卡写入本机，"
            "并创建一条新联系人会话。若当前图片平台已配置 API Key，应用会继续在后台"
            "生成正方形角色头像；头像失败不会影响角色和会话，之后可自动补齐。生成失败、"
            "重名或当日达到上限时不会新增角色，也不会占用成功名额。自动生成的角色可照常"
            "编辑或删除，手动选择的头像不会被自动覆盖。"
        )
        discovery_note.setWordWrap(True)
        discovery_note.setProperty("muted", True)
        discovery_form.addRow("", discovery_note)
        self._update_character_discovery_controls()
        layout.addWidget(discovery)

        appearance = QGroupBox("聊天与外观")
        appearance_form = QFormLayout(appearance)
        configure_mobile_form(appearance_form)
        self.default_model = QComboBox()
        self._refresh_default_model_options()
        self.default_model.currentIndexChanged.connect(self._save_model)
        appearance_form.addRow("新会话默认模型", self.default_model)
        self.theme = QComboBox()
        self.theme.addItem("跟随系统", "system")
        self.theme.addItem("浅色", "light")
        self.theme.addItem("深色", "dark")
        theme_value = settings.get("theme", "system")
        self.theme.setCurrentIndex(max(0, self.theme.findData(theme_value)))
        self.theme.currentIndexChanged.connect(self._theme_selected)
        appearance_form.addRow("主题", self.theme)
        self.tts_auto_play = QCheckBox(
            "AI 回复完成后自动朗读角色台词（跳过动作与旁白）"
        )
        self.tts_auto_play.setChecked(
            settings.get("tts_auto_play", "true").lower()
            in {"1", "true", "yes", "on"}
        )
        self.tts_auto_play.toggled.connect(
            lambda checked: self._settings.set(
                "tts_auto_play", "true" if checked else "false"
            )
        )
        appearance_form.addRow("语音", self.tts_auto_play)
        notification_row = responsive_row_layout()
        self.notification_sound_enabled = QCheckBox(
            "收到完整 AI 消息后播放提示音"
        )
        self.notification_sound_enabled.setChecked(
            settings.get("notification_sound_enabled", "true").lower()
            in {"1", "true", "yes", "on"}
        )
        self.notification_sound_enabled.toggled.connect(
            lambda checked: self._settings.set(
                "notification_sound_enabled",
                "true" if checked else "false",
            )
        )
        preview_notification = QPushButton("试听")
        preview_notification.setAccessibleName("试听消息提示音")
        preview_notification.clicked.connect(
            self.notification_sound_preview_requested
        )
        notification_row.addWidget(self.notification_sound_enabled)
        notification_row.addWidget(preview_notification)
        notification_row.addStretch(1)
        appearance_form.addRow("消息提示", notification_row)

        self.proactive_enabled = QCheckBox("允许角色随机主动发消息")
        self.proactive_enabled.setChecked(
            settings.get("proactive_enabled", "false").lower()
            in {"1", "true", "yes", "on"}
        )
        self.proactive_enabled.toggled.connect(self._save_proactive_settings)
        appearance_form.addRow("主动消息", self.proactive_enabled)

        interval_row = responsive_row_layout()
        self.proactive_min_minutes = QSpinBox()
        self.proactive_min_minutes.setRange(5, 1_440)
        self.proactive_min_minutes.setSuffix(" 分钟")
        self.proactive_min_minutes.setValue(
            self._integer_setting("proactive_min_minutes", 30)
        )
        self.proactive_max_minutes = QSpinBox()
        self.proactive_max_minutes.setRange(5, 1_440)
        self.proactive_max_minutes.setSuffix(" 分钟")
        self.proactive_max_minutes.setValue(
            max(
                self.proactive_min_minutes.value(),
                self._integer_setting("proactive_max_minutes", 120),
            )
        )
        interval_row.addWidget(self.proactive_min_minutes)
        interval_row.addWidget(QLabel("至"))
        interval_row.addWidget(self.proactive_max_minutes)
        interval_row.addStretch(1)
        appearance_form.addRow("随机间隔", interval_row)
        self.proactive_min_minutes.valueChanged.connect(
            self._save_proactive_settings
        )
        self.proactive_max_minutes.valueChanged.connect(
            self._save_proactive_settings
        )
        proactive_note = QLabel(
            "仅在软件运行且当前为角色会话时触发；角色会读取设备本地时间，"
            "按清晨、午间、傍晚、晚间和深夜选择自然话题。每次会调用 DeepSeek"
            "或 GRS AI 当前文本平台并产生相应 API 用量。"
        )
        proactive_note.setWordWrap(True)
        proactive_note.setProperty("muted", True)
        appearance_form.addRow("", proactive_note)
        layout.addWidget(appearance)

        privacy = QGroupBox("数据与隐私")
        privacy_layout = QVBoxLayout(privacy)
        note = QLabel(
            "会话保存在本机应用数据目录；发送的内容会传输到当前文本平台。"
            "用户图片会发送到当前图片平台生成画面描述，"
            "启用角色自主发图后，角色回复会在本机分类为对白、旁白和发图事件；"
            "只有发图事件的绘图提示词会发送到当前图片平台并立即下载返回图片。"
            "AI 回复完成后还会发送给当前文本平台生成消息列表摘要；角色会话同时更新"
            "本机保存的场景、关系、共同记忆和未完事件状态；"
            "启用主动消息后，角色会在随机间隔再次调用当前文本平台，并结合设备"
            "当前本地日期、星期、时区和时段开启合适话题。"
            "启用新角色发现后，你填写的称呼、人物简介以及现有角色的名称和简短性格"
            "会发送到当前文本平台，用于生成不重名的新联系人角色卡；生成结果保存在本机。"
            "自动角色的外貌、性格、场景和题材标签还会发送到当前图片平台生成头像，"
            "头像文件保存在本机应用数据目录。"
            "启用自动朗读或手动播放时，只会提取 AI 最终回复中角色真正说出口的"
            "台词，并按语音引擎设置发送到 Microsoft Edge 在线语音服务、"
            "科大讯飞或硅基流动；选择 IndexTTS2 时台词只发往本机回环接口。"
            "思考过程、旁白和括号内动作不会发送，"
            "生成的音频只保存在本机缓存。TTS 失败不影响聊天。"
        )
        note.setWordWrap(True)
        note.setProperty("muted", True)
        privacy_layout.addWidget(note)
        clear_data = QPushButton("清空全部会话")
        clear_data.clicked.connect(self.data_clear_requested)
        privacy_layout.addWidget(clear_data)
        layout.addWidget(privacy)

        about = QLabel(f"{PRODUCT_NAME} {PRODUCT_VERSION}")
        about.setProperty("muted", True)
        layout.addWidget(about)
        if self._mobile:
            for group in content.findChildren(QGroupBox):
                group.setMinimumWidth(0)
                group.setSizePolicy(
                    QSizePolicy.Policy.Ignored,
                    QSizePolicy.Policy.Preferred,
                )
            for label in content.findChildren(QLabel):
                if label.wordWrap():
                    label.setMinimumWidth(0)
                    label.setSizePolicy(
                        QSizePolicy.Policy.Ignored,
                        QSizePolicy.Policy.Preferred,
                    )
        layout.addStretch(1)
        self.scroll.setWidget(content)
        root.addWidget(self.scroll, 1)

        for provider in ("grsai", "siliconflow"):
            cached = deserialize_models(
                self._settings.get(f"model_catalog_{provider}", "")
            )
            if cached:
                self._apply_model_catalog(provider, cached, persist=False)
        self._update_provider_sections()
        if (
            self.tts_provider.currentData() == "indextts2"
            and self.indextts2_auto_start.isChecked()
        ):
            QTimer.singleShot(0, self._start_indextts2_service)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._catalog_auto_refresh_started:
            return
        self._catalog_auto_refresh_started = True
        QTimer.singleShot(0, self._refresh_active_catalogs)

    def _refresh_active_catalogs(self) -> None:
        providers: list[str] = []
        if (
            self.text_provider.currentData() == "grsai"
            or self.image_provider.currentData() == "grsai"
        ):
            providers.append("grsai")
        if (
            self.image_provider.currentData() == "siliconflow"
            or self.tts_provider.currentData() == "siliconflow"
        ) and self._siliconflow_catalog_key():
            providers.append("siliconflow")
        for provider in providers:
            self._ensure_model_catalog(provider)

    def _ensure_model_catalog(self, provider: str) -> None:
        if provider not in {"grsai", "siliconflow"}:
            return
        if deserialize_models(
            self._settings.get(f"model_catalog_{provider}", "")
        ):
            return
        if provider == "siliconflow" and not self._siliconflow_catalog_key():
            return
        self._queue_model_refresh(provider)

    def _queue_model_refresh(self, provider: str) -> None:
        if provider == self._model_refresh_provider:
            return
        if provider not in self._model_refresh_queue:
            self._model_refresh_queue.append(provider)
        if self._model_thread is None or not self._model_thread.isRunning():
            next_provider = self._model_refresh_queue.pop(0)
            self._refresh_models(next_provider)

    @staticmethod
    def _set_layout_visible(layout, visible: bool) -> None:
        for index in range(layout.count()):
            item = layout.itemAt(index)
            widget = item.widget()
            if widget is not None:
                widget.setVisible(visible)
            child_layout = item.layout()
            if child_layout is not None:
                SettingsPage._set_layout_visible(child_layout, visible)

    @classmethod
    def _set_form_rows_visible(
        cls,
        form: QFormLayout,
        rows: tuple[int, ...],
        visible: bool,
    ) -> None:
        if hasattr(form, "setRowVisible"):
            for row in rows:
                form.setRowVisible(row, visible)
            return
        roles = (
            QFormLayout.ItemRole.LabelRole,
            QFormLayout.ItemRole.FieldRole,
            QFormLayout.ItemRole.SpanningRole,
        )
        for row in rows:
            for role in roles:
                item = form.itemAt(row, role)
                if item is None:
                    continue
                widget = item.widget()
                if widget is not None:
                    widget.setVisible(visible)
                child_layout = item.layout()
                if child_layout is not None:
                    cls._set_layout_visible(child_layout, visible)

    def _update_provider_sections(self) -> None:
        text_provider = self.text_provider.currentData() or "deepseek"
        self._set_form_rows_visible(
            self._text_form,
            self._text_deepseek_rows,
            text_provider == "deepseek",
        )
        self._set_form_rows_visible(
            self._text_form,
            self._text_grsai_rows,
            text_provider == "grsai",
        )

        image_provider = self.image_provider.currentData() or "siliconflow"
        self._set_form_rows_visible(
            self._image_form,
            self._image_siliconflow_rows,
            image_provider == "siliconflow",
        )
        self._set_form_rows_visible(
            self._image_form,
            self._image_grsai_rows,
            image_provider == "grsai",
        )

        tts_provider = self.tts_provider.currentData() or "edge"
        self._set_form_rows_visible(
            self._tts_form,
            self._tts_siliconflow_rows,
            tts_provider == "siliconflow",
        )
        self._set_form_rows_visible(
            self._tts_form,
            self._tts_xfyun_rows,
            tts_provider == "xfyun",
        )
        self._set_form_rows_visible(
            self._tts_form,
            self._tts_indextts2_rows,
            tts_provider == "indextts2",
        )
        if tts_provider == "xfyun":
            auth_method = self.xfyun_tts_auth_method.currentData() or "password"
            self._set_form_rows_visible(
                self._tts_form,
                self._xfyun_password_rows,
                auth_method == "password",
            )
            self._set_form_rows_visible(
                self._tts_form,
                self._xfyun_hmac_rows,
                auth_method == "hmac",
            )

    @staticmethod
    def _populate_model_combo(
        combo: ModelComboBox,
        models: tuple[ProviderModel, ...],
        capability: str,
    ) -> None:
        current = combo.text()
        choices = models_for_capability(models, capability)
        combo.blockSignals(True)
        combo.clear()
        for model in choices:
            combo.addItem(model.label, model.id)
            if model.description:
                combo.setItemData(
                    combo.count() - 1,
                    model.description,
                    3,
                )
        if current and combo.findData(current) < 0:
            combo.addItem(f"{current}  [自定义]", current)
        selected = combo.findData(current)
        if selected >= 0:
            combo.setCurrentIndex(selected)
        elif combo.count():
            combo.setCurrentIndex(0)
        else:
            combo.setText(current)
        combo.blockSignals(False)

    def _apply_model_catalog(
        self,
        provider: str,
        models: tuple[ProviderModel, ...],
        *,
        persist: bool,
    ) -> None:
        usable = tuple(model for model in models if model.provider == provider)
        if not usable:
            return
        if persist:
            self._settings.set(
                f"model_catalog_{provider}", serialize_models(usable)
            )
        if provider == "grsai":
            self._populate_model_combo(
                self.grsai_text_model, usable, "chat"
            )
            self._populate_model_combo(
                self.grsai_vision_model, usable, "vision"
            )
            self._populate_model_combo(
                self.grsai_image_model, usable, "image_generation"
            )
            if hasattr(self, "default_model"):
                self._refresh_default_model_options()
        elif provider == "siliconflow":
            self._populate_model_combo(
                self.siliconflow_vision_model, usable, "vision"
            )
            self._populate_model_combo(
                self.siliconflow_image_model, usable, "image_generation"
            )
            self._populate_model_combo(
                self.siliconflow_tts_model, usable, "tts"
            )

    def _siliconflow_catalog_key(self) -> str:
        for name in (
            "get_siliconflow_image_api_key",
            "get_siliconflow_tts_api_key",
            "get_siliconflow_api_key",
        ):
            getter = getattr(self._credentials, name, None)
            if callable(getter):
                value = str(getter() or "").strip()
                if value:
                    return value
        return ""

    def _set_model_refresh_status(self, provider: str, text: str) -> None:
        if provider == "grsai":
            self.grsai_text_status.setText(text)
            if self.image_provider.currentData() == "grsai":
                self.siliconflow_status.setText(text)
        else:
            self.siliconflow_status.setText(text)
            if self.tts_provider.currentData() == "siliconflow":
                self.tts_provider_status.setText(text)

    def _refresh_models(self, provider: str) -> None:
        if self._model_thread is not None and self._model_thread.isRunning():
            if provider != self._model_refresh_provider:
                self._queue_model_refresh(provider)
            self._set_model_refresh_status(
                provider, "另一份模型列表正在刷新；本次刷新已排队。"
            )
            return
        api_key = (
            self._siliconflow_catalog_key()
            if provider == "siliconflow"
            else ""
        )
        if provider == "siliconflow" and not api_key:
            self._set_model_refresh_status(
                provider, "请先保存硅基流动图片或 TTS API Key，再刷新模型列表。"
            )
            return
        self._model_refresh_provider = provider
        for button in self._model_refresh_buttons:
            button.setEnabled(False)
        self._set_model_refresh_status(provider, "正在读取官方模型列表…")

        thread = QThread(self)
        worker = ModelCatalogWorker(provider, api_key)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._models_refreshed)
        worker.failed.connect(self._models_refresh_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._model_refresh_finished)
        self._model_thread = thread
        self._model_worker = worker
        thread.start()

    @Slot(str, object)
    def _models_refreshed(self, provider: str, models: object) -> None:
        catalog = tuple(
            model for model in models if isinstance(model, ProviderModel)
        )
        self._apply_model_catalog(provider, catalog, persist=True)
        counts = {
            capability: len(models_for_capability(catalog, capability))
            for capability in ("chat", "vision", "image_generation", "tts")
        }
        if provider == "grsai":
            detail = (
                f"对话 {counts['chat']}、多模态 {counts['vision']}、"
                f"生图 {counts['image_generation']}"
            )
        else:
            detail = (
                f"多模态 {counts['vision']}、生图 {counts['image_generation']}、"
                f"TTS {counts['tts']}"
            )
        self._set_model_refresh_status(
            provider, f"模型列表已更新（{detail}），并已缓存到本机。"
        )

    @Slot(str, str)
    def _models_refresh_failed(self, provider: str, error: str) -> None:
        self._set_model_refresh_status(
            provider,
            f"模型列表刷新失败：{error} 已保留当前选择和本机缓存。",
        )

    @Slot()
    def _model_refresh_finished(self) -> None:
        self._model_worker = None
        self._model_thread = None
        self._model_refresh_provider = ""
        for button in self._model_refresh_buttons:
            button.setEnabled(True)
        if self._model_refresh_queue:
            provider = self._model_refresh_queue.pop(0)
            QTimer.singleShot(0, lambda: self._refresh_models(provider))

    def shutdown_model_refresh(self) -> None:
        self._model_refresh_queue.clear()
        thread = self._model_thread
        if thread is not None and thread.isRunning():
            thread.requestInterruption()
            thread.quit()
            thread.wait(40_000)
        voice_thread = self._xfyun_voice_thread
        if voice_thread is not None and voice_thread.isRunning():
            voice_thread.requestInterruption()
            voice_thread.quit()
            voice_thread.wait(40_000)

    def _refresh_xfyun_voices(self) -> None:
        if (
            self._xfyun_voice_thread is not None
            and self._xfyun_voice_thread.isRunning()
        ):
            return
        self._save_xfyun_settings()
        app_id = self.xfyun_tts_app_id.text().strip()
        auth_method = self.xfyun_tts_auth_method.currentData() or "password"
        password = ""
        api_key = ""
        api_secret = ""
        if auth_method == "hmac":
            api_key = getattr(
                self._credentials, "get_xfyun_tts_api_key", lambda: ""
            )()
            api_secret = getattr(
                self._credentials,
                "get_xfyun_tts_api_secret",
                lambda: "",
            )()
        else:
            password = getattr(
                self._credentials,
                "get_xfyun_tts_api_password",
                lambda: "",
            )()
        if not app_id or not (
            password or (api_key and api_secret)
        ):
            self.xfyun_tts_status.setText(
                "请先填写 APPID，并保存当前鉴权方式所需的凭据。"
            )
            return
        self.xfyun_voice_refresh.setEnabled(False)
        self.xfyun_tts_status.setText(
            "正在逐项验证官方音色；预计约 1 分钟，请保持网络连接。"
        )
        thread = QThread(self)
        worker = XfyunVoiceCatalogWorker(
            app_id, password, api_key, api_secret
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._xfyun_voice_progress)
        worker.completed.connect(self._xfyun_voices_refreshed)
        worker.failed.connect(self._xfyun_voices_refresh_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._xfyun_voice_refresh_finished)
        self._xfyun_voice_thread = thread
        self._xfyun_voice_worker = worker
        thread.start()

    @Slot(int, int, int)
    def _xfyun_voice_progress(
        self, completed: int, total: int, available: int
    ) -> None:
        self.xfyun_tts_status.setText(
            f"正在检测音色 {completed}/{total}；已确认 {available} 个可用。"
        )

    @Slot(object, object)
    def _xfyun_voices_refreshed(self, voice_ids, errors) -> None:
        available = tuple(
            voice_id
            for voice_id in voice_ids
            if isinstance(voice_id, str)
        )
        if not available:
            self.xfyun_tts_status.setText(
                "未检测到可用音色。请核对 APPID 与凭据是否属于同一应用，并在控制台开通发音人。"
            )
            return
        previous = (
            self.xfyun_tts_voice.currentData()
            or self.xfyun_tts_voice.currentText().strip()
            or "auto"
        )
        self._xfyun_available_voices = available
        self._settings.set(
            "xfyun_tts_available_voices",
            serialize_available_voices(available),
        )
        self._settings.set(
            "xfyun_tts_voice_checked_at",
            datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        self.xfyun_tts_voice.blockSignals(True)
        self.xfyun_tts_voice.clear()
        for label, voice_id in available_voice_options(
            available, current=previous
        ):
            self.xfyun_tts_voice.addItem(label, voice_id)
        selected = self.xfyun_tts_voice.findData(previous)
        if previous not in {"auto", *available}:
            selected = self.xfyun_tts_voice.findData("auto")
        self.xfyun_tts_voice.setCurrentIndex(max(0, selected))
        self.xfyun_tts_voice.blockSignals(False)
        self._save_xfyun_settings()
        denied = int((errors or {}).get("11200", 0))
        self.xfyun_tts_status.setText(
            f"检测完成：当前账号可调用 {len(available)} 个音色"
            f"，另有 {denied} 个未开通；下拉框已仅保留可用项。"
        )

    @Slot(str)
    def _xfyun_voices_refresh_failed(self, error: str) -> None:
        self.xfyun_tts_status.setText(
            f"音色检测失败：{error} 已保留上次缓存和当前选择。"
        )

    @Slot()
    def _xfyun_voice_refresh_finished(self) -> None:
        self._xfyun_voice_worker = None
        self._xfyun_voice_thread = None
        self.xfyun_voice_refresh.setEnabled(True)

    def _toggle_key(self, visible: bool) -> None:
        self.key_input.setEchoMode(
            QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        )

    def _save_key(self) -> None:
        warning = ""
        try:
            self._credentials.save_api_key(self.key_input.text())
        except ValueError as exc:
            self.key_status.setText(str(exc))
            return
        except RuntimeError as exc:
            warning = str(exc)
        self.key_input.clear()
        self.key_input.setPlaceholderText(
            "本次运行有效" if warning else "已安全保存"
        )
        self.key_status.setText(warning or "API Key 已安全保存。")
        self.credentials_changed.emit()

    def _clear_key(self) -> None:
        self._credentials.clear_api_key()
        self.key_input.clear()
        self.key_input.setPlaceholderText("输入你的 API Key")
        self.key_status.setText("API Key 已清除。")
        self.credentials_changed.emit()

    def _save_text_provider(self, index: int) -> None:
        previous_provider = self._settings.get(
            "text_provider", "deepseek"
        ).lower()
        if (
            previous_provider == "deepseek"
            and hasattr(self, "default_model")
            and self.default_model.currentData()
        ):
            self._settings.set(
                "deepseek_default_model", self.default_model.currentData()
            )
        provider = self.text_provider.itemData(index) or "deepseek"
        self._settings.set("text_provider", provider)
        if hasattr(self, "_text_form"):
            self._update_provider_sections()
            if self.isVisible():
                self._ensure_model_catalog(str(provider))
        if hasattr(self, "default_model"):
            self._refresh_default_model_options()
        self.text_settings_changed.emit()

    def _toggle_grsai_text_key(self, visible: bool) -> None:
        self.grsai_text_key_input.setEchoMode(
            QLineEdit.EchoMode.Normal
            if visible
            else QLineEdit.EchoMode.Password
        )

    def _save_grsai_text_key(self) -> None:
        save = getattr(self._credentials, "save_grsai_text_api_key", None)
        if not callable(save):
            self.grsai_text_status.setText("当前凭据存储不支持 GRS AI 文本 Key。")
            return
        warning = ""
        try:
            save(self.grsai_text_key_input.text())
        except ValueError as exc:
            self.grsai_text_status.setText(str(exc))
            return
        except RuntimeError as exc:
            warning = str(exc)
        self.grsai_text_key_input.clear()
        self.grsai_text_key_input.setPlaceholderText(
            "本次运行有效" if warning else "已安全保存"
        )
        self.grsai_text_status.setText(
            warning or "GRS AI 文本 API Key 已安全保存。"
        )
        self.credentials_changed.emit()
        self._refresh_models("grsai")

    def _clear_grsai_text_key(self) -> None:
        clear = getattr(self._credentials, "clear_grsai_text_api_key", None)
        if callable(clear):
            clear()
        self.grsai_text_key_input.clear()
        self.grsai_text_key_input.setPlaceholderText("输入 GRS AI 文本 API Key")
        self.grsai_text_status.setText("GRS AI 文本 API Key 已清除。")
        self.credentials_changed.emit()

    def _save_grsai_text_settings(self, *_args) -> None:
        try:
            base_url = normalize_grsai_base_url(
                self.grsai_text_base_url.text()
                or DEFAULT_GRSAI_API_BASE_URL
            )
        except ValueError as exc:
            self.grsai_text_status.setText(str(exc))
            return
        model = (
            self.grsai_text_model.text().strip()
            or DEFAULT_GRSAI_TEXT_MODEL
        )
        self.grsai_text_base_url.setText(base_url)
        self.grsai_text_model.blockSignals(True)
        self.grsai_text_model.setText(model)
        self.grsai_text_model.blockSignals(False)
        self._settings.set("grsai_text_base_url", base_url)
        self._settings.set("grsai_text_model", model)
        self._refresh_default_model_options()
        self.text_settings_changed.emit()

    def _toggle_siliconflow_key(self, visible: bool) -> None:
        self.siliconflow_key_input.setEchoMode(
            QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        )

    def _save_siliconflow_key(self) -> None:
        save = getattr(
            self._credentials, "save_siliconflow_image_api_key", None
        )
        if not callable(save):
            save = getattr(
                self._credentials, "save_siliconflow_api_key", None
            )
        if not callable(save):
            self.siliconflow_status.setText(
                "当前凭据存储不支持硅基流动 API Key。"
            )
            return
        warning = ""
        try:
            save(self.siliconflow_key_input.text())
        except ValueError as exc:
            self.siliconflow_status.setText(str(exc))
            return
        except RuntimeError as exc:
            warning = str(exc)
        self.siliconflow_key_input.clear()
        self.siliconflow_key_input.setPlaceholderText(
            "本次运行有效" if warning else "已安全保存"
        )
        self.siliconflow_status.setText(
            warning
            or "硅基流动图片 API Key 已安全保存，仅用于识图与生图。"
        )
        self.credentials_changed.emit()
        self._refresh_models("siliconflow")

    def _clear_siliconflow_key(self) -> None:
        clear = getattr(
            self._credentials, "clear_siliconflow_image_api_key", None
        )
        if not callable(clear):
            clear = getattr(
                self._credentials, "clear_siliconflow_api_key", None
            )
        if callable(clear):
            clear()
        self.siliconflow_key_input.clear()
        self.siliconflow_key_input.setPlaceholderText(
            "输入 sk- 开头的 API Key"
        )
        self.siliconflow_status.setText("硅基流动图片 API Key 已清除。")
        self.credentials_changed.emit()

    def _save_image_provider(self, index: int) -> None:
        self._settings.set(
            "image_provider",
            self.image_provider.itemData(index) or "siliconflow",
        )
        if hasattr(self, "_image_form"):
            self._update_provider_sections()
            if self.isVisible():
                self._ensure_model_catalog(
                    str(self.image_provider.itemData(index) or "siliconflow")
                )

    def _toggle_grsai_image_key(self, visible: bool) -> None:
        self.grsai_image_key_input.setEchoMode(
            QLineEdit.EchoMode.Normal
            if visible
            else QLineEdit.EchoMode.Password
        )

    def _save_grsai_image_key(self) -> None:
        save = getattr(self._credentials, "save_grsai_image_api_key", None)
        if not callable(save):
            self.siliconflow_status.setText("当前凭据存储不支持 GRS AI 图片 Key。")
            return
        warning = ""
        try:
            save(self.grsai_image_key_input.text())
        except ValueError as exc:
            self.siliconflow_status.setText(str(exc))
            return
        except RuntimeError as exc:
            warning = str(exc)
        self.grsai_image_key_input.clear()
        self.grsai_image_key_input.setPlaceholderText(
            "本次运行有效" if warning else "已安全保存"
        )
        self.siliconflow_status.setText(
            warning or "GRS AI 图片 API Key 已安全保存，仅用于识图与生图。"
        )
        self.credentials_changed.emit()
        self._refresh_models("grsai")

    def _clear_grsai_image_key(self) -> None:
        clear = getattr(self._credentials, "clear_grsai_image_api_key", None)
        if callable(clear):
            clear()
        self.grsai_image_key_input.clear()
        self.grsai_image_key_input.setPlaceholderText("输入 GRS AI 图片 API Key")
        self.siliconflow_status.setText("GRS AI 图片 API Key 已清除。")
        self.credentials_changed.emit()

    def _save_grsai_image_settings(self, *_args) -> None:
        try:
            base_url = normalize_grsai_base_url(
                self.grsai_image_base_url.text()
                or DEFAULT_GRSAI_API_BASE_URL
            )
        except ValueError as exc:
            self.siliconflow_status.setText(str(exc))
            return
        vision_model = (
            self.grsai_vision_model.text().strip()
            or DEFAULT_GRSAI_VISION_MODEL
        )
        image_model = (
            self.grsai_image_model.text().strip()
            or DEFAULT_GRSAI_IMAGE_MODEL
        )
        self.grsai_image_base_url.setText(base_url)
        self.grsai_vision_model.blockSignals(True)
        self.grsai_vision_model.setText(vision_model)
        self.grsai_vision_model.blockSignals(False)
        self.grsai_image_model.blockSignals(True)
        self.grsai_image_model.setText(image_model)
        self.grsai_image_model.blockSignals(False)
        self._settings.set("grsai_image_base_url", base_url)
        self._settings.set("grsai_vision_model", vision_model)
        self._settings.set("grsai_image_model", image_model)
        self._settings.set(
            "grsai_image_size",
            self.grsai_image_size.currentData() or DEFAULT_GRSAI_IMAGE_SIZE,
        )

    def _toggle_siliconflow_tts_key(self, visible: bool) -> None:
        self.siliconflow_tts_key_input.setEchoMode(
            QLineEdit.EchoMode.Normal
            if visible
            else QLineEdit.EchoMode.Password
        )

    def _save_siliconflow_tts_key(self) -> None:
        save = getattr(
            self._credentials, "save_siliconflow_tts_api_key", None
        )
        if not callable(save):
            save = getattr(
                self._credentials, "save_siliconflow_api_key", None
            )
        if not callable(save):
            self.tts_provider_status.setText(
                "当前凭据存储不支持硅基流动 TTS Key。"
            )
            return
        warning = ""
        try:
            save(self.siliconflow_tts_key_input.text())
        except ValueError as exc:
            self.tts_provider_status.setText(str(exc))
            return
        except RuntimeError as exc:
            warning = str(exc)
        self.siliconflow_tts_key_input.clear()
        self.siliconflow_tts_key_input.setPlaceholderText(
            "本次运行有效" if warning else "已安全保存"
        )
        self.tts_provider_status.setText(
            warning or "硅基流动 TTS API Key 已安全保存，仅用于语音。"
        )
        self.credentials_changed.emit()
        self.tts_settings_changed.emit()
        self._update_tts_provider_status()
        self._refresh_models("siliconflow")

    def _clear_siliconflow_tts_key(self) -> None:
        clear = getattr(
            self._credentials, "clear_siliconflow_tts_api_key", None
        )
        if not callable(clear):
            clear = getattr(
                self._credentials, "clear_siliconflow_api_key", None
            )
        if callable(clear):
            clear()
        self.siliconflow_tts_key_input.clear()
        self.siliconflow_tts_key_input.setPlaceholderText(
            "输入硅基流动 TTS API Key"
        )
        self.tts_provider_status.setText("硅基流动 TTS API Key 已清除。")
        self.credentials_changed.emit()
        self.tts_settings_changed.emit()
        self._update_tts_provider_status()

    def _toggle_xfyun_password(self, visible: bool) -> None:
        self.xfyun_tts_password_input.setEchoMode(
            QLineEdit.EchoMode.Normal
            if visible
            else QLineEdit.EchoMode.Password
        )

    def _toggle_xfyun_hmac(self, visible: bool) -> None:
        mode = (
            QLineEdit.EchoMode.Normal
            if visible
            else QLineEdit.EchoMode.Password
        )
        self.xfyun_tts_api_key_input.setEchoMode(mode)
        self.xfyun_tts_api_secret_input.setEchoMode(mode)

    def _save_xfyun_password(self) -> None:
        save = getattr(
            self._credentials, "save_xfyun_tts_api_password", None
        )
        if not callable(save):
            self.xfyun_tts_status.setText(
                "当前凭据存储不支持科大讯飞 API Password。"
            )
            return
        warning = ""
        try:
            save(self.xfyun_tts_password_input.text())
        except ValueError as exc:
            self.xfyun_tts_status.setText(str(exc))
            return
        except RuntimeError as exc:
            warning = str(exc)
        self.xfyun_tts_password_input.clear()
        self.xfyun_tts_password_input.setPlaceholderText(
            "本次运行有效" if warning else "已安全保存"
        )
        self.xfyun_tts_status.setText(
            warning
            or "科大讯飞 API Password 已安全保存。请同时填写 APPID。"
        )
        self.credentials_changed.emit()
        self.tts_settings_changed.emit()
        self._update_tts_provider_status()

    def _clear_xfyun_password(self) -> None:
        clear = getattr(
            self._credentials, "clear_xfyun_tts_api_password", None
        )
        if callable(clear):
            clear()
        self.xfyun_tts_password_input.clear()
        self.xfyun_tts_password_input.setPlaceholderText(
            "输入 API Password"
        )
        self.xfyun_tts_status.setText(
            "科大讯飞 API Password 已清除。"
        )
        self.credentials_changed.emit()
        self.tts_settings_changed.emit()
        self._update_tts_provider_status()

    def _save_xfyun_hmac(self) -> None:
        save_key = getattr(
            self._credentials, "save_xfyun_tts_api_key", None
        )
        save_secret = getattr(
            self._credentials, "save_xfyun_tts_api_secret", None
        )
        if not callable(save_key) or not callable(save_secret):
            self.xfyun_tts_status.setText(
                "当前凭据存储不支持科大讯飞签名密钥。"
            )
            return
        api_key = self.xfyun_tts_api_key_input.text().strip()
        api_secret = self.xfyun_tts_api_secret_input.text().strip()
        if not api_key or not api_secret:
            self.xfyun_tts_status.setText(
                "APIKey 与 APISecret 必须同时填写。"
            )
            return
        warning = ""
        try:
            save_key(api_key)
            save_secret(api_secret)
        except ValueError as exc:
            self.xfyun_tts_status.setText(str(exc))
            return
        except RuntimeError as exc:
            warning = str(exc)
        self.xfyun_tts_api_key_input.clear()
        self.xfyun_tts_api_secret_input.clear()
        placeholder = "本次运行有效" if warning else "已安全保存"
        self.xfyun_tts_api_key_input.setPlaceholderText(placeholder)
        self.xfyun_tts_api_secret_input.setPlaceholderText(placeholder)
        self.xfyun_tts_status.setText(
            warning
            or "科大讯飞 APIKey 与 APISecret 已安全保存。"
        )
        self.credentials_changed.emit()
        self.tts_settings_changed.emit()
        self._update_tts_provider_status()

    def _clear_xfyun_hmac(self) -> None:
        for name in (
            "clear_xfyun_tts_api_key",
            "clear_xfyun_tts_api_secret",
        ):
            clear = getattr(self._credentials, name, None)
            if callable(clear):
                clear()
        self.xfyun_tts_api_key_input.clear()
        self.xfyun_tts_api_secret_input.clear()
        self.xfyun_tts_api_key_input.setPlaceholderText("输入 APIKey")
        self.xfyun_tts_api_secret_input.setPlaceholderText("输入 APISecret")
        self.xfyun_tts_status.setText(
            "科大讯飞 APIKey 与 APISecret 已清除。"
        )
        self.credentials_changed.emit()
        self.tts_settings_changed.emit()
        self._update_tts_provider_status()

    def _save_xfyun_settings(self, *_args) -> None:
        app_id = self.xfyun_tts_app_id.text().strip()
        auth_method = (
            self.xfyun_tts_auth_method.currentData() or "password"
        )
        voice = (
            self.xfyun_tts_voice.currentData()
            or self.xfyun_tts_voice.currentText().strip()
            or DEFAULT_XFYUN_TTS_VOICE
        )
        self.xfyun_tts_app_id.setText(app_id)
        self._settings.set("xfyun_tts_app_id", app_id)
        self._settings.set("xfyun_tts_auth_method", auth_method)
        self._settings.set("xfyun_tts_voice", voice)
        self._update_provider_sections()
        self.tts_settings_changed.emit()
        self._update_tts_provider_status()

    def _save_siliconflow_settings(self, *_args) -> None:
        vision_model = (
            self.siliconflow_vision_model.text().strip()
            or DEFAULT_SILICONFLOW_VISION_MODEL
        )
        image_model = (
            self.siliconflow_image_model.text().strip()
            or DEFAULT_SILICONFLOW_IMAGE_MODEL
        )
        tts_model = (
            self.siliconflow_tts_model.text().strip()
            or DEFAULT_SILICONFLOW_TTS_MODEL
        )
        tts_voice = (
            self.siliconflow_tts_voice.currentData()
            or self.siliconflow_tts_voice.currentText().strip()
            or "auto"
        )
        self.siliconflow_vision_model.blockSignals(True)
        self.siliconflow_vision_model.setText(vision_model)
        self.siliconflow_vision_model.blockSignals(False)
        self.siliconflow_tts_model.blockSignals(True)
        self.siliconflow_tts_model.setText(tts_model)
        self.siliconflow_tts_model.blockSignals(False)
        self._settings.set("siliconflow_vision_model", vision_model)
        self._settings.set("siliconflow_image_model", image_model)
        self._settings.set(
            "siliconflow_image_size",
            self.siliconflow_image_size.currentData()
            or DEFAULT_SILICONFLOW_IMAGE_SIZE,
        )
        self._settings.set("siliconflow_tts_model", tts_model)
        self._settings.set("siliconflow_tts_voice", tts_voice)
        self.tts_settings_changed.emit()
        self._update_tts_provider_status()

    def _save_model(self, index: int) -> None:
        model = self.default_model.itemData(index)
        if not model:
            return
        self._settings.set("default_model", model)
        if self.text_provider.currentData() == "deepseek":
            self._settings.set("deepseek_default_model", model)

    def _refresh_default_model_options(self) -> None:
        provider = self.text_provider.currentData() or "deepseek"
        models = text_provider_models(
            provider,
            self.grsai_text_model.text(),
        )
        if provider == "deepseek":
            selected = self._settings.get(
                "deepseek_default_model",
                self._settings.get("default_model", MODEL_CHAT),
            )
        else:
            selected = MODEL_CHAT

        self.default_model.blockSignals(True)
        self.default_model.clear()
        for model in models:
            self.default_model.addItem(model.label, model.id)
        index = self.default_model.findData(selected)
        self.default_model.setCurrentIndex(index if index >= 0 else 0)
        self.default_model.blockSignals(False)
        self.default_model.setEnabled(len(models) > 1)
        self.default_model.setToolTip(
            "可为新会话选择 DeepSeek 实际模型。"
            if len(models) > 1
            else "GRS AI 使用“GRS 文本模型”中配置的实际模型。"
        )
        current = self.default_model.currentData() or MODEL_CHAT
        self._settings.set("default_model", current)

    def _save_tts_provider(self, index: int) -> None:
        provider = self.tts_provider.itemData(index) or "edge"
        self._settings.set("tts_provider", provider)
        if hasattr(self, "_tts_form"):
            self._update_provider_sections()
            if self.isVisible():
                self._ensure_model_catalog(str(provider))
        self._update_tts_provider_status()
        self.tts_settings_changed.emit()
        if (
            provider == "indextts2"
            and self.indextts2_auto_start.isChecked()
        ):
            QTimer.singleShot(0, self._start_indextts2_service)

    def _save_indextts2_settings(self, *_args) -> bool:
        try:
            base_url = normalize_index_tts2_base_url(
                self.indextts2_base_url.text()
            )
        except ValueError as exc:
            self.indextts2_status.setText(str(exc))
            return False
        root = self.indextts2_root.text().strip()
        preset = (
            self.indextts2_preset.currentData()
            or self.indextts2_preset.currentText().strip()
            or DEFAULT_INDEXTTS2_PRESET
        )
        self.indextts2_base_url.setText(base_url)
        self.indextts2_root.setText(root)
        self._settings.set("indextts2_base_url", base_url)
        self._settings.set("indextts2_root", root)
        self._settings.set("indextts2_preset", str(preset))
        self._settings.set(
            "indextts2_auto_start",
            "true" if self.indextts2_auto_start.isChecked() else "false",
        )
        self.tts_settings_changed.emit()
        self._update_tts_provider_status()
        return True

    def _start_indextts2_service(self) -> None:
        if not self._save_indextts2_settings():
            return
        project = discover_index_tts2_root(self.indextts2_root.text())
        if project is None:
            self.indextts2_status.setText(
                "未找到可用的 IndexTTS2 目录，请检查项目目录、"
                "banverse_api.py 和 checkpoints/config.yaml。"
            )
            return
        self.indextts2_root.setText(str(project))
        self._settings.set("indextts2_root", str(project))
        started, message = launch_index_tts2_service(
            project, self.indextts2_base_url.text(), fp16=True
        )
        self.indextts2_status.setText(message)
        if started:
            QTimer.singleShot(1_500, self._refresh_indextts2_presets)

    def _refresh_indextts2_presets(self) -> None:
        if self._indextts2_thread is not None:
            return
        if not self._save_indextts2_settings():
            return
        self.indextts2_refresh.setEnabled(False)
        self.indextts2_status.setText("正在检查本地 IndexTTS2 服务…")
        thread = QThread(self)
        worker = IndexTts2CatalogWorker(
            self.indextts2_base_url.text()
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._indextts2_presets_refreshed)
        worker.failed.connect(self._indextts2_refresh_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._indextts2_refresh_finished)
        self._indextts2_thread = thread
        self._indextts2_worker = worker
        thread.start()

    @Slot(object, str)
    def _indextts2_presets_refreshed(self, presets, status: str) -> None:
        values = tuple(
            item for item in presets if isinstance(item, str) and item.strip()
        )
        if values:
            previous = (
                self.indextts2_preset.currentData()
                or self.indextts2_preset.currentText().strip()
                or DEFAULT_INDEXTTS2_PRESET
            )
            self._indextts2_presets = values
            self._settings.set(
                "indextts2_presets",
                serialize_index_tts2_presets(values),
            )
            self.indextts2_preset.blockSignals(True)
            self.indextts2_preset.clear()
            for preset_name in values:
                self.indextts2_preset.addItem(preset_name, preset_name)
            selected = self.indextts2_preset.findData(previous)
            self.indextts2_preset.setCurrentIndex(
                selected if selected >= 0 else 0
            )
            self.indextts2_preset.blockSignals(False)
            self._save_indextts2_settings()
        status_text = {
            "ready": "服务已就绪",
            "loading": "服务已启动，模型正在加载",
            "error": "服务模型加载失败，请查看日志",
        }.get(status, f"服务状态：{status}")
        self.indextts2_status.setText(
            f"{status_text}；已发现 {len(values)} 个可用预设。"
        )
        self._update_tts_provider_status()

    @Slot(str)
    def _indextts2_refresh_failed(self, error: str) -> None:
        self.indextts2_status.setText(
            f"无法连接本地 IndexTTS2：{error}"
        )
        self._update_tts_provider_status()

    @Slot()
    def _indextts2_refresh_finished(self) -> None:
        self._indextts2_worker = None
        self._indextts2_thread = None
        self.indextts2_refresh.setEnabled(True)

    def _update_tts_provider_status(self) -> None:
        provider = self.tts_provider.currentData() or "edge"
        if provider == "xfyun":
            auth_method = (
                self.xfyun_tts_auth_method.currentData() or "password"
            )
            if auth_method == "hmac":
                api_key = getattr(
                    self._credentials,
                    "get_xfyun_tts_api_key",
                    lambda: "",
                )()
                api_secret = getattr(
                    self._credentials,
                    "get_xfyun_tts_api_secret",
                    lambda: "",
                )()
                credential_ready = bool(api_key and api_secret)
                credential_hint = "APIKey 与 APISecret"
            else:
                password = getattr(
                    self._credentials,
                    "get_xfyun_tts_api_password",
                    lambda: "",
                )()
                credential_ready = bool(password)
                credential_hint = "API Password"
            ready = bool(
                self.xfyun_tts_app_id.text().strip()
                and credential_ready
            )
            available_count = len(
                getattr(self, "_xfyun_available_voices", ())
            )
            text = (
                (
                    "当前使用科大讯飞超拟人 TTS，凭据已就绪；"
                    f"已缓存 {available_count} 个账号可用音色。"
                    if available_count
                    else "当前使用科大讯飞超拟人 TTS，凭据已就绪；请检测账号可用音色。"
                )
                if ready
                else (
                    "当前使用科大讯飞；播放前需填写 APPID 并保存"
                    f" {credential_hint}。"
                )
            )
        elif provider == "siliconflow":
            getter = getattr(
                self._credentials, "get_siliconflow_tts_api_key", None
            )
            if not callable(getter):
                getter = getattr(
                    self._credentials, "get_siliconflow_api_key", lambda: ""
                )
            ready = bool(getter())
            text = (
                "当前使用硅基流动 TTS，凭据已就绪。"
                if ready
                else "当前使用硅基流动；播放前需保存硅基流动 API Key。"
            )
        elif provider == "indextts2":
            preset = (
                self.indextts2_preset.currentData()
                or self.indextts2_preset.currentText().strip()
                or DEFAULT_INDEXTTS2_PRESET
            )
            text = (
                "当前使用本地 IndexTTS2；角色卡预设优先，"
                f"无角色预设时使用“{preset}”。"
            )
        else:
            text = (
                "当前使用 Android 系统 TTS；无需单独 API Key。"
                if is_android_platform()
                else "当前使用 Edge TTS；无需单独 API Key。"
            )
        self.tts_provider_status.setText(text)

    def _save_roleplay_settings(self, *_args) -> None:
        user_name = " ".join(self.user_name.text().split()).strip() or "用户"
        if self.user_name.text() != user_name:
            self.user_name.blockSignals(True)
            self.user_name.setText(user_name)
            self.user_name.blockSignals(False)
        persona = self.user_persona.toPlainText().strip()[:1_500]
        self._settings.set("user_name", user_name)
        self._settings.set("user_persona", persona)
        self._settings.set(
            "roleplay_temperature",
            f"{self.roleplay_temperature.value():.1f}",
        )
        self._settings.set(
            "role_memory_enabled",
            "true" if self.role_memory_enabled.isChecked() else "false",
        )

    def _theme_selected(self, index: int) -> None:
        value = self.theme.itemData(index)
        self._settings.set("theme", value)
        self.theme_changed.emit(value)

    def _integer_setting(self, key: str, default: int) -> int:
        return self._bounded_integer_setting(key, default, 5, 1_440)

    def _bounded_integer_setting(
        self,
        key: str,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        try:
            return max(
                minimum,
                min(int(self._settings.get(key, str(default))), maximum),
            )
        except ValueError:
            return default

    def _update_character_discovery_controls(self) -> None:
        enabled = self.character_discovery_enabled.isChecked()
        self.character_discovery_min_minutes.setEnabled(enabled)
        self.character_discovery_max_minutes.setEnabled(enabled)
        self.character_discovery_daily_limit.setEnabled(enabled)

    def _save_character_discovery_settings(self, *_args) -> None:
        minimum = self.character_discovery_min_minutes.value()
        maximum = self.character_discovery_max_minutes.value()
        if maximum < minimum:
            self.character_discovery_max_minutes.blockSignals(True)
            self.character_discovery_max_minutes.setValue(minimum)
            self.character_discovery_max_minutes.blockSignals(False)
            maximum = minimum
        self._settings.set(
            "character_discovery_enabled",
            "true" if self.character_discovery_enabled.isChecked() else "false",
        )
        self._settings.set("character_discovery_min_minutes", str(minimum))
        self._settings.set("character_discovery_max_minutes", str(maximum))
        self._settings.set(
            "character_discovery_daily_limit",
            str(self.character_discovery_daily_limit.value()),
        )
        self._update_character_discovery_controls()
        self.character_discovery_settings_changed.emit()

    def _save_proactive_settings(self, *_args) -> None:
        minimum = self.proactive_min_minutes.value()
        maximum = self.proactive_max_minutes.value()
        if maximum < minimum:
            self.proactive_max_minutes.blockSignals(True)
            self.proactive_max_minutes.setValue(minimum)
            self.proactive_max_minutes.blockSignals(False)
            maximum = minimum
        self._settings.set(
            "proactive_enabled",
            "true" if self.proactive_enabled.isChecked() else "false",
        )
        self._settings.set("proactive_min_minutes", str(minimum))
        self._settings.set("proactive_max_minutes", str(maximum))
        self.proactive_settings_changed.emit()
