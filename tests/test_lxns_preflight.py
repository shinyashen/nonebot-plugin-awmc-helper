"""落雪 token 主动续期预检（Q43）：JWT exp 判过期 + best-effort 前置续期。

- 预检省掉闲置超期后首查的必败 401（exp 到秒即失效、无宽限）；
- 解码失败（非 JWT/字段缺失）回退 401 驱动链路；预检失败不上抛；
- 短窗防重放：刚续期过的用户不重放 grant（预检与 401 双入口汇聚防白转）；
- 查询工厂惰性装配：401 续期后的阶梯重试须用上新 token（scores/plates/
  minfo 闭包捕获旧 ident 的修复回归）。
"""

import json
import time
import base64
from pathlib import Path

import pytest
from maimai_py.exceptions import InvalidPlayerIdentifierError


@pytest.fixture
async def db(tmp_path: Path):
    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    binding_service._lxns_refresh_at.clear()
    yield
    binding_service._lxns_refresh_at.clear()
    store.set_db_file(None)


def _jwt(payload: dict) -> str:
    """构造测试用 JWT（签名段不验，token_expiry 只解 payload）。"""

    def enc(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{enc({'alg': 'HS256', 'typ': 'JWT'})}.{enc(payload)}.sig"


def _oauth_configured(monkeypatch):
    from nonebot_plugin_awmc_helper.config import plugin_config

    monkeypatch.setattr(plugin_config, "awmc_lxns_client_id", "cid")
    monkeypatch.setattr(plugin_config, "awmc_lxns_client_secret", "sec")
    monkeypatch.setattr(plugin_config, "awmc_lxns_redirect_uri", "http://localhost/cb")


async def _lxns_binding(user_id: str, token: str):
    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    binding = await binding_service.ensure("OneBot V11", user_id)
    await binding_service.bind_lxns(binding, token=token, friend_code=123)
    binding.lxns_refresh_token = "rt-old"
    await store.save_binding(binding)
    return binding


def _grant_factory(calls: list, new_token: str):
    """伪 refresh grant：记录收到的 rt，返回新 token 三件套。"""

    async def fake_refresh(rt: str):
        calls.append(rt)

        class _T:
            access_token = new_token
            refresh_token = "rt-new"
            friend_code = 123

        return _T()

    return fake_refresh


def test_token_expiry_decode():
    """exp 优先；缺 exp 回退 iat+900（oauth-guide expires_in=900）；异常形状 None。"""
    from nonebot_plugin_awmc_helper.core.ext.lxns import token_expiry

    assert token_expiry(_jwt({"exp": 1234.5, "iat": 1})) == 1234.5
    assert token_expiry(_jwt({"iat": 100})) == 1000
    assert token_expiry("plain-opaque-token") is None  # 非 JWT（历史直绑 token）
    assert token_expiry(_jwt({"sub": "x"})) is None  # 两字段皆缺
    assert token_expiry("a.b.c") is None  # payload 非 JSON 对象


@pytest.mark.asyncio
async def test_preflight_refreshes_stale_token(db, monkeypatch):
    """exp 已过：预检先续期并落库，内存对象同步，后续查询首跳即新 token。"""
    _oauth_configured(monkeypatch)
    from nonebot_plugin_awmc_helper.core.ext import lxns as lxns_ext
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    new_token = _jwt({"exp": time.time() + 600})
    binding = await _lxns_binding("50001", _jwt({"exp": time.time() - 10}))
    calls: list[str] = []
    monkeypatch.setattr(lxns_ext, "refresh_token", _grant_factory(calls, new_token))

    await binding_service.preflight_lxns(binding)
    assert calls == ["rt-old"]
    assert binding.lxns_token == new_token
    assert binding.lxns_refresh_token == "rt-new"
    got = await binding_service.get("OneBot V11", "50001")
    assert got is not None
    assert got.lxns_token == new_token


@pytest.mark.asyncio
async def test_preflight_margin_and_fallback(db, monkeypatch):
    """exp 远期不触发；临近 margin（60s）触发；非 JWT 静默回退不预检。"""
    _oauth_configured(monkeypatch)
    from nonebot_plugin_awmc_helper.core.ext import lxns as lxns_ext
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    calls: list[str] = []
    monkeypatch.setattr(
        lxns_ext,
        "refresh_token",
        _grant_factory(calls, _jwt({"exp": time.time() + 600})),
    )

    fresh = await _lxns_binding("50002", _jwt({"exp": time.time() + 600}))
    await binding_service.preflight_lxns(fresh)
    assert calls == []

    near = await _lxns_binding("50003", _jwt({"exp": time.time() + 30}))
    await binding_service.preflight_lxns(near)
    assert calls == ["rt-old"]

    opaque = await _lxns_binding("50004", "old-token")
    await binding_service.preflight_lxns(opaque)
    assert calls == ["rt-old"]


@pytest.mark.asyncio
async def test_preflight_swallows_refresh_failure(db, monkeypatch):
    """续期链路任何异常都不得使查询入口失败（旧 token 可能仍有效）。"""
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    stale_token = _jwt({"exp": time.time() - 10})
    binding = await _lxns_binding("50005", stale_token)

    async def boom(_b):
        raise RuntimeError("network down")

    monkeypatch.setattr(binding_service, "refresh_lxns", boom)
    await binding_service.preflight_lxns(binding)
    assert binding.lxns_token == stale_token


@pytest.mark.asyncio
async def test_recent_refresh_guard_no_regrant(db, monkeypatch):
    """刚续期成功的短窗内再触发续期（401 驱动双入口汇聚）：不重放 grant。"""
    _oauth_configured(monkeypatch)
    from nonebot_plugin_awmc_helper.core.ext import lxns as lxns_ext
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    binding = await _lxns_binding("50006", _jwt({"exp": time.time() - 10}))
    calls: list[str] = []
    monkeypatch.setattr(
        lxns_ext,
        "refresh_token",
        _grant_factory(calls, _jwt({"exp": time.time() + 600})),
    )

    assert await binding_service.refresh_lxns(binding) == "refreshed"
    assert await binding_service.refresh_lxns(binding) == "refreshed"
    assert calls == ["rt-old"]


@pytest.mark.asyncio
async def test_run_preflight_first_attempt_uses_new_token(db, monkeypatch):
    """_run 入口预检：闲置超期后的首查不再有 401 首跳。"""
    _oauth_configured(monkeypatch)
    from nonebot_plugin_awmc_helper.core import sources
    from nonebot_plugin_awmc_helper.core.ext import lxns as lxns_ext

    new_token = _jwt({"exp": time.time() + 600})
    binding = await _lxns_binding("50007", _jwt({"exp": time.time() - 10}))
    calls: list[str] = []
    monkeypatch.setattr(lxns_ext, "refresh_token", _grant_factory(calls, new_token))

    seen: list[str | None] = []

    async def make_coro():
        seen.append(binding.lxns_token)
        return "ok"

    assert await sources._run(binding, make_coro) == "ok"
    assert seen == [new_token]


@pytest.mark.asyncio
async def test_run_fallback_retry_uses_refreshed_ident(db, monkeypatch):
    """scores/plates 回退链：401 续期后重试重新装配 ident（闭包修复回归）。"""
    _oauth_configured(monkeypatch)
    from nonebot_plugin_awmc_helper.core import sources
    from nonebot_plugin_awmc_helper.core.ext import lxns as lxns_ext
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    old_token = _jwt({"exp": time.time() - 10})
    new_token = _jwt({"exp": time.time() + 600})
    binding = await _lxns_binding("50008", old_token)
    calls: list[str] = []
    monkeypatch.setattr(lxns_ext, "refresh_token", _grant_factory(calls, new_token))

    # 本用例隔离 401 重试链的 ident 重装配行为，预检置空（其余用例已覆盖）
    async def no_preflight(_b):
        return None

    monkeypatch.setattr(binding_service, "preflight_lxns", no_preflight)

    attempts: list[str | None] = []

    def make(get_ident):
        async def factory():
            attempts.append(get_ident().credentials)
            if len(attempts) == 1:
                raise InvalidPlayerIdentifierError("unauthorized")
            return "ok"

        return factory

    assert await sources._run_full(binding, make) == "ok"
    assert attempts == [old_token, new_token]
