"""E1 工具测试。序列都是数值，不是真人记录，也不能写成参与者结果。"""
import json
from pathlib import Path
from threading import Thread
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from tongxi.e1 import (
    DISTANCE_MAX,
    L3_CONSISTENT_FRAMES,
    L3_MAX_MS,
    MARGIN,
    MATCH_LAG_MS,
    MATCH_LEAD_MS,
    MOUTH_THRESHOLD,
    WINDOW_MIN,
    ExperimentStore,
    UploadQueue,
    engineering_gate,
    evaluate,
    relative_feature,
    replay,
)
from tongxi.main import create_app

ROOT = Path(__file__).resolve().parents[1]


def headers(session, key=None):
    return {"Authorization": "Bearer " + session["token"], "Idempotency-Key": key or str(uuid4())}


def call(client, session, path, data=None, method="POST", status=200):
    response = client.request(method, f'/api/v1/rooms/{session["room"]}{path}', json=data, headers=headers(session))
    assert response.status_code == status, response.text
    return response.json() if response.content else None


def room(client):
    host = client.post("/api/v1/rooms", json={"title": "E1", "name": "主持人"}).json()
    call(client, host, "/segments", {"text": "方案 B 需要实验室，周六可以。"})
    snap = call(client, host, "/snapshot", method="GET")
    decision = call(client, host, "/decisions", {
        "title": "方案 B", "conclusion": "周六采用方案 B",
        "evidence": [{"segment": snap["segments"][0]["id"], "rev": 1}],
    })
    member = client.post("/api/v1/join", json={"invite": snap["invite"], "name": "小林"}).json()
    return host, member, decision


def test_e1_is_off_unless_the_environment_flag_is_set(monkeypatch):
    monkeypatch.delenv("TONGXI_E1", raising=False)
    app = create_app(auto_ai=False)
    with TestClient(app) as client:
        health = client.get("/api/v1/health").json()
        assert health["e1"] is False
        host, member, decision = room(client)
        response = client.post(
            f'/api/v1/rooms/{member["room"]}/e1/intents',
            json={"session_id": "s", "session_role": "test", "agenda_id": decision["id"], "pressed_at_ms": 1000},
            headers=headers(member),
        )
        assert response.status_code == 404
        snap = call(client, host, "/snapshot", method="GET")
        assert snap["notices"] == []
        assert snap["decisions"][0]["status"] == "discussing"


def test_intent_button_does_not_open_a_notice_or_change_the_decision(monkeypatch, tmp_path):
    monkeypatch.setenv("TONGXI_E1", "1")
    monkeypatch.setenv("TONGXI_E1_DIR", str(tmp_path))
    app = create_app(auto_ai=False)
    with TestClient(app) as client:
        host, member, decision = room(client)
        saved = call(client, member, "/e1/intents", {
            "session_id": "session-2", "session_role": "test", "agenda_id": decision["id"], "pressed_at_ms": 5_000,
        })
        assert saved["label"] == "positive"
        assert saved["label_source"] == "button"
        snap = call(client, host, "/snapshot", method="GET")
        assert snap["notices"] == []
        assert snap["decisions"][0]["status"] == "discussing"
        exported = call(client, host, "/e1/export", method="GET")
        blob = json.dumps(exported, ensure_ascii=False)
        assert "小林" not in blob
        assert "主持人" not in blob
        assert exported["participants"][0]["participant_id"].startswith("p")


def test_review_can_delete_a_press_but_cannot_invent_one(monkeypatch, tmp_path):
    monkeypatch.setenv("TONGXI_E1", "1")
    monkeypatch.setenv("TONGXI_E1_DIR", str(tmp_path))
    app = create_app(auto_ai=False)
    with TestClient(app) as client:
        host, member, decision = room(client)
        saved = call(client, member, "/e1/intents", {
            "session_id": "session-2", "session_role": "test", "agenda_id": decision["id"], "pressed_at_ms": 5_000,
        })
        rejected = client.post(
            f'/api/v1/rooms/{member["room"]}/e1/intents',
            json={"session_id": "session-2", "session_role": "test", "agenda_id": decision["id"],
                  "pressed_at_ms": 9_000, "label_source": "memory"},
            headers=headers(member),
        )
        assert rejected.status_code == 422
        call(client, member, f'/e1/intents/{saved["id"]}', {"action": "delete"})
        exported = call(client, host, "/e1/export", method="GET")
        intents = exported["participants"][0]["sessions"][0]["intents"]
        assert intents[0]["status"] == "deleted"
        assert all(item["label_source"] == "button" for item in intents)


