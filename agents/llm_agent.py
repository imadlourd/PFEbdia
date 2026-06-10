"""
LLM Agent — W6
==============
Agent réel basé sur Ollama (llama3.2:3b) avec tool-use simulé.
100% local, aucune clé API requise.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone

import ollama

from lake  import DataLake
from graph import KnowledgeGraph

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Tu es DevInsight, un agent d'analyse de performance pour equipes de developpement logiciel.

Tu as acces a des donnees reelles de GitHub, Jira et Slack sur 42 jours.
Tu as aussi acces a un graphe de connaissances reliant developpeurs, pull requests, tickets et incidents.

Regles strictes :
1. Reponds UNIQUEMENT en JSON valide, sans texte avant ou apres.
2. Si tu as besoin de donnees, utilise un outil via le format JSON ci-dessous.
3. Si tu as la reponse, retourne-la directement.
4. Reponds toujours en francais dans le champ "answer".
5. Ne jamais inventer de chiffres — utilise toujours les outils.

Pour appeler un outil, reponds exactement :
{"tool": "NOM_OUTIL", "args": {"param": "valeur"}}

Outils disponibles :
- get_team_stats : args = {"team": "alpha|beta|delta|gamma", "days": "14"}
- query_metrics  : args = {"sql": "SELECT ... FROM events LIMIT 10"}
- query_graph    : args = {"query_type": "stats|developer|team"}

Pour donner une reponse finale :
{"answer": "Ta reponse en francais ici", "sources": ["outil1", "outil2"]}

Date : """ + datetime.now(timezone.utc).strftime("%Y-%m-%d")


class DevInsightAgent:

    def __init__(self, lake: DataLake, kg: KnowledgeGraph):
        self.lake    = lake
        self.kg      = kg
        self.model   = "llama3.2:3b"
        self.history = []

    def ask(self, question: str) -> dict:
        logger.info(f"[Agent] Question: {question}")

        self.history = [
            {"role": "system",  "content": SYSTEM_PROMPT},
            {"role": "user",    "content": question},
        ]

        tool_calls = []
        sources    = []

        for round_n in range(5):
            response = ollama.chat(
                model=self.model,
                messages=self.history,
                options={"temperature": 0.1},
            )
            content = response["message"]["content"].strip()
            logger.info(f"[Agent] Round {round_n+1}: {content[:120]}")

            # parse JSON response
            parsed = _parse_json(content)
            if not parsed:
                # try to extract answer from free text
                return {
                    "answer":     content,
                    "sources":    sources,
                    "tool_calls": tool_calls,
                }

            # tool call
            if "tool" in parsed:
                tool_name = parsed["tool"]
                args      = parsed.get("args", {})
                logger.info(f"[Agent] Tool: {tool_name}({args})")
                tool_calls.append({"tool": tool_name, "args": args})
                sources.append(tool_name)

                result = self._execute_tool(tool_name, args)

                self.history.append({"role": "assistant", "content": content})
                self.history.append({
                    "role": "user",
                    "content": f"Resultat de l'outil {tool_name}: {json.dumps(result, default=str)[:2000]}\n\nMaintenant reponds a la question originale en JSON avec le champ 'answer'."
                })
                continue

            # final answer
            if "answer" in parsed:
                return {
                    "answer":     parsed["answer"],
                    "sources":    list(set(sources)) or ["ollama:llama3.2:3b"],
                    "tool_calls": tool_calls,
                }

            # fallback
            return {
                "answer":     content,
                "sources":    sources,
                "tool_calls": tool_calls,
            }

        return {
            "answer":     "Je n'ai pas pu obtenir une reponse complete.",
            "sources":    sources,
            "tool_calls": tool_calls,
        }

    def _execute_tool(self, name: str, args: dict) -> dict:
        try:
            if name == "query_metrics":
                sql = args.get("sql", "SELECT 1")
                df  = self.lake.query(sql)
                return df.to_dict(orient="records")[:15]

            elif name == "query_graph":
                qt = args.get("query_type", "stats")
                if qt == "stats":
                    return self.kg.stats()
                elif qt == "developer":
                    return self.kg.get_developer_stats(args.get("name", ""))
                elif qt == "team":
                    return self.kg.get_team_graph(args.get("team", ""))
                return {"error": "query_type inconnu"}

            elif name == "get_team_stats":
                team = args.get("team", "alpha")
                days = int(str(args.get("days", "14")))

                def q(sql):
                    return self.lake.query(sql).iloc[0]["v"]

                return {
                    "team":              team,
                    "days":              days,
                    "lead_time_median":  round(float(q(f"SELECT MEDIAN(CAST(json_extract(payload,'$.lead_time_hrs') AS DOUBLE)) AS v FROM events WHERE event_type='pr_merged' AND json_extract_string(payload,'$.team')='{team}' AND occurred_at>=NOW()-INTERVAL '{days} days'") or 0), 1),
                    "cycle_time_median": round(float(q(f"SELECT MEDIAN(CAST(json_extract(payload,'$.cycle_time_hrs') AS DOUBLE)) AS v FROM events WHERE event_type='ticket_closed' AND json_extract_string(payload,'$.team')='{team}' AND occurred_at>=NOW()-INTERVAL '{days} days'") or 0), 1),
                    "prs_merged":        int(q(f"SELECT COUNT(*) AS v FROM events WHERE event_type='pr_merged' AND json_extract_string(payload,'$.team')='{team}' AND occurred_at>=NOW()-INTERVAL '{days} days'")),
                    "blocked_tickets":   int(q(f"SELECT COUNT(*) AS v FROM events WHERE event_type='ticket_transitioned' AND json_extract_string(payload,'$.to_status')='Blocked' AND occurred_at>=NOW()-INTERVAL '{days} days'")),
                }

        except Exception as e:
            logger.error(f"[Agent] Tool error: {e}")
            return {"error": str(e)}

        return {"error": "outil inconnu"}


def _parse_json(text: str) -> dict | None:
    """Extract JSON from text, handling markdown code blocks."""
    # strip markdown
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*", "", text)
    text = text.strip()

    # try direct parse
    try:
        return json.loads(text)
    except Exception:
        pass

    # try to find JSON object in text
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except Exception:
            pass

    return None