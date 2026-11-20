"""下载项目目录内的 CPU 推理资源，不修改系统环境。"""
import concurrent.futures
import hashlib
import json
import urllib.request
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / ".runtime"
FILES = {
    "llama.zip": "https://github.com/ggml-org/llama.cpp/releases/download/b12549/llama-b12549-bin-win-cpu-x64.zip",
    "qwen-1.5b.gguf": "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf?download=true",
    "embedding/model.onnx": "https://huggingface.co/Xenova/paraphrase-multilingual-MiniLM-L12-v2/resolve/main/onnx/model_quantized.onnx?download=true",
    "embedding/tokenizer.json": "https://huggingface.co/Xenova/paraphrase-multilingual-MiniLM-L12-v2/resolve/main/tokenizer.json?download=true",
    **{f"whisper-small/{name}": f"https://huggingface.co/Systran/faster-whisper-small/resolve/main/{name}?download=true"
       for name in ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"]},
}


def download(item):
    name, url = item
    target = ROOT / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        temporary = target.with_suffix(target.suffix + '.download')
        request = urllib.request.Request(url, headers={"User-Agent": "Tongxi-local-setup"})
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open('wb') as output:
            while chunk := response.read(1024*1024):
                output.write(chunk)
        temporary.replace(target)
    print(name, target.stat().st_size, flush=True)
    return name, {"source": url, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}


def main():
    ROOT.mkdir(exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        manifest = dict(pool.map(download, FILES.items()))
    destination = (ROOT / 'llama').resolve()
    destination.mkdir(exist_ok=True)
    with zipfile.ZipFile(ROOT / 'llama.zip') as archive:
        for info in archive.infolist():
            target = (destination / info.filename).resolve()
            if not target.is_relative_to(destination):
                raise ValueError('Unsafe archive path')
            if target.is_file() and target.stat().st_size == info.file_size and zlib.crc32(target.read_bytes()) == info.CRC:
                continue
            archive.extract(info, destination)
    (ROOT / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print('Local models ready.', flush=True)


if __name__ == '__main__':
    main()
