// 浏览器只负责收音与传输，语音检测、识别和标点交给本地 sherpa-onnx。
export function encodePcm(frame) {
  const buffer = new ArrayBuffer(frame.length * 2);
  const view = new DataView(buffer);
  for (let i = 0; i < frame.length; i++) {
    const sample = Number.isFinite(frame[i]) ? Math.max(-1, Math.min(1, frame[i])) : 0;
    view.setInt16(i * 2, Math.round(sample * (sample < 0 ? 32768 : 32767)), true);
  }
  return buffer;
}

export class CaptionMicrophone {
  constructor({url, token, onStatus = () => {}, onError = () => {}, onSentences = () => {}}) {
    Object.assign(this, {url, token, onStatus, onError, onSentences});
    this.sentFrames = 0;
    this.ackFrames = 0;
    this.closed = false;
    this.finished = false;
  }
  async start() {
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true}});
      if (this.closed) throw new Error('收音已取消');
      this.context = new AudioContext({sampleRate: 16000});
      if (this.context.sampleRate !== 16000) throw new Error('当前浏览器不支持本地字幕所需采样率，请使用新版 Chrome 或 Edge');
      await this.context.resume();
      this.socket = new WebSocket(this.url);
      this.onStatus('正在加载本地字幕组件，准备好后开始收音');
      await new Promise((resolve, reject) => {
        this.readyResolve = resolve;
        this.readyReject = reject;
        this.startTimer = setTimeout(() => this.fail(new Error('本地字幕组件加载超时，请检查模型文件')), 45000);
        this.socket.onopen = () => this.socket.send(JSON.stringify({token: this.token, sample_rate: 16000}));
        this.socket.onmessage = event => {
          try {this.message(JSON.parse(event.data));}
          catch {this.fail(new Error('字幕组件返回了无效消息，请重新开启'));}
        };
        this.socket.onerror = () => this.fail(new Error('字幕连接失败，请检查主机与网络'));
        this.socket.onclose = () => {
          if (!this.finished && !this.closed) this.fail(new Error('字幕连接已断开，末尾未确认的音频不会显示，请重新开启'));
        };
      });
      await this.context.audioWorklet.addModule('/assets/pcm-worklet.js');
      if (this.closed) throw new Error('收音已取消');
      this.node = new AudioWorkletNode(this.context, 'tongxi-pcm');
      this.node.port.onmessage = event => {
        if (event.data?.type === 'flushed') this.flushResolve?.();
        else this.accept(event.data);
      };
      this.source = this.context.createMediaStreamSource(this.stream);
      this.mute = this.context.createGain();
      this.mute.gain.value = 0;
      this.source.connect(this.node).connect(this.mute).connect(this.context.destination);
      this.onStatus('连续收音中 · 停顿后自动生成带标点的字幕');
    } catch (error) {this.abort(); throw error;}
  }
  message(message) {
    if (message.type === 'ready') {
      clearTimeout(this.startTimer);
      this.readyResolve?.();
    } else if (message.type === 'ack') this.ackFrames = message.frames;
    else if (message.type === 'sentences') this.onSentences(message.items);
    else if (message.type === 'done') {this.finished = true; this.doneResolve?.();}
    else if (message.type === 'error') this.fail(new Error(message.message));
  }
  accept(frame) {
    if (this.closed || this.finished || !frame.length) return;
    if (this.socket?.readyState !== WebSocket.OPEN) return this.fail(new Error('字幕连接不可用，已停止收音'));
    // 不按句数停机；仅在处理持续落后时明确报错，避免静默丢音频。
    if (this.sentFrames - this.ackFrames >= 64 || this.socket.bufferedAmount > 256000) {
      return this.fail(new Error('字幕处理持续积压，已停止收音。请等待后重新开启，末尾未确认内容可能缺失。'));
    }
    this.socket.send(encodePcm(frame));
    this.sentFrames++;
  }
  fail(error) {
    if (this.closed) return;
    this.readyReject?.(error);
    this.doneReject?.(error);
    this.abort();
    this.onError(error);
  }
  async stop() {
    if (this.closed) return;
    try {
      this.source?.disconnect();
      this.stream?.getTracks().forEach(track => track.stop());
      if (this.node) {
        await new Promise((resolve, reject) => {
          const timer = setTimeout(() => reject(new Error('收音尾帧未能提交，请重新开启字幕')), 1500);
          this.flushResolve = () => {clearTimeout(timer); resolve();};
          this.node.port.postMessage({type: 'flush'});
        });
      }
      if (this.closed) return;
      this.onStatus('正在完成最后一句字幕…');
      await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('最后一句处理超时，可能未完整写入，请核对字幕')), 45000);
        this.doneResolve = () => {clearTimeout(timer); resolve();};
        this.doneReject = error => {clearTimeout(timer); reject(error);};
        this.socket.send(JSON.stringify({type: 'stop'}));
      });
      this.onStatus('收音已停止，最后一句已处理');
    } finally {this.abort();}
  }
  abort() {
    this.closed = true;
    this.readyReject?.(new Error('收音已停止'));
    clearTimeout(this.startTimer);
    this.source?.disconnect();
    this.stream?.getTracks().forEach(track => track.stop());
    if (this.node) {this.node.port.onmessage = null; this.node.disconnect();}
    this.mute?.disconnect();
    this.socket?.close();
    this.context?.close().catch(() => {});
    this.stream = null;
  }
}
