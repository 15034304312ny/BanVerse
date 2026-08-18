"""托管用户头像文件。"""

from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import (
    QBuffer,
    QByteArray,
    QFile,
    QIODevice,
    QSaveFile,
    QStandardPaths,
    Qt,
    QUrl,
)
from PySide6.QtGui import QImage, QImageReader, QImageWriter

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 20_000_000
MAX_CHAT_IMAGE_BYTES = 20 * 1024 * 1024
MAX_GENERATED_IMAGE_BYTES = 50 * 1024 * 1024
MAX_CHAT_PIXELS = 40_000_000
MAX_CHAT_SIDE = 2_048


class AvatarError(ValueError):
    pass


def _load_image_source(
    source: str | Path,
    *,
    max_bytes: int,
    max_pixels: int,
    missing_message: str,
    size_message: str,
    decode_message: str,
) -> QImage:
    """Read a local path, file URL, or Android content URI safely."""

    source_text = str(source)
    if source_text.lower().startswith("file:"):
        local_path = QUrl(source_text).toLocalFile()
        if local_path:
            source_text = local_path

    backing_store = None
    if source_text.lower().startswith("content://"):
        content_file = QFile(source_text)
        if not content_file.open(QIODevice.OpenModeFlag.ReadOnly):
            raise AvatarError(missing_message)
        payload = content_file.read(max_bytes + 1)
        content_file.close()
        if not payload or len(payload) > max_bytes:
            raise AvatarError(missing_message)
        backing_store = QBuffer(payload)
        if not backing_store.open(QIODevice.OpenModeFlag.ReadOnly):
            raise AvatarError(missing_message)
        reader = QImageReader(backing_store)
    else:
        path = Path(source_text)
        if (
            not path.is_file()
            or path.stat().st_size <= 0
            or path.stat().st_size > max_bytes
        ):
            raise AvatarError(missing_message)
        reader = QImageReader(str(path))

    reader.setAutoTransform(True)
    size = reader.size()
    if (
        not size.isValid()
        or size.width() <= 0
        or size.height() <= 0
        or size.width() * size.height() > max_pixels
    ):
        raise AvatarError(size_message)
    image = reader.read()
    if image.isNull():
        raise AvatarError(decode_message)
    return image


def avatars_directory(app_data_root: str | Path | None = None) -> Path:
    root = (
        Path(app_data_root)
        if app_data_root is not None
        else Path(
            QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.AppDataLocation
            )
        )
    ) / "avatars"
    root.mkdir(parents=True, exist_ok=True)
    return root


def media_directory(app_data_root: str | Path | None = None) -> Path:
    root = (
        Path(app_data_root)
        if app_data_root is not None
        else Path(
            QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.AppDataLocation
            )
        )
    ) / "media"
    root.mkdir(parents=True, exist_ok=True)
    return root


def install_builtin_avatar(
    builtin_id: str,
    png_bytes: bytes,
    *,
    app_data_root: str | Path | None = None,
) -> str:
    """校验并原子安装打包头像到稳定的 AppData 路径。"""

    if not re.fullmatch(r"[a-z0-9_]+", builtin_id):
        raise AvatarError("内置头像标识不合法。")
    if not png_bytes or len(png_bytes) > MAX_IMAGE_BYTES:
        raise AvatarError("内置头像为空或超过 10 MB。")

    data = QByteArray(png_bytes)
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer, b"PNG")
    size = reader.size()
    if (
        not size.isValid()
        or size.width() != size.height()
        or size.width() * size.height() > MAX_PIXELS
    ):
        raise AvatarError("内置头像必须是有效的正方形 PNG。")
    image = reader.read()
    if image.isNull():
        raise AvatarError("无法读取内置头像。")

    directory = avatars_directory(app_data_root) / "builtin"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{builtin_id}-v1.png"
    if target.is_file() and target.read_bytes() == png_bytes:
        return str(target.resolve())

    output = QSaveFile(str(target))
    if not output.open(QIODevice.OpenModeFlag.WriteOnly):
        raise AvatarError("无法写入内置头像。")
    if output.write(png_bytes) != len(png_bytes) or not output.commit():
        output.cancelWriting()
        raise AvatarError("无法保存内置头像。")
    return str(target.resolve())


