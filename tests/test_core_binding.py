"""core/binding 绑定服务测试：ensure/identifier 装配、session 键语义、
pending 回填会话、落雪续期并发对拍与代查目标解析链。"""

from pathlib import Path

import pytest


@pytest.fixture
async def db(tmp_path: Path):
    from nonebot_plugin_awmc_helper.core import store

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    yield
    store.set_db_file(None)


@pytest.fixture
async def songs(db):
    from mocks import seed_service

    from nonebot_plugin_awmc_helper.core.songs import song_service

    await seed_service(song_service)
    yield
    song_service._ready.clear()


@pytest.mark.asyncio
async def test_binding_ensure_and_identifier(db):
    from maimai_py import PlayerIdentifier

    from nonebot_plugin_awmc_helper.core.binding import (
        SERVICE_LXNS,
        SERVICE_DIVINGFISH,
        BindingError,
        binding_service,
    )

    binding = await binding_service.ensure("qq", "10001")
    assert binding.service == SERVICE_DIVINGFISH  # 部署默认
    # 空 QQ 绑定 → qq 公开查询凭据可用
    ident = binding_service.identifier(binding)
    assert ident.qq == 10001

    # 非 QQ 平台且无凭据 → 不可查
    binding2 = await binding_service.ensure("discord", "20002")
    with pytest.raises(BindingError):
        binding_service.identifier(binding2)

    # 水鱼用户名绑定
    await binding_service.bind_divingfish_username(binding, "tester")
    got = await binding_service.get("qq", "10001")
    assert got is not None
    assert got.divingfish_username == "tester"

    # 落雪切换：无凭据拒绝
    from nonebot_plugin_awmc_helper.core.binding import BindingError as BE

    with pytest.raises(BE):
        await binding_service.set_service(binding, SERVICE_LXNS)
    await binding_service.bind_lxns(binding, token="jwt-token", friend_code=123456)
    await binding_service.set_service(binding, SERVICE_LXNS)
    got = await binding_service.get("qq", "10001")
    assert got is not None
    assert got.service == SERVICE_LXNS
    ident = binding_service.identifier(got)
    assert isinstance(ident, PlayerIdentifier)
    assert ident.credentials == "jwt-token"
    assert ident.friend_code == 123456

    # 主题与解绑
    await binding_service.set_theme(got, "circle")
    got_after = await binding_service.get("qq", "10001")
    assert got_after is not None
    assert got_after.theme == "circle"
    assert await binding_service.unbind("qq", "10001")
    assert await binding_service.get("qq", "10001") is None


@pytest.mark.asyncio
async def test_identifier_qq_semantics(db):
    """OneBot v11 运行时键为适配器标识 "OneBot V11"（uninfo 不填 platform，
    adapter 名即平台语义，session_keys 统一产出）：user_id 应照常按 QQ 号装配
    水鱼凭据，与 Hoshino 迁移数据（历史键 "qq"）同等对待；真正的非 QQ 平台
    仍不可查。"""
    from nonebot_plugin_uninfo import User, Scene, Session, SceneType

    from nonebot_plugin_awmc_helper.core.binding import (
        QQ_PLATFORMS,
        BindingError,
        session_keys,
        binding_service,
    )

    assert QQ_PLATFORMS == {"qq", "OneBot V11"}
    session = Session(
        self_id="test",
        adapter="OneBot V11",
        scope="qq_client",
        scene=Scene(id="30003", type=SceneType.PRIVATE),
        user=User(id="30003"),
        member=None,
        operator=None,  # OneBot v11 实际不填 platform
    )
    platform, user_id = session_keys(session)
    assert (platform, user_id) == ("OneBot V11", "30003")

    binding = await binding_service.ensure(platform, user_id)
    ident = binding_service.identifier(binding)
    assert ident.qq == 30003

    other = await binding_service.ensure("telegram", "40004")
    with pytest.raises(BindingError):
        binding_service.identifier(other)


@pytest.mark.asyncio
async def test_pending_bindings():
    from nonebot_plugin_awmc_helper.core.binding import pending_bindings

    pending_bindings.start("qq", "1", "lxns", ttl=60)
    assert pending_bindings.is_active("qq", "1", "lxns")
    assert not pending_bindings.is_active("qq", "2")

    # 过期
    pending_bindings.start("qq", "1", "lxns", ttl=-1)
    assert not pending_bindings.is_active("qq", "1")


