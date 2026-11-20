"""合成中文原句，经本地语音、转写、语义模型执行一次真实推理。"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tongxi import adapters


def main():
    phrase = '方案B需要实验室设备，周六可以使用实验室。'
    start = time.perf_counter()
    tts = adapters.LocalTTS()
    wav = tts.synthesize('verification', {"id": "s1", "rev": 1, "text": phrase})
    tts_time = time.perf_counter()-start
    asr = adapters.LocalASR()
    start = time.perf_counter()
    transcript = asr.transcribe(wav)
    asr_time = time.perf_counter()-start
    local = adapters.LocalSemantic()
    try:
        start = time.perf_counter()
        result = adapters.candidates({"segments": [{"id": "s1", "rev": 1, "stable": True, "text": phrase},
                {"id": "s2", "rev": 1, "stable": True, "text": "那就选择方案B。"}]}, local)
        elapsed = time.perf_counter()-start
    finally:
        local.close()
    report = {"input": phrase, "tts_bytes": len(wav), "tts_seconds": round(tts_time, 2),
              "asr_text": transcript, "asr_seconds": round(asr_time, 2), "semantic_candidates": result,
              "semantic_seconds_including_load": round(elapsed, 2), "note": "合成中文语音测试，非真人讨论效果"}
    folder = Path(__file__).resolve().parents[1] / 'artifacts'
    folder.mkdir(exist_ok=True)
    (folder / 'model-check.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
