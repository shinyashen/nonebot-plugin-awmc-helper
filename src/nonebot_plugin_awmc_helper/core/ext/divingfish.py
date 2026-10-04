"""水鱼直连：RA 排行榜 + OAuth 设备码绑定（maimai-py 未覆盖的部分）。"""

import re
from dataclasses import dataclass

from . import (
    ExtError,
    fetch_json,
    ext_request,
    jwt_payload_unverified,
)
from ..cache import TtlCache
from ...config import plugin_config

RANKING_URL = "https://www.diving-fish.com/api/maimaidxprober/rating_ranking"
MUSIC_DATA_URL = "https://www.diving-fish.com/api/maimaidxprober/music_data"


async def fetch_music_data() -> list[dict]:
    """直连拉取水鱼曲库（国服对账源：version_cn/定数互证，song-db-design §7.2）。

    与落雪同为唯二国服源；不得走 MaimaiClient（理由同 lxns.fetch_song_list）。
    """
    return await fetch_json(MUSIC_DATA_URL, name="水鱼曲库")


@dataclass
class RankUser:
    username: str
    ra: int


_RANKING_CACHE = TtlCache(ttl=600.0)
"""RA 排行榜进程内缓存（TTL 600s，:class:`core.cache.TtlCache`）。榜单位于
「查看排名/我的排名」用户指令热路径，TTL 内复用免每次直拉（全服统计态，
短缓存无一致性代价）；缓存期返回共享列表，调用方约定只读不改动。"""
_RANKING_CACHE_KEY = "rating_ranking"


def _ranking_cache_clear() -> None:
    """清空排行榜缓存（测试用）。"""
    _RANKING_CACHE.clear()


async def rating_ranking() -> list[RankUser]:
    """全量 RA 排行（按 RA 从高到低；进程内 TTL 缓存，见 :data:`_RANKING_CACHE`）。"""
    if (users := _RANKING_CACHE.get(_RANKING_CACHE_KEY)) is not None:
        return users
    data = await fetch_json(RANKING_URL, name="水鱼排行榜")
    users = [RankUser(username=u["username"], ra=int(u["ra"])) for u in data]
    users.sort(key=lambda x: x.ra, reverse=True)
    _RANKING_CACHE.set(_RANKING_CACHE_KEY, users)
    return users


# ---------------------------------------------------------------------------
# OAuth 设备码绑定（handoff=code 确认码回填形态，对齐 Hoshino maimaiDX 上游；
# 2026-09-28：水鱼写路径强制 OAuth，导分统一凭据迁移）
# ---------------------------------------------------------------------------

DF_AUTH_BASE = "https://auth.diving-fish.com"
REVOKE_URL = DF_AUTH_BASE + "/apps"
# scope 一次带齐 read+write（sunset 文档 §定案④：写为导分统一凭据，2026-09-28 已获批）
OAUTH_SCOPE = "prober.records.read prober.records.write"

# 确认码字母表：去掉元音（避免随机拼出脏词）与形近字符（0/1/L），用户要在
# 聊天窗口转发，认错一个字符就白跑一趟（同 Hoshino 上游）
_CONFIRMATION_ALPHABET = "BCDFGHJKLMNPQRSTVWXZ"
_CONFIRMATION_LENGTH = 12
_BODY_PATTERN = re.compile(rf"^[{_CONFIRMATION_ALPHABET}]{{{_CONFIRMATION_LENGTH}}}$")
_PREFIX_PATTERN = re.compile(r"^(?:确认码|授权码)\s*[:：]?\s*(\S+)$")


def extract_confirmation_code(text: str) -> str | None:
    """从整条消息认出确认码并归一化为 ``XXXX-XXXX-XXXX``；不是码返回 None。

    容错小写、连字符/空格丢失、「确认码：/授权码：」前缀（后者是落雪提取器
    的文案，混输场景容错面与之对齐）；只认「整条消息就是一串码」——从句子
    里抠码会把恰好凑够十二个字母的闲聊当码送去兑换（该规则要过每一条群
    消息）。
    """
    value = (text or "").strip()
    prefixed = _PREFIX_PATTERN.fullmatch(value)
    if prefixed:
        value = prefixed.group(1)
    body = re.sub(r"[\s\-—_]", "", value).upper()
    if _BODY_PATTERN.fullmatch(body):
        return f"{body[:4]}-{body[4:8]}-{body[8:]}"
    return None


_IMPORT_TOKEN_PATTERN = re.compile(r"^[0-9a-fA-F]{64,}$")


