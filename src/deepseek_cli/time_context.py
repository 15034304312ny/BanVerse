"""为角色提示提供可信、可测试的设备本地时间上下文。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

_WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


@dataclass(frozen=True, slots=True)
class LocalTimeContext:
    moment: datetime
    period: str
    guidance: str

    @property
    def display(self) -> str:
        timezone_name = self.moment.tzname() or "本地时区"
        return (
            f"{self.moment.year}年{self.moment.month}月{self.moment.day}日 "
            f"{_WEEKDAYS[self.moment.weekday()]} "
            f"{self.moment:%H:%M}（{timezone_name}，{self.period}）"
        )

    @property
    def prompt_text(self) -> str:
        return (
            f"用户设备当前本地时间：{self.display}\n"
            f"时段提示：{self.guidance}\n"
            "时间只用于校准问候、饮食、作息和场景，不要每次都报时，也不要仅凭时间"
            "断言用户已经吃饭、正在上班、失眠或尚未睡觉。"
        )


def local_time_context(moment: datetime | None = None) -> LocalTimeContext:
    """把给定时刻划分为适合中文生活对话的时段。"""

    current = moment or datetime.now().astimezone()
    if current.tzinfo is None:
        current = current.astimezone()
    minutes = current.hour * 60 + current.minute

    if 5 * 60 <= minutes < 8 * 60 + 30:
        period = "清晨"
        guidance = "适合早安、昨晚睡眠、早餐或今天安排；不要过早催促用户。"
    elif 8 * 60 + 30 <= minutes < 11 * 60 + 15:
        period = "上午"
        guidance = "适合关心上午状态、工作学习或稍后安排；此时不要说已经到了午饭时间。"
    elif 11 * 60 + 15 <= minutes < 13 * 60 + 30:
        period = "午间"
        guidance = "可以自然问午饭吃什么、吃过没有或分享午餐，但不要假定用户一定按时吃饭。"
    elif 13 * 60 + 30 <= minutes < 17 * 60 + 30:
        period = "下午"
        guidance = "适合聊下午状态、工作学习、短暂休息或下午茶。"
    elif 17 * 60 + 30 <= minutes < 20 * 60 + 30:
        period = "傍晚"
        guidance = "适合聊下班放学、晚饭、回家路上或傍晚见闻。"
    elif 20 * 60 + 30 <= minutes < 22 * 60 + 45:
        period = "晚间"
        guidance = "适合聊今天过得怎样、晚间活动和放松安排，不要提前断言用户要睡了。"
    elif minutes >= 22 * 60 + 45 or minutes < 2 * 60 + 30:
        period = "深夜"
        guidance = "若用户仍在使用软件，可温和问怎么还没睡、是否睡不着或失眠，语气克制且不作诊断。"
    else:
        period = "凌晨"
        guidance = "这是很晚的作息时段；适合轻声关心是否睡不着，并避免频繁、兴奋或催促式消息。"
    return LocalTimeContext(current, period, guidance)
