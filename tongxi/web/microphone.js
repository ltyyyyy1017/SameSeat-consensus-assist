export function encodeWav(frames, rate) {
  const length = frames.reduce((sum, frame) => sum + frame.length, 0);
  const data = new ArrayBuffer(44 + length * 2);
  const view = new DataView(data);
  const text = (offset, value) => [...value].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)));
  text(0, 'RIFF'); view.setUint32(4, 36 + length * 2, true);
  text(8, 'WAVE'); text(12, 'fmt '); view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  text(36, 'data'); view.setUint32(40, length * 2, true);
  let offset = 44;
  for (const frame of frames) for (const sample of frame) {
    view.setInt16(offset, Math.max(-1, Math.min(1, sample)) * 32767, true); offset += 2;
  }
  return new Blob([data], {type: 'audio/wav'});
}

export class PhraseMicrophone {
  constructor(onPhrase, onStatus) {
    this.onPhrase = onPhrase;
    this.onStatus = onStatus;
    this.stream = null;
    this.frames = [];
    this.preRoll = [];
    this.duration = 0;
    this.voiced = 0;
    this.silence = 0;
  }
  async start() {
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true}});
      this.context = new AudioContext();
      await this.context.audioWorklet.addModule('/assets/pcm-worklet.js');
      this.node = new AudioWorkletNode(this.context, 'tongxi-pcm');
      this.source = this.context.createMediaStreamSource(this.stream);
      const mute = this.context.createGain(); mute.gain.value = 0;
      this.source.connect(this.node).connect(mute).connect(this.context.destination);
      this.node.port.onmessage = event => this.accept(event.data);
      await this.context.resume();
      this.onStatus('正在收音，讲话停顿后自动转写');
    } catch (error) {this.stop(false); throw error;}
  }
  accept(frame) {
    if (!this.stream) return;
    const dt = frame.length / this.context.sampleRate;
    const rms = Math.sqrt(frame.reduce((sum, value) => sum + value * value, 0) / frame.length);
    const speaking = rms > .012;
    if (!this.frames.length) {
      this.preRoll.push(frame);
      while (this.preRoll.length * dt > .32) this.preRoll.shift();
      if (!speaking) return;
      this.frames = this.preRoll;
      this.preRoll = [];
    } else this.frames.push(frame);
    this.duration += dt;
    if (speaking) {this.voiced += dt; this.silence = 0;}
    else this.silence += dt;
    if (this.duration >= 8 || this.silence >= .65) this.flush();
  }
  flush() {
    const blob = this.frames.length && this.voiced >= .25 ? encodeWav(this.frames, this.context.sampleRate) : null;
    this.frames = []; this.duration = 0; this.voiced = 0; this.silence = 0;
    if (blob) this.onPhrase(blob);
  }
  stop(flush = true) {
    if (flush && this.context) this.flush();
    this.stream?.getTracks().forEach(track => track.stop());
    this.stream = null;
    if (this.node) {this.node.port.onmessage = null; this.node.disconnect();}
    this.source?.disconnect();
    this.context?.close().catch(() => {});
    this.frames = []; this.preRoll = [];
  }
}

function toPcm16(samples, fromRate) {
  const outLen = Math.max(1, Math.floor(samples.length * 16000 / fromRate));
  const pcm = new Int16Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const pos = i * fromRate / 16000;
    const left = Math.floor(pos);
    const right = Math.min(left + 1, samples.length - 1);
    const mixed = samples[left] * (1 - (pos - left)) + samples[right] * (pos - left);
    const value = Math.max(-1, Math.min(1, mixed));
    pcm[i] = value < 0 ? value * 0x8000 : value * 0x7fff;
  }
  return pcm;
}

export class StreamMicrophone {
  constructor(session, onStatus) {
    this.session = session;
    this.onStatus = onStatus;
    this.stream = null;
    this.socket = null;
  }
  async start() {
    this.stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true, channelCount: 1}});
    this.context = new AudioContext();
    await this.context.audioWorklet.addModule('/assets/pcm-worklet.js');
    this.node = new AudioWorkletNode(this.context, 'tongxi-pcm');
    this.source = this.context.createMediaStreamSource(this.stream);
    const mute = this.context.createGain();
    mute.gain.value = 0;
    this.source.connect(this.node).connect(mute).connect(this.context.destination);
    this.socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/v1/rooms/${this.session.room}/asr`);
    await new Promise((resolve, reject) => {
      this.socket.onopen = resolve;
      this.socket.onerror = () => reject(new Error('无法连接 FunASR 转写通道'));
    });
    this.socket.send(JSON.stringify({token: this.session.token}));
    this.socket.onmessage = event => {
      try {
        const message = JSON.parse(event.data);
        if (message.type === 'error') this.onStatus(message.message);
      } catch { /* 二进制回执不用于字幕，字幕由房间快照更新。 */ }
    };
    this.node.port.onmessage = event => {
      if (this.socket?.readyState !== WebSocket.OPEN) return;
      this.socket.send(toPcm16(event.data, this.context.sampleRate).buffer);
    };
    await this.context.resume();
    this.onStatus('正在连续转写。临时字幕会在句末修正，修正后的稳定字幕才进入议题分析。');
  }
  stop() {
    if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify({type: 'stop'}));
    this.socket?.close();
    this.socket = null;
    this.stream?.getTracks().forEach(track => track.stop());
    this.stream = null;
    if (this.node) {this.node.port.onmessage = null; this.node.disconnect();}
    this.source?.disconnect();
    this.context?.close().catch(() => {});
  }
}
