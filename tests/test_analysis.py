import threading
import time

from fastapi.testclient import TestClient

from tongxi import adapters
from tongxi.analysis import fingerprint
from tongxi.main import create_app
from test_rooms import call, env, setup_room


def wait_analysis(client, host, status="ready"):
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        snap = call(client, host, '/snapshot', method='GET')
        if snap['analysis']['status'] == status:
            return snap['analysis']
        time.sleep(.05)
    raise AssertionError(snap['analysis'])


def fake_analysis(room, local):
    s = room['segments'][-1]
    return {'phase': 'closing', 'summary': '正在确认活动日期', 'items': [{'title': '活动日期', 'conclusion': s['text'],
            'why': '发言中明确确认安排', 'evidence': [{'segment': s['id'], 'rev': s['rev']}]}]}


def test_automatic_analysis_without_host_page(monkeypatch):
    monkeypatch.setattr(adapters, 'analyze_discussion', fake_analysis)
    with TestClient(create_app()) as client:
        host = client.post('/api/v1/rooms', json={'title': '自动分析'}).json()
        call(client, host, '/segments', {'text': '那就决定周日开展活动。'})
        result = wait_analysis(client, host)
        assert result['phase'] == 'closing'
        assert result['items'][0]['conclusion'] == '那就决定周日开展活动。'
        snap = call(client, host, '/snapshot', method='GET')
        assert result['source'] == fingerprint(snap)


def test_revision_during_inference_discards_old_results(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def slow(room, local):
        calls.append(room['segments'][-1]['rev'])
        if len(calls) == 1:
            entered.set()
            assert release.wait(timeout=5)
        return fake_analysis(room, local)
    monkeypatch.setattr(adapters, 'analyze_discussion', slow)
    with TestClient(create_app()) as client:
        host = client.post('/api/v1/rooms', json={'title': '修订竞态'}).json()
        segment = call(client, host, '/segments', {'text': '周六可借设备。'})
        assert entered.wait(timeout=3)
        call(client, host, '/segments/'+segment['id'], {'version': 1, 'text': '周六不可借设备。'}, 'PATCH')
        release.set()
        result = wait_analysis(client, host)
        assert calls == [1, 2]
        assert result['items'][0]['evidence'][0]['rev'] == 2
        assert '不可借' in result['items'][0]['conclusion']


def test_semantic_matching_rejects_invalid_quote_index(env, monkeypatch):
    from tongxi import embeddings
    monkeypatch.setattr(embeddings, 'rank', lambda *args: [])
    client, _ = env
    host, members, d1, snap = setup_room(env)
    d2 = call(client, host, '/decisions', {'title': '另一议题', 'conclusion': '下周开会',
         'evidence': [{'segment': snap['segments'][0]['id'], 'rev': 1}]})
    monkeypatch.setattr(adapters, 'generate_json', lambda *args: {'relevant': True,
        'relation': 'addition', 'reason': '相关补充', 'quote_index': 99})
    result = call(client, members[0], '/matches', {'text': '这个安排需要改动'})
    assert result['items'] == []


def test_pause_and_access_control(env):
    client, _ = env
    host, members, _, _ = setup_room(env)
    call(client, members[0], '/ai/settings', {'enabled': True}, 'PUT', status=403)
    call(client, host, '/ai/settings', {'enabled': False}, 'PUT')
    snap = call(client, host, '/snapshot', method='GET')
    assert snap['analysis']['status'] == 'paused' and not snap['ai_enabled']
    mine = call(client, members[0], '/snapshot', method='GET')
    assert 'analysis' not in mine


def test_extractive_candidates_reject_invented_option():
    segments = [{'id': 's1', 'rev': 1, 'text': '那就采用方案B，周日开展活动。'}]
    invented = adapters.extract_candidates(segments, [{'title': '方案A', 'decision_segment': 's1', 'premises': []}])
    assert invented == []
    grounded = adapters.extract_candidates(segments, [{'title': '方案B', 'decision_segment': 's1', 'premises': []}])
    assert grounded[0]['conclusion'] == segments[0]['text']


def test_explicit_conditional_quote_cannot_be_final_candidate():
    segments = [{'id': 's1', 'rev': 1, 'text': '如果采用方案B，需要先预约。'}]
    result = adapters.extract_candidates(segments, [{'title': '方案B', 'decision_segment': 's1', 'premises': []}])
    assert result == []


def test_model_citations_round_trip_to_stable_room_segments(monkeypatch):
    segments = [
        {"id": "s2", "rev": 4, "text": "方案B需要实验室。", "stable": True},
        {"id": "pending", "rev": 1, "text": "这句还没稳定。", "stable": False},
        {"id": "s1", "rev": 2, "text": "那就采用方案B。", "stable": True},
    ]

    def generate(local, prompt, schema):
        fields = schema["properties"]["items"]["items"]["properties"]
        assert fields["decision_segment"]["enum"] == ["s1", "s2"]
        assert "这句还没稳定" not in prompt
        return {"items": [{"title": "方案B", "decision_segment": "s2", "premises": ["s1"]}]}

    monkeypatch.setattr(adapters, "generate_json", generate)
    result = adapters.analyze_discussion({"segments": segments}, None)
    assert result["items"][0]["conclusion"] == "那就采用方案B。"
    assert result["items"][0]["evidence"] == [{"segment": "s2", "rev": 4}, {"segment": "s1", "rev": 2}]
