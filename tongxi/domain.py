import copy
import time

from .store import require, uid

ACTIVE = {"delivered", "seen"}
OPEN_OPINIONS = {"pending", "verifying", "disputed"}
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


def new_request(room, person_id, decision, kind, source, reason, evidence=None, mode=None):
    q = {"id": uid("q"), "member": person_id, "decision": decision["id"] if decision else None,
         "round": decision["round"] if decision else 1, "kind": kind, "source": source,
         "status": "delivered", "reason": reason, "closed_reason": "", "created_at": time.time(),
         "phase": "receiving" if kind == "wait" else "expressing", "mode": mode,
         "evidence": evidence or [], "ready_at": None, "minimum_until": None,
         "contribution_ids": [], "feedback": None, "transitions": []}
    room["requests"].append(q)
    return q


def phase(q, value, **extra):
    if q["phase"] != value:
        q["transitions"].append({"phase": value, "at": time.time()})
    q.update(phase=value, **extra)


def start_response(room, q, stage="thinking"):
    now = time.time()
    seconds = room["members"][q["member"]]["consent"].get("response_seconds", 20)
    phase(q, stage, ready_at=now, minimum_until=now + seconds)


def refresh_windows(room):
    """只接受明确的完成回执；队列消失、超时或失联都不代表已经收到内容。"""
    now = time.monotonic()
    for q in room["requests"]:
        if q["status"] not in ACTIVE or q["phase"] != "receiving" or q["source"] != "auto":
            continue
        person = room["members"][q["member"]]
        p = person["presentation"]
        if not p or now-person["heartbeat"] > 6 or now-p["received"] > 6:
            continue
        if p["epoch"] != person["epoch"] or p["consent_version"] != person["consent"]["version"]:
            continue
        finished = p.get("review_completed", []) if q["mode"] == "review" else p.get("completed", [])
        done = {(e["segment"], e["rev"]) for e in finished}
        if q["evidence"] and all((e["segment"], e["rev"]) in done for e in q["evidence"]):
            start_response(room, q)


def request_view(room, q, own=False):
    result = copy.deepcopy(q)
    person = room["members"][q["member"]]
    fresh = bool(person["epoch"]) and time.monotonic()-person["heartbeat"] <= 6
    if q["source"] == "auto":
        p = person["presentation"]
        fresh = fresh and bool(p) and time.monotonic()-p["received"] <= 6
    if q["status"] in ACTIVE and q["phase"] != "submitted" and not fresh:
        result["phase"] = "unknown"
    # 主持人只获得安排回应所需的状态，不获得个人计时、队列及评价。
    if not own:
        for key in ("minimum_until", "ready_at", "evidence", "mode", "feedback", "transitions"):
            result.pop(key, None)
    return result


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
                refs = [e for e in p["evidence"] if (e["segment"], e["rev"]) in required]
                new_request(room, person_id, decision, "wait", "auto",
                            "相关依据正在回看" if review else "相关依据仍在排队或播放", refs,
                            "review" if review else "playback")
        refresh_decision(room, decision)
    refresh_windows(room)


def consent(room, actor, data):
    member(actor)
    person = room["members"][actor["id"]]
    version(person["consent"], data.version)
    person["consent"] = {"version": data.version+1, "playback": data.playback, "review": data.review,
                         "response_seconds": data.response_seconds}
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
                if q["decision"] == d["id"] and q["source"] == "auto" and q["status"] in ACTIVE:
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
    for q in room["requests"]:
        if q["decision"] == d["id"] and q["status"] in ACTIVE:
            q["round"] = d["round"]
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
    validate_evidence(room, [e.model_dump() for e in data.completed + data.review_completed])
    person["seq"] = data.seq
    person["presentation"] = {**data.model_dump(), "received": time.monotonic()}
    check_auto(room)
    return {"accepted": True}


def manual_request(room, actor, data):
    member(actor)
    d = find(room["decisions"], data.decision) if data.decision else None
    if data.segment:
        find(room["segments"], data.segment)
    q = new_request(room, actor["id"], d, data.kind, "manual", "本人主动请求")
    q["segment"] = data.segment
    if d:
        refresh_decision(room, d)
    return q


