import {requestCard, contributionCard, openOpinions, outcomes} from '/assets/response-flow.js';
import {InterventionPolicy} from '/assets/intervention-policy.js';
const interventionPolicy = new InterventionPolicy();
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;'
} [c]));
function id() {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
}
const labels = {
  discussing: '讨论中',
  preparing: '准备定案',
  waiting: '待回应',
  confirmed: '已确认',
  deferred: '已推迟',
  review: '依据需复核',
  delivered: '已送达',
  seen: '主持人已看到',
  handled: '已处理',
  withdrawn: '已撤回',
  skipped: '已略过',
  expired: '已失效',
  ignored: '已忽略',
  invalidated: '依据已修订',
  pending: '等待答复',
  verifying: '核实中',
  disputed: '要求继续回应',
  overridden: '主持人说明后继续'
};
const kinds = {
  wait: '请等我读／听完',
  clarify: '这句话需要澄清',
  speak: '我想接着说'
};
const actions = {
  maintain: '维持决定',
  modify: '修改决定',
  defer: '推迟处理',
  reopen: '重新讨论',
  reply: '主持人回应'
};
const icon = name => `<svg viewBox="0 0 24 24" aria-hidden="true">${({home:'<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',talk:'<path d="M21 11a8 8 0 0 1-8 8H7l-4 3V11a9 9 0 0 1 18 0Z"/><path d="M7 10h10M7 14h7"/>',decision:'<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 3h6v4H9zM9 12l2 2 4-4M9 18h6"/>',reply:'<path d="M9 10 4 15l5 5M4 15h10a6 6 0 0 0 0-12h-2"/>',history:'<path d="M4 8a9 9 0 1 1-1 7M4 3v5h5M12 7v6l4 2"/>',settings:'<circle cx="12" cy="12" r="3"/><path d="m10 3-1 3-3 1-3 3 2 3-1 3 3 3 3-1 3 2 3-2 3-3-1-3 2-3-3-3-3 1-3-2z"/>'})[name] || ''}</svg>`;
const brand = () => '<a class="brand" href="/"><span class="brand-mark" aria-hidden="true"><i></i><i></i><i></i><i></i></span><span><strong>同席</strong><small>一起讨论，共同决定</small></span></a>';
const state = {
  session: null,
  snapshot: null,
  health: null,
  view: 'overview',
  online: false,
  epoch: '',
  seq: 0,
  review: null,
  reviewed: new Map(),
  selected: new Set(),
  recording: false,
  recordingStarting: false,
  recordingStopping: false,
  captionStatus: '连续收音，停顿后自动生成带标点的字幕。',
  candidate: [],
  autoAnalyze: true,
  analysisBusy: false,
  analysisError: '',
  visionHint: null,
  vision: null,
  expressionKey: '',
  pendingConsent: null
};
let socket, reconnectTimer, toastTimer, microphone, camera;
const modal = $('#modal');

function toast(text) {
  const el = $('#toast');
  el.textContent = text;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 4500);
}

function tag(status) {
  return `<span class="tag ${status==='waiting'||status==='review'?'amber':status==='confirmed'?'':'neutral'}">${esc(labels[status] || status)}</span>`;
}

function empty(title, text) {
  return `<div class="empty"><div class="empty-symbol" aria-hidden="true">◌</div><strong>${esc(title)}</strong><span class="small-text">${esc(text)}</span></div>`;
}

function panel(title, body, right = '') {
  return `<section class="panel"><div class="panel-head"><h2>${title}</h2>${right}</div>${body}</section>`;
}

function decisionById(value) {
  return state.snapshot?.decisions.find(d => d.id === value);
}

function segmentById(value) {
  return state.snapshot?.segments?.find(s => s.id === value);
}

function showDialog(title, html, onSubmit) {
  modal.innerHTML = `<div class="modal-head"><h2 id="modal-title">${esc(title)}</h2><button class="ghost small" type="button" data-close aria-label="关闭弹窗">关闭</button></div><form id="modal-form" class="modal-body">${html}<div class="modal-error" id="modal-error" role="alert"></div></form>`;
  $('[data-close]', modal).onclick = () => modal.close();
  $('#modal-form').onsubmit = async event => {
    event.preventDefault();
    const submit = $('button[type=submit]', modal);
    if (submit) submit.disabled = true;
    try {
      await onSubmit?.(new FormData(event.target));
    } catch (error) {
      $('#modal-error').textContent = error.message;
    } finally {
      if (submit) submit.disabled = false;
    }
  };
  if (!modal.open) modal.showModal();
}
const modalButtons = (title = '保存') => `<div class="modal-actions"><button type="button" data-action="close-modal">取消</button><button type="submit" class="primary">${title}</button></div>`;
async function api(path, body, method = body === undefined ? 'GET' : 'POST', raw = false) {
  const headers = {
    'Idempotency-Key': id()
  };
  if (state.session) headers.Authorization = `Bearer ${state.session.token}`;
  if (!raw) headers['Content-Type'] = 'application/json';
  const response = await fetch(`/api/v1${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : raw ? body : JSON.stringify(body)
  });
  let result;
  try {
    result = await response.json();
  } catch {
    throw new Error('服务响应异常，请检查连接');
  }
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '输入内容有误，请检查字段');
  return result;
}
const route = tail => `/rooms/${state.session.room}${tail}`;
async function change(path, body, method) {
  const result = await api(route(path), body, method);
  await reload();
  return result;
}
async function reload() {
  if (state.session) {
    state.snapshot = await api(route('/snapshot'));
    state.clockOffset = state.snapshot.server_time * 1000 - Date.now();
    applyServerAnalysis();
    syncSense();
    render();
  }
}

function saveSession(session) {
  state.session = session;
  sessionStorage.setItem(`tongxi:${session.room}`, JSON.stringify(session));
  history.replaceState(null, '', `/room/${session.room}`);
  connect();
}

function remember() {
  if (!state.snapshot || state.snapshot.role === 'board') return;
  const rows = JSON.parse(localStorage.getItem('tongxi:recent') || '[]').filter(r => r.id !== state.snapshot.id);
  rows.unshift({
    id: state.snapshot.id,
    title: state.snapshot.title,
    role: state.snapshot.role
  });
  localStorage.setItem('tongxi:recent', JSON.stringify(rows.slice(0, 5)));
}

function connect() {
  clearTimeout(reconnectTimer);
  if (socket) {
    socket.onclose = null;
    socket.close();
  }
  socket = new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/api/v1/rooms/${state.session.room}/events`);
  socket.onopen = () => socket.send(JSON.stringify({
    token: state.session.token
  }));
  socket.onmessage = async event => {
    const message = JSON.parse(event.data);
    if (message.type === 'connected') {
      state.epoch = message.epoch;
      state.seq = 0;
      state.online = true;
      if (state.pendingConsent) {
        try {
          const snap = await api(route('/snapshot'));
          await api(route('/me/consent'), {
            ...state.pendingConsent,
            version: snap.me.consent.version
          }, 'PUT');
          state.pendingConsent = null;
        } catch (e) {
          toast(e.message);
        }
      }
      reportProgress();
    }
    if (message.type === 'snapshot') {
      const before = state.snapshot;
      state.snapshot = message.data;
      const opened = q => q.member === state.snapshot.me.id && (q.source === 'auto' || q.source === 'vision') && ['delivered', 'seen', 'confirmed'].includes(q.status);
      if (before && state.snapshot.requests?.some(q => opened(q) && !before.requests?.some(b => b.id === q.id && opened(b)))) toast('已为当前议题保留一次回应机会，可以继续表达、略过或撤回');
      remember();
      applyServerAnalysis();
      syncSense();
      render();
      speech.sync();
      if (state.snapshot.status === 'closed') {
        speech.stop();
        stopRecording();
        camera?.stop();
      }
    }
  };
  socket.onclose = event => {
    state.online = false;
    state.epoch = '';
    render();
    if (state.snapshot?.status !== 'closed' && event.code !== 1008) reconnectTimer = setTimeout(connect, 2000);
    else if (event.code === 1008) toast('会话失效，请从邀请重新加入');
  };
  socket.onerror = () => {};
}
setInterval(() => {
  if (socket?.readyState === 1) {
    socket.send(JSON.stringify({
      type: 'heartbeat'
    }));
    reportProgress();
  }
}, 2000);

