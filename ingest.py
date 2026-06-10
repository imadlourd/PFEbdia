import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from connectors import GitHubConnector, JiraConnector
from lake       import DataLake

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="PFE ingestion pipeline")
    parser.add_argument("--synthetic",     action="store_true")
    parser.add_argument("--days",          type=int, default=14)
    parser.add_argument("--lake-path",     default="./lake")
    parser.add_argument("--github-token",  default=os.environ.get("GITHUB_TOKEN"))
    parser.add_argument("--github-owner",  default=os.environ.get("GITHUB_OWNER", "myorg"))
    parser.add_argument("--github-repo",   default=os.environ.get("GITHUB_REPO",  "myrepo"))
    parser.add_argument("--jira-url",      default=os.environ.get("JIRA_URL"))
    parser.add_argument("--jira-email",    default=os.environ.get("JIRA_EMAIL"))
    parser.add_argument("--jira-token",    default=os.environ.get("JIRA_TOKEN"))
    parser.add_argument("--jira-project",  default=os.environ.get("JIRA_PROJECT", "PROJ"))
    args = parser.parse_args()

    lake = DataLake(args.lake_path)

    # GitHub
    if args.synthetic or not args.github_token:
        gh = GitHubConnector.synthetic(owner=args.github_owner, repo=args.github_repo)
    else:
        gh = GitHubConnector(token=args.github_token, owner=args.github_owner, repo=args.github_repo)

    logger.info("=" * 55)
    logger.info(f"  GitHub — last {args.days} days")
    logger.info("=" * 55)
    github_events = gh.fetch_all(days_back=args.days)

    # Jira
    if args.synthetic or not args.jira_token:
        jira = JiraConnector.synthetic(project_key=args.jira_project)
    else:
        jira = JiraConnector(base_url=args.jira_url, email=args.jira_email,
                             api_token=args.jira_token, project_key=args.jira_project)

    logger.info("=" * 55)
    logger.info(f"  Jira  — last {args.days} days")
    logger.info("=" * 55)
    jira_events = jira.fetch_all(days_back=args.days)

    # Ingest
    all_events = github_events + jira_events
    logger.info("=" * 55)
    logger.info(f"  Ingesting {len(all_events)} events")
    logger.info("=" * 55)
    lake.ingest(all_events)

    # Stats
    stats = lake.stats()
    logger.info("")
    logger.info("┌─────────────────────────────────────────────┐")
    logger.info("│  LAKE STATISTICS                            │")
    logger.info("├─────────────────────────────────────────────┤")
    logger.info(f"│  Total  : {stats['total_events']:<6} events  {stats['size_mb']:.2f} MB           │")
    for row in stats["by_source"]:
        logger.info(f"│  {row['source']:<8}: {row['n_events']:<5} events  "
                    f"{row['earliest'][:10]} → {row['latest'][:10]}  │")
    logger.info("└─────────────────────────────────────────────┘")

    # Metrics
    logger.info("")
    df_lt = lake.lead_time(days=args.days)
    if not df_lt.empty:
        logger.info(f"  Lead Time median   : {df_lt['lead_time_hrs'].median():.1f} h")
        for team, val in df_lt.groupby("team")["lead_time_hrs"].median().items():
            logger.info(f"    └─ {team:<8} : {val:.1f} h")

    df_ct = lake.cycle_time(days=args.days)
    if not df_ct.empty:
        logger.info(f"  Cycle Time median  : {df_ct['cycle_time_hrs'].median():.1f} h")
        for team, val in df_ct.groupby("team")["cycle_time_hrs"].median().items():
            logger.info(f"    └─ {team:<8} : {val:.1f} h")

    df_dep = lake.deployment_frequency(days=args.days)
    if not df_dep.empty:
        logger.info(f"  Deploy Freq avg    : {df_dep['deployments'].mean():.1f} / day")

    df_blk = lake.blocked_tickets(days=args.days)
    logger.info(f"  Blocked tickets    : {len(df_blk)}")
    logger.info("")
    logger.info("  Done.")


if __name__ == "__main__":
    main()