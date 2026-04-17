"""
ATLAS Tool Handler
Implements all custom tools the agents can call, plus the event loop that handles
agent.custom_tool_use / user.custom_tool_result round-trips.

Tools provided:
  slack_read_channel(channel_name, limit, oldest_hours)
  slack_send_message(recipient, text)       — recipient = Slack user ID or channel name
  notion_fetch(page_id)
  notion_update_page(page_id, content, mode)
  notion_create_page(parent_id, parent_type, properties, content)
  notion_query_database(database_id, filter, sorts)

DRY_RUN behaviour (when dry_run=True):
  - Read ops always execute (we need real data)
  - Write/send ops to anything except #atlas-curator-log are simulated
  - All dry-run actions are logged to #atlas-curator-log
"""

import json
import time
import requests
from datetime import datetime, timezone, timedelta

SLACK_API = "https://slack.com/api"
NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

# Simple in-process caches (reset between runs since each run is a fresh process)
_channel_id_cache: dict = {}
_user_name_cache: dict = {}


# ─────────────────────────────────────────────────────────────────────────────
# Slack internals
# ─────────────────────────────────────────────────────────────────────────────

def _slack_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _get_channel_id(channel_name: str, token: str) -> str | None:
    if channel_name in _channel_id_cache:
        return _channel_id_cache[channel_name]

    cursor = None
    while True:
        params = {"types": "public_channel,private_channel", "limit": 1000, "exclude_archived": "true"}
        if cursor:
            params["cursor"] = cursor
        resp = requests.get(f"{SLACK_API}/conversations.list", headers=_slack_headers(token), params=params, timeout=15)
        data = resp.json()
        if not data.get("ok"):
            return None
        for ch in data.get("channels", []):
            _channel_id_cache[ch["name"]] = ch["id"]
        if data.get("response_metadata", {}).get("next_cursor"):
            cursor = data["response_metadata"]["next_cursor"]
        else:
            break

    return _channel_id_cache.get(channel_name)


def _get_user_name(user_id: str, token: str) -> str:
    if user_id in _user_name_cache:
        return _user_name_cache[user_id]
    resp = requests.get(f"{SLACK_API}/users.info", headers=_slack_headers(token), params={"user": user_id}, timeout=10)
    data = resp.json()
    if data.get("ok"):
        name = data["user"].get("display_name") or data["user"].get("real_name") or user_id
        _user_name_cache[user_id] = name
        return name
    return user_id


def _ts_to_dt(ts: str) -> str:
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return ts


# ─────────────────────────────────────────────────────────────────────────────
# Slack tools
# ─────────────────────────────────────────────────────────────────────────────

def slack_read_channel(channel_name: str, limit: int = 50, oldest_hours: int = None, token: str = None) -> str:
    channel_id = _get_channel_id(channel_name, token)
    if not channel_id:
        return f"Error: Channel #{channel_name} not found in workspace"

    params = {"channel": channel_id, "limit": min(limit, 200)}
    if oldest_hours:
        params["oldest"] = str(time.time() - oldest_hours * 3600)

    resp = requests.get(f"{SLACK_API}/conversations.history", headers=_slack_headers(token), params=params, timeout=15)
    data = resp.json()
    if not data.get("ok"):
        return f"Error reading #{channel_name}: {data.get('error', 'unknown')}"

    messages = data.get("messages", [])
    if not messages:
        return f"#{channel_name}: no messages in the requested time window"

    lines = [f"#{channel_name} — {len(messages)} message(s):"]
    for msg in reversed(messages):  # oldest first
        user_id = msg.get("user", "bot")
        name = _get_user_name(user_id, token) if user_id != "bot" else msg.get("bot_profile", {}).get("name", "bot")
        text = msg.get("text", "")
        dt = _ts_to_dt(msg.get("ts", ""))
        lines.append(f"[{dt}] {name}: {text}")

    return "\n".join(lines)


