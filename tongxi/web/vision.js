// 视觉特征与视频仅保留在当前端，不含服务端请求。
export class PrivateVision {
  constructor(notify) {
    this.notify = notify;
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
            mouth
          };
          if (this.baseline) {
            const delta = (width / this.baseline.scale - 1) * 100;
            this.update(`相对肩宽变化 ${delta.toFixed(0)}% · 口部开合 ${(mouth*100).toFixed(0)}。${delta>18?'检测到靠近镜头，可在准备好后主动请求发言。':'当前仅显示动作特征，不判断真实意图。'}`);
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
    this.update('摄像头已关闭，短时动作缓存已清除。');
  }
}
