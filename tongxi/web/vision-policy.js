// 个人显式标注的动作示例匹配。结果只用于本人端的可忽略建议。
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
  predict(frames) {
    const descriptor = this.describe(frames);
    return descriptor ? super.predict(descriptor) : null;
  }
}
