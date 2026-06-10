from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional

import requests

from schema.event import DevEvent

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
TEAMS      = ["alpha", "beta", "gamma", "delta"]
USERS      = ["alice", "bob", "carol", "dan", "eve", "frank", "grace", "henry"]
PR_LABELS  = ["bug", "feature", "refactor", "docs", "hotfix", "chore"]


class GitHubConnector:

    def __init__(self, token=None, owner="myorg", repo="myrepo", _synthetic=False):
        self.token      = token
        self.owner      = owner
        self.repo       = repo
        self._synthetic = _synthetic or (token is None)

        self._session = requests.Session()
        if token:
            self._session.headers.update({
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            })

    @classmethod
    def synthetic(cls, owner="myorg", repo="myrepo"):
        return cls(token=None, owner=owner, repo=repo, _synthetic=True)

    def fetch_all(self, days_back=14):
        since = datetime.now(timezone.utc) - timedelta(days=days_back)
        logger.info(f"[GitHub] Fetching since {since.date()} ({'synthetic' if self._synthetic else self.repo})")

        events = []
        if self._synthetic:
            events.extend(self._synthetic_prs(since))
            events.extend(self._synthetic_commits(since))
            events.extend(self._synthetic_releases(since))
            events.extend(self._synthetic_action_runs(since))
        else:
            events.extend(list(self._fetch_prs(since)))
            events.extend(list(self._fetch_commits(since)))
            events.extend(list(self._fetch_releases(since)))
            events.extend(list(self._fetch_action_runs(since)))

        now = datetime.now(timezone.utc)
        events = [e for e in events if e.occurred_at <= now]
        logger.info(f"[GitHub] Collected {len(events)} events")
        return events

    # ── Real API ──────────────────────────────────────────────────────────────

    def _get(self, path, params=None):
        url     = f"{GITHUB_API}/{path}"
        results = []
        page    = 1
        while True:
            p = {**(params or {}), "per_page": 100, "page": page}
            resp = self._session.get(url, params=p, timeout=10)
            if resp.status_code == 403:
                wait = max(int(resp.headers.get("X-RateLimit-Reset", time.time() + 60)) - time.time(), 1)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            if not data:
                break
            results.extend(data if isinstance(data, list) else [data])
            if len(data) < 100:
                break
            page += 1
        return results

    def _fetch_prs(self, since):
        for pr in self._get(f"repos/{self.owner}/{self.repo}/pulls", {"state": "all", "sort": "updated", "direction": "desc"}):
            if _parse_dt(pr["updated_at"]) < since:
                break
            author = pr.get("user", {}).get("login", "unknown")
            pr_id  = str(pr["number"])
            ref    = f"PR#{pr_id}"
            yield DevEvent(source="github", event_type="pr_opened",
                occurred_at=_parse_dt(pr["created_at"]),
                actor_id=f"github:{author}", entity_ref=ref, entity_id=pr_id,
                payload={"title": pr["title"], "additions": pr.get("additions", 0),
                         "deletions": pr.get("deletions", 0), "base": pr["base"]["ref"]})
            if pr.get("merged_at"):
                yield DevEvent(source="github", event_type="pr_merged",
                    occurred_at=_parse_dt(pr["merged_at"]),
                    actor_id=f"github:{author}", entity_ref=ref, entity_id=pr_id,
                    payload={"title": pr["title"],
                             "lead_time_hrs": _delta_hours(pr["created_at"], pr["merged_at"])})

    def _fetch_commits(self, since):
        for c in self._get(f"repos/{self.owner}/{self.repo}/commits", {"since": since.isoformat()}):
            author = (c.get("author") or {}).get("login") or c["commit"]["author"]["name"]
            sha    = c["sha"][:8]
            yield DevEvent(source="github", event_type="commit_pushed",
                occurred_at=_parse_dt(c["commit"]["author"]["date"]),
                actor_id=f"github:{author}", entity_ref=f"SHA:{sha}", entity_id=sha,
                payload={"message": c["commit"]["message"][:200]})

    def _fetch_releases(self, since):
        for r in self._get(f"repos/{self.owner}/{self.repo}/releases"):
            if _parse_dt(r["created_at"]) < since:
                continue
            yield DevEvent(source="github", event_type="release_created",
                occurred_at=_parse_dt(r["created_at"]),
                actor_id=f"github:{r.get('author', {}).get('login', 'unknown')}",
                entity_ref=r["tag_name"], entity_id=str(r["id"]),
                payload={"name": r["name"], "prerelease": r["prerelease"]})

    def _fetch_action_runs(self, since):
        for run in self._get(f"repos/{self.owner}/{self.repo}/actions/runs",
                             {"created": f">={since.date().isoformat()}"}):
            if run.get("status") != "completed":
                continue
            yield DevEvent(source="github", event_type="action_run_completed",
                occurred_at=_parse_dt(run["updated_at"]),
                actor_id="github:ci-bot",
                entity_ref=f"RUN#{run['id']}", entity_id=str(run["id"]),
                payload={"workflow": run["name"], "conclusion": run["conclusion"],
                         "branch": run["head_branch"]})

    # ── Synthetic ─────────────────────────────────────────────────────────────

    def _synthetic_prs(self, since):
        events, rng, pr_id = [], random.Random(42), 1000
        current = since
        while current < datetime.now(timezone.utc):
            for _ in range(rng.randint(2, 7)):
                author = rng.choice(USERS)
                team   = rng.choice(TEAMS)
                label  = rng.choice(PR_LABELS)
                pr_id += 1
                ref    = f"PR#{pr_id}"
                opened_at = current + timedelta(hours=rng.uniform(0, 18))
                if opened_at > datetime.now(timezone.utc):
                    continue
                review_lag = timedelta(hours=abs(rng.gauss(
                    {"alpha": 8, "beta": 22, "gamma": 40, "delta": 14}[team], 4)))
                merged_at = opened_at + review_lag + timedelta(hours=rng.uniform(0.5, 4))
                events.append(DevEvent(source="github", event_type="pr_opened",
                    occurred_at=opened_at, actor_id=f"github:{author}",
                    entity_ref=ref, entity_id=str(pr_id),
                    payload={"title": f"[{label}] PR #{pr_id}", "team": team,
                             "additions": rng.randint(10, 400), "labels": [label]}))
                if rng.random() < 0.85 and merged_at <= datetime.now(timezone.utc):
                    events.append(DevEvent(source="github", event_type="pr_merged",
                        occurred_at=merged_at, actor_id=f"github:{author}",
                        entity_ref=ref, entity_id=str(pr_id),
                        payload={"title": f"[{label}] PR #{pr_id}", "team": team,
                                 "lead_time_hrs": round((merged_at - opened_at).total_seconds() / 3600, 2)}))
            current += timedelta(days=1)
        return events

    def _synthetic_commits(self, since):
        events, rng, n = [], random.Random(99), 0
        current = since
        while current < datetime.now(timezone.utc):
            for _ in range(rng.randint(8, 25)):
                n += 1
                events.append(DevEvent(source="github", event_type="commit_pushed",
                    occurred_at=current + timedelta(hours=rng.uniform(0, 20)),
                    actor_id=f"github:{rng.choice(USERS)}",
                    entity_ref=f"SHA:{n:08x}", entity_id=f"{n:08x}",
                    payload={"message": rng.choice([
                        "fix: resolve null pointer", "feat: add export endpoint",
                        "refactor: split service layer", "chore: update deps"])}))
            current += timedelta(days=1)
        return events

    def _synthetic_releases(self, since):
        events, rng, v = [], random.Random(7), [1, 4, 0]
        current = since
        while current < datetime.now(timezone.utc):
            if rng.random() < 0.6:
                v[2] += rng.randint(1, 3)
                tag = f"v{v[0]}.{v[1]}.{v[2]}"
                events.append(DevEvent(source="github", event_type="release_created",
                    occurred_at=current + timedelta(hours=13),
                    actor_id="github:ci-bot", entity_ref=tag, entity_id=tag,
                    payload={"name": f"Release {tag}", "prerelease": False}))
            current += timedelta(days=1)
        return events

    def _synthetic_action_runs(self, since):
        events, rng, run_id = [], random.Random(13), 90000
        current = since
        while current < datetime.now(timezone.utc):
            for _ in range(rng.randint(5, 15)):
                run_id += 1
                conclusion = rng.choices(["success","failure","cancelled"], weights=[80,15,5])[0]
                events.append(DevEvent(source="github", event_type="action_run_completed",
                    occurred_at=current + timedelta(hours=rng.uniform(0, 22)),
                    actor_id="github:ci-bot",
                    entity_ref=f"RUN#{run_id}", entity_id=str(run_id),
                    payload={"workflow": rng.choice(["CI","Deploy","Tests","Lint"]),
                             "conclusion": conclusion, "branch": "main"}))
            current += timedelta(days=1)
        return events


def _parse_dt(s):
    from dateutil.parser import parse
    dt = parse(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

def _delta_hours(start, end):
    return round((_parse_dt(end) - _parse_dt(start)).total_seconds() / 3600, 2)