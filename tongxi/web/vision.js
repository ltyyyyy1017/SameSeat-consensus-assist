// 视觉特征与视频仅保留在当前端，不含服务端请求。
import {PersonalActionModel} from '/assets/vision-policy.js';
export class PrivateVision {
  constructor(notify, onHint = () => {}) {
    this.notify = notify;
    this.onHint = onHint;
    this.model = new PersonalActionModel();
    this.capture = null;
    this.streak = 0;
    this.previous = null;
    this.lastHint = 0;
    this.stream = null;
    this.face = null;
    this.pose = null;
    this.timer = null;
    this.baseline = null;
    this.last = null;
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
  tick() {
    if (!this.stream) return;
    try {
      const t = performance.now(),
        faces = this.face.detectForVideo(this.video, t).faceLandmarks;
      const poses = this.pose.detectForVideo(this.video, t).landmarks;
      if (faces.length !== 1 || !poses.length) {
        this.streak = 0;
        this.update('当前画面无法可靠取得本人动作，暂不判断。');
      } else {
        const f = faces[0],
          p = poses[0],
          width = Math.hypot(p[11].x - p[12].x, p[11].y - p[12].y);
        if (width < .08 || Math.min(p[11].visibility, p[12].visibility) < .5) {
          this.update('肩部出框或遮挡，请调整机位。');
        } else {
          const mouth = Math.hypot(f[13].x - f[14].x, f[13].y - f[14].y) / Math.max(.01, Math.hypot(f[33].x - f[263].x, f[33].y - f[263].y));
          this.last = {
            scale: width,
            mouth,
            pitch: (f[1].y - (f[33].y + f[263].y)/2) / Math.max(.01, Math.abs(f[33].x-f[263].x)),
            turn: (f[1].x - (f[33].x + f[263].x)/2) / Math.max(.01, Math.abs(f[33].x-f[263].x))
          };
          if (this.baseline) {
            this.observe(t);
          }
        }
      }
    } catch {
      this.update('本机视觉处理暂停，请关闭后重试。');
      this.stop();
      return;
    }
    this.timer = setTimeout(() => this.tick(), 250);
  }
  calibrate() {
    if (!this.last) {
      this.update('请让面部与双肩完整入框后再校准。');
      return;
    }
    this.baseline = {
      ...this.last
    };
    this.update('自然坐姿已校准，开始比较相对动作。');
  }
  record(label) {
    if (!this.baseline) {this.update('请先校准自然坐姿，再记录动作示例。');return;}
    this.capture = {label, frames: [], started: performance.now()};
    this.update('请保持你希望表达的动作两秒，样例只留在本机本次会话。');
  }
  observe(now) {
    const f = [(this.last.scale/this.baseline.scale-1)/.18, (this.last.mouth-this.baseline.mouth)/.12,
      (this.last.pitch-this.baseline.pitch)/.2, (this.last.turn-this.baseline.turn)/.2];
    if (this.capture) {
      this.capture.frames.push(f);
      if (now-this.capture.started >= 2000) {
        try {this.model.train(this.capture.label,this.capture.frames);this.update('已记录本人的动作样例。三类样例齐全后开始匹配。');}
        catch(error) {this.update(error.message);}
        this.capture = null;
      }
      return;
    }
    const label = this.model.predict(f);
    this.streak = label && label === this.previous ? this.streak+1 : 0;
    this.previous = label;
    const count = Object.keys(this.model.samples).length;
    this.update(count<3?`已记录 ${count}/3 类动作。请分别记录自然聆听、准备发言和希望等待的示例。`:
      label?`当前动作接近本人的“${{listen:'自然聆听',speak:'准备发言',wait:'希望等待'}[label]}”示例。`:'当前动作不明确，暂不建议。');
    if (label && label !== 'listen' && this.streak >= 3 && now-this.lastHint > 12000) {
      this.lastHint = now;
      this.onHint(label === 'wait' ? 'wait' : 'speak');
    }
  }
  stop() {
    clearTimeout(this.timer);
    this.stream?.getTracks().forEach(t => t.stop());
    this.stream = null;
    if (this.video) this.video.srcObject = null;
    this.face?.close();
    this.pose?.close();
    this.face = null;
    this.pose = null;
    this.last = null;
    this.baseline = null;
    this.model = new PersonalActionModel();
    this.capture = null;
    this.onHint(null);
    this.update('摄像头已关闭，短时动作缓存已清除。');
  }
}
