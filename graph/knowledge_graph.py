"""
Knowledge Graph — W5
====================
Construit un graphe de connaissances depuis le data lake.

Entites : Developer, PullRequest, Ticket, Sprint, Incident
Relations : OWNS, REVIEWS, CLOSES, BLOCKS, BELONGS_TO, CAUSED_BY
"""
from __future__ import annotations

import json
import re
import logging
from dataclasses import dataclass, field
from typing import Optional

import networkx as nx

from lake import DataLake

logger = logging.getLogger(__name__)


# ── Entites ────────────────────────────────────────────────────────────────────

@dataclass
class Node:
    id:         str
    type:       str
    properties: dict = field(default_factory=dict)

    def __hash__(self):  return hash(self.id)
    def __eq__(self, o): return self.id == o.id


@dataclass
class Edge:
    source:     str
    target:     str
    relation:   str
    properties: dict = field(default_factory=dict)


# ── Knowledge Graph ────────────────────────────────────────────────────────────

class KnowledgeGraph:
    """
    Graphe de connaissances DevOps.
    Construit depuis le data lake Parquet via DuckDB.
    """

    def __init__(self, lake: DataLake):
        self.lake = lake
        self.G    = nx.DiGraph()
        self._nodes: dict[str, Node] = {}
        self._edges: list[Edge]      = []

    # ── Construction ──────────────────────────────────────────────────────────

    def build(self) -> "KnowledgeGraph":
        """Build the full graph from the data lake."""
        logger.info("[KG] Building knowledge graph...")

        self._extract_developers()
        self._extract_pull_requests()
        self._extract_tickets()
        self._extract_sprints()
        self._extract_incidents()
        self._extract_relations()

        logger.info(
            f"[KG] Graph built: {self.G.number_of_nodes()} nodes, "
            f"{self.G.number_of_edges()} edges"
        )
        return self

    def _add_node(self, node: Node):
        self._nodes[node.id] = node
        self.G.add_node(node.id, type=node.type, **node.properties)

    def _add_edge(self, edge: Edge):
        if edge.source in self._nodes and edge.target in self._nodes:
            self._edges.append(edge)
            self.G.add_edge(edge.source, edge.target,
                           relation=edge.relation, **edge.properties)

    # ── Extracteurs de noeuds ─────────────────────────────────────────────────

    def _extract_developers(self):
        df = self.lake.query("""
            SELECT DISTINCT actor_id,
                   COUNT(*) AS activity_count
            FROM events
            WHERE actor_id IS NOT NULL
              AND actor_id NOT IN ('github:ci-bot', 'jira:system')
            GROUP BY actor_id
            ORDER BY activity_count DESC
        """)
        for _, row in df.iterrows():
            actor = row["actor_id"]
            source, name = actor.split(":", 1) if ":" in actor else ("unknown", actor)
            node_id = f"dev:{name}"
            self._add_node(Node(
                id=node_id, type="Developer",
                properties={
                    "name":           name,
                    "source":         source,
                    "activity_count": int(row["activity_count"]),
                }
            ))

    def _extract_pull_requests(self):
        df = self.lake.query("""
            SELECT entity_id AS pr_id,
                   entity_ref AS pr_ref,
                   actor_id,
                   occurred_at,
                   payload
            FROM events
            WHERE event_type = 'pr_merged'
            ORDER BY occurred_at DESC
            LIMIT 200
        """)
        for _, row in df.iterrows():
            payload = _parse_payload(row["payload"])
            pr_id   = f"pr:{row['pr_id']}"
            self._add_node(Node(
                id=pr_id, type="PullRequest",
                properties={
                    "ref":            row["pr_ref"],
                    "title":          payload.get("title", ""),
                    "team":           payload.get("team", "unknown"),
                    "lead_time_hrs":  payload.get("lead_time_hrs", 0),
                    "merged_at":      str(row["occurred_at"]),
                }
            ))
            # OWNS relation
            actor  = row["actor_id"]
            _, name = actor.split(":", 1) if ":" in actor else ("unknown", actor)
            dev_id  = f"dev:{name}"
            self._add_edge(Edge(
                source=dev_id, target=pr_id,
                relation="OWNS",
                properties={"since": str(row["occurred_at"])}
            ))

    def _extract_tickets(self):
        df = self.lake.query("""
            SELECT entity_id AS ticket_id,
                   entity_ref AS ticket_ref,
                   actor_id,
                   occurred_at,
                   payload
            FROM events
            WHERE event_type = 'ticket_closed'
            ORDER BY occurred_at DESC
            LIMIT 300
        """)
        for _, row in df.iterrows():
            payload   = _parse_payload(row["payload"])
            ticket_id = f"ticket:{row['ticket_id']}"
            self._add_node(Node(
                id=ticket_id, type="Ticket",
                properties={
                    "ref":            row["ticket_ref"],
                    "summary":        payload.get("summary", ""),
                    "issue_type":     payload.get("issue_type", ""),
                    "priority":       payload.get("priority", ""),
                    "team":           payload.get("team", "unknown"),
                    "cycle_time_hrs": payload.get("cycle_time_hrs", 0),
                    "story_points":   payload.get("story_points", 0),
                }
            ))
            # OWNS relation
            actor  = row["actor_id"]
            _, name = actor.split(":", 1) if ":" in actor else ("unknown", actor)
            dev_id  = f"dev:{name}"
            self._add_edge(Edge(
                source=dev_id, target=ticket_id,
                relation="OWNS",
                properties={"role": "assignee"}
            ))

    def _extract_sprints(self):
        df = self.lake.query("""
            SELECT DISTINCT entity_id AS sprint_id,
                   entity_ref AS sprint_name,
                   occurred_at
            FROM events
            WHERE event_type = 'sprint_started'
            ORDER BY occurred_at DESC
            LIMIT 10
        """)
        for _, row in df.iterrows():
            sprint_id = f"sprint:{row['sprint_id']}"
            self._add_node(Node(
                id=sprint_id, type="Sprint",
                properties={
                    "name":       row["sprint_name"],
                    "started_at": str(row["occurred_at"]),
                }
            ))

    def _extract_incidents(self):
        df = self.lake.query("""
            SELECT entity_id AS inc_id,
                   actor_id,
                   occurred_at,
                   payload
            FROM events
            WHERE source = 'slack'
              AND event_type = 'incident_opened'
            ORDER BY occurred_at DESC
            LIMIT 100
        """)
        for _, row in df.iterrows():
            payload = _parse_payload(row["payload"])
            inc_id  = f"incident:{payload.get('incident_id', row['inc_id'])}"
            self._add_node(Node(
                id=inc_id, type="Incident",
                properties={
                    "severity":   payload.get("severity", "P3"),
                    "opened_at":  str(row["occurred_at"]),
                    "channel":    payload.get("channel", "#incidents"),
                }
            ))

    # ── Extracteurs de relations ───────────────────────────────────────────────

    def _extract_relations(self):
        self._link_prs_to_tickets()
        self._link_tickets_to_sprints()
        self._link_incidents_to_deploys()
        self._link_blocked_tickets()

    def _link_prs_to_tickets(self):
        """
        CLOSES relation : PR -> Ticket
        Heuristique : cherche des patterns comme PROJ-42, #42
        dans le titre de la PR.
        """
        ticket_pattern = re.compile(r'(PROJ-\d+|#\d+)', re.IGNORECASE)
        linked = 0
        for node_id, data in self.G.nodes(data=True):
            if data.get("type") != "PullRequest":
                continue
            title = data.get("title", "")
            matches = ticket_pattern.findall(title)
            for match in matches:
                # normalize: #42 -> PROJ-42
                if match.startswith("#"):
                    ticket_ref = f"PROJ-{match[1:]}"
                else:
                    ticket_ref = match.upper()
                ticket_id = f"ticket:{ticket_ref}"
                if ticket_id in self._nodes:
                    self._add_edge(Edge(
                        source=node_id, target=ticket_id,
                        relation="CLOSES",
                        properties={"method": "title_ner"}
                    ))
                    linked += 1
        logger.info(f"[KG] CLOSES relations extracted: {linked}")

    def _link_tickets_to_sprints(self):
        """BELONGS_TO : Ticket -> Sprint (par date de fermeture)"""
        sprint_nodes = [
            (nid, data) for nid, data in self.G.nodes(data=True)
            if data.get("type") == "Sprint"
        ]
        if not sprint_nodes:
            return

        # Get latest sprint
        latest_sprint = sorted(
            sprint_nodes,
            key=lambda x: x[1].get("started_at", ""),
            reverse=True
        )[0]

        ticket_nodes = [
            nid for nid, data in self.G.nodes(data=True)
            if data.get("type") == "Ticket"
        ]
        linked = 0
        for ticket_id in ticket_nodes[:50]:  # limit for prototype
            self._add_edge(Edge(
                source=ticket_id,
                target=latest_sprint[0],
                relation="BELONGS_TO",
                properties={"method": "temporal_heuristic"}
            ))
            linked += 1
        logger.info(f"[KG] BELONGS_TO relations extracted: {linked}")

    def _link_incidents_to_deploys(self):
        """CAUSED_BY : Incident -> Deploy (correlation temporelle)"""
        df = self.lake.query("""
            WITH deploys AS (
                SELECT entity_id, occurred_at AS deploy_at
                FROM events
                WHERE event_type IN ('release_created', 'action_run_completed')
                ORDER BY occurred_at DESC
                LIMIT 50
            ),
            incidents AS (
                SELECT json_extract_string(payload, '$.incident_id') AS inc_id,
                       occurred_at AS inc_at
                FROM events
                WHERE source = 'slack' AND event_type = 'incident_opened'
                ORDER BY occurred_at DESC
                LIMIT 50
            )
            SELECT i.inc_id, d.entity_id AS deploy_id,
                   ABS(EPOCH(i.inc_at) - EPOCH(d.deploy_at)) AS delta_s
            FROM incidents i
            CROSS JOIN deploys d
            WHERE ABS(EPOCH(i.inc_at) - EPOCH(d.deploy_at)) < 7200
            ORDER BY delta_s
            LIMIT 30
        """)
        linked = 0
        for _, row in df.iterrows():
            inc_id    = f"incident:INC-{row['inc_id']}"
            deploy_id = f"deploy:{row['deploy_id']}"
            if inc_id in self._nodes:
                if deploy_id not in self._nodes:
                    self._add_node(Node(
                        id=deploy_id, type="Deploy",
                        properties={"deploy_id": str(row["deploy_id"])}
                    ))
                self._add_edge(Edge(
                    source=inc_id, target=deploy_id,
                    relation="CAUSED_BY",
                    properties={"delta_seconds": int(row["delta_s"])}
                ))
                linked += 1
        logger.info(f"[KG] CAUSED_BY relations extracted: {linked}")

    def _link_blocked_tickets(self):
        """BLOCKS : Ticket -> Ticket (via transitions Jira)"""
        df = self.lake.query("""
            SELECT entity_id AS ticket_id, occurred_at
            FROM events
            WHERE event_type = 'ticket_transitioned'
              AND json_extract_string(payload, '$.to_status') = 'Blocked'
            LIMIT 50
        """)
        linked = 0
        ticket_ids = list(df["ticket_id"]) if not df.empty else []
        for i in range(len(ticket_ids) - 1):
            src = f"ticket:{ticket_ids[i]}"
            tgt = f"ticket:{ticket_ids[i+1]}"
            if src in self._nodes and tgt in self._nodes:
                self._add_edge(Edge(
                    source=src, target=tgt,
                    relation="BLOCKS",
                    properties={"method": "transition_sequence"}
                ))
                linked += 1
        logger.info(f"[KG] BLOCKS relations extracted: {linked}")

    # ── Requetes sur le graphe ────────────────────────────────────────────────

    def get_developer_stats(self, name: str) -> dict:
        dev_id = f"dev:{name}"
        if dev_id not in self.G:
            return {"error": f"Developer '{name}' not found"}
        neighbors = list(self.G.successors(dev_id))
        prs      = [n for n in neighbors if n.startswith("pr:")]
        tickets  = [n for n in neighbors if n.startswith("ticket:")]
        return {
            "developer":   name,
            "prs_owned":   len(prs),
            "tickets_owned": len(tickets),
            "total_activity": self.G.nodes[dev_id].get("activity_count", 0),
        }

    def get_team_graph(self, team: str) -> dict:
        nodes = [
            (nid, data) for nid, data in self.G.nodes(data=True)
            if data.get("team") == team
        ]
        return {
            "team":     team,
            "n_nodes":  len(nodes),
            "entities": [{"id": nid, "type": data.get("type")} for nid, data in nodes[:10]],
        }

    def shortest_path(self, source_id: str, target_id: str) -> list:
        try:
            return nx.shortest_path(self.G, source_id, target_id)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return []

    def stats(self) -> dict:
        types = {}
        for _, data in self.G.nodes(data=True):
            t = data.get("type", "Unknown")
            types[t] = types.get(t, 0) + 1
        rels = {}
        for _, _, data in self.G.edges(data=True):
            r = data.get("relation", "UNKNOWN")
            rels[r] = rels.get(r, 0) + 1
        return {
            "n_nodes":       self.G.number_of_nodes(),
            "n_edges":       self.G.number_of_edges(),
            "nodes_by_type": types,
            "edges_by_rel":  rels,
        }

    def to_dict(self) -> dict:
        return {
            "nodes": [
                {"id": nid, "type": data.get("type"), **{k:v for k,v in data.items() if k != "type"}}
                for nid, data in self.G.nodes(data=True)
            ],
            "edges": [
                {"source": s, "target": t, "relation": data.get("relation")}
                for s, t, data in self.G.edges(data=True)
            ]
        }


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse_payload(p) -> dict:
    if not p:
        return {}
    if isinstance(p, dict):
        return p
    try:
        return json.loads(p)
    except Exception:
        return {}