def test_thresholds_and_match_window_stay_at_the_published_values():
    assert MOUTH_THRESHOLD == 0.55
    assert DISTANCE_MAX == 1.0
    assert MARGIN == 0.25
    assert WINDOW_MIN == 6
    assert L3_CONSISTENT_FRAMES == 4
    assert L3_MAX_MS == 2000
    assert (MATCH_LEAD_MS, MATCH_LAG_MS) == (2000, 1500)


def test_relative_feature_uses_the_product_denominators():
    feature = relative_feature(
        {"scale": 0.236, "mouth": 0.30, "pitch": 0.10, "turn": -0.04},
        {"scale": 0.200, "mouth": 0.18, "pitch": 0.10, "turn": 0.00},
    )
    assert feature[0] == pytest.approx((0.236 / 0.200 - 1) / 0.18)
    assert feature[1] == pytest.approx((0.30 - 0.18) / 0.12)


def _frame(ts, raw, status="ok", faces=1, shoulders=True):
    return {
        "timestamp_ms": ts,
        "raw": raw,
        "face_count": faces,
        "shoulder_valid": shoulders,
        "visibility_status": status,
        "agenda_id": "agenda-1",
    }


def _export():
    rest = {"scale": 0.20, "mouth": 0.18, "pitch": 0.10, "turn": 0.00}
    personal = {"scale": 0.20, "mouth": 0.10, "pitch": 0.10, "turn": 0.00}
    still = {"scale": 0.20, "mouth": 0.10, "pitch": 0.10, "turn": 0.00}
    speak = {"scale": 0.20, "mouth": 0.22, "pitch": 0.10, "turn": 0.00}
    wait = {"scale": 0.24, "mouth": 0.10, "pitch": 0.10, "turn": 0.00}
    frames = []
    for index in range(8):
        frames.append(_frame(10_000 + index * 100, speak if index < 6 else still))
    frames.append(_frame(12_000, speak, status="multi_face", faces=2))
    return {
        "synthetic": True,
        "global_reference": rest,
        "participants": [{
            "participant_id": "p-test",
            "calibration": {
                "session_role": "calibration",
                "calibration_id": "cal-1",
                "duration_ms": 4_000,
                "baseline": personal,
                "samples": {
                    "listen": [relative_feature(still, personal)] * 6,
                    "speak": [relative_feature(speak, personal)] * 6,
                    "wait": [relative_feature(wait, personal)] * 6,
                },
            },
            "sessions": [{
                "session_id": "session-2",
                "session_role": "test",
                "frames": frames,
                "intents": [{
                    "id": "i1", "pressed_at_ms": 10_300, "agenda_id": "agenda-1",
                    "status": "confirmed", "label_source": "button", "t_speech_ms": None,
                }],
            }],
        }],
    }


def test_condition_c_confirms_only_after_four_ready_frames():
    exported = _export()
    personal = exported["participants"][0]["calibration"]["baseline"]
    speak = {"scale": 0.20, "mouth": 0.22, "pitch": 0.10, "turn": 0.00}
    session = exported["participants"][0]["sessions"][0]
    session["frames"] = [_frame(10_000 + index * 100, speak) for index in range(9)]
    session["intents"] = [{
        "id": "i1", "pressed_at_ms": 10_800, "agenda_id": "agenda-1",
        "status": "confirmed", "label_source": "button", "t_speech_ms": None,
    }]
    result = replay(exported)
    alerts = result["participants"][0]["alerts"]
    assert alerts["A"] == []
    assert alerts["B"][0]["timestamp_ms"] == 10_100
    assert alerts["C"][0]["timestamp_ms"] == 10_800
    assert alerts["C"][0]["label"] == "speak"
    assert relative_feature(speak, personal)[1] > 0.55


def test_same_frames_feed_three_rules_and_session_two_cannot_train():
    exported = _export()
    result = replay(exported)
    row = result["participants"][0]
    assert row["sessions"][0]["frames"][0]["prediction_A"] is not None or row["alerts"]["A"] == []
    assert set(row["alerts"]) <= {"A", "B", "C"}
    leaked = dict(exported)
    leaked["participants"][0]["calibration"]["session_role"] = "test"
    with pytest.raises(ValueError, match="第二次讨论"):
        replay(leaked)


