"""core/ext/net：日服 NET 直连抓取测试（respx mock + 解析器单测）。

HTML fixture 按 dxrating 解析器（parseMusicRecordNode）的选择器语义构造
最小页面，真实页面联调后如有出入以实测为准（调研笔记 dxrating-net-notes §6）。
"""

import respx
import pytest

BASE = "https://maimaidx.jp"
MOBILE = f"{BASE}/maimai-mobile"

LOGIN_PAGE = (
    "<html><body><form>"
    '<input type="hidden" name="token" value="tok123">'
    "</form></body></html>"
)

# 首页身份块最小样本（2026-09-27 服务器实测 .basic_block 结构：头像/称号/玩家名/rating）
HOME_PAGE = """
<html><body><div class="basic_block p_10 f_0">
  <img class="w_112 f_l" src="/maimai-mobile/img/Icon/34f0363f4ce86d07.png">
  <div class="p_l_10 f_l">
    <div class="trophy_block trophy_Normal p_3 t_c f_0">
      <div class="trophy_inner_block f_13"><span>アウラ、フルコンしろ。</span></div>
    </div>
    <div class="m_b_5">
      <div class="name_block f_l f_16">ｃｄｄ</div>
      <div class="f_r t_r f_0">
        <div class="p_r p_3">
          <img class="h_30 f_r" src="/maimai-mobile/img/rating_base_purple.png">
          <div class="rating_block">15828</div>
        </div>
      </div>
      <div class="clearfix"></div>
    </div>
    <img class="user_data_block_line" src="/maimai-mobile/img/line_01.png">
    <img class="h_35 f_l" src="/maimai-mobile/img/course/course_rank_10hvsSHd90.png">
    <img class="p_l_10 h_35 f_l" src="/maimai-mobile/img/class/class_rank_s_00.png">
    <div class="p_l_10 f_l f_14">
      <img class="h_30 m_3 v_m" src="/maimai-mobile/img/icon_star.png">
      ×3
    </div>
  </div>
</div></body></html>
"""


# 记录页最小样本：DX 块（达成率/DX分/AP+徽章）+ SD 块（toggle 覆盖/FSD+ 徽章）+ 坏块
# 收藏品姓名框页最小样本（betterDXnet 同口径）：装备中项带 collection_setting_block
NAMEPLATE_PAGE = """
<html><body><div class="see_through_area">
<div class="town_block" name="genre_1">
<div class="see_through_block collection_setting_block p_r m_t_10 p_10 f_0">
  <img class="w_396" src="/maimai-mobile/img/line_01.png">
  <div class="p_r"><img class="w_396 m_r_10" src="/maimai-mobile/img/NamePlate/a.png">
  </div>
  <img class="collection_setting_img" src="/maimai-mobile/img/collection_setting.png">
  <div class="block_info">装备中名牌</div>
</div>
<div class="see_through_block">
  <div class="p_r"><img class="w_396 m_r_10 gray_img"
   src="/maimai-mobile/img/NamePlate/b.png"></div>
  <div class="block_info">未装备</div>
</div>
</div></div></body></html>
"""

# 收藏品姓名框页：装备「デフォルト」框的真实形态（2026-09-28 shinya 账号实测，
# 预览图哈希为该账号原文）——block_info 是分组名、项名才是判定依据
NAMEPLATE_DEFAULT_PAGE = """
<html><body>
<div class="town_block m_15 p_15 t_l">
<div class="t_c f_15 f_b">― 設定中のネームプレート ―</div>
<div class="see_through_block collection_setting_block p_r m_t_10 p_10 f_0">
  <div class="block_info f_11 orange">デフォルト</div>
  <div class="p_5 f_14 break">デフォルト</div>
  <img class="w_396" src="/maimai-mobile/img/line_01.png">
  <div class="p_l_5 f_12 gray break">はじめから所持</div>
  <div class="p_r"><img class="w_396 m_r_10"
   src="/maimai-mobile/img/NamePlate/b919c327669240b8.png"></div>
</div>
</div>
</body></html>
"""


