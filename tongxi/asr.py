"""复用 sherpa-onnx 的 SenseVoice 与 Silero VAD，不训练或改写识别模型。"""
import io
import os
import re
import threading
from pathlib import Path

from fastapi import HTTPException

from .store import require

SAMPLE_RATE = 16000
DEFAULT_MODELS = Path(__file__).resolve().parents[1] / '.runtime' / 'asr'


def model_directory():
    return Path(os.getenv('TONGXI_ASR_MODEL', str(DEFAULT_MODELS)))


def asr_capabilities():
    path = model_directory()
    ready = all((path / name).is_file() for name in ('model.int8.onnx', 'tokens.txt', 'silero_vad.onnx'))
    return {'ready': ready, 'label': '本地 SenseVoice · sherpa-onnx',
            'engine': 'sherpa-onnx', 'model': 'SenseVoiceSmall int8',
            'endpointing': 'Silero VAD', 'punctuation': True,
            'output': '停顿后输出带标点的句子，不是逐字流式识别'}


def split_sentences(text):
    """只按识别结果里的标点整理，不用语言模型补造或润色原话。"""
    text = re.sub(r'<\|[^>]*\|>', '', text).strip()
    sentences = []
    for paragraph in text.splitlines():
        for match in re.finditer(r'[^。！？!?]+(?:[。！？!?]+[”’」』"]*|$)', paragraph):
            sentence = match.group().strip()
            # 与原句接口长度约束一致；正常语音端点不会达到此长度。
            sentences.extend(sentence[i:i + 1500] for i in range(0, len(sentence), 1500))
    return sentences


def decode_audio(audio):
    """沿用 PyAV 解码/重采样，便于文件接口与旧验证脚本继续使用。"""
    try:
        import av
        import numpy as np
        chunks, size = [], 0
        with av.open(io.BytesIO(audio)) as container:
            resampler = av.AudioResampler(format='s16', layout='mono', rate=SAMPLE_RATE)
            for frame in container.decode(audio=0):
                for converted in resampler.resample(frame):
                    values = converted.to_ndarray().reshape(-1).astype(np.float32) / 32768
                    size += len(values)
                    require(size <= 60 * SAMPLE_RATE, '文件转写每次最多 60 秒，请使用连续字幕入口', 413)
                    chunks.append(values)
            for converted in resampler.resample(None):
                chunks.append(converted.to_ndarray().reshape(-1).astype(np.float32) / 32768)
        require(bool(chunks), '录音没有可解码的音轨')
        return np.concatenate(chunks)
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(422, '无法解码这段录音，请检查格式或收音') from error


class LocalASR:
    def __init__(self):
        self.model = None
        self.lock = threading.Lock()

    def _load(self):
        if self.model is not None:
            return
        require(asr_capabilities()['ready'], '请先运行 scripts/setup_asr.py 准备本地字幕组件', 503)
        try:
            import sherpa_onnx
        except ImportError as error:
            raise HTTPException(503, '请安装 requirements-ai.txt 中的 sherpa-onnx') from error
        path = model_directory()
        self.model = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(path / 'model.int8.onnx'), tokens=str(path / 'tokens.txt'),
            language='zh', use_itn=True, num_threads=4, provider='cpu', debug=False)

    def recognize(self, samples):
        import numpy as np
        if not len(samples):
            return ''
        with self.lock:
            self._load()
            stream = self.model.create_stream()
            stream.accept_waveform(SAMPLE_RATE, np.asarray(samples, dtype=np.float32))
            self.model.decode_stream(stream)
            return re.sub(r'<\|[^>]*\|>', '', stream.result.text).strip()

    def new_stream(self):
        with self.lock:
            self._load()
        return CaptionStream(self)

    def transcribe(self, audio):
        session = self.new_stream()
        results = session.accept(decode_audio(audio)) + session.finish()
        return ''.join(results)


class CaptionStream:
    """一个录音连接一份官方 VAD 状态；ASR 模型复用，音频不落盘。"""
    def __init__(self, recognizer):
        import sherpa_onnx
        import numpy as np
        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = str(model_directory() / 'silero_vad.onnx')
        config.silero_vad.threshold = .5
        config.silero_vad.min_silence_duration = .55
        config.silero_vad.min_speech_duration = .25
        config.silero_vad.max_speech_duration = 25
        config.sample_rate = SAMPLE_RATE
        config.num_threads = 1
        self.window = config.silero_vad.window_size
        self.vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=40)
        self.pending = np.zeros(0, dtype=np.float32)
        self.recognizer = recognizer
        self.finished = False

    def accept(self, samples):
        import numpy as np
        require(not self.finished, '录音已经结束', 409)
        self.pending = np.concatenate((self.pending, np.asarray(samples, dtype=np.float32)))
        count = len(self.pending) // self.window * self.window
        for offset in range(0, count, self.window):
            self.vad.accept_waveform(self.pending[offset:offset + self.window])
        self.pending = self.pending[count:]
        return self._drain()

    def accept_pcm(self, pcm):
        import numpy as np
        return self.accept(np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768)

    def _drain(self):
        results = []
        while not self.vad.empty():
            samples = self.vad.front.samples.copy()
            self.vad.pop()
            results.extend(split_sentences(self.recognizer.recognize(samples)))
        return results

    def finish(self):
        import numpy as np
        if self.finished:
            return []
        if len(self.pending):
            padded = np.pad(self.pending, (0, self.window - len(self.pending)))
            self.vad.accept_waveform(padded)
        self.pending = np.zeros(0, dtype=np.float32)
        self.vad.flush()
        self.finished = True
        return self._drain()