def test_unknown_is_counted_and_left_out_of_precision():
    result = evaluate(replay(_export()))
    metrics = result["participants"][0]["metrics"]
    assert metrics["unknown_count"] == 1
    assert metrics["unknown_ratio"] == pytest.approx(1 / 9)
    for condition in ("A", "B", "C"):
        assert "precision" in metrics[condition]
        assert metrics[condition]["unknown_in_scores"] is False
    assert result["human_evidence"] is False
    assert result["claim_level"] == "synthetic_tool_check"
    frames = replay(_export())["participants"][0]["sessions"][0]["frames"]
    assert all(frame["label"] != "negative" for frame in frames if frame["visibility_status"] == "ok")


def test_match_window_keeps_early_and_late_alerts_and_drops_the_rest():
    exported = _export()
    session = exported["participants"][0]["sessions"][0]
    session["frames"] = [
        _frame(0, {"scale": 0.20, "mouth": 0.10, "pitch": 0.10, "turn": 0.00}),
    ]
    session["intents"] = [{
        "id": "i1", "pressed_at_ms": 10_000, "agenda_id": "agenda-1",
        "status": "confirmed", "label_source": "button", "t_speech_ms": None,
    }]
    scored = evaluate(replay(exported), alerts={
        "A": [{"timestamp_ms": 8_000, "label": "speak"}, {"timestamp_ms": 11_500, "label": "wait"}, {"timestamp_ms": 13_000, "label": "speak"}],
        "B": [],
        "C": [],
    })
    matched = scored["participants"][0]["metrics"]["A"]
    assert matched["matched"] == 1
    assert matched["duplicate_alerts"] == 1
    assert matched["unverified_alerts"] == 1
    assert matched["confirmed_false_alarms"] == 0
    assert [item["delta_ms"] for item in matched["deltas"]] == [1500]


def test_gate_reports_the_v21_stop_without_editing_thresholds():
    before = (MOUTH_THRESHOLD, DISTANCE_MAX, MARGIN)
    stopped = engineering_gate(
        {"confirmed_false_alarms_per_10_min": 1, "recall": 0.8},
        {"confirmed_false_alarms_per_10_min": 2, "recall": 0.69},
        label_coverage=1,
    )
    assert stopped["remove_automatic_vision"] is True
    assert stopped["applied_to_product"] is False
    kept = engineering_gate(
        {"confirmed_false_alarms_per_10_min": 2, "recall": 0.8},
        {"confirmed_false_alarms_per_10_min": 1, "recall": 0.75},
        label_coverage=1,
    )
    assert kept["remove_automatic_vision"] is False
    missing = engineering_gate(
        {"confirmed_false_alarms_per_10_min": None, "recall": None},
        {"confirmed_false_alarms_per_10_min": 0, "recall": 1},
    )
    assert missing["decision"] == "insufficient"
    thin = engineering_gate(
        {"confirmed_false_alarms_per_10_min": 2, "recall": 0.8},
        {"confirmed_false_alarms_per_10_min": 1, "recall": 0.8},
        label_coverage=0.2,
    )
    assert thin["decision"] == "insufficient"
    assert (MOUTH_THRESHOLD, DISTANCE_MAX, MARGIN) == before


def test_completed_calibration_stays_locked(monkeypatch, tmp_path):
    monkeypatch.setenv("TONGXI_E1", "1")
    monkeypatch.setenv("TONGXI_E1_DIR", str(tmp_path))
    app = create_app(auto_ai=False)
    with TestClient(app) as client:
        _host, member, _decision = room(client)
        samples = {"listen": [[0, 0, 0, 0]] * 6, "speak": [[0, 1, 0, 0]] * 6, "wait": [[1, 0, 0, 0]] * 6}
        call(client, member, "/e1/calibration", {
            "session_role": "calibration", "calibration_id": "first", "duration_ms": 1000,
            "baseline": {"scale": 0.2, "mouth": 0.1, "pitch": 0.1, "turn": 0.0},
            "samples": samples,
        })
        same = client.post(
            f'/api/v1/rooms/{member["room"]}/e1/calibration',
            json={"session_role": "calibration", "calibration_id": "first", "baseline": {"scale": 1, "mouth": 1, "pitch": 1, "turn": 1}, "samples": samples},
            headers=headers(member),
        )
        assert same.status_code == 422
        call(client, member, "/e1/calibration", {
            "session_role": "calibration", "calibration_id": "second", "duration_ms": 1000,
            "baseline": {"scale": 0.3, "mouth": 0.2, "pitch": 0.1, "turn": 0.0},
            "samples": samples,
        })
        exported = call(client, _host, "/e1/export", method="GET")
        assert exported["participants"][0]["calibration"]["calibration_id"] == "first"
        assert exported["participants"][0]["calibration"]["locked"] is True
        assert len(exported["participants"][0]["calibration_history"]) == 2


