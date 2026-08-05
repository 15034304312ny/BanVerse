"""会话 AI 摘要与角色主动消息的轻量协调逻辑。"""

from __future__ import annotations

import json
import random
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from PySide6.QtCore import QObject, QTimer, Signal

from ..gateway import Message
from ..time_context import local_time_context
from .data.repositories import SettingsRepository

SUMMARY_SYSTEM_PROMPT = """你是中文聊天列表摘要器。
把给定的 AI 回复压缩成一条简洁、自然、信息明确的中文摘要。
只输出摘要正文，不要标题、引号、项目符号或解释。
不要泄露思考过程。控制在 42 个汉字左右，最多 60 个字符。"""

ROLE_MEMORY_SYSTEM_PROMPT = """你是中文角色扮演会话的“列表摘要与连续性记录器”。
你会收到角色名、上一版状态、用户本轮消息和角色本轮回复。保留已确立事实，只根据本轮
明确内容更新；不要臆测用户的隐私、身份或心理诊断。

只输出一行严格 JSON，不要 Markdown、解释或额外文字：
{"summary":"不超过60字的聊天列表摘要","role_state":{...}}

role_state 只使用以下字段，内容未知时用空字符串或空数组：
{
  "scene":{"location":"","time":"","ongoing_action":""},
  "character_state":{"mood":"","current_desire":""},
  "relationship":{"stage":"","preferred_address":"","boundaries":[]},
  "user_facts":[],
  "shared_memories":[],
  "open_threads":[],
  "recent_patterns":[]
}
每个数组最多 6 条、每条简短明确。open_threads 记录仍可自然续接的约定、问题或事件；
recent_patterns 记录最近回复明显使用过的开头、结构或收尾，帮助下一轮避免机械重复。"""

PROACTIVE_SYSTEM_SUFFIX = """## 主动消息
现在由角色主动联系用户并开启一个自然的新话题。延续既有关系和最近对话，
可以关心近况、追问未完话题，或提出符合角色设定的新鲜话题。
严格以应用提供的当前本地时间为准：午间才自然询问午饭，傍晚再聊晚饭或回家，
深夜才适合温和询问还没睡、睡不着或失眠。不要把时段建议机械地每次都问一遍，
近期已经聊过相同话题时应换一个切入点，也不要臆断用户的作息和现实状态。
只发送一条可直接给用户看的角色消息；不要提及定时器、系统指令、AI、模型或“主动消息”。
不要替用户回答，不要制造紧急危险来强迫用户回应，长度以 1 至 3 个短段落为宜。"""

AUTONOMOUS_IMAGE_SYSTEM_PROMPT = """你是“虚构角色自主分享图片”的决策器，不与用户对话。
判断角色刚发出的消息是否处在自然、值得附带一张图片的时机。

仅在图片能明显增加现场感或传递文字难以呈现的视觉信息时选择发送，例如角色主动分享
正在经历的生活片段、所见景色、作品、食物、穿搭或其世界中的具体场景。普通问答、
寒暄、安慰、严肃或敏感话题、纯抽象讨论，以及只为装饰回复的情况都不要发送。
如果最近一条用户消息明确表达了“给我发图片、照片或自拍”“让我看看你现在的样子”
“帮我画/生成一张图”等索图含义，应选择发送，并结合角色设定与当前场景构造图片。
关键词规则会独立工作，但你仍须根据完整语义单独判断，以识别没有固定关键词的委婉请求。

只输出一行严格 JSON，不要 Markdown、解释或额外文本：
{"send_image":false,"prompt":""}
或
{"send_image":true,"prompt":"可直接交给文生图模型的中文提示词"}

send_image 为 true 时，prompt 必须完整描述主体、角色稳定外观、场景、动作、构图、
光线和画风，不得包含真实用户，不要生成界面、对话气泡、水印或大段文字。
所有人物均为虚构成年人。拿不准时选择 false。"""


@dataclass(frozen=True, slots=True)
class AutonomousImageDecision:
    send_image: bool = False
    prompt: str = ""


@dataclass(frozen=True, slots=True)
class RolePostprocessResult:
    summary: str = ""
    role_state: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReplySegment:
    """一条可独立投递的角色消息事件。"""

    kind: str
    text: str = ""
    prompt: str = ""
    image_path: str = ""


