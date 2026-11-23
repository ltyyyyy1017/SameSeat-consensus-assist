// E1 标注只在实验开关打开后加载。按键不进入视觉特征，也不调用正式提醒接口。
const REVIEW_MS = 180000;
const PAUSE_MS = 20000;
const UPLOAD_MS = 2000;

function roomId() {
  return location.pathname.split('/').pop();
}

function storageKey(name) {
  return `tongxi-e1:${roomId()}:${name}`;
}

let sessionRole = sessionStorage.getItem(storageKey('role')) || 'calibration';
let sessionId = sessionStorage.getItem(storageKey('session')) || '';
let presses = JSON.parse(sessionStorage.getItem(storageKey('presses')) || '[]');
let pending = JSON.parse(sessionStorage.getItem(storageKey('pending')) || '[]');
let collecting = sessionStorage.getItem(storageKey('collecting')) !== '0';
let reviewTimer = 0;
let uploadTimer = 0;
let sending = false;
let lastError = '';
let baselineArming = false;
let restoreStarted = false;
let restoredBaseline = null;

function authHeaders() {
  const session = JSON.parse(sessionStorage.getItem('tongxi:' + roomId()) || 'null');
  return {
    'Content-Type': 'application/json',
    'Idempotency-Key': crypto.randomUUID(),
    Authorization: session ? 'Bearer ' + session.token : ''
  };
}

function remember() {
  sessionStorage.setItem(storageKey('presses'), JSON.stringify(presses));
  sessionStorage.setItem(storageKey('pending'), JSON.stringify(pending));
  sessionStorage.setItem(storageKey('collecting'), collecting ? '1' : '0');
}

function showError(message) {
  lastError = message || '';
  const node = document.getElementById('e1-error');
  if (node) node.textContent = lastError;
}

async function post(path, body) {
  const response = await fetch(`/api/v1/rooms/${roomId()}${path}`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify(body)
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : '实验记录没有写下');
  return payload;
}

function currentAgenda(state) {
  return state.snapshot && state.snapshot.active_decision || '';
}

function queueFrames(frames) {
  if (!collecting || !frames.length) return;
  pending.push({
    batch_id: crypto.randomUUID(),
    session_id: sessionId,
    session_role: sessionRole,
    agenda_id: currentAgenda(window.tongxiE1State || {}),
    frames
  });
  remember();
}

async function sendOldest() {
  if (sending || !pending.length) return pending.length === 0;
  const batch = pending[0];
  sending = true;
  try {
    const response = await fetch(`/api/v1/rooms/${roomId()}/e1/frames`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify(batch)
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : '上传没有完成');
    pending.shift();
    remember();
    showError('');
    return true;
  } catch (error) {
    showError(error.message + '。这批数据还在本机，会自动重试。');
    return false;
  } finally {
    sending = false;
  }
}

async function uploadAll() {
  while (pending.length) {
    const ok = await sendOldest();
    if (!ok) return false;
  }
  return true;
}

export function attach() {
  if (!sessionId) {
    sessionId = 'e1-' + crypto.randomUUID();
    sessionStorage.setItem(storageKey('session'), sessionId);
  }
  window.tongxiE1 = {afterRender, recordFrame, openReview, uploadAll};
  reviewTimer = window.setInterval(openReview, REVIEW_MS);
  uploadTimer = window.setInterval(() => { sendOldest(); }, UPLOAD_MS);
  sendOldest();
}

