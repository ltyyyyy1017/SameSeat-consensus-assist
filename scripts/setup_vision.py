"""准备本地静态视觉资源。安装后浏览器运行无需外网。"""
import hashlib
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "tongxi/web/vendor"
BASE = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.22-rc.20250304"
FILES = {
    "vision_bundle.mjs": BASE + "/vision_bundle.mjs",
    "wasm/vision_wasm_internal.js": BASE + "/wasm/vision_wasm_internal.js",
    "wasm/vision_wasm_internal.wasm": BASE + "/wasm/vision_wasm_internal.wasm",
    "wasm/vision_wasm_nosimd_internal.js": BASE + "/wasm/vision_wasm_nosimd_internal.js",
    "wasm/vision_wasm_nosimd_internal.wasm": BASE + "/wasm/vision_wasm_nosimd_internal.wasm",
    "face_landmarker.task": "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
    "pose_landmarker_lite.task": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task",
    "LICENSE": "https://raw.githubusercontent.com/google-ai-edge/mediapipe/master/LICENSE",
}


def main():
    manifest = {}
    manifest_path = ROOT / "manifest.json"
    previous = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    for relative, url in FILES.items():
        target = ROOT / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or previous.get(relative, {}).get("source") != url:
            with urllib.request.urlopen(url, timeout=60) as response:
                data = response.read()
            temporary = target.with_suffix(target.suffix + ".download")
            temporary.write_bytes(data)
            temporary.replace(target)
        manifest[relative] = {"source": url, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
        print(relative, target.stat().st_size)
    (ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
