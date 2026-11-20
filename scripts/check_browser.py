"""使用独立无痕上下文验证完整 UI，绝不读取用户浏览器配置。"""
import json
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.getenv("TONGXI_URL", "http://127.0.0.1:8765")
OUT = Path(__file__).resolve().parents[1] / "artifacts"


def enter(context, room, token, role):
    page = context.new_page()
    page.goto(f'{BASE}/room/{room}#token={token}&role={role}')
    page.get_by_text('实时同步' if role == 'board' else '已连接', exact=True).wait_for()
    return page


def main():
    OUT.mkdir(exist_ok=True)
    errors = []
    external_requests = []
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tongxi.adapters import LocalTTS
    sample_path = OUT / 'synthetic-microphone.wav'
    sample_path.write_bytes(LocalTTS().synthesize('test', {"id": "sample", "rev": 1, "text": "方案需要实验室设备。"}))
    with sync_playwright() as p:
        chrome = Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) / 'Google/Chrome/Application/chrome.exe'
        browser = p.chromium.launch(executable_path=str(chrome) if chrome.is_file() else None, headless=True,
                args=['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream',
                      '--use-file-for-fake-audio-capture=' + str(sample_path)])
        context = browser.new_context(viewport={"width": 1440, "height": 1040})
        context.on('page', lambda page: page.on('pageerror', lambda error: errors.append(str(error))))
        context.on('request', lambda request: external_requests.append(request.url)
                   if not request.url.startswith(BASE) and not request.url.startswith('blob:') else None)
        host = context.new_page()
        host.goto(BASE)
        host.screenshot(path=str(OUT / '首页.png'), full_page=True)
        host.get_by_role('button', name='体验示例讨论').click()
        host.get_by_role('button', name='创建并进入').click()
        host.get_by_text('已连接', exact=True).wait_for()
        host.get_by_role('heading', name='讨论概览').wait_for()
        host.screenshot(path=str(OUT / '主持人概览.png'), full_page=True)
        session = host.evaluate("JSON.parse(sessionStorage.getItem('tongxi:'+location.pathname.split('/').pop()))")
        room = session['room']
        auth = {"Authorization": 'Bearer ' + session['token']}
        snap = context.request.get(f'{BASE}/api/v1/rooms/{room}/snapshot', headers=auth).json()
        joined = context.request.post(f'{BASE}/api/v1/join', data={"invite": snap['invite'], "name": "小林"}).json()
        subtitle = enter(context, room, joined['token'], 'member')
        other = context.request.post(f'{BASE}/api/v1/join', data={"invite": snap['invite'], "name": "小陈"}).json()
        fourth = enter(context, room, other['token'], 'member')
        board = enter(context, room, snap['board_token'], 'board')
        # 授权与回看均由真实 UI 操作，服务端实际匹配决定依据。
        subtitle.get_by_role('button', name='我的授权', exact=True).click()
        subtitle.get_by_label('我主动回看相关原句时，替我留位', exact=False).check()
        subtitle.get_by_role('button', name='保存授权').click()
        subtitle.get_by_role('button', name='实时讨论', exact=True).click()
        subtitle.locator('.segment').nth(1).get_by_role('button', name='回看这句').click()
        host.get_by_role('button', name='准备定案', exact=True).click()
        host.get_by_role('button', name='确认依据，准备定案').click()
        board.get_by_text('待回应', exact=True).wait_for()
        subtitle.screenshot(path=str(OUT / '成员字幕端.png'), full_page=True)
        board.screenshot(path=str(OUT / '公共议题屏.png'), full_page=True)
        # 通过真实意见输入与原议题推荐，再交由主持人修改决定。
        subtitle.get_by_role('button', name='写一条补充意见').click()
        subtitle.get_by_label('你想补充什么').fill('周六实验室设备借不到，我建议改成周日。')
        with subtitle.expect_response('**/matches', timeout=90000) as match_response:
            subtitle.get_by_role('button', name='根据内容查找原议题').click()
        match_payload = match_response.value.json()
        (OUT / 'browser-semantic-match.json').write_text(json.dumps(match_payload, ensure_ascii=False, indent=2), encoding='utf-8')
        assert match_response.value.status == 200 and match_payload.get('items'), match_payload
        subtitle.get_by_role('button', name='校园活动方案 B', exact=True).click()
        subtitle.get_by_role('button', name='提交自己的意见').click()
        subtitle.get_by_text('周六实验室设备借不到，我建议改成周日。', exact=True).wait_for()
        host.get_by_role('button', name='回应工作台', exact=False).click()
        host.get_by_text('周六实验室设备借不到，我建议改成周日。', exact=True).wait_for()
        host.get_by_role('button', name='处理关联议题').click()
        host.get_by_label('如何处理').select_option('modify')
        host.get_by_label('修改后的结论', exact=False).fill('周日采用方案 B 开展活动')
        host.get_by_label('处理理由／回应说明').fill('采纳设备时间建议，改到周日。')
        host.get_by_label('若仍有未回应事项', exact=False).check()
        host.get_by_role('button', name='记录处理结果').click()
        board.get_by_text('已确认', exact=True).wait_for()
        board.get_by_text('周日采用方案 B 开展活动', exact=True).wait_for()
        board.screenshot(path=str(OUT / '公共屏最终结项.png'), full_page=True)
        subtitle.get_by_text('修改决定 · 采纳设备时间建议，改到周日。', exact=True).wait_for()
        host.screenshot(path=str(OUT / '意见处理工作台.png'), full_page=True)
        # 全部主要路由都实际打开，确认存在且无浏览器脚本错误。
        for label in ['实时讨论', '议题与决定', '讨论复盘', '个人与设备']:
            host.get_by_role('button', name=label, exact=True).click()
            host.locator('main h1').wait_for()
        host.get_by_role('button', name='实时讨论', exact=True).click()
        host.get_by_role('heading', name='本地 AI 候选议题', exact=True).wait_for()
        host.locator('[data-candidate]').first.wait_for(timeout=90000)
        host.screenshot(path=str(OUT / '实时讨论与AI候选.png'), full_page=True)
        # 合成麦克风只验证浏览器录音容器到本地转写接口，无需真实用户录音。
        host.get_by_role('button', name='开启本地转写', exact=True).click()
        host.get_by_role('button', name='停止录音', exact=True).wait_for()
        host.wait_for_timeout(1000)
        with host.expect_response('**/audio', timeout=60000) as audio_result:
            host.get_by_role('button', name='停止录音', exact=True).click()
        assert audio_result.value.status == 200
        host.get_by_role('button', name='个人与设备', exact=True).click()
        # 只使用浏览器合成摄像头，验证模型能初始化与退出。
        host.get_by_role('button', name='授权并开启摄像头').click()
        host.get_by_role('button', name='关闭摄像头', exact=True).wait_for(timeout=60000)
        assert host.locator('#camera-video').evaluate('video => video.srcObject.getVideoTracks()[0].readyState') == 'live'
        host.get_by_role('button', name='关闭摄像头', exact=True).click()
        host.get_by_text('摄像头已关闭，短时动作缓存已清除。', exact=True).wait_for()
        fourth.get_by_role('button', name='实时讨论', exact=True).click()
        fourth.get_by_role('button', name='开启播报', exact=True).click()
        fourth.get_by_text('播放中', exact=True).wait_for(timeout=30000)
        fourth.get_by_role('button', name='关闭播报', exact=True).click()
        mobile = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
        mobile_page = enter(mobile, room, other['token'], 'member')
        mobile_page.get_by_role('button', name='实时讨论', exact=True).click()
        mobile_page.screenshot(path=str(OUT / '手机成员端.png'), full_page=True)
        assert mobile_page.evaluate('document.documentElement.scrollWidth <= innerWidth'), '手机页面横向溢出'
        assert not errors, errors
        assert not external_requests, external_requests
        result = {"four_client_lifecycle": "passed", "mobile_overflow": "passed", "local_tts_playback": "passed",
                  "local_ai_candidate": "passed", "synthetic_microphone_upload": "passed", "external_page_requests": external_requests,
                  "local_vision_initialization_synthetic_camera": "passed", "page_errors": errors,
                  "screenshots": [x.name for x in OUT.glob('*.png')]}
        (OUT / 'browser-check.json').write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False))
        context.close()
        mobile.close()
        browser.close()


if __name__ == '__main__':
    main()