export function recordFrame(frame) {
  const state = window.tongxiE1State;
  if (!state || !state.snapshot || state.snapshot.role !== 'member' || !collecting) return;
  if (frame.event === 'baseline_arm') {
    if (sessionRole !== 'calibration') {
      showError('第二次讨论不能重新采集自然坐姿。');
      return;
    }
    if (baselineArming || (window.tongxiVision && window.tongxiVision.capture && window.tongxiVision.capture.kind === 'baseline')) return;
    baselineArming = true;
    const previous = window.tongxiVision ? window.tongxiVision.baseline : null;
    post('/e1/baseline-attempts', {session_role: 'calibration', calibration_id: sessionId}).then(ticket => {
      if (window.tongxiVision) window.tongxiVision.baseline = previous;
      if (window.tongxiVision) window.tongxiVision.runNeutralBaseline(ticket);
    }).catch(error => showError(error.message)).finally(() => { baselineArming = false; });
    return;
  }
  if (frame.event === 'baseline_failed' || frame.event === 'baseline_ready') {
    if (sessionRole !== 'calibration') return;
    submitBaseline(frame);
    return;
  }
  if (frame.event === 'calibration') return;
  if (frame.event === 'sample_failed') {
    showError((frame.sample_label || '样例') + '没有保存：' + (frame.reason || '有效帧不足'));
    return;
  }
  if (frame.event === 'sample' && sessionRole === 'calibration') {
    const samples = JSON.parse(sessionStorage.getItem(storageKey('samples')) || '{}');
    if (!frame.sample_frames || frame.sample_frames.length < 6) {
      showError('样例不足 6 帧，没有保存。');
      return;
    }
    samples[frame.sample_label] = frame.sample_frames.slice(0, 8);
    sessionStorage.setItem(storageKey('samples'), JSON.stringify(samples));
    const baseline = JSON.parse(sessionStorage.getItem(storageKey('baseline')) || 'null');
    if (baseline) {
      post('/e1/calibration', {
        session_role: 'calibration',
        calibration_id: sessionId,
        duration_ms: calibrationDuration(samples),
        baseline,
        samples
      }).then(result => {
        if (result.locked) showError('三类样例已锁定。');
      }).catch(error => showError(error.message));
    }
    return;
  }
  if (frame.event) return;
  queueFrames([{
    frame_id: crypto.randomUUID(),
    timestamp_ms: frame.timestamp_ms,
    raw: frame.raw,
    face_count: frame.face_count,
    shoulder_valid: frame.shoulder_valid,
    visibility_status: frame.visibility_status,
    elapsed_ms: frame.elapsed_ms
  }]);
}

function submitBaseline(frame) {
  const previous = window.tongxiVision ? window.tongxiVision.baseline : null;
  if (!sessionStorage.getItem(storageKey('cal-start'))) sessionStorage.setItem(storageKey('cal-start'), String(Date.now()));
  const samples = JSON.parse(sessionStorage.getItem(storageKey('samples')) || '{}');
  post('/e1/calibration', {
    session_role: 'calibration',
    calibration_id: sessionId,
    duration_ms: frame.duration_ms,
    video_width: frame.video_width,
    video_height: frame.video_height,
    attempt: frame.attempt,
    attempt_id: frame.attempt_id,
    baseline_measurements: frame.measurements,
    samples
  }).then(result => {
    sessionStorage.setItem(storageKey('baseline'), JSON.stringify(result.baseline));
    if (window.tongxiVision && result.baseline) window.tongxiVision.baseline = result.baseline;
    showError('自然坐姿已按有效帧的中位数保存。');
  }).catch(error => {
    if (window.tongxiVision) window.tongxiVision.baseline = previous;
    showError(error.message);
  });
}

function applyBaseline(baseline) {
  if (!baseline) return;
  restoredBaseline = baseline;
  sessionStorage.setItem(storageKey('baseline'), JSON.stringify(baseline));
  if (window.tongxiVision) window.tongxiVision.baseline = baseline;
}

async function restoreCalibration() {
  if (!restoreStarted) {
    restoreStarted = true;
    const saved = JSON.parse(sessionStorage.getItem(storageKey('baseline')) || 'null');
    applyBaseline(saved);
    const response = await fetch(`/api/v1/rooms/${roomId()}/e1/calibration`, {headers: authHeaders()});
    if (response.ok) {
      const payload = await response.json();
      if (payload.baseline) applyBaseline(payload.baseline);
    }
  } else if (restoredBaseline && window.tongxiVision && !window.tongxiVision.baseline) {
    window.tongxiVision.baseline = restoredBaseline;
  }
}

function calibrationDuration(samples) {
  const start = Number(sessionStorage.getItem(storageKey('cal-start')) || 0);
  const ready = ['listen', 'speak', 'wait'].every(label => samples[label] && samples[label].length >= 6);
  return start && ready ? Date.now() - start : null;
}