def test_any_active_short_circuit_semantics():
    """any_active 空表短路 O(1)（L-29）：只回答「有无候选会话」。

    非空表含过期残留时也 True（过期惰性清除语义，精确判定仍走
    is_active/take）——拦截规则借此免走每条消息的 Session 构造。
    """
    from nonebot_plugin_awmc_helper.core.binding import pending_bindings
    from nonebot_plugin_awmc_helper.core.session_store import (
        TtlSession,
        TtlSessionStore,
    )

    store: TtlSessionStore[str, TtlSession] = TtlSessionStore()
    assert store.any_active() is False  # 空表短路
    store.start("k", TtlSession(expire_at=-1.0))  # 已过期的残留
    assert store.any_active() is True

    pending_bindings.start("qq", "1", "lxns", ttl=60)
    assert pending_bindings.any_active() is True
    pending_bindings.discard("qq", "1")
    assert pending_bindings.any_active() is False


@pytest.mark.asyncio
async def test_binding_ensure_concurrent_first_bind(db, monkeypatch):
    """并发首绑定双 INSERT 竞态（L-14）：后 commit 方撞主键 → 捕
    IntegrityError 重读返回既有行，两协程都拿到绑定而不抛。"""
    import asyncio

    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.config import plugin_config
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    real_get = store.get_binding
    state = {"i": 0}
    ready = (asyncio.Event(), asyncio.Event())

    async def gated_get(platform, user_id):
        """前两查模拟「双方都看到空表」：互等放行后再各自 INSERT。"""
        i = state["i"]
        state["i"] += 1
        if i < 2:
            ready[i].set()
            await ready[1 - i].wait()
            return None
        return await real_get(platform, user_id)

    monkeypatch.setattr(store, "get_binding", gated_get)
    results = await asyncio.gather(
        binding_service.ensure("OneBot V11", "77700001"),
        binding_service.ensure("OneBot V11", "77700001"),
    )
    assert all(b.service == plugin_config.awmc_default_provider for b in results)
    row = await real_get("OneBot V11", "77700001")
    assert row is not None  # 库里恰好一行


@pytest.mark.asyncio
async def test_lxns_refresh_concurrent_single_call(db, songs, monkeypatch):
    """并发续期对拍：同用户两协程同时 401 → 落雪 refresh 只调一次，
    后到者经锁内重读采用新凭据直接返回（不重放旧 rt）。
    回归保护：落库必须在锁内完成——先释放锁再落库时，save 的 commit 与
    后到者的重读走不同池化连接，全量压测下本测试偶发抓到双次续期。"""
    import asyncio

    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.config import plugin_config
    from nonebot_plugin_awmc_helper.core.ext import lxns as lxns_ext
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    monkeypatch.setattr(plugin_config, "awmc_lxns_client_id", "cid")
    monkeypatch.setattr(plugin_config, "awmc_lxns_client_secret", "sec")
    monkeypatch.setattr(plugin_config, "awmc_lxns_redirect_uri", "http://localhost/cb")

    binding = await binding_service.ensure("OneBot V11", "30007")
    await binding_service.bind_lxns(binding, token="old-token", friend_code=123)
    binding.lxns_refresh_token = "rt-old"
    await store.save_binding(binding)

    calls = []

    async def fake_refresh(rt):
        calls.append(rt)
        await asyncio.sleep(0.05)  # 制造并发窗口

        class _T:
            access_token = "new-token"
            refresh_token = "rt-new"
            friend_code = 123

        return _T()

    monkeypatch.setattr(lxns_ext, "refresh_token", fake_refresh)

    b1 = await binding_service.get("OneBot V11", "30007")
    b2 = await binding_service.get("OneBot V11", "30007")
    assert b1 is not None
    assert b2 is not None
    results = await asyncio.gather(
        binding_service.refresh_lxns(b1),
        binding_service.refresh_lxns(b2),
    )
    assert results == ["refreshed", "refreshed"]
    assert calls == ["rt-old"]  # 落雪只被消耗一次
    assert b1.lxns_token == "new-token"
    assert b2.lxns_token == "new-token"  # 后到者经锁内重读采用新凭据