function home() {
  const invite = new URLSearchParams(location.hash.slice(1)).get('invite');
  const recent = JSON.parse(localStorage.getItem('tongxi:recent') || '[]');
  $('#app').innerHTML = `<main id="main" class="home">
    <nav class="home-nav">${brand()}<div class="home-nav-actions"><span class="local-label">为每一种参与方式留出空间</span><button data-action="join">加入讨论</button></div></nav>
    <section class="hero">
      <div class="hero-copy"><div class="hero-kicker">面向听障人士与多元沟通需求的会议参与辅助</div><h1>让不容易被察觉的<br>表达意愿被看见。</h1><p>在高校线上小组讨论中，把非语言行为转化为表达信号。先不点名提醒主持人，由主持人决定是否为当前议题保留回应机会。</p><div class="hero-actions"><button class="primary" data-action="create">创建讨论房间</button><button class="home-demo" data-action="demo">体验示例讨论</button></div><div class="home-privacy">本地运行 / 参与方式可自行选择</div></div>
      <div class="stage" aria-label="功能示意，非实时数据">
        <div class="preview-window"><div class="preview-top"><div class="window-dots"><i></i><i></i><i></i></div><span>小组讨论 / 界面示意</span><span class="preview-live">讨论中</span></div><div class="preview-main"><div class="preview-conversation"><div class="preview-heading"><span>实时字幕</span><span>字幕已开启</span></div><div class="preview-line"><div><small>讨论原句</small><p>方案 B 需要实验室设备，<br>周六可以使用实验室。</p></div></div><div class="preview-line second"><div><small>讨论原句</small><p>那就先选 B，<br>接下来讨论人员分工。</p></div></div><div class="preview-caption">正在为这项讨论保留回应机会<span class="caption-dots">•••</span></div></div><aside class="preview-aside"><div class="preview-heading"><span>当前议题</span><span>···</span></div><div class="preview-topic"><span class="preview-wait">● &nbsp;待回应</span><h3>校园活动<br>方案 B</h3><p>在确认之前，<br>等待相关回应。</p><div class="preview-topic-foot">依据原句已关联</div></div><div class="preview-note"><strong>回应机会</strong><p>可以继续讨论其他事项</p></div></aside></div><div class="preview-toolbar"><span>字幕</span><span>议题</span><span class="preview-tool-active">回应</span><span>参与方式</span></div></div>
      </div>
    </section>
    <section class="home-grid"><article><div><h3>跟上讨论节奏</h3><p>字幕与语音播报，按你的方式获取信息。</p></div></article><article><div><h3>为议题留出回应</h3><p>在决定确认前，给重要补充一点时间。</p></div></article><article><div><h3>看见意见的结果</h3><p>找回原句，了解每一条意见如何处理。</p></div></article></section>
    ${recent.length?`<section class="recent"><h2>最近的讨论</h2><div class="recent-row">${recent.map(r=>`<a href="/room/${esc(r.id)}">${esc(r.title)} <span class="muted small-text">${r.role==='host'?'主持人':'成员'}</span></a>`).join('')}</div></section>`:''}
    <footer class="home-footer"><span>同席 · 让讨论更平等</span><span>本地协作，信息由参与者掌握</span></footer>
  </main>`;
  if (invite) joinDialog(invite);
}

function createDialog(demo = false) {
  showDialog(demo ? '体验示例讨论' : '创建一个讨论房间', `<div class="field"><label class="form-label" for="room-title">讨论主题</label><input id="room-title" name="title" value="${demo?'校园周末活动策划':''}" placeholder="例如：学生社区公共空间改造" required maxlength="80"></div><div class="field"><label class="form-label" for="host-name">怎么称呼你</label><input id="host-name" name="name" value="主持人" required maxlength="30"></div><div class="hint">成员通过邀请链接加入。公共屏只展示议题状态，个人授权按场次选择。${demo?'示例会载入三句人工编写的字幕和一个议题。':''}</div>${modalButtons('创建并进入')}`, async data => {
    const session = await api('/rooms', Object.fromEntries(data));
    state.session = session;
    if (demo) await api(route('/demo'), {});
    modal.close();
    saveSession(session);
  });
}

function joinDialog(invite = '') {
  showDialog('加入讨论', `<div class="field"><label class="form-label" for="invite">邀请链接或邀请码</label><input name="invite" id="invite" value="${esc(invite)}" required placeholder="粘贴主持人发来的邀请"></div><div class="field"><label class="form-label" for="join-name">怎么称呼你</label><input name="name" id="join-name" required maxlength="30" placeholder="你的名字或昵称"></div><div class="hint">加入后可选择字幕和播报。播报滞后、主动回看可以按授权自动留位。持续表达信号需要单独授权，只会私下提醒主持人，不会自动暂停会议。</div>${modalButtons('加入讨论')}`, async data => {
    let code = data.get('invite');
    try {
      code = new URLSearchParams(new URL(code).hash.slice(1)).get('invite') || code;
    } catch {}
    const session = await api('/join', {
      invite: code,
      name: data.get('name')
    });
    modal.close();
    saveSession(session);
  });
}

function nav() {
  const s = state.snapshot;
  const openRequest = s.role === 'host' ? ['delivered', 'seen'] : ['delivered', 'seen', 'confirmed'];
  const pending = s.requests?.filter(q => openRequest.includes(q.status)).length || 0;
  return [
    ['overview', 'home', '讨论概览'],
    ['live', 'talk', '实时讨论'],
    ['decisions', 'decision', '议题与决定'],
    ['responses', 'reply', s.role === 'host' ? '回应工作台' : '我的回应'],
    ['recap', 'history', '讨论复盘'],
    ['settings', 'settings', '个人与设备']
  ].map(([view, ico, text]) => `<button data-view="${view}" class="${state.view===view?'active':''}" ${state.view===view?'aria-current="page"':''}>${icon(ico)}${text}${view==='responses'&&pending?`<span class="count">${pending}</span>`:''}</button>`).join('');
}

function render() {
  if (!state.session) {
    home();
    return;
  }
  const s = state.snapshot;
  if (!s) {
    $('#app').innerHTML = '<main id="main" class="loading">正在连接讨论房间…</main>';
    return;
  }
  if (s.status === 'closed') {
    $('#app').innerHTML = `<main id="main" class="home">${brand()}<section class="hero"><div><h1>这场讨论已结束。</h1><p>临时字幕、进度和房间内容已清除。你可以创建下一场讨论。</p><a href="/" class="tag">返回首页</a></div></section></main>`;
    return;
  }
  if (s.role === 'board') {
    renderBoard();
    return;
  }
  const active = document.activeElement,
    focusId = active?.id,
    start = active?.selectionStart,
    end = active?.selectionEnd;
  const saved = {};
  $$('#app input[id],#app textarea[id],#app select[id]').forEach(el => saved[el.id] = {
    value: el.value,
    checked: el.checked
  });
  const transcript = $('#transcript'),
    scroll = transcript?.scrollTop,
    atBottom = transcript ? transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 35 : true;
  const view = {
    overview: overview,
    live: live,
    decisions: decisions,
    responses: responses,
    recap: recap,
    settings: settings
  } [state.view];
  $('#app').innerHTML = `<div class="layout"><aside class="sidebar">${brand()}<div class="nav-label">工作空间</div><nav class="nav" aria-label="主要导航">${nav()}</nav><div class="sidebar-bottom"><div class="identity"><span class="avatar">${esc(s.me.name[0])}</span><span><strong>${esc(s.me.name)}</strong>${s.role==='host'?'本场主持人':'讨论成员'}</span></div><a href="/">返回讨论首页</a></div></aside><div class="workspace"><header class="topbar"><div class="crumb">讨论空间 <span aria-hidden="true"> / </span><b>${esc(s.title)}</b></div><div class="top-actions"><span class="connection ${state.online?'':'offline'}">${state.online?'已连接':'连接中'}</span>${s.role==='host'?'<button class="small" data-action="invite">邀请与投屏</button>':'<button class="small" data-action="consent">我的授权</button>'}</div></header><main class="content" id="main">${view()}</main></div></div><div class="request-bar" aria-label="快捷请求"><span class="label">回应讨论</span><button data-request="wait">等我读／听完</button><button data-request="clarify">这句话需澄清</button><button data-request="speak">我想接着说</button></div>`;
  Object.entries(saved).forEach(([key, v]) => {
    const el = document.getElementById(key);
    if (el) {
      el.value = v.value;
      if (el.type === 'checkbox') el.checked = v.checked;
    }
  });
  if (focusId && document.getElementById(focusId)) {
    const el = document.getElementById(focusId);
    el.focus({
      preventScroll: true
    });
    try {
      el.setSelectionRange(start, end);
    } catch {}
  }
  const next = $('#transcript');
  if (next) next.scrollTop = state.review || !atBottom ? scroll || 0 : next.scrollHeight;
  if (camera?.stream && $('#camera-video')) {
    $('#camera-video').srcObject = camera.stream;
    $('#camera-video').play().catch(() => {});
  }
  updateResponseClocks();
  if (camera) window.tongxiVision = camera;
  if (state.e1 && window.tongxiE1) {
    window.tongxiE1.afterRender(state);
    if (camera) camera.onExperimentFrame = window.tongxiE1.recordFrame;
  }
}

function updateResponseClocks() {
  $$('[data-window-until]').forEach(el => {
    const seconds = Math.max(0, Math.ceil((Number(el.dataset.windowUntil) * 1000 - Date.now() - (state.clockOffset || 0)) / 1000));
    el.textContent = seconds ? `至少再保留 ${seconds} 秒准备时间；需要时可延长。` : '准备时间已满足，回应机会仍保留，等待你的选择。';
  });
}
setInterval(updateResponseClocks, 1000);

