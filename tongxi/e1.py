"""E1 采集和离线对照。

默认不参与会议。只有环境变量 TONGXI_E1=1 时，接口才接受记录。
识别阈值与 Phase 2B 相同，回放不得改它们。
匹配窗口在看到任何参与者结果之前冻结。

没有按键不再写成 Negative。Negative 只来自参与者对一段具体时间的明确确认。
回顾补充的 Positive 是一个时间点，不是一段最长三分钟的区间。
同一时刻不能既是 Positive 又是 Negative。优先级是 Positive、Unknown、Negative。
V2.1 的停止比较仍是：C 的误报没有低于 A，或 Positive 检出率低超过 10 个百分点。
误报改为“已确认没有表达意愿的区间里的提醒”，未标注区间单独计数。
比较只在明确标签盖住至少一半有效观察时间时执行。这是执行条件，不是新的成绩线。
通用参照使用第一次讨论的个人坐姿中位数，再取参与者之间的中位数。
applied_to_product 永远为 false。
"""
import hashlib
import json
import os
import secrets
import sqlite3
import statistics
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

MOUTH_THRESHOLD = 0.55
DISTANCE_MAX = 1.0
MARGIN = 0.25
WINDOW_MIN = 6
WINDOW_LIMIT = 8
L3_CONSISTENT_FRAMES = 4
L3_CONSISTENT_MS = 400
L3_MAX_MS = 2000
MATCH_LEAD_MS = 2000
MATCH_LAG_MS = 1500
SCALE_DENOM = 0.18
MOUTH_DENOM = 0.12
PITCH_DENOM = 0.2
TURN_DENOM = 0.2
SAMPLE_INTERVAL_MS = 250
SAMPLE_MAX_MS = 4000
OBSERVATION_GAP_MS = 2000
WINDOW_GAP_MS = L3_CONSISTENT_MS
REVIEW_MS = 180000
# 执行 V2.1 比较之前的覆盖条件。不是准确率目标，写在看到预演结果之前。
LABEL_COVERAGE_MIN = 0.5
BASELINE_PROTOCOL = "neutral_pose_median_v1"
BASELINE_MS = 8000
BASELINE_INTERVAL_MS = 250
BASELINE_TARGET = 32
BASELINE_MIN_SAMPLES = 30
BASELINE_MIN_VALID = 24
BASELINE_MIN_MS = 7500
BASELINE_MAX_MS = 10000
BASELINE_MAX_ATTEMPT = 2
REFERENCE_METHOD = "cohort_neutral_baseline_median_v1"
REFERENCE_VERSION = "1.0"
REFERENCE_METHODS = (REFERENCE_METHOD,)
COHORT_MIN = 8
COHORT_MAX = 12
AXES = ("scale", "mouth", "pitch", "turn")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS people (
  member_id TEXT PRIMARY KEY,
  participant_id TEXT UNIQUE,
  withdrawn INTEGER DEFAULT 0,
  baseline_failed INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS baseline_attempts (
  attempt_id TEXT PRIMARY KEY,
  member_id TEXT,
  attempt_no INTEGER,
  status TEXT,
  started_at TEXT,
  finished_at TEXT,
  valid_frames INTEGER,
  total_frames INTEGER,
  failure_reason TEXT,
  calibration_id TEXT,
  log TEXT
);
CREATE TABLE IF NOT EXISTS calibrations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  member_id TEXT,
  calibration_id TEXT,
  duration_ms INTEGER,
  baseline TEXT,
  samples TEXT,
  locked INTEGER DEFAULT 0,
  active INTEGER DEFAULT 0,
  version TEXT,
  sha256 TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
  member_id TEXT,
  session_id TEXT,
  session_role TEXT,
  video_width INTEGER,
  video_height INTEGER,
  setup_confirmed INTEGER DEFAULT 0,
  protocol_deviation INTEGER DEFAULT 0,
  PRIMARY KEY (member_id, session_id)
);
CREATE TABLE IF NOT EXISTS batches (
  batch_id TEXT PRIMARY KEY,
  member_id TEXT,
  session_id TEXT
);
CREATE TABLE IF NOT EXISTS frames (
  frame_id TEXT PRIMARY KEY,
  batch_id TEXT,
  member_id TEXT,
  session_id TEXT,
  timestamp_ms INTEGER,
  seq INTEGER,
  payload TEXT
);
CREATE TABLE IF NOT EXISTS labels (
  id TEXT PRIMARY KEY,
  member_id TEXT,
  session_id TEXT,
  agenda_id TEXT,
  start_ms INTEGER,
  end_ms INTEGER,
  label TEXT,
  label_source TEXT,
  confirmation_status TEXT,
  intent_status TEXT,
  window_anchor INTEGER,
  superseded INTEGER DEFAULT 0,
  revised_from TEXT,
  revised_at TEXT
);
CREATE TABLE IF NOT EXISTS speeches (
  id TEXT PRIMARY KEY,
  participant_id TEXT,
  session_id TEXT,
  agenda_id TEXT,
  t_speech_ms INTEGER
);
CREATE TABLE IF NOT EXISTS reference_row (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  payload TEXT
);
"""


def e1_enabled():
    return os.getenv("TONGXI_E1") == "1"


def e1_dir():
    configured = os.getenv("TONGXI_E1_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "06_实验数据" / "e1"


def relative_feature(raw, baseline):
    return [
        (raw["scale"] / baseline["scale"] - 1) / SCALE_DENOM,
        (raw["mouth"] - baseline["mouth"]) / MOUTH_DENOM,
        (raw["pitch"] - baseline["pitch"]) / PITCH_DENOM,
        (raw["turn"] - baseline["turn"]) / TURN_DENOM,
    ]


def describe(frames):
    if len(frames) < WINDOW_MIN or any(len(frame) != 4 or any(v != v or abs(v) == float("inf") for v in frame) for frame in frames):
        return None
    window = frames[-WINDOW_LIMIT:]
    mean = [sum(frame[index] for frame in window) / len(window) for index in range(4)]
    span = [max(frame[index] for frame in window) - min(frame[index] for frame in window) for index in range(4)]
    if any(value > 4 for value in span):
        return None
    movement = [window[-1][index] - window[0][index] for index in range(4)]
    return mean + movement


def fit_templates(samples):
    templates = {}
    for label in ("listen", "speak", "wait"):
        frames = samples.get(label) or []
        if len(frames) < WINDOW_MIN:
            continue
        descriptor = describe(frames[:WINDOW_LIMIT])
        if descriptor is not None:
            templates[label] = descriptor
    return templates


def template_sha(baseline, samples):
    payload = json.dumps({"baseline": baseline, "samples": samples}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def rank(templates, frames):
    descriptor = describe(frames)
    if descriptor is None or any(label not in templates for label in ("listen", "speak", "wait")):
        return None
    ranked = []
    for label, values in templates.items():
        distance = (sum((values[index] - descriptor[index]) ** 2 for index in range(len(values))) / len(values)) ** 0.5
        ranked.append({"label": label, "distance": distance})
    ranked.sort(key=lambda item: item["distance"])
    return ranked


def predict(templates, frames):
    ranked = rank(templates, frames)
    if not ranked or ranked[0]["distance"] > DISTANCE_MAX or ranked[1]["distance"] - ranked[0]["distance"] < MARGIN:
        return None
    return ranked[0]["label"]


class FeatureWindow:
    def __init__(self):
        self.frames = []

    def push(self, frame):
        self.frames.append(list(frame))
        if len(self.frames) > WINDOW_LIMIT:
            self.frames.pop(0)

    def clear(self):
        self.frames = []

    def ready(self):
        return len(self.frames) >= WINDOW_MIN

    def ordered(self):
        return [list(frame) for frame in self.frames]


class SenseSchedule:
    """与 vision-policy.js 的短时复核同一套数字。E1 回放不打开议题阶段切换。"""

    def __init__(self):
        self.level = "L1"
        self.decision = False
        self.mouth_high = 0
        self.l3_start = 0
        self.consistent = []

    def enter(self, level, now, reason):
        if self.level == level:
            return
        self.level = level
        if level == "L3":
            self.l3_start = now
            self.consistent = []

    def finish(self, now, reason):
        self.mouth_high = 0
        self.consistent = []
        self.enter("L2" if self.decision else "L1", now, reason)

    def observe(self, frame):
        invalid = frame["faces"] != 1 or not frame["shoulders"]
        if self.level == "L3" and (frame["elapsed"] > 200 or invalid or frame["now"] - self.l3_start >= L3_MAX_MS):
            self.finish(frame["now"], "invalid" if invalid else "slow" if frame["elapsed"] > 200 else "timeout")
            return {"notice": None, "unknown": True}
        if invalid:
            self.mouth_high = 0
            self.consistent = []
            return {"notice": None, "unknown": True}
        if self.level == "L1":
            self.mouth_high = self.mouth_high + 1 if frame["mouth"] > MOUTH_THRESHOLD else 0
            if self.mouth_high >= 2:
                self.enter("L3", frame["now"], "mouth")
        elif self.level == "L2" and frame.get("candidates"):
            best = frame["candidates"][0]
            if best["label"] in {"speak", "wait"} and best["distance"] <= DISTANCE_MAX:
                self.enter("L3", frame["now"], "candidate")
        if self.level != "L3":
            return {"notice": None, "unknown": False}
        if frame.get("predict") in {"speak", "wait"}:
            self.consistent.append({"at": frame["now"], "label": frame["predict"]})
            self.consistent = [item for item in self.consistent if frame["now"] - item["at"] <= L3_CONSISTENT_MS and item["label"] == frame["predict"]]
            if len(self.consistent) >= L3_CONSISTENT_FRAMES and frame.get("ready"):
                notice = frame["predict"]
                self.finish(frame["now"], "confirmed")
                return {"notice": notice, "unknown": False}
        else:
            self.consistent = []
        return {"notice": None, "unknown": False}


class UploadQueue:
    """待发送批次在服务端确认前一直留着。失败或响应丢失都不删。"""

    def __init__(self):
        self.pending = []
        self.acked_frames = 0

    def add(self, batch):
        self.pending.append(batch)

    def flush_once(self, send):
        if not self.pending:
            return "idle"
        batch = self.pending[0]
        send(batch)
        self.acked_frames += len(batch["frames"])
        self.pending.pop(0)
        return "acked"


def _valid(frame):
    return frame.get("visibility_status") == "ok" and frame.get("face_count") == 1 and frame.get("shoulder_valid") is True and frame.get("raw")


def _samples_complete(samples):
    return all(WINDOW_MIN <= len(samples.get(label) or []) <= WINDOW_LIMIT for label in ("listen", "speak", "wait"))


def _finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value and abs(value) != float("inf")


def _measurement_raw(item):
    if not isinstance(item, dict):
        return None
    if any(key in item for key in ("name", "image", "video", "audio", "jpeg", "png")):
        raise ValueError("基准采集不保存画面、录音或姓名")
    status = item.get("visibility_status", "ok")
    faces = item.get("face_count", 1)
    shoulders = item.get("shoulder_valid", True)
    raw = item.get("raw")
    if not isinstance(raw, dict):
        raw = item if "scale" in item else None
    if status != "ok" or faces != 1 or shoulders is not True or not isinstance(raw, dict):
        return None
    if not all(_finite_number(raw.get(key)) for key in AXES):
        return None
    return {key: float(raw[key]) for key in AXES}


def neutral_baseline(measurements, duration_ms, video_width, video_height, attempt=1):
    """四维原始值的中位数。不用上一份基准再归一化，也不拿最后一帧代替。"""
    if attempt not in (1, 2):
        return None
    if not isinstance(measurements, list) or not BASELINE_MIN_SAMPLES <= len(measurements) <= BASELINE_TARGET:
        return None
    valid = [raw for raw in (_measurement_raw(item) for item in measurements) if raw]
    if not BASELINE_MIN_VALID <= len(valid) <= BASELINE_TARGET:
        return None
    medians = {key: statistics.median(row[key] for row in valid) for key in AXES}
    mad = {key: statistics.median(abs(row[key] - medians[key]) for row in valid) for key in AXES}
    if not all(_finite_number(medians[key]) and _finite_number(mad[key]) for key in AXES):
        return None
    if isinstance(video_width, bool) or isinstance(video_height, bool):
        return None
    if not isinstance(video_width, int) or not isinstance(video_height, int) or video_width <= 0 or video_height <= 0:
        return None
    if not isinstance(duration_ms, int) or isinstance(duration_ms, bool) or not BASELINE_MIN_MS <= duration_ms <= BASELINE_MAX_MS:
        return None
    return {
        **medians,
        "mad": mad,
        "target_frames": BASELINE_TARGET,
        "sampled_frames": len(measurements),
        "valid_frames": len(valid),
        "valid_ratio": len(valid) / len(measurements),
        "duration_ms": duration_ms,
        "video_width": video_width,
        "video_height": video_height,
        "protocol": BASELINE_PROTOCOL,
        "attempt": attempt,
    }


def _baseline_usable(baseline):
    if not isinstance(baseline, dict) or baseline.get("protocol") != BASELINE_PROTOCOL:
        return False
    if baseline.get("valid_frames") is None or baseline.get("valid_frames") < BASELINE_MIN_VALID:
        return False
    if baseline.get("target_frames") != BASELINE_TARGET:
        return False
    mad = baseline.get("mad")
    if not isinstance(mad, dict):
        return False
    if not BASELINE_MIN_SAMPLES <= baseline.get("sampled_frames", 0) <= BASELINE_TARGET:
        return False
    if not BASELINE_MIN_MS <= baseline.get("duration_ms", 0) <= BASELINE_MAX_MS:
        return False
    if baseline.get("attempt") not in (1, 2):
        return False
    return all(_finite_number(baseline.get(key)) and _finite_number(mad.get(key)) for key in AXES)


def _baseline_values(baseline):
    if not isinstance(baseline, dict):
        return None
    if not all(_finite_number(baseline.get(key)) for key in AXES):
        return None
    return {key: baseline[key] for key in AXES}


def cohort_reference(baselines):
    """先每人一个中位数，再在人与人之间取中位数。帧数多的人不会因此更重。"""
    usable = [item for item in baselines if _baseline_values(item)]
    if not COHORT_MIN <= len(usable) <= COHORT_MAX:
        raise ValueError("通用参照需要 8 到 12 名完成第一次自然坐姿校准的参与者")
    return {key: statistics.median(item[key] for item in usable) for key in AXES}


def reference_digest(record):
    body = {
        "method": record.get("method"),
        "version": record.get("version"),
        "participant_count": record.get("participant_count"),
        "values": record.get("values"),
        "statistic": record.get("statistic"),
        "capture_protocol": record.get("capture_protocol"),
        "participant_ids": record.get("participant_ids"),
        "calibration_ids": record.get("calibration_ids"),
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _mouth_alerts(frames, mouth_of):
    streak = 0
    armed = False
    alerts = []
    for frame in frames:
        mouth = mouth_of(frame)
        if mouth is None:
            streak = 0
            armed = False
            continue
        if mouth > MOUTH_THRESHOLD:
            streak += 1
            if streak >= 2 and not armed:
                alerts.append({"timestamp_ms": frame["timestamp_ms"], "label": "speak"})
                armed = True
        else:
            streak = 0
            armed = False
    return alerts


def _personal_mouth(frame, baseline):
    if not _valid(frame) or not baseline:
        return None
    return relative_feature(frame["raw"], baseline)[1]


def _global_mouth(frame, reference):
    if not _valid(frame) or not reference:
        return None
    return relative_feature(frame["raw"], reference)[1]


def _break_window(window, schedule, now):
    window.clear()
    schedule.consistent = []
    if schedule.level == "L3":
        schedule.finish(now, "gap")


def _condition_c(frames, baseline, templates):
    window = FeatureWindow()
    schedule = SenseSchedule()
    alerts = []
    last_valid = None
    for frame in frames:
        elapsed = frame.get("elapsed_ms")
        if elapsed is None:
            elapsed = 20
        if not _valid(frame) or not baseline:
            _break_window(window, schedule, frame["timestamp_ms"])
            schedule.observe({
                "now": frame["timestamp_ms"], "elapsed": elapsed, "faces": frame.get("face_count") or 0,
                "shoulders": False, "mouth": 0, "predict": None, "ready": False, "candidates": None,
            })
            last_valid = None
            continue
        if last_valid is not None and frame["timestamp_ms"] - last_valid > WINDOW_GAP_MS:
            _break_window(window, schedule, frame["timestamp_ms"])
        last_valid = frame["timestamp_ms"]
        feature = relative_feature(frame["raw"], baseline)
        window.push(feature)
        ready = window.ready()
        ordered = window.ordered() if ready else None
        label = predict(templates, ordered) if ordered else None
        ranked = rank(templates, ordered) if ordered else None
        outcome = schedule.observe({
            "now": frame["timestamp_ms"], "elapsed": elapsed, "faces": 1, "shoulders": True,
            "mouth": feature[1], "predict": label, "ready": ready, "candidates": ranked,
        })
        if outcome["notice"] in {"speak", "wait"}:
            alerts.append({"timestamp_ms": frame["timestamp_ms"], "label": outcome["notice"]})
    return alerts


def _reference_values(exported):
    ref = exported.get("global_reference")
    if not ref:
        raise ValueError("条件 A 需要事先写好的通用参照，不能用测试讨论估计")
    if isinstance(ref, dict) and "scale" in ref:
        if not exported.get("synthetic"):
            raise ValueError("通用参照的采集方法尚未确定，不能用于真人对照")
        return ref
    values = ref.get("values")
    if not exported.get("synthetic") and not ref.get("ready"):
        raise ValueError("通用参照尚未就绪，不能开始真人对照")
    if not values:
        raise ValueError("条件 A 需要事先写好的通用参照，不能用测试讨论估计")
    return values


def _explicit_labels(session):
    labels = session.get("labels") or []
    if not labels:
        labels = []
        for intent in session.get("intents") or []:
            if intent.get("intent_status") == "deleted" or intent.get("status") == "deleted":
                continue
            if intent.get("label_source") != "button":
                continue
            pressed = intent.get("pressed_at_ms", intent.get("start_ms"))
            labels.append({
                "id": intent.get("id"),
                "start_ms": pressed,
                "end_ms": intent.get("end_ms", pressed),
                "label": "positive",
                "label_source": "button",
                "confirmation_status": "confirmed",
            })
    positives, negatives, uncertain = [], [], []
    for item in labels:
        if item.get("intent_status") == "deleted" or item.get("confirmation_status") == "deleted" or item.get("superseded"):
            continue
        normalized = {
            "id": item.get("id"),
            "start_ms": item.get("start_ms", item.get("start_timestamp", item.get("pressed_at_ms"))),
            "end_ms": item.get("end_ms", item.get("end_timestamp", item.get("pressed_at_ms"))),
            "label": item.get("label"),
            "label_source": item.get("label_source"),
            "confirmation_status": item.get("confirmation_status") or "confirmed",
        }
        if normalized["start_ms"] is None or normalized["end_ms"] is None:
            continue
        if normalized["label"] == "positive" and normalized["confirmation_status"] == "confirmed":
            positives.append(normalized)
        elif normalized["label"] == "negative" and normalized["label_source"] == "explicit_negative_review" and normalized["confirmation_status"] == "confirmed":
            negatives.append(normalized)
        elif normalized["label"] == "unknown":
            uncertain.append(normalized)
    return positives, negatives, uncertain


def _observation(frames):
    ordered = sorted(frames, key=lambda frame: frame["timestamp_ms"])
    valid_ms = 0
    segments = []
    for prev, nxt in zip(ordered, ordered[1:]):
        if not _valid(prev) or not _valid(nxt):
            continue
        gap = nxt["timestamp_ms"] - prev["timestamp_ms"]
        if gap <= 0 or gap > OBSERVATION_GAP_MS:
            continue
        valid_ms += gap
        segments.append((prev["timestamp_ms"], nxt["timestamp_ms"]))
    return valid_ms, segments


def _covered_ms(segments, spans):
    total = 0
    for start, end in segments:
        pieces = []
        for span_start, span_end in spans:
            lo, hi = max(start, span_start), min(end, span_end)
            if hi > lo:
                pieces.append((lo, hi))
        pieces.sort()
        cursor = None
        for lo, hi in pieces:
            if cursor is None:
                cursor = [lo, hi]
            elif lo <= cursor[1]:
                cursor[1] = max(cursor[1], hi)
            else:
                total += cursor[1] - cursor[0]
                cursor = [lo, hi]
        if cursor:
            total += cursor[1] - cursor[0]
    return total


def _carve_spans(spans, cuts):
    remaining = []
    for start, end in spans:
        pieces = [(start, end)]
        for cut_start, cut_end in cuts:
            nxt = []
            for lo, hi in pieces:
                if cut_end <= lo or cut_start >= hi:
                    nxt.append((lo, hi))
                    continue
                if lo < cut_start:
                    nxt.append((lo, cut_start))
                if cut_end < hi:
                    nxt.append((cut_end, hi))
            pieces = [(lo, hi) for lo, hi in nxt if hi > lo]
        remaining.extend(pieces)
    return remaining


def _positive_windows(positives):
    return [(item["start_ms"] - MATCH_LEAD_MS, item["end_ms"] + MATCH_LAG_MS) for item in positives]


def _resolved_negative_items(positives, negatives, uncertain):
    """Positive 的匹配窗口和 Unknown 区间从 Negative 里挖掉。剩下的未确认时间仍是 Unknown。"""
    spans = _carve_spans([(item["start_ms"], item["end_ms"]) for item in negatives], _positive_windows(positives))
    spans = _carve_spans(spans, [(item["start_ms"], item["end_ms"]) for item in uncertain])
    return [{
        "start_ms": start,
        "end_ms": end,
        "label": "negative",
        "label_source": "explicit_negative_review",
        "confirmation_status": "confirmed",
    } for start, end in spans if end > start]


def _label_spans(positives, negatives, uncertain):
    spans = [(item["start_ms"] - MATCH_LEAD_MS, item["end_ms"] + MATCH_LAG_MS) for item in positives]
    spans.extend((item["start_ms"], item["end_ms"]) for item in negatives)
    spans.extend((item["start_ms"], item["end_ms"]) for item in uncertain)
    return spans


def _negative_ms(negatives, segments):
    return _covered_ms(segments, [(item["start_ms"], item["end_ms"]) for item in negatives])


def score_alerts(alerts, positives, negatives, negative_ms):
    windows = []
    for item in positives:
        windows.append({
            "start": item["start_ms"] - MATCH_LEAD_MS,
            "end": item["end_ms"] + MATCH_LAG_MS,
            "anchor": (item["start_ms"] + item["end_ms"]) / 2,
        })
    pairs = []
    for index, alert in enumerate(alerts):
        for window_index, window in enumerate(windows):
            if window["start"] <= alert["timestamp_ms"] <= window["end"]:
                delta = alert["timestamp_ms"] - window["anchor"]
                pairs.append((abs(delta), index, window_index, delta))
    pairs.sort()
    used_alerts, used_windows = set(), set()
    matched = []
    for _, index, window_index, delta in pairs:
        if index in used_alerts or window_index in used_windows:
            continue
        used_alerts.add(index)
        used_windows.add(window_index)
        matched.append({"delta_ms": delta, "timestamp_ms": alerts[index]["timestamp_ms"], "label": alerts[index].get("label")})
    duplicates = []
    confirmed = []
    unverified = []
    for index, alert in enumerate(alerts):
        if index in used_alerts:
            continue
        inside_positive = any(window["start"] <= alert["timestamp_ms"] <= window["end"] for window in windows)
        if inside_positive:
            duplicates.append(alert)
            continue
        if any(item["start_ms"] <= alert["timestamp_ms"] <= item["end_ms"] for item in negatives):
            confirmed.append(alert)
        else:
            unverified.append(alert)
    precision_den = len(matched) + len(confirmed)
    precision = len(matched) / precision_den if precision_den else None
    recall = len(matched) / len(positives) if positives else None
    if precision is None or recall is None or precision + recall == 0:
        f1 = None if precision is None or recall is None else 0.0
    else:
        f1 = 2 * precision * recall / (precision + recall)
    rate = (len(confirmed) / negative_ms * 600_000) if negative_ms else None
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "matched": len(matched),
        "positives_found": len(matched),
        "confirmed_false_alarms": len(confirmed),
        "confirmed_false_alarms_per_10_min": rate,
        "unverified_alerts": len(unverified),
        "duplicate_alerts": len(duplicates),
        "deltas": matched,
        "unknown_in_scores": False,
    }


def replay(exported):
    reference = _reference_values(exported)
    participants = []
    for person in exported.get("participants") or []:
        calibration = person.get("calibration") or {}
        if calibration.get("session_role") == "test":
            raise ValueError("第二次讨论不能用来建立个人基准或样例")
        if any(session.get("session_role") != "test" for session in person.get("sessions") or []) and calibration.get("samples") is None:
            raise ValueError("第一次讨论只建立基准，不能把同一次记录同时当作测试")
        baseline = calibration.get("baseline")
        templates = fit_templates(calibration.get("samples") or {})
        sessions = []
        alerts = {"A": [], "B": [], "C": []}
        for session in person.get("sessions") or []:
            if session.get("session_role") != "test":
                sessions.append(session)
                continue
            frames_in = sorted(session.get("frames") or [], key=lambda frame: (frame["timestamp_ms"], frame.get("seq") or 0))
            alerts_a = _mouth_alerts(frames_in, lambda frame: _global_mouth(frame, reference))
            alerts_b = _mouth_alerts(frames_in, lambda frame: _personal_mouth(frame, baseline))
            alerts_c = _condition_c(frames_in, baseline, templates)
            alerts["A"].extend(alerts_a)
            alerts["B"].extend(alerts_b)
            alerts["C"].extend(alerts_c)
            marked = {
                "A": {item["timestamp_ms"] for item in alerts_a},
                "B": {item["timestamp_ms"] for item in alerts_b},
                "C": {item["timestamp_ms"] for item in alerts_c},
            }
            positives, negatives, uncertain = _explicit_labels(session)
            resolved_negatives = _resolved_negative_items(positives, negatives, uncertain)
            copied_frames = []
            for frame in frames_in:
                copied = dict(frame)
                copied["participant_id"] = person.get("participant_id")
                copied["session_id"] = session.get("session_id")
                copied["timestamp"] = frame["timestamp_ms"]
                copied["prediction_A"] = "speak" if frame["timestamp_ms"] in marked["A"] else None
                copied["prediction_B"] = "speak" if frame["timestamp_ms"] in marked["B"] else None
                copied["prediction_C"] = "speak" if frame["timestamp_ms"] in marked["C"] else None
                copied["feature_vector"] = relative_feature(frame["raw"], baseline) if _valid(frame) and baseline else None
                if not _valid(frame):
                    copied["label"] = "unknown"
                    copied["label_source"] = "system_unknown"
                    copied["confirmation_status"] = "unconfirmed"
                    copied["unknown_reason"] = frame.get("visibility_status")
                elif any(item["start_ms"] <= frame["timestamp_ms"] <= item["end_ms"] for item in resolved_negatives):
                    copied["label"] = "negative"
                    copied["label_source"] = "explicit_negative_review"
                    copied["confirmation_status"] = "confirmed"
                    copied["unknown_reason"] = None
                else:
                    copied["label"] = "unknown"
                    copied["label_source"] = "system_unknown"
                    copied["confirmation_status"] = "unconfirmed"
                    copied["unknown_reason"] = "unlabeled"
                copied["t_intent"] = None
                copied["t_speech"] = None
                copied["t_alert"] = frame["timestamp_ms"] if any(copied[key] for key in ("prediction_A", "prediction_B", "prediction_C")) else None
                copied["calibration_id"] = calibration.get("calibration_id")
                copied_frames.append(copied)
            sessions.append({
                "session_id": session.get("session_id"),
                "session_role": "test",
                "frames": copied_frames,
                "intents": list(session.get("intents") or []),
                "labels": list(session.get("labels") or []),
                "speeches": list(session.get("speeches") or []),
            })
        participants.append({
            "participant_id": person.get("participant_id"),
            "sessions": sessions,
            "alerts": alerts,
            "calibration_ms": calibration.get("duration_ms"),
            "withdrawn": bool(person.get("withdrawn")),
        })
    return {
        "synthetic": bool(exported.get("synthetic")),
        "human_pilot_complete": False,
        "global_reference_ready": bool(exported.get("global_reference_ready")),
        "participants": participants,
    }


def _person_metrics(person, alerts_override=None):
    test_sessions = [session for session in person.get("sessions") or [] if session.get("session_role") == "test"]
    frames = [frame for session in test_sessions for frame in session.get("frames") or []]
    positives, negatives, uncertain = [], [], []
    for session in test_sessions:
        found_pos, found_neg, found_unknown = _explicit_labels(session)
        positives.extend(found_pos)
        negatives.extend(found_neg)
        uncertain.extend(found_unknown)
    unknown_frames = [frame for frame in frames if not _valid(frame)]
    valid_ms, segments = _observation(frames)
    labeled_ms = _covered_ms(segments, _label_spans(positives, negatives, uncertain))
    resolved_negatives = _resolved_negative_items(positives, negatives, uncertain)
    negative_ms = _negative_ms(resolved_negatives, segments)
    coverage = (labeled_ms / valid_ms) if valid_ms else None
    chosen = alerts_override if alerts_override is not None else person.get("alerts") or {}
    metrics = {
        "unknown_count": len(unknown_frames),
        "unknown_ratio": (len(unknown_frames) / len(frames) if frames else None),
        "positive_events": len(positives),
        "negative_intervals": len(negatives),
        "uncertain_intervals": len(uncertain),
        "valid_ms": valid_ms,
        "negative_ms": negative_ms,
        "label_coverage": coverage,
        "unlabeled_ratio": (None if coverage is None else 1 - coverage),
        "calibration_ms": person.get("calibration_ms"),
    }
    for condition in ("A", "B", "C"):
        metrics[condition] = score_alerts(chosen.get(condition) or [], positives, resolved_negatives, negative_ms)
    missing = []
    if person.get("withdrawn"):
        missing.append("withdrawn")
    if not test_sessions:
        missing.append("no_test_session")
    if not positives:
        missing.append("no_positive")
    if not negatives:
        missing.append("no_confirmed_negative")
    if any(session.get("protocol_deviation") for session in test_sessions):
        missing.append("protocol_deviation")
    return metrics, missing


def _numbers(people, condition, key):
    values = [person["metrics"][condition][key] for person in people if person["metrics"][condition].get(key) is not None]
    return {
        "n": len(values),
        "mean": statistics.fmean(values) if values else None,
        "median": statistics.median(values) if values else None,
        "values": values,
    }


def evaluate(replayed, alerts=None):
    people = []
    for person in replayed.get("participants") or []:
        metrics, missing = _person_metrics(person, alerts if alerts is not None else None)
        people.append({
            "participant_id": person.get("participant_id"),
            "metrics": metrics,
            "missing": missing,
            "calibration_ms": person.get("calibration_ms"),
        })
    counted = [person for person in people if "protocol_deviation" not in person["missing"]]
    micro = {}
    for condition in ("A", "B", "C"):
        matched = sum(person["metrics"][condition]["matched"] for person in counted)
        confirmed = sum(person["metrics"][condition]["confirmed_false_alarms"] for person in counted)
        positives = sum(person["metrics"]["positive_events"] for person in counted)
        negative_ms = sum(person["metrics"]["negative_ms"] for person in counted)
        precision_den = matched + confirmed
        precision = matched / precision_den if precision_den else None
        recall = matched / positives if positives else None
        if precision is None or recall is None or precision + recall == 0:
            f1 = None if precision is None or recall is None else 0.0
        else:
            f1 = 2 * precision * recall / (precision + recall)
        micro[condition] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "matched": matched,
            "confirmed_false_alarms": confirmed,
            "confirmed_false_alarms_per_10_min": (confirmed / negative_ms * 600_000) if negative_ms else None,
            "unverified_alerts": sum(person["metrics"][condition]["unverified_alerts"] for person in counted),
            "duplicate_alerts": sum(person["metrics"][condition]["duplicate_alerts"] for person in counted),
            "unknown_in_scores": False,
        }
    valid_ms = sum(person["metrics"]["valid_ms"] for person in counted)
    labeled_ms = sum((person["metrics"]["label_coverage"] or 0) * person["metrics"]["valid_ms"] for person in counted)
    coverage = (labeled_ms / valid_ms) if valid_ms else None
    claim = "synthetic_tool_check" if replayed.get("synthetic") else "recorded_not_a_finding"
    return {
        "participants": people,
        "micro": micro,
        "distribution": {
            condition: {
                "recall": _numbers(counted, condition, "recall"),
                "precision": _numbers(counted, condition, "precision"),
                "confirmed_false_alarms_per_10_min": _numbers(counted, condition, "confirmed_false_alarms_per_10_min"),
            }
            for condition in ("A", "B", "C")
        },
        "label_coverage": coverage,
        "unlabeled_ratio": None if coverage is None else 1 - coverage,
        "missing": [person["participant_id"] for person in people if person["missing"]],
        "human_evidence": False,
        "claim_level": claim,
        "global_reference_ready": bool(replayed.get("global_reference_ready")),
    }


def engineering_gate(condition_a, condition_c, label_coverage=None):
    if label_coverage is not None and label_coverage < LABEL_COVERAGE_MIN:
        return {
            "decision": "insufficient",
            "remove_automatic_vision": False,
            "applied_to_product": False,
            "reasons": ["明确标签覆盖的有效观察时间不足一半，不执行门槛比较。未标注区间没有被当成负样本。"],
        }
    needed = ("confirmed_false_alarms_per_10_min", "recall")
    if any(condition_a.get(key) is None or condition_c.get(key) is None for key in needed):
        return {
            "decision": "insufficient",
            "remove_automatic_vision": False,
            "applied_to_product": False,
            "reasons": ["确认负向区间或 Positive 事件不足，不能判定门槛，也不能改阈值。"],
        }
    reasons = []
    if not condition_c["confirmed_false_alarms_per_10_min"] < condition_a["confirmed_false_alarms_per_10_min"]:
        reasons.append("C 的确认误报率没有低于 A")
    if condition_a["recall"] - condition_c["recall"] > 0.10:
        reasons.append("C 的 Positive 检出率比 A 低超过 10 个百分点")
    return {
        "decision": "stop_automatic" if reasons else "continue",
        "remove_automatic_vision": bool(reasons),
        "applied_to_product": False,
        "reasons": reasons,
    }


def reference_record(values, source, version):
    if not source or not version:
        raise ValueError("参照必须写明来源和版本")
    for key in ("scale", "mouth", "pitch", "turn"):
        if not isinstance(values.get(key), (int, float)):
            raise ValueError("参照需要肩宽、嘴部、俯仰、转向四个原始数")
    body = {"values": {key: values[key] for key in ("scale", "mouth", "pitch", "turn")}, "source": source, "version": version}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    method = body["source"]
    return {
        **body,
        "method": method,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sha256": digest,
        "frozen": False,
        "ready": False,
    }


class ExperimentStore:
    def __init__(self, root=None):
        self.root = Path(root) if root else e1_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self._guard = threading.Lock()

    def _file(self, room):
        return self.root / f"{room}.sqlite"

    def _json_file(self, room):
        return self.root / f"{room}.json"

    def _connect(self, room):
        conn = sqlite3.connect(self._file(room), timeout=5, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _prepare(self, conn, room):
        conn.executescript(SCHEMA)
        self._migrate(conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT value FROM meta WHERE key='migrated'").fetchone()
            if not row:
                legacy = self._json_file(room)
                if legacy.exists():
                    self._import_json(conn, json.loads(legacy.read_text(encoding="utf-8")))
                conn.execute("INSERT INTO meta(key, value) VALUES('migrated', '1')")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def _migrate(self, conn):
        def names(table):
            return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}

        for name, typedef in (
            ("window_anchor", "INTEGER"),
            ("superseded", "INTEGER DEFAULT 0"),
            ("revised_from", "TEXT"),
            ("revised_at", "TEXT"),
        ):
            if name not in names("labels"):
                conn.execute(f"ALTER TABLE labels ADD COLUMN {name} {typedef}")
        if "baseline_failed" not in names("people"):
            conn.execute("ALTER TABLE people ADD COLUMN baseline_failed INTEGER DEFAULT 0")
        for name, typedef in (
            ("video_width", "INTEGER"),
            ("video_height", "INTEGER"),
            ("setup_confirmed", "INTEGER DEFAULT 0"),
            ("protocol_deviation", "INTEGER DEFAULT 0"),
        ):
            if name not in names("sessions"):
                conn.execute(f"ALTER TABLE sessions ADD COLUMN {name} {typedef}")

    def _import_json(self, conn, document):
        people = document.get("participants") or {}
        if isinstance(people, list):
            return
        for member_id, person in people.items():
            conn.execute(
                "INSERT OR IGNORE INTO people(member_id, participant_id, withdrawn) VALUES (?, ?, 0)",
                (member_id, person.get("participant_id") or ("p" + secrets.token_hex(4))),
            )
            calibration = person.get("calibration")
            if calibration:
                samples = calibration.get("samples") or {}
                locked = 1 if _samples_complete(samples) else 0
                conn.execute(
                    """INSERT INTO calibrations(member_id, calibration_id, duration_ms, baseline, samples, locked, active, version, sha256)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (member_id, calibration.get("calibration_id"), calibration.get("duration_ms"),
                     json.dumps(calibration.get("baseline"), ensure_ascii=False), json.dumps(samples, ensure_ascii=False),
                     locked, locked, "legacy", template_sha(calibration.get("baseline"), samples) if locked else None),
                )
            for session in (person.get("sessions") or {}).values():
                conn.execute(
                    "INSERT OR IGNORE INTO sessions(member_id, session_id, session_role) VALUES (?, ?, ?)",
                    (member_id, session["session_id"], session["session_role"]),
                )
                for index, frame in enumerate(session.get("frames") or []):
                    frame_id = frame.get("frame_id") or f"legacy-{member_id}-{session['session_id']}-{index}"
                    conn.execute(
                        "INSERT OR IGNORE INTO frames(frame_id, batch_id, member_id, session_id, timestamp_ms, seq, payload) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (frame_id, frame.get("batch_id") or f"legacy-{session['session_id']}", member_id, session["session_id"],
                         frame["timestamp_ms"], index, json.dumps(frame, ensure_ascii=False)),
                    )
                for intent in session.get("intents") or []:
                    pressed = intent["pressed_at_ms"]
                    conn.execute(
                        """INSERT OR IGNORE INTO labels(id, member_id, session_id, agenda_id, start_ms, end_ms, label, label_source, confirmation_status, intent_status)
                           VALUES (?, ?, ?, ?, ?, ?, 'positive', 'button', ?, ?)""",
                        (intent["id"], member_id, session["session_id"], intent.get("agenda_id"), pressed, pressed,
                         "deleted" if intent.get("status") == "deleted" else "confirmed", intent.get("status") or "confirmed"),
                    )

    @contextmanager
    def _tx(self, room):
        with self._guard:
            conn = self._connect(room)
        try:
            self._prepare(conn, room)
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    def _person(self, conn, member_id):
        row = conn.execute("SELECT * FROM people WHERE member_id=?", (member_id,)).fetchone()
        if row:
            if row["withdrawn"]:
                raise ValueError("该参与者已退出实验，不再写入")
            return row
        for _ in range(6):
            participant_id = "p" + secrets.token_hex(4)
            try:
                conn.execute("INSERT INTO people(member_id, participant_id) VALUES (?, ?)", (member_id, participant_id))
                break
            except sqlite3.IntegrityError:
                continue
        else:
            raise ValueError("研究编号没有生成")
        return conn.execute("SELECT * FROM people WHERE member_id=?", (member_id,)).fetchone()

    def _session(self, conn, member_id, session_id, session_role, body=None):
        if session_role not in {"calibration", "test"}:
            raise ValueError("讨论只能是第一次建立基准，或第二次独立测试")
        if not session_id:
            raise ValueError("需要场次编号")
        if session_role == "test":
            reference = self._reference(conn)
            if not reference or not reference.get("ready"):
                raise ValueError("通用参照尚未冻结，不能开始第二次讨论")
        conn.execute(
            "INSERT OR IGNORE INTO sessions(member_id, session_id, session_role) VALUES (?, ?, ?)",
            (member_id, session_id, session_role),
        )
        row = conn.execute(
            "SELECT * FROM sessions WHERE member_id=? AND session_id=?",
            (member_id, session_id),
        ).fetchone()
        if row["session_role"] != session_role:
            raise ValueError("同一场次不能改成另一种讨论")
        if session_role == "test":
            self._mark_setup(conn, member_id, session_id, body or {})
            row = conn.execute(
                "SELECT * FROM sessions WHERE member_id=? AND session_id=?",
                (member_id, session_id),
            ).fetchone()
        return row

    def _mark_setup(self, conn, member_id, session_id, body):
        active = conn.execute(
            "SELECT baseline FROM calibrations WHERE member_id=? AND active=1",
            (member_id,),
        ).fetchone()
        baseline = json.loads(active["baseline"]) if active and active["baseline"] else {}
        width = body.get("video_width")
        height = body.get("video_height")
        same = body.get("same_device") is True
        size_matches = (
            isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0
            and width == baseline.get("video_width") and height == baseline.get("video_height")
        )
        confirmed = 1 if same and size_matches else 0
        conn.execute(
            """UPDATE sessions SET video_width=?, video_height=?, setup_confirmed=?, protocol_deviation=?
               WHERE member_id=? AND session_id=?""",
            (width if isinstance(width, int) else None, height if isinstance(height, int) else None,
             confirmed, 0 if confirmed else 1, member_id, session_id),
        )

    def add_intent(self, room, member_id, body):
        if body.get("label_source", "button") != "button":
            raise ValueError("表达意愿按钮只能记录当时按下的时间，回顾补充要走单独确认")
        pressed = body.get("pressed_at_ms")
        if not isinstance(pressed, int):
            raise ValueError("需要毫秒时间戳")
        with self._tx(room) as conn:
            person = self._person(conn, member_id)
            self._session(conn, member_id, str(body.get("session_id") or ""), body.get("session_role"), body)
            label_id = "i" + hashlib.sha256(f"{member_id}:{pressed}:{body.get('session_id')}".encode()).hexdigest()[:10]
            conn.execute(
                """INSERT OR IGNORE INTO labels(id, member_id, session_id, agenda_id, start_ms, end_ms, label, label_source, confirmation_status, intent_status)
                   VALUES (?, ?, ?, ?, ?, ?, 'positive', 'button', 'confirmed', 'confirmed')""",
                (label_id, member_id, str(body.get("session_id")), body.get("agenda_id"), pressed, pressed),
            )
        return {"id": label_id, "label": "positive", "label_source": "button", "participant_id": person["participant_id"], "pressed_at_ms": pressed, "confirmation_status": "confirmed"}

    def review_intent(self, room, member_id, intent_id, action, anchor=None):
        if action != "delete":
            raise ValueError("按钮回顾只能删除误触。补充意愿或确认没有意愿要用三分钟回顾。")
        with self._tx(room) as conn:
            row = conn.execute("SELECT * FROM labels WHERE id=? AND member_id=?", (intent_id, member_id)).fetchone()
            if not row or row["label_source"] != "button":
                raise ValueError("没有这条按键")
            if isinstance(anchor, int) and not (anchor - REVIEW_MS <= row["start_ms"] <= anchor + 2000):
                raise ValueError("这次回顾只能处理刚才三分钟里的按键")
            conn.execute("UPDATE labels SET intent_status='deleted', confirmation_status='deleted' WHERE id=?", (intent_id,))
            updated = conn.execute("SELECT * FROM labels WHERE id=?", (intent_id,)).fetchone()
        return {"id": updated["id"], "status": "deleted", "label_source": "button"}

    def add_label(self, room, member_id, body):
        action = body.get("action")
        if action not in {"add_positive", "confirm_negative", "uncertain"}:
            raise ValueError("回顾只能补充已确认的意愿、确认没有意愿，或标成无法确定")
        start, end = body.get("start_timestamp"), body.get("end_timestamp")
        anchor = body.get("review_anchor_ms")
        if not all(isinstance(value, int) for value in (start, end, anchor)):
            raise ValueError("回顾需要这段时间的起止和回顾时刻")
        if end < start or end - start > REVIEW_MS:
            raise ValueError("回顾区间不能长于三分钟")
        if start < anchor - REVIEW_MS or end > anchor + 2000:
            raise ValueError("回顾只能标刚刚这三分钟，不能补整场")
        if action == "add_positive" and start != end:
            raise ValueError("回顾补充的表达意愿只能是一个时间点，不能是一段时间")
        kind = {"add_positive": ("positive", "participant_review", "confirmed"),
                "confirm_negative": ("negative", "explicit_negative_review", "confirmed"),
                "uncertain": ("unknown", "participant_review", "uncertain")}[action]
        with self._tx(room) as conn:
            person = self._person(conn, member_id)
            session_id = str(body.get("session_id") or "")
            self._session(conn, member_id, session_id, body.get("session_role"), body)
            active = conn.execute(
                """SELECT * FROM labels
                   WHERE member_id=? AND session_id=? AND window_anchor=? AND IFNULL(superseded, 0)=0
                     AND label_source IN ('participant_review', 'explicit_negative_review')""",
                (member_id, session_id, anchor),
            ).fetchone()
            if active and active["label"] == kind[0] and active["start_ms"] == start and active["end_ms"] == end:
                label_id = active["id"]
            else:
                revised_from = None
                revised_at = None
                if active:
                    revised_at = datetime.now(timezone.utc).isoformat()
                    revised_from = active["id"]
                    conn.execute(
                        "UPDATE labels SET superseded=1, revised_at=? WHERE id=?",
                        (revised_at, active["id"]),
                    )
                count = conn.execute(
                    "SELECT COUNT(*) AS n FROM labels WHERE member_id=? AND session_id=? AND window_anchor=?",
                    (member_id, session_id, anchor),
                ).fetchone()["n"]
                label_id = "r" + hashlib.sha256(f"{member_id}:{session_id}:{anchor}:{count}:{action}:{start}:{end}".encode()).hexdigest()[:12]
                conn.execute(
                    """INSERT INTO labels(id, member_id, session_id, agenda_id, start_ms, end_ms, label, label_source, confirmation_status, intent_status, window_anchor, superseded, revised_from, revised_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?, 0, ?, ?)""",
                    (label_id, member_id, session_id, body.get("agenda_id"), start, end, *kind, anchor, revised_from, revised_at),
                )
        return {
            "id": label_id,
            "participant_id": person["participant_id"],
            "label": kind[0],
            "label_source": kind[1],
            "confirmation_status": kind[2],
            "start_timestamp": start,
            "end_timestamp": end,
        }

    def add_frames(self, room, member_id, body):
        batch_id = body.get("batch_id")
        if not isinstance(batch_id, str) or len(batch_id) < 8:
            raise ValueError("每一批上传都要有稳定的批次编号")
        incoming = body.get("frames") or []
        with self._tx(room) as conn:
            person = self._person(conn, member_id)
            session_id = str(body.get("session_id") or "")
            self._session(conn, member_id, session_id, body.get("session_role"), body)
            existing = conn.execute("SELECT batch_id FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
            if existing:
                stored = conn.execute("SELECT COUNT(*) AS n FROM frames WHERE member_id=? AND session_id=?", (member_id, session_id)).fetchone()["n"]
                return {"duplicate": True, "accepted": 0, "stored": stored, "participant_id": person["participant_id"]}
            accepted = 0
            for seq, frame in enumerate(incoming):
                if any(key in frame for key in ("name", "image", "video", "audio", "jpeg", "png")):
                    raise ValueError("实验记录只收特征和按键，不收画面、录音或姓名")
                if not isinstance(frame.get("timestamp_ms"), int):
                    raise ValueError("帧要保留原始毫秒时间")
                frame_id = frame.get("frame_id") or f"{batch_id}:{seq}"
                cursor = conn.execute(
                    """INSERT OR IGNORE INTO frames(frame_id, batch_id, member_id, session_id, timestamp_ms, seq, payload)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (frame_id, batch_id, member_id, session_id, frame["timestamp_ms"], seq,
                     json.dumps({
                         "timestamp_ms": frame["timestamp_ms"],
                         "raw": frame.get("raw"),
                         "face_count": frame.get("face_count"),
                         "shoulder_valid": frame.get("shoulder_valid"),
                         "visibility_status": frame.get("visibility_status") or "record_error",
                         "elapsed_ms": frame.get("elapsed_ms"),
                         "agenda_id": body.get("agenda_id"),
                         "seq": seq,
                         "frame_id": frame_id,
                     }, ensure_ascii=False)),
                )
                accepted += cursor.rowcount
            conn.execute("INSERT INTO batches(batch_id, member_id, session_id) VALUES (?, ?, ?)", (batch_id, member_id, session_id))
            stored = conn.execute("SELECT COUNT(*) AS n FROM frames WHERE member_id=? AND session_id=?", (member_id, session_id)).fetchone()["n"]
        return {"duplicate": False, "accepted": accepted, "stored": stored, "participant_id": person["participant_id"]}

    def _attempt_log(self, measurements):
        log = []
        for item in measurements or []:
            if not isinstance(item, dict):
                log.append({"visibility_status": "record_error"})
                continue
            if any(key in item for key in ("name", "image", "video", "audio", "jpeg", "png")):
                raise ValueError("基准采集不保存画面、录音或姓名")
            raw = item.get("raw") if isinstance(item.get("raw"), dict) else None
            log.append({
                "sampled_at_ms": item.get("sampled_at_ms"),
                "visibility_status": item.get("visibility_status"),
                "valid": _measurement_raw(item) is not None,
                "raw": {key: raw.get(key) for key in AXES} if raw else None,
            })
        return log

    def begin_baseline_attempt(self, room, member_id, body):
        if body.get("session_role") != "calibration":
            raise ValueError("第二次讨论不能重新采集自然坐姿")
        blocked = None
        ticket = None
        with self._tx(room) as conn:
            person = self._person(conn, member_id)
            if person["baseline_failed"]:
                blocked = "两次自然坐姿采集都已失败，不能继续纳入通用参照"
            else:
                active = conn.execute(
                    "SELECT locked FROM calibrations WHERE member_id=? AND active=1",
                    (member_id,),
                ).fetchone()
                succeeded = conn.execute(
                    "SELECT COUNT(*) AS n FROM baseline_attempts WHERE member_id=? AND status='succeeded'",
                    (member_id,),
                ).fetchone()["n"]
                used = conn.execute(
                    "SELECT COUNT(*) AS n FROM baseline_attempts WHERE member_id=?",
                    (member_id,),
                ).fetchone()["n"]
                if active and active["locked"]:
                    blocked = "个人基准和样例已经锁定，不能重新采集"
                elif succeeded:
                    blocked = "自然坐姿已经采集成功，不能再开始"
                elif used >= BASELINE_MAX_ATTEMPT:
                    conn.execute("UPDATE people SET baseline_failed=1 WHERE member_id=?", (member_id,))
                    blocked = "自然坐姿只允许两次尝试，刷新页面也不能重置"
                else:
                    attempt_no = used + 1
                    attempt_id = "ba" + secrets.token_hex(8)
                    conn.execute(
                        """INSERT INTO baseline_attempts(attempt_id, member_id, attempt_no, status, started_at, calibration_id)
                           VALUES (?, ?, ?, 'started', ?, ?)""",
                        (attempt_id, member_id, attempt_no, datetime.now(timezone.utc).isoformat(), str(body.get("calibration_id") or "")),
                    )
                    ticket = {"attempt_id": attempt_id, "attempt_no": attempt_no, "participant_id": person["participant_id"]}
        if blocked:
            raise ValueError(blocked)
        return ticket

    def _finish_attempt(self, conn, row, status, body, reason):
        measurements = body.get("baseline_measurements") or []
        valid = len([item for item in measurements if isinstance(item, dict) and _measurement_raw(item)])
        conn.execute(
            """UPDATE baseline_attempts
               SET status=?, finished_at=?, valid_frames=?, total_frames=?, failure_reason=?, log=?
               WHERE attempt_id=? AND status='started'""",
            (status, datetime.now(timezone.utc).isoformat(), valid, len(measurements), reason,
             json.dumps(self._attempt_log(measurements), ensure_ascii=False), row["attempt_id"]),
        )
        if status == "failed":
            failed = conn.execute(
                "SELECT COUNT(*) AS n FROM baseline_attempts WHERE member_id=? AND status='failed'",
                (row["member_id"],),
            ).fetchone()["n"]
            if failed >= BASELINE_MAX_ATTEMPT:
                conn.execute("UPDATE people SET baseline_failed=1 WHERE member_id=?", (row["member_id"],))

    def calibration_status(self, room, member_id):
        with self._tx(room) as conn:
            person = self._person(conn, member_id)
            active = conn.execute(
                "SELECT * FROM calibrations WHERE member_id=? AND active=1",
                (member_id,),
            ).fetchone()
            baseline = json.loads(active["baseline"]) if active and active["baseline"] else None
            if not _baseline_usable(baseline):
                baseline = None
            attempts = []
            for row in conn.execute(
                "SELECT attempt_no, status, valid_frames, total_frames, failure_reason FROM baseline_attempts WHERE member_id=? ORDER BY attempt_no",
                (member_id,),
            ):
                attempts.append({
                    "attempt_no": row["attempt_no"],
                    "status": row["status"],
                    "valid_frames": row["valid_frames"],
                    "total_frames": row["total_frames"],
                    "failure_reason": row["failure_reason"],
                })
        return {
            "participant_id": person["participant_id"],
            "baseline": baseline,
            "locked": bool(active and active["locked"]),
            "attempts_used": len(attempts),
            "attempts": attempts,
            "baseline_failed": bool(person["baseline_failed"]),
        }

    def _resolve_baseline(self, conn, member_id, existing, body):
        if "baseline_measurements" in body:
            attempt_id = body.get("attempt_id")
            if not isinstance(attempt_id, str) or len(attempt_id) < 8:
                raise ValueError("自然坐姿采集要先向服务端申请尝试")
            row = conn.execute(
                "SELECT * FROM baseline_attempts WHERE attempt_id=? AND member_id=?",
                (attempt_id, member_id),
            ).fetchone()
            if not row:
                raise ValueError("没有这次采集申请")
            if row["status"] != "started":
                raise ValueError("这次尝试已经结束，不能改成成功")
            if row["calibration_id"] and body.get("calibration_id") and row["calibration_id"] != str(body.get("calibration_id")):
                raise ValueError("不能用新的校准编号绕过已经申请的尝试")
            computed = neutral_baseline(
                body.get("baseline_measurements") or [],
                body.get("duration_ms"),
                body.get("video_width"),
                body.get("video_height"),
                row["attempt_no"],
            )
            if computed is None:
                self._finish_attempt(conn, row, "failed", body, "有效帧、总采样或采集时长不符合 8 秒协议")
                return None
            self._finish_attempt(conn, row, "succeeded", body, None)
            return computed
        if existing and existing["baseline"]:
            stored = json.loads(existing["baseline"])
            if _baseline_usable(stored):
                return stored
        raise ValueError("单帧不能完成自然坐姿校准")

    def add_calibration(self, room, member_id, body):
        if body.get("session_role") != "calibration":
            raise ValueError("第二次讨论不能用来建立个人基准或样例")
        incoming = body.get("samples") or {}
        for label, frames in incoming.items():
            if label not in {"listen", "speak", "wait"}:
                raise ValueError("样例只接受自然聆听、准备发言、希望等待")
            if frames and not WINDOW_MIN <= len(frames) <= WINDOW_LIMIT:
                raise ValueError(f"{label} 需要 6 到 8 帧有效特征，这次没有保存，原模板不变")
        with self._tx(room) as conn:
            person = self._person(conn, member_id)
            active = conn.execute(
                "SELECT * FROM calibrations WHERE member_id=? AND active=1",
                (member_id,),
            ).fetchone()
            calibration_id = str(body.get("calibration_id") or "")
            if not calibration_id:
                raise ValueError("需要校准编号")
            if active and active["locked"] and active["calibration_id"] == calibration_id:
                raise ValueError("三类样例已经锁定，不能覆盖")
            existing = conn.execute(
                "SELECT * FROM calibrations WHERE member_id=? AND calibration_id=?",
                (member_id, calibration_id),
            ).fetchone()
            if active and active["locked"] and "baseline_measurements" in body:
                raise ValueError("个人基准已经锁定，不能重新采集")
            if active and active["locked"] and not _samples_complete(incoming):
                raise ValueError("已锁定的模板不能被一份未完成的校准替换")
            try:
                baseline = self._resolve_baseline(conn, member_id, existing, body)
            except ValueError:
                if active and active["locked"]:
                    raise ValueError("不完整采集不能覆盖已锁定的自然坐姿基准")
                raise
            refused = None
            result = None
            if baseline is None:
                refused = "有效帧不足 24，或这次采集不符合 8 秒协议。基准没有更新"
            elif not _baseline_usable(baseline):
                raise ValueError("自然坐姿基准还没有达到 24 帧有效采集，不能建立样例")
            elif active and active["locked"]:
                digest = template_sha(baseline, incoming)
                conn.execute(
                    """INSERT INTO calibrations(member_id, calibration_id, duration_ms, baseline, samples, locked, active, version, sha256)
                       VALUES (?, ?, ?, ?, ?, 1, 0, ?, ?)""",
                    (member_id, calibration_id, body.get("duration_ms"), json.dumps(baseline, ensure_ascii=False),
                     json.dumps(incoming, ensure_ascii=False), "later", digest),
                )
                result = {
                    "participant_id": person["participant_id"],
                    "locked": True,
                    "active_calibration_id": active["calibration_id"],
                    "stored_as": "history",
                    "baseline": json.loads(active["baseline"]),
                }
            else:
                samples = incoming
                locked = 1 if _samples_complete(samples) else 0
                digest = template_sha(baseline, samples) if locked else None
                version = "1" if locked else None
                payload = (body.get("duration_ms"), json.dumps(baseline, ensure_ascii=False), json.dumps(samples, ensure_ascii=False), locked, 1 if locked or not active else 0, version, digest, member_id, calibration_id)
                if existing:
                    conn.execute(
                        """UPDATE calibrations SET duration_ms=?, baseline=?, samples=?, locked=?, active=?, version=?, sha256=?
                           WHERE member_id=? AND calibration_id=?""",
                        payload,
                    )
                else:
                    conn.execute(
                        """INSERT INTO calibrations(duration_ms, baseline, samples, locked, active, version, sha256, member_id, calibration_id)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        payload,
                    )
                if locked:
                    conn.execute("UPDATE calibrations SET active=0 WHERE member_id=? AND calibration_id<>?", (member_id, calibration_id))
                    conn.execute("UPDATE calibrations SET active=1 WHERE member_id=? AND calibration_id=?", (member_id, calibration_id))
                result = {"participant_id": person["participant_id"], "locked": bool(locked), "active_calibration_id": calibration_id, "baseline": baseline}
        if refused:
            raise ValueError(refused)
        return result

    def mark_speech(self, room, participant_id, body):
        if not participant_id or not isinstance(body.get("t_speech_ms"), int):
            raise ValueError("开口标记需要匿名编号和毫秒时间；没有看到就不要填")
        speech_id = "s" + hashlib.sha256(f"{participant_id}:{body.get('session_id')}:{body.get('t_speech_ms')}".encode()).hexdigest()[:12]
        with self._tx(room) as conn:
            person = conn.execute("SELECT * FROM people WHERE participant_id=?", (participant_id,)).fetchone()
            if not person:
                raise ValueError("没有这个匿名编号")
            session = conn.execute(
                "SELECT * FROM sessions WHERE member_id=? AND session_id=?",
                (person["member_id"], str(body.get("session_id") or "")),
            ).fetchone()
            if not session:
                raise ValueError("没有这场讨论")
            conn.execute(
                "INSERT OR IGNORE INTO speeches(id, participant_id, session_id, agenda_id, t_speech_ms) VALUES (?, ?, ?, ?, ?)",
                (speech_id, participant_id, session["session_id"], body.get("agenda_id"), body["t_speech_ms"]),
            )
            positives = conn.execute(
                "SELECT COUNT(*) AS n FROM labels WHERE member_id=? AND label='positive' AND confirmation_status='confirmed'",
                (person["member_id"],),
            ).fetchone()["n"]
        return {"id": speech_id, "participant_id": participant_id, "t_speech_ms": body["t_speech_ms"], "created_positive": False, "positive_count": positives}

    def integrity(self, room, member_id, session_id):
        with self._tx(room) as conn:
            frames = conn.execute(
                "SELECT COUNT(*) AS n FROM frames WHERE member_id=? AND session_id=?",
                (member_id, session_id),
            ).fetchone()["n"]
            batches = [row["batch_id"] for row in conn.execute(
                "SELECT batch_id FROM batches WHERE member_id=? AND session_id=? ORDER BY batch_id",
                (member_id, session_id),
            )]
        return {"frames": frames, "batches": batches}

    def withdraw(self, room, member_id):
        with self._tx(room) as conn:
            person = conn.execute("SELECT * FROM people WHERE member_id=?", (member_id,)).fetchone()
            if not person:
                raise ValueError("没有这个参与者的记录")
            conn.execute("DELETE FROM frames WHERE member_id=?", (member_id,))
            conn.execute("DELETE FROM batches WHERE member_id=?", (member_id,))
            conn.execute("DELETE FROM labels WHERE member_id=?", (member_id,))
            conn.execute("DELETE FROM calibrations WHERE member_id=?", (member_id,))
            conn.execute("DELETE FROM speeches WHERE participant_id=?", (person["participant_id"],))
            conn.execute("DELETE FROM baseline_attempts WHERE member_id=?", (member_id,))
            conn.execute("UPDATE people SET withdrawn=1 WHERE member_id=?", (member_id,))
        return {"participant_id": person["participant_id"], "withdrawn": True}

    def mark_synthetic(self, room):
        with self._tx(room) as conn:
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('synthetic', '1')")
        return {"synthetic": True}

    def _synthetic(self, conn):
        row = conn.execute("SELECT value FROM meta WHERE key='synthetic'").fetchone()
        return bool(row and row["value"] == "1")

    def _test_sessions(self, conn):
        return conn.execute("SELECT COUNT(*) AS n FROM sessions WHERE session_role='test'").fetchone()["n"]

    def _eligible(self, conn):
        found = []
        for person in conn.execute("SELECT * FROM people"):
            if person["withdrawn"] or person["baseline_failed"]:
                continue
            row = conn.execute(
                "SELECT * FROM calibrations WHERE member_id=? AND active=1 AND locked=1",
                (person["member_id"],),
            ).fetchone()
            if not row or not row["baseline"] or not row["samples"]:
                continue
            try:
                baseline = json.loads(row["baseline"])
                samples = json.loads(row["samples"])
            except json.JSONDecodeError:
                continue
            if not _baseline_usable(baseline) or not _samples_complete(samples):
                continue
            found.append({
                "participant_id": person["participant_id"],
                "calibration_id": row["calibration_id"],
                "baseline": baseline,
            })
        found.sort(key=lambda item: item["participant_id"])
        return found

    def _guard_reference_edit(self, conn):
        if self._synthetic(conn):
            raise ValueError("合成数据不能设定真人通用参照")
        if self._test_sessions(conn):
            raise ValueError("已经创建第二次讨论，不能再生成或修改通用参照")
        current = conn.execute("SELECT payload FROM reference_row WHERE id=1").fetchone()
        if current and json.loads(current["payload"]).get("frozen"):
            raise ValueError("通用参照已经冻结，不能再改")

    def set_reference(self, room, body):
        del body
        raise ValueError("不能用手填的四个数字充当真人通用参照")

    def build_reference(self, room):
        with self._tx(room) as conn:
            self._guard_reference_edit(conn)
            eligible = self._eligible(conn)
            count = len(eligible)
            values = None
            blocked = None
            deviation = False
            if count > COHORT_MAX:
                deviation = True
                blocked = "超过 12 名有效参与者，记为实验方案偏差，不能截取后冻结"
            elif count < COHORT_MIN:
                blocked = "不足 8 名有效参与者，不能生成用于真人比较的通用参照"
            else:
                values = json.loads(json.dumps(cohort_reference([item["baseline"] for item in eligible])))
            record = {
                "method": REFERENCE_METHOD,
                "version": REFERENCE_VERSION,
                "participant_count": count,
                "participant_ids": [item["participant_id"] for item in eligible],
                "calibration_ids": [item["calibration_id"] for item in eligible],
                "values": values,
                "statistic": "componentwise_median",
                "capture_protocol": BASELINE_PROTOCOL,
                "source": "第一次讨论的自然坐姿中位数",
                "origin": "cohort_build",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "frozen": False,
                "ready": False,
                "protocol_deviation": deviation,
                "blocked_reason": blocked,
                "scope": "本次 E1 先导队列的共享参照，不是全体大学生的标准坐姿，也不是已经验证的通用人体标准",
            }
            record["sha256"] = reference_digest(record) if values else None
            conn.execute(
                "INSERT OR REPLACE INTO reference_row(id, payload) VALUES (1, ?)",
                (json.dumps(record, ensure_ascii=False),),
            )
        return record

    def freeze_reference(self, room):
        with self._tx(room) as conn:
            if self._synthetic(conn):
                raise ValueError("合成数据不能冻结为真人通用参照")
            if self._test_sessions(conn):
                raise ValueError("已经创建第二次讨论，不能再冻结或修改通用参照")
            current = conn.execute("SELECT payload FROM reference_row WHERE id=1").fetchone()
            if not current:
                raise ValueError("还没有通用参照")
            record = json.loads(current["payload"])
            if record.get("origin") != "cohort_build" or record.get("method") not in REFERENCE_METHODS:
                raise ValueError("真人参照必须来自第一次讨论的有效校准，不能用手填的数字冻结")
            if record.get("frozen") and record.get("ready"):
                if record.get("sha256") != reference_digest(record):
                    raise ValueError("通用参照的 SHA-256 对不上，不能当作已冻结")
                return record
            eligible = self._eligible(conn)
            count = len(eligible)
            if not COHORT_MIN <= count <= COHORT_MAX:
                raise ValueError(record.get("blocked_reason") or "有效参与者不是 8 到 12 人，不能冻结")
            if record.get("protocol_deviation"):
                raise ValueError("参与者人数超出方案，不能截取后冻结")
            fresh = json.loads(json.dumps(cohort_reference([item["baseline"] for item in eligible])))
            ids = [item["participant_id"] for item in eligible]
            calibrations = [item["calibration_id"] for item in eligible]
            if record.get("values") != fresh or record.get("participant_ids") != ids or record.get("calibration_ids") != calibrations:
                raise ValueError("参照和当前第一次讨论的校准不一致，不能冻结")
            if record.get("statistic") != "componentwise_median" or record.get("capture_protocol") != BASELINE_PROTOCOL:
                raise ValueError("参照来源不符合已冻结的采集协议")
            if record.get("version") != REFERENCE_VERSION or not record.get("values"):
                raise ValueError("参照版本或四维数值不完整")
            digest = reference_digest(record)
            if record.get("sha256") != digest:
                raise ValueError("通用参照的 SHA-256 对不上，不能冻结")
            record["frozen"] = True
            record["ready"] = True
            conn.execute("UPDATE reference_row SET payload=? WHERE id=1", (json.dumps(record, ensure_ascii=False),))
        return record

    def open_session(self, room, member_id, body):
        with self._tx(room) as conn:
            person = self._person(conn, member_id)
            row = self._session(conn, member_id, str(body.get("session_id") or ""), body.get("session_role"), body)
        return {
            "participant_id": person["participant_id"],
            "session_id": row["session_id"],
            "session_role": row["session_role"],
            "protocol_deviation": bool(row["protocol_deviation"]),
            "setup_confirmed": bool(row["setup_confirmed"]),
            "video_width": row["video_width"],
            "video_height": row["video_height"],
        }

    def _reference(self, conn):
        row = conn.execute("SELECT payload FROM reference_row WHERE id=1").fetchone()
        if not row:
            return None
        record = json.loads(row["payload"])
        record["ready"] = bool(record.get("frozen")) and record.get("method") in REFERENCE_METHODS
        return record

    def export_room(self, room):
        with self._tx(room) as conn:
            reference = self._reference(conn)
            people = []
            for person in conn.execute("SELECT * FROM people ORDER BY participant_id"):
                calibrations = []
                for row in conn.execute("SELECT * FROM calibrations WHERE member_id=? ORDER BY id", (person["member_id"],)):
                    calibrations.append({
                        "calibration_id": row["calibration_id"],
                        "session_role": "calibration",
                        "duration_ms": row["duration_ms"],
                        "baseline": json.loads(row["baseline"]) if row["baseline"] else None,
                        "samples": json.loads(row["samples"]) if row["samples"] else {},
                        "locked": bool(row["locked"]),
                        "active": bool(row["active"]),
                        "version": row["version"],
                        "sha256": row["sha256"],
                    })
                active = next((item for item in calibrations if item["active"]), None)
                sessions = []
                if not person["withdrawn"]:
                    for session in conn.execute("SELECT * FROM sessions WHERE member_id=? ORDER BY session_id", (person["member_id"],)):
                        frames = []
                        for frame in conn.execute(
                            "SELECT payload FROM frames WHERE member_id=? AND session_id=? ORDER BY timestamp_ms, seq",
                            (person["member_id"], session["session_id"]),
                        ):
                            frames.append(json.loads(frame["payload"]))
                        labels = []
                        intents = []
                        for label in conn.execute(
                            "SELECT * FROM labels WHERE member_id=? AND session_id=? ORDER BY start_ms",
                            (person["member_id"], session["session_id"]),
                        ):
                            item = {
                                "id": label["id"],
                                "participant_id": person["participant_id"],
                                "session_id": session["session_id"],
                                "agenda_id": label["agenda_id"],
                                "start_timestamp": label["start_ms"],
                                "end_timestamp": label["end_ms"],
                                "start_ms": label["start_ms"],
                                "end_ms": label["end_ms"],
                            "label": label["label"],
                            "label_source": label["label_source"],
                            "confirmation_status": label["confirmation_status"],
                            "intent_status": label["intent_status"],
                            "window_anchor": label["window_anchor"],
                            "superseded": bool(label["superseded"]),
                            "revised_from": label["revised_from"],
                            "revised_at": label["revised_at"],
                        }
                            labels.append(item)
                            if label["label_source"] == "button":
                                intents.append({
                                    "id": label["id"],
                                    "pressed_at_ms": label["start_ms"],
                                    "agenda_id": label["agenda_id"],
                                    "status": "deleted" if label["intent_status"] == "deleted" else "confirmed",
                                    "label_source": "button",
                                    "t_speech_ms": None,
                                })
                        speeches = [dict(row) for row in conn.execute(
                            "SELECT id, participant_id, session_id, agenda_id, t_speech_ms FROM speeches WHERE participant_id=? AND session_id=?",
                            (person["participant_id"], session["session_id"]),
                        )]
                        sessions.append({
                            "session_id": session["session_id"],
                            "session_role": session["session_role"],
                            "video_width": session["video_width"],
                            "video_height": session["video_height"],
                            "setup_confirmed": bool(session["setup_confirmed"]),
                            "protocol_deviation": bool(session["protocol_deviation"]),
                            "frames": frames,
                            "labels": labels,
                            "intents": intents,
                            "speeches": speeches,
                        })
                people.append({
                    "participant_id": person["participant_id"],
                    "withdrawn": bool(person["withdrawn"]),
                    "calibration": active,
                    "calibration_history": calibrations,
                    "sessions": sessions,
                })
        return {
            "synthetic": False,
            "human_pilot_complete": False,
            "global_reference_ready": bool(reference and reference.get("ready")),
            "global_reference": reference,
            "participants": people,
        }
