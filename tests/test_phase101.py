import json
import time
from uuid import uuid4

from fastapi.testclient import TestClient

from tongxi import domain
from tongxi.main import create_app


def headers(session, key=None):
    return {"Authorization": "Bearer " + session["token"], "Idempotency-Key": key or str(uuid4())}


def call(client, session, path, data=None, method="POST", status=200):
    response = client.request(method, f'/api/v1/rooms/{session["room"]}{path}', json=data, headers=headers(session))
    assert response.status_code == status, response.text
    return response.json() if response.content else None


def snap(client, session):
    return call(client, session, "/snapshot", method="GET")


def join(client, host, name):
    invite = snap(client, host)["invite"]
    member = client.post("/api/v1/join", json={"invite": invite, "name": name}).json()
    call(client, member, "/me/consent", {"version": 1, "playback": False, "review": False, "camera": True, "intervene": True}, "PUT")
    return member


def connect(store, member):
    who = store.auth(member["token"], member["room"])
    store.connect(member["room"], who)
    return who


def signal(client, member):
    call(client, member, "/me/expression", {"kind": "speak", "confident": True, "summary": "可能希望补充"})


def test_domain_current_topic_and_epoch_do_not_follow_array_order():
    now = time.monotonic()
    room = {
        "members": {"m1": {"role": "member", "epoch": "new", "heartbeat": now, "consent": {"camera": True, "intervene": True}}},
        "decisions": [
            {"id": "a", "status": "preparing", "version": 2, "round": 1},
            {"id": "b", "status": "preparing", "version": 2, "round": 1},
        ],
        "requests": [{
            "id": "q1", "member": "m1", "decision": "a", "round": 1, "decision_version": 2,
            "epoch": "old", "at": time.time(), "kind": "speak", "source": "vision",
            "status": "pending", "reason": domain.NOTICE_TEXT, "closed_reason": "",
        }],
        "contributions": [], "history": [], "active_decision": None,
    }
    assert domain.current_target(room) is None
    room["active_decision"] = "a"
    assert domain.current_target(room)["id"] == "a"
    rejected = domain.decide_notice(room, {"id": "host", "role": "host"}, "q1", "confirm")
    assert rejected["rejected"] is True
    assert room["requests"][0]["status"] == "expired"
    assert room["requests"][0]["closed_reason"] == "连接已更新"
    assert room["requests"][0]["member"] == "m1"
    assert room["decisions"][0]["status"] == "preparing"
    room["active_decision"] = None
    assert domain.current_target(room) is None
    room["active_decision"] = "b"
    assert domain.current_target(room)["id"] == "b"