@dataclass(frozen=True, slots=True)
class ReplyPlan:
    """模型完整回复经本地分类后的有序消息计划。"""

    segments: tuple[ReplySegment, ...] = ()

    @property
    def visible_text(self) -> str:
        return "\n\n".join(
            segment.text
            for segment in self.segments
            if segment.kind in {"dialogue", "narration"} and segment.text
        ).strip()

    @property
    def dialogue_text(self) -> str:
        return "\n".join(
            segment.text
            for segment in self.segments
            if segment.kind == "dialogue" and segment.text
        ).strip()

    @property
    def has_image_action(self) -> bool:
        return any(
            segment.kind == "image" and segment.prompt
            for segment in self.segments
        )


_REPLY_ACTION_PATTERN = re.compile(
    r"（(?P<cn>[^（）]{0,1200})）"
    r"|\((?P<en>[^()\n]{0,1200})\)"
    r"|【(?P<brace>[^【】]{0,1200})】"
    r"|\[(?P<bracket>[^\[\]\n]{0,1200})\]"
)
_IMAGE_WORDS = ("图片", "照片", "自拍", "截图", "画面", "相片")
_IMAGE_VERBS = ("发送", "分享", "发来", "发出", "展示", "递出", "传来")
_IMAGE_REQUEST_NOUN = (
    r"(?:图片|图像|照片|相片|自拍|写真|插画|头像|壁纸|图)"
)
_IMAGE_REQUEST_BOUNDARY = r"(?:^|[，。！？；,.!?;])\s*"
_EXPLICIT_IMAGE_REQUEST_PATTERNS = (
    re.compile(
        _IMAGE_REQUEST_BOUNDARY
        + r"(?:你\s*)?(?:请|麻烦|能不能|可以|可不可以|能|帮我|给我|"
        r"想请你|希望你)?\s*(?:给我\s*)?"
        r"(?:发|发送|传|分享|来|拍|画|生成|做|制作|展示)\s*"
        r"(?:给我\s*)?(?:一|两|几|点|个)?\s*(?:张|幅|个)?\s*"
        r"[^，。！？；,.!?;]{0,18}"
        + _IMAGE_REQUEST_NOUN
    ),
    re.compile(
        _IMAGE_REQUEST_BOUNDARY
        + r"(?:我\s*)?(?:想|想要|希望|可以|能不能|可不可以)?\s*"
        r"(?:看|看看|看一眼|收到)\s*(?:一下\s*)?"
        r"(?:你|你的|一张|几张)?[^，。！？；,.!?;]{0,12}"
        + _IMAGE_REQUEST_NOUN
    ),
    re.compile(
        r"(?:让我|给我|我想|想要|能不能让我|可以让我)"
        r"[^，。！？；,.!?;]{0,8}(?:看看|看一眼)"
        r"[^，。！？；,.!?;]{0,16}"
        r"(?:你|你的|现在|今天)"
        r"[^，。！？；,.!?;]{0,16}"
        r"(?:样子|穿搭|打扮|周围|环境|风景)"
    ),
    re.compile(
        _IMAGE_REQUEST_BOUNDARY
        + r"(?:你\s*)?(?:请|麻烦|能不能|可以|帮我)?\s*"
        r"(?:画|生成|做|制作)\s*(?:一|两|几)?\s*(?:张|幅)"
        r"[^，。！？；,.!?;]{1,80}"
    ),
)


def classify_role_reply(
    text: str,
    *,
    max_dialogue_chars: int = 72,
    max_segments: int = 18,
) -> ReplyPlan:
    """把完整回复分类成对白、旁白和一次图片动作。

    角色提示约定动作与场景写在括号中，因此这里不再额外调用模型，避免
    分类请求篡改原台词或增加一次 API 用量。格式不规范时按对白安全回退。
    """

    source = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not source:
        return ReplyPlan()
    planned: list[ReplySegment] = []
    cursor = 0
    image_count = 0
    for match in _REPLY_ACTION_PATTERN.finditer(source):
        _append_dialogue_segments(
            planned,
            source[cursor : match.start()],
            max_dialogue_chars=max_dialogue_chars,
        )
        cue = next(
            (
                value
                for value in match.groupdict().values()
                if value is not None
            ),
            "",
        )
        cue = " ".join(cue.split()).strip()
        image_prompt = _image_action_prompt(cue)
        if image_prompt and image_count < 1:
            planned.append(ReplySegment("image", prompt=image_prompt))
            image_count += 1
        elif cue:
            planned.append(ReplySegment("narration", text=f"（{cue}）"))
        cursor = match.end()
    _append_dialogue_segments(
        planned,
        source[cursor:],
        max_dialogue_chars=max_dialogue_chars,
    )
    compact = _compact_reply_segments(planned, max_segments=max_segments)
    if not compact:
        _append_dialogue_segments(
            compact,
            source,
            max_dialogue_chars=max_dialogue_chars,
        )
    return ReplyPlan(tuple(compact[:max_segments]))


