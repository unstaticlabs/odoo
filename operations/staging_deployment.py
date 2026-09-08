"""Refuse a production promotion whose tree never deployed to staging.

Staging exists to prove a release deploys.  Qualification proves it builds and
passes tests, which is a different claim: on 2026-09-08 the promotion of
``8828cb1ed418`` merged fully green at 08:49 while that exact commit's staging
deployment had been marked ``failure`` since 04:55, and production then failed
the same way.  GitHub already holds the answer as a deployment status; this
reads it.
"""
from __future__ import annotations

ENVIRONMENT = "staging-release"
SUCCESS = "success"


def verify(sha: str, deployments: list[dict], statuses_for) -> dict:
    """Return the evidence that ``sha`` reached staging, or raise.

    ``deployments`` are GitHub deployments for the commit; ``statuses_for``
    maps a deployment id to its status list, newest first as the API returns.
    """
    if not isinstance(sha, str) or len(sha) != 40 or not all(
        c in "0123456789abcdef" for c in sha
    ):
        raise ValueError("staging deployment check needs a full commit sha")

    candidates = [
        deployment for deployment in deployments
        if deployment.get("environment") == ENVIRONMENT
        and deployment.get("sha") == sha
    ]
    if not candidates:
        raise ValueError(
            f"no {ENVIRONMENT} deployment exists for {sha}: this tree has not "
            "been deployed to staging, so nothing proves it can deploy at all",
        )

    # A retried deployment that finally succeeded is proof; take any success
    # rather than only the newest deployment, but report what was seen.
    observed = {}
    for deployment in candidates:
        states = [status.get("state") for status in statuses_for(deployment["id"])]
        observed[deployment["id"]] = states
        if SUCCESS in states:
            return {
                "sha": sha,
                "environment": ENVIRONMENT,
                "deployment_id": deployment["id"],
                "states": states,
            }

    raise ValueError(
        f"no {ENVIRONMENT} deployment of {sha} succeeded; observed "
        + "; ".join(f"{key}: {', '.join(value) or 'no status'}" for key, value in observed.items())
        + ". Staging could not deploy this tree, so production must not take it.",
    )
