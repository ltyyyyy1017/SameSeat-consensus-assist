class PcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Float32Array(2048);
    this.offset = 0;
    this.port.onmessage = event => {
      if (event.data?.type === 'flush') {
        if (this.offset) {
          const tail = this.buffer.slice(0, this.offset);
          this.port.postMessage(tail, [tail.buffer]);
          this.offset = 0;
        }
        this.port.postMessage({type: 'flushed'});
      }
    };
  }
  process(inputs) {
    const channels = inputs[0];
    if (!channels?.length) return true;
    const channel = channels[0];
    for (const value of channel) {
      this.buffer[this.offset++] = value;
      if (this.offset === this.buffer.length) {
        this.port.postMessage(this.buffer, [this.buffer.buffer]);
        this.buffer = new Float32Array(2048);
        this.offset = 0;
      }
    }
    return true;
  }
}
registerProcessor('tongxi-pcm', PcmCapture);