def test_member_actions_stay_with_the_requester(env=None):
    app = create_app(auto_ai=False)
    with TestClient(app) as client:
        store = app.state.store
        host = client.post("/api/v1/rooms", json={"title": "成员操作", "name": "主持"}).json()
        call(client, host, "/segments", {"text": "方案 B 需要实验室，周六可以。"})
        evidence = snap(client, host)["segments"][0]
        decision = call(client, host, "/decisions", {"title": "方案 B", "conclusion": "周六采用方案 B", "evidence": [{"segment": evidence["id"], "rev": 1}]})
        owner = join(client, host, "小林")
        other = join(client, host, "小周")
        connect(store, owner)
        call(client, host, f'/decisions/{decision["id"]}/prepare', {"version": decision["version"]})
        signal(client, owner)
        connect(store, other)
        signal(client, other)
        pending = snap(client, host)
        assert pending["active_decision"] == decision["id"]
        assert pending["decisions"][0]["status"] == "preparing"
        assert len(pending["notices"]) == 2
        assert "小林" not in json.dumps(pending["notices"], ensure_ascii=False)
        assert all("member" not in item for item in pending["notices"])
        for notice in pending["notices"]:
            call(client, host, f'/notices/{notice["id"]}', {"action": "confirm"})
        confirmed = snap(client, host)
        assert confirmed["decisions"][0]["status"] == "waiting"
        assert confirmed["notices"] == []
        mine = snap(client, owner)
        own = next(item for item in mine["requests"] if item["source"] == "vision" and item["status"] == "confirmed")
        stored = next(item for item in store.read(owner["room"])["requests"] if item["id"] == own["id"])
        assert stored["member"] == mine["me"]["id"]
        assert stored["decision"] == decision["id"]
        assert stored["epoch"] and stored["at"]
        assert "member" not in next(item for item in confirmed["requests"] if item["id"] == own["id"])
        page = client.get("/assets/app.js")
        script = page.text
        assert "我来表达" in script
        assert "data-member-request" in script
        assert "本次略过" in script
        assert "按持续表达信号留位" not in script
        assert "不会自动暂停会议" in script

        spoken = call(client, owner, "/contributions", {"text": "我想补充实验室时间。", "decision": decision["id"]})
        assert spoken["member"] == mine["me"]["id"]
        assert spoken["decision"] == decision["id"]
        assert any(item["id"] == own["id"] and item["status"] == "confirmed" for item in snap(client, owner)["requests"])
        assert snap(client, host)["decisions"][0]["status"] == "waiting"

        call(client, other, f'/requests/{own["id"]}', {"action": "skip"}, "PATCH", status=403)
        call(client, host, f'/requests/{own["id"]}', {"action": "withdraw"}, "PATCH", status=403)
        call(client, owner, f'/requests/{own["id"]}', {"action": "skip"}, "PATCH")
        after_skip = snap(client, host)
        assert after_skip["decisions"][0]["status"] == "waiting"
        theirs = next(item for item in snap(client, other)["requests"] if item["source"] == "vision")
        assert theirs["status"] == "confirmed"
        assert theirs["id"] != own["id"]
        call(client, other, f'/requests/{theirs["id"]}', {"action": "withdraw"}, "PATCH")
        finished = snap(client, host)
        assert finished["decisions"][0]["status"] == "preparing"
        assert finished["active_decision"] == decision["id"]
        assert next(item for item in snap(client, owner)["requests"] if item["id"] == own["id"])["status"] == "skipped"


def test_closing_the_latest_topic_does_not_restore_an_older_one():
    app = create_app(auto_ai=False)
    with TestClient(app) as client:
        store = app.state.store
        host = client.post("/api/v1/rooms", json={"title": "当前议题", "name": "主持"}).json()
        call(client, host, "/segments", {"text": "方案 B 需要实验室，周六可以。"})
        call(client, host, "/segments", {"text": "人员分工下次再定。"})
        state = snap(client, host)
        first = call(client, host, "/decisions", {"title": "方案 A", "conclusion": "先保留 A", "evidence": [{"segment": state["segments"][0]["id"], "rev": 1}]})
        second = call(client, host, "/decisions", {"title": "方案 B", "conclusion": "周六采用方案 B", "evidence": [{"segment": state["segments"][1]["id"], "rev": 1}]})
        member = join(client, host, "小林")
        connect(store, member)
        prepared_a = call(client, host, f'/decisions/{first["id"]}/prepare', {"version": first["version"]})
        signal(client, member)
        notice_a = snap(client, host)["notices"][0]
        assert notice_a["decision"] == first["id"]
        prepared_b = call(client, host, f'/decisions/{second["id"]}/prepare', {"version": second["version"]})
        switched = snap(client, host)
        assert switched["active_decision"] == second["id"]
        assert switched["decisions"][0]["status"] == "preparing"
        call(client, host, f'/notices/{notice_a["id"]}', {"action": "confirm"}, status=409)
        if not any(item["decision"] == second["id"] for item in switched["notices"]):
            signal(client, member)
            switched = snap(client, host)
        notice_b = next(item for item in switched["notices"] if item["decision"] == second["id"])
        call(client, host, f'/notices/{notice_b["id"]}', {"action": "confirm"})
        confirmed = snap(client, host)
        assert confirmed["decisions"][0]["status"] == "preparing"
        assert confirmed["decisions"][1]["status"] == "waiting"
        manual = call(client, member, "/requests", {"kind": "clarify", "decision": first["id"]})
        assert manual["source"] == "manual" and manual["status"] == "delivered"
        after_manual = snap(client, host)
        assert after_manual["active_decision"] == second["id"]
        assert after_manual["decisions"][0]["status"] == "waiting"
        assert after_manual["decisions"][1]["status"] == "waiting"
        call(client, member, f'/requests/{manual["id"]}', {"action": "withdraw"}, "PATCH")
        withdrawn = snap(client, host)
        assert withdrawn["decisions"][0]["status"] == "preparing"
        assert withdrawn["decisions"][1]["status"] == "waiting"
        assert withdrawn["active_decision"] == second["id"]
        resolved = call(client, host, f'/decisions/{second["id"]}/resolve', {
            "version": prepared_b["version"], "action": "maintain", "reason": "补充已核对，维持原结论", "close_pending": True
        })
        assert resolved["status"] == "confirmed"
        closed = snap(client, host)
        assert closed["active_decision"] is None
        assert closed["decisions"][0]["status"] == "preparing"
        assert closed["decisions"][1]["status"] == "confirmed"
        signal(client, member)
        assert snap(client, host)["notices"] == []
        focused = call(client, host, f'/decisions/{first["id"]}/focus', {"version": prepared_a["version"]})
        assert focused["id"] == first["id"]
        assert snap(client, host)["active_decision"] == first["id"]
        signal(client, member)
        restored = snap(client, host)
        assert len(restored["notices"]) == 1
        assert restored["notices"][0]["decision"] == first["id"]
        assert restored["decisions"][1]["status"] == "confirmed"
        assert "m.name" not in client.get("/assets/app.js").text
        assert "data-focus" in client.get("/assets/app.js").text


