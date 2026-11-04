"""Staging on demand: the pure policy behind ``usl-stack runtime wake|sleep``.

Staging shares one VPS with production.  Idle, it is about 1.6 GB of RAM and a
steady slice of CPU that production could use.  This module decides *when*
staging should be awake; ``operations.stack`` does the actual starting and
stopping with the same Compose identity that ``runtime start`` and ``runtime
stop`` already use, under the same locks as every other release operation.

Nothing here touches Docker or the network, so every rule below is testable
with a clock and a list of log lines.

Vocabulary
----------
``active``    every staging service runs.
``waking``    a wake is in progress (state written first, so a crash is visible).
``sleeping``  every application service is stopped; volumes, generations,
              snapshots and the gateway are untouched.
``stopping``  a sleep is in progress.
``failed``    the last wake or sleep did not complete; ``last_error`` says why.

A staging target without a state file is ``active``: the feature adds no
behaviour until the first explicit ``sleep``.
"""
from __future__ import annotations

import html
import json
import re
from datetime import UTC, datetime, timedelta

SCHEMA = "usl-staging-lifecycle/v1"
CONFIG_FIELDS = {
    "mode",
    "idle_after_ci_minutes",
    "idle_after_interactive_minutes",
    "wake_timeout_seconds",
    "max_lease_minutes",
    "sweep_seconds",
    "gateway_service",
    "listen_port",
    "oidc_issuer",
    "oidc_client_file",
    "oidc_required_group",
}
STATES = ("active", "waking", "sleeping", "stopping", "failed")
KINDS = ("ci", "interactive")
HISTORY_LIMIT = 30
# Anything that is not a person using Odoo must never keep staging awake, and
# must never wake it.  Matching is on the user agent because the gateway sits
# behind Cloudflare and sees only the tunnel's address.
AUTOMATED_AGENT = re.compile(
    r"bot|crawl|spider|slurp|uptime|kuma|monitor|pingdom|healthcheck|probe|"
    r"curl/|wget/|python-requests|python-httpx|aiohttp|node-fetch|axios|okhttp|"
    r"libwww|go-http-client|java/|cloudflare|headless|scan",
    re.IGNORECASE,
)
# A person at the keyboard shows up as authenticated Odoo traffic.  These are
# the routes a browser session calls while someone works; the login page,
# static assets, health probes and robots.txt are all reachable anonymously and
# therefore prove nothing.
INTERACTIVE_POST = (
    "/web/dataset/call_kw",
    "/web/dataset/call_button",
    "/web/action/load",
    "/web/webclient/",
    "/mail/",
    "/discuss/",
    "/web/session/",
    "/json/",
)
LOG_LINE = re.compile(
    r'^(?P<stamp>\d{4}-\d\d-\d\dT[\d:.]+Z)\s+\S+ \S+ \S+ \[[^\]]+\] '
    r'"(?P<method>[A-Z]+) (?P<path>\S+)[^"]*" (?P<status>\d{3}) \S+ '
    r'"[^"]*" "(?P<agent>[^"]*)"',
)


class LifecycleError(ValueError):
    pass


def utc(value: datetime | None = None) -> datetime:
    return (value or datetime.now(UTC)).astimezone(UTC)


def stamp(value: datetime) -> str:
    return utc(value).isoformat().replace("+00:00", "Z")


