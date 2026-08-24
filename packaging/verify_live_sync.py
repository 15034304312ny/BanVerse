"""使用正式配对凭据验证两台临时客户端经公网服务双向收敛。"""

from __future__ import annotations

import argparse
import base64
import tempfile
from pathlib import Path
from uuid import uuid4

from deepseek_cli.character_cards import empty_card
from deepseek_cli.desktop.data.database import Database
from deepseek_cli.desktop.data.repositories import CharacterRepository, ChatRepository
from deepseek_cli.desktop.sync_client import SyncEngine, SyncHttpClient, SyncRepository
from deepseek_cli.model_catalog import MODEL_CHAT
from deepseek_cli.sync_protocol import parse_sync_pairing

_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUB"
    "AScY42YAAAAASUVORK5CYII="
)


def _engine(
    database: Database,
    pairing: dict[str, str],
    device_id: str,
    media_root: Path,
) -> SyncEngine:
    return SyncEngine(
        SyncRepository(database),
        SyncHttpClient(
            pairing["server_url"],
            pairing["account_id"],
            pairing["token"],
        ),
        device_id=device_id,
        device_name=f"BanVerse release verification {device_id[-6:]}",
        media_root=media_root,
    )


def verify(pairing_file: Path) -> None:
    pairing = parse_sync_pairing(pairing_file.read_text(encoding="utf-8"))
    run_id = uuid4().hex
    with tempfile.TemporaryDirectory(prefix="banverse-live-sync-") as root_value:
        root = Path(root_value)
        first_db = Database(root / "desktop.db")
        second_db = Database(root / "android.db")
        try:
            avatar = root / "release-avatar.png"
            avatar.write_bytes(_PNG_1X1)
            card = empty_card(f"发布验证角色-{run_id[:8]}")
            card["data"]["description"] = "仅用于 1.3.0 公网双端同步验证。"
            first_characters = CharacterRepository(first_db)
            character = first_characters.create(card, str(avatar))
            first_chats = ChatRepository(first_db)
            conversation = first_chats.create_conversation(
                title=f"发布验证会话-{run_id[:8]}",
                character_id=character.id,
                opening_message="同步验证开始。",
            )
            turn = first_chats.create_turn(
                conversation.id,
                "这条消息来自临时桌面客户端。",
                MODEL_CHAT,
                user_image_path=str(avatar),
            )
            first_chats.complete_turn(
                turn.id,
                "临时 Android 客户端应该能收到这条回复和图片。",
                assistant_image_path=str(avatar),
            )

            first = _engine(first_db, pairing, f"desktop-{run_id}", root / "pc")
            second = _engine(second_db, pairing, f"android-{run_id}", root / "phone")
            first_result = first.sync_once()
            second_result = second.sync_once()
            remote_character = CharacterRepository(second_db).get(character.id)
            remote_conversation = ChatRepository(second_db).get_conversation(
                conversation.id
            )
            remote_turns = ChatRepository(second_db).list_turns(conversation.id)
            if remote_character is None or remote_conversation is None:
                raise RuntimeError("桌面到 Android 的角色或会话未收敛。")
            if len(remote_turns) != 1 or not remote_turns[0].assistant_image_path:
                raise RuntimeError("桌面到 Android 的消息或图片未收敛。")
            if Path(remote_character.avatar_path).read_bytes() != _PNG_1X1:
                raise RuntimeError("角色头像内容校验失败。")

            second_chats = ChatRepository(second_db)
            renamed = f"Android 回传成功-{run_id[:8]}"
            second_chats.rename_conversation(conversation.id, renamed)
            second.sync_once()
            first.sync_once()
            if first_chats.get_conversation(conversation.id).title != renamed:
                raise RuntimeError("Android 到桌面的会话修改未收敛。")

            first_chats.delete_conversation(conversation.id)
            first_characters.delete(character.id)
            first.sync_once()
            second.sync_once()
            if second_chats.get_conversation(conversation.id) is not None:
                raise RuntimeError("会话删除墓碑未传播。")
            if CharacterRepository(second_db).get(character.id) is not None:
                raise RuntimeError("角色删除墓碑未传播。")
            print(
                "公网双端同步验证通过："
                f"首次上传 {first_result.pushed} 项，首次接收 {second_result.pulled} 项；"
                "文本、角色、图片、反向修改和删除均已收敛。"
            )
        finally:
            first_db.close()
            second_db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairing-file", type=Path, required=True)
    args = parser.parse_args()
    verify(args.pairing_file.expanduser().resolve())


if __name__ == "__main__":
    main()