# 记录页为 dxrating 版式最小构造（真实记录页含个人成绩，不入库，见 AGENTS.md
# 规则 5 与测试数据方针）；曲目锚定为真实曲（物語はここから/True Love Song），
# 达成率等成绩数值按「真实曲目 + 合理值」口径。
RECORD_PAGE = """
<html><body><div class="wrapper">
<form id="f1"><div class="w_450 m_15 p_r f_0">
  <img class="music_kind_icon" src="/maimai-mobile/img/Music/music_dx.png">
  <div class="music_name_block">物語はここから</div>
  <img class="h_20 f_l" src="/maimai-mobile/img/diff_master.png">
  <div class="music_score_block w_112">100.5000%</div>
  <div class="music_score_block w_190">1,234,567 / 2,000,000</div>
  <form><img class="f_r" src="/maimai-mobile/img/Music/applus.png"></form>
</div></form>
<form id="f2"><div class="w_450 m_15 p_r f_0">
  <img class="music_kind_icon" src="/maimai-mobile/img/Music/music_dx.png">
  <div class="music_kind_icon_standard _btn_on"></div>
  <div class="music_name_block">True Love Song</div>
  <img class="h_20 f_l" src="/maimai-mobile/img/diff_expert.png">
  <div class="music_score_block w_112">99.1234%</div>
  <div class="music_score_block w_190">500,000 / 1,000,000</div>
  <form><img class="f_r" src="/maimai-mobile/img/Music/fsd.png"></form>
</div></form>
<form id="f3"><div class="w_450 m_15 p_r f_0">
  <div class="music_name_block">坏块（无达成率）</div>
</div></form>
</div></body></html>
"""


@pytest.fixture
def net_mock():
    with respx.mock(assert_all_called=False) as mock:
        yield mock


@pytest.fixture
def net_ext():
    from nonebot_plugin_awmc_helper.core.ext import net as mod

    return mod


def _mock_login_flow(m, *, login_page=LOGIN_PAGE, login_redirect=None, home_page="ok"):
    m.get(f"{MOBILE}/").respond(text=login_page)
    m.post(f"{MOBILE}/submit/").respond(
        302, headers={"location": login_redirect or f"{MOBILE}/home"}
    )
    m.get(f"{MOBILE}/aimeList/").respond(text="ok")
    m.get(f"{MOBILE}/aimeList/submit/", params={"idx": "0"}).respond(text="ok")
    m.get(f"{MOBILE}/home/").respond(text=home_page)
    m.get(f"{MOBILE}/collection/nameplate").respond(302)


def _mock_record_pages(m, *, page=RECORD_PAGE):
    for d in range(5):
        m.get(
            f"{MOBILE}/record/musicGenre/search/",
            params={"genre": "99", "diff": str(d)},
        ).respond(text=page)


@pytest.mark.asyncio
async def test_parse_record_block(net_ext):
    """块解析：达成率万分位还原、DX 分逗号剥离、FC/FS 徽章语义。"""
    records = net_ext._parse_music_records(RECORD_PAGE)
    assert len(records) == 2  # 坏块（无达成率）被跳过

    dx = records[0]
    assert dx.title == "物語はここから"
    assert dx.type == "dx"
    assert dx.difficulty == "master"
    assert dx.achievement == 100.5
    assert dx.dx_score == 1234567
    assert dx.dx_score_total == 2000000
    assert dx.fc == "app"
    assert dx.fs is None

    sd = records[1]
    # toggle 按钮 _btn_on 覆盖图标推断：standard 优先
    assert sd.type == "standard"
    assert sd.difficulty == "expert"
    assert sd.achievement == 99.1234
    assert sd.fc is None
    assert sd.fs == "fsd"


