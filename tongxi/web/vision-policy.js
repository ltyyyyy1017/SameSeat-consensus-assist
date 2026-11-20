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
