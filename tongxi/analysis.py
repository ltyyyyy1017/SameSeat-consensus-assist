"""稳定字幕变化驱动的本地分析队列，结果在提交时再次核对原句版本。"""
import asyncio
import time

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from . import adapters, domain, models
from .store import uid


def fingerprint(room):
    return [[s["id"], s["rev"]] for s in room["segments"][-30:] if s["stable"]]


def valid_items(room, raw):
    accepted = []
    for value in raw[:3]:
        try:
            parsed = models.AICandidate.model_validate(value)
            domain.validate_evidence(room, [e.model_dump() for e in parsed.evidence])
            accepted.append(parsed.model_dump())
        except (ValueError, HTTPException):
            continue
    return accepted


class AnalysisService:
    def __init__(self, store, semantic):
        self.store = store
        self.semantic = semantic
        self.tasks = {}

    def write(self, room_id, changes):
        return self.store.mutate(room_id, {"id": "ai-worker"}, uid("ai"), changes,
                                 lambda room: room["analysis"].update(changes))

    async def trigger(self, room_id, force=False):
        room = self.store.read(room_id)
        if room["status"] != "open" or not fingerprint(room) or not (force or room["ai_enabled"]):
            return None
        if not force and room["analysis"].get("source") == fingerprint(room) and room["analysis"]["status"] == "ready":
            return None
        existing = self.tasks.get(room_id)
        if existing and not existing.done():
            return existing
        self.write(room_id, {"status": "queued", "error": "", "items": []})
        task = asyncio.create_task(self.run(room_id, force))
        self.tasks[room_id] = task
        return task

    async def run(self, room_id, force):
        try:
            await asyncio.sleep(.7)
            while True:
                room = self.store.read(room_id)
                if room["status"] != "open" or not (force or room["ai_enabled"]):
                    return
                source = fingerprint(room)
                self.write(room_id, {"status": "running", "items": [], "error": ""})
                started = time.monotonic()
                requested_at = time.time()
                raw = await run_in_threadpool(adapters.analyze_discussion, room, self.semantic)
                latest = self.store.read(room_id)
                if latest["status"] != "open":
                    return
                if not (force or latest["ai_enabled"]):
                    self.write(room_id, {"status": "paused", "items": []})
                    return
                if source != fingerprint(latest):
                    # 模型计算期间原句已变化，旧结果不展示，直接合并处理最新窗口。
                    continue
                self.write(room_id, {"status": "ready", "items": valid_items(latest, raw.get("items", [])),
                    "phase": raw.get("phase") if raw.get("phase") in {"discussing", "seeking_response", "closing", "uncertain"} else "uncertain",
                    "summary": str(raw.get("summary", ""))[:160], "source": source,
                    "input_ids": list(raw.get("input_ids") or []),
                    "input_texts": [str(text)[:1500] for text in raw.get("input_texts") or []],
                    "requested_at": requested_at,
                    "elapsed_ms": round((time.monotonic()-started)*1000), "updated_at": time.time(),
                    "provider": adapters.capabilities()["semantic"]["label"], "error": ""})
                return
        except asyncio.CancelledError:
            raise
        except Exception as error:
            latest = self.store.read(room_id)
            if latest["status"] == "open":
                message = error.detail if isinstance(error, HTTPException) else "本地分析失败，请重新分析或手动建项"
                self.write(room_id, {"status": "error", "error": str(message), "items": []})

    async def shutdown(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
