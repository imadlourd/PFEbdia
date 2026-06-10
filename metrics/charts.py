"""
charts.py — Graphiques de tendance DORA
Génère 6 graphiques sur les N dernières semaines.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # sans interface graphique
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd

from lake import DataLake

COLORS = {
    "alpha": "#2ecc8f",
    "beta":  "#f5a623",
    "gamma": "#e05252",
    "delta": "#4f8ef7",
    "global": "#9b6cf7",
}

DORA_THRESHOLDS = {
    "lead_time":   {"elite": 1,  "high": 24,  "medium": 168},
    "cycle_time":  {"elite": 24, "high": 72,  "medium": 168},
    "deploy_freq": {"elite": 1,  "high": 0.14},
    "cfr":         {"elite": 5,  "high": 10,  "medium": 15},
    "mttr":        {"elite": 1,  "high": 24,  "medium": 168},
}


def generate_all(lake: DataLake, output_dir: str = "./charts", days: int = 42):
    """Génère tous les graphiques et les sauvegarde dans output_dir."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    files = []
    files.append(_chart_deployment_frequency(lake, output_dir, days))
    files.append(_chart_lead_time(lake, output_dir, days))
    files.append(_chart_cycle_time(lake, output_dir, days))
    files.append(_chart_cfr(lake, output_dir, days))
    files.append(_chart_mttr(lake, output_dir, days))
    files.append(_chart_team_radar(lake, output_dir, days))

    print(f"\n  {len(files)} graphiques generes dans {output_dir}/")
    for f in files:
        print(f"    └─ {f}")

    return files


# ── 1. Deployment Frequency ────────────────────────────────────────────────────

def _chart_deployment_frequency(lake, output_dir, days):
    df = lake.query(f"""
        SELECT
            DATE_TRUNC('day', occurred_at)::DATE AS day,
            COUNT(*) AS deployments
        FROM events
        WHERE event_type IN ('release_created', 'action_run_completed')
          AND occurred_at >= NOW() - INTERVAL '{days} days'
        GROUP BY 1
        ORDER BY 1
    """)

    fig, ax = plt.subplots(figsize=(10, 4))
    _style(fig, ax)

    if not df.empty:
        df["day"] = pd.to_datetime(df["day"])
        # rolling 7-day average
        df["rolling"] = df["deployments"].rolling(7, min_periods=1).mean()

        ax.bar(df["day"], df["deployments"], color=COLORS["global"],
               alpha=0.4, width=0.8, label="Deploiements/jour")
        ax.plot(df["day"], df["rolling"], color=COLORS["global"],
                linewidth=2, label="Moyenne mobile 7j")

        # DORA elite threshold line
        ax.axhline(y=1, color="#2ecc8f", linestyle="--",
                   linewidth=1, alpha=0.7, label="Seuil Elite (>=1/jour)")

    ax.set_title("Deployment Frequency", fontsize=13, fontweight="bold", pad=12)
    ax.set_xlabel("Date")
    ax.set_ylabel("Deploiements / jour")
    ax.legend(fontsize=9)
    _format_xaxis(ax)

    path = f"{output_dir}/01_deployment_frequency.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


# ── 2. Lead Time ───────────────────────────────────────────────────────────────