@pytest.mark.asyncio
async def test_divingfish_identifier_shapes(db):
    """水鱼凭据装配形状（2026-09-26 at 代查定案）：

    - 公开键（identifier，bests/players/minfo）：绑定用户名优先且不带 qq——
      maimai_py 的 ``_as_diving_fish`` 中 qq 优先，同时携带会在
      「聊天 QQ ≠ 水鱼账号 QQ」时查错账号；
    - 全量键（full_identifier，scores/plates）：Import-Token 优先且不带
      username——maimai_py 把 username+credentials 当「账号+密码」登录水鱼；
    - 仅 Import-Token：公开键缺失（非 QQ 平台给专项文案），全量键
      credentials-only（对齐 score-updater 的用法），has_usable 仍为可用。
    """
    from nonebot_plugin_awmc_helper.core.store import UserBinding
    from nonebot_plugin_awmc_helper.core.binding import (
        SERVICE_DIVINGFISH,
        BindingError,
        binding_service,
    )

    # 用户名绑定：公开键 = 用户名（不带 qq）
    b = UserBinding(
        platform="OneBot V11",
        user_id="10001",
        service=SERVICE_DIVINGFISH,
        divingfish_username="fish",
    )
    ident = binding_service.identifier(b)
    assert ident.username == "fish"
    assert ident.qq is None
    assert ident.credentials is None

    # 用户名 + Import-Token 并存：公开键仍为用户名；全量键走 Import-Token
    b.divingfish_import_token = "tok"
    ident_full = binding_service.full_identifier(b)
    assert ident_full.credentials == "tok"
    assert ident_full.username is None

    # 仅 Import-Token（非 QQ 平台）：公开键缺失 → 专项文案；全量可查
    b2 = UserBinding(
        platform="telegram",
        user_id="u1",
        service=SERVICE_DIVINGFISH,
        divingfish_import_token="tok",
    )
    with pytest.raises(BindingError, match="用户名或 QQ"):
        binding_service.identifier(b2)
    full = binding_service.full_identifier(b2)
    assert full.credentials == "tok"
    assert full.qq is None
    assert binding_service.has_usable_credentials(b2)

    # 无任何凭据（QQ 平台）：公开键 = QQ 兜底
    b3 = UserBinding(platform="OneBot V11", user_id="10002", service=SERVICE_DIVINGFISH)
    assert binding_service.identifier(b3).qq == 10002


@pytest.mark.asyncio
async def test_resolve_query_at_chain(db):
    """代查目标解析链：无 at → ensure 发送者；有 at → 目标行只读 →
    QQ 平台水鱼临时绑定（不落库）→ 非 QQ 平台 None（调用方降级）。"""
    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.core.binding import (
        SERVICE_DIVINGFISH,
        binding_service,
    )

    # 无 at：发送者 ensure（落库，对齐 auto_create）
    b = await binding_service.resolve_query("OneBot V11", "10001", None)
    assert b is not None
    assert b.user_id == "10001"
    assert await store.get_binding("OneBot V11", "10001") is not None

    # at 有行：只读复用目标行，不新建
    await binding_service.bind_divingfish_username(b, "fishuser")
    b2 = await binding_service.resolve_query("OneBot V11", "10002", "10001")
    assert b2 is not None
    assert b2.divingfish_username == "fishuser"
    assert await store.get_binding("OneBot V11", "10002") is None  # 发送者未落库

    # at 无行（QQ 平台）：临时水鱼绑定，查询后不落库
    b3 = await binding_service.resolve_query("OneBot V11", "10002", "10003")
    assert b3 is not None
    assert b3.service == SERVICE_DIVINGFISH
    assert b3.user_id == "10003"
    assert await store.get_binding("OneBot V11", "10003") is None

    # at 无行（非 QQ 平台）：None → 由调用方给出「无法代查」降级
    assert await binding_service.resolve_query("telegram", "u1", "u2") is None