def test_reconnect_revoke_and_offline_expire_only_pending_notices():
    app = create_app(auto_ai=False)
    with TestClient(app) as client:
        store = app.state.store
        host = client.post("/api/v1/rooms", json={"title": "连接", "name": "主持"}).json()
        call(client, host, "/segments", {"text": "方案 B 需要实验室，周六可以。"})
        evidence = snap(client, host)["segments"][0]
        decision = call(client, host, "/decisions", {"title": "方案 B", "conclusion": "周六采用方案 B", "evidence": [{"segment": evidence["id"], "rev": 1}]})
        member = join(client, host, "小林")
        who = connect(store, member)
        call(client, host, f'/decisions/{decision["id"]}/prepare', {"version": decision["version"]})
        signal(client, member)
        old = snap(client, host)["notices"][0]
        stored = next(item for item in store.read(member["room"])["requests"] if item["id"] == old["id"])
        assert stored["epoch"] == store.read(member["room"])["members"][who["id"]]["epoch"]
        connect(store, member)
        assert snap(client, host)["notices"] == []
        assert next(item for item in store.read(member["room"])["requests"] if item["id"] == old["id"])["status"] == "expired"
        call(client, host, f'/notices/{old["id"]}', {"action": "confirm"}, status=409)
        signal(client, member)
        fresh = snap(client, host)["notices"][0]
        assert fresh["id"] != old["id"]
        call(client, host, f'/notices/{fresh["id"]}', {"action": "confirm"})
        opened = snap(client, host)
        assert opened["decisions"][0]["status"] == "waiting"
        connect(store, member)
        stayed = snap(client, member)
        assert next(item for item in stayed["requests"] if item["id"] == fresh["id"])["status"] == "confirmed"
        assert snap(client, host)["decisions"][0]["status"] == "waiting"
        assert snap(client, host)["notices"] == []
        call(client, member, f'/requests/{fresh["id"]}', {"action": "withdraw"}, "PATCH")
        assert snap(client, host)["decisions"][0]["status"] == "preparing"

        signal(client, member)
        pending = snap(client, host)["notices"][0]
        call(client, member, "/me/consent", {"version": 2, "playback": False, "review": False, "camera": True, "intervene": False}, "PUT")
        call(client, host, f'/notices/{pending["id"]}', {"action": "confirm"}, status=409)
        assert snap(client, host)["decisions"][0]["status"] == "preparing"

        call(client, member, "/me/consent", {"version": 3, "playback": False, "review": False, "camera": True, "intervene": True}, "PUT")
        who = connect(store, member)
        signal(client, member)
        offline = snap(client, host)["notices"][0]
        def mark_offline(room):
            room["members"][who["id"]]["heartbeat"] = 0
            return True
        store.apply(member["room"], mark_offline)
        call(client, host, f'/notices/{offline["id"]}', {"action": "confirm"}, status=409)
        assert next(item for item in store.read(member["room"])["requests"] if item["id"] == offline["id"])["status"] == "expired"
        assert snap(client, host)["decisions"][0]["status"] == "preparing"