def slack_send_message(recipient: str, text: str, token: str = None, dry_run: bool = True) -> str:
    is_dm = recipient.startswith("U") or recipient.startswith("W")

    # Resolve channel_id
    if is_dm:
        resp = requests.post(f"{SLACK_API}/conversations.open", headers=_slack_headers(token),
                             json={"users": recipient}, timeout=10)
        data = resp.json()
        if not data.get("ok"):
            return f"Error opening DM with {recipient}: {data.get('error')}"
        channel_id = data["channel"]["id"]
        dest_label = f"DM to {recipient}"
    else:
        channel_name = recipient.lstrip("#")
        channel_id = _get_channel_id(channel_name, token)
        if not channel_id:
            return f"Error: Channel #{channel_name} not found"
        dest_label = f"#{channel_name}"

    # In dry_run, only actually send to #atlas-curator-log
    is_log_channel = (not is_dm) and recipient.lstrip("#") == "atlas-curator-log"
    if dry_run and not is_log_channel:
        return f"[DRY RUN] Would send to {dest_label}: {text[:100]}..."

    resp = requests.post(f"{SLACK_API}/chat.postMessage", headers=_slack_headers(token),
                         json={"channel": channel_id, "text": text}, timeout=15)
    data = resp.json()
    if not data.get("ok"):
        return f"Error sending to {dest_label}: {data.get('error')}"
    return f"OK: message sent to {dest_label}"


# ─────────────────────────────────────────────────────────────────────────────
# Notion internals
# ─────────────────────────────────────────────────────────────────────────────

def _notion_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION,
    }


def _rich_text_to_plain(rich_text: list) -> str:
    return "".join(t.get("plain_text", "") for t in rich_text)


def _blocks_to_markdown(blocks: list) -> str:
    lines = []
    for block in blocks:
        btype = block.get("type", "")
        if btype == "paragraph":
            text = _rich_text_to_plain(block["paragraph"].get("rich_text", []))
            lines.append(text)
        elif btype == "heading_1":
            text = _rich_text_to_plain(block["heading_1"].get("rich_text", []))
            lines.append(f"# {text}")
        elif btype == "heading_2":
            text = _rich_text_to_plain(block["heading_2"].get("rich_text", []))
            lines.append(f"## {text}")
        elif btype == "heading_3":
            text = _rich_text_to_plain(block["heading_3"].get("rich_text", []))
            lines.append(f"### {text}")
        elif btype == "bulleted_list_item":
            text = _rich_text_to_plain(block["bulleted_list_item"].get("rich_text", []))
            lines.append(f"- {text}")
        elif btype == "numbered_list_item":
            text = _rich_text_to_plain(block["numbered_list_item"].get("rich_text", []))
            lines.append(f"1. {text}")
        elif btype == "divider":
            lines.append("---")
        elif btype == "callout":
            text = _rich_text_to_plain(block["callout"].get("rich_text", []))
            lines.append(f"> {text}")
        elif btype == "quote":
            text = _rich_text_to_plain(block["quote"].get("rich_text", []))
            lines.append(f"> {text}")
        elif btype == "code":
            text = _rich_text_to_plain(block["code"].get("rich_text", []))
            lines.append(f"```\n{text}\n```")
        # skip unsupported block types (child_page, image, etc.)
    return "\n".join(lines)


def _markdown_to_blocks(markdown: str) -> list:
    blocks = []
    for line in markdown.split("\n"):
        if line.startswith("### "):
            content = line[4:]
            blocks.append({"object": "block", "type": "heading_3",
                           "heading_3": {"rich_text": [{"type": "text", "text": {"content": content}}]}})
        elif line.startswith("## "):
            content = line[3:]
            blocks.append({"object": "block", "type": "heading_2",
                           "heading_2": {"rich_text": [{"type": "text", "text": {"content": content}}]}})
        elif line.startswith("# "):
            content = line[2:]
            blocks.append({"object": "block", "type": "heading_1",
                           "heading_1": {"rich_text": [{"type": "text", "text": {"content": content}}]}})
        elif line.startswith("- ") or line.startswith("* "):
            content = line[2:]
            blocks.append({"object": "block", "type": "bulleted_list_item",
                           "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": content}}]}})
        elif line.startswith("---"):
            blocks.append({"object": "block", "type": "divider", "divider": {}})
        else:
            blocks.append({"object": "block", "type": "paragraph",
                           "paragraph": {"rich_text": [{"type": "text", "text": {"content": line}}] if line else []}})
    return blocks


# ─────────────────────────────────────────────────────────────────────────────
# Notion tools
# ─────────────────────────────────────────────────────────────────────────────

