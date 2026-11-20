"""仅使用预下载 ONNX 模型在本地计算多语言语义向量。"""
import threading
from pathlib import Path


class SentenceEncoder:
    def __init__(self):
        self.lock = threading.Lock()
        self.session = None
        self.tokenizer = None

    def encode(self, texts):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer
        root = Path(__file__).resolve().parents[1] / '.runtime/embedding'
        with self.lock:
            if self.session is None:
                options = ort.SessionOptions()
                options.intra_op_num_threads = 4
                self.session = ort.InferenceSession(str(root / 'model.onnx'), sess_options=options, providers=['CPUExecutionProvider'])
                self.tokenizer = Tokenizer.from_file(str(root / 'tokenizer.json'))
                self.tokenizer.enable_truncation(max_length=128)
                self.tokenizer.enable_padding()
            encoded = self.tokenizer.encode_batch(texts)
            inputs = {'input_ids': np.array([x.ids for x in encoded], dtype=np.int64),
                      'attention_mask': np.array([x.attention_mask for x in encoded], dtype=np.int64),
                      'token_type_ids': np.array([x.type_ids for x in encoded], dtype=np.int64)}
            valid = {x.name for x in self.session.get_inputs()}
            output = self.session.run(None, {k: v for k, v in inputs.items() if k in valid})[0]
            mask = inputs['attention_mask'][..., None]
            pooled = (output * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1)
            return pooled / np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-9)


encoder = SentenceEncoder()


def rank(text, packed):
    passages = [q['text'] for d in packed for q in d['quotes']]
    vectors = encoder.encode([text] + passages)
    scores = vectors[1:] @ vectors[0]
    ranked, offset = [], 0
    for d in packed:
        own = scores[offset:offset+len(d['quotes'])]
        best = int(own.argmax())
        ranked.append({'decision': d['id'], 'segment': d['quotes'][best]['segment'], 'similarity': float(own[best])})
        offset += len(d['quotes'])
    return sorted(ranked, key=lambda item: item['similarity'], reverse=True)
