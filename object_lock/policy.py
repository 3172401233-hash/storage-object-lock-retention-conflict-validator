"""离线保留策略注册表。

所有策略均为离线配置：本模块只做本地内存/文件读取，
不发起任何网络请求，也不依赖外部控制面。
"""

import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .models import RejectionCode, RejectionEvidence


@dataclass(frozen=True)
class RetentionPolicy:
    """一条版本化的离线保留策略。

    :param version: 策略版本号
    :param governance_bypass_allowed: 该策略是否允许具备授权的调用方
        携带 Bypass 头绕过 Governance 模式
    :param compliance_bypass_allowed: 合规模式是否可被绕过，固定应为 False
    :param legal_hold_bypass_allowed: Legal Hold 是否可被绕过，固定应为 False
    """

    version: str
    governance_bypass_allowed: bool = False
    compliance_bypass_allowed: bool = False
    legal_hold_bypass_allowed: bool = False

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "RetentionPolicy":
        return cls(
            version=str(payload["version"]),
            governance_bypass_allowed=bool(
                payload.get("governance_bypass_allowed", False)
            ),
            compliance_bypass_allowed=bool(
                payload.get("compliance_bypass_allowed", False)
            ),
            legal_hold_bypass_allowed=bool(
                payload.get("legal_hold_bypass_allowed", False)
            ),
        )


class PolicyRegistry:
    """内存型离线策略注册表，支持版本化更新与快照读取。"""

    def __init__(self, policies: Optional[Iterable[RetentionPolicy]] = None):
        self._policies: Dict[str, RetentionPolicy] = {}
        if policies:
            for policy in policies:
                self.register(policy)

    @classmethod
    def from_file(cls, path: str) -> "PolicyRegistry":
        """从本地 JSON 文件加载离线配置，不访问网络。"""
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, list):
            raise ValueError("离线策略文件必须是策略对象列表")
        return cls.from_dict_list(payload)

    @classmethod
    def from_dict_list(
        cls, payload: List[Dict[str, Any]]
    ) -> "PolicyRegistry":
        """从已解析的 dict 列表构建离线注册表。"""
        return cls(
            RetentionPolicy.from_dict(item)
            for item in payload
        )

    def register(self, policy: RetentionPolicy) -> None:
        """注册或更新一个策略版本（离线配置推送）。"""
        self._policies[policy.version] = policy

    def versions(self) -> List[str]:
        return sorted(self._policies.keys())

    def get(
        self, version: str
    ) -> Tuple[Optional[RetentionPolicy], Optional[RejectionEvidence]]:
        """按版本号取策略；不存在时返回 (None, 拒绝证据)。"""
        policy = self._policies.get(version)
        if policy is None:
            evidence = RejectionEvidence(
                code=RejectionCode.UNKNOWN_POLICY,
                reason="引用了未离线注册的策略版本",
                detail={"policy_version": version},
            )
            return None, evidence
        return policy, None
