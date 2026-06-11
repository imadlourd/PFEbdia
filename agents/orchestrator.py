"""
Orchestrateur W7
================
Decompose les questions complexes en sous-taches,
dispatche vers les agents specialises, fusionne les reponses.

Question cible :
  'Pourquoi l equipe Gamma est-elle en retard et quelles actions recommandes-tu ?'
  -> necessite : metriques DORA + graphe + analyse causale + recommandations
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

import ollama

from lake  import DataLake
from graph import KnowledgeGraph

logger = logging.getLogger(__name__)

ORCHESTRATOR_PROMPT = """Tu es un orchestrateur d'analyse DevOps. Tu recois une question complexe et tu dois la decomposer en sous-taches.

Reponds UNIQUEMENT en JSON valide avec ce format exact :
{
  "subtasks": [
    {"id": "1", "description": "description courte", "tool": "NOM_OUTIL", "args": {...}},
    {"id": "2", "description": "description courte", "tool": "NOM_OUTIL", "args": {...}}
  ],
  "synthesis_instruction": "Comment fusionner les resultats pour repondre a la question"
}

Outils disponibles :
- get_team_stats : {"team": "alpha|beta|delta|gamma", "days": "14"}
- query_metrics  : {"sql": "SELECT ... FROM events LIMIT 10"}
- query_graph    : {"query_type": "stats|team", "team": "gamma"}
- get_incidents  : {"days": "14"}
- get_blocked    : {"days": "14"}

Decompose en 2-4 sous-taches maximum. Sois precis et concis.
Date : """ + datetime.now(timezone.utc).strftime("%Y-%m-%d")

SYNTHESIS_PROMPT = """Tu es DevInsight, un agent d'analyse de performance DevOps.

On t'a pose cette question : {question}

Voici les donnees collectees :
{data}

Instruction de synthese : {instruction}

Reponds en JSON :
{{"answer": "Ta reponse complete en francais, structuree et actionnable", "sources": ["liste des sources utilisees"]}}

