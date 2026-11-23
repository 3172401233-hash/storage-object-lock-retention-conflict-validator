"""对象锁删除校验的数据模型。

仅使用 Python 3.8 标准库能力，不引入任何第三方依赖。
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class RetentionMode(str, Enum):
    """对象版本上的保留模式。"""

    NONE = "NONE"
    GOVERNANCE = "GOVERNANCE"
    COMPLIANCE = "COMPLIANCE"


class RejectionCode(str, Enum):
    """拒绝删除的证据码。"""

    LEGAL_HOLD = "LEGAL_HOLD"
    GOVERNANCE_RETENTION = "GOVERNANCE_RETENTION"
    COMPLIANCE_RETENTION = "COMPLIANCE_RETENTION"
    DELETE_MARKER_PRESENT = "DELETE_MARKER_PRESENT"
    UNKNOWN_POLICY = "UNKNOWN_POLICY"


class FinalStatus(str, Enum):
    """对象版本在校验后的最终状态。"""

    DELETE_MARKER_CREATED = "DELETE_MARKER_CREATED"
    RETAINED = "RETAINED"
    BATCH_ABORTED = "BATCH_ABORTED"
    DELETE_MARKER_PRESENT = "DELETE_MARKER_PRESENT"


def to_utc(value: Optional[datetime]) -> Optional[datetime]:
    """把时间统一为带 UTC 时区的时间；naive 时间按 UTC 解释。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class ObjectVersion:
    """一个对象版本及其锁/保留状态。

    :param version_id: 版本 ID
    :param retention_mode: 保留模式（NONE/GOVERNANCE/COMPLIANCE）
    :param retention_until: 保留截止时间，None 表示无时间保留
    :param legal_hold: 是否处于 Legal Hold
    :param delete_marker: 当前是否已存在删除标记
    :param policy_version: 该版本绑定的策略版本
    :param object_key: 所属对象键，仅用于证据展示
    """

    version_id: str
    retention_mode: RetentionMode
    retention_until: Optional[datetime]
    legal_hold: bool
    delete_marker: bool
    policy_version: str
    object_key: Optional[str] = None

    def normalized_until(self) -> Optional[datetime]:
        return to_utc(self.retention_until)


@dataclass(frozen=True)
class DeleteRequest:
    """一次删除请求（单版本或批量共用）。

    :param bypass_header: 请求是否携带 Governance 绕过头
    :param bypass_authority: 调用方是否具备绕过 Governance 的离线授权
    :param policy_version: 本次请求要求使用的策略版本，None 表示按各版本绑定值
    :param version_ids: 限定删除的版本 ID，None 表示目标列表中的全部版本
    """

    bypass_header: bool = False
    bypass_authority: bool = False
    policy_version: Optional[str] = None
    version_ids: Optional[List[str]] = None

    def governance_bypass_effective(self, policy_bypass_allowed: bool) -> bool:
        """Governance 绕过只有在头、授权、策略三者同时满足时才生效。"""
        return bool(
            self.bypass_header
            and self.bypass_authority
            and policy_bypass_allowed
        )


@dataclass(frozen=True)
class RejectionEvidence:
    """单条拒绝证据，Legal Hold 与时间保留分别出条。"""

    code: RejectionCode
    reason: str
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code.value,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass
class VersionDecision:
    """单个版本的判定结果。"""

    version_id: str
    allowed: bool
    final_status: FinalStatus
    delete_marker_before: bool
    delete_marker_after: bool
    policy_version: str
    evidence: List[RejectionEvidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version_id": self.version_id,
            "allowed": self.allowed,
            "final_status": self.final_status.value,
            "delete_marker_before": self.delete_marker_before,
            "delete_marker_after": self.delete_marker_after,
            "policy_version": self.policy_version,
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass
class BatchResult:
    """一次批量删除的整体结果。

    只要任一版本被拒绝，整批中止，任何版本都不会出现新的删除标记。
    """

    allowed: bool
    policy_version: Optional[str]
    decisions: List[VersionDecision]

    def rejected_versions(self) -> List[VersionDecision]:
        return [decision for decision in self.decisions if not decision.allowed]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "policy_version": self.policy_version,
            "decisions": [decision.to_dict() for decision in self.decisions],
        }