function responseProgressPanel() {
  const s = state.snapshot;
  const mine = s.requests.filter(q => q.member === s.me.id && ['delivered','seen'].includes(q.status));
  if (!mine.length) return '';
  return panel('我的回应机会', `<div class="panel-body">${mine.map(q => requestCard(q, s, esc, tag, kinds)).join('')}</div>`);
}

function head(title, sub, tools = '') {
  return `<div class="page-head"><div><div class="eyebrow">${esc(state.snapshot.title)}</div><h1>${title}</h1><p>${sub}</p></div>${tools?`<div class="actions">${tools}</div>`:''}</div>`;
}

function metrics() {
  const s = state.snapshot;
  return `<div class="stats">${[['本场议题',s.decisions.length,'项'],['待回应议题',s.decisions.filter(d=>d.status==='waiting').length,'项'],['已确认决定',s.decisions.filter(d=>d.status==='confirmed').length,'项'],[s.role==='host'?'已加入成员':'我的待处理请求',s.role==='host'?s.members.length:s.requests.filter(q=>['delivered','seen','confirmed'].includes(q.status)).length,s.role==='host'?'人':'条']].map(([label,n,unit])=>`<article class="stat"><span>${label}</span><strong>${n}<small>${unit}</small></strong></article>`).join('')}</div>`;
}

function overview() {
  const s = state.snapshot;
  return head('讨论概览', '查看当前议题、回应和参与状态。', s.role === 'host' ? '<button class="primary" data-action="new-decision">建立议题</button>' : '<button class="primary" data-view="live">进入实时讨论</button>') + metrics() + (s.role === 'host' ? suggestionPanel() : '') + `<div class="columns"><div>${s.demo?'<div class="hint amber" style="margin-bottom:20px">当前为示例讨论，字幕为人工编写。你仍可以真实邀请成员、回看原句并验证留位流程。</div>':''}${panel('正在讨论的议题',s.decisions.length?s.decisions.map(d=>decisionCard(d)).join(''):empty('还没有议题','先记录几句讨论内容，再选择原句建立议题。'))}${panel('最近的讨论原句',s.segments.length?`<div>${s.segments.slice(-3).map(seg=>`<div class="segment"><div class="seg-body"><div class="segment-meta">${seg.source==='asr'?'录音转写':'主持人记录'} · 第 ${s.segments.indexOf(seg)+1} 句 · v${seg.rev}</div><p>${esc(seg.text)}</p></div></div>`).join('')}</div>`:empty('从一句话开始','可以现场录音转写，或由主持人先输入原话。'),'<button class="ghost small" data-view="live">查看全部</button>')}</div><aside>${expressionPanel()}${panel('本场参与方式',`<div class="panel-body"><div class="status-row"><span>字幕呈现</span><span class="tag">已开启</span></div><div class="status-row"><span>播报滞后自动留位</span><span class="tag ${s.me.consent.playback?'':'neutral'}">${s.me.consent.playback?'已授权':'未开启'}</span></div><div class="status-row"><span>主动回看时留位</span><span class="tag ${s.me.consent.review?'':'neutral'}">${s.me.consent.review?'已授权':'未开启'}</span></div><div class="status-row"><span>摄像头分析</span><span class="tag ${s.me.consent.camera?'':'neutral'}">${s.me.consent.camera?'已授权':'未开启'}</span></div><div class="status-row"><span>表达信号私下提醒</span><span class="tag ${s.me.consent.intervene?'':'neutral'}">${s.me.consent.intervene?'已授权':'未开启'}</span></div><button class="wide" style="margin-top:17px" data-action="consent">管理我的授权</button></div>`)}${panel('一次回应如何被处理',`<div class="panel-body">${[['确认依据','主持人核对与决定相关的原句。'],['留住机会','主持人确认或本人请求后，议题进入待回应。'],['共同处理','提交补充，主持人说明最终结果。']].map(([t,p],i)=>`<div class="mini-step"><span class="step-dot">${i+1}</span><div><b>${t}</b><p>${p}</p></div></div>`).join('')}</div>`)}</aside></div>`;
}

function decisionCard(d) {
  const s = state.snapshot;
  return `<article class="decision"><div class="row between">${tag(d.status)}${s.active_decision===d.id?'<span class="tag">当前议题</span>':''}<span class="muted small-text">第 ${d.round} 轮 · v${d.version}</span></div><h3>${esc(d.title)}</h3><p>${esc(d.conclusion)}</p>${d.evidence.map(e=>`<div class="quote">${esc(segmentById(e.segment)?.text||'原句不可用')} <span class="muted small-text">v${e.rev}</span></div>`).join('')}${d.resolutions.length?`<div class="resolution-label">${esc(actions[d.resolutions.at(-1).action])} · ${esc(d.resolutions.at(-1).reason)}</div>`:''}<div class="actions">${s.role==='host'&&['discussing','deferred','review'].includes(d.status)?`<button class="primary small" data-prepare="${d.id}">${d.status==='review'?'核对新版依据':'准备定案'}</button>`:''}${s.role==='host'&&['preparing','waiting'].includes(d.status)&&s.active_decision!==d.id?`<button class="small" data-focus="${d.id}">设为当前议题</button>`:''}${s.role==='host'&&['preparing','waiting'].includes(d.status)?`<button class="primary small" data-resolve="${d.id}" data-resolve-action="maintain">处理并确认</button><button class="small" data-resolve="${d.id}" data-resolve-action="defer">推迟</button>`:''}${s.role==='host'&&d.status==='confirmed'?`<button class="small" data-resolve="${d.id}" data-resolve-action="reopen">重新讨论</button>`:''}<button class="ghost small" data-compose="${d.id}">补充这个议题</button></div></article>`;
}

function live() {
  const s = state.snapshot,
    host = s.role === 'host';
  return head('实时讨论', '保持原句可见，让补充能够回到它回应的地方。', host ? `<button class="${state.recording?'recording':''}" data-action="record">${state.recording?'停止录音':'开启本地转写'}</button>` : '') + `<div class="columns"><div>${state.review?`<div class="hint review-banner">正在回看选中的原句<button class="small" data-action="end-review">回到实时位置</button></div>`:''}${panel('讨论原句',`<div class="transcript" id="transcript">${s.segments.length?s.segments.map((seg,i)=>`<article class="segment ${state.review?.segment===seg.id?'selected':''}">${host?`<input type="checkbox" data-evidence="${seg.id}" aria-label="选择第 ${i+1} 句作为决定依据" ${state.selected.has(seg.id)?'checked':''}>`:''}<div class="seg-body"><div class="segment-meta"><span>第 ${i+1} 句</span><span>${seg.source==='asr'?'本地转写':'主持人记录'}</span><span>v${seg.rev}${seg.rev>1?' · 已修订':''}</span>${!seg.stable?'<span>临时字幕</span>':''}</div><p>${esc(seg.text)}</p><div class="seg-tools"><button class="ghost small" data-review="${seg.id}">回看这句</button><button class="ghost small" data-quote="${seg.id}">回应这句</button>${host?`<button class="ghost small" data-edit="${seg.id}">修订</button>`:''}</div></div></article>`).join(''):empty('等待讨论开始','原句会按顺序出现在这里。')}</div>${host?'<div class="panel-body"><form id="segment-form"><label class="form-label" for="segment-input">记录一句讨论原话</label><textarea id="segment-input" name="text" required placeholder="例如：方案 B 需要实验室设备，周六可以使用实验室。" maxlength="1500"></textarea><div class="row between" style="margin-top:12px"><span class="muted small-text">将明确标记为主持人记录</span><button class="primary" type="submit">加入字幕</button></div></form></div>':''}`,`<span class="muted">${s.segments.length} 句</span>`)}${host?`<div class="row"><button data-action="new-decision" class="primary">用选中原句建立议题</button><button data-action="analyze">本地 AI 提议议题</button></div>${suggestionPanel()}`:''}</div><aside>${expressionPanel()}${visionHintPanel()}${playerPanel()}${panel('回应与阅读',`<div class="panel-body"><div class="hint">${s.me.consent.review?'已授权回看留位。你主动选择回看相关依据、主持人准备定案时，系统会替该议题留位。':'点击“回看这句”保留阅读位置。你可以在个人授权里开启相关依据回看时自动留位。'}</div><button class="wide" style="margin-top:15px" data-action="compose">写一条补充意见</button></div>`)}${panel('当前议题',decisionById(s.active_decision)?decisionCard(decisionById(s.active_decision)):empty('还没有当前议题','主持人准备定案，或从仍未结项的议题里重新选择。'))}</aside></div>`;
}

