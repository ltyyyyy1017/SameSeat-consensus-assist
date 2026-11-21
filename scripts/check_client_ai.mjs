import assert from 'node:assert/strict';
import {PersonalActionModel} from '../tongxi/web/vision-policy.js';
import {CaptionMicrophone, encodePcm} from '../tongxi/web/caption-microphone.js';

const model = new PersonalActionModel();
assert.equal(model.predict([0, 0, 0, 0]), null);
const examples = {listen: [0, 0, 0, 0], speak: [2, 1, .5, .2], wait: [-1, -.8, 1, -.5]};
for (const [label, sample] of Object.entries(examples)) model.train(label, Array.from({length: 8}, () => sample));
for (const [label, sample] of Object.entries(examples)) assert.equal(model.predict(sample), label);
assert.equal(model.predict([10, 10, 10, 10]), null);
assert.equal(model.predict(examples.speak.map((v, i) => v/2)), null);

globalThis.WebSocket = {OPEN: 1};
const frames = [], errors = [];
const mic = new CaptionMicrophone({url: '', token: '', onError: error => errors.push(error.message)});
mic.socket = {readyState: 1, bufferedAmount: 0, send: data => frames.push(data), close: () => {}};
// 静音与低音量都交给真正的 VAD；连续超过八秒仍传输，不在浏览器切句。
for (let i=0; i<100; i++) {
  mic.accept(new Float32Array(2048).fill(i % 2 ? .003 : 0));
  mic.message({type: 'ack', frames: i+1});
}
assert.equal(frames.length, 100);
assert.equal(errors.length, 0);
const pcm = new DataView(encodePcm(new Float32Array([-1, 0, 1])));
assert.equal(pcm.getInt16(0, true), -32768);
assert.equal(pcm.getInt16(4, true), 32767);
mic.ackFrames = 0;
mic.accept(new Float32Array(2048));
assert.equal(errors.length, 1);
assert.equal(mic.closed, true);
console.log('Personal action abstention, continuous PCM and explicit backlog handling passed.');
