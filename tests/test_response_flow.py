import json
import time

import pytest

from test_rooms import env, setup_room, call, authorize, progress


def snap(env, who):
    return call(env[0], who, '/snapshot', method='GET')


def automatic(env):
    client, store = env
    host, members, d, original = setup_room(env)
    m = members[0]
    authorize(client, m)
    actor = store.auth(m['token'], m['room'])
    epoch = store.connect(m['room'], actor)
    progress(env, m, d['evidence'], epoch=epoch)
    prepared = call(client, host, f'/decisions/{d["id"]}/prepare', {'version': 1})
    return host, m, members[1], prepared, snap(env, m)['requests'][0], epoch, original


def receipt(env, m, epoch, refs, state='idle', completed=None, reviewed=None, seq=2):
    return call(env[0], m, '/presentation', {'epoch': epoch, 'seq': seq, 'consent_version': 2,
        'evidence': refs, 'state': state, 'instance': 'actual-player',
        'completed': completed or [], 'review_completed': reviewed or []})


def test_disappearing_queue_is_not_receipt_and_expiry_is_not_consent(env):
    host, m, _, d, q, epoch, _ = automatic(env)
    receipt(env, m, epoch, [])
    assert snap(env, m)['requests'][0]['phase'] == 'receiving'
    receipt(env, m, epoch, [], completed=d['evidence'], seq=3)
    current = snap(env, m)['requests'][0]
    assert current['phase'] == 'thinking' and current['minimum_until'] >= time.time()+18
    call(env[0], host, f'/requests/{q["id"]}', {'action': 'handled', 'reason': '立即关闭'}, 'PATCH', status=409)
    with env[1].lock, env[1].db:
        r = env[1].load(m['room'])
        r['requests'][0]['minimum_until'] = time.time()-1
        env[1].save(r)
    current = snap(env, m)['requests'][0]
    assert current['status'] == 'delivered'
    call(env[0], host, f'/decisions/{d["id"]}/resolve', {'version': d['version'], 'action': 'maintain', 'reason': '时间已到'}, status=409)


def test_review_completion_and_playback_completion_are_separate(env):
    host, m, _, d, q, epoch, _ = automatic(env)
    receipt(env, m, epoch, [], reviewed=d['evidence'])
    assert snap(env, m)['requests'][0]['phase'] == 'receiving'
    receipt(env, m, epoch, [], completed=d['evidence'], seq=3)
    assert snap(env, m)['requests'][0]['phase'] == 'thinking'


def test_offline_is_unknown_and_reconnect_preserves_opportunity(env):
    host, m, _, d, q, epoch, _ = automatic(env)
    actor = env[1].auth(m['token'], m['room'])
    env[1].disconnect(m['room'], actor, epoch)
    assert snap(env, m)['requests'][0]['phase'] == 'unknown'
    assert snap(env, host)['decisions'][0]['status'] == 'waiting'
    env[1].connect(m['room'], actor)
    assert snap(env, m)['requests'][0]['phase'] == 'unknown'


def test_only_owner_can_extend_and_handling_requires_explanation(env):
    host, m, other, d, q, _, _ = automatic(env)
    call(env[0], other, f'/requests/{q["id"]}', {'action':'ready'}, 'PATCH', status=403)
    call(env[0], m, f'/requests/{q["id"]}', {'action':'ready'}, 'PATCH')
    first = snap(env, m)['requests'][0]['minimum_until']
    call(env[0], m, f'/requests/{q["id"]}', {'action':'extend'}, 'PATCH')
    assert snap(env, m)['requests'][0]['minimum_until'] >= first
    call(env[0], host, f'/requests/{q["id"]}', {'action':'handled'}, 'PATCH', status=422)


def test_unanswered_opinion_blocks_confirmation_without_request(env):
    client, _ = env
    host, members, d, _ = setup_room(env)
    c = call(client, members[0], '/contributions', {'text':'这项安排的时间不对', 'decision':d['id']})
    prepared = call(client, host, f'/decisions/{d["id"]}/prepare', {'version':1})
    call(client, host, f'/decisions/{d["id"]}/resolve', {'version':prepared['version'], 'action':'maintain', 'reason':'继续'}, status=409)
    result = call(client, host, f'/decisions/{d["id"]}/resolve', {'version':prepared['version'], 'action':'maintain', 'reason':'先确认方案，时间待核实', 'close_pending':True})
    assert result['resolutions'][-1]['continued_without_response']
    assert snap(env, members[0])['contributions'][0]['resolution'] is None


