"""E1 工具测试。序列都是数值，不是真人记录，也不能写成参与者结果。"""
import json
import statistics
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
    cohort_reference,
    engineering_gate,
    evaluate,
    neutral_baseline,
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
            "session_id": "session-1", "session_role": "calibration", "agenda_id": decision["id"], "pressed_at_ms": 5_000,
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
            "session_id": "session-1", "session_role": "calibration", "agenda_id": decision["id"], "pressed_at_ms": 5_000,
        })
        rejected = client.post(
            f'/api/v1/rooms/{member["room"]}/e1/intents',
            json={"session_id": "session-1", "session_role": "calibration", "agenda_id": decision["id"],
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
        ticket = call(client, member, "/e1/baseline-attempts", {"session_role": "calibration", "calibration_id": "first"})
        call(client, member, "/e1/calibration", _calibration_body("first", samples, mouth=0.1, attempt_id=ticket["attempt_id"]))
        same = client.post(
            f'/api/v1/rooms/{member["room"]}/e1/calibration',
            json={"session_role": "calibration", "calibration_id": "first", "baseline": {"scale": 1, "mouth": 1, "pitch": 1, "turn": 1}, "samples": samples},
            headers=headers(member),
        )
        assert same.status_code == 422
        again = client.post(
            f'/api/v1/rooms/{member["room"]}/e1/baseline-attempts',
            json={"session_role": "calibration", "calibration_id": "second"},
            headers=headers(member),
        )
        assert again.status_code == 422
        second = client.post(
            f'/api/v1/rooms/{member["room"]}/e1/calibration',
            json=_calibration_body("second", samples, mouth=0.2, scale=0.3),
            headers=headers(member),
        )
        assert second.status_code == 422
        exported = call(client, _host, "/e1/export", method="GET")
        assert exported["participants"][0]["calibration"]["calibration_id"] == "first"
        assert exported["participants"][0]["calibration"]["locked"] is True
        assert len(exported["participants"][0]["calibration_history"]) == 1


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
            "batch_id": "batch-0001", "session_id": "session-1", "session_role": "calibration", "agenda_id": decision["id"],
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
            store.add_frames("room", member, {"batch_id": f"batch-{index:04d}", "session_id": "s", "session_role": "calibration", "frames": frames})
            store.add_intent("room", member, {"session_id": "s", "session_role": "calibration", "agenda_id": "a", "pressed_at_ms": 1000 + index})
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
        rejected = client.post(
            f'/api/v1/rooms/{host["room"]}/e1/reference',
            json=reference_body(),
            headers=headers(host),
        )
        assert rejected.status_code == 422
        frozen = client.post(f'/api/v1/rooms/{host["room"]}/e1/reference/freeze', json={}, headers=headers(host))
        assert frozen.status_code == 422
        call(client, member, "/e1/frames", {
            "batch_id": "batch-speech", "session_id": "session-1", "session_role": "calibration", "agenda_id": decision["id"],
            "frames": [{"frame_id": "only", "timestamp_ms": 5, "raw": _raw(), "face_count": 1, "shoulder_valid": True, "visibility_status": "ok"}],
        })
        exported = call(client, host, "/e1/export", method="GET")
        participant = exported["participants"][0]["participant_id"]
        speech = call(client, host, "/e1/speech", {
            "participant_id": participant, "session_id": "session-1", "agenda_id": decision["id"], "t_speech_ms": 50,
        })
        assert speech["created_positive"] is False
        exported = call(client, host, "/e1/export", method="GET")
        assert exported["global_reference_ready"] is False
        assert exported["participants"][0]["sessions"][0]["labels"] == []
        assert len(exported["participants"][0]["sessions"][0]["speeches"]) == 1
        blocked = dict(exported)
        blocked["synthetic"] = False
        with pytest.raises(ValueError, match="通用参照"):
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


def _measurements(scale=0.2, mouth=0.1, pitch=0.1, turn=0.0, count=32, invalid=0):
    rows = [{
        "raw": {"scale": scale, "mouth": mouth, "pitch": pitch, "turn": turn},
        "visibility_status": "ok", "face_count": 1, "shoulder_valid": True,
    } for _ in range(count)]
    rows.extend({
        "raw": {"scale": 9, "mouth": 9, "pitch": 9, "turn": 9},
        "visibility_status": "multi_face", "face_count": 2, "shoulder_valid": False,
    } for _ in range(invalid))
    return rows


def _calibration_body(calibration_id, samples, **kwargs):
    measurements = kwargs.pop("baseline_measurements", None)
    attempt_id = kwargs.pop("attempt_id", None)
    body = {
        "session_role": "calibration",
        "calibration_id": calibration_id,
        "duration_ms": kwargs.pop("duration_ms", 8000),
        "video_width": kwargs.pop("video_width", 480),
        "video_height": kwargs.pop("video_height", 360),
        "attempt": kwargs.pop("attempt", 1),
        "baseline_measurements": measurements if measurements is not None else _measurements(**kwargs),
        "samples": samples,
    }
    if attempt_id:
        body["attempt_id"] = attempt_id
    return body


_SAMPLE_SET = {"listen": [[0, 0, 0, 0]] * 6, "speak": [[0, 1, 0, 0]] * 6, "wait": [[1, 0, 0, 0]] * 6}


def _enroll(store, count, mouths=None, counts=None):
    for index in range(count):
        mouth = 0.10 + index * 0.01 if mouths is None else mouths[index]
        frames = 32 if counts is None else counts[index]
        invalid = max(0, 30 - frames)
        ticket = store.begin_baseline_attempt("cohort", f"m{index}", {"session_role": "calibration", "calibration_id": f"cal-{index}"})
        store.add_calibration("cohort", f"m{index}", _calibration_body(
            f"cal-{index}", _SAMPLE_SET, mouth=mouth, count=frames, invalid=invalid, attempt_id=ticket["attempt_id"],
        ))


def test_c01_one_frame_cannot_finish_calibration(tmp_path):
    assert neutral_baseline(_measurements(count=1), 8000, 480, 360) is None
    store = ExperimentStore(tmp_path)
    ticket = store.begin_baseline_attempt("room", "m", {"session_role": "calibration", "calibration_id": "only"})
    with pytest.raises(ValueError, match="有效帧不足"):
        store.add_calibration("room", "m", _calibration_body("only", _SAMPLE_SET, count=1, attempt_id=ticket["attempt_id"]))
    with pytest.raises(ValueError, match="单帧"):
        store.add_calibration("room", "m", {
            "session_role": "calibration", "calibration_id": "single", "samples": _SAMPLE_SET,
            "baseline": {"scale": 0.2, "mouth": 0.1, "pitch": 0.1, "turn": 0.0},
        })


def test_c02_thirty_two_frames_use_the_component_median():
    rows = [{
        "raw": {"scale": 0.2 + index * 0.001, "mouth": 0.1 + index * 0.0001, "pitch": 0.05, "turn": -0.02},
        "visibility_status": "ok", "face_count": 1, "shoulder_valid": True,
    } for index in range(32)]
    result = neutral_baseline(rows, 8000, 480, 360)
    assert result["scale"] == pytest.approx(0.2155)
    assert result["scale"] != rows[-1]["raw"]["scale"]
    assert result["protocol"] == "neutral_pose_median_v1"
    assert result["valid_frames"] == 32


def test_c03_twenty_four_valid_frames_can_finish():
    result = neutral_baseline(_measurements(count=24, invalid=8), 8000, 480, 360)
    assert result["valid_frames"] == 24
    assert result["sampled_frames"] == 32
    assert result["valid_ratio"] == pytest.approx(0.75)
    assert result["mouth"] == pytest.approx(0.1)


def test_c04_twenty_three_valid_frames_fail():
    assert neutral_baseline(_measurements(count=23), 8000, 480, 360) is None


def test_c05_non_finite_measurements_are_dropped():
    rows = _measurements(count=32)
    rows[0]["raw"]["mouth"] = float("nan")
    rows[1]["raw"]["turn"] = float("inf")
    result = neutral_baseline(rows, 8000, 480, 360)
    assert result["valid_frames"] == 30
    short = _measurements(count=23)
    short.append({
        "raw": {"scale": float("nan"), "mouth": 0.1, "pitch": 0.1, "turn": 0.0},
        "visibility_status": "ok", "face_count": 1, "shoulder_valid": True,
    })
    assert neutral_baseline(short, 8000, 480, 360) is None


def test_c06_high_mad_is_kept():
    rows = []
    for index in range(32):
        rows.append({
            "raw": {"scale": 0.2, "mouth": 0.0 if index < 16 else 1.0, "pitch": 0.1, "turn": 0.0},
            "visibility_status": "ok", "face_count": 1, "shoulder_valid": True,
        })
    result = neutral_baseline(rows, 8000, 480, 360)
    assert result["mouth"] == pytest.approx(0.5)
    assert result["mad"]["mouth"] == pytest.approx(0.5)
    assert result["mouth"] != 0.1


def test_c07_eight_people_can_freeze_a_componentwise_reference(tmp_path):
    store = ExperimentStore(tmp_path)
    _enroll(store, 8, mouths=[0.10, 0.10, 0.10, 0.10, 0.90, 0.90, 0.90, 0.90], counts=[32, 32, 32, 32, 24, 24, 24, 24])
    built = store.build_reference("cohort")
    assert built["ready"] is False
    assert built["values"]["mouth"] == pytest.approx(0.5)
    assert built["method"] == "cohort_neutral_baseline_median_v1"
    assert built["statistic"] == "componentwise_median"
    assert built["participant_count"] == 8
    pooled = [0.10] * (32 * 4) + [0.90] * (24 * 4)
    assert statistics.median(pooled) == pytest.approx(0.10)
    assert built["values"]["mouth"] == pytest.approx(0.5)
    frozen = store.freeze_reference("cohort")
    assert frozen["ready"] is True
    assert frozen["sha256"]
    assert store.export_room("cohort")["global_reference_ready"] is True


def test_c08_seven_people_cannot_become_ready(tmp_path):
    store = ExperimentStore(tmp_path)
    _enroll(store, 7)
    built = store.build_reference("cohort")
    assert built["ready"] is False
    assert built["participant_count"] == 7
    with pytest.raises(ValueError, match="8"):
        store.freeze_reference("cohort")
    assert store.export_room("cohort")["global_reference_ready"] is False


def test_c09_twelve_people_can_freeze(tmp_path):
    store = ExperimentStore(tmp_path)
    _enroll(store, 12)
    built = store.build_reference("cohort")
    assert built["participant_count"] == 12
    frozen = store.freeze_reference("cohort")
    assert frozen["ready"] is True
    assert frozen["protocol_deviation"] is False


def test_c10_test_session_data_cannot_build_the_reference(tmp_path):
    store = ExperimentStore(tmp_path)
    _enroll(store, 8, mouths=[0.10, 0.10, 0.10, 0.10, 0.90, 0.90, 0.90, 0.90], counts=[32, 32, 32, 32, 24, 24, 24, 24])
    built = store.build_reference("cohort")
    before = built["values"]["mouth"]
    store.freeze_reference("cohort")
    store.add_frames("cohort", "m0", {
        "batch_id": "batch-test", "session_id": "test-1", "session_role": "test",
        "same_device": True, "video_width": 480, "video_height": 360,
        "frames": [{
            "frame_id": "wild", "timestamp_ms": 1,
            "raw": {"scale": 0.2, "mouth": 5, "pitch": 0.1, "turn": 0},
            "face_count": 1, "shoulder_valid": True, "visibility_status": "ok",
        }],
    })
    with pytest.raises(ValueError, match="第二次讨论"):
        store.build_reference("cohort")
    assert store.export_room("cohort")["global_reference"]["values"]["mouth"] == pytest.approx(before)


def test_c11_a_test_session_blocks_any_later_reference_edit(tmp_path):
    store = ExperimentStore(tmp_path)
    _enroll(store, 8)
    store.build_reference("cohort")
    store.freeze_reference("cohort")
    store.open_session("cohort", "m0", {
        "session_id": "test-1", "session_role": "test", "same_device": True, "video_width": 480, "video_height": 360,
    })
    with pytest.raises(ValueError, match="第二次讨论"):
        store.build_reference("cohort")
    with pytest.raises(ValueError, match="四个数字|第二次讨论"):
        store.set_reference("cohort", reference_body())


def test_c12_a_frozen_reference_cannot_be_rebuilt(tmp_path):
    store = ExperimentStore(tmp_path)
    _enroll(store, 8)
    store.build_reference("cohort")
    store.freeze_reference("cohort")
    with pytest.raises(ValueError, match="已经冻结"):
        store.build_reference("cohort")
    with pytest.raises(ValueError, match="四个数字"):
        store.set_reference("cohort", reference_body())


def test_c13_synthetic_data_cannot_set_a_real_reference(tmp_path):
    store = ExperimentStore(tmp_path)
    store.mark_synthetic("cohort")
    _enroll(store, 8)
    with pytest.raises(ValueError, match="合成"):
        store.build_reference("cohort")
    with pytest.raises(ValueError, match="合成"):
        store.freeze_reference("cohort")
    exported = store.export_room("cohort")
    assert exported["global_reference_ready"] is False
    assert exported["global_reference"] is None


def test_c14_positive_window_is_removed_from_negative():
    exported = _export()
    session = exported["participants"][0]["sessions"][0]
    session["frames"] = [_frame(0, _raw()), _frame(100, _raw()), _frame(150_000, _raw()), _frame(150_100, _raw())]
    session["intents"] = []
    session["labels"] = [
        {"id": "n", "start_ms": 0, "end_ms": 180_000, "label": "negative", "label_source": "explicit_negative_review", "confirmation_status": "confirmed"},
        {"id": "p", "start_ms": 100_000, "end_ms": 100_000, "label": "positive", "label_source": "participant_review", "confirmation_status": "confirmed"},
    ]
    scored = evaluate(replay(exported), alerts={"A": [{"timestamp_ms": 100_000, "label": "speak"}, {"timestamp_ms": 150_000, "label": "speak"}], "B": [], "C": []})
    metrics = scored["participants"][0]["metrics"]["A"]
    assert metrics["matched"] == 1
    assert metrics["confirmed_false_alarms"] == 1
    assert metrics["unverified_alerts"] == 0


def test_c15_unknown_is_removed_from_negative():
    exported = _export()
    session = exported["participants"][0]["sessions"][0]
    session["frames"] = [_frame(110_000, _raw()), _frame(110_100, _raw()), _frame(150_000, _raw()), _frame(150_100, _raw())]
    session["intents"] = []
    session["labels"] = [
        {"id": "n", "start_ms": 0, "end_ms": 180_000, "label": "negative", "label_source": "explicit_negative_review", "confirmation_status": "confirmed"},
        {"id": "u", "start_ms": 100_000, "end_ms": 130_000, "label": "unknown", "label_source": "participant_review", "confirmation_status": "uncertain"},
    ]
    scored = evaluate(replay(exported), alerts={"A": [{"timestamp_ms": 110_000, "label": "speak"}, {"timestamp_ms": 150_000, "label": "speak"}], "B": [], "C": []})
    metrics = scored["participants"][0]["metrics"]["A"]
    assert metrics["confirmed_false_alarms"] == 1
    assert metrics["unverified_alerts"] == 1
    assert metrics["matched"] == 0


def test_c16_review_positive_is_a_single_time(tmp_path):
    store = ExperimentStore(tmp_path)
    with pytest.raises(ValueError, match="一个时间点"):
        store.add_label("room", "m", {
            "action": "add_positive", "session_id": "s", "session_role": "calibration", "agenda_id": "a",
            "start_timestamp": 1_000, "end_timestamp": 5_000, "review_anchor_ms": 10_000,
        })
    saved = store.add_label("room", "m", {
        "action": "add_positive", "session_id": "s", "session_role": "calibration", "agenda_id": "a",
        "start_timestamp": 8_000, "end_timestamp": 8_000, "review_anchor_ms": 10_000,
    })
    assert saved["start_timestamp"] == saved["end_timestamp"] == 8_000


def test_c17_old_presses_cannot_be_deleted_from_the_current_review(tmp_path):
    store = ExperimentStore(tmp_path)
    saved = store.add_intent("room", "m", {"session_id": "s", "session_role": "calibration", "agenda_id": "a", "pressed_at_ms": 1_000})
    with pytest.raises(ValueError, match="三分钟"):
        store.review_intent("room", "m", saved["id"], "delete", 200_000)
    page = (ROOT / "tongxi/web/e1.js").read_text(encoding="utf-8")
    assert "presses.filter" in page
    assert "大约在刚才三分钟里的多少秒前，你开始希望表达？" in page
    assert "review_anchor_ms" in page


def test_c18_a_resolution_change_is_a_protocol_deviation(tmp_path):
    store = ExperimentStore(tmp_path)
    _enroll(store, 8)
    store.build_reference("cohort")
    store.freeze_reference("cohort")
    changed = store.open_session("cohort", "m0", {
        "session_id": "test-a", "session_role": "test", "same_device": True, "video_width": 640, "video_height": 360,
    })
    assert changed["protocol_deviation"] is True
    same = store.open_session("cohort", "m1", {
        "session_id": "test-b", "session_role": "test", "same_device": True, "video_width": 480, "video_height": 360,
    })
    assert same["protocol_deviation"] is False
    refused = store.open_session("cohort", "m2", {
        "session_id": "test-c", "session_role": "test", "same_device": False, "video_width": 480, "video_height": 360,
    })
    assert refused["protocol_deviation"] is True


def test_c19_the_same_review_window_does_not_write_twice(tmp_path):
    store = ExperimentStore(tmp_path)
    body = {
        "action": "confirm_negative", "session_id": "s", "session_role": "calibration", "agenda_id": "a",
        "start_timestamp": 0, "end_timestamp": 180_000, "review_anchor_ms": 180_000,
    }
    first = store.add_label("room", "m", body)
    second = store.add_label("room", "m", body)
    assert first["id"] == second["id"]
    changed = store.add_label("room", "m", {**body, "action": "uncertain"})
    labels = store.export_room("room")["participants"][0]["sessions"][0]["labels"]
    assert len(labels) == 2
    assert sum(item["superseded"] for item in labels) == 1
    assert any(item["revised_from"] == first["id"] and item["revised_at"] for item in labels)
    assert changed["label"] == "unknown"
    assert sum(item["label"] == "negative" and not item["superseded"] for item in labels) == 0


def test_c20_the_product_path_stays_when_e1_is_off(monkeypatch):
    monkeypatch.delenv("TONGXI_E1", raising=False)
    app = create_app(auto_ai=False)
    with TestClient(app) as client:
        assert client.get("/api/v1/health").json()["e1"] is False
        host, member, decision = room(client)
        response = client.post(
            f'/api/v1/rooms/{member["room"]}/e1/intents',
            json={"session_id": "s", "session_role": "calibration", "agenda_id": decision["id"], "pressed_at_ms": 1000},
            headers=headers(member),
        )
        assert response.status_code == 404
        snap = call(client, host, "/snapshot", method="GET")
        assert snap["notices"] == []
        assert snap["decisions"][0]["status"] == "discussing"
    vision = (ROOT / "tongxi/web/vision.js").read_text(encoding="utf-8")
    calibrate = vision.split("calibrate()", 1)[1].split("record(", 1)[0]
    assert "baseline_arm" in calibrate
    assert calibrate.index("onExperimentFrame") < calibrate.index("this.baseline = {")
    assert "runNeutralBaseline" in vision
    assert "L1: 1000" in (ROOT / "tongxi/web/vision-policy.js").read_text(encoding="utf-8")
    assert MOUTH_THRESHOLD == 0.55


def test_withdraw_removes_features_without_putting_the_member_id_in_the_export(monkeypatch, tmp_path):
    monkeypatch.setenv("TONGXI_E1", "1")
    monkeypatch.setenv("TONGXI_E1_DIR", str(tmp_path))
    app = create_app(auto_ai=False)
    with TestClient(app) as client:
        host, member, decision = room(client)
        member_id = call(client, member, "/snapshot", method="GET")["me"]["id"]
        call(client, member, "/e1/frames", {
            "batch_id": "batch-withdraw", "session_id": "session-1", "session_role": "calibration", "agenda_id": decision["id"],
            "frames": [{"frame_id": "gone", "timestamp_ms": 5, "raw": _raw(), "face_count": 1, "shoulder_valid": True, "visibility_status": "ok"}],
        })
        call(client, member, "/e1/withdraw", {})
        exported = call(client, host, "/e1/export", method="GET")
        blob = json.dumps(exported)
        assert member_id not in blob
        assert exported["participants"][0]["withdrawn"] is True
        assert exported["participants"][0]["sessions"] == []


def test_server_rejects_baseline_outside_the_eight_second_bounds():
    short = _measurements(count=24)
    assert neutral_baseline(short, 1, 480, 360) is None
    assert neutral_baseline(short, 7000, 480, 360) is None
    accepted = neutral_baseline(_measurements(count=24, invalid=6), 8000, 480, 360)
    assert accepted["valid_frames"] == 24
    assert accepted["sampled_frames"] == 30
    full = neutral_baseline(_measurements(count=32), 8000, 640, 480)
    assert full["valid_frames"] == 32
    assert full["sampled_frames"] == 32
    assert neutral_baseline(_measurements(count=23, invalid=9), 8000, 480, 360) is None
    assert neutral_baseline(_measurements(count=33), 8000, 480, 360) is None
    poisoned = _measurements(count=32)
    poisoned[0]["raw"]["mouth"] = float("nan")
    poisoned[1]["raw"]["scale"] = float("inf")
    dropped = neutral_baseline(poisoned, 8000, 480, 360)
    assert dropped["valid_frames"] == 30
    assert neutral_baseline(_measurements(count=32), 8000, 0, 360) is None
    assert neutral_baseline(_measurements(count=32), 8000, True, 360) is None
    assert neutral_baseline(_measurements(count=32), 8000, -1, 360) is None


def test_attempts_are_server_side_and_cannot_be_reset(tmp_path):
    store = ExperimentStore(tmp_path)
    first = store.begin_baseline_attempt("room", "m", {"session_role": "calibration", "calibration_id": "first"})
    with pytest.raises(ValueError, match="有效帧不足"):
        store.add_calibration("room", "m", _calibration_body("first", _SAMPLE_SET, count=23, invalid=9, attempt_id=first["attempt_id"]))
    with pytest.raises(ValueError, match="已经结束"):
        store.add_calibration("room", "m", _calibration_body("brand-new", _SAMPLE_SET, attempt_id=first["attempt_id"]))
    second = store.begin_baseline_attempt("room", "m", {"session_role": "calibration", "calibration_id": "brand-new"})
    assert second["attempt_no"] == 2
    with pytest.raises(ValueError, match="两次"):
        store.begin_baseline_attempt("room", "m", {"session_role": "calibration", "calibration_id": "third"})
    status = store.calibration_status("room", "m")
    assert status["baseline"] is None
    assert status["baseline_failed"] is True
    assert status["attempts_used"] == 2
    assert "member_id" not in status
    with pytest.raises(ValueError, match="第二次讨论"):
        store.begin_baseline_attempt("room", "m", {"session_role": "test", "calibration_id": "later"})