def parse_stamp(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise LifecycleError("timestamp must be a string")
    text = value.replace("Z", "+00:00")
    # Docker log timestamps carry nanoseconds; datetime accepts six digits.
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise LifecycleError(f"invalid timestamp: {value!r}") from error
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def validate_lifecycle(value: object) -> dict:
    """Validate the declarative ``lifecycle`` block of a staging target."""
    if isinstance(value, dict) and value.get("mode") == "always_on":
        # Production and local are always on: the declaration is the whole truth.
        if set(value) != {"mode"}:
            raise LifecycleError("an always_on lifecycle declares nothing else")
        return value
    if not isinstance(value, dict) or set(value) != CONFIG_FIELDS:
        actual = sorted(value) if isinstance(value, dict) else type(value).__name__
        raise LifecycleError(f"lifecycle fields differ: {actual}")
    if value["mode"] != "on_demand":
        raise LifecycleError("lifecycle.mode must be on_demand or always_on")
    for key in (
        "idle_after_ci_minutes", "idle_after_interactive_minutes",
        "wake_timeout_seconds", "max_lease_minutes", "sweep_seconds",
        "listen_port",
    ):
        if type(value[key]) is not int or value[key] <= 0:
            raise LifecycleError(f"lifecycle.{key} must be a positive integer")
    if not 1 <= value["listen_port"] <= 65535:
        raise LifecycleError("lifecycle.listen_port is out of range")
    if value["sweep_seconds"] > 600:
        raise LifecycleError("lifecycle.sweep_seconds must be at most 600")
    if not isinstance(value["gateway_service"], str) or not value["gateway_service"]:
        raise LifecycleError("lifecycle.gateway_service is required")
    if not isinstance(value["oidc_issuer"], str) or not value["oidc_issuer"].startswith("https://"):
        raise LifecycleError("lifecycle.oidc_issuer must be an https URL")
    client = value["oidc_client_file"]
    if not isinstance(client, str) or not client.startswith("/"):
        raise LifecycleError("lifecycle.oidc_client_file must be absolute")
    if not isinstance(value["oidc_required_group"], str) or not value["oidc_required_group"]:
        raise LifecycleError("lifecycle.oidc_required_group is required")
    return value


def new_state(now: datetime, state: str = "active") -> dict:
    return {
        "schema": SCHEMA,
        "state": state,
        "since": stamp(now),
        "last_ci_at": stamp(now),
        "last_interactive_at": None,
        "last_wake_at": None,
        "last_wake_reason": None,
        "last_sleep_at": None,
        "last_sleep_reason": None,
        "lease": None,
        "last_error": None,
        "history": [],
    }


def validate_state(value: object) -> dict:
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise LifecycleError("lifecycle state has an unsupported schema")
    if value.get("state") not in STATES:
        raise LifecycleError(f"lifecycle state is invalid: {value.get('state')!r}")
    for key in ("since", "last_ci_at", "last_interactive_at", "last_wake_at", "last_sleep_at"):
        parse_stamp(value.get(key))
    lease = value.get("lease")
    if lease is not None and (
        not isinstance(lease, dict)
        or parse_stamp(lease.get("until")) is None
        or not isinstance(lease.get("holder"), str)
    ):
        raise LifecycleError("lifecycle lease is invalid")
    if not isinstance(value.get("history"), list):
        raise LifecycleError("lifecycle history is invalid")
    return value


def record(state: dict, now: datetime, event: str, **details: object) -> dict:
    """Append one event; the history is the operator's audit trail."""
    history = [*state.get("history", []), {"at": stamp(now), "event": event, **details}]
    return {**state, "history": history[-HISTORY_LIMIT:]}


def kind_of(reason: str) -> str:
    """CI and release work gets the short grace period; people get the long one."""
    return "ci" if reason.split(":", 1)[0] in {"ci", "release", "pipeline"} else "interactive"


def active_lease(state: dict, now: datetime) -> dict | None:
    lease = state.get("lease")
    if lease and parse_stamp(lease["until"]) > now:
        return lease
    return None


def grant_lease(
    state: dict, now: datetime, config: dict, *, minutes: int, holder: str,
) -> dict:
    if minutes <= 0:
        raise LifecycleError("a lease must last a positive number of minutes")
    if minutes > config["max_lease_minutes"]:
        raise LifecycleError(
            f"a lease may not exceed {config['max_lease_minutes']} minutes; "
            "renew it instead",
        )
    until = now + timedelta(minutes=minutes)
    current = active_lease(state, now)
    if current:
        until = max(until, parse_stamp(current["until"]))
    return record(
        {**state, "lease": {"until": stamp(until), "holder": holder}},
        now, "lease", holder=holder, until=stamp(until),
    )


def sleep_not_before(state: dict, config: dict) -> datetime:
    """The earliest instant at which idleness may put staging to sleep."""
    ci = parse_stamp(state.get("last_ci_at"))
    interactive = parse_stamp(state.get("last_interactive_at"))
    woke = parse_stamp(state.get("last_wake_at"))
    candidates = []
    if ci:
        candidates.append(ci + timedelta(minutes=config["idle_after_ci_minutes"]))
    if interactive:
        candidates.append(
            interactive + timedelta(minutes=config["idle_after_interactive_minutes"]),
        )
    if woke:
        # A wake is itself activity.  A person who woke staging gets the long
        # grace period, a pipeline the short one.
        grace = (
            config["idle_after_ci_minutes"]
            if kind_of(state.get("last_wake_reason") or "") == "ci"
            else config["idle_after_interactive_minutes"]
        )
        candidates.append(woke + timedelta(minutes=grace))
    since = parse_stamp(state.get("since"))
    if not candidates and since:
        candidates.append(since + timedelta(minutes=config["idle_after_interactive_minutes"]))
    return max(candidates)


def decide(state: dict, now: datetime, config: dict, *, busy: list[str]) -> dict:
    """Return ``{"action": "none"|"sleep", "reason": str, ...}``.

    ``busy`` lists live operations discovered by the caller (a release or
    backup holding a lock, a maintenance marker, an unfinished release
    attempt).  Any entry vetoes sleep outright: idleness never outranks work.
    """
    if config["mode"] != "on_demand":
        return {"action": "none", "reason": "lifecycle mode is always_on"}
    if state["state"] != "active":
        return {"action": "none", "reason": f"state is {state['state']}"}
    if busy:
        return {"action": "none", "reason": "busy: " + ", ".join(sorted(busy))}
    lease = active_lease(state, now)
    if lease:
        return {
            "action": "none",
            "reason": f"leased by {lease['holder']} until {lease['until']}",
        }
    due = sleep_not_before(state, config)
    if now < due:
        return {"action": "none", "reason": "recently used", "sleep_not_before": stamp(due)}
    return {"action": "sleep", "reason": "idle", "sleep_not_before": stamp(due)}


def interactive_activity(lines: list[str]) -> datetime | None:
    """Latest instant at which a person used Odoo, from timestamped gateway logs.

    The lines are ``docker logs --timestamps`` output of the staging gateway.
    Anonymous traffic (login page, assets, probes, crawlers) is ignored by
    construction: only authenticated browser calls count.
    """
    latest: datetime | None = None
    for line in lines:
        match = LOG_LINE.match(line)
        if not match:
            continue
        agent = match["agent"]
        if not agent or AUTOMATED_AGENT.search(agent):
            continue
        method, path, status = match["method"], match["path"], int(match["status"])
        route = path.split("?", 1)[0]
        person = (
            (method == "POST" and status == 200 and route.startswith(INTERACTIVE_POST))
            or (route == "/websocket" and status == 101)
        )
        if not person:
            continue
        moment = parse_stamp(match["stamp"])
        if latest is None or moment > latest:
            latest = moment
    return latest


def wake_attempt_allowed(agent: str) -> bool:
    """Bots, crawlers and probes never wake staging, even with a valid token."""
    return bool(agent) and not AUTOMATED_AGENT.search(agent)


def public_status(state: dict | None, now: datetime) -> dict:
    """What the unauthenticated status endpoint may reveal: the state, nothing else."""
    if state is None:
        return {"state": "active"}
    value = {"state": state["state"], "since": state["since"]}
    if state["state"] == "failed":
        value["detail"] = "the last wake failed; an operator can retry"
    return value


def sleeping_page(status: dict, *, hostname: str, person: str | None = None, notice: str | None = None) -> str:
    """The page shown while staging sleeps; it polls ``/__usl/status`` itself.

    Anonymous visitors get a sign-in button; a signed-in person gets the wake
    button.  Nothing here can start staging without a Pocket ID session.
    """
    state = html.escape(str(status.get("state", "sleeping")))
    host = html.escape(hostname)
    if person:
        action = (
            f'<p>Signed in as <b>{html.escape(person)}</b>.</p>'
            '<form id="wake" method="post" action="/__usl/wake">'
            '<button type="submit">Wake staging</button></form>'
        )
    else:
        action = '<p><a class="button" href="/__usl/login">Sign in with Pocket ID to wake staging</a></p>'
    warning = f'<p class="warning">{html.escape(notice)}</p>' if notice else ""
    return f"""<!doctype html>
<html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>USL Odoo staging is asleep</title>
<style>
body{{font:18px system-ui;margin:12vh auto;max-width:34rem;padding:0 1.5rem;color:#29252b}}
h1{{color:#714b67;font-size:1.6rem}} button,.button{{font:inherit;padding:.5rem .9rem;margin:.25rem 0}}
button,.button{{background:#714b67;color:#fff;border:0;border-radius:4px;cursor:pointer;text-decoration:none;display:inline-block}}
small{{color:#6b6470}} #message{{margin-top:1rem}} .warning{{color:#a4262c}}
</style>
<h1>Staging is asleep</h1>
<p>{host} runs only when a pipeline or a teammate needs it, which keeps the
shared server free for production. Current state: <b id="state">{state}</b>.</p>
{warning}{action}
<p id="message"><small>Waking takes two to three minutes and this page then opens
staging by itself. Operators can also run
<code>scripts/usl-stack --target staging runtime wake</code>.</small></p>
<script>
const message = document.getElementById('message');
const stateEl = document.getElementById('state');
async function poll() {{
  try {{
    const r = await fetch('/__usl/status', {{cache: 'no-store'}});
    const s = await r.json();
    stateEl.textContent = s.state;
    if (s.state === 'active') {{ location.href = '/'; return; }}
  }} catch (e) {{}}
  setTimeout(poll, 5000);
}}
const form = document.getElementById('wake');
if (form) form.addEventListener('submit', async (event) => {{
  event.preventDefault();
  const r = await fetch('/__usl/wake', {{method: 'POST', credentials: 'same-origin'}});
  message.textContent = r.status === 202 ? 'Waking staging…'
    : r.status === 401 ? 'Your session expired; sign in again.'
    : 'Could not start the wake (' + r.status + ').';
  if (r.status === 202) poll();
}});
if (stateEl.textContent !== 'sleeping') poll();
</script>
</html>
"""


def dumps(value: dict) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"
