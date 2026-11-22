// 视觉特征与视频仅保留在当前端，不含服务端请求。
import {FeatureWindow, SenseSchedule, TemporalActionModel} from '/assets/vision-policy.js';
export class PrivateVision {
  constructor(notify, onSignal = () => {}) {
    this.notify = notify;
    this.onSignal = onSignal;
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
        this.update(faces.length > 1 ? '多人同框，暂不判断。' : '当前画面无法可靠取得本人动作，暂不判断。');
      } else {
        const f = faces[0],
          p = poses[0],
          width = Math.hypot(p[11].x - p[12].x, p[11].y - p[12].y);
        if (width < .08 || Math.min(p[11].visibility, p[12].visibility) < .5) {
          this.invalidate();
          this.schedule.observe({now: started, elapsed, faces: 1, shoulders: false, mouth: 0, predict: null, ready: false, candidates: null});
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
    this.capture = null;
    this.streak = 0;
    this.previous = null;
    this.mouthStreak = 0;
    this.onSignal(null);
    this.update('自然坐姿已校准，开始比较相对动作。');
  }
  record(label) {
    if (!this.baseline) {this.update('请先校准自然坐姿，再记录动作示例。');return;}
    this.capture = {label, frames: [], started: performance.now()};
    this.update('请保持你希望表达的动作两秒，样例只留在本机本次会话。');
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
    const f = [(this.last.scale/this.baseline.scale-1)/.18, (this.last.mouth-this.baseline.mouth)/.12,
      (this.last.pitch-this.baseline.pitch)/.2, (this.last.turn-this.baseline.turn)/.2];
    this.window.push(f);
    if (this.capture) {
      this.capture.frames.push(f);
      if (now-this.capture.started >= 2000) {
        try {this.model.train(this.capture.label,this.capture.frames);this.update('已记录本人的动作样例。三类样例齐全后开始匹配。');}
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
    this.ticking = false;
    clearTimeout(this.timer);
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
