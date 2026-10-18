"""Rotating event logs. Payloads, credentials and source text never enter the log."""
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import traceback
import uuid

from vtx.redact import redact

request_id = ContextVar('request_id', default=None)
_FIELDS = {'request_id', 'job_id', 'run_id', 'service_id', 'model', 'output', 'phase',
           'index', 'total', 'duration_ms', 'status', 'method', 'route', 'code',
           'error_type', 'stack', 'cached', 'total_tokens', 'config', 'work', 'logs'}


class EventLog:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger('vtx.events.' + uuid.uuid4().hex)
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        for name, level in [('app.jsonl', logging.INFO), ('errors.jsonl', logging.WARNING)]:
            handler = RotatingFileHandler(directory / name, maxBytes=5 * 1024 * 1024,
                                          backupCount=5, encoding='utf-8')
            handler.setLevel(level)
            handler.setFormatter(logging.Formatter('%(message)s'))
            self.logger.addHandler(handler)

    def event(self, event: str, level: int = logging.INFO, **fields):
        data = {'time': datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
                'level': logging.getLevelName(level), 'event': event}
        if request_id.get():
            data['request_id'] = request_id.get()
        for key, value in fields.items():
            if key not in _FIELDS or value is None:
                continue
            data[key] = redact(value) if isinstance(value, str) else value
        self.logger.log(level, json.dumps(data, ensure_ascii=False))

    def failure(self, event: str, exc: Exception, **fields):
        # Stack locations are useful; exception messages can contain arbitrary secrets.
        stack = [{'file': Path(frame.filename).name, 'line': frame.lineno, 'function': frame.name}
                 for frame in traceback.extract_tb(exc.__traceback__)]
        self.event(event, logging.ERROR, error_type=type(exc).__name__,
                   code=getattr(exc, 'code', None), stack=stack, **fields)

    def close(self):
        for handler in list(self.logger.handlers):
            handler.close()
            self.logger.removeHandler(handler)


def install_request_logging(app, events):
    from fastapi import Request
    import time

    @app.middleware('http')
    async def trace_request(request: Request, call_next):
        supplied = request.headers.get('x-request-id', '')
        identifier = supplied if re.fullmatch(r'[A-Za-z0-9_-]{1,64}', supplied) else uuid.uuid4().hex[:16]
        context = request_id.set(identifier)
        started = time.monotonic()
        try:
            response = await call_next(request)
            route = getattr(request.scope.get('route'), 'path', None)
            if route and (request.method != 'GET' or response.status_code >= 400):
                events.event('http.request', logging.WARNING if response.status_code >= 400 else logging.INFO,
                             method=request.method, route=route, status=response.status_code,
                             duration_ms=round((time.monotonic() - started) * 1000))
            response.headers['X-Request-ID'] = identifier
            return response
        except Exception as exc:
            events.failure('http.failed', exc, method=request.method,
                           route=getattr(request.scope.get('route'), 'path', None),
                           duration_ms=round((time.monotonic() - started) * 1000))
            raise
        finally:
            request_id.reset(context)
