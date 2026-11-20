import assert from 'node:assert/strict';
import {PersonalActionModel} from '../tongxi/web/vision-policy.js';
import {PhraseMicrophone} from '../tongxi/web/microphone.js';

const model = new PersonalActionModel();
assert.equal(model.predict([0, 0, 0, 0]), null);
const examples = {listen: [0, 0, 0, 0], speak: [2, 1, .5, .2], wait: [-1, -.8, 1, -.5]};
for (const [label, sample] of Object.entries(examples)) model.train(label, Array.from({length: 8}, () => sample));
for (const [label, sample] of Object.entries(examples)) assert.equal(model.predict(sample), label);
assert.equal(model.predict([10, 10, 10, 10]), null);
assert.equal(model.predict(examples.speak.map((v, i) => v/2)), null);

const phrases = [];
const mic = new PhraseMicrophone(blob => phrases.push(blob), () => {});
mic.stream = {}; mic.context = {sampleRate: 16000};
for (let i=0; i<30; i++) mic.accept(new Float32Array(2048));
assert.equal(phrases.length, 0);
for (let i=0; i<6; i++) mic.accept(new Float32Array(2048).fill(.03));
for (let i=0; i<7; i++) mic.accept(new Float32Array(2048));
assert.equal(phrases.length, 1);
const data = new Uint8Array(await phrases[0].arrayBuffer());
assert.equal(new TextDecoder().decode(data.slice(0, 4)), 'RIFF');
console.log('Personal action abstention, voice endpointing and WAV encoding passed.');