@pytest.mark.asyncio
async def test_login_and_fetch(net_mock, net_ext):
    """登录成功 → 选卡 → 逐难度抓 5 页记录。"""
    _mock_login_flow(net_mock)
    _mock_record_pages(net_mock)
    client = net_ext.MaimaiNetClient()
    try:
        await client.login(net_ext.NetCredentials(sega_id="sid", password="pw"))
        records = await client.fetch_music_records()
    finally:
        await client.aclose()
    assert len(records) == 10  # 5 页 × 2 有效块
    assert {r.title for r in records} == {"物語はここから", "True Love Song"}


@pytest.mark.asyncio
async def test_login_invalid_credentials(net_mock, net_ext):
    """302 → error 页 = 凭据错误。"""
    _mock_login_flow(net_mock, login_redirect=f"{MOBILE}/error/?code=701")
    client = net_ext.MaimaiNetClient()
    try:
        with pytest.raises(net_ext.NetError) as ei:
            await client.login(net_ext.NetCredentials(sega_id="sid", password="bad"))
        assert ei.value.code == "invalid_credentials"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_login_maintenance(net_mock, net_ext):
    """登录页出现维护文案 → maintenance。"""
    _mock_login_flow(net_mock, login_page=f"定期メンテナンス中です{LOGIN_PAGE}")
    client = net_ext.MaimaiNetClient()
    try:
        with pytest.raises(net_ext.NetError) as ei:
            await client.login(net_ext.NetCredentials(sega_id="sid", password="pw"))
        assert ei.value.code == "maintenance"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_login_token_error(net_mock, net_ext):
    """登录页无 token（页面改版/风控）→ token_error。"""
    _mock_login_flow(net_mock, login_page="<html><body>no token</body></html>")
    client = net_ext.MaimaiNetClient()
    try:
        with pytest.raises(net_ext.NetError) as ei:
            await client.login(net_ext.NetCredentials(sega_id="sid", password="pw"))
        assert ei.value.code == "token_error"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_fetch_parse_error_on_empty_page(net_mock, net_ext):
    """登录态下记录页解析不到任何块 → parse_error（页面改版信号）。"""
    _mock_login_flow(net_mock)
    for d in range(5):
        net_mock.get(
            f"{MOBILE}/record/musicGenre/search/",
            params={"genre": "99", "diff": str(d)},
        ).respond(text="<html><body></body></html>")
    client = net_ext.MaimaiNetClient()
    try:
        await client.login(net_ext.NetCredentials(sega_id="sid", password="pw"))
        with pytest.raises(net_ext.NetError) as ei:
            await client.fetch_music_records()
        assert ei.value.code == "parse_error"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_achievement_out_of_range_dropped(net_ext):
    """达成率越界（>101）的块整条丢弃（dxrating validateAchievement 同语义）。"""
    page = RECORD_PAGE.replace("100.5000%", "200.0000%")
    records = net_ext._parse_music_records(page)
    assert [r.title for r in records] == ["True Love Song"]


def test_parse_player_identity(net_ext):
    """首页身份块解析：玩家名/官方 rating/头像 URL/称号与稀有度。

    fixture 头像用的是デフォルト头像哈希（真实页面原文），判定为 None；
    自定义头像走 test_parse_player_custom_icon。
    """
    player = net_ext._parse_player(HOME_PAGE)
    assert player is not None
    assert player.name == "\uff43\uff44\uff44"
    assert player.rating == 15828
    assert player.icon_url is None  # デフォルト头像 → None（渲染层落 QQ 头像）
    assert player.trophy_name == "アウラ、フルコンしろ。"
    assert player.trophy_color == "Normal"
    assert player.course_url == f"{MOBILE}/img/course/course_rank_10hvsSHd90.png"
    assert player.class_url == f"{MOBILE}/img/class/class_rank_s_00.png"


def test_parse_player_custom_icon(net_ext):
    """自定义头像（非デフォルト哈希）→ URL 原样下发。"""
    custom = HOME_PAGE.replace("Icon/34f0363f4ce86d07.png", "Icon/cafe12cafe12cafe.png")
    player = net_ext._parse_player(custom)
    assert player is not None
    assert player.icon_url == f"{MOBILE}/img/Icon/cafe12cafe12cafe.png"