def explicit_image_request_prompt(text: str) -> str:
    """识别用户明确或常见委婉索图表达，并生成可靠的兜底绘图指令。

    这里只处理高置信度短语；更含蓄的语义由独立 AI 决策器判断。返回非空
    提示词意味着即使 AI 决策失败或选择 false，本轮仍应执行一次图片生成。
    """

    source = " ".join(
        str(text or "").replace("\r", "\n").split()
    ).strip()
    if not source or not any(
        pattern.search(source) for pattern in _EXPLICIT_IMAGE_REQUEST_PATTERNS
    ):
        return ""
    return (
        "用户明确请求角色发送或生成一张图片。优先呈现用户原话中想看的主体、"
        "场景、穿搭或生活片段；如果用户没有指定具体内容，就结合角色当前设定"
        "生成一张自然、亲近、适合在聊天中分享的生活照片。"
        f"用户原话：{source[:600]}"
    )


def serialize_reply_segments(segments: Sequence[ReplySegment]) -> str:
    payload = [
        {
            "kind": segment.kind,
            "text": segment.text,
            "prompt": segment.prompt,
            "image_path": segment.image_path,
        }
        for segment in segments
        if segment.kind in {"dialogue", "narration", "image"}
    ]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def deserialize_reply_segments(value: str) -> tuple[ReplySegment, ...]:
    try:
        payload = json.loads(value or "[]")
    except (TypeError, ValueError):
        return ()
    if not isinstance(payload, list):
        return ()
    result: list[ReplySegment] = []
    for item in payload[:24]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind", "")).strip().lower()
        if kind not in {"dialogue", "narration", "image"}:
            continue
        text = str(item.get("text", "") or "").strip()[:2_000]
        prompt = " ".join(
            str(item.get("prompt", "") or "").split()
        ).strip()[:1_500]
        image_path = str(item.get("image_path", "") or "").strip()[:2_000]
        if kind == "image" and not (prompt or image_path):
            continue
        if kind != "image" and not text:
            continue
        result.append(ReplySegment(kind, text, prompt, image_path))
    return tuple(result)


def enrich_role_image_prompt(character_name: str, card: dict, prompt: str) -> str:
    """把简短的发图动作补成可直接交给文生图服务的提示词。"""

    data = card.get("data", {}) if isinstance(card, dict) else {}
    if not isinstance(data, dict):
        data = {}
    details = []
    for key in ("description", "personality", "scenario"):
        value = " ".join(str(data.get(key, "") or "").split()).strip()
        if value:
            details.append(value[:500])
    profile = "；".join(details)
    parts = [
        f"虚构成年角色{character_name}" if character_name else "虚构成年角色",
        profile,
        prompt.strip()[:1_000],
        "自然生活感，画面完整，无界面、对话气泡、水印或大段文字",
    ]
    return "；".join(part for part in parts if part)


def _append_dialogue_segments(
    target: list[ReplySegment],
    text: str,
    *,
    max_dialogue_chars: int,
) -> None:
    for part in _split_chat_text(text, max_chars=max_dialogue_chars):
        target.append(ReplySegment("dialogue", text=part))


def _split_chat_text(text: str, *, max_chars: int) -> list[str]:
    value = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not value:
        return []
    units: list[str] = []
    for paragraph in re.split(r"\n+", value):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        sentences = [
            item.strip()
            for item in re.split(r"(?<=[。！？!?…])\s*", paragraph)
            if item.strip()
        ]
        units.extend(sentences or [paragraph])

    result: list[str] = []
    current = ""
    limit = max(24, min(int(max_chars), 160))
    for unit in units:
        for piece in _split_long_unit(unit, limit):
            if current and len(current) + len(piece) > limit:
                result.append(current)
                current = piece
            else:
                current = f"{current}{piece}" if current else piece
    if current:
        result.append(current)
    return result


def _split_long_unit(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    pieces = [
        piece
        for piece in re.split(r"(?<=[，,；;：:、])", text)
        if piece
    ]
    if len(pieces) == 1:
        return [
            text[index : index + limit]
            for index in range(0, len(text), limit)
        ]
    result: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) > limit:
            result.append(current)
            current = ""
        while len(piece) > limit:
            head, piece = piece[:limit], piece[limit:]
            if current:
                result.append(current)
                current = ""
            result.append(head)
        current += piece
    if current:
        result.append(current)
    return result


