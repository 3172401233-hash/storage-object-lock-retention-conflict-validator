"""对象锁删除判定引擎。

判定顺序与语义：

1. 已有删除标记的版本不再生成新标记（不能出现部分可见标记）。
2. Legal Hold 与时间保留分别检查、分别出证据；Legal Hold 不可被任何绕过突破。
3. 保留期已过（now >= retention_until）时，时间保留不再拦截。
4. Compliance 保留在生效期内不可被普通 Bypass 突破。
5. Governance 保留仅在 Bypass 头、调用方授权、策略开关三者同时满足时可绕过。
6. 批量删除只要有一个版本被拒绝即整批中止，不产生任何新删除标记。
"""

from datetime import datetime, timezone
from typing import Callable, List, Optional, Sequence

from .models import (
    BatchResult,
    DeleteRequest,
    FinalStatus,
    ObjectVersion,
    RejectionCode,
    RejectionEvidence,
    RetentionMode,
    VersionDecision,
)
from .policy import PolicyRegistry, RetentionPolicy


class RetentionEngine:
    """基于离线策略注册表的删除校验引擎。"""

    def __init__(
        self,
        registry: PolicyRegistry,
        clock: Optional[Callable[[], datetime]] = None,
    ):
        self._registry = registry
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def evaluate_version(
        self,
        version: ObjectVersion,
        request: DeleteRequest,
    ) -> VersionDecision:
        """评估单个版本的删除请求。"""
        now = self._clock()
        policy_version = request.policy_version or version.policy_version

        if version.delete_marker:
            return VersionDecision(
                version_id=version.version_id,
                allowed=False,
                final_status=FinalStatus.DELETE_MARKER_PRESENT,
                delete_marker_before=True,
                delete_marker_after=True,
                policy_version=policy_version,
                evidence=[
                    RejectionEvidence(
                        code=RejectionCode.DELETE_MARKER_PRESENT,
                        reason="版本已存在删除标记，不能再生成可见删除标记",
                        detail={"version_id": version.version_id},
                    )
                ],
            )

        policy, unknown_evidence = self._registry.get(policy_version)
        evidence: List[RejectionEvidence] = []
        if unknown_evidence is not None:
            evidence.append(unknown_evidence)

        evidence.extend(
            self._legal_hold_evidence(version)
        )
        evidence.extend(
            self._retention_evidence(version, request, policy, now)
        )

        allowed = not evidence
        return VersionDecision(
            version_id=version.version_id,
            allowed=allowed,
            final_status=(
                FinalStatus.DELETE_MARKER_CREATED
                if allowed
                else FinalStatus.RETAINED
            ),
            delete_marker_before=False,
            delete_marker_after=allowed,
            policy_version=policy_version,
            evidence=evidence,
        )

    def evaluate_batch(
        self,
        versions: Sequence[ObjectVersion],
        request: DeleteRequest,
    ) -> BatchResult:
        """评估批量删除；任一版本被拒绝则整批中止，保证原子性。"""
        targets = self._select_targets(versions, request.version_ids)
        decisions = [
            self.evaluate_version(version, request) for version in targets
        ]

        rejected = [decision for decision in decisions if not decision.allowed]
        if rejected:
            for decision in decisions:
                if decision.allowed:
                    decision.allowed = False
                    decision.final_status = FinalStatus.BATCH_ABORTED
                    decision.delete_marker_after = False

        return BatchResult(
            allowed=not rejected,
            policy_version=request.policy_version,
            decisions=decisions,
        )

    @staticmethod
    def _select_targets(
        versions: Sequence[ObjectVersion],
        version_ids: Optional[List[str]],
    ) -> List[ObjectVersion]:
        if version_ids is None:
            return list(versions)
        wanted = set(version_ids)
        selected = [
            version for version in versions if version.version_id in wanted
        ]
        found = {version.version_id for version in selected}
        missing = wanted - found
        if missing:
            raise KeyError("批量请求包含未知版本 ID: %s" % sorted(missing))
        return selected

    @staticmethod
    def _legal_hold_evidence(
        version: ObjectVersion,
    ) -> List[RejectionEvidence]:
        if not version.legal_hold:
            return []
        return [
            RejectionEvidence(
                code=RejectionCode.LEGAL_HOLD,
                reason="版本处于 Legal Hold，任何 Bypass 都不能突破",
                detail={
                    "version_id": version.version_id,
                    "legal_hold": True,
                },
            )
        ]

    @staticmethod
    def _retention_evidence(
        version: ObjectVersion,
        request: DeleteRequest,
        policy: Optional[RetentionPolicy],
        now: datetime,
    ) -> List[RejectionEvidence]:
        until = version.normalized_until()
        if until is None or until <= now:
            return []

        mode = version.retention_mode
        detail = {
            "version_id": version.version_id,
            "mode": mode.value,
            "retention_until": until.isoformat(),
            "evaluated_at": now.isoformat(),
            "bypass_header": request.bypass_header,
        }

        if mode == RetentionMode.COMPLIANCE:
            detail["bypass_effective"] = False
            return [
                RejectionEvidence(
                    code=RejectionCode.COMPLIANCE_RETENTION,
                    reason="Compliance 保留期内不可被普通 Bypass 突破",
                    detail=detail,
                )
            ]

        if mode == RetentionMode.GOVERNANCE:
            policy_allows = (
                policy.governance_bypass_allowed if policy is not None else False
            )
            bypass_effective = request.governance_bypass_effective(
                policy_allows
            )
            detail["policy_allows_bypass"] = policy_allows
            detail["bypass_authority"] = request.bypass_authority
            detail["bypass_effective"] = bypass_effective
            if bypass_effective:
                return []
            return [
                RejectionEvidence(
                    code=RejectionCode.GOVERNANCE_RETENTION,
                    reason=(
                        "Governance 保留期内，Bypass 头/授权/策略开关未同时满足"
                    ),
                    detail=detail,
                )
            ]

        return []