def test_parse_player_absent(net_ext):
    """无身份块/关键字段缺失 → None（不阻塞成绩组装）。"""
    assert net_ext._parse_player("<html><body></body></html>") is None
    no_rating = HOME_PAGE.replace('<div class="rating_block">15828</div>', "")
    assert net_ext._parse_player(no_rating) is None


@pytest.mark.asyncio
async def test_login_carries_player_identity(net_mock, net_ext):
    """登录流最后一跳 home/ 顺带解析身份（client.player）。"""
    _mock_login_flow(net_mock, login_page=LOGIN_PAGE, home_page=HOME_PAGE)
    client = net_ext.MaimaiNetClient()
    try:
        await client.login(net_ext.NetCredentials(sega_id="sid", password="pw"))
        assert client.player is not None
        assert client.player.name == "\uff43\uff44\uff44"
    finally:
        await client.aclose()


def test_parse_equipped_nameplate(net_ext):
    """收藏品页装备中项：w_396.m_r_10 预览图（第一张 w_396 是装饰线，勿取）。"""
    parsed = net_ext._parse_equipped_nameplate(NAMEPLATE_PAGE)
    assert parsed.url == f"{MOBILE}/img/NamePlate/a.png"
    assert parsed.is_default is False
    assert "line_01" not in (parsed.url or "")
    # 无装备块 → 未知态（url None 且 is_default False，区别于确认默认框）
    bare = NAMEPLATE_PAGE.replace(" collection_setting_block", "")
    assert net_ext._parse_equipped_nameplate(bare) == (None, False)
    assert net_ext._parse_equipped_nameplate("<html></html>") == (None, False)


def test_parse_equipped_nameplate_default(net_ext):
    """装备「デフォルト」框 → is_default 态（卡面落水鱼缺省牌，不贴官方素色框）。

    真实形态（2026-09-28 实测）：block_info 是分组名（デフォルト 分组下所有项
    同名），判定默认框只能看项名 .p_5.f_14.break。
    """
    parsed = net_ext._parse_equipped_nameplate(NAMEPLATE_DEFAULT_PAGE)
    assert parsed.url is None
    assert parsed.is_default is True


@pytest.mark.asyncio
async def test_login_fetches_equipped_nameplate(net_mock, net_ext):
    """登录流顺带抓装备名牌：200 → URL；302（收藏品区弹回）→ None。"""
    _mock_login_flow(net_mock, home_page=HOME_PAGE)
    net_mock.get(f"{MOBILE}/collection/nameplate").respond(text=NAMEPLATE_PAGE)
    client = net_ext.MaimaiNetClient()
    try:
        await client.login(net_ext.NetCredentials(sega_id="sid", password="pw"))
        assert client.player is not None
        assert client.player.nameplate_url == f"{MOBILE}/img/NamePlate/a.png"
        assert client.player.nameplate_is_default is False
    finally:
        await client.aclose()

    _mock_login_flow(net_mock, home_page=HOME_PAGE)  # nameplate 默认 302
    client = net_ext.MaimaiNetClient()
    try:
        await client.login(net_ext.NetCredentials(sega_id="sid", password="pw"))
        assert client.player is not None
        assert client.player.nameplate_url is None
        assert client.player.nameplate_is_default is False
    finally:
        await client.aclose()

    # 装备的是「デフォルト」框：确认态落到 player（is_default=True）
    _mock_login_flow(net_mock, home_page=HOME_PAGE)
    net_mock.get(f"{MOBILE}/collection/nameplate").respond(text=NAMEPLATE_DEFAULT_PAGE)
    client = net_ext.MaimaiNetClient()
    try:
        await client.login(net_ext.NetCredentials(sega_id="sid", password="pw"))
        assert client.player is not None
        assert client.player.nameplate_url is None
        assert client.player.nameplate_is_default is True
    finally:
        await client.aclose()
