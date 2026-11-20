import copy
import time

from .store import require, uid

ACTIVE = {"delivered", "seen"}
STATES = {"discussing": "讨论中", "preparing": "准备定案", "waiting": "待回应", "confirmed": "已确认", "deferred": "已推迟", "review": "依据需复核"}


def find(items, item_id):
    item = next((x for x in items if x["id"] == item_id), None)
    require(item is not None, "目标记录不存在", 404)
    return item


def host(actor):
    require(actor["role"] == "host", "此操作需要主持人身份", 403)


def member(actor):
    require(actor["role"] in {"host", "member"}, "公共屏只允许查看公开议题", 403)


def version(item, expected):
    require(item["version"] == expected, "内容已更新，请刷新后重新核对", 409)


def validate_evidence(room, refs):
    for ref in refs:
        segment = find(room["segments"], ref["segment"])
        require(segment["stable"] and segment["rev"] == ref["rev"], "依据已修订或尚未稳定，请重新选择", 409)


def audit(room, actor, event, detail):
    room["history"].append({"id": uid("h"), "at": time.time(), "actor": actor["id"], "event": event, "detail": detail})


def refresh_decision(room, decision):
    if decision["status"] not in {"preparing", "waiting"}:
        return
    active = any(q["decision"] == decision["id"] and q["round"] == decision["round"] and q["status"] in ACTIVE for q in room["requests"])
    decision["status"] = "waiting" if active else "preparing"


def check_auto(room):
    now = time.monotonic()
    for decision in room["decisions"]:
        if decision["status"] not in {"preparing", "waiting"}:
            continue
        validate_evidence(room, decision["evidence"])
        required = {(e["segment"], e["rev"]) for e in decision["evidence"]}
        for person_id, person in room["members"].items():
            p = person["presentation"]
            if not p or now-person["heartbeat"] > 6 or now-p["received"] > 6:
                continue
            consent = person["consent"]
            if p["consent_version"] != consent["version"] or p["epoch"] != person["epoch"]:
                continue
            playback = p["state"] in {"playing", "queued"} and consent["playback"]
            review = p["state"] == "review" and consent["review"]
            if not (playback or review) or not required.intersection((e["segment"], e["rev"]) for e in p["evidence"]):
                continue
            # 一人一轮仅触发一次。略过、撤回、已处理都构成这一轮的终止。
            exists = any(q["member"] == person_id and q["decision"] == decision["id"] and q["round"] == decision["round"] and q["source"] == "auto" for q in room["requests"])
            if not exists:
                room["requests"].append({"id": uid("q"), "member": person_id, "decision": decision["id"],
                    "round": decision["round"], "kind": "wait", "source": "auto", "status": "delivered",
                    "reason": "相关依据正在回看" if review else "相关依据仍在排队或播放", "closed_reason": ""})
        refresh_decision(room, decision)


def consent(room, actor, data):
    member(actor)
    person = room["members"][actor["id"]]
    version(person["consent"], data.version)
    person["consent"] = {"version": data.version+1, "playback": data.playback, "review": data.review}
    person["presentation"] = None
    for q in room["requests"]:
        if q["member"] == actor["id"] and q["source"] == "auto" and q["status"] in ACTIVE:
            q.update(status="withdrawn", closed_reason="授权设置变更")
    for d in room["decisions"]:
        refresh_decision(room, d)
    return person["consent"]


def add_segment(room, actor, data):
    host(actor)
    item = {"id": uid("s"), "rev": 1, "text": data.text, "stable": data.stable, "source": data.source, "revisions": []}
    room["segments"].append(item)
    return item


def revise(room, actor, segment_id, data):
    host(actor)
    s = find(room["segments"], segment_id)
    require(s["rev"] == data.version, "字幕已更新", 409)
    s["revisions"].append({"rev": s["rev"], "text": s["text"]})
    s.update(rev=s["rev"]+1, text=data.text, stable=True)
    for d in room["decisions"]:
        if any(e["segment"] == segment_id for e in d["evidence"]):
            d.update(status="review", version=d["version"]+1)
            for q in room["requests"]:
                if q["decision"] == d["id"] and q["status"] in ACTIVE:
                    q.update(status="invalidated", closed_reason="依据修订，等待重新确认")
    audit(room, actor, "字幕修订", {"segment": segment_id, "rev": s["rev"]})
    return s


def create_decision(room, actor, data):
    host(actor)
    refs = [e.model_dump() for e in data.evidence]
    validate_evidence(room, refs)
    d = {"id": uid("d"), "version": 1, "round": 1, "title": data.title, "conclusion": data.conclusion,
         "evidence": refs, "status": "discussing", "resolutions": []}
    room["decisions"].append(d)
    audit(room, actor, "建立议题", {"decision": d["id"], "title": d["title"]})
    return d


def prepare(room, actor, decision_id, data):
    host(actor)
    d = find(room["decisions"], decision_id)
    version(d, data.version)
    require(d["status"] in {"discussing", "deferred", "review"}, "当前状态不能重复准备定案", 409)
    if d["status"] == "review":
        d["evidence"] = [{"segment": e["segment"], "rev": find(room["segments"], e["segment"])["rev"]} for e in d["evidence"]]
        d["round"] += 1
    validate_evidence(room, d["evidence"])
    d.update(status="preparing", version=d["version"]+1)
    check_auto(room)
    audit(room, actor, "准备定案", copy.deepcopy(d))
    return d


