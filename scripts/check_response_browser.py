"""隔离样例房间验证回应时间、逐条答复、追问及三端同步。"""
import json
import os
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import sync_playwright, expect

BASE = os.getenv('TONGXI_URL', 'http://127.0.0.1:8789')
OUT = Path(__file__).resolve().parents[1] / 'artifacts' / 'response-upgrade'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    errors, external = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe', headless=True,
            args=['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'])
        context = browser.new_context(viewport={'width':1440, 'height':1050})
        context.on('page', lambda page: page.on('pageerror', lambda error: errors.append(str(error))))
        context.on('request', lambda req: external.append(req.url) if not req.url.startswith((BASE,'blob:','data:')) else None)
        host_session = context.request.post(BASE+'/api/v1/rooms', data={'title':'周末校园活动 · 回应流程演示','name':'主持人'}).json()
        room = host_session['room']

        def api(session, path, data=None, method='POST'):
            return context.request.fetch(f'{BASE}/api/v1/rooms/{room}{path}', method=method, data=data,
                headers={'Authorization':'Bearer '+session['token'], 'Idempotency-Key':str(uuid4())})

        decision = api(host_session, '/demo', {}).json()
        initial = api(host_session, '/snapshot', method='GET').json()
        member_session = context.request.post(BASE+'/api/v1/join', data={'invite':initial['invite'],'name':'小林'}).json()

        def enter(session, role, target_context=context):
            page = target_context.new_page()
            page.goto(f'{BASE}/room/{room}#token={session["token"]}&role={role}')
            page.get_by_text('实时同步' if role=='board' else '已连接', exact=True).wait_for()
            return page

        host = enter(host_session, 'host')
        member = enter(member_session, 'member')
        board = enter({'token':initial['board_token']}, 'board')
        member.get_by_role('button', name='我的授权', exact=True).click()
        member.get_by_label('我主动回看相关原句时，替我留位', exact=False).check()
        member.get_by_label('关键内容送达后，至少保留多少准备时间').fill('30')
        member.get_by_role('button', name='保存授权', exact=True).click()
        member.get_by_role('button', name='实时讨论', exact=True).click()
        member.locator('.segment').nth(1).get_by_role('button', name='回看这句').click()
        host.get_by_role('button', name='准备定案', exact=True).click()
        host.get_by_role('button', name='确认依据，准备定案').click()
        board.get_by_text('待回应', exact=True).wait_for()
        member.get_by_text('相关内容尚未接收完', exact=True).wait_for()
        member.get_by_role('button', name='回到实时位置', exact=True).click()
        member.get_by_text('保留准备时间', exact=True).wait_for()
        expect(member.locator('[data-window-until]')).to_contain_text('秒准备时间')
        member.get_by_role('button', name='还需准备时间', exact=True).click()
        member.screenshot(path=str(OUT/'01-成员回应时间.png'), full_page=True)
        member.get_by_role('button', name='写一条补充意见', exact=True).click()
        member.get_by_label('你想补充什么').fill('周六实验室设备借不到，我建议改成周日。')
        member.get_by_label('它回应哪个议题').select_option(decision['id'])
        member.get_by_role('button', name='提交自己的意见', exact=True).click()
        member.get_by_text('意见已提交，等待答复', exact=True).wait_for()
        host.get_by_role('button', name='回应工作台', exact=False).click()
        host.get_by_role('button', name='逐条答复', exact=True).click()
        host.get_by_label('这条意见如何处理').select_option('partial')
        host.get_by_label('针对这条意见的答复').fill('建议调整活动日期，仍需核实实验室开放安排。')
        host.get_by_label('具体影响或采纳范围', exact=False).fill('计划将日期改为周日，另行确认预约。')
        host.get_by_role('button', name='发送这条答复', exact=True).click()
        member.get_by_text('部分采纳', exact=True).first.wait_for()
        member.get_by_role('button', name='这没有回答我的问题', exact=True).click()
        member.get_by_label('哪些问题还没有得到回答').fill('需要明确由谁核实并预约设备。')
        member.get_by_role('button', name='发回主持人继续回应', exact=True).click()
        host.get_by_text('要求继续回应', exact=True).first.wait_for()
        host.get_by_role('button', name='逐条答复', exact=True).click()
        host.get_by_label('这条意见如何处理').select_option('answered')
        host.get_by_label('针对这条意见的答复').fill('由设备负责人周五核实并预约；未借到时重新讨论。')
        host.get_by_role('button', name='发送这条答复', exact=True).click()
        member.get_by_role('button', name='已收到答复', exact=True).click()
        member.get_by_text('本人已确认收到答复', exact=True).wait_for()
        host.screenshot(path=str(OUT/'02-逐条答复工作台.png'), full_page=True)
        host.get_by_role('button', name='议题与决定', exact=True).click()
        host.get_by_role('button', name='核对并确认决定', exact=True).click()
        host.get_by_label('如何处理', exact=True).select_option('modify')
        host.get_by_label('修改后的结论', exact=False).fill('周日采用方案 B；由设备负责人周五核实预约。')
        host.get_by_label('处理理由／回应说明').fill('根据设备限制调整日期，明确预约责任。')
        host.get_by_role('button', name='记录处理结果', exact=True).click()
        board.get_by_text('已确认', exact=True).wait_for()
        board.screenshot(path=str(OUT/'03-公共议题屏.png'), full_page=True)
        assert '小林' not in board.locator('body').inner_text()
        host.get_by_role('button', name='讨论复盘', exact=True).click()
        host.get_by_text('原决定', exact=True).wait_for()
        host.get_by_text('处理后', exact=True).wait_for()
        host.screenshot(path=str(OUT/'04-决定变化复盘.png'), full_page=True)
        member.locator('[data-rating="helpful"]').click()
        export = api(host_session, '/export/preview', method='GET').json()['data']
        assert export['contributions'][0]['acknowledged']
        assert len(export['contributions'][0]['history']) == 4
        assert 'feedback' not in json.dumps(export)
        # 只有合成摄像头画面；检查模型加载、重校准与关闭。
        member.get_by_role('button', name='个人与设备', exact=True).click()
        member.get_by_role('button', name='授权并开启摄像头', exact=True).click()
        member.get_by_role('button', name='关闭摄像头', exact=True).wait_for(timeout=60000)
        member.get_by_role('button', name='关闭摄像头', exact=True).click()
        member.get_by_text('摄像头已关闭，短时动作缓存已清除。', exact=True).wait_for()
        mobile = browser.new_context(viewport={'width':390,'height':844}, is_mobile=True)
        mobile_page = enter(member_session, 'member', mobile)
        mobile_page.get_by_role('button', name='我的回应', exact=False).click()
        mobile_page.get_by_text('本人已确认收到答复', exact=True).wait_for()
        assert mobile_page.evaluate('document.documentElement.scrollWidth <= innerWidth'), '手机横向溢出'
        mobile_page.screenshot(path=str(OUT/'05-手机回应页.png'), full_page=True)
        assert not errors, errors
        assert not external, external
        result = {'response_window':'passed', 'per_opinion_reply_and_dispute':'passed', 'decision_diff':'passed',
            'three_role_sync':'passed', 'private_feedback':'passed', 'synthetic_camera_lifecycle':'passed',
            'mobile_overflow':'passed', 'page_errors':errors, 'external_requests':external}
        (OUT/'browser-check.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False))
        mobile.close()
        context.close()
        browser.close()


if __name__ == '__main__':
    main()
