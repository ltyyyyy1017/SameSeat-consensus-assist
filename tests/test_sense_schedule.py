"""L1/L2/L3 调度与真实采样间隔。

间隔数字来自浏览器 setTimeout 的实测，不是只读取常量。
这里不宣称真人三类识别已经准确。
"""
import statistics
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
POLICY = (ROOT / "tongxi/web/vision-policy.js").read_text(encoding="utf-8")
VISION = (ROOT / "tongxi/web/vision.js").read_text(encoding="utf-8")
APP = (ROOT / "tongxi/web/app.js").read_text(encoding="utf-8")


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
      const frame = (extra) => Object.assign({
        now: 0, elapsed: 20, faces: 1, shoulders: true, mouth: 0,
        predict: null, ready: false, candidates: null
      }, extra);

      const schedule = new policyModule.SenseSchedule();
      check('b02-starts-l1', schedule.level === 'L1' && schedule.interval() === 1000);
      schedule.setDecision(false, 1);
      check('b04-context-alone', schedule.level === 'L1' && schedule.observe(frame({now: 2})).notice === null);
      schedule.setDecision(true, 3);
      check('b01-decision-l2', schedule.level === 'L2' && schedule.interval() === 250);
      schedule.noteCaption(false, 4);
      check('b03-one-caption-stays', schedule.level === 'L2');
      schedule.noteCaption(false, 5);
      check('b03-two-captions-l1', schedule.level === 'L1' && schedule.events.at(-1).reason === 'two-captions');
      schedule.noteCaption(true, 6);
      check('b01-caption-returns-l2', schedule.level === 'L2');

      const mouth = new policyModule.SenseSchedule();
      mouth.observe(frame({now: 10, mouth: 0.6}));
      check('b05-one-mouth-stays', mouth.level === 'L1' && mouth.observe(frame({now: 11, mouth: 0.2})).notice === null);
      mouth.observe(frame({now: 12, mouth: 0.6}));
      mouth.observe(frame({now: 13, mouth: 0.6}));
      check('b05-mouth-l3', mouth.level === 'L3' && mouth.interval() === 100 && mouth.events.at(-1).reason === 'mouth');

      const near = new policyModule.SenseSchedule();
      near.setDecision(true, 20);
      near.observe(frame({now: 21, candidates: [{label: 'listen', distance: 0.2}]}));
      check('b06-listen-not-l3', near.level === 'L2');
      near.observe(frame({now: 22, candidates: [{label: 'speak', distance: 1.01}]}));
      check('b06-far-not-l3', near.level === 'L2');
      near.observe(frame({now: 23, candidates: null}));
      check('b16-no-samples', near.level === 'L2' && near.observe(frame({now: 24})).notice === null);
      near.observe(frame({now: 25, candidates: [{label: 'wait', distance: 0.4}]}));
      check('b06-candidate-l3', near.level === 'L3' && near.events.at(-1).reason === 'candidate');

      const unsure = new policyModule.SenseSchedule();
      unsure.enter('L3', 30, 'test');
      for (let i = 0; i < 6; i += 1) unsure.observe(frame({now: 30 + i * 100, predict: null, ready: true}));
      check('b08-uncertain-no-notice', unsure.level === 'L3');
      const blocked = unsure.observe(frame({now: 900, faces: 2, predict: 'speak', ready: true}));
      check('b13-multiple-faces', blocked.notice === null && blocked.unknown && unsure.level === 'L1');

      const lost = new policyModule.SenseSchedule();
      lost.enter('L3', 40, 'test');
      const hidden = lost.observe(frame({now: 50, shoulders: false, predict: 'speak', ready: true}));
      check('b14-shoulders', hidden.notice === null && hidden.unknown && lost.level === 'L1');

      const slow = new policyModule.SenseSchedule();
      slow.enter('L3', 60, 'test');
      const exited = slow.observe(frame({now: 80, elapsed: 230, predict: 'speak', ready: true}));
      check('b15-slow-exits', exited.notice === null && slow.level === 'L1' && slow.events.at(-1).reason === 'slow');

      const late = new policyModule.SenseSchedule();
      late.enter('L3', 100, 'test');
      const timeout = late.observe(frame({now: 2100, predict: 'speak', ready: true}));
      check('b15-timeout', timeout.notice === null && late.level === 'L1' && late.events.at(-1).reason === 'timeout');

      const confirmed = new policyModule.SenseSchedule();
      confirmed.setDecision(true, 200);
      confirmed.enter('L3', 200, 'test');
      let notice = null;
      for (let i = 0; i < 4; i += 1) notice = confirmed.observe(frame({now: 200 + i * 100, predict: 'speak', ready: true}));
      check('b07-l3-notice', notice.notice === 'speak' && confirmed.level === 'L2');
      const early = new policyModule.SenseSchedule();
      early.enter('L3', 300, 'test');
      let premature = null;
      for (let i = 0; i < 4; i += 1) premature = early.observe(frame({now: 300 + i * 100, predict: 'wait', ready: false}));
      check('b07-needs-six-frames', premature.notice === null && early.level === 'L3');
      const listen = new policyModule.SenseSchedule();
      listen.enter('L3', 400, 'test');
      let listening = null;
      for (let i = 0; i < 4; i += 1) listening = listen.observe(frame({now: 400 + i * 100, predict: 'listen', ready: true}));
      check('b07-listen-not-notice', listening.notice === null);

      const model = new policyModule.TemporalActionModel();
      check('b16-rank-without-samples', model.rank(Array.from({length: 6}, () => [0, 0, 0, 0])) === null);
      const still = value => Array.from({length: 6}, () => [value, 0, 0, 0]);
      model.train('listen', still(0));
      model.train('speak', still(0.2));
      model.train('wait', still(0).map(row => [0, 0.2, 0, 0]));
      const ranked = model.rank(still(0.2));
      check('candidate-does-not-lower-threshold', ranked && ranked[0].label === 'speak' && model.predict(still(0.2)) === null);

      const separated = new policyModule.TemporalActionModel();
      separated.train('listen', still(0));
      separated.train('speak', still(2));
      separated.train('wait', still(0).map(row => [0, 2, 0, 0]));
      check('existing-threshold-still-labels', separated.predict(still(2)) === 'speak');

      const face = Array.from({length: 468}, () => ({x: 0.5, y: 0.5}));
      face[13] = {x: 0.48, y: 0.6};
      face[14] = {x: 0.50, y: 0.6};
      face[33] = {x: 0.4, y: 0.45};
      face[263] = {x: 0.6, y: 0.45};
      face[1] = {x: 0.5, y: 0.5};
      const pose = Array.from({length: 33}, () => ({x: 0.5, y: 0.5, visibility: 1}));
      pose[11] = {x: 0.3, y: 0.7, visibility: 1};
      pose[12] = {x: 0.7, y: 0.7, visibility: 1};
      const vision = new visionModule.PrivateVision(() => {});
      vision.stream = {getTracks() { return [{stop() {}}]; }};
      vision.video = {};
      vision.face = {detectForVideo() { return {faceLandmarks: [face]}; }, close() {}};
      vision.pose = {detectForVideo() { return {landmarks: [pose]}; }, close() {}};
      const collect = async (ms) => {
        const from = vision.gaps.length;
        const plans = vision.plan.length;
        await new Promise(resolve => setTimeout(resolve, ms));
        return {gaps: vision.gaps.slice(from), plans: vision.plan.slice(plans)};
      };
      vision.tick();
      vision.baseline = {...vision.last};
      clearTimeout(vision.timer);
      vision.gaps = [];
      vision.plan = [];
      vision.lastTick = 0;
      vision.tick();
      const l1 = await collect(2300);
      clearTimeout(vision.timer);
      vision.setContext({decision: true, stableIds: []});
      vision.tick();
      const l2 = await collect(900);
      clearTimeout(vision.timer);
      const l3Began = performance.now();
      vision.schedule.enter('L3', l3Began, 'measure');
      vision.tick();
      const l3 = await collect(550);
      const l3Span = performance.now() - l3Began;
      clearTimeout(vision.timer);
      vision.schedule.enter('L3', performance.now(), 'slow-frame');
      vision.face.detectForVideo = () => {
        const began = performance.now();
        while (performance.now() - began < 230) {}
        return {faceLandmarks: [face]};
      };
      vision.tick();
      check('measured-slow-exits-l3', vision.schedule.level !== 'L3' && vision.confident !== true);
      vision.stop();
      return {failures, measured: {
        l1: {set: 1000, gaps: l1.gaps, plans: l1.plans},
        l2: {set: 250, gaps: l2.gaps, plans: l2.plans},
        l3: {set: 100, gaps: l3.gaps, plans: l3.plans, span: Math.round(l3Span)}
      }};
    }
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel="msedge",
            headless=True,
            args=["--disable-background-timer-throttling", "--disable-renderer-backgrounding"],
        )
        page = browser.new_page()
        page.goto("about:blank")
        result = page.evaluate(script, {"policy": POLICY, "visionSource": VISION})
        browser.close()
    return result


def within(gaps, low, high):
    return bool(gaps) and all(low <= gap <= high for gap in gaps)


def test_context_schedules_sensing_without_creating_risk_by_itself():
    assert "phase === 'seeking_response' || phase === 'closing'" in APP
    assert "current?.status === 'preparing'" in APP
    assert "当前议题可能有人还没来得及回应。" in APP
    assert "当前议题已保留回应机会。" in APP
    assert "data-host-notice" in APP
    assert "data-notice-action=\"ignore\"" in APP
    assert "风险分数" not in APP
    result = run_js()
    assert result["failures"] == [], result
    measured = result["measured"]
    assert within(measured["l1"]["gaps"], 800, 1400), measured
    assert within(measured["l2"]["gaps"], 180, 400), measured
    assert within(measured["l3"]["gaps"], 60, 170), measured
    assert measured["l3"]["span"] <= 2000, measured
    assert set(measured["l1"]["plans"]) == {1000}
    assert set(measured["l2"]["plans"]) == {250}
    assert set(measured["l3"]["plans"]) == {100}
    assert statistics.median(measured["l1"]["gaps"])
    assert statistics.median(measured["l2"]["gaps"])
    assert statistics.median(measured["l3"]["gaps"])
