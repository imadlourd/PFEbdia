"""
Dashboard W4 — DevInsight
Interface de monitoring DORA + agent de chat simulé
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
from datetime import datetime, timezone

from lake import DataLake
from connectors import GitHubConnector, JiraConnector, SlackConnector

# ── Config ────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="DevInsight — PFE",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    [data-testid="stAppViewContainer"] { background: #0d0f14; }
    [data-testid="stSidebar"]          { background: #141720; }
    .main-header {
        font-size: 1.6rem; font-weight: 700;
        color: #4f8ef7; margin-bottom: 0.2rem;
    }
    .sub-header { color: #7a8099; font-size: 0.9rem; margin-bottom: 1.5rem; }
    .kpi-card {
        background: #141720; border: 1px solid #2a2f42;
        border-radius: 8px; padding: 1rem;
        border-top: 3px solid var(--c);
    }
    .kpi-label { color: #7a8099; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; }
    .kpi-value { color: #e2e6f0; font-size: 1.8rem; font-weight: 700; font-family: monospace; }
    .kpi-delta { font-size: 0.8rem; margin-top: 0.2rem; }
    .up   { color: #2ecc8f; }
    .down { color: #e05252; }
    .pill {
        display: inline-block; padding: 2px 10px;
        border-radius: 12px; font-size: 0.75rem; font-weight: 600;
    }
    .pill-elite  { background: rgba(46,204,143,.15); color: #2ecc8f; }
    .pill-high   { background: rgba(79,142,247,.15); color: #4f8ef7; }
    .pill-medium { background: rgba(245,166,35,.15);  color: #f5a623; }
    .pill-low    { background: rgba(224,82,82,.15);   color: #e05252; }
    .chat-msg-user  { background:#1c2440; border-radius:8px; padding:10px 14px; margin:6px 0; }
    .chat-msg-agent { background:#141720; border:1px solid #2a2f42; border-radius:8px; padding:10px 14px; margin:6px 0; }
    .chat-source { color:#7a8099; font-size:0.75rem; margin-top:4px; }
    .stButton>button {
        background:#4f8ef7; color:#fff; border:none;
        border-radius:6px; padding:0.4rem 1.2rem;
    }
</style>
""", unsafe_allow_html=True)

COLORS = {
    "alpha": "#2ecc8f",
    "beta":  "#f5a623",
    "gamma": "#e05252",
    "delta": "#4f8ef7",
}

# ── Data loading ──────────────────────────────────────────────────────────────

@st.cache_resource
def get_lake():
    lake_path = Path.home() / "pfe" / "lake"
    lake = DataLake(str(lake_path))
    # ingest if empty
    try:
        s = lake.stats()
        if s.get("status") == "empty" or s.get("total_events", 0) == 0:
            _ingest(lake)
    except Exception:
        _ingest(lake)
    return lake

def _ingest(lake):
    gh    = GitHubConnector.synthetic()
    jira  = JiraConnector.synthetic()
    slack = SlackConnector.synthetic()
    events = gh.fetch_all(42) + jira.fetch_all(42) + slack.fetch_all(42)
    lake.ingest(events)

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("### ⚙️ Filtres")
    days = st.slider("Fenetre temporelle (jours)", 7, 42, 14, step=7)
    teams_all = ["alpha", "beta", "delta", "gamma"]
    teams_sel = st.multiselect("Equipes", teams_all, default=teams_all)
    st.markdown("---")
    st.markdown("### 📡 Sources")
    st.markdown("🟢 GitHub")
    st.markdown("🟢 Jira")
    st.markdown("🟢 Slack")
    st.markdown("---")
    st.markdown("### 🗂 Navigation")
    page = st.radio("", ["Vue d'ensemble", "Metriques DORA", "Equipes", "Agent IA"], label_visibility="collapsed")

lake = get_lake()

# ── Header ────────────────────────────────────────────────────────────────────

st.markdown('<div class="main-header">📊 DevInsight — Decision Support</div>', unsafe_allow_html=True)
st.markdown(f'<div class="sub-header">PFE · Master Big Data & IA · Fenetre : {days} jours · {len(teams_sel)} equipes selectionnees</div>', unsafe_allow_html=True)

# ── Helper: DORA pill ─────────────────────────────────────────────────────────

def pill(level):
    cls = f"pill-{level}"
    return f'<span class="pill {cls}">{level.upper()}</span>'

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 1 — VUE D'ENSEMBLE
# ═══════════════════════════════════════════════════════════════════════════════