def test_client_files_keep_the_button_out_of_the_product_path():
    app = (ROOT / "tongxi/web/app.js").read_text(encoding="utf-8")
    vision = (ROOT / "tongxi/web/vision.js").read_text(encoding="utf-8")
    page = (ROOT / "tongxi/web/e1.js").read_text(encoding="utf-8")
    assert "state.health && state.health.e1" in app
    assert "onExperimentFrame" in vision
    assert "此刻我想说" in page
    assert "结束本场采集并完成上传" in page
    assert "我确认本时间段没有表达意愿" in page
    assert "不能凭模型或面部动作" in page
    assert "prediction_A" not in page
    assert "/expression" not in page
    assert "/notices" not in page
    assert "armExperimentSample" in vision
    assert "250" in vision
    policy = (ROOT / "tongxi/web/vision-policy.js").read_text(encoding="utf-8")
    assert "L1: 1000" in policy
    assert page.index("if (!response.ok) throw") < page.index("pending.shift()")


def _raw(mouth=0.1):
    return {"scale": 0.2, "mouth": mouth, "pitch": 0.1, "turn": 0.0}


def test_small_batches_are_idempotent_when_the_response_is_lost(monkeypatch, tmp_path):
    monkeypatch.setenv("TONGXI_E1", "1")
    monkeypatch.setenv("TONGXI_E1_DIR", str(tmp_path))
    app = create_app(auto_ai=False)
    with TestClient(app) as client:
        _host, member, decision = room(client)
        member["id"] = call(client, member, "/snapshot", method="GET")["me"]["id"]
        batch = {
            "batch_id": "batch-0001", "session_id": "session-2", "session_role": "test", "agenda_id": decision["id"],
            "frames": [{"frame_id": "frame-1", "timestamp_ms": 10, "raw": _raw(), "face_count": 1, "shoulder_valid": True, "visibility_status": "ok"}],
        }
        store = ExperimentStore(tmp_path)
        queue = UploadQueue()
        queue.add(batch)
        state = {"lost": False}

        def send(item):
            store.add_frames(member["room"], member["id"], item)
            if not state["lost"]:
                state["lost"] = True
                raise TimeoutError("响应丢失")

        with pytest.raises(TimeoutError):
            queue.flush_once(send)
        assert queue.pending
        assert queue.flush_once(send) == "acked"
        assert queue.pending == []
        exported = call(client, _host, "/e1/export", method="GET")
        frames = exported["participants"][0]["sessions"][0]["frames"]
        assert len(frames) == 1
        again = call(client, member, "/e1/frames", batch)
        assert again["duplicate"] is True
        exported = call(client, _host, "/e1/export", method="GET")
        assert len(exported["participants"][0]["sessions"][0]["frames"]) == 1


