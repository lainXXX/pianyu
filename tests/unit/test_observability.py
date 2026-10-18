import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from vtx.observability import EventLog, install_request_logging


def read(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def test_log_is_structured_redacted_and_only_logs_supported_fields(tmp_path):
    log = EventLog(tmp_path)
    log.event('job.started', job_id='job1', prompt='private transcript', key='arbitrary-secret',
              model='model', payload={'authorization': 'Bearer fake-private-secret'})
    try:
        raise ValueError('arbitrary-secret')
    except ValueError as exc:
        log.failure('job.failed', exc, job_id='job1')
    log.close()
    content = (tmp_path / 'app.jsonl').read_text(encoding='utf-8')
    assert 'arbitrary-secret' not in content and 'private transcript' not in content
    events = read(tmp_path / 'app.jsonl')
    assert events[0]['job_id'] == 'job1'
    assert events[1]['error_type'] == 'ValueError' and events[1]['stack'][0]['line'] > 0
    assert len(read(tmp_path / 'errors.jsonl')) == 1


def test_request_ids_and_route_templates_exclude_query_and_body(tmp_path):
    app = FastAPI()
    log = EventLog(tmp_path)
    install_request_logging(app, log)

    @app.post('/api/item/{identifier}')
    def save(identifier: str):
        return {'ok': True}

    client = TestClient(app)
    response = client.post('/api/item/raw-secret?key=another-secret', json={'key': 'body-secret'},
                           headers={'X-Request-ID': 'trace_123'})
    assert response.headers['X-Request-ID'] == 'trace_123'
    event = read(tmp_path / 'app.jsonl')[0]
    assert event['route'] == '/api/item/{identifier}' and event['request_id'] == 'trace_123'
    assert 'secret' not in json.dumps(event)
    log.close()