function afterRender(state) {
  window.tongxiE1State = state;
  if (state.snapshot && state.snapshot.role === 'member') restoreCalibration();
  if (!state.snapshot || state.snapshot.role === 'board') return;
  const main = document.getElementById('main');
  if (!main || document.getElementById('e1-panel')) return;
  const panel = document.createElement('section');
  panel.id = 'e1-panel';
  panel.className = 'panel';
  panel.style.marginTop = '16px';
  if (state.snapshot.role === 'member') {
    const roleLabel = sessionRole === 'test' ? '第二次讨论：使用已锁定的基准和样例' : '第一次讨论：建立个人基准和样例';
    const switcher = sessionRole === 'test' ? '' : '<button type="button" class="small" id="e1-role">改为第二次讨论</button>';
    panel.innerHTML = `<div class="panel-body"><p class="list-note">E1 实验标注。按下只记录时间，不提醒主持人，也不改变议题。不能凭模型或面部动作补写。</p><p class="small-text">${roleLabel}</p><p class="small-text">这场编号 ${sessionId}</p><p class="small-text" id="e1-error"></p><div class="actions"><button type="button" id="e1-intent">此刻我想说</button><button type="button" class="small" id="e1-review-now">回顾刚才三分钟</button>${switcher}<button type="button" class="small" id="e1-finish">结束本场采集并完成上传</button></div></div>`;
    main.appendChild(panel);
    showError(lastError);
    panel.querySelector('#e1-intent').addEventListener('click', async () => {
      try {
        const saved = await post('/e1/intents', {
          session_id: sessionId,
          session_role: sessionRole,
          agenda_id: currentAgenda(state),
          pressed_at_ms: Date.now()
        });
        presses.push(saved);
        remember();
        showError('');
      } catch (error) {
        showError(error.message);
      }
    });
    panel.querySelector('#e1-review-now').addEventListener('click', openReview);
    const roleButton = panel.querySelector('#e1-role');
    if (roleButton) roleButton.addEventListener('click', async () => {
      const ok = await uploadAll();
      if (!ok) return;
      const video = document.querySelector('#camera-video');
      const same = window.confirm('第二次讨论要沿用第一次的设备、摄像头、分辨率、机位、距离和光照。现在是否仍然一样？');
      const nextId = 'e1-' + crypto.randomUUID();
      try {
        const opened = await post('/e1/session', {
          session_id: nextId,
          session_role: 'test',
          same_device: same,
          video_width: video && video.videoWidth || 0,
          video_height: video && video.videoHeight || 0
        });
        if (opened.protocol_deviation) showError('设备和机位与第一次不一致，已标成方案偏差，不会当成标准配对样本。');
      } catch (error) {
        showError(error.message);
        return;
      }
      sessionRole = 'test';
      sessionStorage.setItem(storageKey('role'), sessionRole);
      sessionId = nextId;
      sessionStorage.setItem(storageKey('session'), sessionId);
      presses = [];
      remember();
      panel.remove();
      afterRender(state);
    });
    panel.querySelector('#e1-finish').addEventListener('click', () => finishCollection(state));
    return;
  }
  panel.innerHTML = `<div class="panel-body"><p class="list-note">实验员只能标记看见的开口。开口不是表达意愿，也不会生成正向标签。通用参照由第一次讨论的坐姿中位数生成，不能在这里填四个数。</p><p class="small-text" id="e1-error"></p><div class="actions"><button type="button" id="e1-speech">记录实际开口时间</button><a class="tag" id="e1-export" href="#">导出 E1 记录</a></div></div>`;
  main.appendChild(panel);
  panel.querySelector('#e1-speech').addEventListener('click', async () => {
    const participant = window.prompt('匿名编号（p 开头）。没有观察到开口就取消。');
    if (!participant) return;
    const memberSession = window.prompt('成员屏幕上的这场编号。对不上就取消，不要猜测。');
    if (!memberSession) return;
    try {
      await post('/e1/speech', {
        participant_id: participant,
        session_id: memberSession,
        agenda_id: currentAgenda(state),
        t_speech_ms: Date.now()
      });
      showError('已记录开口。这不是表达意愿。');
    } catch (error) {
      showError(error.message);
    }
  });
  panel.querySelector('#e1-export').addEventListener('click', async event => {
    event.preventDefault();
    const response = await fetch(`/api/v1/rooms/${roomId()}/e1/export`, {headers: authHeaders()});
    const payload = await response.json();
    const blob = new Blob([JSON.stringify(payload, null, 2)], {type: 'application/json'});
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = 'e1-export.json';
    link.click();
  });
}

