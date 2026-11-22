"""时序样例窗口：不满 6 帧不预测，无效帧不与旧窗口拼接。"""
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
POLICY = (ROOT / "tongxi/web/vision-policy.js").read_text(encoding="utf-8")
VISION = (ROOT / "tongxi/web/vision.js").read_text(encoding="utf-8")


def run_js():
    script = r"""
    async ({policy, visionSource}) => {
      const policyUrl = URL.createObjectURL(new Blob([policy], {type: 'text/javascript'}));
      const visionUrl = URL.createObjectURL(new Blob([
        visionSource.replace("from '/assets/vision-policy.js'", `from '${policyUrl}'`)
      ], {type: 'text/javascript'}));
      const policyModule = await import(policyUrl);
      const visionModule = await import(visionUrl);
      const failures = [];
      const check = (name, ok) => { if (!ok) failures.push(name); };

      const five = Array.from({length: 5}, (_, i) => [i, 0, 0, 0]);
      check('five-not-ready', new policyModule.FeatureWindow().ready === undefined || (() => {
        const window = new policyModule.FeatureWindow();
        five.forEach(frame => window.push(frame));
        return window.ready() === false && window.ordered().length === 5;
      })());
      const model = new policyModule.TemporalActionModel();
      check('five-no-descriptor', model.describe(five) === null);

      const six = Array.from({length: 6}, (_, i) => [i / 10, 0, 0, 0]);
      const described = model.describe(six);
      check('six-enters-describe', Array.isArray(described) && described.length === 8);
      check('six-predict-called', model.predict(six) === null);

      const sliding = new policyModule.FeatureWindow();
      Array.from({length: 10}, (_, i) => [i, 1, 2, 3]).forEach(frame => sliding.push(frame));
      const ordered = sliding.ordered().map(frame => frame[0]);
      check('window-order', JSON.stringify(ordered) === JSON.stringify([2, 3, 4, 5, 6, 7, 8, 9]));

      const broken = new policyModule.FeatureWindow();
      [[1, 0, 0, 0], [2, 0, 0, 0], [3, 0, 0, 0], [4, 0, 0, 0]].forEach(frame => broken.push(frame));
      broken.clear();
      [[8, 0, 0, 0], [9, 0, 0, 0]].forEach(frame => broken.push(frame));
      check('invalid-not-stitched', JSON.stringify(broken.ordered()) === JSON.stringify([[8, 0, 0, 0], [9, 0, 0, 0]]));

      const vision = new visionModule.PrivateVision(() => {});
      vision.baseline = {scale: 1, mouth: 0.2, pitch: 0, turn: 0};
      vision.last = {scale: 1.05, mouth: 0.22, pitch: 0.01, turn: 0.01};
      const seen = [];
      vision.model.predict = frames => { seen.push(frames.map(frame => frame.slice())); return null; };
      for (let i = 0; i < 5; i += 1) vision.observe(1000 + i);
      check('vision-under-six', seen.length === 0 && vision.window.ordered().length === 5);
      vision.observe(1006);
      check('vision-sixth', seen.length === 1 && seen[0].length === 6);

      vision.invalidate();
      check('invalidate-clears', vision.window.ordered().length === 0 && vision.capture === null);
      vision.baseline = {scale: 1, mouth: 0.2, pitch: 0, turn: 0};
      vision.last = {scale: 1.05, mouth: 0.22, pitch: 0.01, turn: 0.01};
      vision.observe(2000);
      vision.observe(2001);
      check('after-gap', seen.length === 1 && vision.window.ordered().length === 2);

      vision.last = {scale: 1, mouth: 0.2, pitch: 0, turn: 0};
      vision.calibrate();
      check('calibrate-restarts', vision.window.ordered().length === 0);
      vision.observe(3000);
      check('calibrate-new-data', vision.window.ordered().length === 1);

      vision.observe(3001);
      vision.stop();
      check('stop-clears', vision.window.ordered().length === 0 && vision.baseline === null);
      return failures;
    }
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page()
        page.goto("about:blank")
        failures = page.evaluate(script, {"policy": POLICY, "visionSource": VISION})
        browser.close()
    return failures


def test_temporal_window_does_not_predict_before_six_frames():
    failures = run_js()
    assert failures == []
