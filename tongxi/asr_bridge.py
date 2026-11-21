"""FunASR 2-pass 消息组装。临时片段只累积当前句，句末离线结果替换同一句。"""


class CaptionAssembler:
    def __init__(self):
        self.online = ""

    def feed(self, message):
        if not isinstance(message, dict):
            return None
        mode = message.get("mode")
        piece = message.get("text") or ""
        if mode == "2pass-online":
            if not piece:
                return None
            self.online += piece
            shown = self.online.strip()[:1500]
            if not shown:
                return None
            return {"text": shown, "stable": False}
        if mode == "2pass-offline":
            sentence = (piece or self.online).strip()[:1500]
            self.online = ""
            if not sentence:
                return None
            return {"text": sentence, "stable": True}
        return None
