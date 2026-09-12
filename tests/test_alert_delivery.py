"""R7: outbound alert delivery, exercised against a real HTTP receiver.

The webhook contract was previously unverified: the code path existed but nothing had ever
confirmed that a warning produces a correctly shaped POST, or that a dead endpoint degrades
instead of losing the alert. These tests stand up a local receiver and assert both.

Slack itself is never contacted. What is proven here is our side of the contract.
"""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import threading

ALARM_STEP = 185  # the step at which sim-gearbox-drift crosses the M2 warning threshold


class _Receiver(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        try: self.server.received.append(json.loads(body))
        except ValueError: self.server.received.append({'undecodable': body[:200].decode('utf-8', 'replace')})
        self.send_response(self.server.status); self.end_headers(); self.wfile.write(b'{}')

    def log_message(self, *args): pass  # keep pytest output clean


@contextmanager
def receiver(status: int = 200):
    server = HTTPServer(('127.0.0.1', 0), _Receiver)
    server.received, server.status = [], status
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try: yield server
    finally: server.shutdown(); server.server_close()


def _raise_warning(client):
    response = client.post('/ingest/replay', json={'scenario_id': 'sim-gearbox-drift', 'step': ALARM_STEP})
    assert response.status_code == 200
    return response


def test_a_warning_is_delivered_to_a_configured_webhook(client, monkeypatch):
    with receiver() as hook:
        monkeypatch.setenv('SLACK_WEBHOOK_URL', f'http://127.0.0.1:{hook.server_port}/hook')
        monkeypatch.setenv('ALERT_ESCALATION_SECONDS', '600')  # keep escalation out of this assertion
        _raise_warning(client)

        assert len(hook.received) == 1, 'the configured webhook received exactly one message'
        message = hook.received[0]
        assert set(message) == {'text'}, 'incoming-webhook payloads carry a single text field'
        assert 'T-03' in message['text'] and 'gearbox' in message['text'].lower()
        assert '[SIMULATED]' in message['text'], 'demo alerts must announce themselves as simulated'


def test_a_dead_webhook_never_costs_us_the_alert(client, monkeypatch):
    """A failing notifier must degrade to local-only, not swallow the warning."""
    with receiver(status=500) as hook:
        monkeypatch.setenv('SLACK_WEBHOOK_URL', f'http://127.0.0.1:{hook.server_port}/hook')
        monkeypatch.setenv('ALERT_ESCALATION_SECONDS', '600')
        _raise_warning(client)

        assert len(hook.received) == 1, 'delivery was attempted'
        alerts = client.get('/alerts').json()
        assert any(a['asset_id'] == 'T-03' and a['severity'] == 'Warning' for a in alerts), \
            'the alert survives a failed delivery'


def test_an_unreachable_host_is_handled_like_any_other_failure(client, monkeypatch):
    # Port 1 is reserved and never listening, so this exercises connect failure, not an HTTP status.
    monkeypatch.setenv('SLACK_WEBHOOK_URL', 'http://127.0.0.1:1/hook')
    monkeypatch.setenv('ALERT_ESCALATION_SECONDS', '600')
    _raise_warning(client)
    assert any(a['asset_id'] == 'T-03' for a in client.get('/alerts').json())


def test_nothing_is_sent_when_no_webhook_is_configured(client, monkeypatch):
    """Opt-in delivery: an unconfigured deployment must stay silent rather than guess a destination."""
    with receiver() as hook:
        monkeypatch.delenv('SLACK_WEBHOOK_URL', raising=False)
        monkeypatch.setenv('ALERT_ESCALATION_SECONDS', '600')
        _raise_warning(client)

        assert hook.received == [], 'no outbound message without explicit configuration'
        assert any(a['asset_id'] == 'T-03' for a in client.get('/alerts').json())