def test_each_opinion_needs_own_answer_and_can_be_disputed(env):
    client, _ = env
    host, m, other, d, q, _, _ = automatic(env)
    a = call(client, m, '/contributions', {'text':'实验室周六不开门', 'decision':d['id']})
    b = call(client, m, '/contributions', {'text':'活动预算也需要核实', 'decision':d['id']})
    assert snap(env, m)['requests'][0]['phase'] == 'submitted'
    call(client, host, f'/contributions/{a["id"]}/reply', {'version':1, 'outcome':'partial', 'reason':'已核实日期', 'impact':'日期拟调整'})
    assert snap(env, m)['requests'][0]['status'] == 'delivered', '第二条未答复仍需保留'
    call(client, host, f'/contributions/{b["id"]}/reply', {'version':1, 'outcome':'verifying', 'reason':'还需询价'})
    assert snap(env, m)['contributions'][1]['status'] == 'verifying'
    call(client, other, f'/contributions/{a["id"]}/feedback', {'version':2, 'action':'dispute', 'reason':'冒充'}, status=403)
    call(client, m, f'/contributions/{a["id"]}/feedback', {'version':2, 'action':'dispute', 'reason':'还没有说明如何预约'})
    opinion = snap(env, m)['contributions'][0]
    assert opinion['status'] == 'disputed' and len(opinion['history']) == 2
    call(client, host, f'/contributions/{a["id"]}/reply', {'version':2, 'reason':'旧窗口'}, status=409)
    call(client, host, f'/contributions/{a["id"]}/reply', {'version':3, 'outcome':'answered', 'reason':'由负责人周五预约'})
    call(client, m, f'/contributions/{a["id"]}/feedback', {'version':4, 'action':'acknowledge'})
    assert snap(env, m)['contributions'][0]['acknowledged']


def test_claiming_adoption_requires_specific_impact(env):
    client, _ = env
    host, members, _, _ = setup_room(env)
    c = call(client, members[0], '/contributions', {'text':'希望增加无障碍入口'})
    call(client, host, f'/contributions/{c["id"]}/reply', {'version':1, 'outcome':'adopted', 'reason':'收到'}, status=422)
    assert snap(env, members[0])['contributions'][0]['status'] == 'pending'


def test_feedback_private_and_export_has_no_device_timeline(env):
    host, m, other, d, q, _, original = automatic(env)
    call(env[0], other, '/interventions/feedback', {'request':q['id'], 'rating':'uncomfortable'}, status=403)
    call(env[0], m, '/interventions/feedback', {'request':q['id'], 'rating':'uncomfortable'})
    assert snap(env, m)['requests'][0]['feedback']['rating'] == 'uncomfortable'
    host_q = snap(env, host)['requests'][0]
    assert not {'feedback','evidence','minimum_until','transitions'} & host_q.keys()
    exported = call(env[0], host, '/export/preview', method='GET')['data']
    assert 'uncomfortable' not in json.dumps(exported)
    board = {'room':m['room'], 'token':original['board_token']}
    assert 'requests' not in snap(env, board)


def test_manual_request_survives_evidence_revision(env):
    client, _ = env
    host, members, d, _ = setup_room(env)
    q = call(client, members[0], '/requests', {'kind':'clarify', 'decision':d['id']})
    segment = d['evidence'][0]['segment']
    call(client, host, f'/segments/{segment}', {'version':1, 'text':'周六不能借设备'}, 'PATCH')
    mine = snap(env, members[0])['requests'][0]
    assert mine['id'] == q['id'] and mine['status'] == 'delivered'
    current = snap(env, host)['decisions'][0]
    prepared = call(client, host, f'/decisions/{d["id"]}/prepare', {'version':current['version']})
    assert prepared['status'] == 'waiting'
    assert snap(env, members[0])['requests'][0]['round'] == prepared['round']
    call(client, host, f'/decisions/{d["id"]}/resolve', {'version':prepared['version'], 'action':'maintain', 'reason':'忽略旧请求'}, status=409)


def test_old_completion_cannot_start_new_evidence_window(env):
    host, m, _, d, q, epoch, _ = automatic(env)
    segment = d['evidence'][0]['segment']
    call(env[0], host, f'/segments/{segment}', {'version':1, 'text':'周六不能借设备'}, 'PATCH')
    current = snap(env, host)['decisions'][0]
    call(env[0], host, f'/decisions/{d["id"]}/prepare', {'version':current['version']})
    call(env[0], m, '/presentation', {'epoch':epoch, 'seq':2, 'consent_version':2, 'evidence':[],
        'state':'idle', 'instance':'old-audio', 'completed':d['evidence']}, status=409)
    assert snap(env, m)['requests'][0]['status'] == 'invalidated'


def test_host_command_response_also_hides_private_window_data(env):
    host, m, _, d, q, _, _ = automatic(env)
    response = call(env[0], host, f'/requests/{q["id"]}', {'action':'seen'}, 'PATCH')
    assert not {'minimum_until','feedback','evidence','transitions'} & response.keys()