function signalText(view) {
  if (!view) return '尚未判断';
  const current = state.snapshot?.decisions?.find(d => d.id === state.snapshot.active_decision);
  const open = Boolean(current && (current.status === 'preparing' || current.status === 'waiting'));
  if (view.kind === 'speak' && view.confident) return open ? '可能希望补充' : '可能准备发言';
  if (view.kind === 'wait' && view.confident) return '可能需要等待';
  if (view.kind === 'listen') return '接近自然聆听';
  return '状态不确定';
}
function meter(name, value) {
  return `<div class="meter"><span>${name}</span><i><b style="width:${Math.round((value || 0) * 100)}%"></b></i></div>`;
}
function signalBody(view) {
  if (!view) return '<p class="list-note">开启摄像头并校准自然坐姿后，这里显示只留在本机的表达信号。</p>';
  const bars = view.bars || {};
  return `<strong data-signal-label>${esc(signalText(view))}</strong><div class="signal-meters">${meter('嘴部', bars.mouth)}${meter('前倾', bars.lean)}${meter('俯仰', bars.pitch)}${meter('转向', bars.turn)}</div><p class="list-note">${esc(view.summary || '')}</p>`;
}
function paintSignal(view) {
  const box = document.getElementById('signal-readout');
  if (box) box.innerHTML = signalBody(view);
}
function hostNoticePanel() {
  const s = state.snapshot;
  if (!s || s.role !== 'host') return '';
  const notices = (s.notices || []).filter(n => n.status === 'pending');
  if (notices.length) return notices.map(n => panel('主持人提醒', `<div class="panel-body" data-host-notice="${esc(n.id)}"><p>当前议题可能有人还没来得及回应。</p><p class="list-note">只处理这项议题：${esc(decisionById(n.decision)?.title || '当前议题')}</p><div class="actions"><button class="primary small" data-notice="${esc(n.id)}" data-notice-action="confirm">确认</button><button class="small" data-notice="${esc(n.id)}" data-notice-action="ignore">忽略</button></div></div>`)).join('');
  const held = (s.requests || []).find(q => q.source === 'vision' && q.status === 'confirmed' && q.decision === s.active_decision);
  if (!held) return '';
  return panel('主持人提醒', `<div class="panel-body" data-host-held="${esc(held.id)}"><p>当前议题已保留回应机会。</p><p class="list-note">只处理这项议题：${esc(decisionById(held.decision)?.title || '当前议题')}</p></div>`);
}
function expressionPanel() {
  const s = state.snapshot;
  if (!s || s.role === 'board') return '';
  if (s.role === 'host') return hostNoticePanel();
  return panel('我的表达信号', `<div class="panel-body"><div id="signal-readout">${signalBody(state.vision)}</div><p class="list-note">原始画面不离开这台设备。状态不确定时不会提醒主持人。</p></div>`);
}
function applyVision(view) {
  const previous = signalText(state.vision);
  const was = Boolean(state.vision?.confident);
  state.vision = view;
  state.visionHint = view?.confident ? view.kind : view ? 'unknown' : null;
  if (previous !== signalText(view) || was !== Boolean(view?.confident)) render();
  else paintSignal(view);
  publishExpression(view);
}
function publishExpression(view) {
  if (!state.session || !state.snapshot || state.snapshot.role === 'board') return;
  const payload = {
    kind: view?.confident && (view.kind === 'speak' || view.kind === 'wait') ? view.kind : view ? 'unknown' : null,
    confident: Boolean(view?.confident && (view.kind === 'speak' || view.kind === 'wait')),
    summary: (view?.summary || '').slice(0, 80)
  };
  if (!state.snapshot.me?.consent?.camera && payload.kind) return;
  const key = JSON.stringify(payload);
  if (key === state.expressionKey) return;
  state.expressionKey = key;
  clearTimeout(state.expressionTimer);
  api(route('/me/expression'), payload).then(() => {
    if (payload.confident) state.expressionTimer = setTimeout(() => {
      state.expressionKey = '';
      publishExpression(state.vision);
    }, 2000);
  }).catch(error => {
    state.expressionKey = '';
    toast(error.message);
  });
}
function visionHintPanel() {
  if (!state.vision || state.vision.kind === 'listen') return '';
  const waiting = state.vision.kind === 'wait' || state.vision.kind === 'unknown' || Boolean(state.review) || Boolean(speech.current);
  const title = state.vision.confident ? '你可以现在回应' : '仅你可见';
  return panel(title, `<div class="panel-body"><p class="small-text">${state.vision.confident ? '持续信号只留在本机。若你授权了提醒，主持人只会看到“当前议题可能有人还没来得及回应”，不会看到你的姓名。' : '这次动作还不能确认，系统不会提醒主持人。'}</p><div class="actions"><button class="small primary" data-request="${waiting ? 'wait' : 'speak'}">${waiting ? '请等我读／听完' : '我想接着说'}</button><button class="small" data-action="dismiss-vision">这次不用</button></div></div>`);
}
function playerPanel() {
  return panel('本地语音播报', `<div class="panel-body"><div class="player"><div class="row between"><div class="wave ${speech.active?'playing':''}">${'<i></i>'.repeat(14)}</div><span class="tag neutral">${speech.active?'播放中':speech.enabled?'等待新字幕':'未开启'}</span></div><p>${esc(speech.message)}</p></div><div class="actions"><button class="${speech.enabled?'':'primary'}" data-action="speech">${speech.enabled?'关闭播报':'开启播报'}</button><button class="small" data-action="speech-replay">重播现有原句</button></div><label class="form-label" for="speech-rate" style="margin-top:18px">语速</label><select id="speech-rate"><option value="0.8">稍慢 · 0.8 倍</option><option value="1">正常 · 1 倍</option><option value="1.2">稍快 · 1.2 倍</option></select><p class="list-note">主机使用本地中文音色合成，成员端按句播放。只有真实排队和播放状态参与留位。</p></div>`);
}

function decisions() {
  return head('议题与决定', '原句、当前结论与每次人工处理，保留在同一个位置。', state.snapshot.role === 'host' ? '<button class="primary" data-action="new-decision">建立议题</button>' : '') + panel('本场全部议题', state.snapshot.decisions.length ? state.snapshot.decisions.map(d => decisionCard(d)).join('') : empty('还没有建立议题', '进入实时讨论，选中一条或多条依据后建立。'));
}

function responses() {
  const s = state.snapshot;
  return head(s.role === 'host' ? '回应工作台' : '我的回应', s.role === 'host' ? '把“已经看到”与“已经处理”分别记录。' : '查看自己的请求、已提交意见以及最终处理结果。', '<button class="primary" data-action="compose">写一条补充</button>') + `<div class="columns"><div>${panel('已经提交的补充意见',s.contributions.length?`<div class="panel-body">${s.contributions.map(c=>`<article class="contribution"><div class="row between"><b>${esc(decisionById(c.decision)?.title||'独立意见')}</b>${tag(c.status)}</div><p>${esc(c.text)}</p>${c.quote?`<div class="quote">${esc(segmentById(c.quote.segment)?.text||'')}</div>`:''}${c.resolution?`<div class="hint">${esc(actions[c.resolution.action])} · ${esc(c.resolution.reason)}</div>`:s.role==='host'&&c.decision?`<button class="small" data-resolve="${c.decision}" data-resolve-action="maintain">处理关联议题</button>`:s.role==='host'?`<button class="small" data-reply="${c.id}">回复这条意见</button>`:'<span class="muted small-text">等待主持人回应</span>'}</article>`).join('')}</div>`:empty('还没有补充意见','想法准备好后，选择对应原议题或作为独立意见提交。'))}</div><aside>${hostNoticePanel()}${panel(s.role==='host'?'待回应请求':'我的请求',s.requests.length?`<div class="panel-body">${s.requests.slice().reverse().map(q=>`<article class="notice-card"><div class="row between"><h3>${kinds[q.kind]}</h3>${tag(q.status)}</div><p>${esc(decisionById(q.decision)?.title||'当前讨论')}</p><p class="muted">${q.source==='vision'?(q.status==='confirmed'?'主持人确认后的表达机会':'表达信号提醒'):q.source==='auto'?'播报或回看自动留位':'本人主动发起'}${q.closed_reason?' · '+esc({skip:'本次略过',withdraw:'已撤回',seen:'主持人已看到',handled:'已处理'}[q.closed_reason]||q.closed_reason):''}</p>${s.role==='host'?(['delivered','seen'].includes(q.status)?`<div class="actions"><button class="small" data-request-id="${q.id}" data-request-action="seen">已看到</button><button class="primary small" data-request-id="${q.id}" data-request-action="handled">标记已处理</button></div>`:''):(['delivered','seen','confirmed'].includes(q.status)?`<div class="actions" data-member-request="${esc(q.id)}">${q.status==='confirmed'?`<button class="primary small" data-compose="${esc(q.decision||'')}">我来表达</button>`:''}<button class="small" data-request-id="${q.id}" data-request-action="skip">本次略过</button><button class="ghost small" data-request-id="${q.id}" data-request-action="withdraw">撤回</button></div>`:'')}</article>`).join('')}</div>`:empty('暂无请求','手动请求、播报回看留位，以及主持人确认后的表达机会，会在这里显示。'))}</aside></div>`;
}