def _chart_lead_time(lake, output_dir, days):
    df = lake.query(f"""
        SELECT
            DATE_TRUNC('week', occurred_at)::DATE AS week,
            json_extract_string(payload, '$.team') AS team,
            MEDIAN(CAST(json_extract(payload, '$.lead_time_hrs') AS DOUBLE)) AS median_hrs
        FROM events
        WHERE event_type = 'pr_merged'
          AND occurred_at >= NOW() - INTERVAL '{days} days'
        GROUP BY 1, 2
        ORDER BY 1
    """)

    fig, ax = plt.subplots(figsize=(10, 4))
    _style(fig, ax)

    if not df.empty:
        df["week"] = pd.to_datetime(df["week"])
        for team in ["alpha", "beta", "delta", "gamma"]:
            sub = df[df["team"] == team]
            if not sub.empty:
                ax.plot(sub["week"], sub["median_hrs"],
                        marker="o", markersize=4,
                        color=COLORS.get(team, "#888"),
                        linewidth=2, label=team.capitalize())

        # DORA level lines
        ax.axhline(y=1,   color="#2ecc8f", linestyle="--", linewidth=1, alpha=0.6, label="Elite (<1h)")
        ax.axhline(y=24,  color="#f5a623", linestyle="--", linewidth=1, alpha=0.6, label="High (<24h)")
        ax.axhline(y=168, color="#e05252", linestyle="--", linewidth=1, alpha=0.6, label="Medium (<1sem)")

    ax.set_title("Lead Time for Change — par equipe (median hebdo)", fontsize=13, fontweight="bold", pad=12)
    ax.set_xlabel("Semaine")
    ax.set_ylabel("Lead Time (heures)")
    ax.legend(fontsize=9, ncol=2)
    _format_xaxis(ax)

    path = f"{output_dir}/02_lead_time.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


# ── 3. Cycle Time ──────────────────────────────────────────────────────────────

def _chart_cycle_time(lake, output_dir, days):
    df = lake.query(f"""
        SELECT
            DATE_TRUNC('week', occurred_at)::DATE AS week,
            json_extract_string(payload, '$.team') AS team,
            MEDIAN(CAST(json_extract(payload, '$.cycle_time_hrs') AS DOUBLE)) AS median_hrs
        FROM events
        WHERE event_type = 'ticket_closed'
          AND occurred_at >= NOW() - INTERVAL '{days} days'
        GROUP BY 1, 2
        ORDER BY 1
    """)

    fig, ax = plt.subplots(figsize=(10, 4))
    _style(fig, ax)

    if not df.empty:
        df["week"] = pd.to_datetime(df["week"])
        for team in ["alpha", "beta", "delta", "gamma"]:
            sub = df[df["team"] == team]
            if not sub.empty:
                ax.plot(sub["week"], sub["median_hrs"],
                        marker="s", markersize=4,
                        color=COLORS.get(team, "#888"),
                        linewidth=2, label=team.capitalize())

    ax.set_title("Cycle Time par ticket — par equipe (median hebdo)", fontsize=13, fontweight="bold", pad=12)
    ax.set_xlabel("Semaine")
    ax.set_ylabel("Cycle Time (heures)")
    ax.legend(fontsize=9)
    _format_xaxis(ax)

    path = f"{output_dir}/03_cycle_time.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


# ── 4. Change Failure Rate ─────────────────────────────────────────────────────

