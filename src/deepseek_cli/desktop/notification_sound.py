"""播放随应用打包的短消息提示音。"""

from __future__ import annotations

from importlib import resources

from PySide6.QtCore import QObject, QUrl
from PySide6.QtMultimedia import QSoundEffect


class NotificationSound(QObject):
    """持有一个可重复播放的本地 QSoundEffect。"""

    def __init__(self, parent: QObject | None = None, *, volume: float = 0.42):
        super().__init__(parent)
        self._effect = QSoundEffect(self)
        self._available = False
        try:
            path = resources.files("deepseek_cli.desktop").joinpath(
                "resources", "message_notification.wav"
            )
            if path.is_file():
                self._effect.setSource(QUrl.fromLocalFile(str(path)))
                self._effect.setVolume(max(0.0, min(float(volume), 1.0)))
                self._available = True
        except (FileNotFoundError, OSError, TypeError, ValueError):
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    def play(self) -> bool:
        if not self._available:
            return False
        if self._effect.isPlaying():
            self._effect.stop()
        self._effect.play()
        return True

    def shutdown(self) -> None:
        if self._effect.isPlaying():
            self._effect.stop()

