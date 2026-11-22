// 视觉特征与视频仅保留在当前端，不含服务端请求。
import {FeatureWindow, SenseSchedule, TemporalActionModel} from '/assets/vision-policy.js';
export class PrivateVision {
  constructor(notify, onSignal = () => {}) {
    this.notify = notify;
    this.onSignal = onSignal;
    this.onExperimentFrame = null;
    this.model = new TemporalActionModel();
    this.window = new FeatureWindow();
    this.schedule = new SenseSchedule();
    this.bindSchedule();
    this.timerToken = 0;
    this.ticking = false;
    this.gaps = [];
    this.lastTick = 0;
    this.seenCaptions = new Set();
    this.captionsReady = false;
    this.capture = null;
    this.streak = 0;
    this.previous = null;
    this.stream = null;
    this.face = null;
    this.pose = null;
    this.timer = null;
    this.baseline = null;
    this.last = null;
    this.kind = null;
    this.confident = false;
    this.bars = null;
    this.mouthStreak = 0;
    this.suppressedUntil = 0;
    this.message = '正在准备本地视觉模型…';
  }
  update(message) {
    this.message = message;
    this.notify(message);
  }
  noteExperiment(partial) {
    if (typeof this.onExperimentFrame !== 'function') return;
    const raw = this.last ? {scale: this.last.scale, mouth: this.last.mouth, pitch: this.last.pitch, turn: this.last.turn} : null;
    this.onExperimentFrame(Object.assign({
      timestamp_ms: Date.now(),
      raw,
      face_count: 0,
      shoulder_valid: false,
      visibility_status: 'record_error',
      feature_vector: null,
      notice: null,
      elapsed_ms: this.lastElapsed || 0
    }, partial));
  }
  async start() {
    try {
      const {
        FilesetResolver,
        FaceLandmarker,
        PoseLandmarker
      } = await import('/assets/vendor/vision_bundle.mjs');
      const files = await FilesetResolver.forVisionTasks('/assets/vendor/wasm');
      this.face = await FaceLandmarker.createFromOptions(files, {
        baseOptions: {
          modelAssetPath: '/assets/vendor/face_landmarker.task'
        },
        runningMode: 'VIDEO',
        numFaces: 2
      });
      this.pose = await PoseLandmarker.createFromOptions(files, {
        baseOptions: {
          modelAssetPath: '/assets/vendor/pose_landmarker_lite.task'
        },
        runningMode: 'VIDEO',
        numPoses: 1
      });
      this.stream = await navigator.mediaDevices.getUserMedia({
        video: {
          width: 480,
          height: 360
        },
        audio: false
      });
      this.video = document.createElement('video');
      this.video.muted = true;
      this.video.playsInline = true;
      this.video.srcObject = this.stream;
      await this.video.play();
      this.update('画面只在本机处理。请正坐后点击“校准自然坐姿”。');
      this.tick();
    } catch (error) {
      this.stop();
      throw new Error('摄像头或本地视觉模型不可用。请检查权限，并运行 scripts/setup_vision.py 准备模型。');
    }
  }
  bindSchedule() {
    const schedule = this.schedule;
    const enter = schedule.enter.bind(schedule);
    schedule.enter = (level, now, reason) => {
      const before = schedule.level;
      enter(level, now, reason);
      if (schedule.level !== before) this.arm(typeof now === 'number' ? now : performance.now());
    };
  }
  arm(now = performance.now()) {
    if (this.capture && this.capture.experiment) return;
    this.timerToken += 1;
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    if (!this.stream || this.ticking) return;
    const since = this.lastTick ? Math.max(0, now - this.lastTick) : 0;
    const wait = Math.max(0, this.schedule.interval() - since);
    const token = this.timerToken;
    this.plan = this.plan || [];
    this.plan.push(this.schedule.interval());
    this.timer = setTimeout(() => {
      if (token !== this.timerToken || this.ticking) return;
      this.timer = null;
      this.tick();
    }, wait);
  }
  setContext(context) {
    const decision = Boolean(context && context.decision);
    const ids = context && context.stableIds || [];
    const now = performance.now();
    if (!this.captionsReady) {
      ids.forEach(id => this.seenCaptions.add(id));
      this.captionsReady = true;
      this.schedule.setDecision(decision, now);
      return;
    }
    const fresh = ids.filter(id => !this.seenCaptions.has(id));
    fresh.forEach(id => this.seenCaptions.add(id));
    if (!fresh.length) this.schedule.setDecision(decision, now);
    else fresh.forEach(() => this.schedule.noteCaption(decision, now));
  }
  tick() {
    if (!this.stream || this.ticking) return;
    this.ticking = true;
    this.timerToken += 1;
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    const started = performance.now();
    if (this.lastTick) this.gaps.push(Math.round(started - this.lastTick));
    this.lastTick = started;
    if (this.gaps.length > 48) this.gaps.shift();
    let elapsed = 0;
    try {
      const faces = this.face.detectForVideo(this.video, started).faceLandmarks;
      const poses = this.pose.detectForVideo(this.video, started).landmarks;
      elapsed = performance.now() - started;
      this.lastElapsed = elapsed;
      if (faces.length !== 1 || !poses.length) {
        this.invalidate();
        this.schedule.observe({now: started, elapsed, faces: faces.length, shoulders: false, mouth: 0, predict: null, ready: false, candidates: null});
        this.noteExperiment({face_count: faces.length, shoulder_valid: false, visibility_status: faces.length > 1 ? 'multi_face' : faces.length === 0 ? 'no_face' : 'occluded'});
        this.update(faces.length > 1 ? '多人同框，暂不判断。' : '当前画面无法可靠取得本人动作，暂不判断。');
      } else {
        const f = faces[0],
          p = poses[0],
          width = Math.hypot(p[11].x - p[12].x, p[11].y - p[12].y);
        if (width < .08 || Math.min(p[11].visibility, p[12].visibility) < .5) {
          this.invalidate();
          this.schedule.observe({now: started, elapsed, faces: 1, shoulders: false, mouth: 0, predict: null, ready: false, candidates: null});
          this.noteExperiment({face_count: 1, shoulder_valid: false, visibility_status: 'shoulder'});
          this.update('肩部出框或遮挡，请调整机位。');
        } else {
          const mouth = Math.hypot(f[13].x - f[14].x, f[13].y - f[14].y) / Math.max(.01, Math.hypot(f[33].x - f[263].x, f[33].y - f[263].y));
          this.last = {
            scale: width,
            mouth,
            pitch: (f[1].y - (f[33].y + f[263].y)/2) / Math.max(.01, Math.abs(f[33].x-f[263].x)),
            turn: (f[1].x - (f[33].x + f[263].x)/2) / Math.max(.01, Math.abs(f[33].x-f[263].x))
          };
          const frame = this.baseline ? [(this.last.scale/this.baseline.scale-1)/.18, (this.last.mouth-this.baseline.mouth)/.12,
            (this.last.pitch-this.baseline.pitch)/.2, (this.last.turn-this.baseline.turn)/.2] : [0, 0, 0, 0];
          this.bars = {
            mouth: Math.max(0, Math.min(1, frame[1])),
            lean: Math.max(0, Math.min(1, frame[0])),
            pitch: Math.max(0, Math.min(1, Math.abs(frame[2]))),
            turn: Math.max(0, Math.min(1, Math.abs(frame[3])))
          };
          if (this.baseline) {
            this.observe(started);
          }
        }
      }
    } catch {
      this.update('本机视觉处理暂停，请关闭后重试。');
      this.stop();
    } finally {
      this.ticking = false;
      if (this.stream) this.arm(performance.now());
    }
  }
  calibrate() {
    if (typeof this.onExperimentFrame === 'function') {
      this.startNeutralBaseline();
      return;
    }
    if (!this.last) {
      this.update('请让面部与双肩完整入框后再校准。');
      return;
    }
    this.baseline = {
      ...this.last
    };
    this.model = new TemporalActionModel();
    this.window.clear();
    this.frames = [];
    this.sampleToken = (this.sampleToken || 0) + 1;
    clearTimeout(this.sampleTimer);
    this.capture = null;
    this.streak = 0;
    this.previous = null;
    this.mouthStreak = 0;
    this.onSignal(null);
    this.noteExperiment({event: 'calibration', raw: {...this.baseline}, face_count: 1, shoulder_valid: true, visibility_status: 'ok'});
    this.update('自然坐姿已校准，开始比较相对动作。');
  }
  record(label) {
    if (!this.baseline) {this.update('请先校准自然坐姿，再记录动作示例。');return;}
    if (typeof this.onExperimentFrame === 'function') {
      this.capture = {label, frames: [], started: performance.now(), experiment: true};
      this.update('E1 样例采集：每 250 毫秒一帧，最多 4 秒，至少 6 帧有效画面。无效帧不计。');
      this.armExperimentSample();
      return;
    }
    this.capture = {label, frames: [], started: performance.now()};
    this.update('请保持你希望表达的动作两秒，样例只留在本机本次会话。');
  }
  startNeutralBaseline() {
    if ((this.neutralFailures || 0) >= 2) {
      this.update('已经重试过一次，仍不足 24 帧有效画面。基准没有更新。');
      this.noteExperiment({event: 'baseline_failed', reason: '已经重试过一次，仍不足 24 帧有效画面。基准没有更新。', valid_frames: 0});
      return;
    }
    this.sampleToken = (this.sampleToken || 0) + 1;
    clearTimeout(this.sampleTimer);
    this.capture = {kind: 'baseline', frames: [], started: performance.now(), experiment: true};
    this.update('自然坐姿采集 8 秒：每 250 毫秒一帧，至少 24 帧有效。请正对屏幕，嘴部放松，面部和双肩入镜。');
    this.armNeutralBaseline();
  }
  armNeutralBaseline() {
    this.sampleToken = (this.sampleToken || 0) + 1;
    const token = this.sampleToken;
    clearTimeout(this.sampleTimer);
    const step = () => {
      if (token !== this.sampleToken || !this.capture || this.capture.kind !== 'baseline') return;
      if (this.ticking) {
        this.sampleTimer = setTimeout(step, 40);
        return;
      }
      const age = performance.now() - this.capture.started;
      if (this.capture.frames.length >= 32 || age >= 8000) {
        this.finishNeutralBaseline();
        return;
      }
      this.sampleOnce();
      if (!this.capture || this.capture.kind !== 'baseline') return;
      if (this.capture.frames.length >= 32 || performance.now() - this.capture.started >= 8000) {
        this.finishNeutralBaseline();
        return;
      }
      this.sampleTimer = setTimeout(step, 250);
    };
    this.sampleTimer = setTimeout(step, 250);
  }
  finishNeutralBaseline() {
    const capture = this.capture;
    this.capture = null;
    this.sampleToken = (this.sampleToken || 0) + 1;
    clearTimeout(this.sampleTimer);
    if (!capture) return;
    const valid = capture.frames.filter(frame => frame && frame.raw && ['scale', 'mouth', 'pitch', 'turn'].every(key => Number.isFinite(frame.raw[key])));
    const attempt = (this.neutralAttempts || 0) + 1;
    this.neutralAttempts = attempt;
    if (valid.length < 24) {
      this.neutralFailures = (this.neutralFailures || 0) + 1;
      const retry = this.neutralFailures < 2;
      const reason = retry ? '有效帧不足 24。还可以重新采集一次。这次没有更新基准。' : '重试后仍不足 24 帧。基准没有更新。';
      this.noteExperiment({event: 'baseline_failed', reason, valid_frames: valid.length, attempt});
      this.update(reason);
    } else {
      this.neutralFailures = 0;
      this.neutralAttempts = 0;
      this.noteExperiment({
        event: 'baseline_ready',
        measurements: capture.frames.slice(0, 32),
        duration_ms: Math.min(10000, Math.max(1, Math.round(performance.now() - capture.started))),
        video_width: this.video && this.video.videoWidth || 0,
        video_height: this.video && this.video.videoHeight || 0,
        attempt: Math.min(attempt, 2),
        valid_frames: valid.length
      });
      this.update('正在用有效帧的中位数保存自然坐姿。没有达到 24 帧就不会替换已有基准。');
    }
    if (this.stream) this.arm(performance.now());
  }
  armExperimentSample() {
    this.sampleToken = (this.sampleToken || 0) + 1;
    const token = this.sampleToken;
    clearTimeout(this.sampleTimer);
    const step = () => {
      if (token !== this.sampleToken || !this.capture || !this.capture.experiment) return;
      if (this.ticking) {
        this.sampleTimer = setTimeout(step, 40);
        return;
      }
      const age = performance.now() - this.capture.started;
      if (this.capture.frames.length >= 8 || age >= 4000) {
        this.finishExperimentSample();
        return;
      }
      this.sampleOnce();
      if (!this.capture || !this.capture.experiment) return;
      if (this.capture.frames.length >= 8 || performance.now() - this.capture.started >= 4000) {
        this.finishExperimentSample();
        return;
      }
      this.sampleTimer = setTimeout(step, 250);
    };
    this.sampleTimer = setTimeout(step, 250);
  }
  sampleOnce() {
    if (!this.stream || !this.face || !this.pose || this.ticking) return;
    this.ticking = true;
    const started = performance.now();
    try {
      const faces = this.face.detectForVideo(this.video, started).faceLandmarks;
      const poses = this.pose.detectForVideo(this.video, started).landmarks;
      this.lastElapsed = performance.now() - started;
      if (faces.length !== 1 || !poses.length) {
        const visibility = faces.length > 1 ? 'multi_face' : faces.length === 0 ? 'no_face' : 'occluded';
        this.noteExperiment({face_count: faces.length, shoulder_valid: false, visibility_status: visibility});
        if (this.capture && this.capture.kind === 'baseline') {
          this.capture.frames.push({raw: null, face_count: faces.length, shoulder_valid: false, visibility_status: visibility});
        }
        return;
      }
      const pose = poses[0];
      const width = Math.hypot(pose[11].x - pose[12].x, pose[11].y - pose[12].y);
      if (width < .08 || Math.min(pose[11].visibility, pose[12].visibility) < .5) {
        this.noteExperiment({face_count: 1, shoulder_valid: false, visibility_status: 'shoulder'});
        if (this.capture && this.capture.kind === 'baseline') {
          this.capture.frames.push({raw: null, face_count: 1, shoulder_valid: false, visibility_status: 'shoulder'});
        }
        return;
      }
      const face = faces[0];
      this.last = {
        scale: width,
        mouth: Math.hypot(face[13].x - face[14].x, face[13].y - face[14].y) / Math.max(.01, Math.hypot(face[33].x - face[263].x, face[33].y - face[263].y)),
        pitch: (face[1].y - (face[33].y + face[263].y) / 2) / Math.max(.01, Math.abs(face[33].x - face[263].x)),
        turn: (face[1].x - (face[33].x + face[263].x) / 2) / Math.max(.01, Math.abs(face[33].x - face[263].x))
      };
      if (this.capture.kind === 'baseline') {
        this.capture.frames.push({
          raw: {scale: this.last.scale, mouth: this.last.mouth, pitch: this.last.pitch, turn: this.last.turn},
          face_count: 1,
          shoulder_valid: true,
          visibility_status: 'ok'
        });
        return;
      }
      if (!this.baseline) return;
      const feature = [(this.last.scale / this.baseline.scale - 1) / .18, (this.last.mouth - this.baseline.mouth) / .12,
        (this.last.pitch - this.baseline.pitch) / .2, (this.last.turn - this.baseline.turn) / .2];
      if (feature.every(value => Number.isFinite(value))) this.capture.frames.push(feature);
    } catch {
      this.update('本机视觉处理暂停，请关闭后重试。');
    } finally {
      this.ticking = false;
    }
  }
  finishExperimentSample() {
    const capture = this.capture;
    this.capture = null;
    this.sampleToken = (this.sampleToken || 0) + 1;
    clearTimeout(this.sampleTimer);
    if (!capture) return;
    if (capture.frames.length < 6) {
      this.noteExperiment({event: 'sample_failed', sample_label: capture.label, reason: '4 秒内有效帧不足 6', face_count: 1, shoulder_valid: false, visibility_status: 'short_sample'});
      this.update('采集失败：4 秒内没有 6 帧有效画面。这次没有保存，可以重新采集。');
    } else {
      const frames = capture.frames.slice(0, 8);
      try {
        this.model.train(capture.label, frames);
        this.noteExperiment({event: 'sample', sample_label: capture.label, sample_frames: frames.map(frame => frame.slice()), face_count: 1, shoulder_valid: true, visibility_status: 'ok'});
        this.update('已记录本人的动作样例。三类都满 6 帧后才会锁定。');
      } catch (error) {
        this.noteExperiment({event: 'sample_failed', sample_label: capture.label, reason: error.message, face_count: 1, shoulder_valid: false, visibility_status: 'short_sample'});
        this.update(error.message);
      }
    }
    if (this.stream) this.arm(performance.now());
  }
  invalidate() {
    this.streak = 0;
    this.mouthStreak = 0;
    this.previous = null;
    this.frames = [];
    this.window.clear();
    this.capture = null;
    this.last = null;
    this.onSignal(null);
  }
  observe(now) {
    if (this.capture && this.capture.experiment) return;
    const f = [(this.last.scale/this.baseline.scale-1)/.18, (this.last.mouth-this.baseline.mouth)/.12,
      (this.last.pitch-this.baseline.pitch)/.2, (this.last.turn-this.baseline.turn)/.2];
    this.window.push(f);
    if (this.capture) {
      this.capture.frames.push(f);
      if (now-this.capture.started >= 2000) {
        try {
          this.model.train(this.capture.label, this.capture.frames);
          this.noteExperiment({event: 'sample', sample_label: this.capture.label, sample_frames: this.capture.frames.map(frame => frame.slice()), face_count: 1, shoulder_valid: true, visibility_status: 'ok'});
          this.update('已记录本人的动作样例。三类样例齐全后开始匹配。');
        }
        catch(error) {this.update(error.message);}
        this.capture = null;
      }
      return;
    }
    const ready = this.window.ready();
    const frames = ready ? this.window.ordered() : null;
    const label = frames ? this.model.predict(frames) : null;
    const ranked = frames && typeof this.model.rank === 'function' ? this.model.rank(frames) : null;
    const outcome = this.schedule.observe({
      now, elapsed: this.lastElapsed || 0, faces: 1, shoulders: true, mouth: f[1],
      predict: label, ready, candidates: ranked
    });
    const notice = outcome.notice;
    this.kind = notice || label || 'unknown';
    this.confident = Boolean(notice) && now >= this.suppressedUntil;
    this.noteExperiment({
      face_count: 1, shoulder_valid: true, visibility_status: 'ok', feature_vector: f.slice(),
      notice: notice || null
    });
    if (notice === 'speak') this.update('短时复核得到准备发言，已提交一条不点名提醒。');
    else if (notice === 'wait') this.update('短时复核得到希望等待，已提交一条不点名提醒。');
    else if (label === 'listen') this.update(`观察 ${this.schedule.level}。更接近自然聆听，不会提醒主持人。`);
    else this.update(`观察 ${this.schedule.level}。结果不确定时不会提醒主持人。`);
    this.onSignal(this.view());
  }
  view() {
    return {kind: this.kind, confident: this.confident, summary: (this.message || '').slice(0, 80), bars: this.bars};
  }
  suppress(ms) {
    this.suppressedUntil = performance.now() + ms;
    this.confident = false;
    this.onSignal(this.view());
  }
  stop() {
    this.timerToken += 1;
    this.sampleToken = (this.sampleToken || 0) + 1;
    this.ticking = false;
    clearTimeout(this.timer);
    clearTimeout(this.sampleTimer);
    this.timer = null;
    this.stream?.getTracks().forEach(t => t.stop());
    this.stream = null;
    if (this.video) this.video.srcObject = null;
    this.face?.close();
    this.pose?.close();
    this.face = null;
    this.pose = null;
    this.last = null;
    this.baseline = null;
    this.model = new TemporalActionModel();
    this.window.clear();
    this.frames = [];
    this.capture = null;
    this.kind = null;
    this.confident = false;
    this.bars = null;
    this.schedule = new SenseSchedule();
    this.bindSchedule();
    this.gaps = [];
    this.plan = [];
    this.lastTick = 0;
    this.seenCaptions = new Set();
    this.captionsReady = false;
    this.onSignal(null);
    this.update('摄像头已关闭，短时动作缓存已清除。');
  }
}