def _chart_cfr(lake, output_dir, days):
    df = lake.query(f"""
        WITH deploys AS (
            SELECT DATE_TRUNC('week', occurred_at)::DATE AS week,
                   COUNT(*) AS n_deploys
            FROM events
            WHERE event_type IN ('release_created', 'action_run_completed')
              AND occurred_at >= NOW() - INTERVAL '{days} days'
            GROUP BY 1
        ),
        incidents AS (
            SELECT DATE_TRUNC('week', occurred_at)::DATE AS week,
                   COUNT(*) AS n_incidents
            FROM events
            WHERE source = 'slack' AND event_type = 'incident_opened'
              AND occurred_at >= NOW() - INTERVAL '{days} days'
            GROUP BY 1
        )
        SELECT
            d.week,
            d.n_deploys,
            COALESCE(i.n_incidents, 0) AS n_incidents,
            ROUND(COALESCE(i.n_incidents, 0) * 100.0 / d.n_deploys, 2) AS cfr_pct
        FROM deploys d
        LEFT JOIN incidents i ON d.week = i.week
        ORDER BY d.week
    """)

    fig, ax = plt.subplots(figsize=(10, 4))
    _style(fig, ax)

    if not df.empty:
        df["week"] = pd.to_datetime(df["week"])
        ax.bar(df["week"], df["cfr_pct"], color=COLORS["gamma"],
               alpha=0.6, width=5, label="CFR %")
        ax.plot(df["week"], df["cfr_pct"], color=COLORS["gamma"],
                marker="o", markersize=4, linewidth=1.5)

        ax.axhline(y=5,  color="#2ecc8f", linestyle="--", linewidth=1, alpha=0.7, label="Elite (<5%)")
        ax.axhline(y=10, color="#f5a623", linestyle="--", linewidth=1, alpha=0.7, label="High (<10%)")
        ax.axhline(y=15, color="#e05252", linestyle="--", linewidth=1, alpha=0.7, label="Medium (<15%)")

    ax.set_title("Change Failure Rate (hebdomadaire)", fontsize=13, fontweight="bold", pad=12)
    ax.set_xlabel("Semaine")
    ax.set_ylabel("CFR (%)")
    ax.legend(fontsize=9)
    _format_xaxis(ax)

    path = f"{output_dir}/04_change_failure_rate.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


# ── 5. MTTR ────────────────────────────────────────────────────────────────────

def _chart_mttr(lake, output_dir, days):
    df = lake.query(f"""
        WITH opened AS (
            SELECT
                json_extract_string(payload, '$.incident_id') AS inc_id,
                occurred_at AS opened_at
            FROM events
            WHERE source = 'slack' AND event_type = 'incident_opened'
              AND occurred_at >= NOW() - INTERVAL '{days} days'
        ),
        resolved AS (
            SELECT
                json_extract_string(payload, '$.incident_id') AS inc_id,
                CAST(json_extract(payload, '$.mttr_hrs') AS DOUBLE) AS mttr_hrs,
                occurred_at AS resolved_at
            FROM events
            WHERE source = 'slack' AND event_type = 'incident_resolved'
              AND occurred_at >= NOW() - INTERVAL '{days} days'
        )
        SELECT
            DATE_TRUNC('week', o.opened_at)::DATE AS week,
            MEDIAN(r.mttr_hrs) AS median_mttr,
            COUNT(*) AS n_incidents
        FROM opened o
        JOIN resolved r ON o.inc_id = r.inc_id
        GROUP BY 1
        ORDER BY 1
    """)

    fig, ax1 = plt.subplots(figsize=(10, 4))
    _style(fig, ax1)

    if not df.empty:
        df["week"] = pd.to_datetime(df["week"])

        ax1.bar(df["week"], df["median_mttr"], color=COLORS["delta"],
                alpha=0.5, width=5, label="MTTR median (h)")
        ax1.plot(df["week"], df["median_mttr"], color=COLORS["delta"],
                 marker="o", markersize=4, linewidth=2)

        ax1.axhline(y=1,  color="#2ecc8f", linestyle="--", linewidth=1, alpha=0.7, label="Elite (<1h)")
        ax1.axhline(y=24, color="#f5a623", linestyle="--", linewidth=1, alpha=0.7, label="High (<24h)")

        ax2 = ax1.twinx()
        ax2.plot(df["week"], df["n_incidents"], color="#888",
                 marker="x", markersize=5, linewidth=1, linestyle=":", label="Nb incidents")
        ax2.set_ylabel("Nb incidents", color="#888", fontsize=9)
        ax2.tick_params(axis="y", labelcolor="#888")

    ax1.set_title("MTTR — Mean Time To Restore (hebdomadaire)", fontsize=13, fontweight="bold", pad=12)
    ax1.set_xlabel("Semaine")
    ax1.set_ylabel("MTTR median (heures)")
    ax1.legend(fontsize=9, loc="upper left")
    _format_xaxis(ax1)

    path = f"{output_dir}/05_mttr.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


