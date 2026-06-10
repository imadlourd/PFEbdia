from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

from schema.event import DevEvent

logger = logging.getLogger(__name__)

CHANNELS  = ["#general", "#engineering", "#incidents", "#deployments", "#standup"]
USERS     = ["alice", "bob", "carol", "dan", "eve", "frank", "grace", "henry"]
TEAMS     = ["alpha", "beta", "gamma", "delta"]

MSG_TEMPLATES = [
    "PR #{n} est prete pour review",
    "Deploiement v1.4.{n} effectue avec succes sur prod",
    "Incident detecte sur le service auth — investigation en cours",
    "Incident #INC-{n} resolu apres {h}h d'interruption",
    "Sprint {n} termine — velocity : {v} points",
    "Ticket PROJ-{n} bloque en attente de validation",
    "Code review en attente sur PR #{n} depuis {h}h",
    "Build CI echoue sur la branche feature/{n}",
    "Hotfix deploye pour corriger la regression de PROJ-{n}",
    "Standup : {n} tickets termines hier, {b} bloques",
]


class SlackConnector:
    """
    Slack connector.
    Modes :
      - Export JSON  : SlackConnector(export_path="./slack_export")
      - API Slack    : SlackConnector(token="xoxb-...")
      - Synthetique  : SlackConnector.synthetic()
    """

    def __init__(
        self,
        token:       Optional[str]  = None,
        export_path: Optional[str]  = None,
        _synthetic:  bool           = False,
    ):
        self.token       = token
        self.export_path = Path(export_path) if export_path else None
        self._synthetic  = _synthetic or (token is None and export_path is None)

        if token:
            self._session = requests.Session()
            self._session.headers.update({
                "Authorization": f"Bearer {token}",
                "Content-Type":  "application/json",
            })

    @classmethod
    def synthetic(cls) -> "SlackConnector":
        return cls(_synthetic=True)

    # ── Public API ─────────────────────────────────────────────────────────────

    def fetch_all(self, days_back: int = 14) -> list[DevEvent]:
        since = datetime.now(timezone.utc) - timedelta(days=days_back)
        logger.info(f"[Slack] Fetching since {since.date()} "
                    f"({'synthetic' if self._synthetic else 'API/export'})")

        events = []
        if self._synthetic:
            events.extend(self._synthetic_messages(since))
            events.extend(self._synthetic_incidents(since))
        elif self.export_path:
            events.extend(self._from_export(since))
        else:
            events.extend(self._from_api(since))

        now    = datetime.now(timezone.utc)
        events = [e for e in events if e.occurred_at <= now]
        logger.info(f"[Slack] Collected {len(events)} events")
        return events

    # ── Export JSON ────────────────────────────────────────────────────────────

    def _from_export(self, since: datetime) -> list[DevEvent]:
        """
        Parse a Slack export directory.
        Structure attendue :
            slack_export/
              channels.json
              #general/
                2025-06-01.json
                ...
        """
        events = []
        channels_file = self.export_path / "channels.json"
        if not channels_file.exists():
            logger.warning(f"[Slack] channels.json not found in {self.export_path}")
            return events

        with open(channels_file) as f:
            channels = json.load(f)

        for channel in channels:
            channel_name = channel["name"]
            channel_dir  = self.export_path / channel_name
            if not channel_dir.exists():
                continue

            for msg_file in sorted(channel_dir.glob("*.json")):
                with open(msg_file) as f:
                    messages = json.load(f)
                for msg in messages:
                    ts = _ts_to_dt(float(msg["ts"]))
                    if ts < since:
                        continue
                    if msg.get("subtype"):
                        continue  # skip join/leave system messages

                    user = msg.get("user", "unknown")
                    text = msg.get("text", "")

                    event_type = _classify_message(text)

                    events.append(DevEvent(
                        source     = "slack",
                        event_type = event_type,
                        occurred_at= ts,
                        actor_id   = f"slack:{user}",
                        entity_ref = f"#{channel_name}",
                        entity_id  = msg["ts"],
                        payload    = {
                            "channel":   channel_name,
                            "text":      text[:500],
                            "reactions": [r["name"] for r in msg.get("reactions", [])],
                            "thread":    msg.get("thread_ts") is not None,
                        },
                    ))

        return events

    # ── Slack API ──────────────────────────────────────────────────────────────

    def _from_api(self, since: datetime) -> list[DevEvent]:
        events   = []
        channels = self._api_get("conversations.list", {
            "types":           "public_channel,private_channel",
            "exclude_archived": "true",
            "limit":           "200",
        }).get("channels", [])

        for ch in channels:
            ch_id   = ch["id"]
            ch_name = ch["name"]
            oldest  = str(since.timestamp())

            cursor = None
            while True:
                params = {
                    "channel": ch_id,
                    "oldest":  oldest,
                    "limit":   "200",
                }
                if cursor:
                    params["cursor"] = cursor

                data     = self._api_get("conversations.history", params)
                messages = data.get("messages", [])

                for msg in messages:
                    if msg.get("subtype"):
                        continue
                    ts   = _ts_to_dt(float(msg["ts"]))
                    user = msg.get("user", "unknown")
                    text = msg.get("text", "")

                    events.append(DevEvent(
                        source     = "slack",
                        event_type = _classify_message(text),
                        occurred_at= ts,
                        actor_id   = f"slack:{user}",
                        entity_ref = f"#{ch_name}",
                        entity_id  = msg["ts"],
                        payload    = {
                            "channel": ch_name,
                            "text":    text[:500],
                            "thread":  msg.get("thread_ts") is not None,
                        },
                    ))

                meta   = data.get("response_metadata", {})
                cursor = meta.get("next_cursor")
                if not cursor:
                    break

        return events

    def _api_get(self, method: str, params: dict) -> dict:
        resp = self._session.get(
            f"https://slack.com/api/{method}",
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Slack API error: {data.get('error')}")
        return data

    # ── Synthetic ──────────────────────────────────────────────────────────────

    def _synthetic_messages(self, since: datetime) -> list[DevEvent]:
        events, rng, n = [], random.Random(33), 0
        current = since

        while current < datetime.now(timezone.utc):
            n_msgs = rng.randint(8, 25)
            for _ in range(n_msgs):
                n      += 1
                user    = rng.choice(USERS)
                channel = rng.choice(CHANNELS)
                tmpl    = rng.choice(MSG_TEMPLATES)
                text    = tmpl.format(
                    n=rng.randint(1, 999),
                    h=rng.randint(1, 12),
                    v=rng.randint(20, 60),
                    b=rng.randint(0, 5),
                )
                ts = current + timedelta(hours=rng.uniform(8, 20))

                events.append(DevEvent(
                    source     = "slack",
                    event_type = _classify_message(text),
                    occurred_at= ts,
                    actor_id   = f"slack:{user}",
                    entity_ref = channel,
                    entity_id  = f"{ts.timestamp():.6f}",
                    payload    = {
                        "channel": channel,
                        "text":    text,
                        "team":    rng.choice(TEAMS),
                        "thread":  rng.random() < 0.3,
                    },
                ))
            current += timedelta(days=1)

        return events

    def _synthetic_incidents(self, since: datetime) -> list[DevEvent]:
        """Generate realistic incident open/resolve pairs."""
        events, rng = [], random.Random(99)
        inc_n = 200
        current = since

        while current < datetime.now(timezone.utc):
            if rng.random() < 0.35:  # ~2-3 incidents per week
                inc_n    += 1
                severity  = rng.choices(["P1","P2","P3"], weights=[10,30,60])[0]
                opened_at = current + timedelta(hours=rng.uniform(0, 22))
                mttr_h    = rng.gauss(
                    {"P1": 2.5, "P2": 5.0, "P3": 12.0}[severity],
                    {"P1": 1.0, "P2": 2.0, "P3":  4.0}[severity],
                )
                mttr_h    = max(0.25, mttr_h)
                resolved  = opened_at + timedelta(hours=mttr_h)

                events.append(DevEvent(
                    source     = "slack",
                    event_type = "incident_opened",
                    occurred_at= opened_at,
                    actor_id   = f"slack:{rng.choice(USERS)}",
                    entity_ref = f"#incidents",
                    entity_id  = f"INC-{inc_n}",
                    payload    = {
                        "channel":      "#incidents",
                        "text":         f"[{severity}] Incident INC-{inc_n} ouvert",
                        "severity":     severity,
                        "incident_id":  f"INC-{inc_n}",
                    },
                ))

                if resolved <= datetime.now(timezone.utc):
                    events.append(DevEvent(
                        source     = "slack",
                        event_type = "incident_resolved",
                        occurred_at= resolved,
                        actor_id   = f"slack:{rng.choice(USERS)}",
                        entity_ref = f"#incidents",
                        entity_id  = f"INC-{inc_n}-resolved",
                        payload    = {
                            "channel":     "#incidents",
                            "text":        f"[{severity}] Incident INC-{inc_n} resolu",
                            "severity":    severity,
                            "incident_id": f"INC-{inc_n}",
                            "mttr_hrs":    round(mttr_h, 2),
                        },
                    ))

            current += timedelta(days=1)

        return events


# ── Helpers ────────────────────────────────────────────────────────────────────

def _ts_to_dt(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)

def _classify_message(text: str) -> str:
    """
    Classify a Slack message into an event_type
    based on keywords in the text.
    """
    text_lower = text.lower()

    if any(w in text_lower for w in ["incident", "alerte", "alert", "panne", "down", "p1", "p2"]):
        if any(w in text_lower for w in ["resolu", "resolved", "ferme", "closed", "fixed"]):
            return "incident_resolved"
        return "incident_opened"

    if any(w in text_lower for w in ["deploiement", "deploy", "release", "mis en prod", "shipped"]):
        return "deployment_announced"

    if any(w in text_lower for w in ["review", "pr #", "pull request", "code review"]):
        return "review_requested"

    if any(w in text_lower for w in ["bloque", "blocked", "bloquant", "dependance"]):
        return "blocker_reported"

    if any(w in text_lower for w in ["sprint", "velocity", "retrospective", "retro"]):
        return "sprint_update"

    if any(w in text_lower for w in ["standup", "daily", "hier", "aujourd"]):
        return "standup_update"

    return "message"