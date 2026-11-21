// 可解释规则版策略；尚未训练或验证介入增益模型。
export class InterventionPolicy {
  constructor() { this.last = -Infinity; this.cooldown = 15000; this.snoozeUntil = 0; }
  decide(label, context, now = performance.now()) {
    if (!label || label === 'listen' || !context.visible || context.busy || context.hasRequest ||
        !context.online || now < this.snoozeUntil || now - this.last < this.cooldown) return null;
    const closing = context.decisions.filter(d => ['preparing', 'waiting'].includes(d.status));
    const d = closing.find(d=>context.receiving.some(e=>d.evidence.some(r=>r.segment===e.segment && r.rev===e.rev))) || closing.at(-1);
    if (!d) return null;
    const receiving = context.receiving.some(e => d.evidence.some(r => r.segment === e.segment && r.rev === e.rev));
    this.last = now;
    return {kind: label === 'wait' || receiving ? 'wait' : 'speak', decision: d.id,
      reason: receiving ? '相关议题准备定案，你仍在播放或回看相关依据。' :
        '相关议题准备定案，连续动作接近你记录的个人示例。'};
  }
  feedback(helpful, now = performance.now()) {
    this.cooldown = helpful ? 15000 : Math.min(120000, this.cooldown * 2);
    this.snoozeUntil = now + this.cooldown;
  }
  reset() { this.last = -Infinity; this.cooldown = 15000; this.snoozeUntil = 0; }
}