async function finishCollection(state) {
  collecting = false;
  remember();
  const ok = await uploadAll();
  if (!ok) {
    showError('还有数据没有上传成功。采集已停止，数据仍留在本机。');
    return;
  }
  const response = await fetch(`/api/v1/rooms/${roomId()}/e1/integrity?session_id=${encodeURIComponent(sessionId)}`, {headers: authHeaders()});
  const payload = await response.json();
  if (!response.ok) {
    showError(typeof payload.detail === 'string' ? payload.detail : '没能核对服务端数量');
    return;
  }
  showError(`上传完成。服务端这场有 ${payload.frames} 帧、${payload.batches.length} 个批次。`);
}

function openReview() {
  const state = window.tongxiE1State;
  if (!state || !state.snapshot || state.snapshot.role !== 'member') return;
  const existing = document.getElementById('e1-review');
  if (existing) existing.remove();
  const anchor = Date.now();
  const layer = document.createElement('section');
  layer.id = 'e1-review';
  layer.style.cssText = 'position:fixed;inset:0;background:rgba(15,23,32,.72);z-index:30;display:flex;align-items:center;justify-content:center;padding:24px;';
  const recent = presses.filter(item => {
    const pressed = item.pressed_at_ms || 0;
    return pressed >= anchor - REVIEW_MS && pressed <= anchor + 2000;
  });
  const mine = recent.map(item => `<li>${new Date(item.pressed_at_ms || Date.now()).toLocaleTimeString()} <button type="button" data-delete="${item.id}">删除误触</button></li>`).join('') || '<li>这三分钟没有按键。</li>';
  layer.innerHTML = `<div class="panel" style="max-width:560px"><div class="panel-body"><h2>回顾刚才三分钟</h2><p class="list-note">暂停 20 秒。这里不显示模型判断。不能凭模型或面部动作补写。大约在刚才三分钟里的多少秒前，你开始希望表达？说不出时间点就选无法确定。</p><ul>${mine}</ul><div class="actions"><button type="button" id="e1-add">有未记录的表达意愿</button><button type="button" id="e1-negative">我确认本时间段没有表达意愿</button><button type="button" id="e1-unknown">我无法确定</button></div></div></div>`;
  document.body.appendChild(layer);
  const closeLater = window.setTimeout(() => layer.remove(), PAUSE_MS);
  layer.querySelectorAll('[data-delete]').forEach(button => {
    button.addEventListener('click', async () => {
      await post('/e1/intents/' + button.dataset.delete, {action: 'delete', review_anchor_ms: anchor});
      presses = presses.filter(item => item.id !== button.dataset.delete);
      remember();
      button.parentElement.remove();
    });
  });
  layer.querySelector('#e1-add').addEventListener('click', async () => {
    const ago = window.prompt('大约在刚才三分钟里的多少秒前，你开始希望表达？');
    if (ago === null) return;
    const seconds = Number(ago);
    if (!Number.isFinite(seconds) || seconds < 0 || seconds > 180) {
      showError('说不出一个三分钟内的时间点时，请选择「我无法确定」。');
      return;
    }
    const eventTime = anchor - Math.round(seconds * 1000);
    await post('/e1/labels', {
      action: 'add_positive', session_id: sessionId, session_role: sessionRole,
      agenda_id: currentAgenda(state), start_timestamp: eventTime, end_timestamp: eventTime,
      review_anchor_ms: anchor
    });
    clearTimeout(closeLater);
    layer.remove();
  });
  layer.querySelector('#e1-negative').addEventListener('click', async () => {
    await post('/e1/labels', {
      action: 'confirm_negative', session_id: sessionId, session_role: sessionRole,
      agenda_id: currentAgenda(state), start_timestamp: anchor - REVIEW_MS, end_timestamp: anchor,
      review_anchor_ms: anchor
    });
    clearTimeout(closeLater);
    layer.remove();
  });
  layer.querySelector('#e1-unknown').addEventListener('click', async () => {
    await post('/e1/labels', {
      action: 'uncertain', session_id: sessionId, session_role: sessionRole,
      agenda_id: currentAgenda(state), start_timestamp: anchor - REVIEW_MS, end_timestamp: anchor,
      review_anchor_ms: anchor
    });
    clearTimeout(closeLater);
    layer.remove();
  });
}

window.addEventListener('beforeunload', () => { sendOldest(); });
