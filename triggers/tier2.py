#!/usr/bin/env python3
"""
ATLAS Tier 2 Trigger — Daily Synthesis
Runs 8:30am Mon-Sat (CEST).
Processes #leap-eod, #brain-dump, deferred Tier 1 events, Fathom summaries.
Generates 4 team briefs and sends morning DMs at 8:35am.
"""

import os
import sys
import anthropic

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
from tool_handler import run_agent_session

TRIGGER_MESSAGE = """\
Run ATLAS Tier 2 — Daily Synthesis.

Process everything from the last 24 hours that Tier 1 did not handle:
- #leap-eod posts from yesterday
- #brain-dump content from yesterday
- Any deferred events from Tier 1 (marked as deferred in the Changelog)
- Fathom meeting summaries (if any)

Generate comprehensive briefs for all four team members (Christian, Victor, Mathias, Nicolai).
Write each brief to their respective Notion brief pages.
Send morning DMs to all four team members via Slack at the end.

Log all actions (or dry-run simulations) to #atlas-curator-log.
This is a scheduled run triggered by GitHub Actions.
"""


def main():
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
    if dry_run:
        print("[DRY RUN mode — writes simulated, reads are real]")

    slack_users = {
        "christian": os.getenv("SLACK_USER_CHRISTIAN", ""),
        "victor": os.getenv("SLACK_USER_VICTOR", ""),
        "mathias": os.getenv("SLACK_USER_MATHIAS", ""),
        "nicolai": os.getenv("SLACK_USER_NICOLAI", ""),
    }
    missing = [k for k, v in slack_users.items() if not v]
    if missing:
        print(f"[warning] Missing Slack user IDs for: {missing} — DMs will be skipped")

    # Inject Slack user IDs into trigger message so agent knows them
    user_ids_block = "\n".join(f"  {k.capitalize()}: {v or '[NOT SET]'}" for k, v in slack_users.items())
    trigger = TRIGGER_MESSAGE.rstrip() + f"\n\nSlack user IDs for DMs:\n{user_ids_block}\n"

    session = client.beta.sessions.create(
        agent=os.environ["ATLAS_TIER2_AGENT_ID"],
        environment_id=os.environ["ATLAS_ENVIRONMENT_ID"],
        title=f"atlas-tier2-{os.getenv('GITHUB_RUN_ID', 'manual')}",
    )
    print(f"Session: {session.id}")

    result = run_agent_session(
        client=client,
        session_id=session.id,
        trigger_message=trigger,
        dry_run=dry_run,
        slack_token=os.environ["SLACK_BOT_TOKEN"],
        notion_token=os.environ["NOTION_API_TOKEN"],
        log_fn=print,
        max_iterations=40,
    )

    print("\n── Agent response ──")
    print(result)


if __name__ == "__main__":
    main()
