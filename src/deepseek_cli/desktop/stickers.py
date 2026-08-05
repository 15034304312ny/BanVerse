"""内置表情包目录与供模型理解的稳定语义。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Sticker:
    id: str
    emoji: str
    label: str

    @property
    def model_text(self) -> str:
        return f"我发了一个“{self.label}”的表情。"


STICKERS = (
    Sticker("happy", "😄", "开心"),
    Sticker("laugh", "😂", "笑哭"),
    Sticker("cute", "🥰", "喜欢"),
    Sticker("wink", "😉", "眨眼"),
    Sticker("shy", "😊", "害羞"),
    Sticker("cool", "😎", "得意"),
    Sticker("please", "🥺", "可怜巴巴"),
    Sticker("hug", "🤗", "抱抱"),
    Sticker("heart", "❤️", "爱心"),
    Sticker("wave", "👋", "挥手"),
    Sticker("okay", "👌", "好的"),
    Sticker("like", "👍", "点赞"),
    Sticker("cheer", "🎉", "庆祝"),
    Sticker("surprised", "😲", "吃惊"),
    Sticker("thinking", "🤔", "思考"),
    Sticker("confused", "😵‍💫", "迷惑"),
    Sticker("sad", "😢", "难过"),
    Sticker("cry", "😭", "大哭"),
    Sticker("angry", "😠", "生气"),
    Sticker("pout", "😤", "哼"),
    Sticker("sleepy", "😴", "困了"),
    Sticker("awkward", "😅", "尴尬"),
    Sticker("kiss", "😘", "亲亲"),
    Sticker("clap", "👏", "鼓掌"),
)

_STICKERS_BY_ID = {sticker.id: sticker for sticker in STICKERS}


def sticker_by_id(sticker_id: str) -> Sticker | None:
    return _STICKERS_BY_ID.get(sticker_id.strip())