def looks_like_import_token(text: str) -> bool:
    """参数是否形似 Import-Token（64+ 位连续十六进制）。

    「绑定水鱼 <参数>」曾把参数当用户名落库，误投 token 后 b50 按用户名
    公开查询必败（生产实测 2026-09-28，用户名 = token → 水鱼 400 user not
    exists）；绑定层据此在用户名/确认码入口软引导（token → 「绑定水鱼token」，
    用户名 → 「绑定水鱼用户名」），拒绝输入不落库。真实水鱼用户名为 64+
    位纯十六进制串的情形可忽略不计。
    """
    return bool(_IMPORT_TOKEN_PATTERN.fullmatch((text or "").strip()))


def binding_label(qq: str) -> str:
    """授权页展示的绑定身份遮罩串，用户凭它确认不是在给别人授权。"""
    if len(qq) <= 4:
        return f"QQ {qq}"
    return f"QQ {qq[:2]}{'*' * (len(qq) - 4)}{qq[-2:]}"


def token_subject(access_token: str) -> str | None:
    """读出 access token 的 ``sub``（水鱼用户 ID）。

    **只解不验**：令牌是 bot 刚从水鱼账号服务取回的，仅用于自洽性比对，
    真正的验签由资源服务器做。
    """
    payload = jwt_payload_unverified(access_token)
    return payload.get("sub") if payload else None


def oauth_ready() -> bool:
    """OAuth 机密客户端凭据是否已配置（设备码流程与 subject 换票的前提）。"""
    return bool(
        plugin_config.awmc_divingfish_oauth_client_id
        and plugin_config.awmc_divingfish_oauth_client_secret
    )


class DivingFishSubjectMismatch(ExtError):
    """确认码对应的授权不属于发起绑定的账号（subject_ref 比对失败）。

    供调用方与一般 ExtError 区分：mismatch 是钓鱼/转发场景，要给专项
    文案；其余错误统一兜底即可。
    """


async def _post_form(url: str, data: dict) -> tuple[int, dict]:
    """OAuth 端点公共封装：网络错误经 ext_request 包装、JSON 解析转 ExtError。"""
    resp = await ext_request(
        "POST",
        url,
        name="水鱼授权服务",
        network_message="水鱼授权服务网络异常，请稍后再试",
        data=data,
    )
    try:
        resp_data = resp.json()
    except Exception as e:
        raise ExtError(f"水鱼授权服务响应异常（HTTP {resp.status_code}）") from e
    return resp.status_code, resp_data


async def device_authorize(ref: str, label: str) -> dict:
    """发起水鱼绑定（``handoff=code`` 确认码回填形态）。

    ``handoff=code`` 让 device_code 换不到令牌、改由用户回填确认码收尾——
    发起绑定不需要任何凭据，谁都能拿本 bot 的 client_id 造链接填自己的
    标识转发给别人；确认码只出现在点同意那个人的浏览器里，回填验的就是
    「点同意的人」和「发起绑定的人」是同一个（对齐 Hoshino 上游 oauth.py）。

    返回含 ``verification_uri_complete``（用户码 20 分钟有效窗口内可重复
    进入）等字段的原始响应 dict；OAuth 错误体转 :class:`ExtError`。
    """
    status, data = await _post_form(
        f"{DF_AUTH_BASE}/oauth/device_authorization",
        {
            "client_id": plugin_config.awmc_divingfish_oauth_client_id,
            "client_secret": plugin_config.awmc_divingfish_oauth_client_secret,
            "scope": OAUTH_SCOPE,
            "subject_ref": ref,
            "binding_label": label,
            "handoff": "code",
        },
    )
    if status != 200 or "error" in data:
        detail = data.get("error_description") or data.get("error") or status
        raise ExtError(f"水鱼设备码发起失败：{detail}")
    return data


async def redeem(ref: str, confirmation_code: str) -> dict:
    """用用户回填的确认码兑换令牌（``confirmation-code`` grant）。

    一并送上发起绑定时提交的 ref，让水鱼比对「回填的人」和「发起的人」；
    不匹配时水鱼回 ``invalid_grant`` 且不消费那串码。
    """
    status, data = await _post_form(
        f"{DF_AUTH_BASE}/oauth/token",
        {
            "grant_type": "urn:diving-fish:params:oauth:grant-type:confirmation-code",
            "client_id": plugin_config.awmc_divingfish_oauth_client_id,
            "client_secret": plugin_config.awmc_divingfish_oauth_client_secret,
            "confirmation_code": confirmation_code,
            "subject_ref": ref,
        },
    )
    if status != 200 or "error" in data:
        error = data.get("error", "")
        if error == "subject_mismatch":
            raise DivingFishSubjectMismatch("这串确认码对应的授权不属于发起绑定的账号")
        if error == "invalid_grant":
            raise ExtError("确认码不存在、已过期或已使用")
        detail = data.get("error_description") or error or status
        raise ExtError(f"水鱼确认码兑换失败：{detail}")
    return data