def test_ten_participants_can_write_at_the_same_time(tmp_path):
    store = ExperimentStore(tmp_path)
    errors = []

    def write(index):
        try:
            member = f"m{index}"
            frames = [{
                "frame_id": f"f-{index}-{item}", "timestamp_ms": item, "raw": _raw(),
                "face_count": 1, "shoulder_valid": True, "visibility_status": "ok",
            } for item in range(15)]
            store.add_frames("room", member, {"batch_id": f"batch-{index:04d}", "session_id": "s", "session_role": "test", "frames": frames})
            store.add_intent("room", member, {"session_id": "s", "session_role": "test", "agenda_id": "a", "pressed_at_ms": 1000 + index})
        except Exception as exc:
            errors.append(exc)

    threads = [Thread(target=write, args=(index,)) for index in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    exported = store.export_room("room")
    assert len(exported["participants"]) == 10
    assert sum(len(session["frames"]) for person in exported["participants"] for session in person["sessions"]) == 150
    blob = json.dumps(exported)
    assert "m0" not in blob
    assert all(person["participant_id"].startswith("p") for person in exported["participants"])


def test_condition_c_clears_the_window_after_an_invalid_frame_or_a_gap():
    speak = _raw(0.22)
    exported = _export()
    session = exported["participants"][0]["sessions"][0]
    broken = [_frame(10_000 + index * 100, speak) for index in range(5)]
    broken.append(_frame(10_500, speak, status="multi_face", faces=2))
    broken.extend(_frame(10_600 + index * 100, speak) for index in range(5))
    session["frames"] = broken
    assert replay(exported)["participants"][0]["alerts"]["C"] == []
    gapped = [_frame(10_000 + index * 100, speak) for index in range(3)]
    gapped.extend(_frame(10_700 + index * 100, speak) for index in range(6))
    session["frames"] = gapped
    assert replay(exported)["participants"][0]["alerts"]["C"] == []


def test_valid_time_does_not_cross_unknown_or_a_long_hole():
    from tongxi.e1 import _observation
    frames = [
        _frame(0, _raw()),
        _frame(100, _raw()),
        _frame(200, _raw(), status="no_face", faces=0),
        _frame(5_000, _raw()),
        _frame(5_100, _raw()),
    ]
    valid_ms, _segments = _observation(frames)
    assert valid_ms == 200


def test_speech_is_independent_and_reference_stays_unready(monkeypatch, tmp_path):
    monkeypatch.setenv("TONGXI_E1", "1")
    monkeypatch.setenv("TONGXI_E1_DIR", str(tmp_path))
    app = create_app(auto_ai=False)
    with TestClient(app) as client:
        host, member, decision = room(client)
        recorded = reference_body()
        saved = call(client, host, "/e1/reference", recorded)
        assert saved["ready"] is False
        frozen = call(client, host, "/e1/reference/freeze", {})
        assert frozen["frozen"] is True
        assert frozen["ready"] is False
        rejected = client.post(
            f'/api/v1/rooms/{host["room"]}/e1/reference',
            json=recorded,
            headers=headers(host),
        )
        assert rejected.status_code == 422
        call(client, member, "/e1/frames", {
            "batch_id": "batch-speech", "session_id": "session-2", "session_role": "test", "agenda_id": decision["id"],
            "frames": [{"frame_id": "only", "timestamp_ms": 5, "raw": _raw(), "face_count": 1, "shoulder_valid": True, "visibility_status": "ok"}],
        })
        exported = call(client, host, "/e1/export", method="GET")
        participant = exported["participants"][0]["participant_id"]
        speech = call(client, host, "/e1/speech", {
            "participant_id": participant, "session_id": "session-2", "agenda_id": decision["id"], "t_speech_ms": 50,
        })
        assert speech["created_positive"] is False
        exported = call(client, host, "/e1/export", method="GET")
        assert exported["global_reference_ready"] is False
        assert exported["participants"][0]["sessions"][0]["labels"] == []
        assert len(exported["participants"][0]["sessions"][0]["speeches"]) == 1
        blocked = dict(exported)
        blocked["synthetic"] = False
        with pytest.raises(ValueError, match="尚未就绪"):
            replay(blocked)


def reference_body():
    return {"values": {"scale": 0.2, "mouth": 0.18, "pitch": 0.1, "turn": 0.0}, "source": "待项目负责人确定采集方法", "version": "unset"}


def test_confirmed_negative_counts_a_false_alarm_and_unlabeled_does_not():
    exported = _export()
    session = exported["participants"][0]["sessions"][0]
    session["labels"] = [{
        "id": "n1", "start_ms": 12_000, "end_ms": 14_000, "label": "negative",
        "label_source": "explicit_negative_review", "confirmation_status": "confirmed",
    }]
    session["intents"] = []
    scored = evaluate(replay(exported), alerts={"A": [{"timestamp_ms": 13_000, "label": "speak"}, {"timestamp_ms": 20_000, "label": "speak"}], "B": [], "C": []})
    metrics = scored["participants"][0]["metrics"]["A"]
    assert metrics["confirmed_false_alarms"] == 1
    assert metrics["unverified_alerts"] == 1
    assert metrics["precision"] == 0


def test_withdraw_removes_features_without_putting_the_member_id_in_the_export(monkeypatch, tmp_path):
    monkeypatch.setenv("TONGXI_E1", "1")
    monkeypatch.setenv("TONGXI_E1_DIR", str(tmp_path))
    app = create_app(auto_ai=False)
    with TestClient(app) as client:
        host, member, decision = room(client)
        member_id = call(client, member, "/snapshot", method="GET")["me"]["id"]
        call(client, member, "/e1/frames", {
            "batch_id": "batch-withdraw", "session_id": "session-2", "session_role": "test", "agenda_id": decision["id"],
            "frames": [{"frame_id": "gone", "timestamp_ms": 5, "raw": _raw(), "face_count": 1, "shoulder_valid": True, "visibility_status": "ok"}],
        })
        call(client, member, "/e1/withdraw", {})
        exported = call(client, host, "/e1/export", method="GET")
        blob = json.dumps(exported)
        assert member_id not in blob
        assert exported["participants"][0]["withdrawn"] is True
        assert exported["participants"][0]["sessions"] == []
