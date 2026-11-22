import json
from uuid import uuid4

from fastapi.testclient import TestClient

from tongxi.main import create_app


def headers(session, key=None):
    return {"Authorization": "Bearer " + session["token"], "Idempotency-Key": key or str(uuid4())}


def call(client, session, path, data=None, method="POST", status=200):
    response = client.request(method, f'/api/v1/rooms/{session["room"]}{path}', json=data, headers=headers(session))
    assert response.status_code == status, response.text
    return response.json() if response.content else None


def room(client, store):
    host = client.post("/api/v1/rooms", json={"title": "确认闭环", "name": "主持"}).json()
    call(client, host, "/segments", {"text": "方案 B 需要实验室，周六可以。" })
    call(client, host, "/segments", {"text": "人员分工下次再定。"})
    snap = call(client, host, "/snapshot", method="GET")
    first = call(client, host, "/decisions", {"title": "方案 B", "conclusion": "周六采用方案 B", "evidence": [{"segment": snap["segments"][0]["id"], "rev": 1}]})
    second = call(client, host, "/decisions", {"title": "人员分工", "conclusion": "下次再定", "evidence": [{"segment": snap["segments"][1]["id"], "rev": 1}]})
    member = client.post("/api/v1/join", json={"invite": snap["invite"], "name": "小林"}).json()
    call(client, member, "/me/consent", {"version": 1, "playback": False, "review": False, "camera": True, "intervene": True}, "PUT")
    store.connect(member["room"], store.auth(member["token"], member["room"]))
    return host, member, first, second


def test_notice_flow_covers_domain_and_http(env=None):
    app = create_app(auto_ai=False)
    with TestClient(app) as client:
        store = app.state.store
        host, member, first, second = room(client, store)

        call(client, member, "/me/expression", {"kind": "unknown", "confident": False, "summary": "多人同框，无法判断"})
        call(client, member, "/me/expression", {"kind": "speak", "confident": False, "summary": "动作不足"})
        quiet = call(client, host, "/snapshot", method="GET")
        assert quiet["notices"] == []
        assert quiet["requests"] == []
        assert quiet["decisions"][0]["status"] == "discussing"

        call(client, host, f'/decisions/{first["id"]}/prepare', {"version": 1})
        call(client, member, "/me/expression", {"kind": "speak", "confident": True, "summary": "可能希望补充"})
        pending = call(client, host, "/snapshot", method="GET")
        assert pending["decisions"][0]["status"] == "preparing"
        assert pending["decisions"][1]["status"] == "discussing"
        assert pending["requests"] == []
        assert len(pending["notices"]) == 1
        notice = pending["notices"][0]
        assert notice["text"] == "当前议题可能有人还没来得及回应。"
        assert notice["decision"] == first["id"]
        assert notice["status"] == "pending"
        assert "member" not in notice
        assert "小林" not in json.dumps(pending["notices"], ensure_ascii=False)
        assert all(person["expression"] is None for person in pending["members"])
        page = client.get("/assets/app.js")
        assert page.status_code == 200
        script = page.text
        assert "当前议题可能有人还没来得及回应。" in script
        assert "data-host-notice" in script
        assert "m.name" not in script

        call(client, host, f'/notices/{notice["id"]}', {"action": "ignore"})
        ignored = call(client, host, "/snapshot", method="GET")
        assert ignored["notices"] == []
        assert ignored["decisions"][0]["status"] == "preparing"
        assert ignored["decisions"][1]["status"] == "discussing"
        call(client, host, f'/notices/{notice["id"]}', {"action": "confirm"}, status=409)

        call(client, member, "/me/expression", {"kind": "speak", "confident": True, "summary": "可能希望补充"})
        again = call(client, host, "/snapshot", method="GET")
        assert again["notices"] == []
        assert again["decisions"][0]["status"] == "preparing"
        assert again["decisions"][1]["status"] == "discussing"
        member_view = call(client, member, "/snapshot", method="GET")
        assert member_view["context_phase"] == "uncertain"
        assert "analysis" not in member_view

        call(client, host, f'/decisions/{second["id"]}/prepare', {"version": 1})
        switched = call(client, host, "/snapshot", method="GET")
        assert switched["decisions"][0]["status"] == "preparing"
        call(client, host, f'/notices/{notice["id"]}', {"action": "confirm"}, status=409)

        current = next(item for item in switched["notices"] if item["decision"] == second["id"])
        call(client, member, "/me/consent", {"version": 2, "playback": False, "review": False, "camera": False, "intervene": True}, "PUT")
        closed = call(client, host, "/snapshot", method="GET")
        assert closed["notices"] == []
        assert closed["decisions"][1]["status"] == "preparing"
        call(client, host, f'/notices/{current["id"]}', {"action": "confirm"}, status=409)

        call(client, member, "/me/consent", {"version": 3, "playback": False, "review": False, "camera": True, "intervene": True}, "PUT")
        who = store.auth(member["token"], member["room"])
        epoch = store.connect(member["room"], who)
        call(client, member, "/me/expression", {"kind": "wait", "confident": True, "summary": "可能需要等待"})
        armed = call(client, host, "/snapshot", method="GET")
        leaving = armed["notices"][0]["id"]
        store.disconnect(member["room"], who, epoch)
        left = call(client, host, "/snapshot", method="GET")
        assert left["notices"] == []
        assert left["decisions"][1]["status"] == "preparing"
        call(client, host, f'/notices/{leaving}', {"action": "confirm"}, status=409)

        epoch = store.connect(member["room"], who)
        call(client, member, "/me/expression", {"kind": "speak", "confident": True, "summary": "可能希望补充"})
        final = call(client, host, "/snapshot", method="GET")["notices"][0]
        call(client, host, f'/notices/{final["id"]}', {"action": "confirm"})
        confirmed = call(client, host, "/snapshot", method="GET")
        assert confirmed["notices"] == []
        assert confirmed["decisions"][0]["status"] == "preparing"
        assert confirmed["decisions"][1]["status"] == "waiting"
        held = next(item for item in confirmed["requests"] if item["id"] == final["id"])
        assert held["source"] == "vision"
        assert held["status"] == "confirmed"
        assert "member" not in held
        call(client, host, f'/notices/{final["id"]}', {"action": "confirm"}, status=409)

        manual = call(client, member, "/requests", {"kind": "clarify", "decision": first["id"]})
        assert manual["source"] == "manual"
        assert manual["status"] == "delivered"
        after_manual = call(client, host, "/snapshot", method="GET")
        assert after_manual["decisions"][0]["status"] == "waiting"
        assert after_manual["decisions"][1]["status"] == "waiting"

        resolved = call(client, host, f'/decisions/{second["id"]}/resolve', {
            "version": confirmed["decisions"][1]["version"], "action": "maintain", "reason": "补充已核对，维持原结论", "close_pending": True
        })
        assert resolved["status"] == "confirmed"
        assert resolved["resolutions"][-1]["reason"] == "补充已核对，维持原结论"
        done = call(client, host, "/snapshot", method="GET")
        assert done["decisions"][0]["status"] == "waiting"
        assert done["decisions"][1]["status"] == "confirmed"
        board = client.get(f'/api/v1/rooms/{host["room"]}/snapshot', headers={"Authorization": "Bearer " + confirmed["board_token"]}).json()
        assert "notices" not in board
        assert "小林" not in json.dumps(board, ensure_ascii=False)
        assert board["signals"] == 0
