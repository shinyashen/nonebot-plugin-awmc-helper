"""日服 NET 绑定验证域（无注册副作用：matcher 在 matchers.py）。

「绑定即验证」：绑定前用凭据实登录一次 NET，能区分密码错误（拒绝保存）
与临时故障（维护/改版，凭据照存、查询时再生效）——凭据敏感级别高，
宁可多一次登录也不落库无效凭据。
"""

from ...core.ext.net import NetError, NetCredentials, MaimaiNetClient

INVALID_CREDENTIALS = " SEGA ID 或密码错误，绑定未保存"


async def verify_net_credentials(sega_id: str, password: str) -> tuple[bool, str]:
    """实登录验证 NET 凭据，返回 (凭据是否有效, 完成消息附注)。

    - 密码错误：返回 (False, "")——调用方终止绑定、不落库；
    - NET 维护/页面改版等可识别故障：返回 (True, 已保存附注)——凭据照存，
      稍后查询时生效；
    - 网络不可达等未知异常：同上按已保存处理（附注措辞弱化）。
    """
    client = MaimaiNetClient()
    try:
        await client.login(NetCredentials(sega_id=sega_id, password=password))
    except NetError as e:
        if e.code == "invalid_credentials":
            return False, ""
        return True, f"\n（凭据已保存；NET 当前无法验证：{e.code}，稍后查询时生效）"
    except Exception:
        return True, "\n（凭据已保存；NET 暂时无法连接验证，稍后查询时生效）"
    finally:
        await client.aclose()
    return True, ""