@pytest.mark.asyncio
async def test_resolve_query_binding_gate(db, monkeypatch):
    """resolve_query_binding 门禁（非 QQ 平台可达分支）：at 目标未绑定 →
    「对方」提示；自身未绑定 → 绑定引导。QQ 平台自身未绑定凭 QQ 可查
    （ensure 默认水鱼行，Hoshino 对齐语义），不经本门禁。"""
    from types import SimpleNamespace

    from nonebot.exception import FinishedException
    from nonebot.adapters.onebot.v11 import Message, MessageSegment

    from nonebot_plugin_awmc_helper.core.binding import resolve_query_binding

    async def fake_finish(self, *a, **kw):
        raise FinishedException(str(self))

    monkeypatch.setattr("nonebot_plugin_alconna.uniseg.UniMessage.finish", fake_finish)
    # 非 QQ 平台（platform 无 QQ 语义、无适配器回退）
    session = SimpleNamespace(platform="telegram", user=SimpleNamespace(id="42"))

    with pytest.raises(FinishedException) as ei:
        await resolve_query_binding(session, None)  # pyright: ignore[reportArgumentType]
    assert "尚未绑定查分器，请先使用「绑定水鱼」" in str(ei.value)

    event = SimpleNamespace(message=Message([MessageSegment.at(777)]))
    with pytest.raises(FinishedException) as ei:
        await resolve_query_binding(session, event)  # pyright: ignore[reportArgumentType]
    assert "对方尚未绑定查分器，无法代查" in str(ei.value)


@pytest.mark.asyncio
async def test_divingfish_subject_frozen_ref(db, monkeypatch):
    """subject 冻结语义（2026-11-06，divingfish_ref 列）：

    - consent 未建立（ref 为空）：按「用户名 > QQ」派生，行为同前；
    - ``bind_divingfish_oauth`` 冻结**时点**摘要；此后改用户名不牵动
      subject（ref 一经 consent 即不可变，修正公开键与 OAuth 身份解耦）；
    - 冻结值优先于任何现算（OAuth 未配置也能返回已冻结的 ref）。
    """
    import hashlib

    from nonebot_plugin_awmc_helper.config import plugin_config
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    monkeypatch.setattr(plugin_config, "awmc_divingfish_oauth_client_id", "cid")
    monkeypatch.setattr(plugin_config, "awmc_divingfish_oauth_client_secret", "sec")

    binding = await binding_service.ensure("OneBot V11", "10001")
    await binding_service.bind_divingfish_username(binding, "bogus")
    derived = "ref:" + hashlib.sha256(b"cid:bogus").hexdigest()
    assert binding_service.divingfish_subject(binding) == derived

    # OAuth 完成：冻结时点摘要（无效用户名摘要）；随后修正用户名不牵动
    await binding_service.bind_divingfish_oauth(binding, sub="42")
    got = await binding_service.get("OneBot V11", "10001")
    assert got is not None
    assert got.divingfish_ref == derived
    await binding_service.bind_divingfish_username(got, "real")
    assert binding_service.divingfish_subject(got) == derived

    # 冻结值优先于现算：即便 OAuth 凭据未配置也原样返回
    monkeypatch.setattr(plugin_config, "awmc_divingfish_oauth_client_id", None)
    monkeypatch.setattr(plugin_config, "awmc_divingfish_oauth_client_secret", None)
    assert binding_service.divingfish_subject(got) == derived


@pytest.mark.asyncio
async def test_bind_username_freezes_legacy_oauth_row(db, monkeypatch):
    """冻结上线前完成授权的存量行（divingfish_ref 为空）：「绑定水鱼用户名」
    必须先冻结当前派生（QQ 摘要 = consent 当时所用）再改名，否则 subject
    随用户名漂移、换票 consent_required（2026-11-06 补缺口）。"""
    import hashlib

    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.config import plugin_config
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    monkeypatch.setattr(plugin_config, "awmc_divingfish_oauth_client_id", "cid")
    monkeypatch.setattr(plugin_config, "awmc_divingfish_oauth_client_secret", "sec")

    qq_ref = "ref:" + hashlib.sha256(b"cid:30001").hexdigest()
    legacy = await binding_service.ensure("OneBot V11", "30001")
    legacy.divingfish_oauth = True  # 存量行：已授权、无冻结值
    await store.save_binding(legacy)
    assert legacy.divingfish_ref is None
    assert binding_service.divingfish_subject(legacy) == qq_ref

    await binding_service.bind_divingfish_username(legacy, "fishuser")
    got = await store.get_binding("OneBot V11", "30001")
    assert got is not None
    assert got.divingfish_username == "fishuser"
    assert got.divingfish_ref == qq_ref
    assert binding_service.divingfish_subject(got) == qq_ref

    # 未授权行改名不冻结（无 consent 可保，派生如旧随用户名走）
    plain = await binding_service.ensure("OneBot V11", "30002")
    await binding_service.bind_divingfish_username(plain, "other")
    assert plain.divingfish_ref is None
    assert binding_service.divingfish_subject(plain) == (
        "ref:" + hashlib.sha256(b"cid:other").hexdigest()
    )