function recap() {
  const s = state.snapshot;
  return head('讨论复盘', '对照当时依据，查看每个决定如何被补充与修订。', s.role === 'host' ? '<button class="primary" data-action="export">预览并导出</button>' : '') + `<div class="columns"><div>${panel('决定处理记录',s.decisions.some(d=>d.resolutions.length)?`<div class="panel-body"><div class="timeline">${s.decisions.flatMap(d=>d.resolutions.map(r=>({d,r}))).sort((a,b)=>a.r.at-b.r.at).map(({d,r})=>`<article class="timeline-item"><time>${new Date(r.at*1000).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'})}</time><strong>${esc(d.title)} · ${actions[r.action]}</strong><p>${esc(r.reason)}</p><div class="decision-diff"><div><small>原决定</small><p>${esc(r.before)}</p></div><div><small>处理后</small><p>${esc(r.after)}</p></div></div>${r.continued_without_response?'<div class="hint amber">主持人记录原因后继续；尚未答复的意见仍保留。</div>':''}</article>`).join('')
}</div></div>`:empty('尚无处理记录','主持人处理议题后，这里会显示理由和结果。'))}</div><aside>${panel('本场记录',`<div class="panel-body"><div class="status-row"><span>讨论原句</span><b>${s.segments.length} 句</b></div><div class="status-row"><span>已提交补充</span><b>${s.contributions.length} 条</b></div><div class="status-row"><span>已确认议题</span><b>${s.decisions.filter(d=>d.status==='confirmed').length} 项</b></div><p class="list-note">导出前可以预览完整内容。个人进度、授权详情、设备信息和原始媒体不进入导出文件。房间结束时清除临时内容。</p></div>`)}</aside></div>`;
}

function settings() {
  const s = state.snapshot,
    h = state.health?.capabilities;
  return head('个人与设备', '选择适合自己的参与方式，随时调整。') + `<div class="columns"><div>${panel('播报回看与表达提醒',`<div class="panel-body"><form id="consent-form">${consentFields()}<button class="primary" type="submit">保存本场授权</button></form></div>`)}${panel('本机表达信号',`<div class="panel-body"><p class="list-note">摄像头画面只在本机处理。先校准自然坐姿，再记录自然聆听、准备发言和希望等待。持续信号只显示在这里；另外授权后，也只向主持人发送不点名提醒，不会直接改变议题。</p><div id="signal-readout">${signalBody(state.vision)}</div><video id="camera-video" class="camera-video" autoplay muted playsinline ${camera?.stream?'':'hidden'}></video><p id="camera-status" class="hint">${esc(camera?.message||'尚未开启，模型资源位于本地。')}</p><div class="actions"><button data-action="camera">${camera?.stream?'关闭摄像头':'授权并开启摄像头'}</button><button data-action="calibrate" ${camera?.stream?'':'disabled'}>校准自然坐姿</button></div><div class="actions"><button class="small" data-vision-sample="listen" ${camera?.stream?'':'disabled'}>记录自然聆听</button><button class="small" data-vision-sample="speak" ${camera?.stream?'':'disabled'}>记录准备发言</button><button class="small" data-vision-sample="wait" ${camera?.stream?'':'disabled'}>记录希望等待</button></div></div>`)}${s.role==='host'?panel('结束本场讨论',`<div class="panel-body"><p class="list-note">结束前请先导出需要保留的决定记录。结束后清空本场原句、意见与临时进度，邀请随即失效。</p><button class="danger" data-action="end">结束并清空房间</button></div>`):''}</div><aside>${playerPanel()}${panel('房间主机能力',`<div class="panel-body"><div class="status-row"><span>中文转写<small>FunASR Runtime 2-pass</small></span><span class="tag ${h?.asr.ready?'':'neutral'}">${h?.asr.ready?'已配置':'待配置'}</span></div><div class="status-row"><span>语义议题提取<small>${esc(h?.semantic.label||'本地语义模型')}</small></span><span class="tag ${h?.semantic.ready?'':'neutral'}">${h?.semantic.ready?'已配置':'待配置'}</span></div><div class="status-row"><span>语义关联与依据解释</span><span class="tag">可用</span></div><p class="list-note">已配置表示主机设置了模型入口，首次运行仍需验证模型能否加载。手动建项、请求和处理流程可独立使用。</p></div>`)}</aside></div>`;
}

function renderBoard() {
  const s = state.snapshot;
  $('#app').innerHTML = `<main class="board" id="main"><header>${brand()}<span class="connection ${state.online?'':'offline'}">${state.online?'实时同步':'重新连接中'}</span></header><div class="eyebrow">共同讨论 · 共同决定</div><h1>${esc(s.title)}</h1>${s.signals?`<div class="hint amber">有成员出现持续表达信号。这里不显示身份，只提示当前议题是否需要留出回应。</div>`:''}<div class="board-grid">${s.decisions.length?s.decisions.map(d=>`<article class="board-card ${d.status}">${tag(d.status)}<h2>${esc(d.title)}</h2><p>${esc(d.conclusion)}</p>${d.status==='waiting'?'<div class="hint amber">这项议题留有回应机会，大家可以先讨论其他事项。</div>':d.resolutions.length?`<div class="hint">${esc(d.resolutions.at(-1).reason)}</div>`:'<span class="muted">等待主持人确认</span>'}</article>`).join(''):empty('讨论即将开始','这里将显示共同议题及其处理结果。')}</div><footer>公共屏仅展示议题信息。个人阅读、播报进度与草稿仅用于本人授权的流程。</footer></main>`;
}

function consentFields() {
  const c = state.snapshot.me.consent;
  return `<label class="check-row"><input type="checkbox" name="playback" ${c.playback?'checked':''}><span>相关依据尚在播报时，替我留位<small>仅在主持人准备定案且设备状态有效时触发。</small></span></label><label class="check-row"><input type="checkbox" name="review" ${c.review?'checked':''}><span>我主动回看相关原句时，替我留位<small>只使用明确选择的回看位置，随时可以返回实时位置。</small></span></label><label class="check-row"><input type="checkbox" name="camera" ${c.camera?'checked':''}><span>允许在本机分析摄像头<small>只上传表达信号，不上传画面。关闭后立即停止分析。</small></span></label><label class="check-row"><input type="checkbox" name="intervene" ${c.intervene?'checked':''}><span>持续表达信号达到条件时，私下提醒主持人<small>与摄像头授权分开。不显示你的姓名。不确定、未授权或议题尚未准备定案时不会提醒。</small></span></label><div class="hint" style="margin:10px 0 20px">主持人确认后，公共屏只显示这项议题是否留有回应机会，不显示你的身份和画面。你仍然可以不发言、略过、撤回或提交自己的补充意见。</div>`;
}
async function saveConsent(data) {
  const next = {
    playback: data.has('playback'),
    review: data.has('review'),
    camera: data.has('camera'),
    intervene: data.has('intervene')
  };
  if (!state.online) {
    state.pendingConsent = next;
    toast('本机已暂停上报，重连后优先同步授权设置');
    return;
  }
  await change('/me/consent', {
    version: state.snapshot.me.consent.version,
    ...next
  }, 'PUT');
  if (!next.camera && camera) {
    camera.stop();
    render();
  }
  toast('本场授权已更新');
  reportProgress();
}

function consentDialog() {
  showDialog('我的本场授权', consentFields() + modalButtons('保存授权'), async data => {
    await saveConsent(data);
    modal.close();
  });
}

function inviteDialog() {
  const s = state.snapshot;
  const invite = `${location.origin}/#invite=${s.invite}`,
    board = `${location.origin}/room/${s.id}#token=${s.board_token}&role=board`;
  showDialog('邀请成员与打开公共屏', `<div class="field"><label class="form-label" for="share-base">供其他设备访问的地址</label><input id="share-base" value="${esc(location.origin)}" type="url"><small>同机体验保留此地址。局域网模式下改为主机实际 IP 和端口，再复制下方链接。</small></div><div class="field"><label class="form-label" for="invite-link">成员邀请</label><div class="link-box"><input readonly id="invite-link" value="${esc(invite)}"><button type="button" data-copy="invite-link">复制</button></div></div><div class="field"><label class="form-label" for="board-link">公共投屏</label><div class="link-box"><input readonly id="board-link" value="${esc(board)}"><button type="button" data-copy="board-link">复制</button></div><div class="actions"><a id="board-open" class="tag" href="${esc(board)}" target="_blank" rel="noopener">打开公共屏</a><a id="invite-open" class="tag neutral" href="${esc(invite)}" target="_blank" rel="noopener">打开成员入会页</a></div></div><div class="hint">两种链接对应不同权限。公共屏凭证只允许查看公开议题，房间结束后失效。</div>`, null);
}

