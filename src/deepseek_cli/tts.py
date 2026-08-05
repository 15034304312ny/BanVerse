"""角色语音配置、对白提取与动作感知的韵律规划。"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, replace
from typing import Any, Mapping

DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
EMOTION_PRESETS = {
    "neutral": (0, 0, 0),
    "gentle": (-8, 3, -2),
    "cheerful": (10, 10, 4),
    "calm": (-10, -3, -2),
    "serious": (-5, -4, 1),
    "sad": (-12, -9, -4),
}
AUTO_EMOTIONS = {
    "happy": (8, 8, 3),
    "sad": (-10, -8, -4),
    "angry": (6, -4, 6),
    "tender": (-6, 2, -2),
    "serious": (-4, -3, 0),
    "calm": (-8, -3, -2),
    "fearful": (5, 4, -3),
    "surprised": (10, 10, 4),
    "neutral": (0, 0, 0),
}
_KEYWORDS = {
    "happy": (
        "开心", "太好了", "恭喜", "哈哈", "喜欢", "笑", "雀跃",
        "happy", "great",
    ),
    "sad": (
        "难过", "遗憾", "抱歉", "失去", "哭", "泪", "哽咽",
        "sad", "sorry",
    ),
    "angry": (
        "生气", "愤怒", "不能接受", "可恶", "咬牙", "怒", "angry",
    ),
    "tender": (
        "别担心", "慢慢来", "陪着你", "没关系", "温柔", "轻声",
        "柔声",
    ),
    "serious": (
        "必须", "警告", "风险", "注意", "安全", "严重", "郑重",
    ),
    "calm": ("冷静", "平静", "沉稳", "深呼吸"),
    "fearful": ("害怕", "恐惧", "发抖", "颤抖", "不安"),
    "surprised": ("没想到", "竟然", "怎么会", "惊讶", "震惊"),
}
_ACTION_PATTERN = re.compile(
    r"（(?P<cn>[^（）]{0,600})）"
    r"|\((?P<en>[^()\n]{0,600})\)"
    r"|【(?P<brace>[^【】]{0,600})】"
    r"|\[(?P<bracket>[^\[\]\n]{0,600})\]"
)
_QUOTE_PATTERN = re.compile(r"[“「『](.*?)[”」』]", flags=re.DOTALL)
_SPEECH_VERBS = (
    "说", "问", "喊", "答", "道", "嘀咕", "喃喃", "开口", "回应",
    "补充", "提醒", "叫住",
)
_NARRATION_START = re.compile(
    r"^(?:她|他|少女|青年|女人|男人|女孩|男孩|角色|旁白|动作|场景)"
)
_NARRATION_ACTIONS = (
    "抬手", "低头", "抬眼", "转身", "走", "坐", "站", "看向",
    "望向", "推开", "拿起", "放下", "伸手", "皱眉", "点头", "摇头",
    "笑了笑", "沉默", "停顿", "说", "问", "喊", "答", "道",
)
_ACTION_STYLES = (
    (("耳语", "低声", "压低声音", "小声", "凑近"), "tender", -12, -3, -16),
    (("轻声", "柔声", "温柔", "安抚", "拥抱"), "tender", -8, 2, -5),
    (("大喊", "喊道", "吼", "提高声音", "拍桌"), "angry", 14, 6, 16),
    (("生气", "愤怒", "咬牙", "冷声"), "angry", 7, -4, 8),
    (("哭", "哽咽", "泪", "抽泣", "声音发颤"), "sad", -13, -8, -8),
    (("笑", "雀跃", "兴奋", "弯起眼睛"), "happy", 10, 8, 5),
    (("急促", "喘息", "奔跑", "催促", "来不及"), "serious", 15, 3, 8),
    (("害怕", "发抖", "颤抖", "惊慌"), "fearful", 5, 5, -2),
    (("惊讶", "愣住", "睁大眼睛", "倒吸一口气"), "surprised", 12, 10, 5),
    (("平静", "冷静", "沉稳", "深呼吸", "缓缓"), "calm", -10, -4, -3),
    (("严肃", "郑重", "警告", "沉声"), "serious", -5, -4, 4),
)


@dataclass(frozen=True, slots=True)
class TtsProfile:
    voice: str = DEFAULT_VOICE
    rate: int = 0
    pitch: int = 0
    volume: int = 0
    emotion_preset: str = "neutral"
    auto_emotion: bool = True
    index_tts2_preset: str = ""


@dataclass(frozen=True, slots=True)
class EffectiveTtsProfile:
    voice: str
    rate: str
    pitch: str
    volume: str
    emotion: str


@dataclass(frozen=True, slots=True)
class SpeechSegment:
    text: str
    emotion: str = "neutral"
    rate_delta: int = 0
    pitch_delta: int = 0
    volume_delta: int = 0
    action_cue: str = ""


def read_tts_profile(card: Mapping[str, Any] | None) -> TtsProfile:
    if not card:
        return TtsProfile()
    data = card.get("data", {})
    extensions = data.get("extensions", {}) if isinstance(data, Mapping) else {}
    app = extensions.get("deepseek_chat", {}) if isinstance(extensions, Mapping) else {}
    raw = app.get("tts", {}) if isinstance(app, Mapping) else {}
    if not isinstance(raw, Mapping):
        return TtsProfile()
    voice = raw.get("voice", DEFAULT_VOICE)
    preset = raw.get("emotion_preset", "neutral")
    index_tts2_preset = raw.get("index_tts2_preset", "")
    return TtsProfile(
        voice=voice.strip()[:128] if isinstance(voice, str) and voice.strip() else DEFAULT_VOICE,
        rate=_number(raw.get("rate", 0)),
        pitch=_number(raw.get("pitch", 0)),
        volume=_number(raw.get("volume", 0)),
        emotion_preset=preset if preset in EMOTION_PRESETS else "neutral",
        auto_emotion=raw.get("auto_emotion", True) is not False,
        index_tts2_preset=(
            index_tts2_preset.strip()[:240]
            if isinstance(index_tts2_preset, str)
            else ""
        ),
    )


def write_tts_profile(card: Mapping[str, Any], profile: TtsProfile) -> dict[str, Any]:
    result = copy.deepcopy(dict(card))
    data = result.setdefault("data", {})
    extensions = data.setdefault("extensions", {})
    app = extensions.setdefault("deepseek_chat", {})
    app["tts"] = {
        "schema_version": 2,
        "voice": profile.voice.strip() or DEFAULT_VOICE,
        "rate": _clamp(profile.rate),
        "pitch": _clamp(profile.pitch),
        "volume": _clamp(profile.volume),
        "emotion_preset": profile.emotion_preset if profile.emotion_preset in EMOTION_PRESETS else "neutral",
        "auto_emotion": bool(profile.auto_emotion),
        "index_tts2_preset": profile.index_tts2_preset.strip()[:240],
    }
    return result


def prepare_speech_text(text: str) -> str:
    """仅返回角色真正说出口的内容，不朗读动作和旁白。"""

    return "\n".join(
        segment.text for segment in extract_speech_segments(text)
    ).strip()


def extract_speech_segments(
    text: str, *, max_segments: int = 8
) -> tuple[SpeechSegment, ...]:
    """从角色回复中提取对白，并用附近动作提示规划分段情绪。"""

    cleaned = _clean_markup(text)
    if not cleaned:
        return ()
    raw: list[tuple[str, str]] = []
    action_cue = ""
    for line in cleaned.splitlines():
        value = line.strip()
        if not value:
            action_cue = ""
            continue
        cursor = 0
        found_action = False
        for match in _ACTION_PATTERN.finditer(value):
            raw.extend(_dialogue_parts(value[cursor : match.start()], action_cue))
            cue = next(
                (
                    group
                    for group in match.groupdict().values()
                    if group is not None
                ),
                "",
            )
            action_cue = " ".join(cue.split()).strip()[:600]
            found_action = True
            cursor = match.end()
        tail = value[cursor:]
        raw.extend(_dialogue_parts(tail, action_cue))
        if not found_action and raw:
            action_cue = ""

    planned: list[SpeechSegment] = []
    for spoken, cue in raw:
        emotion, rate, pitch, volume = _speech_style(spoken, cue)
        segment = SpeechSegment(
            spoken,
            emotion,
            rate,
            pitch,
            volume,
            cue,
        )
        if (
            planned
            and planned[-1].emotion == segment.emotion
            and planned[-1].rate_delta == segment.rate_delta
            and planned[-1].pitch_delta == segment.pitch_delta
            and planned[-1].volume_delta == segment.volume_delta
        ):
            previous = planned[-1]
            planned[-1] = replace(
                previous, text=f"{previous.text} {segment.text}".strip()
            )
        elif len(planned) < max(1, max_segments):
            planned.append(segment)
        elif planned:
            previous = planned[-1]
            planned[-1] = replace(
                previous, text=f"{previous.text} {segment.text}".strip()
            )
    return tuple(planned)


def _clean_markup(text: str) -> str:
    cleaned = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    cleaned = re.sub(
        r"(?<!\*)\*([^*\n]{1,200})\*(?!\*)",
        r"（\1）",
        cleaned,
    )
    cleaned = re.sub(r"`([^`]*)`", r"\1", cleaned)
    cleaned = re.sub(r"!\[([^]]*)\]\([^)]*\)", "", cleaned)
    cleaned = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", cleaned)
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    cleaned = re.sub(r"<[^>]+>", "", cleaned)
    cleaned = re.sub(r"^\s{0,3}#{1,6}\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^\s*[-*+]\s+", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^\s*\d+[.)]\s+", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"[*_~>]", "", cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _dialogue_parts(value: str, action_cue: str) -> list[tuple[str, str]]:
    text = " ".join(value.split()).strip(" \t")
    if not text:
        return []
    if re.match(r"^(?:旁白|动作|场景|内心)\s*[:：]", text):
        return []
    text = re.sub(
        r"^(?:assistant|角色|人物|AI)\s*[:：]\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    quotes = _QUOTE_PATTERN.findall(text)
    speech_verb_pattern = "|".join(map(re.escape, _SPEECH_VERBS))
    narrated_quotes = bool(
        quotes
        and re.search(
            rf"(?:{speech_verb_pattern})[^“「『]{{0,12}}[“「『]",
            text,
        )
    )
    candidates = quotes if narrated_quotes else [text]
    result: list[tuple[str, str]] = []
    for candidate in candidates:
        candidate = candidate.strip("“”「」『』 \t")
        marker = re.search(
            rf"(?:{speech_verb_pattern})\s*[:：，,]\s*(.+)$",
            candidate,
        )
        if marker and _NARRATION_START.match(candidate):
            candidate = marker.group(1).strip()
        sentences = re.split(r"(?<=[。！？!?…])\s*", candidate)
        for sentence in sentences:
            spoken = sentence.strip("“”「」『』 \t")
            if not spoken or _is_probable_narration(spoken):
                continue
            result.append((spoken, action_cue))
    return result


def _is_probable_narration(text: str) -> bool:
    if not _NARRATION_START.match(text):
        return False
    if any(symbol in text for symbol in ("：", ":")):
        return False
    return any(action in text[:60] for action in _NARRATION_ACTIONS)


def _speech_style(spoken: str, action_cue: str) -> tuple[str, int, int, int]:
    source = f"{action_cue} {spoken}".lower()
    matches = [
        rule
        for rule in _ACTION_STYLES
        if any(keyword.lower() in source for keyword in rule[0])
    ]
    if matches:
        emotion = matches[0][1]
        rate = sum(rule[2] for rule in matches)
        pitch = sum(rule[3] for rule in matches)
        volume = sum(rule[4] for rule in matches)
        return (
            emotion,
            max(-25, min(rate, 25)),
            max(-25, min(pitch, 25)),
            max(-25, min(volume, 25)),
        )
    return detect_emotion(spoken), 0, 0, 0


def detect_emotion(text: str) -> str:
    source = _clean_markup(text)[:4000].lower()
    scores = {
        emotion: sum(source.count(keyword.lower()) for keyword in keywords)
        for emotion, keywords in _KEYWORDS.items()
    }
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    if not ranked or ranked[0][1] < 1:
        return "neutral"
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return "neutral"
    return ranked[0][0]


def resolve_effective_profile(
    profile: TtsProfile,
    text: str,
    *,
    emotion_override: str | None = None,
    rate_delta: int = 0,
    pitch_delta: int = 0,
    volume_delta: int = 0,
) -> EffectiveTtsProfile:
    base = EMOTION_PRESETS.get(profile.emotion_preset, EMOTION_PRESETS["neutral"])
    emotion = (
        emotion_override
        if profile.auto_emotion and emotion_override in AUTO_EMOTIONS
        else detect_emotion(text)
        if profile.auto_emotion
        else "neutral"
    )
    dynamic = AUTO_EMOTIONS[emotion]
    action_rate = rate_delta if profile.auto_emotion else 0
    action_pitch = pitch_delta if profile.auto_emotion else 0
    action_volume = volume_delta if profile.auto_emotion else 0
    rate = _clamp(profile.rate + base[0] + dynamic[0] + action_rate)
    pitch = _clamp(
        profile.pitch + base[1] + dynamic[1] + action_pitch
    )
    volume = _clamp(
        profile.volume + base[2] + dynamic[2] + action_volume
    )
    return EffectiveTtsProfile(
        profile.voice or DEFAULT_VOICE,
        _format(rate, "%"),
        _format(pitch, "Hz"),
        _format(volume, "%"),
        emotion,
    )


def _number(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return _clamp(int(value))
    except (TypeError, ValueError):
        return 0


def _clamp(value: int) -> int:
    return max(-50, min(50, int(value)))


def _format(value: int, unit: str) -> str:
    return f"{value:+d}{unit}"