@pytest.mark.asyncio
async def test_sync_divingfish_profile(db, monkeypatch):
    """授权完成后的本人资料同步（2026-11-06）：用户名以授权账号为准——
    缺失记下（learned）、不一致改为授权账号的（updated，含误绑查无此人）、
    一致不动（unchanged）；未同意水鱼用户协议单列（agreement_required），
    其余读取失败静默（unavailable）；改名前先冻结 subject。"""
    import hashlib

    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.config import plugin_config
    from nonebot_plugin_awmc_helper.core.binding import binding_service
    from nonebot_plugin_awmc_helper.core.ext.divingfish import (
        ExtError,
        DivingFishAgreementRequired,
    )

    monkeypatch.setattr(plugin_config, "awmc_divingfish_oauth_client_id", "cid")
    monkeypatch.setattr(plugin_config, "awmc_divingfish_oauth_client_secret", "sec")

    outcome: dict = {"profile": {"username": "real_name"}}
    tokens: list[str] = []

    async def fake_fetch(access_token: str) -> dict:
        tokens.append(access_token)
        if isinstance(outcome.get("raise"), Exception):
            raise outcome["raise"]
        return outcome["profile"]

    monkeypatch.setattr(
        "nonebot_plugin_awmc_helper.core.ext.divingfish.fetch_oauth_profile",
        fake_fetch,
    )
    qq_ref = "ref:" + hashlib.sha256(b"cid:40001").hexdigest()

    # 无用户名：记下授权账号用户名，subject 冻结在 QQ 摘要
    b = await binding_service.ensure("OneBot V11", "40001")
    await binding_service.bind_divingfish_oauth(b, sub="1")
    sync = await binding_service.sync_divingfish_profile(b, "tok-1")
    assert (sync.status, sync.old, sync.new) == ("learned", None, "real_name")
    assert tokens == ["tok-1"]  # 用的是本次兑换令牌，不另换票
    got = await store.get_binding("OneBot V11", "40001")
    assert got is not None
    assert got.divingfish_username == "real_name"
    assert binding_service.divingfish_subject(got) == qq_ref

    # 一致：不落库
    sync = await binding_service.sync_divingfish_profile(got, "tok-2")
    assert sync.status == "unchanged"

    # 不一致（误绑/他人用户名）：以授权账号为准，subject 仍冻结
    await binding_service.bind_divingfish_username(got, "bogus")
    sync = await binding_service.sync_divingfish_profile(got, "tok-3")
    assert (sync.status, sync.old, sync.new) == ("updated", "bogus", "real_name")
    assert got.divingfish_username == "real_name"
    assert binding_service.divingfish_subject(got) == qq_ref

    # 未同意水鱼用户协议：单列状态，绑定不动
    outcome["raise"] = DivingFishAgreementRequired("该用户未同意用户协议")
    sync = await binding_service.sync_divingfish_profile(got, "tok-4")
    assert sync.status == "agreement_required"
    assert got.divingfish_username == "real_name"

    # 其余读取失败：静默 unavailable
    outcome["raise"] = ExtError("水鱼查分器网络异常，请稍后再试")
    sync = await binding_service.sync_divingfish_profile(got, "tok-5")
    assert sync.status == "unavailable"

    # 资料缺用户名：同按 unavailable
    outcome.pop("raise")
    outcome["profile"] = {"username": None}
    sync = await binding_service.sync_divingfish_profile(got, "tok-6")
    assert sync.status == "unavailable"
