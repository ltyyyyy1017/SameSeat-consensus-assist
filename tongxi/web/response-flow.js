// 纯展示函数：权威回应状态由房间服务维护。
export const outcomes = {adopted: '已采纳', partial: '部分采纳', not_adopted: '未采纳', verifying: '进一步核实', answered: '已作说明'};
export const phases = {receiving: '相关内容尚未接收完', thinking: '保留准备时间', expressing: '准备补充', submitted: '意见已提交，等待答复', unknown: '连接或呈现状态未知'};
export const openOpinions = c => ['pending', 'verifying', 'disputed'].includes(c.status);

export function requestCard(q, s, esc, tag, kinds) {
  const own = q.member === s.me.id;
  const active = ['delivered', 'seen'].includes(q.status);
  const title = s.decisions.find(d => d.id === q.decision)?.title || '当前讨论';
  const clock = own && active && q.minimum_until && q.phase !== 'unknown' && q.phase !== 'submitted'
    ? `<p class="response-clock" data-window-until="${q.minimum_until}"></p>` : '';
  const selfActions = q.phase === 'submitted'
    ? '' : `<button class="small primary" data-request-id="${q.id}" data-request-action="${['receiving','unknown'].includes(q.phase) ? 'ready' : 'express'}">${['receiving','unknown'].includes(q.phase) ? '关键内容已收到，开始准备' : '我准备补充'}</button>${['thinking','expressing'].includes(q.phase) ? `<button class="small" data-request-id="${q.id}" data-request-action="extend">还需准备时间</button>` : ''}`;
  return `<article class="response-card" data-request-card="${q.id}">
    <div class="row between"><strong>${esc(title)}</strong>${tag(q.status)}</div>
    <p class="response-stage">${esc(active ? phases[q.phase] || kinds[q.kind] : q.closed_reason || '本次请求已结束')}</p>
    <p class="muted small-text">${esc(kinds[q.kind])} · ${q.source === 'auto' ? '按本人预授权留位' : '本人主动发起'}</p>
    ${clock}${q.phase === 'unknown' && active ? '<p class="small-text">暂不把未知状态记作收到或放弃；回应机会仍保留。</p>' : ''}
    ${active ? `<div class="actions">${own ? `${selfActions}<button class="small" data-request-id="${q.id}" data-request-action="skip">本次略过</button><button class="ghost small" data-request-id="${q.id}" data-request-action="withdraw">撤回</button>` : `<button class="small" data-request-id="${q.id}" data-request-action="seen">已看到</button>${q.phase !== 'submitted' ? `<button class="small" data-request-id="${q.id}" data-request-action="handled">记录已安排的回应</button>` : '<span class="small-text">请逐条答复关联意见</span>'}`}</div>` : ''}
    ${own && q.source === 'auto' ? `<div class="help-feedback"><span class="small-text muted">这次留位</span>${[['helpful','有帮助'],['unnecessary','没必要'],['uncomfortable','方式不合适']].map(([value, label]) => `<button class="ghost small ${q.feedback?.rating === value ? 'feedback-selected' : ''}" data-intervention="${q.id}" data-rating="${value}" aria-pressed="${q.feedback?.rating === value}">${label}</button>`).join('')}</div>` : ''}
  </article>`;
}

export function contributionCard(c, s, esc, tag) {
  const own = c.member === s.me.id;
  const editable = s.role === 'host' && openOpinions(c);
  const reply = c.resolution;
  const d = s.decisions.find(d=>d.id===c.decision);
  const segment = s.segments.find(segment=>segment.id===c.quote?.segment);
  const quote = segment?.rev === c.quote?.rev ? segment?.text : segment?.revisions.find(r=>r.rev===c.quote?.rev)?.text;
  return `<article class="contribution opinion-card" data-opinion="${c.id}">
    <div class="row between"><strong>${esc(s.decisions.find(d => d.id === c.decision)?.title || '独立意见')}</strong>${tag(c.status)}</div>
    <p class="opinion-text">${esc(c.text)}</p>
    ${c.quote ? `<div class="quote">${esc(quote || '该版本原句暂不可用')} <small>v${c.quote.rev}</small></div>` : ''}
    ${reply ? `<div class="opinion-answer"><div class="row between"><b>${esc(outcomes[reply.outcome] || '主持人答复')}</b><span class="small-text muted">意见记录 v${c.version}</span></div><p>${esc(reply.reason)}</p>${reply.impact ? `<p><span class="muted">具体影响：</span>${esc(reply.impact)}</p>` : ''}${reply.decision_version ? `<small class="muted">答复时议题版本 v${reply.decision_version}；采纳说明与修改决定分别记录。</small>` : ''}</div>` : '<p class="small-text muted">等待主持人逐条答复</p>'}
    ${c.status === 'disputed' ? `<div class="hint amber">成员要求继续回应：${esc(c.history.filter(h => h.kind === 'dispute').at(-1)?.reason)}</div>` : ''}
    <div class="actions">${editable ? `<button class="small primary" data-reply="${c.id}">逐条答复</button>${c.decision ? `<button class="small" data-resolve="${c.decision}" data-resolve-action="${['confirmed','deferred'].includes(d?.status)?'reopen':'maintain'}">${['confirmed','deferred'].includes(d?.status)?'重开关联议题':'查看并处理决定'}</button>` : ''}` : ''}
    ${own && c.status === 'handled' && !c.acknowledged ? `<button class="small" data-opinion-feedback="${c.id}" data-feedback-action="acknowledge">已收到答复</button>` : ''}
    ${own && ['handled','verifying'].includes(c.status) ? `<button class="small" data-opinion-feedback="${c.id}" data-feedback-action="dispute">这没有回答我的问题</button>` : ''}
    ${c.acknowledged ? '<span class="small-text muted">本人已确认收到答复</span>' : ''}</div>
    ${c.history.length ? `<details class="opinion-history"><summary>查看答复与反馈记录</summary>${c.history.map(h => `<p class="small-text"><b>${esc(h.kind === 'reply' ? outcomes[h.outcome] || '答复' : h.kind === 'dispute' ? '要求继续回应' : '确认收到')}</b> · ${esc(h.reason || '本人确认收到答复')}</p>`).join('')}</details>` : ''}
  </article>`;
}
