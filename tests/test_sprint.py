import json
import wave
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from tongxi.analysis import fingerprint
from tongxi.asr_bridge import CaptionAssembler
from tongxi.main import create_app


def headers(session, key=None):
    return {"Authorization": "Bearer " + session["token"], "Idempotency-Key": key or str(uuid4())}


def call(client, session, path, data=None, method="POST", status=200):
    response = client.request(method, f'/api/v1/rooms/{session["room"]}{path}', json=data, headers=headers(session))
    assert response.status_code == status, response.text
    return response.json()


@pytest.fixture
def env():
    app = create_app(auto_ai=False)
    with TestClient(app) as client:
        yield client, app.state.store


def host_room(env):
    client, store = env
    host = client.post("/api/v1/rooms", json={"title": "视觉接入", "name": "主持"}).json()
    actor = store.auth(host["token"], host["room"])
    return client, store, host, actor


def test_online_caption_is_replaced_in_place_and_stays_unstable(env):
    client, store, host, actor = host_room(env)
    store.apply(host["room"], lambda room: __import__("tongxi.domain", fromlist=["live_caption"]).live_caption(room, actor, "我觉得方案a可以暂是", False))
    room = store.read(host["room"])
    assert room["segments"][-1]["stable"] is False
    assert room["segments"][-1]["rev"] == 1
    assert fingerprint(room) == []
    store.apply(host["room"], lambda room: __import__("tongxi.domain", fromlist=["live_caption"]).live_caption(
        room, actor, "我觉得方案a可以，但是方案b周六不能借到实验室", True))
    room = store.read(host["room"])
    segment = room["segments"][-1]
    assert segment["stable"] is True
    assert segment["rev"] == 1
    assert segment["revisions"] == []
    assert "但是" in segment["text"]
    assert fingerprint(room) == [[segment["id"], 1]]
    revised = call(client, host, f'/segments/{segment["id"]}', {"version": 1, "text": "我觉得方案A可以，但是方案B周六不能借到实验室。"}, "PATCH")
    assert revised["rev"] == 2
    assert revised["revisions"][0]["text"].startswith("我觉得方案a可以")


def test_assembler_keeps_online_partials_out_of_the_final_sentence():
    assembler = CaptionAssembler()
    first = assembler.feed({"mode": "2pass-online", "text": "暂是"})
    second = assembler.feed({"mode": "2pass-online", "text": "方案"})
    final = assembler.feed({"mode": "2pass-offline", "text": "但是方案B。"})
    nxt = assembler.feed({"mode": "2pass-online", "text": "下一句"})
    assert first == {"text": "暂是", "stable": False}
    assert second["stable"] is False and second["text"] == "暂是方案"
    assert final == {"text": "但是方案B。", "stable": True}
    assert nxt == {"text": "下一句", "stable": False}


def test_uncertain_signal_does_not_hold_and_confident_signal_holds_only_current_topic(env):
    client, store = env
    host = client.post("/api/v1/rooms", json={"title": "表达信号", "name": "主持"}).json()
    call(client, host, "/segments", {"text": "方案 B 需要实验室设备，周六可以使用实验室。"})
    call(client, host, "/segments", {"text": "人员分工下次再定。"})
    snap = call(client, host, "/snapshot", method="GET")
    first = call(client, host, "/decisions", {"title": "校园活动方案 B", "conclusion": "周六采用方案 B", "evidence": [{"segment": snap["segments"][0]["id"], "rev": 1}]})
    second = call(client, host, "/decisions", {"title": "人员分工", "conclusion": "下次再定", "evidence": [{"segment": snap["segments"][1]["id"], "rev": 1}]})
    member = client.post("/api/v1/join", json={"invite": snap["invite"], "name": "小林"}).json()
    call(client, member, "/me/consent", {"version": 1, "playback": False, "review": False, "camera": True, "intervene": True}, "PUT")
    actor = store.auth(member["token"], member["room"])
    store.connect(member["room"], actor)
    call(client, member, "/me/expression", {"kind": "unknown", "confident": False, "summary": "状态不确定"})
    quiet = call(client, host, "/snapshot", method="GET")
    assert quiet["requests"] == []
    assert quiet["decisions"][0]["status"] == "discussing"
    call(client, member, "/me/consent", {"version": 2, "playback": False, "review": False, "camera": True, "intervene": False}, "PUT")
    call(client, member, "/me/expression", {"kind": "speak", "confident": True, "summary": "可能准备发言"})
    visible = call(client, host, "/snapshot", method="GET")
    mine = call(client, member, "/snapshot", method="GET")
    assert visible["requests"] == []
    assert visible["notices"] == []
    assert all(person["expression"] is None for person in visible["members"])
    assert mine["me"]["expression"]["kind"] == "speak"
    call(client, member, "/me/consent", {"version": 3, "playback": False, "review": False, "camera": True, "intervene": True}, "PUT")
    store.connect(member["room"], store.auth(member["token"], member["room"]))
    call(client, host, f'/decisions/{first["id"]}/prepare', {"version": 1})
    call(client, member, "/me/expression", {"kind": "speak", "confident": True, "summary": "可能希望补充"})
    held = call(client, host, "/snapshot", method="GET")
    assert held["decisions"][0]["status"] == "preparing"
    assert held["decisions"][1]["id"] == second["id"]
    assert held["decisions"][1]["status"] == "discussing"
    assert held["requests"] == []
    assert len(held["notices"]) == 1
    notice_id = held["notices"][0]["id"]
    assert "member" not in held["notices"][0]
    assert "小林" not in json.dumps(held["notices"], ensure_ascii=False)
    call(client, member, "/me/expression", {"kind": "speak", "confident": True, "summary": "可能希望补充"})
    again = call(client, host, "/snapshot", method="GET")
    assert len(again["notices"]) == 1
    call(client, member, "/me/consent", {"version": 4, "playback": False, "review": False, "camera": True, "intervene": False}, "PUT")
    revoked = call(client, host, "/snapshot", method="GET")
    assert revoked["notices"] == []
    assert revoked["decisions"][0]["status"] == "preparing"
    call(client, host, f'/notices/{notice_id}', {"action": "confirm"}, status=409)