def _image_action_prompt(cue: str) -> str:
    source = cue.strip()
    if not source:
        return ""
    if not (
        any(word in source for word in _IMAGE_WORDS)
        and any(verb in source for verb in _IMAGE_VERBS)
    ):
        return ""
    prompt = re.sub(
        r"^(?:角色|她|他|我)?\s*"
        r"(?:发送|分享|发来|发出|展示|递出|传来)"
        r"(?:一张|这张|我的|刚拍的)?"
        r"(?:图片|照片|自拍|截图|画面|相片)?\s*[:：\-—]?\s*",
        "",
        source,
    ).strip()
    return (prompt or source)[:1_000]


def _compact_reply_segments(
    segments: Sequence[ReplySegment], *, max_segments: int
) -> list[ReplySegment]:
    result: list[ReplySegment] = []
    for segment in segments:
        if (
            result
            and segment.kind == result[-1].kind == "narration"
            and len(result[-1].text) + len(segment.text) <= 180
        ):
            previous = result[-1]
            result[-1] = ReplySegment(
                "narration",
                text=f"{previous.text}\n{segment.text}",
            )
        elif len(result) < max_segments:
            result.append(segment)
        elif result and segment.kind == result[-1].kind != "image":
            previous = result[-1]
            result[-1] = ReplySegment(
                previous.kind,
                text=f"{previous.text}{segment.text}",
            )
    return result


def summary_request(answer: str) -> str:
    return f"请概括下面这条 AI 回复：\n\n{answer.strip()}"


def clean_ai_summary(text: str, limit: int = 60) -> str:
    """清理模型可能附加的 Markdown、标签和多余换行。"""

    value = " ".join(text.replace("\u3000", " ").split()).strip()
    value = re.sub(r"^[#>*\-\s]+", "", value)
    value = re.sub(r"^(?:AI\s*)?摘要\s*[:：]\s*", "", value, flags=re.IGNORECASE)
    value = value.strip("\"'“”‘’ ")
    if len(value) > limit:
        value = value[: limit - 1].rstrip("，。；、 ") + "…"
    return value


def role_memory_request(
    character_name: str,
    previous_state_json: str,
    user_text: str,
    answer: str,
) -> str:
    """构造一次同时产出列表摘要与连续性状态的后台请求。"""

    try:
        previous = json.loads(previous_state_json or "{}")
    except (TypeError, ValueError):
        previous = {}
    if not isinstance(previous, dict):
        previous = {}
    previous_text = json.dumps(previous, ensure_ascii=False)
    return (
        f"角色名：{character_name}\n"
        f"上一版连续性状态：{previous_text[:6_000]}\n\n"
        f"用户本轮消息：\n{user_text.strip()[:2_000] or '（角色主动发起，无用户新消息）'}\n\n"
        f"角色本轮回复：\n{answer.strip()[:4_000]}\n\n"
        "请返回更新后的列表摘要和连续性状态。"
    )


