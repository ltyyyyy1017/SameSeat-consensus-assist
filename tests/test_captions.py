"""只验证字幕桥接权限与原句一致性；识别质量由真实模型检查另报。"""
from fastapi.testclient import TestClient

from tongxi.main import create_app


class StubStream:
    def accept_pcm(self, pcm):
        return ['周六实验室借不到。', '是否改到周日？']

    def finish(self):
        return ['请先核对设备安排。']


class StubASR:
    def new_stream(self):
        return StubStream()


def open_host(client):
    return client.post('/api/v1/rooms', json={'title': '字幕接入检查', 'name': '主持'}).json()


def connect(client, session):
    return client.websocket_connect(f'/api/v1/rooms/{session["room"]}/caption-stream')


def hello(ws, session):
    ws.send_json({'token': session['token'], 'sample_rate': 16000})


def ready(ws, host):
    hello(ws, host)
    assert ws.receive_json()['type'] == 'loading'
    assert ws.receive_json()['type'] == 'ready'


def test_sentences_commit_separately_and_stop_flushes_tail():
    app = create_app(auto_ai=False)
    app.state.asr = StubASR()
    with TestClient(app) as client:
        host = open_host(client)
        with connect(client, host) as ws:
            ready(ws, host)
            ws.send_bytes(b'\0\0' * 512)
            message = ws.receive_json()
            assert [s['text'] for s in message['items']] == ['周六实验室借不到。', '是否改到周日？']
            assert ws.receive_json() == {'type': 'ack', 'frames': 1}
            ws.send_json({'type': 'stop'})
            assert ws.receive_json()['items'][0]['text'] == '请先核对设备安排。'
            assert ws.receive_json()['type'] == 'done'
        snap = client.get(f'/api/v1/rooms/{host["room"]}/snapshot',
                          headers={'Authorization': 'Bearer ' + host['token']}).json()
        assert len(snap['segments']) == 3
        assert all(s['source'] == 'asr' and s['stable'] and s['rev'] == 1 for s in snap['segments'])
        assert len({s['id'] for s in snap['segments']}) == 3


def test_members_cannot_start_room_microphone():
    app = create_app(auto_ai=False)
    app.state.asr = StubASR()
    with TestClient(app) as client:
        host = open_host(client)
        snap = app.state.store.read(host['room'])
        member = client.post('/api/v1/join', json={'invite': snap['invite'], 'name': '成员'}).json()
        with connect(client, member) as ws:
            hello(ws, member)
            assert ws.receive_json()['type'] == 'error'
        assert not app.state.store.read(host['room'])['segments']


def test_one_recording_per_room_and_release_on_disconnect():
    app = create_app(auto_ai=False)
    app.state.asr = StubASR()
    with TestClient(app) as client:
        host = open_host(client)
        with connect(client, host) as first:
            ready(first, host)
            with connect(client, host) as other:
                hello(other, host)
                assert other.receive_json()['type'] == 'error'
            # 被拒绝的连接不能释放第一个连接的录音资格。
            with connect(client, host) as third:
                hello(third, host)
                assert third.receive_json()['type'] == 'error'
        with connect(client, host) as again:
            ready(again, host)


def test_room_close_during_recording_prevents_late_transcript():
    app = create_app(auto_ai=False)
    app.state.asr = StubASR()
    with TestClient(app) as client:
        host = open_host(client)
        with connect(client, host) as ws:
            ready(ws, host)
            client.post(f'/api/v1/rooms/{host["room"]}/close',
                        headers={'Authorization': 'Bearer ' + host['token'], 'Idempotency-Key': 'close-caption-check'})
            ws.send_json({'type': 'stop'})
            assert ws.receive_json()['type'] == 'error'
        assert not app.state.store.read(host['room'])['segments']