function newDecision(prefill) {
  const s = state.snapshot;
  const selected = prefill?.evidence?.map(e => e.segment) || [...state.selected];
  showDialog('建立一个议题', `<div class="field"><label class="form-label" for="decision-title">议题名称</label><input id="decision-title" name="title" required maxlength="80" value="${esc(prefill?.title||'')}" placeholder="例如：周末采用方案 B"></div><div class="field"><label class="form-label" for="decision-conclusion">当前候选结论</label><textarea id="decision-conclusion" name="conclusion" required maxlength="300">${esc(prefill?.conclusion||'')}</textarea></div><div class="field"><span class="form-label">选择与这项决定直接相关的依据</span><div class="modal-scroll">${s.segments.filter(seg=>seg.stable).map(seg=>`<label class="check-row"><input type="checkbox" name="evidence" value="${seg.id}" ${selected.includes(seg.id)?'checked':''}><span>${esc(seg.text)}<small>v${seg.rev}</small></span></label>`).join('')||'<p class="muted">请先在实时讨论中记录原句。</p>'}</div></div>${modalButtons('建立议题')}`, async data => {
    const refs = data.getAll('evidence').map(value => ({
      segment: value,
      rev: segmentById(value).rev
    }));
    if (!refs.length) throw new Error('请至少选择一条稳定原句作为依据');
    await change('/decisions', {
      title: data.get('title'),
      conclusion: data.get('conclusion'),
      evidence: refs
    });
    state.selected.clear();
    modal.close();
    state.view = 'decisions';
    render();
  });
}

function resolveDialog(decisionId, action = 'maintain') {
  const d = decisionById(decisionId);
  showDialog('处理议题与回应', `<div class="hint">${esc(d.title)}<br>${esc(d.conclusion)}</div><p class="list-note">确认决定不会自动答复意见。仍待答复：${state.snapshot.contributions.filter(c=>c.decision===d.id&&openOpinions(c)).length} 条。</p><div class="field" style="margin-top:20px"><label class="form-label" for="resolve-action">如何处理</label><select id="resolve-action" name="action">${(action==='reopen'?['reopen']:['maintain','modify','defer']).map(a=>`<option value="${a}" ${action===a?'selected':''}>${actions[a]}</option>`).join('')}</select></div><div class="field"><label class="form-label" for="new-conclusion">修改后的结论（选择修改时填写）</label><input id="new-conclusion" name="conclusion" value="${esc(d.conclusion)}" maxlength="300"></div><div class="field"><label class="form-label" for="resolve-reason">处理理由／回应说明</label><textarea id="resolve-reason" name="reason" required maxlength="1000" placeholder="例如：采纳设备时间建议，活动改到周日。"></textarea></div><label class="check-row"><input type="checkbox" name="close_pending"><span>若仍有未回应事项，记录上述理由后继续确认<small>请求将标记为“说明后继续”，未答复意见继续保留；不表示成员同意。</small></span></label>${modalButtons('记录处理结果')}`, async data => {
    await change(`/decisions/${d.id}/resolve`, {
      version: d.version,
      action: data.get('action'),
      conclusion: data.get('conclusion'),
      reason: data.get('reason'),
      close_pending: data.has('close_pending')
    });
    modal.close();
    toast('处理结果已同步给参与者');
  });
}

function replyDialog(cid) {
  const c = state.snapshot.contributions.find(c=>c.id===cid);
  showDialog('逐条答复意见', `<div class="quote">${esc(c.text)}</div><div class="field"><label class="form-label" for="reply-outcome">这条意见如何处理</label><select id="reply-outcome" name="outcome">${Object.entries(outcomes).map(([key,label])=>`<option value="${key}">${label}</option>`).join('')}</select></div><div class="field"><label class="form-label" for="reply-text">针对这条意见的答复</label><textarea id="reply-text" name="reason" required maxlength="1000"></textarea></div><div class="field"><label class="form-label" for="reply-impact">具体影响或采纳范围（采纳时必填）</label><textarea id="reply-impact" name="impact" maxlength="1000" placeholder="例如：活动日期拟改为周日；请另在议题中记录最终修改。"></textarea></div><p class="list-note">此操作只答复这条意见。需要修改共同决定时，请另行处理议题。</p>${modalButtons('发送这条答复')}`, async data=>{
    await change(`/contributions/${cid}/reply`, {version:c.version, outcome:data.get('outcome'), reason:data.get('reason'), impact:data.get('impact')});
    modal.close();
    toast('这条意见的答复已送达');
  });
}

async function opinionFeedbackDialog(cid, action) {
  const c = state.snapshot.contributions.find(c=>c.id===cid);
  if (action === 'acknowledge') {
    await change(`/contributions/${cid}/feedback`, {version:c.version, action});
    return;
  }
  showDialog('要求继续回应', `<div class="quote">${esc(c.text)}</div><label class="form-label" for="dispute-reason">哪些问题还没有得到回答</label><textarea id="dispute-reason" name="reason" required maxlength="1000"></textarea>${modalButtons('发回主持人继续回应')}`, async data=>{
    await change(`/contributions/${cid}/feedback`, {version:c.version, action, reason:data.get('reason')});
    modal.close();
    toast('已请求继续回应；原答复仍保留在记录中');
  });
}

function composeDialog(decisionId = '', quoteId = '') {
  const s = state.snapshot;
  const quote = segmentById(quoteId);
  showDialog('把你的补充送回原议题', `<div class="field"><label class="form-label" for="compose-text">你想补充什么</label><textarea id="compose-text" name="text" required maxlength="2000" placeholder="写下你的补充、疑问或建议。草稿在点击查找或提交前留在这台设备。"></textarea></div>${quote?`<div class="quote">${esc(quote.text)}</div>`:''}<div class="field"><label class="form-label" for="compose-decision">它回应哪个议题</label><select id="compose-decision" name="decision"><option value="">作为独立意见提交</option>${s.decisions.map(d=>`<option value="${d.id}" ${d.id===decisionId?'selected':''}>${esc(d.title)}</option>`).join('')}</select></div><button type="button" data-action="match" class="small">根据内容查找原议题</button><div id="match-results" class="small-text"></div>${modalButtons('提交自己的意见')}`, async data => {
    await change('/contributions', {
      text: data.get('text'),
      decision: data.get('decision') || null,
      quote: quote ? {
        segment: quote.id,
        rev: quote.rev
      } : null
    });
    modal.close();
    state.view = 'responses';
    render();
    toast('补充意见已提交');
  });
}
async function manualDialog(kind) {
  const s = state.snapshot;
  showDialog(kinds[kind], `<div class="field"><label class="form-label" for="request-decision">针对哪个议题</label><select id="request-decision" name="decision"><option value="">当前讨论（暂不关联）</option>${s.decisions.filter(d=>d.status!=='confirmed').map(d=>`<option value="${d.id}">${esc(d.title)}</option>`).join('')}</select></div>${kind==='clarify'?`<div class="field"><label class="form-label" for="request-segment">需要澄清的原句</label><select id="request-segment" name="segment"><option value="">暂不指定</option>${s.segments.map(seg=>`<option value="${seg.id}">${esc(seg.text)}</option>`).join('')}</select></div>`:''}<div class="hint">主持人收到后会看到这条请求。你可以在“我的回应”中查看状态、略过或撤回。</div>${modalButtons('发送请求')}`, async data => {
    await change('/requests', {
      kind,
      decision: data.get('decision') || null,
      segment: data.get('segment') || null
    });
    modal.close();
    toast('请求已送达');
  });
}

