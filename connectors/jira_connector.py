from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
from requests.auth import HTTPBasicAuth

from schema.event import DevEvent

logger = logging.getLogger(__name__)

ISSUE_TYPES  = ["Story", "Bug", "Task", "Sub-task", "Epic"]
PRIORITIES   = ["Highest", "High", "Medium", "Low", "Lowest"]
TRANSITIONS  = [
    ("To Do",       "In Progress"),
    ("In Progress", "In Review"),
    ("In Progress", "Blocked"),
    ("Blocked",     "In Progress"),
    ("In Review",   "Done"),
    ("In Review",   "In Progress"),
]
COMPONENTS   = ["backend", "frontend", "infra", "data-pipeline", "auth", "api"]
USERS        = ["alice", "bob", "carol", "dan", "eve", "frank", "grace", "henry"]
TEAMS        = ["alpha", "beta", "gamma", "delta"]
STORY_POINTS = [1, 2, 3, 5, 8, 13]


class JiraConnector:

    def __init__(self, base_url=None, email=None, api_token=None,
                 project_key="PROJ", _synthetic=False):
        self.base_url    = base_url
        self.email       = email
        self.api_token   = api_token
        self.project_key = project_key
        self._synthetic  = _synthetic or (api_token is None)

        if not self._synthetic:
            self._session = requests.Session()
            self._session.auth = HTTPBasicAuth(email, api_token)
            self._session.headers.update({"Accept": "application/json"})

    @classmethod
    def synthetic(cls, project_key="PROJ"):
        return cls(project_key=project_key, _synthetic=True)

    def fetch_all(self, days_back=14):
        since = datetime.now(timezone.utc) - timedelta(days=days_back)
        logger.info(f"[Jira] Fetching since {since.date()} ({'synthetic' if self._synthetic else self.base_url})")

        events = []
        if self._synthetic:
            events.extend(self._synthetic_tickets(since))
            events.extend(self._synthetic_transitions(since))
            events.extend(self._synthetic_sprints(since))
        else:
            events.extend(list(self._fetch_issues(since)))
            events.extend(list(self._fetch_sprints(since)))

        logger.info(f"[Jira] Collected {len(events)} events")
        return events

    # ── Real API ──────────────────────────────────────────────────────────────

    def _get(self, path, params=None):
        url  = f"{self.base_url}/rest/api/2/{path}"
        resp = self._session.get(url, params=params, timeout=15)
        if resp.status_code == 429:
            time.sleep(int(resp.headers.get("Retry-After", 30)))
            return self._get(path, params)
        resp.raise_for_status()
        return resp.json()

    def _search(self, jql, fields):
        results, start = [], 0
        while True:
            data   = self._get("search", {"jql": jql, "fields": ",".join(fields),
                                          "startAt": start, "maxResults": 100})
            issues = data.get("issues", [])
            results.extend(issues)
            start += len(issues)
            if start >= data["total"]:
                break
        return results

    def _fetch_issues(self, since):
        jql    = f"project = {self.project_key} AND updated >= '{since.strftime('%Y-%m-%d')}' ORDER BY updated DESC"
        fields = ["summary", "status", "issuetype", "priority", "assignee",
                  "reporter", "created", "updated", "resolutiondate",
                  "customfield_10016", "components"]
        for issue in self._search(jql, fields):
            f          = issue["fields"]
            key        = issue["key"]
            author     = (f.get("reporter") or {}).get("accountId", "unknown")
            assignee   = (f.get("assignee") or {}).get("accountId", "unassigned")
            created_at = _parse_dt(f["created"])
            payload    = {
                "summary":      f["summary"],
                "issue_type":   f["issuetype"]["name"],
                "priority":     (f.get("priority") or {}).get("name", "Medium"),
                "status":       f["status"]["name"],
                "story_points": f.get("customfield_10016"),
                "assignee":     assignee,
            }
            if created_at >= since:
                yield DevEvent(source="jira", event_type="ticket_created",
                    occurred_at=created_at, actor_id=f"jira:{author}",
                    entity_ref=key, entity_id=key, payload=payload)
            if f["status"]["name"] == "Done" and f.get("resolutiondate"):
                resolved_at = _parse_dt(f["resolutiondate"])
                if resolved_at >= since:
                    yield DevEvent(source="jira", event_type="ticket_closed",
                        occurred_at=resolved_at, actor_id=f"jira:{assignee}",
                        entity_ref=key, entity_id=key,
                        payload={**payload, "cycle_time_hrs": round(
                            (resolved_at - created_at).total_seconds() / 3600, 2)})

    def _fetch_sprints(self, since):
        try:
            boards = self._get("board", {"projectKeyOrId": self.project_key})
            for board in boards.get("values", []):
                sprints = self._get(f"board/{board['id']}/sprint", {"state": "active,closed"})
                for sprint in sprints.get("values", []):
                    start_date = _parse_dt_opt(sprint.get("startDate"))
                    end_date   = _parse_dt_opt(sprint.get("endDate"))
                    if start_date and start_date >= since:
                        yield DevEvent(source="jira", event_type="sprint_started",
                            occurred_at=start_date, actor_id="jira:system",
                            entity_ref=sprint["name"], entity_id=str(sprint["id"]),
                            payload={"name": sprint["name"]})
                    if end_date and end_date >= since and sprint["state"] == "closed":
                        yield DevEvent(source="jira", event_type="sprint_completed",
                            occurred_at=end_date, actor_id="jira:system",
                            entity_ref=sprint["name"], entity_id=str(sprint["id"]),
                            payload={"name": sprint["name"]})
        except Exception as e:
            logger.warning(f"[Jira] Could not fetch sprints: {e}")

    # ── Synthetic ─────────────────────────────────────────────────────────────

    def _synthetic_tickets(self, since):
        events, rng, n = [], random.Random(55), 400
        current = since
        while current < datetime.now(timezone.utc):
            for _ in range(rng.randint(3, 10)):
                n += 1
                key      = f"{self.project_key}-{n}"
                author   = rng.choice(USERS)
                assignee = rng.choice(USERS)
                team     = rng.choice(TEAMS)
                sp       = rng.choice(STORY_POINTS)
                comp     = rng.choice(COMPONENTS)
                created  = current + timedelta(hours=rng.uniform(0, 16))
                payload  = {
                    "summary":      f"[{comp}] Ticket {n}",
                    "issue_type":   rng.choices(ISSUE_TYPES, weights=[40,25,20,10,5])[0],
                    "priority":     rng.choices(["High","Medium","Low"], weights=[20,60,20])[0],
                    "status":       "To Do",
                    "story_points": sp,
                    "team":         team,
                    "assignee":     assignee,
                }
                events.append(DevEvent(source="jira", event_type="ticket_created",
                    occurred_at=created, actor_id=f"jira:{author}",
                    entity_ref=key, entity_id=key, payload=payload))

                if rng.random() < 0.60:
                    mean_ct  = {"alpha": 28, "beta": 52, "gamma": 90, "delta": 38}[team]
                    cycle_h  = max(2.0, rng.gauss(mean_ct, mean_ct * 0.3))
                    closed   = created + timedelta(hours=cycle_h)
                    if closed <= datetime.now(timezone.utc):
                        events.append(DevEvent(source="jira", event_type="ticket_closed",
                            occurred_at=closed, actor_id=f"jira:{assignee}",
                            entity_ref=key, entity_id=key,
                            payload={**payload, "status": "Done",
                                     "cycle_time_hrs": round(cycle_h, 2)}))
            current += timedelta(days=1)
        return events

    def _synthetic_transitions(self, since):
        events, rng, n = [], random.Random(77), 400
        current = since
        while current < datetime.now(timezone.utc):
            for _ in range(rng.randint(4, 14)):
                key    = f"{self.project_key}-{rng.randint(n - 200, n)}"
                from_s, to_s = rng.choice(TRANSITIONS)
                events.append(DevEvent(source="jira", event_type="ticket_transitioned",
                    occurred_at=current + timedelta(hours=rng.uniform(0, 20)),
                    actor_id=f"jira:{rng.choice(USERS)}",
                    entity_ref=key, entity_id=key,
                    payload={"from_status": from_s, "to_status": to_s}))
            current += timedelta(days=1)
        return events

    def _synthetic_sprints(self, since):
        events    = []
        sprint_id = 10
        sprint_n  = 8
        current   = since - timedelta(days=since.weekday())
        while current < datetime.now(timezone.utc):
            sprint_id += 1
            sprint_n  += 1
            name = f"Sprint {sprint_n}"
            end  = current + timedelta(days=14)
            events.append(DevEvent(source="jira", event_type="sprint_started",
                occurred_at=current + timedelta(hours=9),
                actor_id="jira:system", entity_ref=name, entity_id=str(sprint_id),
                payload={"name": name}))
            if end < datetime.now(timezone.utc):
                events.append(DevEvent(source="jira", event_type="sprint_completed",
                    occurred_at=end + timedelta(hours=17),
                    actor_id="jira:system", entity_ref=name, entity_id=str(sprint_id),
                    payload={"name": name}))
            current += timedelta(days=14)
        return events


def _parse_dt(s):
    from dateutil.parser import parse
    dt = parse(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

def _parse_dt_opt(s):
    return _parse_dt(s) if s else None