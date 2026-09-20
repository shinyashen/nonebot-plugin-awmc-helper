"""core/ext/yuzu：柚子 REST 客户端与 SSE 解析测试（respx mock）。"""

import json
import asyncio

import respx
import pytest

BASE = "https://www.yuzuchan.moe/api/v2"


@pytest.fixture
def yuzu_mock():
    with respx.mock(assert_all_called=False) as mock:
        yield mock


@pytest.fixture
def yuzu_ext():
    from nonebot_plugin_awmc_helper.core.ext import yuzu as mod

    return mod


@pytest.mark.asyncio
async def test_get_status(yuzu_mock, yuzu_ext):
    yuzu_mock.get(f"{BASE}/aliases/maimaidx/votes").respond(
        json=[
            {
                "song_id": 231,
                "apply_alias": "企鹅",
                "tag": "ABC123",
                "name": "PENGUIN",
                "created_at": "2026-09-21",
                "agree_votes": 3,
                "votes": 10,
            }
        ]
    )
    votes = await yuzu_ext.yuzu_client.get_status()
    assert len(votes) == 1
    assert votes[0].song_id == 231
    assert votes[0].tag == "ABC123"
    assert votes[0].agree_votes == 3


@pytest.mark.asyncio
async def test_get_alias(yuzu_mock, yuzu_ext):
    yuzu_mock.get(f"{BASE}/aliases/maimaidx/aliases").respond(
        json={
            "song_id": 231,
            "name": "PENGUIN",
            "is_votable": True,
            "alias": ["penguin"],
        }
    )
    alias = await yuzu_ext.yuzu_client.get_alias(231)
    assert alias is not None
    assert alias.has("PENGUIN")  # 大小写不敏感
    assert not alias.has("企鹅")

    # MessageResult 形态 → None
    yuzu_mock.get(f"{BASE}/aliases/maimaidx/aliases").respond(
        json={"message": "未找到"}
    )
    assert await yuzu_ext.yuzu_client.get_alias(999) is None


@pytest.mark.asyncio
async def test_get_apply_songs(yuzu_mock, yuzu_ext):
    yuzu_mock.get(f"{BASE}/aliases/maimaidx/songs").respond(
        json={
            "type": "ongoing",
            "data": [
                {
                    "song_id": 500,
                    "apply_alias": "普瑞",
                    "tag": "TAG001",
                    "name": "Preferences",
                    "agree_votes": 1,
                    "votes": 5,
                }
            ],
        }
    )
    found = await yuzu_ext.yuzu_client.get_apply_songs("普瑞")
    assert found is not None
    assert found.votes
    assert found.votes[0].tag == "TAG001"

    yuzu_mock.get(f"{BASE}/aliases/maimaidx/songs").respond(json={"message": "未找到"})
    assert await yuzu_ext.yuzu_client.get_apply_songs("不存在") is None


@pytest.mark.asyncio
async def test_apply_and_agree(yuzu_mock, yuzu_ext):
    apply_route = yuzu_mock.post(f"{BASE}/aliases/maimaidx/apply").respond(
        json={"message": "申请已提交"}
    )
    msg = await yuzu_ext.yuzu_client.apply_alias(231, "企鹅", "10001", "20001")
    assert msg == "申请已提交"
    import json as j

    sent = j.loads(apply_route.calls.last.request.content)
    assert sent["song_id"] == 231
    assert sent["apply_alias"] == "企鹅"
    assert sent["apply_uid"] == "10001"
    assert sent["group_id"] == "20001"

    vote_route = yuzu_mock.post(f"{BASE}/aliases/maimaidx/votes").respond(
        json={"message": "投票成功"}
    )
    msg = await yuzu_ext.yuzu_client.agree_alias("ABC123", "10001")
    assert msg == "投票成功"
    sent = json.loads(vote_route.calls.last.request.content)
    assert sent == {"tag": "ABC123", "agree_user": "10001"}


@pytest.mark.asyncio
async def test_error_mapping(yuzu_mock, yuzu_ext):
    yuzu_mock.post(f"{BASE}/aliases/maimaidx/apply").respond(
        status_code=400, json={"message": "别名已存在"}
    )
    from nonebot_plugin_awmc_helper.core.ext import ExtError

    with pytest.raises(ExtError, match="别名已存在"):
        await yuzu_ext.yuzu_client.apply_alias(231, "x", "1", "1")


def test_iter_sse_parsing(yuzu_ext):
    """SSE 行流解析：event/data/id/retry、多行 data、注释与空行。"""

    async def gen():
        for line in [
            "event: alias",
            "id: 42",
            'data: {"type": "Apply"}',
            "retry: 5000",
            "",
            ": comment",
            "data: first",
            "data: second",
            "",
        ]:
            yield line

    async def collect():
        return [m async for m in yuzu_ext.iter_sse(gen())]

    msgs = asyncio.run(collect())
    assert len(msgs) == 2
    assert msgs[0].event == "alias"
    assert msgs[0].id == "42"
    assert msgs[0].retry == 5000
    assert json.loads(msgs[0].data) == {"type": "Apply"}
    assert msgs[1].data == "first\nsecond"
    assert msgs[1].event == "message"


@pytest.mark.asyncio
async def test_sse_runner_pushes_apply(yuzu_mock, yuzu_ext):
    """SSE 常驻协程：收到 alias/Apply 事件触发回调。"""
    sse_payload = (
        'event: alias\nid: 1\ndata: {"type": "Apply", "status": '
        '[{"song_id": 231, "apply_alias": "企鹅", "tag": "T1",'
        ' "agree_votes": 0, "votes": 10}]}\n\n'
    )
    yuzu_mock.get(f"{BASE}/events").respond(
        200, headers={"content-type": "text/event-stream"}, content=sse_payload
    )

    received: list[yuzu_ext.AliasPush] = []

    async def on_apply(push: yuzu_ext.AliasPush) -> None:
        received.append(push)
        yuzu_ext.stop_alias_push()  # 收到后结束任务

    yuzu_ext.start_alias_push(on_apply)
    task = yuzu_ext._push_task
    assert task is not None
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=5)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass
    assert received
    assert received[0].status[0].song_id == 231