def parse_role_postprocess(text: str) -> RolePostprocessResult:
    """解析并约束模型返回的连续性 JSON；含糊结果不写入长期状态。"""

    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*```$", "", value)
    match = re.search(r"\{.*\}", value, flags=re.DOTALL)
    if match is None:
        return RolePostprocessResult()
    try:
        payload = json.loads(match.group(0))
    except (TypeError, ValueError):
        return RolePostprocessResult()
    if not isinstance(payload, dict):
        return RolePostprocessResult()
    summary = clean_ai_summary(str(payload.get("summary", "")))
    state = _sanitize_role_state(payload.get("role_state"))
    return RolePostprocessResult(summary, state)


def _sanitize_role_state(value) -> dict:
    if not isinstance(value, dict):
        return {}

    def short_text(item, limit: int = 240) -> str:
        return " ".join(str(item or "").split()).strip()[:limit]

    def short_list(item) -> list[str]:
        if not isinstance(item, list):
            return []
        values = [short_text(entry) for entry in item[:6]]
        return [entry for entry in values if entry]

    result = {}
    scene = value.get("scene")
    if isinstance(scene, dict):
        result["scene"] = {
            key: short_text(scene.get(key))
            for key in ("location", "time", "ongoing_action")
        }
    character_state = value.get("character_state")
    if isinstance(character_state, dict):
        result["character_state"] = {
            key: short_text(character_state.get(key))
            for key in ("mood", "current_desire")
        }
    relationship = value.get("relationship")
    if isinstance(relationship, dict):
        result["relationship"] = {
            "stage": short_text(relationship.get("stage")),
            "preferred_address": short_text(
                relationship.get("preferred_address")
            ),
            "boundaries": short_list(relationship.get("boundaries")),
        }
    for key in (
        "user_facts",
        "shared_memories",
        "open_threads",
        "recent_patterns",
    ):
        result[key] = short_list(value.get(key))
    return result


def proactive_request(
    character_name: str,
    *,
    current_time: datetime | None = None,
) -> str:
    context = local_time_context(current_time)
    return (
        f"请以{character_name}的身份，根据最近对话和下面的真实时间，主动给用户发一条"
        "自然的消息并开启话题。\n"
        f"{context.prompt_text}\n"
        "优先选择此刻自然且近期没有重复的话题；直接输出角色会发送的内容。"
    )


def autonomous_image_request(
    character_name: str,
    card: dict,
    history: Sequence[Message],
    answer: str,
) -> str:
    """为独立决策调用整理少量角色信息和最近上下文。"""

    data = card.get("data", {}) if isinstance(card, dict) else {}
    if not isinstance(data, dict):
        data = {}
    character_parts = []
    for label, key in (
        ("角色描述", "description"),
        ("性格", "personality"),
        ("当前场景", "scenario"),
    ):
        value = " ".join(str(data.get(key, "")).split()).strip()
        if value:
            character_parts.append(f"{label}：{value[:900]}")

    recent = []
    for message in history[-8:]:
        role = "角色" if message.role == "assistant" else "用户"
        content = " ".join(message.content.split()).strip()
        if content:
            recent.append(f"{role}：{content[:600]}")

    context = "\n".join(recent) or "（暂无更早对话）"
    profile = "\n".join(character_parts) or "（遵循角色当前设定）"
    return (
        f"角色名：{character_name}\n"
        f"{profile}\n\n"
        f"最近对话：\n{context}\n\n"
        f"刚刚发送的角色消息：\n{answer.strip()[:1_500]}\n\n"
        "判断角色是否会在此刻自主附带一张图片，并按指定 JSON 格式输出。"
    )


def parse_autonomous_image_decision(text: str) -> AutonomousImageDecision:
    """容忍代码围栏，但对含糊或不完整结果一律选择不生图。"""

    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*```$", "", value)
    match = re.search(r"\{.*\}", value, flags=re.DOTALL)
    if match is None:
        return AutonomousImageDecision()
    try:
        payload = json.loads(match.group(0))
    except (TypeError, ValueError):
        return AutonomousImageDecision()
    if not isinstance(payload, dict) or payload.get("send_image") is not True:
        return AutonomousImageDecision()
    prompt = " ".join(str(payload.get("prompt", "")).split()).strip()
    if len(prompt) < 16:
        return AutonomousImageDecision()
    return AutonomousImageDecision(True, prompt[:1_000])


class ProactiveMessageScheduler(QObject):
    """仅在应用运行期间，按设置的随机间隔发出到期信号。"""

    due = Signal()

    def __init__(
        self,
        settings: SettingsRepository,
        parent: QObject | None = None,
        *,
        randint: Callable[[int, int], int] = random.randint,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._randint = randint
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)
        self._next_delay_ms: int | None = None

    @property
    def enabled(self) -> bool:
        return self._settings.get_bool("proactive_enabled", False)

    @property
    def next_delay_ms(self) -> int | None:
        return self._next_delay_ms

    def start(self) -> None:
        self.schedule_next()

    def reload(self) -> None:
        self.schedule_next()

    def stop(self) -> None:
        self._timer.stop()
        self._next_delay_ms = None

    def schedule_next(self) -> None:
        self._timer.stop()
        self._next_delay_ms = None
        if not self.enabled:
            return
        minimum, maximum = self._interval_bounds()
        self._next_delay_ms = self._randint(minimum, maximum) * 60_000
        self._timer.start(self._next_delay_ms)

    def _interval_bounds(self) -> tuple[int, int]:
        try:
            minimum = int(self._settings.get("proactive_min_minutes", "30"))
            maximum = int(self._settings.get("proactive_max_minutes", "120"))
        except ValueError:
            return 30, 120
        minimum = max(5, min(minimum, 1_440))
        maximum = max(minimum, min(maximum, 1_440))
        return minimum, maximum

    def _on_timeout(self) -> None:
        self.schedule_next()
        self.due.emit()
