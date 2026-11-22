"""样例距离机制：三类模板分开时能返回标签，分不开或越界时保持不确定。

这里用数值序列检查现有阈值，不代表真人识别准确率。
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

POLICY = (Path(__file__).resolve().parents[1] / "tongxi/web/vision-policy.js").read_text(encoding="utf-8")


def run_js():
    script = r"""
    async ({policy}) => {
      const url = URL.createObjectURL(new Blob([policy], {type: 'text/javascript'}));
      const policyModule = await import(url);
      const failures = [];
      const check = (name, ok) => { if (!ok) failures.push(name); };
      const model = new policyModule.TemporalActionModel();
      const still = value => Array.from({length: 6}, () => [value, 0, 0, 0]);
      model.train('listen', still(0));
      model.train('speak', still(2));
      model.train('wait', still(0).map(frame => [0, 2, 0, 0]));
      check('listen-output', model.predict(still(0)) === 'listen');
      check('speak-output', model.predict(still(2)) === 'speak');
      check('wait-output', model.predict(still(0).map(frame => [0, 2, 0, 0])) === 'wait');
      check('midpoint-uncertain', model.predict(still(1)) === null);
      check('far-uncertain', model.predict(still(9)) === null);
      check('short-uncertain', model.predict([[0, 0, 0, 0]]) === null);
      const jumped = [[0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [5, 0, 0, 0]];
      check('span-uncertain', model.predict(jumped) === null);
      const window = new policyModule.FeatureWindow();
      [[1, 0, 0, 0], [1, 0, 0, 0], [1, 0, 0, 0]].forEach(frame => window.push(frame));
      window.clear();
      window.push([2, 0, 0, 0]);
      check('invalid-not-stitched', JSON.stringify(window.ordered()) === JSON.stringify([[2, 0, 0, 0]]));
      return failures;
    }
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page()
        page.goto("about:blank")
        failures = page.evaluate(script, {"policy": POLICY})
        browser.close()
    return failures


def test_three_templates_return_labels_and_uncertain_stays_null():
    assert run_js() == []
