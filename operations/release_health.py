"""Notice when the release chain has stopped delivering, and say what broke.

On 2026-09-08 five consecutive staging releases failed across seven hours and
nothing told anyone.  The first failure was visible as a ``staging-release``
deployment status of ``failure`` at 04:55; it was found by hand at about 11:00.
Every release after it inherited the same defect and failed identically, because
each rollback restored the state that caused it.

Two conditions are worth waking someone for, and a check that only looks for the
first would have missed the release-chain stalls this repository has also seen:

* the newest deployment failed, and
* the newest deployment never reached a terminal state at all, because the
  release chain hands off over single-attempt HTTP and a lost hand-off leaves a
  deployment pending forever.
"""
from __future__ import annotations

from datetime import datetime, timezone

STAGING = "staging-release"
PRODUCTION = "production-release"
ENVIRONMENTS = (STAGING, PRODUCTION)

SUCCESS = "success"
FAILED = frozenset({"failure", "error"})
#: ``inactive`` marks a deployment superseded by a newer one; it is not a verdict.
IGNORED = frozenset({"inactive"})

#: A release that has not reached a terminal state in this long is stalled, not
#: slow.  A full qualification, build, release and deploy cycle runs well inside
#: two hours.
STALLED_AFTER_SECONDS = 2 * 60 * 60


def _moment(value: object) -> datetime:
    text = str(value or "").replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise ValueError(f"deployment timestamp is unreadable: {value!r}") from error
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _verdict(states: list[str]) -> str:
    """The newest state that expresses an outcome, or ``pending``."""
    for state in states:
        if state in IGNORED:
            continue
        if state == SUCCESS:
            return SUCCESS
        if state in FAILED:
            return "failed"
        return "pending"
    return "pending"


def assess(
    environment: str,
    deployments: list[dict],
    statuses_for,
    now: datetime,
    stalled_after_seconds: int = STALLED_AFTER_SECONDS,
) -> dict:
    """Judge the newest deployments for ``environment``.

    ``deployments`` are GitHub deployments, any order; ``statuses_for`` maps a
    deployment id to its statuses, newest first as the API returns them.
    """
    if environment not in ENVIRONMENTS:
        raise ValueError(f"unknown release environment {environment!r}")

    ordered = sorted(
        (d for d in deployments if d.get("environment") == environment),
        key=lambda d: _moment(d.get("created_at")),
        reverse=True,
    )
    if not ordered:
        return {
            "environment": environment, "healthy": True, "reason": "no-deployments",
            "consecutive_failures": 0, "summary": f"no {environment} deployment exists yet",
        }

    newest = ordered[0]
    verdict = _verdict([s.get("state") for s in statuses_for(newest["id"])])
    sha = str(newest.get("sha") or "")[:12]
    created = _moment(newest.get("created_at"))
    age = (now - created).total_seconds()

    if verdict == SUCCESS:
        return {
            "environment": environment, "healthy": True, "reason": "deployed",
            "consecutive_failures": 0, "sha": sha,
            "summary": f"{environment} last deployed {sha} successfully",
        }

    if verdict == "pending":
        if age < stalled_after_seconds:
            return {
                "environment": environment, "healthy": True, "reason": "in-flight",
                "consecutive_failures": 0, "sha": sha,
                "summary": f"{environment} deployment of {sha} is still running",
            }
        return {
            "environment": environment, "healthy": False, "reason": "stalled",
            "consecutive_failures": 0, "sha": sha, "age_seconds": round(age),
            "summary": (
                f"{environment} deployment of {sha} has been pending for "
                f"{age / 3600:.1f}h with no result. The release chain hands off "
                "over single-attempt HTTP; a lost hand-off stalls exactly like this."
            ),
        }

    # Failed.  Count how far back the failures run, which is what turns "a
    # release failed" into "the release path is broken and every retry repeats".
    consecutive, oldest = 0, created
    for deployment in ordered:
        if _verdict([s.get("state") for s in statuses_for(deployment["id"])]) != "failed":
            break
        consecutive += 1
        oldest = _moment(deployment.get("created_at"))
    window = (now - oldest).total_seconds() / 3600
    return {
        "environment": environment, "healthy": False, "reason": "failing",
        "consecutive_failures": consecutive, "sha": sha,
        "summary": (
            f"{environment} has failed {consecutive} consecutive "
            f"deployment{'s' if consecutive != 1 else ''} over {window:.1f}h; "
            f"newest is {sha}. A failed release rolls back, so the next attempt "
            "starts from the same state and fails the same way until someone acts."
        ),
    }


def last_delivered(
    environment: str,
    deployments: list[dict],
    statuses_for,
) -> str | None:
    """The commit of the newest deployment that reached users, or ``None``.

    ``assess`` answers whether the chain is delivering now.  This answers what
    it last delivered, which is a different question and the one a changelog
    has to start from: a release that published but never deployed is not a
    baseline, because the changes it carried are still owed to the people who
    read the announcement.
    """
    if environment not in ENVIRONMENTS:
        raise ValueError(f"unknown release environment {environment!r}")
    ordered = sorted(
        (d for d in deployments if d.get("environment") == environment),
        key=lambda d: _moment(d.get("created_at")),
        reverse=True,
    )
    for deployment in ordered:
        states = [status.get("state") for status in statuses_for(deployment["id"])]
        if _verdict(states) == SUCCESS:
            return str(deployment.get("sha") or "") or None
    return None
