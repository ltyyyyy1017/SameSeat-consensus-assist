"""E1 浏览器预演。使用模拟关键点，不招募真人，不能当成真人证据。"""
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "artifacts" / "e1-final-browser"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"

DETECTOR = r"""
window.__tongxiDebugCalls = 0;
window.__tongxiDebugDetector = function (kind) {
  const point = (x, y) => ({x, y, visibility: 1});
  if (kind === 'pose') {
    const pose = [];
    pose[11] = point(0.30, 0.80);
    pose[12] = point(0.70, 0.80);
    return {landmarks: [pose]};
  }
  const status = (document.getElementById('camera-status') || {}).textContent || '';
  const duringBaseline = status.indexOf('第一帧') >= 0 || status.indexOf('250 毫秒') >= 0;
  if (duringBaseline) window.__tongxiDebugCalls += 1;
  const mode = sessionStorage.getItem('tongxi-debug-mode') || 'fail';
  if (mode === 'fail' && duringBaseline && window.__tongxiDebugCalls > 23) return {faceLandmarks: []};
  const wobble = (window.__tongxiDebugCalls % 7) * 0.001;
  const face = [];
  face[1] = point(0.50, 0.42 + wobble);
  face[13] = point(0.48, 0.55);
  face[14] = point(0.52, 0.55 + wobble);
  face[33] = point(0.42, 0.48);
  face[263] = point(0.58, 0.48);
  return {faceLandmarks: [face]};
};
"""


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_health(port):
    for _ in range(40):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/health", timeout=1) as response:
                payload = json.loads(response.read().decode())
                if payload.get("e1") is True:
                    return
        except Exception:
            time.sleep(0.25)
    raise RuntimeError("E1 服务没有在预演端口就绪")


def _api(port, path, token=None, body=None, method=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        method=method or ("POST" if body is not None else "GET"),
        headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + token} if token else {})},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode())


def _enroll_seven(directory, room):
    sys.path.insert(0, str(ROOT))
    from tongxi.e1 import ExperimentStore

    store = ExperimentStore(directory)
    samples = {"listen": [[0, 0, 0, 0]] * 6, "speak": [[0, 1, 0, 0]] * 6, "wait": [[1, 0, 0, 0]] * 6}
    for index in range(7):
        rows = []
        for frame in range(32):
            delta = frame * 0.0001
            rows.append({
                "raw": {"scale": 0.2 + delta, "mouth": 0.2 + index * 0.01, "pitch": 0.1, "turn": 0.0},
                "visibility_status": "ok", "face_count": 1, "shoulder_valid": True,
            })
        member = f"sim-{index}"
        ticket = store.begin_baseline_attempt(room, member, {"session_role": "calibration", "calibration_id": f"sim-{index}"})
        store.add_calibration(room, member, {
            "session_role": "calibration",
            "calibration_id": f"sim-{index}",
            "duration_ms": 8000,
            "video_width": 640,
            "video_height": 480,
            "attempt_id": ticket["attempt_id"],
            "baseline_measurements": rows,
            "samples": samples,
        })
    built = store.build_reference(room)
    frozen = store.freeze_reference(room)
    return {"built_ready": built["ready"], "frozen_ready": frozen["ready"], "count": frozen["participant_count"], "human_evidence": False}


