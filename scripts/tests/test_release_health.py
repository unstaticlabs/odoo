import unittest
from datetime import datetime, timedelta, timezone

from operations import release_health as h

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)


def deployment(identifier, minutes_ago, sha="a" * 40, environment=h.STAGING):
    return {
        "id": identifier,
        "sha": sha,
        "environment": environment,
        "created_at": (NOW - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z"),
    }


def states(mapping):
    return lambda identifier: [{"state": state} for state in mapping[identifier]]


class TestReleaseHealth(unittest.TestCase):
    def test_a_successful_newest_deployment_is_healthy(self):
        report = h.assess(
            h.STAGING, [deployment(1, 10)], states({1: ["success", "in_progress"]}), NOW,
        )
        self.assertTrue(report["healthy"])
        self.assertEqual(report["reason"], "deployed")

    def test_consecutive_failures_are_counted_and_named(self):
        """The morning of 2026-09-08: repeated failures, each rolling back."""
        deployments = [deployment(i, i * 60) for i in range(1, 7)]
        report = h.assess(
            h.STAGING, deployments, states({i: ["failure"] for i in range(1, 7)}), NOW,
        )
        self.assertFalse(report["healthy"])
        self.assertEqual(report["reason"], "failing")
        self.assertEqual(report["consecutive_failures"], 6)
        self.assertIn("6 consecutive deployments", report["summary"])

    def test_counting_stops_at_the_last_success(self):
        deployments = [deployment(1, 10), deployment(2, 70), deployment(3, 130)]
        report = h.assess(
            h.STAGING, deployments,
            states({1: ["failure"], 2: ["failure"], 3: ["success"]}), NOW,
        )
        self.assertEqual(report["consecutive_failures"], 2)

    def test_a_running_deployment_is_not_an_alert(self):
        report = h.assess(
            h.STAGING, [deployment(1, 5)], states({1: ["in_progress"]}), NOW,
        )
        self.assertTrue(report["healthy"])
        self.assertEqual(report["reason"], "in-flight")

    def test_a_deployment_pending_far_too_long_is_a_stall(self):
        """A lost single-attempt hand-off leaves a deployment pending forever."""
        report = h.assess(
            h.STAGING, [deployment(1, 60 * 5)], states({1: ["pending"]}), NOW,
        )
        self.assertFalse(report["healthy"])
        self.assertEqual(report["reason"], "stalled")
        self.assertIn("pending", report["summary"])

    def test_inactive_marks_supersession_not_an_outcome(self):
        report = h.assess(
            h.STAGING, [deployment(1, 10)], states({1: ["inactive", "success"]}), NOW,
        )
        self.assertTrue(report["healthy"])

    def test_no_deployment_yet_is_not_a_failure(self):
        report = h.assess(h.STAGING, [], states({}), NOW)
        self.assertTrue(report["healthy"])
        self.assertEqual(report["reason"], "no-deployments")

    def test_other_environments_are_ignored(self):
        deployments = [deployment(1, 10, environment=h.PRODUCTION)]
        report = h.assess(h.STAGING, deployments, states({1: ["failure"]}), NOW)
        self.assertTrue(report["healthy"])
        self.assertEqual(report["reason"], "no-deployments")

    def test_production_is_watched_too(self):
        deployments = [deployment(1, 10, environment=h.PRODUCTION)]
        report = h.assess(h.PRODUCTION, deployments, states({1: ["failure"]}), NOW)
        self.assertFalse(report["healthy"])

    def test_an_unknown_environment_is_refused(self):
        with self.assertRaises(ValueError):
            h.assess("preview", [], states({}), NOW)

    def test_order_comes_from_the_timestamps_not_the_api_order(self):
        deployments = [deployment(9, 300), deployment(1, 5)]
        report = h.assess(
            h.STAGING, deployments, states({9: ["failure"], 1: ["success"]}), NOW,
        )
        self.assertTrue(report["healthy"])

    def test_an_unreadable_timestamp_is_refused(self):
        with self.assertRaises(ValueError):
            h.assess(
                h.STAGING, [{"id": 1, "environment": h.STAGING, "created_at": "soon"}],
                states({1: ["success"]}), NOW,
            )


class TestLastDelivered(unittest.TestCase):
    """What the chain last delivered, which is where a changelog starts."""

    def test_the_newest_success_is_returned(self):
        deployments = [
            deployment(1, 200, sha="a" * 40, environment=h.PRODUCTION),
            deployment(2, 100, sha="b" * 40, environment=h.PRODUCTION),
        ]
        self.assertEqual(
            h.last_delivered(
                h.PRODUCTION, deployments, states({1: ["success"], 2: ["success"]}),
            ),
            "b" * 40,
        )

    def test_a_failed_release_is_skipped_so_its_changes_are_not_lost(self):
        deployments = [
            deployment(1, 200, sha="a" * 40, environment=h.PRODUCTION),
            deployment(2, 100, sha="b" * 40, environment=h.PRODUCTION),
        ]
        self.assertEqual(
            h.last_delivered(
                h.PRODUCTION, deployments,
                states({1: ["success"], 2: ["failure", "in_progress"]}),
            ),
            "a" * 40,
        )

    def test_a_pending_release_is_not_a_delivery(self):
        deployments = [
            deployment(1, 200, sha="a" * 40, environment=h.PRODUCTION),
            deployment(2, 100, sha="b" * 40, environment=h.PRODUCTION),
        ]
        self.assertEqual(
            h.last_delivered(
                h.PRODUCTION, deployments,
                states({1: ["success"], 2: ["in_progress"]}),
            ),
            "a" * 40,
        )

    def test_supersession_alone_is_not_a_delivery(self):
        self.assertIsNone(
            h.last_delivered(
                h.PRODUCTION,
                [deployment(1, 100, environment=h.PRODUCTION)],
                states({1: ["inactive"]}),
            ),
        )

    def test_the_other_environment_is_ignored(self):
        self.assertIsNone(
            h.last_delivered(
                h.PRODUCTION,
                [deployment(1, 100, environment=h.STAGING)],
                states({1: ["success"]}),
            ),
        )

    def test_order_comes_from_the_timestamps_not_the_api_order(self):
        deployments = [
            deployment(2, 10, sha="b" * 40, environment=h.PRODUCTION),
            deployment(1, 500, sha="a" * 40, environment=h.PRODUCTION),
        ]
        self.assertEqual(
            h.last_delivered(
                h.PRODUCTION, deployments, states({1: ["success"], 2: ["success"]}),
            ),
            "b" * 40,
        )

    def test_nothing_deployed_yet_is_no_base(self):
        self.assertIsNone(h.last_delivered(h.PRODUCTION, [], states({})))

    def test_an_unknown_environment_is_refused(self):
        with self.assertRaises(ValueError):
            h.last_delivered("somewhere", [], states({}))


if __name__ == "__main__":
    unittest.main()