Regles :
- Reponds en francais
- Cite les chiffres exacts des donnees fournies
- Propose des recommandations concretes si pertinent
- Distingue correlation et causalite
- Sois concis (max 200 mots)"""


class Orchestrator:

    def __init__(self, lake: DataLake, kg: KnowledgeGraph):
        self.lake  = lake
        self.kg    = kg
        self.model = "llama3.2:3b"

    def ask(self, question: str) -> dict:
        logger.info(f"[Orchestrator] Question: {question}")

        # Step 1: decompose
        subtasks = self._decompose(question)
        logger.info(f"[Orchestrator] {len(subtasks['subtasks'])} subtasks")

        # Step 2: execute each subtask
        results = []
        sources = []
        for task in subtasks["subtasks"]:
            logger.info(f"[Orchestrator] Subtask {task['id']}: {task['description']}")
            result = self._execute_tool(task["tool"], task.get("args", {}))
            results.append({
                "subtask":     task["description"],
                "tool":        task["tool"],
                "result":      result,
            })
            sources.append(task["tool"])

        # Step 3: synthesize
        answer = self._synthesize(
            question=question,
            results=results,
            instruction=subtasks.get("synthesis_instruction", "Synthesise les resultats."),
        )

        return {
            "answer":     answer,
            "sources":    list(set(sources)),
            "subtasks":   subtasks["subtasks"],
            "raw_results": results,
        }

    # ── Decomposition ──────────────────────────────────────────────────────────

    def _decompose(self, question: str) -> dict:
        response = ollama.chat(
            model=self.model,
            messages=[
                {"role": "system", "content": ORCHESTRATOR_PROMPT},
                {"role": "user",   "content": f"Decompose cette question : {question}"},
            ],
            options={"temperature": 0.1},
        )
        content = response["message"]["content"].strip()
        logger.info(f"[Orchestrator] Decomposition: {content[:200]}")

        parsed = _parse_json(content)
        if parsed and "subtasks" in parsed:
            return parsed

        # fallback: single task
        return {
            "subtasks": [
                {"id": "1", "description": question,
                 "tool": "query_metrics",
                 "args": {"sql": "SELECT source, COUNT(*) AS n FROM events GROUP BY 1 LIMIT 5"}}
            ],
            "synthesis_instruction": "Reponds directement a la question."
        }

    # ── Synthesis ──────────────────────────────────────────────────────────────

    def _synthesize(self, question: str, results: list, instruction: str) -> str:
        data_str = json.dumps(results, default=str, ensure_ascii=False)[:3000]

        prompt = SYNTHESIS_PROMPT.format(
            question=question,
            data=data_str,
            instruction=instruction,
        )

        response = ollama.chat(
            model=self.model,
            messages=[
                {"role": "user", "content": prompt},
            ],
            options={"temperature": 0.2},
        )
        content = response["message"]["content"].strip()
        logger.info(f"[Orchestrator] Synthesis: {content[:200]}")

        parsed = _parse_json(content)
        if parsed and "answer" in parsed:
            return parsed["answer"]

        return content

    # ── Tool execution ─────────────────────────────────────────────────────────

    def _execute_tool(self, name: str, args: dict) -> dict:
        try:
            if name == "get_team_stats":
                team = args.get("team", "alpha")
                days = int(str(args.get("days", "14")))
                def q(sql): return self.lake.query(sql).iloc[0]["v"]
                return {
                    "team":              team,
                    "lead_time_median":  round(float(q(f"SELECT MEDIAN(CAST(json_extract(payload,'$.lead_time_hrs') AS DOUBLE)) AS v FROM events WHERE event_type='pr_merged' AND json_extract_string(payload,'$.team')='{team}' AND occurred_at>=NOW()-INTERVAL '{days} days'") or 0), 1),
                    "cycle_time_median": round(float(q(f"SELECT MEDIAN(CAST(json_extract(payload,'$.cycle_time_hrs') AS DOUBLE)) AS v FROM events WHERE event_type='ticket_closed' AND json_extract_string(payload,'$.team')='{team}' AND occurred_at>=NOW()-INTERVAL '{days} days'") or 0), 1),
                    "prs_merged":        int(q(f"SELECT COUNT(*) AS v FROM events WHERE event_type='pr_merged' AND json_extract_string(payload,'$.team')='{team}' AND occurred_at>=NOW()-INTERVAL '{days} days'")),
                    "blocked_tickets":   int(q(f"SELECT COUNT(*) AS v FROM events WHERE event_type='ticket_transitioned' AND json_extract_string(payload,'$.to_status')='Blocked' AND occurred_at>=NOW()-INTERVAL '{days} days'")),
                }

            elif name == "query_metrics":
                sql = args.get("sql", "SELECT 1")
                df  = self.lake.query(sql)
                return df.to_dict(orient="records")[:15]

            elif name == "query_graph":
                qt = args.get("query_type", "stats")
                if qt == "stats":
                    return self.kg.stats()
                elif qt == "team":
                    return self.kg.get_team_graph(args.get("team", ""))
                return self.kg.stats()

            elif name == "get_incidents":
                days = int(str(args.get("days", "14")))
                df   = self.lake.query(f"""
                    SELECT event_type, COUNT(*) AS n
                    FROM events
                    WHERE source='slack'
                    AND event_type IN ('incident_opened','incident_resolved')
                    AND occurred_at >= NOW()-INTERVAL '{days} days'
                    GROUP BY 1
                """)
                mttr = self.lake.mttr(days=days)
                return {
                    "incidents": df.to_dict(orient="records"),
                    "mttr_median_hrs": mttr.get("mttr_median_hrs"),
                    "n_incidents": mttr.get("n_incidents"),
                }

            elif name == "get_blocked":
                days = int(str(args.get("days", "14")))
                df   = self.lake.blocked_tickets(days=days)
                return {
                    "n_blocked": len(df),
                    "sample":    df.head(5).to_dict(orient="records"),
                }

        except Exception as e:
            logger.error(f"[Orchestrator] Tool error {name}: {e}")
            return {"error": str(e)}

        return {"error": "outil inconnu"}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse_json(text: str) -> dict | None:
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*",     "", text)
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except Exception:
            pass
    return None