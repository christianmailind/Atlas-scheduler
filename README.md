# ATLAS Scheduler

GitHub Actions cron that triggers ATLAS Managed Agents on schedule. Free tier is sufficient — these are simple HTTP triggers.

## How It Works

1. GitHub Actions fires on schedule (defined in `.github/workflows/atlas-cron.yml`)
2. The workflow determines which tier to run based on the time
3. It sends a POST request to the Anthropic Managed Agents API to start a session for that agent
4. The agent runs autonomously, reads Slack and Notion via MCP, and writes back to Notion

## Setup

### 1. Push this directory to a GitHub repo

The scheduler lives in a separate repo (or a subdirectory with its own Actions). The `.github/workflows/` directory must be at the repo root.

```bash
cd atlas-scheduler
git init
git add .
git commit -m "ATLAS scheduler"
gh repo create houseofleap/atlas-scheduler --private
git push -u origin main
```

### 2. Add GitHub repo secrets

In the GitHub repo Settings > Secrets and variables > Actions, add:

| Secret | Value |
|---|---|
| `ANTHROPIC_API_KEY` | From `setup/.env` |
| `ATLAS_TIER1_AGENT_ID` | From `setup/atlas-ids.json` after running `create-agents.py` |
| `ATLAS_TIER2_AGENT_ID` | From `setup/atlas-ids.json` |
| `ATLAS_TIER3_AGENT_ID` | From `setup/atlas-ids.json` |
| `ATLAS_HEARTBEAT_AGENT_ID` | From `setup/atlas-ids.json` |

### 3. Run create-agents.py first

```bash
cd setup
pip install anthropic python-dotenv
python create-agents.py
```

This creates the Managed Agent definitions and writes their IDs to `setup/atlas-ids.json`.

### 4. Enable the workflow

GitHub Actions workflows are disabled by default on new repos. Go to the repo's Actions tab and click "Enable workflows."

### 5. Test manually

Use the workflow_dispatch trigger to run a specific tier manually before letting the cron take over:

```bash
gh workflow run atlas-cron.yml -f tier=tier1
```

Watch the output in the Actions tab. If dry-run is on, check #atlas-curator-log in Slack for proposed changes.

## Schedule (Copenhagen time, CEST — UTC+2)

| Tier | Schedule | Days |
|---|---|---|
| Tier 1 (urgent) | Every hour 7am–10pm | Mon–Sat |
| Tier 2 (daily) | 8:30am | Mon–Sat |
| Tier 3 (weekly) | 8:00pm | Sunday only |
| Heartbeat | Every 4 hours | Every day |

**DST note:** Schedules are in UTC and calibrated for CEST (UTC+2, April–October). When DST ends in late October, Copenhagen moves to CET (UTC+1). Update the cron expressions by adding 1 hour to the UTC values at that point.

## API Note

The workflow uses the Anthropic Managed Agents API. If the trigger endpoint or request format is wrong, check `https://docs.anthropic.com/managed-agents` and update the curl command in `atlas-cron.yml`.

## Dry Run

Agents start with `DRY_RUN=true`. All proposed changes are posted to #atlas-curator-log instead of being applied. Once you're satisfied with the output (3+ days of clean dry runs), update the environment variable to `false` via `setup/create-agents.py`.