def notion_fetch(page_id: str, token: str = None) -> str:
    # Get page metadata
    resp = requests.get(f"{NOTION_API}/pages/{page_id}", headers=_notion_headers(token), timeout=15)
    if not resp.ok:
        return f"Error fetching page {page_id}: {resp.status_code} {resp.text[:200]}"
    page = resp.json()

    # Extract title
    title = ""
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title":
            title = _rich_text_to_plain(prop.get("title", []))
            break

    # Get page blocks (content), handle pagination
    all_blocks = []
    cursor = None
    while True:
        params = {}
        if cursor:
            params["start_cursor"] = cursor
        resp = requests.get(f"{NOTION_API}/blocks/{page_id}/children",
                           headers=_notion_headers(token), params=params, timeout=15)
        if not resp.ok:
            break
        data = resp.json()
        all_blocks.extend(data.get("results", []))
        if data.get("has_more"):
            cursor = data.get("next_cursor")
        else:
            break

    content = _blocks_to_markdown(all_blocks)
    return f"# {title}\n\n{content}" if title else content


def notion_update_page(page_id: str, content: str, mode: str = "append", token: str = None, dry_run: bool = True) -> str:
    if dry_run:
        preview = content[:150].replace("\n", " ")
        return f"[DRY RUN] Would {mode} page {page_id} with {len(content)} chars: {preview}..."

    if mode == "replace":
        # Archive all existing blocks
        resp = requests.get(f"{NOTION_API}/blocks/{page_id}/children",
                           headers=_notion_headers(token), timeout=15)
        if resp.ok:
            for block in resp.json().get("results", []):
                requests.delete(f"{NOTION_API}/blocks/{block['id']}",
                               headers=_notion_headers(token), timeout=10)

    blocks = _markdown_to_blocks(content)
    # Notion API: max 100 blocks per request
    errors = []
    for i in range(0, len(blocks), 100):
        batch = blocks[i:i+100]
        resp = requests.patch(f"{NOTION_API}/blocks/{page_id}/children",
                             headers=_notion_headers(token),
                             json={"children": batch}, timeout=15)
        if not resp.ok:
            errors.append(f"batch {i//100}: {resp.status_code} {resp.text[:100]}")

    if errors:
        return f"Partial error writing to {page_id}: {'; '.join(errors)}"
    return f"OK: {len(blocks)} blocks written to page {page_id} (mode={mode})"


def notion_create_page(parent_id: str, parent_type: str = "database",
                       properties: dict = None, content: str = None,
                       token: str = None, dry_run: bool = True) -> str:
    if dry_run:
        props_preview = json.dumps(properties or {})[:100]
        return f"[DRY RUN] Would create {parent_type} entry in {parent_id}: {props_preview}"

    parent_key = "database_id" if parent_type == "database" else "page_id"
    payload = {
        "parent": {parent_key: parent_id},
        "properties": properties or {}
    }
    if content:
        payload["children"] = _markdown_to_blocks(content)

    resp = requests.post(f"{NOTION_API}/pages", headers=_notion_headers(token),
                        json=payload, timeout=15)
    if not resp.ok:
        return f"Error creating page: {resp.status_code} {resp.text[:200]}"
    result = resp.json()
    return json.dumps({"id": result["id"], "url": result.get("url", "")})


