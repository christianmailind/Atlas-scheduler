#!/usr/bin/env python3
"""
ATLAS Heartbeat Trigger — System health check
Runs every 4 hours. Verifies Notion + Slack connectivity.
Posts status to #atlas-curator-log.
"""

import os
import sys
import anthropic

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
from tool_handler import run_agent_session

TRIGGER_MESSAGE = "Run ATLAS Heartbeat — system health check."


def main():
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # Heartbeat always runs in live mode — it only reads and posts to #atlas-curator-log
    dry_run = False

    session = client.beta.sessions.create(
        agent=os.environ["ATLAS_HEARTBEAT_AGENT_ID"],
        environment_id=os.environ["ATLAS_ENVIRONMENT_ID"],
        title=f"atlas-heartbeat-{os.getenv('GITHUB_RUN_ID', 'manual')}",
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
