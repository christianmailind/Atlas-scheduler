#!/usr/bin/env python3
"""
ATLAS Watcher Trigger — Urgent Processor
Runs every 60 min, 7am-10pm Mon-Sat (CEST).
Processes #claude-sync session-end posts, decisions, tasks, blockers.
"""

import os
import sys
import anthropic

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
from tool_handler import run_agent_session

TRIGGER_MESSAGE = """\
Run ATLAS Watcher — Urgent Processor.

Process Slack messages from the last 60 minutes across all monitored channels.
Apply curator rules as defined in your system prompt.
Update Notion state for any events that pass the confidence threshold.
Post end-of-run status line to #atlas-curator-log.

This is a scheduled run triggered by GitHub Actions.
"""


def main():
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
    if dry_run:
        print("[DRY RUN mode — writes simulated, reads are real]")

    session = client.beta.sessions.create(
        agent=os.environ["ATLAS_TIER1_AGENT_ID"],
        environment_id=os.environ["ATLAS_ENVIRONMENT_ID"],
        title=f"atlas-tier1-{os.getenv('GITHUB_RUN_ID', 'manual')}",
    )
    print(f"Session: {session.id}")

    result = run_agent_session(
        client=client,
        session_id=session.id,
        trigger_message=TRIGGER_MESSAGE,
        dry_run=dry_run,
        slack_token=os.environ["SLACK_BOT_TOKEN"],
        notion_token=os.environ["NOTION_API_TOKEN"],
        log_fn=print,
    )

    print("\n── Agent response ──")
    print(result)


if __name__ == "__main__":
    main()