def act_request(room, actor, request_id, data):
    q = find(room["requests"], request_id)
    if data.action in {"seen", "handled"}:
        host(actor)
    else:
        require(q["member"] == actor["id"], "只能操作本人的请求", 403)
    require(q["status"] in ACTIVE, "请求已经结束", 409)
    if data.action in {"ready", "extend", "express"}:
        require(q["phase"] != "submitted", "意见已提交，请在意见记录中继续回应", 409)
        start_response(room, q, "expressing" if data.action == "express" else "thinking")
    else:
        if data.action == "handled":
            require(bool(data.reason), "请说明如何安排或回应了这条请求")
            current = request_view(room, q, own=True)
            require(current["phase"] not in {"receiving", "unknown"} and
                    (q["minimum_until"] is None or time.time() >= q["minimum_until"]),
                    "回应机会仍在保留；需要提前继续时请在议题中明确记录例外", 409)
            require(q["phase"] != "submitted", "请逐条处理已提交的意见", 409)
        q["status"] = {"seen": "seen", "handled": "handled", "withdraw": "withdrawn", "skip": "skipped"}[data.action]
        if data.action != "seen":
            q["closed_reason"] = data.reason or {"withdraw": "本人撤回", "skip": "本人略过"}.get(data.action, "")
    audit(room, actor, "回应进度", {"request": q["id"], "action": data.action})
    if q["decision"]:
        refresh_decision(room, find(room["decisions"], q["decision"]))
    return request_view(room, q, own=q["member"] == actor["id"])


def contribute(room, actor, data):
    member(actor)
    if data.decision:
        find(room["decisions"], data.decision)
    if data.quote:
        validate_evidence(room, [data.quote.model_dump()])
    c = {"id": uid("c"), "version": 1, "member": actor["id"], "text": data.text, "decision": data.decision,
         "quote": data.quote.model_dump() if data.quote else None, "status": "pending", "resolution": None,
         "history": [], "created_at": time.time(), "acknowledged": False}
    room["contributions"].append(c)
    for q in room["requests"]:
        if q["member"] == actor["id"] and q["decision"] == data.decision and q["status"] in ACTIVE:
            q["contribution_ids"].append(c["id"])
            phase(q, "submitted")
    return c


def reply_contribution(room, actor, contribution_id, data):
    host(actor)
    c = find(room["contributions"], contribution_id)
    version(c, data.version)
    require(c["status"] in OPEN_OPINIONS, "这条意见已有答复；成员要求继续回应后可重新处理", 409)
    if data.outcome in {"adopted", "partial"}:
        require(bool(data.impact), "请说明采纳后具体影响了哪项内容")
    d = find(room["decisions"], c["decision"]) if c["decision"] else None
    resolution = {"action": "reply", "outcome": data.outcome, "reason": data.reason, "impact": data.impact,
                  "at": time.time(), "actor": actor["id"], "decision_version": d["version"] if d else None}
    c.update(status="verifying" if data.outcome == "verifying" else "handled", resolution=resolution,
             version=c["version"]+1, acknowledged=False)
    c["history"].append({"kind": "reply", **copy.deepcopy(resolution)})
    for q in room["requests"]:
        if c["id"] in q.get("contribution_ids", []) and q["status"] in ACTIVE:
            unresolved = any(find(room["contributions"], cid)["status"] in OPEN_OPINIONS for cid in q["contribution_ids"])
            if not unresolved:
                q.update(status="handled", closed_reason="关联意见已逐条答复")
                if q["decision"]:
                    refresh_decision(room, find(room["decisions"], q["decision"]))
    audit(room, actor, "逐条回应意见", {"contribution": c["id"], "outcome": data.outcome})
    return c