def import_avatar(source: str | Path) -> str:
    image = _load_image_source(
        source,
        max_bytes=MAX_IMAGE_BYTES,
        max_pixels=MAX_PIXELS,
        missing_message="头像文件不存在或超过 10 MB。",
        size_message="头像尺寸无效或像素过大。",
        decode_message="无法读取该图片，请选择 PNG、JPEG 或 WebP。",
    )
    side = min(image.width(), image.height())
    x, y = (image.width() - side) // 2, (image.height() - side) // 2
    cropped = image.copy(x, y, side, side).scaled(512, 512)
    target = avatars_directory() / f"{uuid4()}.png"
    if not cropped.save(str(target), "PNG"):
        raise AvatarError("无法保存头像。")
    return str(target)


def load_chat_image(source: str | Path) -> QImage:
    """Load a chat image from local storage or Android's document picker."""

    return _load_image_source(
        source,
        max_bytes=MAX_CHAT_IMAGE_BYTES,
        max_pixels=MAX_CHAT_PIXELS,
        missing_message="图片不存在、为空或超过 20 MB。",
        size_message="图片尺寸无效或像素过大。",
        decode_message="无法读取图片，请选择 PNG、JPEG 或 WebP。",
    )


def import_chat_image(
    source: str | Path,
    *,
    app_data_root: str | Path | None = None,
) -> str:
    """校验、纠正方向并压缩用户发送的聊天图片。"""

    image = load_chat_image(source)
    if max(image.width(), image.height()) > MAX_CHAT_SIDE:
        image = image.scaled(
            MAX_CHAT_SIDE,
            MAX_CHAT_SIDE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    has_alpha = image.hasAlphaChannel()
    suffix, format_name = (".png", b"PNG") if has_alpha else (".jpg", b"JPEG")
    target = media_directory(app_data_root) / "attachments" / f"{uuid4()}{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    quality = -1 if has_alpha else 88
    _save_image_atomic(
        image,
        target,
        format_name,
        quality,
        error_message="无法保存聊天图片。",
    )
    return str(target.resolve())


def install_generated_image(
    image_bytes: bytes,
    *,
    app_data_root: str | Path | None = None,
) -> str:
    """校验图像服务返回的数据并原子保存到本机。"""

    image = _decode_generated_image(image_bytes)
    target = media_directory(app_data_root) / "generated" / f"{uuid4()}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    _save_image_atomic(
        image,
        target,
        b"PNG",
        -1,
        error_message="无法保存生成图片。",
    )
    return str(target.resolve())


def install_generated_avatar(
    image_bytes: bytes,
    *,
    app_data_root: str | Path | None = None,
) -> str:
    """校验生图结果，居中裁切为 512 像素头像并保存到 AppData。"""

    image = _decode_generated_image(image_bytes)
    side = min(image.width(), image.height())
    x, y = (image.width() - side) // 2, (image.height() - side) // 2
    avatar = image.copy(x, y, side, side).scaled(
        512,
        512,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    target = avatars_directory(app_data_root) / "generated" / f"{uuid4()}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    _save_image_atomic(
        avatar,
        target,
        b"PNG",
        -1,
        error_message="无法保存生成头像。",
    )
    return str(target.resolve())


def _decode_generated_image(image_bytes: bytes) -> QImage:
    """对聊天配图和角色头像共用的服务响应执行安全解码。"""

    if (
        not image_bytes
        or len(image_bytes) > MAX_GENERATED_IMAGE_BYTES
    ):
        raise AvatarError("生成图片为空或超过 50 MB。")
    data = QByteArray(image_bytes)
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    size = reader.size()
    if (
        not size.isValid()
        or size.width() <= 0
        or size.height() <= 0
        or size.width() * size.height() > MAX_CHAT_PIXELS
    ):
        raise AvatarError("图像服务返回了无效尺寸。")
    image = reader.read()
    if image.isNull():
        raise AvatarError("图像服务返回的数据无法解码。")
    return image


def _save_image_atomic(
    image,
    target: Path,
    format_name: bytes,
    quality: int,
    *,
    error_message: str,
) -> None:
    encoded = QByteArray()
    buffer = QBuffer(encoded)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        raise AvatarError(error_message)
    writer = QImageWriter(buffer, format_name)
    writer.setQuality(quality)
    if not writer.write(image):
        raise AvatarError(error_message)
    buffer.close()
    output = QSaveFile(str(target))
    if not output.open(QIODevice.OpenModeFlag.WriteOnly):
        raise AvatarError(error_message)
    payload = bytes(encoded)
    if output.write(payload) != len(payload) or not output.commit():
        output.cancelWriting()
        raise AvatarError(error_message)