const speech = {
  enabled: false,
  active: false,
  queue: [],
  done: new Set(),
  completed: new Map(),
  current: null,
  instance: '',
  message: '开启后，由房间主机离线生成中文语音。',
  rate: 1,
  audio: null,
  url: null,
  async start() {
    if (!state.health?.capabilities.tts?.ready) throw new Error('主机暂不支持本地中文播报，请检查 Windows 中文语音包');
    this.enabled = true;
    this.message = '等待新字幕';
    this.sync();
    render();
  },
  sync() {
    if (!this.enabled || !state.snapshot?.segments) return;
    for (const s of state.snapshot.segments) {
      const key = `${s.id}:${s.rev}`;
      if (s.stable && !this.done.has(key) && !this.queue.some(q => q.key === key) && this.current?.key !== key) this.queue.push({
        ...s,
        key
      });
    }
    if (this.current && segmentById(this.current.id)?.rev !== this.current.rev) {
      this.cancelAudio();
      this.current = null;
      this.active = false;
    }
    this.queue = this.queue.filter(q => segmentById(q.id)?.rev === q.rev);
    this.next();
  },
  async next() {
    if (!this.enabled || this.current || !this.queue.length) return;
    const current = this.queue.shift();
    this.current = current;
    this.instance = id();
    this.message = '正在本机生成语音…';
    reportProgress();
    render();
    try {
      const response = await fetch(`/api/v1${route(`/speech/${current.id}/${current.rev}`)}`, {
        headers: {
          Authorization: `Bearer ${state.session.token}`
        }
      });
      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || '本地语音生成失败');
      }
      const blob = await response.blob();
      if (this.current !== current || !this.enabled) return;
      this.url = URL.createObjectURL(blob);
      this.audio = new Audio(this.url);
      this.audio.playbackRate = this.rate;
      this.audio.onplaying = () => {
        if (this.current !== current) return;
        this.active = true;
        this.message = current.text;
        reportProgress();
        render();
      };
      this.audio.onended = () => {
        if (this.current !== current) return;
        this.done.add(current.key);
        this.completed.set(current.key, {segment: current.id, rev: current.rev});
        this.active = false;
        this.current = null;
        this.cancelAudio();
        this.message = '这一句已播完';
        reportProgress();
        this.next();
        render();
      };
      this.audio.onerror = () => {
        if (this.current === current) this.fail('音频播放失败，请检查设备后重新开启');
      };
      await this.audio.play();
    } catch (error) {
      if (this.current === current) this.fail(error.message);
    }
  },
  cancelAudio() {
    if (this.audio) {
      this.audio.onplaying = null;
      this.audio.onended = null;
      this.audio.onerror = null;
      this.audio.pause();
      this.audio.src = '';
      this.audio = null;
    }
    if (this.url) URL.revokeObjectURL(this.url);
    this.url = null;
  },
  fail(message) {
    this.stop();
    this.message = message;
    toast(message);
    render();
  },
  stop() {
    this.enabled = false;
    this.active = false;
    this.current = null;
    this.queue = [];
    this.cancelAudio();
    this.message = '播报已关闭';
    reportProgress();
    render();
  },
  replay() {
    this.stop();
    this.done.clear();
    this.completed.clear();
    return this.start();
  }
};
async function reportProgress() {
  if (!state.online || !state.epoch || !state.snapshot?.me || state.pendingConsent) return;
  let refs = [],
    mode = 'idle',
    instance = 'reading';
  if (document.visibilityState !== 'visible') {
    mode = 'paused';
  } else if (speech.enabled && (speech.current || speech.queue.length)) {
    const list = [...(speech.current ? [speech.current] : []), ...speech.queue];
    refs = list.filter(q => segmentById(q.id)?.rev === q.rev).map(q => ({
      segment: q.id,
      rev: q.rev
    }));
    mode = speech.active ? 'playing' : 'queued';
    instance = speech.instance || 'queue';
  } else if (state.review) {
    refs = [state.review];
    mode = 'review';
    instance = 'review';
  }
  try {
    await api(route('/presentation'), {
      epoch: state.epoch,
      seq: ++state.seq,
      consent_version: state.snapshot.me.consent.version,
      evidence: refs,
      state: mode,
      instance,
      completed: state.snapshot.me.consent.playback ? [...speech.completed.values()].filter(e=>segmentById(e.segment)?.rev===e.rev).slice(-150) : [],
      review_completed: state.snapshot.me.consent.review ? [...state.reviewed.values()].filter(e=>segmentById(e.segment)?.rev===e.rev).slice(-150) : []
    });
  } catch {
    /* 过期回执由下一次当前状态刷新替代。 */
  }
}
document.addEventListener('visibilitychange', () => {
  if (document.hidden && speech.enabled) speech.stop();
  reportProgress();
});

async function startRecording() {
  if (!state.health?.capabilities.asr.ready) throw new Error('FunASR 语音服务尚未启动。请先运行启动脚本，确认 10095 端口可用。');
  const {StreamMicrophone} = await import('/assets/microphone.js');
  microphone = new StreamMicrophone(state.session, message => toast(message));
  await microphone.start();
  state.recording = true;
  render();
}

async function stopRecording() {
  state.recording = false;
  microphone?.stop();
  microphone = null;
  state.recordingStopping = false;
  render();
}

document.addEventListener('change', event => {
  if (event.target.id === 'share-base') {
    try {
      const url = new URL(event.target.value);
      if (!['http:', 'https:'].includes(url.protocol)) throw new Error();
      const s = state.snapshot;
      $('#invite-link').value = `${url.origin}/#invite=${s.invite}`;
      $('#board-link').value = `${url.origin}/room/${s.id}#token=${s.board_token}&role=board`;
      $('#invite-open').href = $('#invite-link').value;
      $('#board-open').href = $('#board-link').value;
    } catch {
      toast('请输入有效的 http 或 https 访问地址');
    }
  }
  if (event.target.matches('[data-evidence]')) {
    const value = event.target.dataset.evidence;
    event.target.checked ? state.selected.add(value) : state.selected.delete(value);
  }
  if (event.target.id === 'speech-rate') {
    speech.rate = Number(event.target.value);
    if (speech.audio) speech.audio.playbackRate = speech.rate;
  }
});
document.addEventListener('submit', async event => {
  if (event.target.id === 'segment-form') {
    event.preventDefault();
    const input = $('#segment-input'),
      text = input.value.trim();
    if (!text) return;
    try {
      await change('/segments', {
        text,
        source: 'manual',
        stable: true
      });
      $('#segment-input').value = '';
      toast('原句已加入字幕');
    } catch (e) {
      toast(e.message);
    }
  }
  if (event.target.id === 'consent-form') {
    event.preventDefault();
    try {
      await saveConsent(new FormData(event.target));
    } catch (e) {
      toast(e.message);
    }
  }
});
document.addEventListener('click', async event => {
  const button = event.target.closest('button');
  if (!button) return;
  try {
    if (button.hasAttribute('data-accept-hint')) {
      const hint = state.visionHint;
      if (!hint) return;
      await change('/requests', {kind:hint.kind, decision:hint.decision});
      interventionPolicy.feedback(true);
      state.visionHint=null;
      render();
      toast('你的请求已送达主持人');
      return;
    }
    if (button.dataset.visionSample) {camera?.record(button.dataset.visionSample);return;}
    if (button.dataset.view) {
      state.view = button.dataset.view;
      render();
      return;
    }
    if (button.dataset.notice) {
      await change(`/notices/${button.dataset.notice}`, {action: button.dataset.noticeAction});
      toast(button.dataset.noticeAction === 'confirm' ? '已为当前议题保留回应窗口' : '已忽略，当前议题状态不变');
      return;
    }
    if (button.dataset.request) {
      await manualDialog(button.dataset.request);
      return;
    }
    if (button.dataset.compose !== undefined) {
      composeDialog(button.dataset.compose);
      return;
    }
    if (button.dataset.quote) {
      composeDialog('', button.dataset.quote);
      return;
    }
    if (button.dataset.review) {
      const s = segmentById(button.dataset.review);
      state.reviewed.delete(`${s.id}:${s.rev}`);
      state.review = {
        segment: s.id,
        rev: s.rev
      };
      await reportProgress();
      render();
      return;
    }
    if (button.dataset.edit) {
      const s = segmentById(button.dataset.edit);
      showDialog('修订原句', `<div class="field"><label class="form-label" for="edit-segment">修订后的完整原话</label><textarea id="edit-segment" name="text" required>${esc(s.text)}</textarea></div><div class="hint amber">关联议题将进入“依据需复核”，主持人重新核对后才会触发留位。</div>${modalButtons('保存新版')}`, async data => {
        await change(`/segments/${s.id}`, {
          version: s.rev,
          text: data.get('text')
        }, 'PATCH');
        modal.close();
      });
      return;
    }
    if (button.dataset.focus) {
      const d = decisionById(button.dataset.focus);
      await change(`/decisions/${d.id}/focus`, {version: d.version});
      toast('已设为当前议题');
      return;
    }
    if (button.dataset.prepare) {
      const d = decisionById(button.dataset.prepare);
      showDialog('确认准备定案', `<h3>${esc(d.title)}</h3>${d.evidence.map(e=>`<div class="quote">${esc(segmentById(e.segment)?.text)}</div>`).join('')}<p class="list-note">确认后，系统将按当前有效的授权和呈现状态，为这项议题保留回应机会。请先核对上述最新原句。</p>${modalButtons('确认依据，准备定案')}`, async () => {
        await change(`/decisions/${d.id}/prepare`, {
          version: d.version
        });
        modal.close();
        toast('已进入定案检查');
      });
      return;
    }
    if (button.dataset.resolve) {
      resolveDialog(button.dataset.resolve, button.dataset.resolveAction);
      return;
    }
    if (button.dataset.reply) {
      replyDialog(button.dataset.reply);
      return;
    }
    if (button.dataset.opinionFeedback) {
      opinionFeedbackDialog(button.dataset.opinionFeedback, button.dataset.feedbackAction);
      return;
    }
    if (button.dataset.intervention) {
      await change('/interventions/feedback', {request: button.dataset.intervention, rating: button.dataset.rating});
      toast('评价已记录，仅你可见；不会改变你的回应资格');
      return;
    }
    if (button.dataset.requestId) {
      const qid = button.dataset.requestId, action = button.dataset.requestAction;
      if (action === 'handled') {
        showDialog('记录已安排的回应', `<p class="list-note">请说明实际安排的发言机会或澄清内容。已提交意见需要逐条答复。</p><label class="form-label" for="request-reason">具体回应说明</label><textarea id="request-reason" name="reason" required maxlength="1000"></textarea>${modalButtons('记录回应')}`, async data => {
          await change(`/requests/${qid}`, {action, reason:data.get('reason')}, 'PATCH');
          modal.close();
        });
      } else {
        await change(`/requests/${qid}`, {action}, 'PATCH');
        toast('回应进度已同步');
      }
      return;
    }
    if (button.dataset.copy) {
      const input = document.getElementById(button.dataset.copy);
      try {
        await navigator.clipboard.writeText(input.value);
        toast('链接已复制');
      } catch {
        input.select();
        toast('请复制已选中的链接');
      }
      return;
    }
    const action = button.dataset.action;
    if (action === 'create') createDialog();
    if (action === 'demo') createDialog(true);
    if (action === 'join') joinDialog();
    if (action === 'close-modal') modal.close();
    if (action === 'invite') inviteDialog();
    if (action === 'consent') consentDialog();
    if (action === 'new-decision') newDecision();
    if (action === 'compose') composeDialog();
    if (action === 'dismiss-vision') {
      camera?.suppress(15000);
      state.visionHint = null;
      if (state.vision) state.vision = {...state.vision, confident: false, kind: 'unknown'};
      publishExpression(state.vision);
      render();
    }
    if (action === 'end-review') {
      if (state.review) state.reviewed.set(`${state.review.segment}:${state.review.rev}`, state.review);
      state.review = null;
      await reportProgress();
      render();
      const el = $('#transcript');
      if (el) el.scrollTop = el.scrollHeight;
    }
    if (action === 'speech') {
      if (speech.enabled) speech.stop();
      else await speech.start();
    }
    if (action === 'speech-replay') await speech.replay();
    if (action === 'record') {
      if (state.recordingStarting || state.recordingStopping) return;
      if (state.recording) await stopRecording();
      else await startRecording();
    }
    if (action === 'auto-analyze') {
      await change('/ai/settings',{enabled:!state.autoAnalyze},'PUT');
    }
    if (action === 'analyze') {
      button.disabled = true;
      button.textContent = '正在本地分析…';
      try {
        const result = await api(route('/analyze'), {});
        state.candidate = result.items;
        if (!result.items.length) toast('这段对话暂未提取出有原句依据的定案候选');
        else showCandidates();
      } finally {
        render();
      }
    }
    if (action === 'match') {
      const originalLabel=button.textContent;
      button.disabled=true;button.textContent='正在本机分析关联…';
      try {
      const text = $('#compose-text').value.trim();
      if (!text) throw new Error('请先写下你的补充');
      const result = await api(route('/matches'), {
        text
      });
      if (!$('#compose-text') || $('#compose-text').value.trim() !== text) return;
      const relation={possible_context:'可能回应的上下文',constraint_conflict:'可能影响原前提',clarification:'澄清问题',addition:'补充信息',support:'支持原议题'};
      $('#match-results').innerHTML = `<p class="list-note">${esc(result.provider)}${result.warning?' · '+esc(result.warning):''}</p>` + (result.items.length ? result.items.map(item => `<div class="quote"><button type="button" class="ghost small" data-match-decision="${item.decision}">${esc(item.title)}</button>${item.relation?`<span class="tag neutral">${relation[item.relation]}</span>`:''}<p>${esc(item.reason)}</p>${item.quote?`<p class="small-text muted">原句依据：${esc(item.quote)}</p>`:''}</div>`).join('') : '<p>未发现相关议题，可以手动选择或作为独立意见提交。</p>');

      } finally {button.disabled=false;button.textContent=originalLabel;}
    }
    if (button.dataset.matchDecision) {
      $('#compose-decision').value = button.dataset.matchDecision;
      toast('已选择原议题，提交前仍可更改');
    }
    if (button.dataset.candidate !== undefined) {
      const c = state.candidate[Number(button.dataset.candidate)];
      modal.close();
      newDecision(c);
    }
    if (action === 'export') {
      const preview = await api(route('/export/preview'));
      showDialog('导出前预览', `<p class="list-note">包含字幕、议题依据、已提交意见和人工处理结果。请核对这些内容适合保存。</p><pre>${esc(JSON.stringify(preview.data,null,2))}</pre>${modalButtons('下载 JSON 记录')}`, async () => {
        const data = await api(route('/export'), {
          revision: preview.revision
        });
        const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {
          type: 'application/json;charset=utf-8'
        }));
        const a = document.createElement('a');
        a.href = url;
        a.download = '同席_讨论记录.json';
        a.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        modal.close();
      });
    }
    if (action === 'end') {
      showDialog('结束并清空本场讨论', `<p>这将清除本场字幕、意见、授权进度与处理记录。需要的记录请先导出。</p>${modalButtons('确认结束并清空')}`, async () => {
        await change('/close', {});
        modal.close();
      });
    }
    if (action === 'camera') {
      if (camera?.stream) {
        camera.stop();
        render();
      } else {
        const consent = state.snapshot.me.consent;
        if (!consent.camera) {
          await change('/me/consent', {
            version: consent.version,
            playback: consent.playback,
            review: consent.review,
            camera: true,
            intervene: consent.intervene
          }, 'PUT');
        }
        const {PrivateVision} = await import('/assets/vision.js');
        camera = new PrivateVision(message => {
          const el = $('#camera-status');
          if (el) el.textContent = message;
        }, applyVision);
        await camera.start();
        syncSense();
        render();
      }
    }
    if (action === 'calibrate') camera?.calibrate();
  } catch (error) {
    toast(error.message);
  }
});