def contribution_feedback(room, actor, contribution_id, data):
    c = find(room["contributions"], contribution_id)
    require(c["member"] == actor["id"], "只能反馈本人的意见", 403)
    version(c, data.version)
    require(c["resolution"] is not None and c["status"] in {"handled", "verifying"}, "当前没有可反馈的答复", 409)
    if data.action == "dispute":
        require(bool(data.reason), "请说明还没有得到回答的问题")
        c.update(status="disputed", acknowledged=False)
        for q in room["requests"]:
            if c["id"] in q.get("contribution_ids", []) and q["status"] == "handled":
                q.update(status="delivered", closed_reason="", phase="submitted")
                if q["decision"]:
                    refresh_decision(room, find(room["decisions"], q["decision"]))
    else:
        require(c["status"] == "handled", "尚在核实中的意见不能确认完成", 409)
        require(not c["acknowledged"], "已经确认收到这条答复", 409)
        c["acknowledged"] = True
    c["version"] += 1
    c["history"].append({"kind": data.action, "reason": data.reason, "at": time.time()})
    audit(room, actor, "成员反馈答复", {"contribution": c["id"], "action": data.action})
    return c


def intervention_feedback(room, actor, data):
    q = find(room["requests"], data.request)
    require(q["member"] == actor["id"] and q["source"] == "auto", "只能评价本人的自动留位", 403)
    q["feedback"] = {"rating": data.rating, "at": time.time()}
    return {"accepted": True}


def resolve(room, actor, decision_id, data):
    host(actor)
    d = find(room["decisions"], decision_id)
    version(d, data.version)
    require(d["status"] != "review", "请先重新核对修订后的依据", 409)
    check_auto(room)
    pending = [q for q in room["requests"] if q["decision"] == d["id"] and q["round"] == d["round"] and q["status"] in ACTIVE]
    opinions = [c for c in room["contributions"] if c["decision"] == d["id"] and c["status"] in OPEN_OPINIONS]
    if data.action == "reopen":
        require(d["status"] in {"confirmed", "deferred"}, "只有已确认或推迟的议题可以重开", 409)
        d.update(round=d["round"]+1, status="discussing")
    elif data.action == "defer":
        d["status"] = "deferred"
    else:
        require(d["status"] in {"preparing", "waiting"}, "请先核对依据并进入准备定案", 409)
        require(not (pending or opinions) or data.close_pending, "仍有待回应请求或未答复意见，请逐条处理或明确记录继续的原因", 409)
        if data.action == "modify":
            require(bool(data.conclusion), "修改时需要填写新结论")
        d["status"] = "confirmed"
    resolution = {"action": data.action, "reason": data.reason, "before": d["conclusion"],
                  "after": data.conclusion if data.action == "modify" else d["conclusion"], "at": time.time(), "round": d["round"],
                  "continued_without_response": bool((pending or opinions) and data.close_pending and data.action in {"maintain", "modify"}),
                  "unresolved_count": len(opinions), "waiting_count": len(pending)}
    if data.action == "modify":
        d["conclusion"] = data.conclusion
    d["resolutions"].append(resolution)
    d["version"] += 1
    if data.action in {"maintain", "modify"}:
        for q in pending:
            q.update(status="overridden", closed_reason="主持人说明原因后继续：" + data.reason)
    audit(room, actor, "人工处理", {"decision": d["id"], **resolution})
    return d


def snapshot(room, actor):
    base = {"id": room["id"], "title": room["title"], "status": room["status"], "role": actor["role"],
            "demo": room["demo"], "decisions": copy.deepcopy(room["decisions"])}
    if actor["role"] == "board":
        base["decisions"] = [{k: d[k] for k in ("id", "title", "status", "conclusion", "resolutions")} for d in room["decisions"]]
        for d in base["decisions"]:
            d["resolutions"] = [{k: v for k, v in r.items() if k not in {"waiting_count", "unresolved_count"}}
                                for r in d["resolutions"]]
        return base
    base.update(server_time=time.time(), me={"id": actor["id"], "name": room["members"][actor["id"]]["name"], "consent": room["members"][actor["id"]]["consent"]},
                segments=room["segments"],
                requests=[request_view(room, q, own=q["member"] == actor["id"]) for q in room["requests"] if actor["role"] == "host" or q["member"] == actor["id"]],
                contributions=[c for c in room["contributions"] if actor["role"] == "host" or c["member"] == actor["id"]])
    if actor["role"] == "host":
        base.update(invite=room["invite"], board_token=room["board_token"], revision=room["revision"], history=room["history"],
                    analysis=room.get("analysis"), ai_enabled=room.get("ai_enabled", True),
                    members=[{"id": mid, "name": p["name"], "role": p["role"]} for mid, p in room["members"].items()])
    return copy.deepcopy(base)
