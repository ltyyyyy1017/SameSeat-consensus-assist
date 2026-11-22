"""上下文改变采样等级后，取消旧的 setTimeout，并按最近一次采样重排。

断言等待的是浏览器真实定时器，不靠再次手动调用 tick 来代替下一次采样。
"""
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
      const visionModule = await import(visionUrl);
      const failures = [];
      const check = (name, ok) => { if (!ok) failures.push(name); };
      const pending = new Set();
      const originalSet = window.setTimeout;
      const originalClear = window.clearTimeout;
      let marking = false;
      const marked = [];
      window.setTimeout = (fn, ms, ...rest) => {
        const id = originalSet(() => {
          pending.delete(id);
          fn();
        }, ms, ...rest);
        pending.add(id);
        if (marking) marked.push(id);
        return id;
      };
      window.clearTimeout = id => {
        pending.delete(id);
        originalClear(id);
      };
      const face = Array.from({length: 468}, () => ({x: 0.5, y: 0.5}));
      face[13] = {x: 0.48, y: 0.6};
      face[14] = {x: 0.50, y: 0.6};
      face[33] = {x: 0.4, y: 0.45};
      face[263] = {x: 0.6, y: 0.45};
      face[1] = {x: 0.5, y: 0.5};
      const pose = Array.from({length: 33}, () => ({x: 0.5, y: 0.5, visibility: 1}));
      pose[11] = {x: 0.3, y: 0.7, visibility: 1};
      pose[12] = {x: 0.7, y: 0.7, visibility: 1};
      const boot = () => {
        const vision = new visionModule.PrivateVision(() => {});
        vision.stream = {getTracks() { return [{stop() {}}]; }};
        vision.video = {};
        vision.face = {detectForVideo() { return {faceLandmarks: [face]}; }, close() {}};
        vision.pose = {detectForVideo() { return {landmarks: [pose]}; }, close() {}};
        vision.tick();
        vision.baseline = {...vision.last};
        return vision;
      };
      const nextGap = async (vision, timeout) => {
        const before = vision.gaps.length;
        const started = performance.now();
        while (performance.now() - started < timeout) {
          if (vision.gaps.length > before) return vision.gaps.at(-1);
          await new Promise(resolve => originalSet(resolve, 15));
        }
        return null;
      };

      const first = boot();
      first.setContext({decision: false, stableIds: ['s0']});
      first.setContext({decision: true, stableIds: ['s0']});
      const r01 = await nextGap(first, 700);
      check('r01-l1-to-l2', r01 !== null && r01 >= 180 && r01 <= 450 && first.schedule.level === 'L2');
      first.stop();

      const second = boot();
      second.setContext({decision: true, stableIds: ['a']});
      const entered = await nextGap(second, 700);
      second.setContext({decision: false, stableIds: ['a', 'b', 'c']});
      const r02 = await nextGap(second, 1500);
      check('r02-l2-to-l1', entered !== null && entered <= 450 && r02 !== null && r02 >= 800 && r02 <= 1400 && second.schedule.level === 'L1');
      second.stop();

      const third = boot();
      third.schedule.enter('L3', performance.now(), 'mouth');
      const r03 = await nextGap(third, 400);
      check('r03-l1-to-l3', r03 !== null && r03 >= 60 && r03 <= 180 && third.schedule.level === 'L3');
      third.stop();

      const fourth = boot();
      fourth.schedule.setDecision(true, performance.now());
      fourth.schedule.enter('L3', performance.now(), 'mouth');
      const l3 = await nextGap(fourth, 400);
      fourth.schedule.finish(performance.now(), 'confirmed');
      const r04 = await nextGap(fourth, 700);
      check('r04-l3-to-l2', l3 !== null && l3 <= 180 && r04 !== null && r04 >= 180 && r04 <= 450 && fourth.schedule.level === 'L2');
      fourth.stop();

      const fifth = boot();
      marking = true;
      for (let i = 0; i < 8; i += 1) {
        fifth.setContext({decision: i % 2 === 0, stableIds: i % 2 === 0 ? ['a'] : ['a', `b${i}`, `c${i}`]});
      }
      marking = false;
      const alive = marked.filter(id => pending.has(id));
      check('r05-single-timer', alive.length === 1 && alive[0] === fifth.timer);
      fifth.stop();

      const sixth = boot();
      const beforeStop = sixth.gaps.length;
      sixth.stop();
      marking = true;
      const markedBefore = marked.length;
      sixth.setContext({decision: true, stableIds: ['off', 'off2']});
      marking = false;
      await new Promise(resolve => originalSet(resolve, 400));
      const restarted = marked.slice(markedBefore).filter(id => pending.has(id));
      check('r06-stopped', sixth.timer === null && sixth.stream === null && sixth.gaps.length === 0 && beforeStop >= 0 && restarted.length === 0);

      const seventh = boot();
      let depth = 0;
      let maxDepth = 0;
      seventh.face.detectForVideo = () => {
        depth += 1;
        maxDepth = Math.max(maxDepth, depth);
        seventh.schedule.enter(seventh.schedule.level === 'L3' ? 'L2' : 'L3', performance.now(), 'during-frame');
        const began = performance.now();
        while (performance.now() - began < 40) {}
        depth -= 1;
        return {faceLandmarks: [face]};
      };
      seventh.schedule.enter('L3', performance.now(), 'mouth');
      const during = await nextGap(seventh, 400);
      check('r07-no-parallel', during !== null && maxDepth === 1 && seventh.timer !== null);
      seventh.stop();

      window.setTimeout = originalSet;
      window.clearTimeout = originalClear;
      return {failures, measured: {r01, entered, r02, r03, l3, r04, alive: alive.length, maxDepth}};
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


def test_context_change_reschedules_the_pending_timer():
    result = run_js()
    assert result["failures"] == [], result
