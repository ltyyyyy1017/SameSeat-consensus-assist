import assert from 'node:assert/strict';
import {PersonalActionModel, TemporalActionModel} from '../tongxi/web/vision-policy.js';
import {InterventionPolicy} from '../tongxi/web/intervention-policy.js';
import {CaptionMicrophone, encodePcm} from '../tongxi/web/caption-microphone.js';

const model = new PersonalActionModel();
assert.equal(model.predict([0, 0, 0, 0]), null);
const examples = {listen: [0, 0, 0, 0], speak: [2, 1, .5, .2], wait: [-1, -.8, 1, -.5]};
for (const [label, sample] of Object.entries(examples)) model.train(label, Array.from({length: 8}, () => sample));
for (const [label, sample] of Object.entries(examples)) assert.equal(model.predict(sample), label);
assert.equal(model.predict([10, 10, 10, 10]), null);
assert.equal(model.predict(examples.speak.map((v, i) => v/2)), null);

const temporal = new TemporalActionModel();
for (const [label, sample] of Object.entries(examples)) temporal.train(label, Array.from({length: 8}, () => sample));
assert.equal(temporal.predict([examples.speak]), null, '单帧不能触发连续动作建议');
assert.equal(temporal.predict(Array.from({length: 8}, () => examples.speak)), 'speak');
assert.equal(temporal.predict(Array.from({length: 8}, (_, i) => [i*2,0,0,0])), null, '大幅机位变化弃判');
const policy = new InterventionPolicy();
const context = {visible:true, online:true, busy:false, hasRequest:false, decisions:[{id:'d1',status:'discussing',evidence:[{segment:'s1',rev:2}]}],receiving:[]};
assert.equal(policy.decide('speak', context, 0), null, '自由讨论不反复弹出定案提示');
context.decisions[0].status='preparing';
context.receiving=[{segment:'s1',rev:2}];
assert.equal(policy.decide('speak', {...context,hasRequest:true}, 1), null, '已有请求时抑制重复');
assert.equal(policy.decide('speak', context, 2).kind, 'wait', '仍在接收相关依据时优先留时间');
assert.equal(policy.decide('speak', context, 3), null, '重复动作进入冷却');
policy.feedback(false, 4);
assert.equal(policy.decide('speak', context, 20000), null, '拒绝建议后延长安静时间');
assert.equal(policy.decide('speak', {...context,receiving:[]}, 40000).kind, 'speak');

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
