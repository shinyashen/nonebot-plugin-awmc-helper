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

# 记录页最小样本：DX 块（达成率/DX分/AP+徽章）+ SD 块（toggle 覆盖/FSD+ 徽章）+ 坏块
RECORD_PAGE = """
<html><body><div class="wrapper">
<form id="f1"><div class="w_450 m_15 p_r f_0">
  <img class="music_kind_icon" src="/maimai-mobile/img/Music/music_dx.png">
  <div class="music_name_block">テスト曲</div>
  <img class="h_20 f_l" src="/maimai-mobile/img/diff_master.png">
  <div class="music_score_block w_112">100.5000%</div>
  <div class="music_score_block w_190">1,234,567 / 2,000,000</div>
  <form><img class="f_r" src="/maimai-mobile/img/Music/applus.png"></form>
</div></form>
<form id="f2"><div class="w_450 m_15 p_r f_0">
  <img class="music_kind_icon" src="/maimai-mobile/img/Music/music_dx.png">
  <div class="music_kind_icon_standard _btn_on"></div>
  <div class="music_name_block">標準曲</div>
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


def _mock_login_flow(m, *, login_page=LOGIN_PAGE, login_redirect=None):
    m.get(f"{MOBILE}/").respond(text=login_page)
    m.post(f"{MOBILE}/submit/").respond(
        302, headers={"location": login_redirect or f"{MOBILE}/home"}
    )
    m.get(f"{MOBILE}/aimeList/").respond(text="ok")
    m.get(f"{MOBILE}/aimeList/submit/", params={"idx": "0"}).respond(text="ok")
    m.get(f"{MOBILE}/home/").respond(text="ok")


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
    assert dx.title == "テスト曲"
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
    assert {r.title for r in records} == {"テスト曲", "標準曲"}


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
    assert [r.title for r in records] == ["標準曲"]