# ── 6. Radar par équipe ────────────────────────────────────────────────────────

def _chart_team_radar(lake, output_dir, days):
    import numpy as np

    teams = ["alpha", "beta", "delta", "gamma"]

    # collect scores per team (0-100, higher = better)
    scores = {}
    for team in teams:
        lt = lake.query(f"""
            SELECT MEDIAN(CAST(json_extract(payload, '$.lead_time_hrs') AS DOUBLE)) AS v
            FROM events WHERE event_type='pr_merged'
            AND json_extract_string(payload, '$.team')='{team}'
            AND occurred_at >= NOW() - INTERVAL '{days} days'
        """).iloc[0]["v"] or 999

        ct = lake.query(f"""
            SELECT MEDIAN(CAST(json_extract(payload, '$.cycle_time_hrs') AS DOUBLE)) AS v
            FROM events WHERE event_type='ticket_closed'
            AND json_extract_string(payload, '$.team')='{team}'
            AND occurred_at >= NOW() - INTERVAL '{days} days'
        """).iloc[0]["v"] or 999

        blk = lake.query(f"""
            SELECT COUNT(*) AS v FROM events
            WHERE event_type='ticket_transitioned'
            AND json_extract_string(payload, '$.to_status')='Blocked'
            AND occurred_at >= NOW() - INTERVAL '{days} days'
        """).iloc[0]["v"] or 0

        # normalize to 0-100 score (higher = better)
        scores[team] = [
            max(0, 100 - (lt  / 48  * 100)),   # lead time  (48h = 0 score)
            max(0, 100 - (ct  / 120 * 100)),   # cycle time (120h = 0 score)
            max(0, 100 - (blk / 20  * 100)),   # low blocked tickets
        ]

    categories  = ["Lead Time", "Cycle Time", "Peu de blocages"]
    N           = len(categories)
    angles      = [n / float(N) * 2 * np.pi for n in range(N)]
    angles     += angles[:1]

    fig, ax = plt.subplots(figsize=(7, 7), subplot_kw={"polar": True})
    fig.patch.set_facecolor("#0d0f14")
    ax.set_facecolor("#141720")
    ax.tick_params(colors="#7a8099")
    ax.spines["polar"].set_color("#2a2f42")

    for team in teams:
        vals  = scores[team] + scores[team][:1]
        ax.plot(angles, vals, linewidth=2, color=COLORS.get(team, "#888"), label=team.capitalize())
        ax.fill(angles, vals, alpha=0.08, color=COLORS.get(team, "#888"))

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(categories, color="#e2e6f0", fontsize=10)
    ax.set_ylim(0, 100)
    ax.set_yticks([25, 50, 75, 100])
    ax.set_yticklabels(["25", "50", "75", "100"], color="#7a8099", fontsize=8)
    ax.grid(color="#2a2f42", linewidth=0.5)

    ax.set_title("Performance par equipe — Score normalise", fontsize=12,
                 fontweight="bold", color="#e2e6f0", pad=20)
    ax.legend(loc="upper right", bbox_to_anchor=(1.3, 1.1),
              fontsize=9, labelcolor="#e2e6f0",
              facecolor="#141720", edgecolor="#2a2f42")

    path = f"{output_dir}/06_team_radar.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


# ── Style helpers ──────────────────────────────────────────────────────────────

def _style(fig, ax):
    fig.patch.set_facecolor("#0d0f14")
    ax.set_facecolor("#141720")
    ax.tick_params(colors="#7a8099", labelsize=9)
    ax.xaxis.label.set_color("#7a8099")
    ax.yaxis.label.set_color("#7a8099")
    ax.title.set_color("#e2e6f0")
    for spine in ax.spines.values():
        spine.set_edgecolor("#2a2f42")
    ax.grid(color="#2a2f42", linewidth=0.5, alpha=0.7)

def _format_xaxis(ax):
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=30, ha="right")