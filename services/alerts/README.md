# Alert delivery

The runnable alert implementation lives in `services/api/app/main.py` to keep the optional demo backend a single process. Replay warnings are deduplicated by scenario, persisted, broadcast over WebSocket and optionally delivered to `SLACK_WEBHOOK_URL`. Unacknowledged warnings escalate after `ALERT_ESCALATION_SECONDS` (default 60). Acknowledgement suppresses escalation.

Webhook delivery is opt-in through backend configuration, and `tests/test_alert_delivery.py` exercises it against a local HTTP receiver: a warning produces exactly one `{"text": ...}` POST carrying the asset and a `[SIMULATED]` marker, an unconfigured deployment sends nothing, and a failing or unreachable endpoint still leaves the alert recorded locally. No message has ever been sent to Slack itself; what is verified is our side of the contract.

In-process escalation timers do not survive a process restart; production needs a durable queue, authentication, rate limits and a separate worker.

