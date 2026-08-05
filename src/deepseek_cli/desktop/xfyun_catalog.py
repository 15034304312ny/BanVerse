"""讯飞超拟人 TTS 发音人目录、账号可用性缓存与自动匹配。"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class XfyunVoice:
    name: str
    id: str
    gender: str
    language: str
    category: str

    @property
    def gender_label(self) -> str:
        if "男" in self.gender:
            return "男声"
        if "女" in self.gender:
            return "女声"
        return self.gender or "其他"

    @property
    def label(self) -> str:
        return (
            f"{self.category}｜{self.gender_label}｜{self.name}"
            f"（{self.id}）"
        )


def _voice(
    name: str,
    voice_id: str,
    gender: str,
    category: str,
    language: str = "中文普通话",
) -> XfyunVoice:
    return XfyunVoice(name, voice_id, gender, language, category)


# 来自讯飞官方“超拟人语音合成 API”发音人表。文档中的五个 x6
# 默认免费 flow ID 与接口当前 schema 不一致，因此同时保留服务端实际接受的
# x5 兼容 ID，并不把无效的 x6 flow ID 放入可选列表。
XFYUN_VOICES = (
    _voice("士兵男声", "x6_youxinanshibing_pro", "成年男", "角色配音"),
    _voice("大会主持女声", "x6_zhuanyenvzhuchi_pro", "成年女", "大会主持"),
    _voice("李白模仿", "x6_zuixianlibai_pro", "成年男", "IP 模仿"),
    _voice("奶凶辛巴", "x6_shiwangxiaoxin_pro", "童年男", "IP 模仿"),
    _voice("播客男声", "x6_bokenansheng_pro", "成年男", "广播电台"),
    _voice("大会主持男声", "x6_zhuanyenanzhuchi_pro", "成年男", "大会主持"),
    _voice("运动陪练女声", "x6_ranzhinvdazi_pro", "成年女", "运动辅助"),
    _voice("展厅接待男声", "x6_zhantingnanjiedai_pro", "成年男", "展厅接待"),
    _voice("展厅接待女声", "x6_zhantingnvjiedai_pro", "成年女", "展厅接待"),
    _voice("回访女声", "x6_huifangnv_pro", "成年女", "客服"),
    _voice("温暖磁性男声", "x6_wennuancixingnansheng_mini", "成年男", "角色配音"),
    _voice("小奶狗弟弟", "x6_xiaonaigoudidi_mini", "成年男", "角色配音"),
    _voice("士兵女声", "x6_shibingnvsheng_mini", "成年女", "角色配音"),
    _voice("恐怖女声", "x6_kongbunvsheng_mini", "成年女", "悬疑旁白"),
    _voice("娱乐新闻女声", "x6_yulexinwennvsheng_mini", "成年女", "娱乐新闻"),
    _voice("温柔男声", "x6_wenrounansheng_mini", "成年男", "售后客服"),
    _voice("景区导览女声", "x6_jingqudaolannvsheng_mini", "成年女", "景区导览"),
    _voice("大气宣传片男声", "x6_daqixuanchuanpiannansheng_mini", "成年男", "广告宣传"),
    _voice("催收女声", "x6_cuishounvsheng_pro", "成年女", "催收客服"),
    _voice("营销女声", "x6_yingxiaonv_pro", "成年女", "营销客服"),
    _voice("海绵宝宝", "x6_huanlemianbao_pro", "童年男", "IP 模仿"),
    _voice("商务殷语", "x6_xiangruiyingyu_pro", "成年男", "IP 模仿"),
    _voice("台湾腔温柔男声", "x6_taiqiangnuannan_pro", "成年男", "台湾话", "台湾话"),
    _voice("妩媚姐姐", "x6_wumeinv_pro", "成年女", "角色配音"),
    _voice("聆伯松", "x6_lingbosong_pro", "成年男", "角色配音"),
    _voice("少女可莉", "x6_dudulibao_pro", "童年女", "IP 模仿"),
    _voice("滑稽大妈", "x6_huajidama_pro", "成年女", "角色配音"),
    _voice("活泼少年", "x6_huoposhaonian_pro", "成年男", "角色配音"),
    _voice("聆小雪", "x6_lingxiaoxue_pro", "成年女", "角色配音"),
    _voice("古风侠女", "x6_gufengxianv_mini", "成年女", "角色配音"),
    _voice("午夜电台", "x6_wuyediantai_mini", "成年女", "角色配音"),
    _voice("贴心男友", "x6_tiexinnanyou_mini", "成年男", "角色配音"),
    _voice("聆小璃", "x6_lingxiaoli_pro", "成年女", "交互聊天"),
    _voice("聆小琪", "x6_xiaoqiChat_pro", "成年女", "交互聊天"),
    _voice("聆飞逸", "x6_lingfeiyi_pro", "成年男", "交互聊天"),
    _voice("聆飞哲", "x6_feizheChat_pro", "成年男", "交互聊天"),
    _voice("聆小玥", "x6_lingxiaoyue_pro", "成年女", "交互聊天"),
    _voice("聆小璇", "x6_lingxiaoxuan_pro", "成年女", "交互聊天"),
    _voice("聆玉言", "x6_lingyuyan_pro", "成年女", "交互聊天"),
    _voice("旁白男声", "x6_pangbainan1_pro", "成年男", "旁白配音"),
    _voice("旁白女声", "x6_pangbainv1_pro", "成年女", "旁白配音"),
    _voice("聆飞瀚", "x6_lingfeihan_pro", "成年男", "纪录片"),
    _voice("聆飞皓", "x6_lingfeihao_pro", "成年男", "广告促销"),
    _voice("古风旁白", "x6_gufengpangbai_pro", "成年男", "旁白配音"),
    _voice("聆园儿", "x6_lingyuaner_pro", "成年女", "儿童绘本"),
    _voice("干练女性", "x6_ganliannvxing_pro", "成年女", "角色配音"),
    _voice("儒雅大叔", "x6_ruyadashu_pro", "成年男", "角色配音"),
    _voice("聆玉菲", "x6_lingyufei_pro", "成年女", "时政新闻"),
    _voice("聆小珊", "x6_lingxiaoshan_pro", "成年女", "时政新闻"),
    _voice("聆小芸", "x6_lingxiaoyun_pro", "成年女", "角色配音"),
    _voice("聆佑佑", "x6_lingyouyou_pro", "童年女", "交互聊天"),
    _voice("聆小颖", "x6_lingxiaoying_pro", "成年女", "交互聊天"),
    _voice("聆小瑱", "x6_lingxiaozhen_pro", "成年女", "直播带货"),
    _voice("聆飞博", "x6_lingfeibo_pro", "成年男", "时政新闻"),
    _voice("外国大叔", "x6_waiguodashu_pro", "成年男", "角色配音", "外国口音中文"),
    _voice("高冷男神", "x6_gaolengnanshen_pro", "成年男", "角色配音"),
    _voice("动漫少女", "x6_dongmanshaonv_pro", "成年女", "动漫角色"),
    _voice("聆小糖", "x5_lingxiaotang_flow", "成年女", "语音助手"),
    _voice("聆玉昭", "x5_lingyuzhao_flow", "成年女", "交互聊天"),
    _voice("子津", "x4_zijin_oral", "成年男", "交互聊天", "天津话"),
    _voice("子阳", "x4_ziyang_oral", "成年男", "交互聊天", "东北话"),
    _voice("Grant", "x5_EnUs_Grant_flow", "成年男", "交互聊天", "英文美式"),
    _voice("Lila", "x5_EnUs_Lila_flow", "成年女", "交互聊天", "英文美式"),
    _voice("聆小璇（兼容）", "x5_lingxiaoxuan_flow", "成年女", "默认免费"),
    _voice("聆飞逸（兼容）", "x5_lingfeiyi_flow", "成年男", "默认免费"),
    _voice("聆小玥（兼容）", "x5_lingxiaoyue_flow", "成年女", "默认免费"),
    _voice("聆玉言（兼容）", "x5_lingyuyan_flow", "成年女", "默认免费"),
)

XFYUN_VOICE_BY_ID = {voice.id: voice for voice in XFYUN_VOICES}
XFYUN_TTS_VOICE_OPTIONS = (
    ("自动匹配角色（仅使用已验证音色）", "auto"),
    *((voice.label, voice.id) for voice in XFYUN_VOICES),
)


def deserialize_available_voices(value: str) -> tuple[str, ...]:
    try:
        payload = json.loads(value or "[]")
    except (TypeError, ValueError):
        return ()
    if not isinstance(payload, list):
        return ()
    known = XFYUN_VOICE_BY_ID
    result: list[str] = []
    for item in payload:
        voice_id = str(item or "").strip()
        if voice_id in known and voice_id not in result:
            result.append(voice_id)
    return tuple(result)


def serialize_available_voices(voice_ids) -> str:
    values = [
        voice_id
        for voice_id in dict.fromkeys(str(item).strip() for item in voice_ids)
        if voice_id in XFYUN_VOICE_BY_ID
    ]
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


def available_voice_options(
    voice_ids: tuple[str, ...], *, current: str = ""
) -> tuple[tuple[str, str], ...]:
    """账号已探测时仅展示可调用音色，并保留一个失效当前值作提示。"""

    options: list[tuple[str, str]] = [
        ("自动匹配角色（仅使用已验证音色）", "auto")
    ]
    available = set(voice_ids)
    for voice in XFYUN_VOICES:
        if voice.id in available:
            options.append((f"已验证｜{voice.label}", voice.id))
    if current and current not in {"auto", *available}:
        voice = XFYUN_VOICE_BY_ID.get(current)
        label = voice.label if voice else current
        options.append((f"未开通｜{label}", current))
    return tuple(options)


def automatic_voice(edge_voice: str, available_ids=()) -> str:
    """按角色性别从已验证目录匹配，未探测时使用当前有效的 x5 ID。"""

    wants_male = "yun" in edge_voice.lower()
    preferred = (
        "x5_lingfeiyi_flow" if wants_male else "x5_lingxiaoxuan_flow"
    )
    available = tuple(available_ids)
    if not available or preferred in available:
        return preferred
    for voice_id in available:
        voice = XFYUN_VOICE_BY_ID.get(voice_id)
        if voice is not None and (("男" in voice.gender) == wants_male):
            return voice_id
    return available[0]