def presentation(room, actor, data):
    member(actor)
    person = room["members"][actor["id"]]
    require(person["epoch"] == data.epoch and bool(data.epoch), "连接已变更，请重新同步", 409)
    require(data.consent_version == person["consent"]["version"], "授权版本过期", 409)
    require(data.seq > person["seq"], "呈现回执序号过期", 409)
    validate_evidence(room, [e.model_dump() for e in data.evidence])
    person["seq"] = data.seq
    person["presentation"] = {**data.model_dump(), "received": time.monotonic()}
    check_auto(room)
    return {"accepted": True}


def manual_request(room, actor, data):
    member(actor)
    d = find(room["decisions"], data.decision) if data.decision else None
    if data.segment:
        find(room["segments"], data.segment)
    q = {"id": uid("q"), "member": actor["id"], "decision": data.decision, "round": d["round"] if d else 1,
         "kind": data.kind, "source": "manual", "status": "delivered", "reason": "本人主动请求", "segment": data.segment, "closed_reason": ""}
    room["requests"].append(q)
    if d:
        refresh_decision(room, d)
    return q


def act_request(room, actor, request_id, data):
    q = find(room["requests"], request_id)
    if data.action in {"seen", "handled"}:
        host(actor)
    else:
        require(q["member"] == actor["id"], "只能撤回或略过本人的请求", 403)
    require(q["status"] in ACTIVE, "请求已经结束", 409)
    q["status"] = {"seen": "seen", "handled": "handled", "withdraw": "withdrawn", "skip": "skipped"}[data.action]
    q["closed_reason"] = data.action
    if q["decision"]:
        refresh_decision(room, find(room["decisions"], q["decision"]))
    return q


def contribute(room, actor, data):
    member(actor)
    if data.decision:
        find(room["decisions"], data.decision)
    if data.quote:
        validate_evidence(room, [data.quote.model_dump()])
    c = {"id": uid("c"), "member": actor["id"], "text": data.text, "decision": data.decision,
         "quote": data.quote.model_dump() if data.quote else None, "status": "pending", "resolution": None}
    room["contributions"].append(c)
    return c


def resolve(room, actor, decision_id, data):
    host(actor)
    d = find(room["decisions"], decision_id)
    version(d, data.version)
    require(d["status"] != "review", "请先重新核对修订后的依据", 409)
    check_auto(room)
    pending = [q for q in room["requests"] if q["decision"] == d["id"] and q["round"] == d["round"] and q["status"] in ACTIVE]
    if data.action == "reopen":
        require(d["status"] in {"confirmed", "deferred"}, "只有已确认或推迟的议题可以重开", 409)
        d.update(round=d["round"]+1, status="discussing")
    elif data.action == "defer":
        d["status"] = "deferred"
    else:
        require(d["status"] in {"preparing", "waiting"}, "请先核对依据并进入准备定案", 409)
        require(not pending or data.close_pending, "仍有待回应事项，请处理或明确记录继续的原因", 409)
        if data.action == "modify":
            require(bool(data.conclusion), "修改时需要填写新结论")
        d["status"] = "confirmed"
    resolution = {"action": data.action, "reason": data.reason, "before": d["conclusion"],
                  "after": data.conclusion or d["conclusion"], "at": time.time(), "round": d["round"]}
    if data.action == "modify":
        d["conclusion"] = data.conclusion
    d["resolutions"].append(resolution)
    d["version"] += 1
    if data.action in {"maintain", "modify"}:
        for q in pending:
            q.update(status="handled", closed_reason=data.reason)
        for c in room["contributions"]:
            if c["decision"] == d["id"] and c["status"] == "pending":
                c.update(status="handled", resolution=resolution)
    audit(room, actor, "人工处理", {"decision": d["id"], **resolution})
    return d


def snapshot(room, actor):
    base = {"id": room["id"], "title": room["title"], "status": room["status"], "role": actor["role"],
            "demo": room["demo"], "decisions": copy.deepcopy(room["decisions"])}
    if actor["role"] == "board":
        base["decisions"] = [{k: d[k] for k in ("id", "title", "status", "conclusion", "resolutions")} for d in room["decisions"]]
        return base
    base.update(me={"id": actor["id"], "name": room["members"][actor["id"]]["name"], "consent": room["members"][actor["id"]]["consent"]},
                segments=room["segments"],
                requests=[q for q in room["requests"] if actor["role"] == "host" or q["member"] == actor["id"]],
                contributions=[c for c in room["contributions"] if actor["role"] == "host" or c["member"] == actor["id"]])
    if actor["role"] == "host":
        base.update(invite=room["invite"], board_token=room["board_token"], revision=room["revision"], history=room["history"],
                    analysis=room.get("analysis"), ai_enabled=room.get("ai_enabled", True),
                    members=[{"id": mid, "name": p["name"], "role": p["role"]} for mid, p in room["members"].items()])
    return copy.deepcopy(base)
