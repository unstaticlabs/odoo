from __future__ import annotations

import unittest

from operations.staging_deployment import ENVIRONMENT, applies_to, verify

SHA = "8828cb1ed418deb03406f539a994925cae4af473"
OTHER = "0801ff5b7cf68f513cb458b78f7de5e7bec3262e"


def statuses(mapping):
    return lambda identifier: [{"state": state} for state in mapping.get(identifier, [])]


class StagingDeploymentTests(unittest.TestCase):
    def deployment(self, identifier, sha=SHA, environment=ENVIRONMENT):
        return {"id": identifier, "sha": sha, "environment": environment}

    def test_a_successful_staging_deployment_is_the_evidence(self):
        evidence = verify(
            SHA,
            [self.deployment(1)],
            statuses({1: ["success", "in_progress"]}),
        )
        self.assertEqual(evidence["deployment_id"], 1)
        self.assertEqual(evidence["sha"], SHA)

    def test_the_real_incident_would_have_been_refused(self):
        """8828cb1ed418 was promoted at 08:49 with staging failed since 04:55."""
        with self.assertRaisesRegex(ValueError, "did not succeed|no .* succeeded"):
            verify(
                SHA,
                [self.deployment(6320799865)],
                statuses({6320799865: ["failure", "in_progress"]}),
            )

    def test_never_deployed_is_refused_rather_than_assumed_fine(self):
        with self.assertRaisesRegex(ValueError, "has not been deployed to staging"):
            verify(SHA, [], statuses({}))

    def test_a_deployment_of_another_commit_does_not_count(self):
        with self.assertRaisesRegex(ValueError, "has not been deployed to staging"):
            verify(SHA, [self.deployment(1, sha=OTHER)], statuses({1: ["success"]}))

    def test_a_production_deployment_does_not_stand_in_for_staging(self):
        with self.assertRaisesRegex(ValueError, "has not been deployed to staging"):
            verify(
                SHA,
                [self.deployment(1, environment="production-release")],
                statuses({1: ["success"]}),
            )

    def test_a_retry_that_finally_succeeded_is_accepted(self):
        evidence = verify(
            SHA,
            [self.deployment(1), self.deployment(2)],
            statuses({1: ["failure"], 2: ["success"]}),
        )
        self.assertEqual(evidence["deployment_id"], 2)

    def test_a_deployment_still_running_is_not_proof(self):
        with self.assertRaisesRegex(ValueError, "no .* succeeded"):
            verify(SHA, [self.deployment(1)], statuses({1: ["in_progress"]}))

    def test_a_malformed_sha_is_refused(self):
        for value in ("", "8828cb1", SHA.upper(), None, SHA + "0"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "full commit sha"):
                    verify(value, [self.deployment(1)], statuses({1: ["success"]}))


class PromotionSourceTests(unittest.TestCase):
    def test_a_staging_promotion_must_show_a_deployment(self):
        self.assertTrue(applies_to("19-usl-staging"))

    def test_an_urgent_promotion_is_exempt_by_design(self):
        """An urgent fix reaches staging after production, never before.

        Requiring a staging deployment would refuse every hotfix at the moment
        it is needed, which is worse than the gap this check closes.
        """
        for ref in ("urgent/receipt-egress-down", "urgent/x", "urgent/a/b"):
            with self.subTest(ref=ref):
                self.assertFalse(applies_to(ref))

    def test_a_branch_merely_mentioning_urgent_is_not_exempt(self):
        for ref in ("feat/urgent-ish", "not-urgent/x", "19-usl", "hotfix/urgent"):
            with self.subTest(ref=ref):
                with self.assertRaises(ValueError):
                    applies_to(ref)

    def test_a_missing_head_ref_is_refused_rather_than_assumed_urgent(self):
        for ref in ("", None):
            with self.subTest(ref=ref):
                with self.assertRaisesRegex(ValueError, "head ref"):
                    applies_to(ref)


if __name__ == "__main__":
    unittest.main()