def test_browser_rehearsal_uses_a_simulated_camera(tmp_path):
    port = _free_port()
    env = os.environ.copy()
    env["TONGXI_E1"] = "1"
    env["TONGXI_E1_DIR"] = str(tmp_path)
    SHOTS.mkdir(parents=True, exist_ok=True)
    server_log = open(SHOTS / "服务日志.txt", "w", encoding="utf-8")
    server = subprocess.Popen(
        [str(PYTHON), "-m", "uvicorn", "tongxi.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=server_log,
        stderr=subprocess.STDOUT,
    )
    notes = {"human_evidence": False, "simulated_camera": True, "port": port}
    try:
        _wait_health(port)
        host = _api(port, "/api/v1/rooms", body={"title": "E1 模拟预演", "name": "主持人"})
        snap = _api(port, f"/api/v1/rooms/{host['room']}/snapshot", token=host["token"])
        member = _api(port, "/api/v1/join", body={"invite": snap["invite"], "name": "模拟参与者"})
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                channel="msedge",
                headless=True,
                args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"],
            )
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.add_init_script(DETECTOR)
            page.goto(f"http://127.0.0.1:{port}/room/{member['room']}#token={member['token']}&role=member")
            page.evaluate("sessionStorage.setItem('tongxi-debug-mode', 'fail')")
            page.reload()
            page.on("pageerror", lambda error: notes.setdefault("pageerrors", []).append(str(error)))

            def open_camera():
                page.get_by_role("button", name="个人与设备").click()
                button = page.locator("[data-action=camera]")
                button.evaluate("node => node.scrollIntoView({block: 'center'})")
                button.click()
                page.wait_for_function("() => { const node = document.querySelector('[data-action=calibrate]'); return node && !node.disabled; }")

            open_camera()
            page.get_by_role("button", name="校准自然坐姿").click()
            page.wait_for_function("() => (document.getElementById('camera-status') || {}).textContent.includes('第一帧立即采集')")
            page.locator("#e1-panel").scroll_into_view_if_needed()
            page.screenshot(path=str(SHOTS / "采集进行中.png"))
            page.wait_for_function(
                "() => (document.getElementById('e1-error') || {}).textContent.includes('基准没有更新')",
                timeout=20000,
            )
            page.locator("#e1-panel").scroll_into_view_if_needed()
            page.screenshot(path=str(SHOTS / "采集失败.png"))
            page.evaluate("sessionStorage.setItem('tongxi-debug-mode', 'ok')")
            page.reload(wait_until="networkidle")
            open_camera()
            page.get_by_role("button", name="校准自然坐姿").click()
            page.wait_for_function(
                "() => (document.getElementById('e1-error') || {}).textContent.includes('中位数')",
                timeout=20000,
            )
            page.locator("#e1-panel").scroll_into_view_if_needed()
            page.screenshot(path=str(SHOTS / "采集成功.png"))
            status = _api(port, f"/api/v1/rooms/{member['room']}/e1/calibration", token=member["token"])
            baseline = status["baseline"]
            page.evaluate(
                """(text) => {
                  const node = document.getElementById('e1-error');
                  if (node) node.textContent = text;
                }""",
                "个人基准已写入。"
                f" scale {baseline['scale']:.4f}，mouth {baseline['mouth']:.4f}，"
                f"pitch {baseline['pitch']:.4f}，turn {baseline['turn']:.4f}。"
                f" MAD mouth {baseline['mad']['mouth']:.4f}。这是模拟数据。",
            )
            page.locator("#e1-panel").scroll_into_view_if_needed()
            page.screenshot(path=str(SHOTS / "个人基准完成.png"))
            page.reload()
            open_camera()
            page.get_by_role("button", name="校准自然坐姿").click()
            page.wait_for_function(
                "() => (document.getElementById('e1-error') || {}).textContent.includes('不能再开始')",
                timeout=10000,
            )
            page.locator("#e1-panel").scroll_into_view_if_needed()
            page.screenshot(path=str(SHOTS / "重试被拒绝.png"))
            page.wait_for_function("() => window.tongxiVision && window.tongxiVision.baseline")
            for label in ("listen", "speak", "wait"):
                button = page.locator(f"[data-vision-sample={label}]")
                button.evaluate("node => node.scrollIntoView({block: 'center'})")
                button.click()
                page.wait_for_function("() => ((document.getElementById('camera-status') || {}).textContent || '').includes('样例采集')")
                page.wait_for_function(
                    "() => ((document.getElementById('camera-status') || {}).textContent || '').includes('已记录')",
                    timeout=8000,
                )
            page.wait_for_function(
                "() => (document.getElementById('e1-error') || {}).textContent.includes('三类样例已锁定')",
                timeout=15000,
            )
            notes["freeze"] = _enroll_seven(tmp_path, member["room"])
            page.once("dialog", lambda dialog: dialog.accept())
            page.get_by_role("button", name="改为第二次讨论").click()
            page.wait_for_function(
                "() => document.body.innerText.includes('第二次讨论：使用已锁定')",
                timeout=15000,
            )
            page.locator("[data-action=calibrate]").evaluate("node => node.scrollIntoView({block: 'center'})")
            page.locator("[data-action=calibrate]").click()
            page.wait_for_function(
                "() => (document.getElementById('e1-error') || {}).textContent.includes('第二次讨论不能重新采集')",
                timeout=10000,
            )
            page.locator("#e1-panel").scroll_into_view_if_needed()
            page.screenshot(path=str(SHOTS / "第二次讨论不能重校准.png"))
            browser.close()
        me = _api(port, f"/api/v1/rooms/{member['room']}/snapshot", token=member["token"])["me"]["id"]
        connection = sqlite3.connect(tmp_path / f"{member['room']}.sqlite")
        rows = connection.execute(
            "SELECT attempt_no, status, total_frames, valid_frames, log FROM baseline_attempts WHERE member_id=? ORDER BY attempt_no",
            (me,),
        ).fetchall()
        connection.close()
        assert [row[0] for row in rows] == [1, 2]
        assert rows[0][1] == "failed" and rows[0][3] == 23
        assert rows[1][1] == "succeeded" and 30 <= rows[1][2] <= 32 and rows[1][3] >= 24
        log = json.loads(rows[1][4])
        assert log[0]["sampled_at_ms"] < 80
        assert 150 <= log[1]["sampled_at_ms"] <= 450
        assert status["attempts_used"] == 2
        assert status["baseline"]["protocol"] == "neutral_pose_median_v1"
        for key in ("scale", "mouth", "pitch", "turn"):
            assert status["baseline"][key] == status["baseline"][key]
            assert status["baseline"]["mad"][key] == status["baseline"]["mad"][key]
        notes["attempts"] = [
            {"attempt_no": row[0], "status": row[1], "total_frames": row[2], "valid_frames": row[3]}
            for row in rows
        ]
        notes["first_two_sample_times_ms"] = [log[0]["sampled_at_ms"], log[1]["sampled_at_ms"]]
        notes["baseline"] = {key: status["baseline"][key] for key in ("scale", "mouth", "pitch", "turn", "mad", "sampled_frames", "valid_frames", "duration_ms", "attempt")}
        (SHOTS / "预演结果.json").write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        server.terminate()
        try:
            server.wait(timeout=8)
        except subprocess.TimeoutExpired:
            server.kill()
        server_log.close()
