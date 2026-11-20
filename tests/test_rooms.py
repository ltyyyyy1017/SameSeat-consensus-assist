import json
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from tongxi.main import create_app


@pytest.fixture
def env():
    app = create_app()
    with TestClient(app) as client:
        yield client, app.state.store


def headers(session, key=None):
    return {"Authorization": "Bearer " + session["token"], "Idempotency-Key": key or str(uuid4())}


def call(client, session, path, data=None, method="POST", key=None, status=200):
    response = client.request(method, f'/api/v1/rooms/{session["room"]}{path}', json=data, headers=headers(session, key))
    assert response.status_code == status, response.text
    return response.json()


def setup_room(env):
    client, store = env
    host = client.post('/api/v1/rooms', json={"title": "周末活动", "name": "主持"}).json()
    decision = call(client, host, '/demo', {})
    snap = call(client, host, '/snapshot', method="GET")
    members = [client.post('/api/v1/join', json={"invite": snap["invite"], "name": name}).json() for name in ("小林", "小陈")]
    return host, members, decision, snap


def progress(env, session, evidence, state="playing", seq=1, epoch=None, consent_version=2):
    client, store = env
    actor = store.auth(session["token"], session["room"])
    epoch = epoch or store.connect(session["room"], actor)
    return call(client, session, '/presentation', {"epoch": epoch, "seq": seq, "consent_version": consent_version,
                "evidence": evidence, "state": state, "instance": "audio-instance"})


def authorize(client, session):
    return call(client, session, '/me/consent', {"version": 1, "playback": True, "review": True}, "PUT")


def test_four_roles_and_actual_lifecycle(env):
    client, _ = env
    host, members, decision, original = setup_room(env)
    for m in members:
        authorize(client, m)
        progress(env, m, decision["evidence"])
    prepared = call(client, host, f'/decisions/{decision["id"]}/prepare', {"version": 1})
    assert prepared["status"] == "waiting"
    board = {"room": host["room"], "token": original["board_token"]}
    public = call(client, board, '/snapshot', method="GET")
    assert len(public["decisions"]) == 1
    assert not ({"members", "me", "requests", "segments", "revision", "contributions"} & public.keys())
    assert "小林" not in json.dumps(public, ensure_ascii=False)
    member_snapshot = call(client, members[1], '/snapshot', method="GET")
    call(client, members[1], f'/requests/{member_snapshot["requests"][0]["id"]}', {"action": "skip"}, "PATCH")
    opinion = call(client, members[0], '/contributions', {"text": "周六借不到设备，改到周日", "decision": decision["id"]})
    progress(env, members[0], [], state="finished")
    mine = call(client, members[0], '/snapshot', method="GET")
    assert mine["requests"][0]["status"] == "delivered", "播完仍应保留回应机会"
    result = call(client, host, f'/decisions/{decision["id"]}/resolve', {"version": prepared["version"], "action": "modify",
                  "reason": "采纳设备安排建议", "conclusion": "改到周日", "close_pending": True})
    assert result["status"] == "confirmed"
    mine = call(client, members[0], '/snapshot', method="GET")
    assert mine["contributions"][0]["id"] == opinion["id"]
    assert mine["contributions"][0]["resolution"]["after"] == "改到周日"


@pytest.mark.parametrize("case", ["unrelated", "revoked", "stale", "no_consent", "paused"])
def test_no_auto_card_for_invalid_evidence(env, case):
    client, store = env
    host, members, decision, original = setup_room(env)
    m = members[0]
    if case != "no_consent":
        authorize(client, m)
    refs = decision["evidence"] if case != "unrelated" else [{"segment": original["segments"][0]["id"], "rev": 1}]
    progress(env, m, refs, state="paused" if case == "paused" else "playing", consent_version=1 if case == "no_consent" else 2)
    if case == "revoked":
        call(client, m, '/me/consent', {"version": 2, "playback": False, "review": False}, "PUT")
    if case == "stale":
        with store.lock, store.db:
            r = store.load(host["room"])
            person = r["members"][store.auth(m["token"], m["room"])["id"]]
            person["heartbeat"] = time.monotonic()-10
            store.save(r)
    prepared = call(client, host, f'/decisions/{decision["id"]}/prepare', {"version": 1})
    assert prepared["status"] == "preparing"


