"""所有房间变更、去重结果与审计在同一内存 SQLite 事务提交。"""
import copy
import hashlib
import json
import secrets
import sqlite3
import threading
import time

from fastapi import HTTPException


def uid(prefix):
    return prefix + secrets.token_hex(5)


def require(condition, message, code=422):
    if not condition:
        raise HTTPException(code, message)


class Store:
    def __init__(self, auto_ai=True):
        self.auto_ai = auto_ai
        self.lock = threading.RLock()
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.executescript("""
            CREATE TABLE rooms (id TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE sessions (token TEXT PRIMARY KEY, room TEXT, member TEXT, role TEXT, expires REAL);
            CREATE TABLE receipts (room TEXT, actor TEXT, command TEXT, fingerprint TEXT, result TEXT,
                PRIMARY KEY(room, actor, command));
        """)

    def load(self, room):
        row = self.db.execute("SELECT body FROM rooms WHERE id=?", (room,)).fetchone()
        require(row is not None, "房间不存在或已重启，请重新加入", 404)
        return json.loads(row[0])

    def save(self, room):
        self.db.execute("INSERT OR REPLACE INTO rooms VALUES (?,?)", (room["id"], json.dumps(room, ensure_ascii=False)))

    def session(self, room, member, role):
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        self.db.execute("INSERT INTO sessions VALUES (?,?,?,?,?)", (digest, room, member, role, time.time() + 12*3600))
        return token

    def auth(self, token, room):
        with self.lock:
            digest = hashlib.sha256(token.encode()).hexdigest()
            row = self.db.execute("SELECT member,role,expires FROM sessions WHERE token=? AND room=?", (digest, room)).fetchone()
            require(row is not None and row[2] > time.time(), "会话已失效，请重新加入房间", 401)
            return {"id": row[0], "role": row[1]}

    def create(self, title, name):
        with self.lock, self.db:
            room_id, member = uid("r"), uid("m")
            invite = secrets.token_urlsafe(18)
            room = {"id": room_id, "title": title, "revision": 1, "status": "open",
                    "invite": invite, "created": time.time(), "members": {}, "segments": [],
                    "decisions": [], "requests": [], "contributions": [], "history": [], "demo": False,
                    "ai_enabled": self.auto_ai, "analysis": {"status": "idle", "items": [], "summary": "", "phase": "uncertain", "error": ""}}
            room["members"][member] = self.member(name, "host")
            token = self.session(room_id, member, "host")
            board = self.session(room_id, uid("b"), "board")
            room["board_token"] = board
            self.save(room)
            return {"room": room_id, "token": token, "role": "host"}

    @staticmethod
    def member(name, role):
        return {"name": name, "role": role, "consent": {"version": 1, "playback": False, "review": False, "response_seconds": 20},
                "presentation": None, "epoch": "", "seq": 0, "heartbeat": 0}

    def join(self, invite, name):
        with self.lock, self.db:
            for (body,) in self.db.execute("SELECT body FROM rooms").fetchall():
                room = json.loads(body)
                if room["status"] == "open" and secrets.compare_digest(room["invite"], invite):
                    require(time.time()-room["created"] < 12*3600, "邀请已过期", 401)
                    require(len(room["members"]) < 20, "原型每房间最多 20 人")
                    member = uid("m")
                    room["members"][member] = self.member(name, "member")
                    token = self.session(room["id"], member, "member")
                    room["revision"] += 1
                    self.save(room)
                    return {"room": room["id"], "token": token, "role": "member"}
            raise HTTPException(404, "邀请无效、过期或房间已经结束")

    def read(self, room):
        with self.lock:
            return self.load(room)

    def mutate(self, room_id, actor, command, payload, operation):
        require(8 <= len(command) <= 100, "需要有效的 Idempotency-Key", 400)
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        with self.lock, self.db:
            room = self.load(room_id)
            require(room["status"] == "open", "讨论已结束", 410)
            prior = self.db.execute("SELECT fingerprint,result FROM receipts WHERE room=? AND actor=? AND command=?",
                                    (room_id, actor["id"], command)).fetchone()
            if prior:
                require(prior[0] == fingerprint, "同一命令编号不能用于不同操作", 409)
                return json.loads(prior[1])
            before = json.dumps({k: v for k, v in room.items() if k not in {"members", "analysis", "ai_enabled"}}, sort_keys=True)
            result = operation(room)
            after = json.dumps({k: v for k, v in room.items() if k not in {"members", "analysis", "ai_enabled"}}, sort_keys=True)
            if before != after:
                room["revision"] += 1
            self.save(room)
            self.db.execute("INSERT INTO receipts VALUES (?,?,?,?,?)",
                            (room_id, actor["id"], command, fingerprint, json.dumps(result)))
            return result

    def connect(self, room_id, actor):
        if actor["role"] == "board":
            return ""
        with self.lock, self.db:
            room = self.load(room_id)
            require(room["status"] == "open", "讨论已结束", 410)
            member = room["members"][actor["id"]]
            epoch = secrets.token_hex(8)
            member.update(epoch=epoch, seq=0, presentation=None, heartbeat=time.monotonic())
            self.save(room)
            return epoch

    def heartbeat(self, room_id, actor, epoch):
        if actor["role"] == "board":
            return
        with self.lock, self.db:
            room = self.load(room_id)
            if room["status"] == "open":
                member = room["members"][actor["id"]]
                if member["epoch"] == epoch:
                    member["heartbeat"] = time.monotonic()
                    self.save(room)

    def disconnect(self, room_id, actor, epoch):
        if actor["role"] == "board":
            return
        with self.lock, self.db:
            room = self.load(room_id)
            if room["status"] == "open" and room["members"][actor["id"]]["epoch"] == epoch:
                room["members"][actor["id"]].update(presentation=None, epoch="", heartbeat=0)
                self.save(room)