def notion_query_database(database_id: str, filter: dict = None, sorts: list = None,
                          token: str = None) -> str:
    payload = {}
    if filter:
        payload["filter"] = filter
    if sorts:
        payload["sorts"] = sorts

    resp = requests.post(f"{NOTION_API}/databases/{database_id}/query",
                        headers=_notion_headers(token), json=payload, timeout=15)
    if not resp.ok and sorts:
        # Sorts format may be wrong — retry without sorts
        payload.pop("sorts", None)
        resp = requests.post(f"{NOTION_API}/databases/{database_id}/query",
                            headers=_notion_headers(token), json=payload, timeout=15)
    if not resp.ok:
        return f"Error querying database {database_id}: {resp.status_code} {resp.text[:200]}"

    results = resp.json().get("results", [])
    # Return a simplified representation
    items = []
    for page in results:
        item = {"id": page["id"], "properties": {}}
        for name, prop in page.get("properties", {}).items():
            ptype = prop.get("type")
            if ptype == "title":
                item["properties"][name] = _rich_text_to_plain(prop.get("title", []))
            elif ptype == "rich_text":
                item["properties"][name] = _rich_text_to_plain(prop.get("rich_text", []))
            elif ptype == "select":
                item["properties"][name] = (prop.get("select") or {}).get("name", "")
            elif ptype == "date":
                item["properties"][name] = (prop.get("date") or {}).get("start", "")
            elif ptype == "checkbox":
                item["properties"][name] = prop.get("checkbox", False)
            elif ptype == "number":
                item["properties"][name] = prop.get("number")
            else:
                item["properties"][name] = f"[{ptype}]"
        items.append(item)

    return json.dumps(items, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
# Tool dispatcher
# ─────────────────────────────────────────────────────────────────────────────

def handle_tool_call(name: str, inputs: dict, dry_run: bool = True,
                     slack_token: str = None, notion_token: str = None) -> str:
    try:
        if name == "slack_read_channel":
            return slack_read_channel(
                channel_name=inputs["channel_name"],
                limit=inputs.get("limit", 50),
                oldest_hours=inputs.get("oldest_hours"),
                token=slack_token
            )
        elif name == "slack_send_message":
            return slack_send_message(
                recipient=inputs["recipient"],
                text=inputs["text"],
                token=slack_token,
                dry_run=dry_run
            )
        elif name == "notion_fetch":
            return notion_fetch(
                page_id=inputs["page_id"],
                token=notion_token
            )
        elif name == "notion_update_page":
            return notion_update_page(
                page_id=inputs["page_id"],
                content=inputs["content"],
                mode=inputs.get("mode", "append"),
                token=notion_token,
                dry_run=dry_run
            )
        elif name == "notion_create_page":
            return notion_create_page(
                parent_id=inputs["parent_id"],
                parent_type=inputs.get("parent_type", "database"),
                properties=inputs.get("properties"),
                content=inputs.get("content"),
                token=notion_token,
                dry_run=dry_run
            )
        elif name == "notion_query_database":
            return notion_query_database(
                database_id=inputs["database_id"],
                filter=inputs.get("filter"),
                sorts=inputs.get("sorts"),
                token=notion_token
            )
        else:
            return f"Error: Unknown tool '{name}'"
    except Exception as e:
        return f"Error executing {name}: {type(e).__name__}: {e}"


# ─────────────────────────────────────────────────────────────────────────────
# Agent session event loop
# ─────────────────────────────────────────────────────────────────────────────

def run_agent_session(
    client,
    session_id: str,
    trigger_message: str,
    dry_run: bool = True,
    slack_token: str = None,
    notion_token: str = None,
    log_fn=print,
    max_iterations: int = 20
) -> str:
    """
    Send trigger_message to a session and handle the full event loop until end_turn.
    Handles agent.custom_tool_use / user.custom_tool_result round-trips automatically.
    Returns the final agent message text.
    """

    # Send the trigger message
    client.beta.sessions.events.send(
        session_id,
        events=[{
            "type": "user.message",
            "content": [{"type": "text", "text": trigger_message}]
        }]
    )

    final_response = []
    iteration = 0
    sent_result_ids: set = set()  # guard against re-sending duplicate results

    while iteration < max_iterations:
        iteration += 1
        pending_tool_calls = []
        stop_reason_type = None

        with client.beta.sessions.events.stream(session_id) as stream:
            for event in stream:
                etype = event.type if hasattr(event, "type") else str(type(event))

                if etype == "agent.custom_tool_use":
                    log_fn(f"[tool] {event.name}({json.dumps(event.input)[:120]})")
                    pending_tool_calls.append(event)

                elif etype == "agent.message":
                    for block in event.content:
                        if hasattr(block, "text"):
                            final_response.append(block.text)

                elif etype == "session.error":
                    err = event.error if hasattr(event, "error") else repr(event)
                    log_fn(f"[session.error] {err}")

                elif etype == "session.status_idle":
                    stop_reason_type = event.stop_reason.type
                    if stop_reason_type == "requires_action":
                        event_ids = event.stop_reason.event_ids
                        log_fn(f"[requires_action] waiting on {len(event_ids)} tool result(s)")
                    break

        # Handle pending tool calls (skip any we already responded to)
        new_tool_calls = [tc for tc in pending_tool_calls if tc.id not in sent_result_ids]
        if new_tool_calls:
            results = []
            for tc in new_tool_calls:
                result = handle_tool_call(
                    name=tc.name,
                    inputs=tc.input,
                    dry_run=dry_run,
                    slack_token=slack_token,
                    notion_token=notion_token
                )
                log_fn(f"[tool result] {tc.name}: {result[:80]}")
                results.append({
                    "type": "user.custom_tool_result",
                    "custom_tool_use_id": tc.id,
                    "content": [{"type": "text", "text": result}]
                })
                sent_result_ids.add(tc.id)
            client.beta.sessions.events.send(session_id, events=results)
            continue

        # No tool calls this iteration
        if stop_reason_type == "end_turn":
            break
        elif stop_reason_type == "requires_action":
            # Results still processing, loop again
            continue
        else:
            # Stream ended or unexpected state
            break

    if iteration >= max_iterations:
        log_fn(f"[warning] max_iterations ({max_iterations}) reached")

    return "\n".join(final_response)


# ─────────────────────────────────────────────────────────────────────────────
# Tool definitions (for agents.create())
# ─────────────────────────────────────────────────────────────────────────────

ATLAS_TOOLS = [
    {
        "type": "custom",
        "name": "slack_read_channel",
        "description": "Read recent messages from a Slack channel. Returns messages with timestamps and user names, oldest first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_name": {
                    "type": "string",
                    "description": "Channel name without # (e.g. 'claude-sync', 'leap-eod', 'brain-dump')"
                },
                "limit": {
                    "type": "integer",
                    "description": "Max messages to return (default 50, max 200)"
                },
                "oldest_hours": {
                    "type": "integer",
                    "description": "Only return messages from the last N hours (optional)"
                }
            },
            "required": ["channel_name"]
        }
    },
    {
        "type": "custom",
        "name": "slack_send_message",
        "description": "Send a Slack message. If recipient is a Slack user ID (starts with U), sends a DM. If it's a channel name, posts to that channel.",
        "input_schema": {
            "type": "object",
            "properties": {
                "recipient": {
                    "type": "string",
                    "description": "Slack user ID (e.g. 'U12345ABC') for DMs, or channel name without # (e.g. 'atlas-curator-log') for channel posts"
                },
                "text": {
                    "type": "string",
                    "description": "Message text (supports Slack markdown: *bold*, _italic_, ```code blocks```)"
                }
            },
            "required": ["recipient", "text"]
        }
    },
    {
        "type": "custom",
        "name": "notion_fetch",
        "description": "Fetch the content of a Notion page as markdown. Always fetch current state before writing to avoid overwriting human edits.",
        "input_schema": {
            "type": "object",
            "properties": {
                "page_id": {
                    "type": "string",
                    "description": "Notion page UUID (e.g. '342c1b6f-a030-81d0-b6a4-ed5a01617719')"
                }
            },
            "required": ["page_id"]
        }
    },
    {
        "type": "custom",
        "name": "notion_update_page",
        "description": "Write content to a Notion page. Use mode='replace' to clear and rewrite the entire page. Use mode='append' to add content at the end.",
        "input_schema": {
            "type": "object",
            "properties": {
                "page_id": {
                    "type": "string",
                    "description": "Notion page UUID"
                },
                "content": {
                    "type": "string",
                    "description": "Markdown-formatted content to write (supports # headings, - bullets, --- dividers)"
                },
                "mode": {
                    "type": "string",
                    "enum": ["replace", "append"],
                    "description": "replace: clear existing content and write fresh. append: add to end of page."
                }
            },
            "required": ["page_id", "content", "mode"]
        }
    },
    {
        "type": "custom",
        "name": "notion_create_page",
        "description": "Create a new Notion page or database entry. Use parent_type='database' to add a row to a database. Use parent_type='page' to create a subpage.",
        "input_schema": {
            "type": "object",
            "properties": {
                "parent_id": {
                    "type": "string",
                    "description": "Parent database UUID or page UUID"
                },
                "parent_type": {
                    "type": "string",
                    "enum": ["database", "page"],
                    "description": "Whether the parent is a database (for new rows) or a page (for subpages)"
                },
                "properties": {
                    "type": "object",
                    "description": "Page/row properties as a Notion properties object. For database entries, include the Title field and any other required fields."
                },
                "content": {
                    "type": "string",
                    "description": "Markdown content for the page body (optional)"
                }
            },
            "required": ["parent_id", "parent_type"]
        }
    },
    {
        "type": "custom",
        "name": "notion_query_database",
        "description": "Query a Notion database with optional filters and sorting. Returns matching entries as JSON.",
        "input_schema": {
            "type": "object",
            "properties": {
                "database_id": {
                    "type": "string",
                    "description": "Notion database UUID"
                },
                "filter": {
                    "type": "object",
                    "description": "Notion filter object (optional). Example: {\"property\": \"Status\", \"select\": {\"equals\": \"In Progress\"}}"
                },
                "sorts": {
                    "type": "array",
                    "description": "Sort criteria (optional). Example: [{\"property\": \"Created\", \"direction\": \"descending\"}]"
                }
            },
            "required": ["database_id"]
        }
    }
]
