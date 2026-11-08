---
title: Connect an AI agent through MCP
type: how-to
description: Create an Agent with its own key, point Claude, ChatGPT or another MCP client at the Odoo MCP server, and check what the Agent can see.
lang: en
persona: administrator
app: Agents & MCP
models:
  - usl.agent
  - usl.agent.credential
actions:
  - usl_access_control.action_usl_agent
screen: /odoo/action-usl_access_control.action_usl_agent
source:
  - custom-addons/usl_access_control/models/agent.py
  - custom-addons/usl_access_control/models/agent_credential.py
  - deploy/odoo-mcp/release.json
generated: false
---

# Connect an AI agent through MCP

An AI assistant works in Odoo as an **Agent**: a non-human identity you own,
with its own key, whose authority is the intersection of yours, what you
delegate to it, and the distribution's safety policy. The Agent, not you, is
the actor in every audit trail. The MCP server at
`https://odoo-mcp.unstaticlabs.com/mcp` is how an assistant reaches Odoo with
that identity.

## 1. Create the Agent

1. Open **Settings → Users & Companies → Agents** and choose **New**.
2. Name the Agent after the assistant and its purpose, for example
   *Claude, accounting review*. You are its owner.
3. Choose the companies it may work in and, per application, **No access**,
   **Read-only** or one of your own access levels. A new Agent starts
   read-only everywhere; widen only what the assistant needs.
4. Save. Settings, identity administration, credentials and Irreversible
   Actions are never delegable, whatever you choose here.

## 2. Give it a key

1. On the Agent form, choose **New API Key**, then **Generate**. The secret
   is shown once; copy it into the assistant's configuration now.
2. The key expires on the date shown. Create a replacement before it does
   (**Create replacement**) so the assistant keeps working, then **Revoke**
   the old one.

Your own API keys do not work on the MCP server. Only an Agent key does.

## 3. Point the assistant at Odoo

The server speaks Streamable HTTP. Each profile is a URL:

| URL | What the assistant sees |
| --- | --- |
| `/mcp` | The everyday tools: find, read, create and update records, post messages, schedule activities, the accounting, expense, document and project helpers. |
| `/mcp/read-only` | Only tools that read. |
| `/mcp/accounting`, `/mcp/projects`, `/mcp/documents`, `/mcp/b2c` | The everyday core plus one area in depth. |
| `/mcp/all` | Every tool, loaded on demand. |

Two ways to authenticate:

- **A hosted assistant (Claude, ChatGPT)**: add the server URL as a custom
  connector. The assistant opens an authorization page; sign in with your
  USL identity and paste the Agent key once. The server keeps it encrypted
  and the assistant never sees it again.
- **A local client (Claude Code, Codex, a script)**: send three headers with
  every request: `X-Odoo-Url: https://odoo.unstaticlabs.com`,
  `X-Odoo-Database` with the database name, and `X-Odoo-Api-Key` with the
  Agent key. Nothing is stored server-side.

## 4. Check what the Agent can do

Ask the assistant to describe its environment (the `odoo_describe_environment`
tool). The answer names the Agent, its owner, the key's expiry and the
companies it works in. Ask it to read this guide: the `usl.docs.guide` model
offers `search_pages`, `read_page` and `list_pages` through the generic
`odoo_call_method` tool, so the assistant can look a procedure up before
acting.

Everything the Agent reads or changes follows Odoo's own rules in your
company context. Losing an access right yourself removes it from the Agent at
once; suspending the Agent (**Suspend** on its form) blocks every key
immediately.

## Related

- [MCP tools and profiles](../reference/mcp-tools.md): the complete catalogue
  of the pinned server.
- [Roles and permissions](../reference/roles-and-permissions.md): what each
  access level allows.