def test_disconnect_does_not_create_a_second_hold(env):
    client, store = env
    host = client.post("/api/v1/rooms", json={"title": "断线", "name": "主持"}).json()
    call(client, host, "/segments", {"text": "那就先选 B，接下来讨论人员分工。"})
    snap = call(client, host, "/snapshot", method="GET")
    decision = call(client, host, "/decisions", {"title": "方案 B", "conclusion": "先选 B", "evidence": [{"segment": snap["segments"][0]["id"], "rev": 1}]})
    member = client.post("/api/v1/join", json={"invite": snap["invite"], "name": "小陈"}).json()
    call(client, member, "/me/consent", {"version": 1, "playback": False, "review": False, "camera": True, "intervene": True}, "PUT")
    who = store.auth(member["token"], member["room"])
    epoch = store.connect(member["room"], who)
    call(client, host, f'/decisions/{decision["id"]}/prepare', {"version": 1})
    call(client, member, "/me/expression", {"kind": "wait", "confident": True, "summary": "可能需要等待"})
    store.disconnect(member["room"], who, epoch)
    call(client, member, "/me/expression", {"kind": "wait", "confident": True, "summary": "可能需要等待"})
    state = call(client, host, "/snapshot", method="GET")
    assert state["notices"] == []
    assert state["decisions"][0]["status"] == "preparing"
    assert state["members"][1]["expression"] is None
    board = client.get(f'/api/v1/rooms/{host["room"]}/snapshot', headers={"Authorization": "Bearer " + state["board_token"]}).json()
    assert "小陈" not in json.dumps(board, ensure_ascii=False)
    assert board["signals"] == 0


def test_live_funasr_offline_text_matches_the_sample_sentence():
    sample = Path(r"D:\challenge_AI\SameSeat\03_语音评测\ASR接入测试\cache\samples\asr_example_zh.wav")
    if not sample.is_file():
        pytest.skip("缺少已缓存的官方示例音频")
    from tongxi.adapters import funasr_ready
    if not funasr_ready():
        pytest.skip("FunASR 10095 未在监听")
    import asyncio
    import ssl
    import websockets
    from tongxi.adapters import funasr_target

    url, secure, _, _ = funasr_target()

    async def run():
        context = None
        if secure:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        assembler = CaptionAssembler()
        finals = []
        async with websockets.connect(url, ssl=context, subprotocols=["binary"], ping_interval=20, max_size=8_000_000) as remote:
            with wave.open(str(sample), "rb") as audio:
                pcm = audio.readframes(audio.getnframes())
                rate = audio.getframerate()
            await remote.send(json.dumps({
                "mode": "2pass", "chunk_size": [5, 10, 5], "chunk_interval": 10,
                "wav_name": "sprint", "is_speaking": True, "wav_format": "pcm", "audio_fs": rate, "itn": True,
            }))
            stride = 1920
            for start in range(0, len(pcm), stride):
                await remote.send(pcm[start:start + stride])
                await asyncio.sleep(0.06)
            await remote.send(json.dumps({"is_speaking": False}))
            try:
                while True:
                    raw = await asyncio.wait_for(remote.recv(), timeout=8)
                    if isinstance(raw, bytes):
                        continue
                    update = assembler.feed(json.loads(raw))
                    if update and update["stable"]:
                        finals.append(update["text"])
                        break
            except asyncio.TimeoutError:
                pass
        return finals

    finals = asyncio.run(run())
    assert finals and "达摩院" in finals[0]
    assert "暂" not in finals[0]
