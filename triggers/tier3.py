#!/usr/bin/env python3
"""
ATLAS Analyst Trigger — Weekly Deep Synthesis
Runs Sunday 8pm (CEST).
Pattern-level analysis: cross-partner insight transfer, decision tracking,
brain dump pattern detection, capacity analysis, client health scoring,
roadmap progress. Feeds Monday briefs.
"""

import os
import sys
import anthropic

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
from tool_handler import run_agent_session

TRIGGER_MESSAGE = """\
Run ATLAS Analyst — Weekly Deep Synthesis.

Perform full pattern-level analysis of the past week as defined in your system prompt:
1. Cross-partner insight transfer (what one team member learned that others need)
2. Decision outcome tracking (decisions from 2-3 weeks ago — did they work?)
3. Brain dump pattern detection (recurring themes across the week's #brain-dump posts)
4. Capacity analysis (who is overloaded, where are delays forming)
5. Client health scoring (update all 6 client scores)
6. Roadmap progress check (30-day plans — on track or stalled?)

Write outputs to Notion. Update client status pages and brief pages.
Log summary to #atlas-curator-log.
This is a scheduled Sunday run triggered by GitHub Actions.
"""


def main():
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
    if dry_run:
        print("[DRY RUN mode — writes simulated, reads are real]")

    session = client.beta.sessions.create(
        agent=os.environ["ATLAS_TIER3_AGENT_ID"],
        environment_id=os.environ["ATLAS_ENVIRONMENT_ID"],
        title=f"atlas-tier3-{os.getenv('GITHUB_RUN_ID', 'manual')}",
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
        max_iterations=40,
    )

    print("\n── Agent response ──")
    print(result)


if __name__ == "__main__":
    main()
