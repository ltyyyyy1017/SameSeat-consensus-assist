"""8 人合成预演。human_evidence 必须保持 false，合成参照不能把正式房间标成 ready。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tongxi.e1 import (
    ExperimentStore,
    cohort_reference,
    evaluate,
    neutral_baseline,
    reference_digest,
    relative_feature,
    replay,
)

SAMPLES = {"listen": [[0, 0, 0, 0]] * 6, "speak": [[0, 1, 0, 0]] * 6, "wait": [[0.2, 0, 0.2, 0]] * 6}


def rows_for(mouth, count):
    rows = []
    for index in range(count):
        delta = (index - (count - 1) / 2) * 0.001
        rows.append({
            "raw": {"scale": 0.20 + delta, "mouth": mouth + delta, "pitch": 0.10, "turn": 0.00},
            "visibility_status": "ok",
            "face_count": 1,
            "shoulder_valid": True,
        })
    return rows


def enroll(store, room, count):
    people = []
    for index in range(count):
        mouth = 0.10 if index < 4 else 0.90
        frames = 32 if index < 4 else 24
        measurements = rows_for(mouth, frames)
        saved = store.add_calibration(room, f"drill-{index}", {
            "session_role": "calibration",
            "calibration_id": f"cal-{index}",
            "duration_ms": 8000,
            "video_width": 480,
            "video_height": 360,
            "attempt": 1,
            "baseline_measurements": measurements,
            "samples": SAMPLES,
        })
        people.append({
            "member": f"drill-{index}",
            "participant_id": saved["participant_id"],
            "mouth": saved["baseline"]["mouth"],
            "mad_mouth": saved["baseline"]["mad"]["mouth"],
            "valid_frames": saved["baseline"]["valid_frames"],
            "last_mouth": measurements[-1]["raw"]["mouth"],
            "baseline": saved["baseline"],
            "calibration_id": f"cal-{index}",
        })
    return people


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("artifacts/e1-baseline-rehearsal")
    root.mkdir(parents=True, exist_ok=True)
    for name in ("rehearsal.sqlite", "rehearsal.sqlite-wal", "rehearsal.sqlite-shm", "over12.sqlite", "over12.sqlite-wal", "over12.sqlite-shm"):
        path = root / name
        if path.exists():
            path.unlink()
    store = ExperimentStore(root)
    room = "rehearsal"
    store.mark_synthetic(room)
    people = enroll(store, room, 8)
    values = cohort_reference([person["baseline"] for person in people])
    ordered = sorted(people, key=lambda item: item["participant_id"])
    digest = reference_digest({
        "method": "cohort_neutral_baseline_median_v1",
        "version": "1.0",
        "participant_count": 8,
        "values": json.loads(json.dumps(values)),
        "statistic": "componentwise_median",
        "capture_protocol": "neutral_pose_median_v1",
        "participant_ids": [item["participant_id"] for item in ordered],
        "calibration_ids": [item["calibration_id"] for item in ordered],
    })
    exported = store.export_room(room)
    probe = {"scale": 0.20, "mouth": 0.20, "pitch": 0.10, "turn": 0.00}
    frames = [{
        "timestamp_ms": 1000 + offset,
        "raw": probe,
        "face_count": 1,
        "shoulder_valid": True,
        "visibility_status": "ok",
    } for offset in (0, 100)]
    replay_input = {
        "synthetic": True,
        "global_reference": values,
        "participants": [],
    }
    for person in exported["participants"]:
        copied = {
            "participant_id": person["participant_id"],
            "calibration": person["calibration"],
            "sessions": [{
                "session_id": "synthetic-test",
                "session_role": "test",
                "frames": [dict(frame) for frame in frames],
                "labels": [],
                "intents": [],
            }],
        }
        replay_input["participants"].append(copied)
    low = min(people, key=lambda item: abs(item["mouth"] - 0.10))
    replayed = replay(replay_input)
    low_row = next(row for row in replayed["participants"] if row["participant_id"] == low["participant_id"])
    feature = low_row["sessions"][0]["frames"][0]["feature_vector"]
    personal_mouth = relative_feature(probe, {"scale": low["baseline"]["scale"], "mouth": low["mouth"], "pitch": low["baseline"]["pitch"], "turn": low["baseline"]["turn"]})[1]
    global_mouth = relative_feature(probe, values)[1]
    labeled = json.loads(json.dumps(replay_input))
    labeled["participants"] = [labeled["participants"][0]]
    labeled["participants"][0]["participant_id"] = low["participant_id"]
    labeled["participants"][0]["calibration"] = next(person["calibration"] for person in exported["participants"] if person["participant_id"] == low["participant_id"])
    labeled["participants"][0]["sessions"][0]["labels"] = [
        {"id": "n", "start_ms": 0, "end_ms": 180_000, "label": "negative", "label_source": "explicit_negative_review", "confirmation_status": "confirmed"},
        {"id": "p", "start_ms": 100_000, "end_ms": 100_000, "label": "positive", "label_source": "participant_review", "confirmation_status": "confirmed"},
        {"id": "u", "start_ms": 100_000, "end_ms": 130_000, "label": "unknown", "label_source": "participant_review", "confirmation_status": "uncertain"},
    ]
    label_metrics = evaluate(replay(labeled), alerts={
        "A": [
            {"timestamp_ms": 100_000, "label": "speak"},
            {"timestamp_ms": 110_000, "label": "speak"},
            {"timestamp_ms": 150_000, "label": "speak"},
        ],
        "B": [],
        "C": [],
    })["participants"][0]["metrics"]["A"]
    rejected = {}
    for name, action in (
        ("build", lambda: store.build_reference(room)),
        ("freeze", lambda: store.freeze_reference(room)),
        ("test_session", lambda: store.open_session(room, "drill-0", {
            "session_id": "test-1", "session_role": "test", "same_device": True, "video_width": 480, "video_height": 360,
        })),
    ):
        try:
            action()
            rejected[name] = False
        except ValueError as exc:
            rejected[name] = str(exc)
    over = ExperimentStore(root)
    enroll(over, "over12", 13)
    over_built = over.build_reference("over12")
    try:
        over.freeze_reference("over12")
        over_freeze = "unexpected-ready"
    except ValueError as exc:
        over_freeze = str(exc)
    report = {
        "human_evidence": False,
        "claim_level": "synthetic_tool_check",
        "applied_to_product": False,
        "global_reference_ready": exported["global_reference_ready"],
        "participants": len(exported["participants"]),
        "personal": [{
            "participant_id": person["participant_id"],
            "mouth": person["mouth"],
            "mad_mouth": person["mad_mouth"],
            "valid_frames": person["valid_frames"],
            "last_frame_is_median": person["last_mouth"] == person["mouth"],
        } for person in people],
        "synthetic_reference": {
            "ready": False,
            "applied_to_room": False,
            "method": "cohort_neutral_baseline_median_v1",
            "version": "1.0",
            "statistic": "componentwise_median",
            "values": values,
            "sha256": digest,
            "note": "计算函数的合成结果，不是真人通用参照",
        },
        "abc": {
            "same_frames": True,
            "A_alerts": len(low_row["alerts"]["A"]),
            "B_alerts": len(low_row["alerts"]["B"]),
            "feature_mouth": feature[1],
            "personal_mouth": personal_mouth,
            "global_mouth": global_mouth,
            "uses_personal_feature": abs(feature[1] - personal_mouth) < 1e-9,
        },
        "labels": {
            "matched": label_metrics["matched"],
            "confirmed_false_alarms": label_metrics["confirmed_false_alarms"],
            "unverified_alerts": label_metrics["unverified_alerts"],
        },
        "rejected": rejected,
        "over_12": {
            "participant_count": over_built["participant_count"],
            "ready": over_built["ready"],
            "protocol_deviation": over_built["protocol_deviation"],
            "freeze": over_freeze,
            "export_ready": over.export_room("over12")["global_reference_ready"],
        },
    }
    (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    checks = [
        report["human_evidence"] is False,
        report["global_reference_ready"] is False,
        report["participants"] == 8,
        all(person["valid_frames"] >= 24 for person in report["personal"]),
        all(abs(person["mouth"] - 0.10) < 1e-6 or abs(person["mouth"] - 0.90) < 1e-6 for person in people),
        all(person["last_frame_is_median"] is False for person in report["personal"]),
        report["synthetic_reference"]["ready"] is False,
        report["abc"]["A_alerts"] == 0,
        report["abc"]["B_alerts"] == 1,
        report["abc"]["uses_personal_feature"] is True,
        report["abc"]["personal_mouth"] != report["abc"]["global_mouth"],
        report["labels"] == {"matched": 1, "confirmed_false_alarms": 1, "unverified_alerts": 1},
        all(rejected.values()),
        report["over_12"]["ready"] is False,
        report["over_12"]["export_ready"] is False,
        bool(report["synthetic_reference"]["sha256"]),
    ]
    return 0 if all(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
