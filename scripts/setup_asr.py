"""准备现成字幕底座；模型仅下载到项目内，运行时不自动联网。"""
import concurrent.futures
import hashlib
import json
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / '.runtime' / 'asr'
REVISION = '2365baeacb507f821a0c8120fcee3d484dba7a07'
REPOSITORY = 'https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17'
FILES = {
    'model.int8.onnx': f'{REPOSITORY}/resolve/{REVISION}/model.int8.onnx',
    'tokens.txt': f'{REPOSITORY}/resolve/{REVISION}/tokens.txt',
    'SenseVoice-LICENSE.txt': f'{REPOSITORY}/resolve/{REVISION}/LICENSE',
    'silero_vad.onnx': 'https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx',
    'sherpa-onnx-LICENSE.txt': 'https://raw.githubusercontent.com/k2-fsa/sherpa-onnx/v1.13.8/LICENSE',
    'Silero-LICENSE.txt': 'https://raw.githubusercontent.com/snakers4/silero-vad/master/LICENSE',
}


def sha256(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def source_candidates(source):
    found = [source]
    prefix = 'https://huggingface.co/'
    if source.startswith(prefix):
        found.append('https://hf-mirror.com/' + source[len(prefix):])
    return found


def download(item):
    name, source = item
    target = ROOT / name
    known = {}
    manifest = ROOT / 'manifest.json'
    if manifest.exists():
        known = json.loads(manifest.read_text(encoding='utf-8')).get(name, {})
    if target.exists() and known.get('source') in source_candidates(source) and known.get('sha256') == sha256(target):
        print('Already ready:', name, flush=True)
        return name, known
    temporary = target.with_suffix(target.suffix + '.download')
    last_error = None
    for candidate in source_candidates(source):
        for attempt in range(3):
            try:
                request = urllib.request.Request(candidate, headers={'User-Agent': 'Tongxi-local-ASR-setup'})
                with urllib.request.urlopen(request, timeout=45) as response, temporary.open('wb') as output:
                    expected = int(response.headers.get('Content-Length', 0))
                    size = 0
                    while block := response.read(1024 * 1024):
                        output.write(block)
                        size += len(block)
                    if expected and size != expected:
                        raise OSError(f'Incomplete download: {name}')
                if name == 'model.int8.onnx' and temporary.stat().st_size != 239233841:
                    raise OSError('Unexpected SenseVoice model size')
                temporary.replace(target)
                print('Downloaded:', name, target.stat().st_size, flush=True)
                return name, {'source': candidate, 'sha256': sha256(target), 'bytes': target.stat().st_size}
            except OSError as exc:
                last_error = exc
                if attempt == 2:
                    break
                time.sleep(attempt + 1)
    raise last_error


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        manifest = dict(pool.map(download, FILES.items()))
    (ROOT / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print('SenseVoice + Silero VAD ready. Runtime stays local.', flush=True)


if __name__ == '__main__':
    main()