function syncSense() {
  if (!camera || !state.snapshot) return;
  const snap = state.snapshot;
  const current = (snap.decisions || []).find(item => item.id === snap.active_decision);
  const phase = snap.context_phase || snap.analysis?.phase || 'uncertain';
  camera.setContext({
    decision: current?.status === 'preparing' || phase === 'seeking_response' || phase === 'closing',
    stableIds: (snap.segments || []).filter(seg => seg.stable).map(seg => seg.id)
  });
}
function applyServerAnalysis() {
  const data = state.snapshot?.analysis;
  if (!data) return;
  state.candidate = data.items || [];
  state.autoAnalyze = state.snapshot.ai_enabled;
  state.analysisBusy = ['queued','running'].includes(data.status);
  state.analysisError = data.error || '';
}
function candidateMarkup(c, index) {
  return `<article class="quote"><b>${esc(c.title)}</b><p>${esc(c.conclusion)}</p>${c.why?`<p class="small-text muted">${esc(c.why)}</p>`:''}<details><summary class="small-text">查看所依据的原句</summary>${c.evidence.map(e=>`<p class="small-text">${esc(segmentById(e.segment)?.text||'原句已更新')} · v${e.rev}</p>`).join('')}</details><button type="button" class="small" data-candidate="${index}">核对并建立</button></article>`;
}
function suggestionPanel() {
  const a=state.snapshot?.analysis||{};
  const phase={discussing:'正在讨论选项',seeking_response:'正在征求回应',closing:'正在收束议题',uncertain:'阶段尚不明确'}[a.phase]||'等待讨论原句';
  const status=state.analysisBusy?'正在本机分析…':state.analysisError||(!state.autoAnalyze?'自动分析已暂停':a.status==='ready'?'已完成本地分析':'等待稳定原句');
  return panel('本地 AI 候选议题', `<div class="panel-body"><div class="row between"><span class="small-text">${esc(status)}</span><button class="small" data-action="auto-analyze">${state.autoAnalyze?'暂停自动提议':'开启自动提议'}</button></div><div class="ai-analysis-meta"><span>${esc(phase)}</span>${a.elapsed_ms?`<span>${esc(a.provider)} · ${(a.elapsed_ms/1000).toFixed(1)} 秒</span>`:''}</div>${a.summary?`<p class="small-text">${esc(a.summary)}</p>`:''}${state.candidate.map(candidateMarkup).join('')}${!state.candidate.length&&a.status==='ready'?'<p class="list-note">本轮没有可确认的定案候选，继续保留原句供讨论。</p>':''}</div>`);
}

function showCandidates() {
  showDialog('AI 提出的候选议题', `<p class="list-note">以下是本地模型输出，请核对原句再建立议题。</p>${state.candidate.map(candidateMarkup).join('')}`, null);
}
async function init() {
  try {
    state.health = await api('/health');
  } catch {
    toast('未连接到房间服务，请用启动脚本运行项目');
  }
  state.e1 = Boolean(state.health && state.health.e1);
  if (state.e1) {
    const experiment = await import('/assets/e1.js');
    experiment.attach();
  }
  const room = location.pathname.match(/^\/room\/([^/]+)$/)?.[1];
  if (room) {
    const hash = new URLSearchParams(location.hash.slice(1));
    if (hash.has('token')) {
      state.session = {
        room,
        token: hash.get('token'),
        role: hash.get('role') || 'member'
      };
      sessionStorage.setItem(`tongxi:${room}`, JSON.stringify(state.session));
      history.replaceState(null, '', `/room/${room}`);
    } else {
      try {
        state.session = JSON.parse(sessionStorage.getItem(`tongxi:${room}`));
      } catch {}
    }
    if (state.session) {
      try {
        await reload();
        connect();
      } catch (e) {
        state.session = null;
        history.replaceState(null, '', '/');
        home();
        toast(e.message);
      }
      return;
    }
    history.replaceState(null, '', '/');
    home();
    toast('当前标签页没有房间会话，请使用邀请链接加入');
    return;
  }
  home();
}
window.addEventListener('beforeunload', () => {
  microphone?.abort();
  camera?.stop();
  window.speechSynthesis?.cancel();
});
init();
