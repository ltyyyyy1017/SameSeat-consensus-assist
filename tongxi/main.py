import asyncio
import hashlib
import json
import ssl
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import websockets

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import adapters, domain, models
from .asr_bridge import CaptionAssembler
from .analysis import AnalysisService
from .store import Store, require

ROOT = Path(__file__).parent / "web"


def create_app(auto_ai=True):
    semantic = adapters.LocalSemantic()
    tts = adapters.LocalTTS()

    @asynccontextmanager
    async def lifespan(app):
        yield
        await analysis.shutdown()
        semantic.close()
        tts.clear()

    app = FastAPI(title="同席 · 房间服务", version="0.2.0", lifespan=lifespan)
    store = Store(auto_ai=auto_ai)
    app.state.store = store
    asr = adapters.LocalASR()
    app.state.asr = asr
    active_audio = {}
    analysis = AnalysisService(store, semantic)
    app.state.analysis = analysis

    @app.middleware("http")
    async def protections(request, call_next):
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "只接受同源操作"}, status_code=403)
        response = await call_next(request)
        response.headers.update({"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "Cache-Control": "no-store"})
        return response

    def actor(room: str, authorization: str = Header(default="")):
        require(authorization.startswith("Bearer "), "需要房间会话", 401)
        return store.auth(authorization[7:], room)

    def command(request, room, who, key, data, fn):
        payload = {"path": request.url.path, "body": data.model_dump() if data else {}}
        return store.mutate(room, who, key, payload, fn)

    @app.get("/api/v1/health")
    def health():
        return {"status": "ok", "capabilities": adapters.capabilities()}

    @app.post("/api/v1/rooms")
    def create(data: models.CreateRoom, request: Request):
        require(request.client.host in {"127.0.0.1", "::1", "testclient"}, "请在房间主机上创建讨论", 403)
        return store.create(data.title, data.name)

    @app.post("/api/v1/join")
    def join(data: models.JoinRoom):
        return store.join(data.invite, data.name)

    @app.get("/api/v1/rooms/{room}/snapshot")
    def snapshot(room: str, who=Depends(actor)):
        return domain.snapshot(store.read(room), who)

    @app.put("/api/v1/rooms/{room}/me/consent")
    def consent(room: str, data: models.Consent, request: Request, who=Depends(actor), idempotency_key: str = Header(default="")):
        return command(request, room, who, idempotency_key, data, lambda r: domain.consent(r, who, data))

    @app.post("/api/v1/rooms/{room}/me/expression")
    def expression(room: str, data: models.Expression, who=Depends(actor)):
        return store.apply(room, lambda r: domain.report_expression(r, who, data))

    @app.post("/api/v1/rooms/{room}/segments")
    def segment(room: str, data: models.Segment, request: Request, background: BackgroundTasks, who=Depends(actor), idempotency_key: str = Header(default="")):
        result = command(request, room, who, idempotency_key, data, lambda r: domain.add_segment(r, who, data))
        background.add_task(analysis.trigger, room)
        return result

    @app.patch("/api/v1/rooms/{room}/segments/{segment_id}")
    def revise(room: str, segment_id: str, data: models.Revision, request: Request, background: BackgroundTasks, who=Depends(actor), idempotency_key: str = Header(default="")):
        result = command(request, room, who, idempotency_key, data, lambda r: domain.revise(r, who, segment_id, data))
        background.add_task(analysis.trigger, room)
        return result

    @app.post("/api/v1/rooms/{room}/decisions")
    def decision(room: str, data: models.Decision, request: Request, who=Depends(actor), idempotency_key: str = Header(default="")):
        return command(request, room, who, idempotency_key, data, lambda r: domain.create_decision(r, who, data))

    @app.post("/api/v1/rooms/{room}/decisions/{decision_id}/prepare")
    def prepare(room: str, decision_id: str, data: models.Version, request: Request, who=Depends(actor), idempotency_key: str = Header(default="")):
        return command(request, room, who, idempotency_key, data, lambda r: domain.prepare(r, who, decision_id, data))

    @app.post("/api/v1/rooms/{room}/decisions/{decision_id}/focus")
    def focus(room: str, decision_id: str, data: models.Version, request: Request, who=Depends(actor), idempotency_key: str = Header(default="")):
        return command(request, room, who, idempotency_key, data, lambda r: domain.focus(r, who, decision_id, data))

    @app.post("/api/v1/rooms/{room}/presentation")
    def presentation(room: str, data: models.Presentation, request: Request, who=Depends(actor), idempotency_key: str = Header(default="")):
        return command(request, room, who, idempotency_key, data, lambda r: domain.presentation(r, who, data))

    @app.post("/api/v1/rooms/{room}/requests")
    def manual(room: str, data: models.ManualRequest, request: Request, who=Depends(actor), idempotency_key: str = Header(default="")):
        return command(request, room, who, idempotency_key, data, lambda r: domain.manual_request(r, who, data))

    @app.patch("/api/v1/rooms/{room}/requests/{request_id}")
    def request_action(room: str, request_id: str, data: models.RequestAction, request: Request, who=Depends(actor), idempotency_key: str = Header(default="")):
        return command(request, room, who, idempotency_key, data, lambda r: domain.act_request(r, who, request_id, data))

    @app.post("/api/v1/rooms/{room}/notices/{notice_id}")
    def notice_action(room: str, notice_id: str, data: models.NoticeAction, request: Request, who=Depends(actor), idempotency_key: str = Header(default="")):
        result = command(request, room, who, idempotency_key, data, lambda r: domain.decide_notice(r, who, notice_id, data.action))
        require(not result.get("rejected"), "提醒已失效", 409)
        return result

    @app.post("/api/v1/rooms/{room}/matches")
    async def matches(room: str, data: models.Match, who=Depends(actor)):
        domain.member(who)
        before = store.read(room)
        require(before["status"] == "open", "房间已结束", 410)
        try:
            result = await run_in_threadpool(adapters.semantic_match, before, data.text, semantic)
        except HTTPException as error:
            result = {"items": adapters.match(before, data.text), "provider": "词组检索（本地语义暂不可用）", "warning": error.detail}
        latest = store.read(room)
        require(latest["status"] == "open", "房间已结束", 410)
        require(latest["decisions"] == before["decisions"] and latest["segments"] == before["segments"], "原议题或原句已更新，请重新查找", 409)
        return result

    @app.post("/api/v1/rooms/{room}/contributions")
    def contribute(room: str, data: models.Contribution, request: Request, who=Depends(actor), idempotency_key: str = Header(default="")):
        return command(request, room, who, idempotency_key, data, lambda r: domain.contribute(r, who, data))

    @app.post("/api/v1/rooms/{room}/decisions/{decision_id}/resolve")
    def resolve(room: str, decision_id: str, data: models.Resolve, request: Request, who=Depends(actor), idempotency_key: str = Header(default="")):
        return command(request, room, who, idempotency_key, data, lambda r: domain.resolve(r, who, decision_id, data))

    @app.post("/api/v1/rooms/{room}/contributions/{contribution_id}/reply")
    def reply(room: str, contribution_id: str, data: models.Reply, request: Request, who=Depends(actor), idempotency_key: str = Header(default="")):
        return command(request, room, who, idempotency_key, data, lambda r: domain.reply_contribution(r, who, contribution_id, data))

    @app.post("/api/v1/rooms/{room}/contributions/{contribution_id}/feedback")
    def contribution_feedback(room: str, contribution_id: str, data: models.ContributionFeedback, request: Request, who=Depends(actor), idempotency_key: str = Header(default="")):
        return command(request, room, who, idempotency_key, data, lambda r: domain.contribution_feedback(r, who, contribution_id, data))

    @app.post("/api/v1/rooms/{room}/interventions/feedback")
    def intervention_feedback(room: str, data: models.InterventionFeedback, request: Request, who=Depends(actor), idempotency_key: str = Header(default="")):
        return command(request, room, who, idempotency_key, data, lambda r: domain.intervention_feedback(r, who, data))

    @app.post("/api/v1/rooms/{room}/analyze")
    async def analyze(room: str, who=Depends(actor)):
        domain.host(who)
        task = await analysis.trigger(room, force=True)
        if task:
            await task
        result = store.read(room)["analysis"]
        require(result["status"] != "error", result.get("error", "分析失败"), 503)
        return result

    @app.put("/api/v1/rooms/{room}/ai/settings")
    async def ai_settings(room: str, data: models.AISettings, request: Request, who=Depends(actor), idempotency_key: str = Header(default="")):
        def update(r):
            domain.host(who)
            r["ai_enabled"] = data.enabled
            if not data.enabled:
                r["analysis"].update(status="paused", items=[])
            return {"enabled": data.enabled}
        result = command(request, room, who, idempotency_key, data, update)
        if data.enabled:
            await analysis.trigger(room)
        return result

    @app.get("/api/v1/rooms/{room}/speech/{segment_id}/{rev}")
    async def speech(room: str, segment_id: str, rev: int, who=Depends(actor)):
        domain.member(who)
        state = store.read(room)
        require(state["status"] == "open", "房间已结束", 410)
        segment = domain.find(state["segments"], segment_id)
        require(segment["rev"] == rev and segment["stable"], "原句已更新，请同步新版", 409)
        audio = await run_in_threadpool(tts.synthesize, room, segment)
        if store.read(room)["status"] != "open":
            tts.clear()
            raise HTTPException(410, "房间已结束")
        return Response(audio, media_type="audio/wav")

    @app.post("/api/v1/rooms/{room}/audio")
    async def audio(room: str, request: Request, who=Depends(actor)):
        domain.host(who)
        require(store.read(room)["status"] == "open", "房间已结束", 410)
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            require(size <= 12_000_000, "录音片段过大", 413)
            chunks.append(chunk)
        text = await run_in_threadpool(asr.transcribe, b"".join(chunks))
        from .asr import split_sentences
        return {"text": text, "sentences": split_sentences(text), "provider": adapters.asr_capabilities()['label']}

    @app.websocket('/api/v1/rooms/{room}/caption-stream')
    async def caption_stream(ws: WebSocket, room: str):
        """只桥接收音、官方 VAD/ASR 与已有原句接口，不另做语音算法。"""
        origin = ws.headers.get('origin')
        if origin and urlparse(origin).netloc != ws.headers.get('host'):
            await ws.close(code=1008)
            return
        await ws.accept()
        lease, session = object(), None

        async def publish(texts, who):
            items = []
            for text in texts:
                data = models.Segment(text=text, source='asr', stable=True)
                result = store.mutate(room, who, str(uuid4()), {'asr': data.model_dump()},
                                      lambda r, data=data: domain.add_segment(r, who, data))
                items.append(result)
            if items:
                await ws.send_json({'type': 'sentences', 'items': items})
                await analysis.trigger(room)

        try:
            hello = await asyncio.wait_for(ws.receive_json(), timeout=8)
            require(isinstance(hello, dict), '需要有效的录音连接消息')
            who = store.auth(str(hello.get('token', '')), room)
            domain.host(who)
            require(hello.get('sample_rate') == 16000, '字幕组件只接收 16 kHz 单声道 PCM')
            require(store.read(room)['status'] == 'open', '房间已结束', 410)
            require(room not in active_audio, '本场已有一台设备正在收音，请先停止另一端', 409)
            require(len(active_audio) < 2, '本地主机正在处理其他录音，请稍后再试', 429)
            active_audio[room] = lease
            await ws.send_json({'type': 'loading', 'message': '正在加载本地字幕组件'})
            session = await run_in_threadpool(app.state.asr.new_stream)
            await ws.send_json({'type': 'ready', 'provider': adapters.asr_capabilities()['label']})
            frames = 0
            while True:
                message = await asyncio.wait_for(ws.receive(), timeout=30)
                if message['type'] == 'websocket.disconnect':
                    break
                require(store.read(room)['status'] == 'open', '房间已结束', 410)
                # 每帧重新核对会话有效期；音频连接不改变成员的呈现连接代次。
                store.auth(str(hello.get('token', '')), room)
                if message.get('bytes') is not None:
                    pcm = message['bytes']
                    require(0 < len(pcm) <= 32768 and len(pcm) % 2 == 0, '音频帧格式或大小无效')
                    texts = await run_in_threadpool(session.accept_pcm, pcm)
                    await publish(texts, who)
                    frames += 1
                    await ws.send_json({'type': 'ack', 'frames': frames})
                else:
                    control = json.loads(message.get('text') or '{}')
                    require(isinstance(control, dict) and control.get('type') == 'stop', '不支持的录音命令')
                    await publish(await run_in_threadpool(session.finish), who)
                    await ws.send_json({'type': 'done'})
                    await ws.close(code=1000)
                    break
        except (HTTPException, asyncio.TimeoutError, ValueError, TypeError) as error:
            try:
                detail = error.detail if isinstance(error, HTTPException) else '录音连接超时或消息无效，请重新开启字幕'
                await ws.send_json({'type': 'error', 'message': detail})
                await ws.close(code=1008)
            except (RuntimeError, WebSocketDisconnect):
                pass
        except WebSocketDisconnect:
            pass
        except Exception:
            try:
                await ws.send_json({'type': 'error', 'message': '本地字幕组件处理失败，请检查模型和收音后重新开启'})
                await ws.close(code=1011)
            except (RuntimeError, WebSocketDisconnect):
                pass
        finally:
            if active_audio.get(room) is lease:
                active_audio.pop(room, None)

    def export_data(room):
        return {"title": room["title"], "demo": room["demo"], "decisions": room["decisions"],
                "contributions": [{k: c[k] for k in ("id", "text", "decision", "status", "resolution", "history", "acknowledged")} for c in room["contributions"]],
                "segments": [{k: s[k] for k in ("id", "rev", "text")} for s in room["segments"]]}

    @app.get("/api/v1/rooms/{room}/export/preview")
    def preview(room: str, who=Depends(actor)):
        domain.host(who)
        r = store.read(room)
        return {"revision": r["revision"], "data": export_data(r)}

    @app.post("/api/v1/rooms/{room}/export")
    def export(room: str, data: models.Export, who=Depends(actor)):
        domain.host(who)
        r = store.read(room)
        require(data.revision == r["revision"], "内容已有变化，请重新预览", 409)
        return export_data(r)

    @app.post("/api/v1/rooms/{room}/demo")
    def demo(room: str, request: Request, background: BackgroundTasks, who=Depends(actor), idempotency_key: str = Header(default="")):
        def seed(r):
            domain.host(who)
            require(not r["segments"], "示例只能载入空白房间", 409)
            r["demo"] = True
            for text in ["我们讨论周末的校园活动方案。", "方案 B 需要实验室设备，周六可以使用实验室。", "那就先选 B，接下来讨论人员分工。"]:
                domain.add_segment(r, who, models.Segment(text=text))
            return domain.create_decision(r, who, models.Decision(title="校园活动方案 B", conclusion="周六采用方案 B 开展活动",
                        evidence=[models.Evidence(segment=r["segments"][1]["id"], rev=1)]))
        result = command(request, room, who, idempotency_key, None, seed)
        background.add_task(analysis.trigger, room)
        return result

    @app.post("/api/v1/rooms/{room}/close")
    def close(room: str, request: Request, who=Depends(actor), idempotency_key: str = Header(default="")):
        domain.host(who)
        tts.clear()
        def clear(r):
            domain.host(who)
            r.update(status="closed", invite="", board_token="", segments=[], decisions=[], requests=[], contributions=[], history=[],
                     analysis={"status": "idle", "items": [], "summary": "", "phase": "uncertain", "error": ""})
            for m in r["members"].values():
                m.update(presentation=None, epoch="", heartbeat=0)
            # 删除包含旧内容的幂等结果，房间结束后只保留清空后的状态。
            store.db.execute("DELETE FROM receipts WHERE room=?", (room,))
            return {"status": "closed"}
        return command(request, room, who, idempotency_key, None, clear)

    @app.websocket("/api/v1/rooms/{room}/asr")
    async def asr_stream(ws: WebSocket, room: str):
        origin = ws.headers.get("origin")
        if origin and urlparse(origin).netloc != ws.headers.get("host"):
            await ws.close(code=1008)
            return
        await ws.accept()
        try:
            hello = await asyncio.wait_for(ws.receive_json(), timeout=8)
            who = store.auth(str(hello.get("token", "")), room)
            domain.host(who)
            require(store.read(room)["status"] == "open", "讨论已结束", 410)
        except (asyncio.TimeoutError, HTTPException, ValueError, RuntimeError):
            await ws.close(code=1008)
            return
        url, secure, _, _ = adapters.funasr_target()
        ssl_context = None
        if secure:
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
        assembler = CaptionAssembler()
        try:
            async with websockets.connect(url, ssl=ssl_context, subprotocols=["binary"], ping_interval=20, max_size=8_000_000) as remote:
                await remote.send(json.dumps({
                    "mode": "2pass", "chunk_size": [5, 10, 5], "chunk_interval": 10,
                    "wav_name": "microphone", "is_speaking": True, "wav_format": "pcm",
                    "audio_fs": 16000, "itn": True,
                }, ensure_ascii=False))

                async def publish(update):
                    text, stable = update["text"], update["stable"]
                    result = store.apply(room, lambda r: domain.live_caption(r, who, text, stable))
                    if result and result.get("committed"):
                        asyncio.create_task(analysis.trigger(room))

                async def pump_remote():
                    while True:
                        raw = await remote.recv()
                        if isinstance(raw, bytes):
                            continue
                        update = assembler.feed(json.loads(raw))
                        if update:
                            await publish(update)

                remote_task = asyncio.create_task(pump_remote())
                try:
                    while True:
                        try:
                            message = await ws.receive()
                        except WebSocketDisconnect:
                            break
                        if message["type"] == "websocket.disconnect":
                            break
                        data = message.get("bytes")
                        if data:
                            await remote.send(data)
                            continue
                        text = message.get("text")
                        if text and json.loads(text).get("type") == "stop":
                            break
                finally:
                    try:
                        await remote.send(json.dumps({"is_speaking": False}))
                    except (OSError, websockets.exceptions.WebSocketException):
                        pass
                    if not remote_task.done():
                        try:
                            await asyncio.wait_for(asyncio.shield(remote_task), timeout=4)
                        except (asyncio.TimeoutError, Exception):
                            remote_task.cancel()
                    if not remote_task.done():
                        remote_task.cancel()
                    try:
                        await remote_task
                    except (asyncio.CancelledError, Exception):
                        pass
        except (OSError, websockets.exceptions.WebSocketException, HTTPException, RuntimeError, json.JSONDecodeError):
            try:
                await ws.send_json({"type": "error", "message": "FunASR 语音服务暂不可用"})
            except RuntimeError:
                pass
        finally:
            try:
                await ws.close()
            except RuntimeError:
                pass

    @app.websocket("/api/v1/rooms/{room}/events")
    async def events(ws: WebSocket, room: str):
        origin = ws.headers.get("origin")
        if origin and urlparse(origin).netloc != ws.headers.get("host"):
            await ws.close(code=1008)
            return
        await ws.accept()
        who, epoch = None, ""
        try:
            hello = await asyncio.wait_for(ws.receive_json(), timeout=8)
            require(isinstance(hello, dict), "需要有效的连接消息")
            who = store.auth(str(hello.get("token", "")), room)
            epoch = store.connect(room, who)
            await ws.send_json({"type": "connected", "epoch": epoch})
            last, cursor = "", 0
            while True:
                state = domain.snapshot(store.read(room), who)
                serialized = json.dumps({k: v for k, v in state.items() if k != "server_time"}, sort_keys=True, ensure_ascii=False)
                if serialized != last:
                    cursor += 1
                    await ws.send_json({"type": "snapshot", "cursor": cursor, "data": state})
                    last = serialized
                if state["status"] == "closed":
                    await ws.close(code=1000)
                    break
                try:
                    message = await asyncio.wait_for(ws.receive_json(), timeout=0.6)
                    if isinstance(message, dict) and message.get("type") == "heartbeat":
                        store.heartbeat(room, who, epoch)
                except asyncio.TimeoutError:
                    pass
        except (WebSocketDisconnect, asyncio.TimeoutError, HTTPException, ValueError, RuntimeError):
            try:
                await ws.close(code=1008)
            except RuntimeError:
                pass
        finally:
            if who:
                store.disconnect(room, who, epoch)

    app.mount("/assets", StaticFiles(directory=ROOT), name="assets")

    @app.get("/")
    @app.get("/room/{room}")
    def frontend():
        return FileResponse(ROOT / "index.html")

    return app


app = create_app()
