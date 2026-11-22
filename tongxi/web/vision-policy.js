// 个人显式标注的动作示例匹配。结果只用于本人端的可忽略建议。
export class FeatureWindow {
  constructor(minimum = 6, limit = 8) {
    this.minimum = minimum;
    this.limit = limit;
    this.frames = [];
  }
  push(frame) {
    this.frames.push(frame.slice());
    if (this.frames.length > this.limit) this.frames.shift();
  }
  clear() { this.frames = []; }
  ready() { return this.frames.length >= this.minimum; }
  ordered() { return this.frames.map(frame => frame.slice()); }
}

export class PersonalActionModel {
  constructor() { this.samples = {}; }
  train(label, frames) {
    if (frames.length < 6) throw new Error('有效画面不足，请让面部与双肩完整入框后重新记录');
    this.samples[label] = frames.reduce((sum, frame) => sum.map((v, i) => v + frame[i]), frames[0].map(() => 0)).map(v => v / frames.length);
  }
  predict(frame) {
    if (!['listen', 'speak', 'wait'].every(label => this.samples[label])) return null;
    const ranked = Object.entries(this.samples).map(([label, values]) => ({label,
      distance: Math.sqrt(values.reduce((sum, v, i) => sum + (v - frame[i]) ** 2, 0) / values.length)
    })).sort((a, b) => a.distance - b.distance);
    if (ranked[0].distance > 1 || ranked[1].distance - ranked[0].distance < .25) return null;
    return ranked[0].label;
  }
}

export class TemporalActionModel extends PersonalActionModel {
  describe(frames) {
    if (frames.length < 6 || frames.some(f => f.length !== 4 || f.some(v => !Number.isFinite(v)))) return null;
    const window = frames.slice(-8);
    const mean = window[0].map((_, i) => window.reduce((sum, f) => sum+f[i], 0)/window.length);
    const span = mean.map((_, i) => Math.max(...window.map(f=>f[i]))-Math.min(...window.map(f=>f[i])));
    if (span.some(v => v > 4)) return null; // 大幅机位变化时弃判。
    const movement = mean.map((_, i) => window.at(-1)[i]-window[0][i]);
    return [...mean, ...movement];
  }
  train(label, frames) {
    const descriptor = this.describe(frames);
    if (!descriptor) throw new Error('连续有效画面不足或机位变化过大，请重新记录');
    super.train(label, Array.from({length: 6}, () => descriptor));
  }
  rank(frames) {
    const descriptor = this.describe(frames);
    if (!descriptor || !['listen', 'speak', 'wait'].every(label => this.samples[label])) return null;
    return Object.entries(this.samples).map(([label, values]) => ({label,
      distance: Math.sqrt(values.reduce((sum, v, i) => sum + (v - descriptor[i]) ** 2, 0) / values.length)
    })).sort((a, b) => a.distance - b.distance);
  }
  predict(frames) {
    const ranked = this.rank(frames);
    if (!ranked || ranked[0].distance > 1 || ranked[1].distance - ranked[0].distance < .25) return null;
    return ranked[0].label;
  }
}

export const SENSE_INTERVALS = {L1: 1000, L2: 250, L3: 100};

export class SenseSchedule {
  constructor() {
    this.level = 'L1';
    this.decision = false;
    this.mouthHigh = 0;
    this.calmCaptions = 0;
    this.l3Start = 0;
    this.consistent = [];
    this.events = [];
  }
  interval() {
    return SENSE_INTERVALS[this.level] || 1000;
  }
  enter(level, now, reason) {
    if (this.level === level) return;
    this.events.push({at: now, from: this.level, to: level, reason});
    this.level = level;
    if (level === 'L3') {
      this.l3Start = now;
      this.consistent = [];
    }
  }
  setDecision(active, now) {
    this.decision = Boolean(active);
    if (this.decision && this.level === 'L1') this.enter('L2', now, 'decision');
  }
  noteCaption(stillDecision, now) {
    this.decision = Boolean(stillDecision);
    if (stillDecision) {
      this.calmCaptions = 0;
      if (this.level === 'L1') this.enter('L2', now, 'decision');
      return;
    }
    this.calmCaptions += 1;
    if (this.level === 'L2' && this.calmCaptions >= 2) this.enter('L1', now, 'two-captions');
  }
  finish(now, reason) {
    this.mouthHigh = 0;
    this.consistent = [];
    this.enter(this.decision ? 'L2' : 'L1', now, reason);
  }
  observe(frame) {
    const invalid = frame.faces !== 1 || !frame.shoulders;
    if (this.level === 'L3' && (frame.elapsed > 200 || invalid || frame.now - this.l3Start >= 2000)) {
      const reason = frame.elapsed > 200 ? 'slow' : invalid ? 'invalid' : 'timeout';
      this.finish(frame.now, reason);
      return {notice: null, unknown: true};
    }
    if (invalid) {
      this.mouthHigh = 0;
      return {notice: null, unknown: true};
    }
    if (this.level === 'L1') {
      this.mouthHigh = frame.mouth > 0.55 ? this.mouthHigh + 1 : 0;
      if (this.mouthHigh >= 2) this.enter('L3', frame.now, 'mouth');
    } else if (this.level === 'L2' && frame.candidates && frame.candidates.length) {
      const best = frame.candidates[0];
      if ((best.label === 'speak' || best.label === 'wait') && best.distance <= 1) this.enter('L3', frame.now, 'candidate');
    }
    if (this.level !== 'L3') return {notice: null, unknown: false};
    if (frame.predict === 'speak' || frame.predict === 'wait') {
      this.consistent.push({at: frame.now, label: frame.predict});
      this.consistent = this.consistent.filter(item => frame.now - item.at <= 400 && item.label === frame.predict);
      if (this.consistent.length >= 4 && frame.ready) {
        const notice = frame.predict;
        this.finish(frame.now, 'confirmed');
        return {notice, unknown: false};
      }
    } else this.consistent = [];
    return {notice: null, unknown: false};
  }
}
