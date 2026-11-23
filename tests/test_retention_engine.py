import unittest
from datetime import datetime, timedelta, timezone

from object_lock import (
    DeleteRequest,
    FinalStatus,
    ObjectVersion,
    PolicyRegistry,
    RejectionCode,
    RetentionEngine,
    RetentionMode,
    RetentionPolicy,
)


NOW = datetime(2026, 11, 23, 6, 0, tzinfo=timezone.utc)
FUTURE = NOW + timedelta(days=30)
PAST = NOW - timedelta(days=1)


def fixed_clock():
    return NOW


def make_registry(governance_bypass=True):
    return PolicyRegistry(
        [
            RetentionPolicy(
                version="v1",
                governance_bypass_allowed=governance_bypass,
            ),
            RetentionPolicy(
                version="v2",
                governance_bypass_allowed=False,
            ),
        ]
    )


def make_version(**overrides):
    defaults = dict(
        version_id="ver-1",
        retention_mode=RetentionMode.GOVERNANCE,
        retention_until=FUTURE,
        legal_hold=False,
        delete_marker=False,
        policy_version="v1",
        object_key="photos/a.jpg",
    )
    defaults.update(overrides)
    return ObjectVersion(**defaults)


class GovernanceModeTests(unittest.TestCase):
    def setUp(self):
        self.engine = RetentionEngine(make_registry(), clock=fixed_clock)

    def test_active_retention_without_bypass_is_denied(self):
        decision = self.engine.evaluate_version(
            make_version(), DeleteRequest()
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.final_status, FinalStatus.RETAINED)
        self.assertFalse(decision.delete_marker_after)
        self.assertEqual(
            [item.code for item in decision.evidence],
            [RejectionCode.GOVERNANCE_RETENTION],
        )

    def test_bypass_header_without_authority_is_denied(self):
        decision = self.engine.evaluate_version(
            make_version(),
            DeleteRequest(bypass_header=True, bypass_authority=False),
        )
        self.assertFalse(decision.allowed)
        evidence = decision.evidence[0]
        self.assertEqual(evidence.code, RejectionCode.GOVERNANCE_RETENTION)
        self.assertFalse(evidence.detail["bypass_effective"])

    def test_bypass_header_authority_and_policy_allows_delete(self):
        decision = self.engine.evaluate_version(
            make_version(),
            DeleteRequest(bypass_header=True, bypass_authority=True),
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(
            decision.final_status, FinalStatus.DELETE_MARKER_CREATED
        )
        self.assertTrue(decision.delete_marker_after)
        self.assertEqual(decision.evidence, [])

    def test_bypass_denied_when_policy_switch_off(self):
        engine = RetentionEngine(
            make_registry(governance_bypass=False), clock=fixed_clock
        )
        decision = engine.evaluate_version(
            make_version(),
            DeleteRequest(bypass_header=True, bypass_authority=True),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(
            decision.evidence[0].code, RejectionCode.GOVERNANCE_RETENTION
        )
        self.assertFalse(
            decision.evidence[0].detail["policy_allows_bypass"]
        )

    def test_expired_governance_retention_allows_delete_without_bypass(self):
        decision = self.engine.evaluate_version(
            make_version(retention_until=PAST), DeleteRequest()
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(
            decision.final_status, FinalStatus.DELETE_MARKER_CREATED
        )

    def test_retention_boundary_is_inclusive(self):
        decision = self.engine.evaluate_version(
            make_version(retention_until=NOW), DeleteRequest()
        )
        self.assertTrue(decision.allowed)


class ComplianceModeTests(unittest.TestCase):
    def setUp(self):
        self.engine = RetentionEngine(make_registry(), clock=fixed_clock)

    def test_compliance_retention_cannot_be_bypassed(self):
        decision = self.engine.evaluate_version(
            make_version(retention_mode=RetentionMode.COMPLIANCE),
            DeleteRequest(bypass_header=True, bypass_authority=True),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.final_status, FinalStatus.RETAINED)
        self.assertFalse(decision.delete_marker_after)
        self.assertEqual(
            [item.code for item in decision.evidence],
            [RejectionCode.COMPLIANCE_RETENTION],
        )
        self.assertFalse(decision.evidence[0].detail["bypass_effective"])

    def test_compliance_retention_expired_allows_delete(self):
        decision = self.engine.evaluate_version(
            make_version(
                retention_mode=RetentionMode.COMPLIANCE,
                retention_until=PAST,
            ),
            DeleteRequest(bypass_header=True),
        )
        self.assertTrue(decision.allowed)

    def test_policy_flag_cannot_loosen_compliance(self):
        loose_policy = RetentionPolicy(
            version="loose",
            governance_bypass_allowed=True,
            compliance_bypass_allowed=True,
            legal_hold_bypass_allowed=True,
        )
        engine = RetentionEngine(
            PolicyRegistry([loose_policy]), clock=fixed_clock
        )
        decision = engine.evaluate_version(
            make_version(
                retention_mode=RetentionMode.COMPLIANCE,
                policy_version="loose",
            ),
            DeleteRequest(bypass_header=True, bypass_authority=True),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(
            decision.evidence[0].code,
            RejectionCode.COMPLIANCE_RETENTION,
        )


class LegalHoldTests(unittest.TestCase):
    def setUp(self):
        self.engine = RetentionEngine(make_registry(), clock=fixed_clock)

    def test_legal_hold_alone_blocks_delete(self):
        decision = self.engine.evaluate_version(
            make_version(
                retention_mode=RetentionMode.NONE,
                retention_until=None,
                legal_hold=True,
            ),
            DeleteRequest(),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(
            [item.code for item in decision.evidence],
            [RejectionCode.LEGAL_HOLD],
        )

    def test_legal_hold_and_time_retention_checked_separately(self):
        # 不带 Bypass：两项检查各自独立产生证据
        decision = self.engine.evaluate_version(
            make_version(
                retention_mode=RetentionMode.GOVERNANCE,
                legal_hold=True,
            ),
            DeleteRequest(),
        )
        self.assertFalse(decision.allowed)
        codes = [item.code for item in decision.evidence]
        self.assertEqual(
            codes,
            [
                RejectionCode.LEGAL_HOLD,
                RejectionCode.GOVERNANCE_RETENTION,
            ],
        )

    def test_legal_hold_remains_after_governance_retention_bypassed(self):
        # Governance 被有效绕过后时间保留不再拦截，但 Legal Hold 仍然独立拦截
        decision = self.engine.evaluate_version(
            make_version(
                retention_mode=RetentionMode.GOVERNANCE,
                legal_hold=True,
            ),
            DeleteRequest(bypass_header=True, bypass_authority=True),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(
            [item.code for item in decision.evidence],
            [RejectionCode.LEGAL_HOLD],
        )

    def test_legal_hold_blocks_even_when_governance_bypass_effective(self):
        decision = self.engine.evaluate_version(
            make_version(
                retention_mode=RetentionMode.GOVERNANCE,
                legal_hold=True,
            ),
            DeleteRequest(bypass_header=True, bypass_authority=True),
        )
        self.assertFalse(decision.allowed)
        legal_evidence = decision.evidence[0]
        self.assertEqual(
            legal_evidence.code, RejectionCode.LEGAL_HOLD
        )
        self.assertTrue(legal_evidence.detail["legal_hold"])

    def test_legal_hold_with_compliance_bypass_still_denied(self):
        decision = self.engine.evaluate_version(
            make_version(
                retention_mode=RetentionMode.COMPLIANCE,
                legal_hold=True,
            ),
            DeleteRequest(bypass_header=True, bypass_authority=True),
        )
        self.assertFalse(decision.allowed)
        codes = {item.code for item in decision.evidence}
        self.assertEqual(
            codes,
            {
                RejectionCode.LEGAL_HOLD,
                RejectionCode.COMPLIANCE_RETENTION,
            },
        )

    def test_legal_hold_persists_after_retention_expires(self):
        decision = self.engine.evaluate_version(
            make_version(
                retention_mode=RetentionMode.COMPLIANCE,
                retention_until=PAST,
                legal_hold=True,
            ),
            DeleteRequest(bypass_header=True, bypass_authority=True),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(
            [item.code for item in decision.evidence],
            [RejectionCode.LEGAL_HOLD],
        )


class DeleteMarkerAndBatchTests(unittest.TestCase):
    def setUp(self):
        self.engine = RetentionEngine(make_registry(), clock=fixed_clock)

    def test_existing_delete_marker_blocks_new_marker(self):
        decision = self.engine.evaluate_version(
            make_version(delete_marker=True),
            DeleteRequest(bypass_header=True, bypass_authority=True),
        )
        self.assertFalse(decision.allowed)
        self.assertTrue(decision.delete_marker_before)
        self.assertTrue(decision.delete_marker_after)
        self.assertEqual(
            decision.final_status, FinalStatus.DELETE_MARKER_PRESENT
        )
        self.assertEqual(
            decision.evidence[0].code,
            RejectionCode.DELETE_MARKER_PRESENT,
        )

    def test_batch_abort_leaves_no_partial_markers(self):
        clean = make_version(version_id="ver-clean")
        locked = make_version(
            version_id="ver-locked",
            retention_mode=RetentionMode.COMPLIANCE,
        )
        result = self.engine.evaluate_batch(
            [clean, locked],
            DeleteRequest(bypass_header=True, bypass_authority=True),
        )
        self.assertFalse(result.allowed)
        statuses = {
            decision.version_id: decision
            for decision in result.decisions
        }
        self.assertEqual(
            statuses["ver-clean"].final_status,
            FinalStatus.BATCH_ABORTED,
        )
        self.assertFalse(statuses["ver-clean"].delete_marker_after)
        self.assertEqual(
            statuses["ver-locked"].final_status, FinalStatus.RETAINED
        )
        self.assertEqual(
            statuses["ver-locked"].evidence[0].code,
            RejectionCode.COMPLIANCE_RETENTION,
        )

    def test_batch_succeeds_atomically_when_all_allowed(self):
        versions = [
            make_version(version_id="ver-a"),
            make_version(
                version_id="ver-b",
                retention_mode=RetentionMode.NONE,
                retention_until=None,
            ),
        ]
        result = self.engine.evaluate_batch(
            versions,
            DeleteRequest(bypass_header=True, bypass_authority=True),
        )
        self.assertTrue(result.allowed)
        self.assertTrue(
            all(
                decision.final_status
                == FinalStatus.DELETE_MARKER_CREATED
                for decision in result.decisions
            )
        )

    def test_batch_filters_requested_version_ids(self):
        versions = [
            make_version(version_id="ver-a"),
            make_version(
                version_id="ver-b",
                retention_mode=RetentionMode.NONE,
                retention_until=None,
            ),
        ]
        result = self.engine.evaluate_batch(
            versions, DeleteRequest(version_ids=["ver-b"])
        )
        self.assertTrue(result.allowed)
        self.assertEqual(len(result.decisions), 1)
        self.assertEqual(result.decisions[0].version_id, "ver-b")

    def test_unknown_policy_version_is_rejected(self):
        decision = self.engine.evaluate_version(
            make_version(policy_version="missing"),
            DeleteRequest(),
        )
        self.assertFalse(decision.allowed)
        self.assertIn(
            RejectionCode.UNKNOWN_POLICY,
            [item.code for item in decision.evidence],
        )


class PolicyUpdateTests(unittest.TestCase):
    def setUp(self):
        self.registry = make_registry()
        self.engine = RetentionEngine(
            self.registry, clock=fixed_clock
        )

    def test_new_policy_version_changes_bypass_behavior(self):
        allowed_before = self.engine.evaluate_version(
            make_version(policy_version="v1"),
            DeleteRequest(bypass_header=True, bypass_authority=True),
        )
        self.assertTrue(allowed_before.allowed)

        self.registry.register(
            RetentionPolicy(
                version="v3",
                governance_bypass_allowed=False,
            )
        )
        denied_after = self.engine.evaluate_version(
            make_version(policy_version="v3"),
            DeleteRequest(bypass_header=True, bypass_authority=True),
        )
        self.assertFalse(denied_after.allowed)
        self.assertEqual(
            denied_after.evidence[0].code,
            RejectionCode.GOVERNANCE_RETENTION,
        )

    def test_policy_update_does_not_affect_old_version_bindings(self):
        self.registry.register(
            RetentionPolicy(
                version="v1",
                governance_bypass_allowed=False,
            )
        )
        decision = self.engine.evaluate_version(
            make_version(policy_version="v1"),
            DeleteRequest(bypass_header=True, bypass_authority=True),
        )
        self.assertFalse(decision.allowed)

    def test_request_level_policy_version_overrides_binding(self):
        decision = self.engine.evaluate_version(
            make_version(policy_version="v1"),
            DeleteRequest(
                bypass_header=True,
                bypass_authority=True,
                policy_version="v2",
            ),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.policy_version, "v2")


if __name__ == "__main__":
    unittest.main()
