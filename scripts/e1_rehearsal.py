"""8 人合成预演。human_evidence 必须保持 false，不能写成真人结果。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tongxi.e1 import ExperimentStore, UploadQueue, engineering_gate, evaluate, replay

MINUTE = 60_000
DRILL_REFERENCE = {"scale": 0.20, "mouth": 0.18, "pitch": 0.10, "turn": 0.00}


def feature(mouth, scale=0.20):
    return {"scale": scale, "mouth": mouth, "pitch": 0.10, "turn": 0.00}


def samples_for(personal):
    still = feature(personal)
    speak = feature(0.22)
    wait = feature(personal, 0.24)
    from tongxi.e1 import relative_feature
    return {
        "listen": [relative_feature(still, feature(personal))] * 6,
        "speak": [relative_feature(speak, feature(personal))] * 6,
        "wait": [relative_feature(wait, feature(personal))] * 6,
    }


def frames_for(index, personal):
    frames = []
    cursor = 0
    while cursor < 12 * MINUTE:
        mouth = personal
        status, faces, shoulders = "ok", 1, True
        if 3 * MINUTE <= cursor < 3 * MINUTE + 1000 or 8 * MINUTE <= cursor < 8 * MINUTE + 1000:
            mouth = 0.30
        if cursor == 5 * MINUTE:
            status, faces, shoulders = "multi_face", 2, False
        frames.append({
            "frame_id": f"p{index}-{cursor}",
            "timestamp_ms": cursor,
            "raw": feature(mouth),
            "face_count": faces,
            "shoulder_valid": shoulders,
            "visibility_status": status,
            "seq": len(frames),
        })
        cursor += 1000
    for origin in (3 * MINUTE, 8 * MINUTE):
        for offset in range(0, 1000, 100):
            frames.append({
                "frame_id": f"p{index}-burst-{origin}-{offset}",
                "timestamp_ms": origin + offset,
                "raw": feature(0.30),
                "face_count": 1,
                "shoulder_valid": True,
                "visibility_status": "ok",
                "seq": len(frames),
            })
    return frames


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("artifacts/e1-rehearsal")
    store = ExperimentStore(root)
    room = "rehearsal"
    upload_log = []
    for index in range(8):
        member = f"drill-{index}"
        personal = 0.10 + index * 0.001
        store.add_calibration(room, member, {
            "session_role": "calibration",
            "calibration_id": f"cal-{index}",
            "duration_ms": 4000,
            "baseline": feature(personal),
            "samples": samples_for(personal),
        })
        batches = []
        chunk = []
        for frame in frames_for(index, personal):
            chunk.append(frame)
            if len(chunk) == 7:
                batches.append(chunk)
                chunk = []
        if chunk:
            batches.append(chunk)
        queue = UploadQueue()
        for number, group in enumerate(batches):
            queue.add({"batch_id": f"batch-{index:02d}-{number:04d}", "session_id": "test-1", "session_role": "test", "frames": group})
        lost = {"done": False}

        def send(batch, member=member, lost=lost):
            store.add_frames(room, member, batch)
            if not lost["done"] and batch["batch_id"].endswith("-0000"):
                lost["done"] = True
                raise TimeoutError("模拟响应丢失")

        try:
            queue.flush_once(send)
        except TimeoutError:
            upload_log.append({"participant": index, "event": "response_lost_kept"})
        while queue.pending:
            queue.flush_once(send)
        store.add_frames(room, member, {"batch_id": f"batch-{index:02d}-0000", "session_id": "test-1", "session_role": "test", "frames": batches[0]})
        store.add_label(room, member, {
            "action": "confirm_negative", "session_id": "test-1", "session_role": "test", "agenda_id": "agenda",
            "start_timestamp": 0, "end_timestamp": 3 * MINUTE, "review_anchor_ms": 3 * MINUTE,
        })
        store.add_label(room, member, {
            "action": "add_positive", "session_id": "test-1", "session_role": "test", "agenda_id": "agenda",
            "start_timestamp": 3 * MINUTE, "end_timestamp": 3 * MINUTE + 1000, "review_anchor_ms": 3 * MINUTE + 1000,
        })
        store.add_label(room, member, {
            "action": "confirm_negative", "session_id": "test-1", "session_role": "test", "agenda_id": "agenda",
            "start_timestamp": 6 * MINUTE, "end_timestamp": 9 * MINUTE, "review_anchor_ms": 9 * MINUTE,
        })
        store.add_label(room, member, {
            "action": "uncertain", "session_id": "test-1", "session_role": "test", "agenda_id": "agenda",
            "start_timestamp": 10 * MINUTE, "end_timestamp": 12 * MINUTE, "review_anchor_ms": 12 * MINUTE,
        })
        exported_now = store.export_room(room)
        participant_id = next(person["participant_id"] for person in exported_now["participants"] if person["calibration"]["calibration_id"] == f"cal-{index}")
        store.mark_speech(room, participant_id, {"session_id": "test-1", "agenda_id": "agenda", "t_speech_ms": 3 * MINUTE + 400})
        integrity = store.integrity(room, member, "test-1")
        upload_log.append({"participant": index, "frames": integrity["frames"], "batches": len(integrity["batches"]), "pending": len(queue.pending)})
    exported = store.export_room(room)
    exported["synthetic"] = True
    exported["global_reference"] = DRILL_REFERENCE
    exported["global_reference_note"] = "预演夹具，不是真人通用参照"
    result = evaluate(replay(exported))
    gate = engineering_gate(result["micro"]["A"], result["micro"]["C"], result["label_coverage"])
    report = {
        "human_evidence": False,
        "claim_level": result["claim_level"],
        "global_reference_ready": exported["global_reference_ready"],
        "participants": len(exported["participants"]),
        "frame_counts": [item["frames"] for item in upload_log if "frames" in item],
        "upload_log": upload_log,
        "label_coverage": result["label_coverage"],
        "unlabeled_ratio": result["unlabeled_ratio"],
        "micro": result["micro"],
        "missing": result["missing"],
        "distribution_recall_C": result["distribution"]["C"]["recall"],
        "gate": gate,
    }
    (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    checks = [
        report["human_evidence"] is False,
        report["global_reference_ready"] is False,
        report["participants"] == 8,
        all(count == report["frame_counts"][0] for count in report["frame_counts"]),
        all(item.get("pending", 0) == 0 for item in upload_log if "pending" in item),
        "m0" not in json.dumps(exported["participants"]),
    ]
    if not all(checks):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