def test_revision_invalidates_proof_and_old_receipts(env):
    client, _ = env
    host, members, d, original = setup_room(env)
    m = members[0]
    authorize(client, m)
    progress(env, m, d["evidence"])
    call(client, host, f'/decisions/{d["id"]}/prepare', {"version": 1})
    segment = d["evidence"][0]["segment"]
    call(client, host, f'/segments/{segment}', {"version": 1, "text": "周六不可以借设备"}, "PATCH")
    state = call(client, host, '/snapshot', method="GET")
    assert state["decisions"][0]["status"] == "review"
    assert state["requests"][0]["status"] == "invalidated"
    # 旧版证据与已修订的原句不能合用。
    actor = env[1].auth(m["token"], m["room"])
    epoch = env[1].connect(m["room"], actor)
    call(client, m, '/presentation', {"epoch": epoch, "seq": 1, "consent_version": 2, "evidence": d["evidence"],
         "state": "playing", "instance": "old"}, status=409)
    current = state["decisions"][0]
    prepared = call(client, host, f'/decisions/{d["id"]}/prepare', {"version": current["version"]})
    assert prepared["evidence"][0]["rev"] == 2
    assert prepared["status"] == "preparing"


def test_revoke_only_closes_own_request_and_deduplication(env):
    client, _ = env
    host, members, d, _ = setup_room(env)
    for m in members:
        authorize(client, m)
        progress(env, m, d["evidence"])
    call(client, host, f'/decisions/{d["id"]}/prepare', {"version": 1})
    call(client, members[0], '/me/consent', {"version": 2, "playback": False, "review": False}, "PUT")
    state = call(client, host, '/snapshot', method="GET")
    assert state["decisions"][0]["status"] == "waiting"
    assert [q["status"] for q in state["requests"]].count('delivered') == 1
    key = str(uuid4())
    first = call(client, members[1], '/requests', {"kind": "speak"}, key=key)
    second = call(client, members[1], '/requests', {"kind": "speak"}, key=key)
    assert first == second
    call(client, members[1], '/requests', {"kind": "wait"}, key=key, status=409)


def test_authorization_room_isolation_and_write_roles(env):
    client, _ = env
    host, members, d, snap = setup_room(env)
    board = {"room": host["room"], "token": snap["board_token"]}
    call(client, board, '/contributions', {"text": "越权"}, status=403)
    call(client, members[0], f'/decisions/{d["id"]}/prepare', {"version": 1}, status=403)
    other = client.post('/api/v1/rooms', json={"title": "另一个房间"}).json()
    response = client.get(f'/api/v1/rooms/{other["room"]}/snapshot', headers=headers(host))
    assert response.status_code == 401
    response = client.post('/api/v1/rooms', json={"title": "跨站"}, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


def test_optimistic_conflict_and_export_preview(env):
    client, _ = env
    host, members, d, _ = setup_room(env)
    call(client, host, f'/decisions/{d["id"]}/prepare', {"version": 1})
    call(client, host, f'/decisions/{d["id"]}/resolve', {"version": 1, "action": "maintain", "reason": "旧窗口"}, status=409)
    p = call(client, host, '/export/preview', method="GET")
    call(client, members[0], '/contributions', {"text": "独立意见"})
    call(client, host, '/export', {"revision": p["revision"]}, status=409)
    preview = call(client, host, '/export/preview', method="GET")
    result = call(client, host, '/export', {"revision": preview["revision"]})
    assert not ({"members", "presentation", "token", "requests"} & result.keys())


def test_websocket_role_projection_and_room_close(env):
    client, _ = env
    host, members, d, snap = setup_room(env)
    with client.websocket_connect(f'/api/v1/rooms/{host["room"]}/events') as ws:
        ws.send_json({"token": snap["board_token"]})
        assert ws.receive_json()["type"] == "connected"
        data = ws.receive_json()["data"]
        assert data["role"] == "board" and 'requests' not in data
        call(client, host, '/close', {})
        data = ws.receive_json()["data"]
        assert data["status"] == "closed" and data["decisions"] == []
    call(client, members[0], '/requests', {"kind": "wait"}, status=410)
    response = client.post('/api/v1/join', json={"invite": snap["invite"], "name": "后来者"})
    assert response.status_code == 404


def test_independent_contribution_can_be_answered_without_decision(env):
    client, _ = env
    host, members, _, _ = setup_room(env)
    contribution = call(client, members[0], '/contributions', {"text": "请解释一下今天的活动范围"})
    call(client, members[1], f'/contributions/{contribution["id"]}/reply', {"reason": "冒充主持人"}, status=403)
    call(client, host, f'/contributions/{contribution["id"]}/reply', {"reason": "本次只讨论校内活动安排"})
    mine = call(client, members[0], '/snapshot', method="GET")
    assert mine["contributions"][0]["status"] == "handled"
    assert mine["contributions"][0]["resolution"]["reason"] == "本次只讨论校内活动安排"