if page == "Vue d'ensemble":

    # KPI row
    df_lt  = lake.lead_time(days=days)
    df_ct  = lake.cycle_time(days=days)
    df_dep = lake.deployment_frequency(days=days)
    cfr    = lake.change_failure_rate(days=days)
    mttr   = lake.mttr(days=days)
    df_blk = lake.blocked_tickets(days=days)

    med_lt  = df_lt["lead_time_hrs"].median()  if not df_lt.empty  else 0
    med_ct  = df_ct["cycle_time_hrs"].median() if not df_ct.empty  else 0
    avg_dep = df_dep["deployments"].mean()     if not df_dep.empty else 0

    c1, c2, c3, c4 = st.columns(4)

    with c1:
        st.markdown(f"""<div class="kpi-card" style="--c:#2ecc8f">
            <div class="kpi-label">Deploy Frequency</div>
            <div class="kpi-value">{avg_dep:.1f}<span style="font-size:1rem;color:#7a8099">/j</span></div>
            <div class="kpi-delta">{pill("elite")}</div>
        </div>""", unsafe_allow_html=True)

    with c2:
        lvl = "high" if med_lt < 24 else "medium" if med_lt < 168 else "low"
        st.markdown(f"""<div class="kpi-card" style="--c:#4f8ef7">
            <div class="kpi-label">Lead Time</div>
            <div class="kpi-value">{med_lt:.1f}<span style="font-size:1rem;color:#7a8099">h</span></div>
            <div class="kpi-delta">{pill(lvl)}</div>
        </div>""", unsafe_allow_html=True)

    with c3:
        lvl = "elite" if cfr["cfr_percent"] < 5 else "high" if cfr["cfr_percent"] < 10 else "medium" if cfr["cfr_percent"] < 15 else "low"
        st.markdown(f"""<div class="kpi-card" style="--c:#e05252">
            <div class="kpi-label">Change Failure Rate</div>
            <div class="kpi-value">{cfr["cfr_percent"]}<span style="font-size:1rem;color:#7a8099">%</span></div>
            <div class="kpi-delta">{pill(lvl)}</div>
        </div>""", unsafe_allow_html=True)

    with c4:
        m = mttr.get("mttr_median_hrs") or 0
        lvl = "elite" if m < 1 else "high" if m < 24 else "medium" if m < 168 else "low"
        st.markdown(f"""<div class="kpi-card" style="--c:#9b6cf7">
            <div class="kpi-label">MTTR</div>
            <div class="kpi-value">{m:.1f}<span style="font-size:1rem;color:#7a8099">h</span></div>
            <div class="kpi-delta">{pill(lvl)}</div>
        </div>""", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # Charts row 1
    col1, col2 = st.columns(2)

    with col1:
        df_dep2 = lake.query(f"""
            SELECT DATE_TRUNC('day', occurred_at)::DATE AS day, COUNT(*) AS deployments
            FROM events WHERE event_type IN ('release_created','action_run_completed')
            AND occurred_at >= NOW() - INTERVAL '{days} days'
            GROUP BY 1 ORDER BY 1
        """)
        if not df_dep2.empty:
            df_dep2["day"] = pd.to_datetime(df_dep2["day"])
            fig = px.bar(df_dep2, x="day", y="deployments",
                         title="Deployment Frequency",
                         color_discrete_sequence=["#4f8ef7"])
            fig.update_layout(
                paper_bgcolor="#141720", plot_bgcolor="#141720",
                font_color="#7a8099", title_font_color="#e2e6f0",
                xaxis=dict(gridcolor="#2a2f42"), yaxis=dict(gridcolor="#2a2f42"),
            )
            st.plotly_chart(fig, use_container_width=True)

    with col2:
        df_lt2 = lake.query(f"""
            SELECT DATE_TRUNC('week', occurred_at)::DATE AS week,
                   json_extract_string(payload, '$.team') AS team,
                   MEDIAN(CAST(json_extract(payload, '$.lead_time_hrs') AS DOUBLE)) AS median_hrs
            FROM events WHERE event_type='pr_merged'
            AND json_extract_string(payload,'$.team') IN ('{"','".join(teams_sel)}')
            AND occurred_at >= NOW() - INTERVAL '{days} days'
            GROUP BY 1, 2 ORDER BY 1
        """)
        if not df_lt2.empty:
            df_lt2["week"] = pd.to_datetime(df_lt2["week"])
            fig2 = px.line(df_lt2, x="week", y="median_hrs", color="team",
                           title="Lead Time par equipe (median hebdo)",
                           color_discrete_map=COLORS, markers=True)
            fig2.add_hline(y=24, line_dash="dash", line_color="#f5a623",
                           annotation_text="High (24h)", annotation_font_color="#f5a623")
            fig2.update_layout(
                paper_bgcolor="#141720", plot_bgcolor="#141720",
                font_color="#7a8099", title_font_color="#e2e6f0",
                xaxis=dict(gridcolor="#2a2f42"), yaxis=dict(gridcolor="#2a2f42"),
            )
            st.plotly_chart(fig2, use_container_width=True)

    # Team table
    st.markdown("#### Performance par equipe")
    rows = []
    for team in teams_sel:
        lt_t  = df_lt[df_lt["team"]==team]["lead_time_hrs"].median()  if not df_lt.empty  else None
        ct_t  = df_ct[df_ct["team"]==team]["cycle_time_hrs"].median() if not df_ct.empty  else None
        blk_t = lake.query(f"""
            SELECT COUNT(*) AS n FROM events
            WHERE event_type='ticket_transitioned'
            AND json_extract_string(payload,'$.to_status')='Blocked'
            AND occurred_at >= NOW() - INTERVAL '{days} days'
        """).iloc[0]["n"]
        rows.append({
            "Equipe":      team.capitalize(),
            "Lead Time":   f"{lt_t:.1f}h"  if lt_t  else "—",
            "Cycle Time":  f"{ct_t:.1f}h"  if ct_t  else "—",
            "Bloques":     int(blk_t),
            "Statut":      "🔴 Critique" if team=="gamma" else "🟡 Attention" if team=="beta" else "🟢 Nominal",
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 2 — METRIQUES DORA
# ═══════════════════════════════════════════════════════════════════════════════

elif page == "Metriques DORA":

    st.markdown("#### 4 Metriques DORA — detail")

    t1, t2, t3, t4 = st.tabs(["📦 Deploy Freq", "⏱ Lead Time", "💥 CFR", "🚑 MTTR"])

    with t1:
        df = lake.query(f"""
            SELECT DATE_TRUNC('day', occurred_at)::DATE AS day, COUNT(*) AS n
            FROM events WHERE event_type IN ('release_created','action_run_completed')
            AND occurred_at >= NOW() - INTERVAL '{days} days'
            GROUP BY 1 ORDER BY 1
        """)
        if not df.empty:
            df["day"] = pd.to_datetime(df["day"])
            df["rolling7"] = df["n"].rolling(7, min_periods=1).mean()
            fig = go.Figure()
            fig.add_trace(go.Bar(x=df["day"], y=df["n"], name="Deploiements/jour",
                                 marker_color="#4f8ef7", opacity=0.5))
            fig.add_trace(go.Scatter(x=df["day"], y=df["rolling7"], name="Moy. mobile 7j",
                                     line=dict(color="#9b6cf7", width=2)))
            fig.add_hline(y=1, line_dash="dash", line_color="#2ecc8f",
                          annotation_text="Elite", annotation_font_color="#2ecc8f")
            fig.update_layout(paper_bgcolor="#141720", plot_bgcolor="#141720",
                              font_color="#7a8099", title_font_color="#e2e6f0",
                              xaxis=dict(gridcolor="#2a2f42"), yaxis=dict(gridcolor="#2a2f42"))
            st.plotly_chart(fig, use_container_width=True)

    with t2:
        df = lake.lead_time(days=days)
        if not df.empty:
            df_filt = df[df["team"].isin(teams_sel)] if teams_sel else df
            fig = px.box(df_filt, x="team", y="lead_time_hrs", color="team",
                         color_discrete_map=COLORS,
                         title="Distribution Lead Time par equipe (heures)")
            fig.add_hline(y=24, line_dash="dash", line_color="#f5a623",
                          annotation_text="High (24h)")
            fig.update_layout(paper_bgcolor="#141720", plot_bgcolor="#141720",
                              font_color="#7a8099", showlegend=False,
                              xaxis=dict(gridcolor="#2a2f42"), yaxis=dict(gridcolor="#2a2f42"))
            st.plotly_chart(fig, use_container_width=True)

    with t3:
        df = lake.query(f"""
            WITH dep AS (SELECT DATE_TRUNC('week',occurred_at)::DATE AS week, COUNT(*) AS n
                FROM events WHERE event_type IN ('release_created','action_run_completed')
                AND occurred_at >= NOW()-INTERVAL '{days} days' GROUP BY 1),
            inc AS (SELECT DATE_TRUNC('week',occurred_at)::DATE AS week, COUNT(*) AS n
                FROM events WHERE source='slack' AND event_type='incident_opened'
                AND occurred_at >= NOW()-INTERVAL '{days} days' GROUP BY 1)
            SELECT d.week, d.n AS deploys, COALESCE(i.n,0) AS incidents,
                   ROUND(COALESCE(i.n,0)*100.0/d.n,2) AS cfr_pct
            FROM dep d LEFT JOIN inc i ON d.week=i.week ORDER BY 1
        """)
        if not df.empty:
            df["week"] = pd.to_datetime(df["week"])
            fig = go.Figure()
            fig.add_trace(go.Bar(x=df["week"], y=df["cfr_pct"], name="CFR %",
                                 marker_color="#e05252", opacity=0.7))
            for lvl, col, val in [("Elite","#2ecc8f",5),("High","#f5a623",10),("Medium","#e05252",15)]:
                fig.add_hline(y=val, line_dash="dash", line_color=col,
                              annotation_text=f"{lvl} ({val}%)", annotation_font_color=col)
            fig.update_layout(paper_bgcolor="#141720", plot_bgcolor="#141720",
                              font_color="#7a8099", xaxis=dict(gridcolor="#2a2f42"),
                              yaxis=dict(gridcolor="#2a2f42", title="CFR (%)"))
            st.plotly_chart(fig, use_container_width=True)

    with t4:
        mttr_data = lake.mttr(days=days)
        if mttr_data["n_incidents"] > 0:
            m1, m2, m3 = st.columns(3)
            m1.metric("MTTR Median", f"{mttr_data['mttr_median_hrs']}h")
            m2.metric("MTTR p95",    f"{mttr_data['mttr_p95_hrs']}h")
            m3.metric("Incidents",   mttr_data["n_incidents"])
            df_inc = pd.DataFrame(mttr_data["incidents"])
            if not df_inc.empty:
                fig = px.histogram(df_inc, x="mttr_hrs", nbins=20,
                                   title="Distribution MTTR (heures)",
                                   color_discrete_sequence=["#9b6cf7"])
                fig.update_layout(paper_bgcolor="#141720", plot_bgcolor="#141720",
                                  font_color="#7a8099", xaxis=dict(gridcolor="#2a2f42"),
                                  yaxis=dict(gridcolor="#2a2f42"))
                st.plotly_chart(fig, use_container_width=True)

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 3 — EQUIPES
# ═══════════════════════════════════════════════════════════════════════════════

elif page == "Equipes":
    st.markdown("#### Performance par equipe")

    df_lt = lake.lead_time(days=days)
    df_ct = lake.cycle_time(days=days)

    col1, col2 = st.columns(2)

    with col1:
        if not df_ct.empty:
            df_f = df_ct[df_ct["team"].isin(teams_sel)]
            fig = px.box(df_f, x="team", y="cycle_time_hrs", color="team",
                         color_discrete_map=COLORS,
                         title="Cycle Time par equipe (heures)")
            fig.update_layout(paper_bgcolor="#141720", plot_bgcolor="#141720",
                              font_color="#7a8099", showlegend=False,
                              xaxis=dict(gridcolor="#2a2f42"), yaxis=dict(gridcolor="#2a2f42"))
            st.plotly_chart(fig, use_container_width=True)

    with col2:
        df_trend = lake.query(f"""
            SELECT DATE_TRUNC('week', occurred_at)::DATE AS week,
                   json_extract_string(payload,'$.team') AS team,
                   MEDIAN(CAST(json_extract(payload,'$.cycle_time_hrs') AS DOUBLE)) AS median_ct
            FROM events WHERE event_type='ticket_closed'
            AND json_extract_string(payload,'$.team') IN ('{"','".join(teams_sel)}')
            AND occurred_at >= NOW()-INTERVAL '{days} days'
            GROUP BY 1,2 ORDER BY 1
        """)
        if not df_trend.empty:
            df_trend["week"] = pd.to_datetime(df_trend["week"])
            fig2 = px.line(df_trend, x="week", y="median_ct", color="team",
                           color_discrete_map=COLORS, markers=True,
                           title="Tendance Cycle Time (median hebdo)")
            fig2.update_layout(paper_bgcolor="#141720", plot_bgcolor="#141720",
                               font_color="#7a8099",
                               xaxis=dict(gridcolor="#2a2f42"), yaxis=dict(gridcolor="#2a2f42"))
            st.plotly_chart(fig2, use_container_width=True)

    # PR count per team
    df_prs = lake.query(f"""
        SELECT json_extract_string(payload,'$.team') AS team,
               COUNT(*) AS prs_merged,
               MEDIAN(CAST(json_extract(payload,'$.lead_time_hrs') AS DOUBLE)) AS lead_time_median
        FROM events WHERE event_type='pr_merged'
        AND json_extract_string(payload,'$.team') IN ('{"','".join(teams_sel)}')
        AND occurred_at >= NOW()-INTERVAL '{days} days'
        GROUP BY 1 ORDER BY 2 DESC
    """)
    if not df_prs.empty:
        fig3 = px.bar(df_prs, x="team", y="prs_merged", color="team",
                      color_discrete_map=COLORS, title="PRs mergees par equipe")
        fig3.update_layout(paper_bgcolor="#141720", plot_bgcolor="#141720",
                           font_color="#7a8099", showlegend=False,
                           xaxis=dict(gridcolor="#2a2f42"), yaxis=dict(gridcolor="#2a2f42"))
        st.plotly_chart(fig3, use_container_width=True)

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 4 — AGENT IA
# ═══════════════════════════════════════════════════════════════════════════════

elif page == "Agent IA":
    st.markdown("#### 🤖 Agent d'analyse — Questions en langage naturel")
    st.markdown(
        '<div style="color:#7a8099;font-size:0.85rem;margin-bottom:1rem;">'
        'Agent simulé W4 — les réponses sont pré-calculées depuis le data lake. '
        'L\'agent LLM réel sera intégré en W6.</div>',
        unsafe_allow_html=True
    )

    # Pre-load metrics for agent
    @st.cache_data
    def load_agent_data(days):
        lt  = get_lake().lead_time(days=days)
        ct  = get_lake().cycle_time(days=days)
        cfr = get_lake().change_failure_rate(days=days)
        mtr = get_lake().mttr(days=days)
        blk = get_lake().blocked_tickets(days=days)
        return lt, ct, cfr, mtr, blk

    lt, ct, cfr_d, mttr_d, blk = load_agent_data(days)

    @st.cache_resource
    def get_agent():
        from agents import DevInsightAgent
        from graph  import KnowledgeGraph
        kg = KnowledgeGraph(get_lake()).build()
        return DevInsightAgent(get_lake(), kg)
    # Chat UI
    if "messages" not in st.session_state:
        st.session_state.messages = [
            {"role": "agent", "text": "Bonjour. Je suis l'agent d'analyse DevInsight. Posez-moi une question sur les métriques, les équipes ou la performance du sprint.", "source": f"Data lake · {days} jours d'historique"}
        ]

    # Display messages
    for msg in st.session_state.messages:
        if msg["role"] == "user":
            st.markdown(f'<div class="chat-msg-user">👤 {msg["text"]}</div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="chat-msg-agent">🤖 {msg["text"]}<div class="chat-source">📎 {msg.get("source","")}</div></div>', unsafe_allow_html=True)

    # Suggested questions
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("**Questions suggérées :**")
    cols = st.columns(3)
    suggestions = [
        "Quelle équipe a le plus de problèmes ?",
        "Quel est le CFR actuel ?",
        "Explique le Lead Time",
        "Combien de tickets bloqués ?",
        "Quel est le MTTR ?",
        "Résumé des métriques DORA",
    ]
    for i, sug in enumerate(suggestions):
        with cols[i % 3]:
            if st.button(sug, key=f"sug_{i}"):
                with st.spinner("Analyse en cours..."):
                    agent  = get_agent()
                    result = agent.ask(sug)
                    resp   = result["answer"].replace("&#39;", "'").replace("&amp;", "&")
                    src    = ", ".join(result["sources"]) if result["sources"] else "ollama:llama3.2:3b"
                st.session_state.messages.append({"role":"user","text":sug})
                st.session_state.messages.append({"role":"agent","text":resp,"source":src})
                st.rerun()

    # Free input
    with st.form("chat_form", clear_on_submit=True):
        col_in, col_btn = st.columns([5,1])
        with col_in:
            user_input = st.text_input("", placeholder="Ex: Quelle équipe a le plus de tickets bloqués ?", label_visibility="collapsed")
        with col_btn:
            submitted = st.form_submit_button("Envoyer")

    if submitted and user_input.strip():
        with st.spinner("Agent en cours de réflexion..."):
            agent  = get_agent()
            result = agent.ask(user_input)
            resp   = result["answer"].replace("&#39;", "'").replace("&amp;", "&")
            src    = ", ".join(result["sources"]) if result["sources"] else "ollama:llama3.2:3b"
        st.session_state.messages.append({"role":"user","text":user_input})
        st.session_state.messages.append({"role":"agent","text":resp,"source":src})
        st.rerun()