"""水鱼直连：RA 排行榜 + OAuth 设备码绑定（maimai-py 未覆盖的部分）。"""

import re
import json
import base64
from dataclasses import dataclass

from . import ExtError, fetch_json, get_client
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
    rating: int | None = None


async def rating_ranking() -> list[RankUser]:
    """全量 RA 排行（按 RA 从高到低）。"""
    data = await fetch_json(RANKING_URL, name="水鱼排行榜")
    users = [
        RankUser(username=u["username"], ra=int(u["ra"]), rating=u.get("rating"))
        for u in data
    ]
    users.sort(key=lambda x: x.ra, reverse=True)
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

    容错小写、连字符/空格丢失、「确认码：」前缀；只认「整条消息就是一串
    码」——从句子里抠码会把恰好凑够十二个字母的闲聊当码送去兑换（该规则
    要过每一条群消息）。
    """
    value = (text or "").strip()
    prefixed = _PREFIX_PATTERN.fullmatch(value)
    if prefixed:
        value = prefixed.group(1)
    body = re.sub(r"[\s\-—_]", "", value).upper()
    if _BODY_PATTERN.fullmatch(body):
        return f"{body[:4]}-{body[4:8]}-{body[8:]}"
    return None


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
    try:
        payload = access_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("sub")
    except (IndexError, ValueError):
        return None


def oauth_ready() -> bool:
    """OAuth 机密客户端凭据是否已配置（设备码流程与 subject 换票的前提）。"""
    return bool(
        plugin_config.awmc_divingfish_oauth_client_id
        and plugin_config.awmc_divingfish_oauth_client_secret
    )


def subject_ref(client_id: str, external_id: str) -> str:
    """ref 摘要（不含 ``ref:`` 前缀）：client_id 与 external_id 的 sha256 小写 hex。"""
    import hashlib

    return hashlib.sha256(f"{client_id}:{external_id}".encode()).hexdigest()


async def device_authorize(ref: str, label: str) -> dict:
    """发起水鱼绑定（``handoff=code`` 确认码回填形态）。

    ``handoff=code`` 让 device_code 换不到令牌、改由用户回填确认码收尾——
    发起绑定不需要任何凭据，谁都能拿本 bot 的 client_id 造链接填自己的
    标识转发给别人；确认码只出现在点同意那个人的浏览器里，回填验的就是
    「点同意的人」和「发起绑定的人」是同一个（对齐 Hoshino 上游 oauth.py）。

    返回含 ``verification_uri_complete``（用户码 20 分钟有效窗口内可重复
    进入）等字段的原始响应 dict；OAuth 错误体转 :class:`ExtError`。
    """
    resp = await get_client().post(
        f"{DF_AUTH_BASE}/oauth/device_authorization",
        data={
            "client_id": plugin_config.awmc_divingfish_oauth_client_id,
            "client_secret": plugin_config.awmc_divingfish_oauth_client_secret,
            "scope": OAUTH_SCOPE,
            "subject_ref": ref,
            "binding_label": label,
            "handoff": "code",
        },
    )
    try:
        data = resp.json()
    except Exception as e:
        raise ExtError(f"水鱼授权服务响应异常（HTTP {resp.status_code}）") from e
    if resp.status_code != 200 or "error" in data:
        detail = data.get("error_description") or data.get("error") or resp.status_code
        raise ExtError(f"水鱼设备码发起失败：{detail}")
    return data


async def redeem(ref: str, confirmation_code: str) -> dict:
    """用用户回填的确认码兑换令牌（``confirmation-code`` grant）。

    一并送上发起绑定时提交的 ref，让水鱼比对「回填的人」和「发起的人」；
    不匹配时水鱼回 ``invalid_grant`` 且不消费那串码。
    """
    resp = await get_client().post(
        f"{DF_AUTH_BASE}/oauth/token",
        data={
            "grant_type": "urn:diving-fish:params:oauth:grant-type:confirmation-code",
            "client_id": plugin_config.awmc_divingfish_oauth_client_id,
            "client_secret": plugin_config.awmc_divingfish_oauth_client_secret,
            "confirmation_code": confirmation_code,
            "subject_ref": ref,
        },
    )
    try:
        data = resp.json()
    except Exception as e:
        raise ExtError(f"水鱼授权服务响应异常（HTTP {resp.status_code}）") from e
    if resp.status_code != 200 or "error" in data:
        error = data.get("error", "")
        if error == "subject_mismatch":
            raise ExtError("mismatch: 这串确认码对应的授权不属于发起绑定的账号")
        if error == "invalid_grant":
            raise ExtError("确认码不存在、已过期或已使用")
        detail = data.get("error_description") or error or resp.status_code
        raise ExtError(f"水鱼确认码兑换失败：{detail}")
    return data
