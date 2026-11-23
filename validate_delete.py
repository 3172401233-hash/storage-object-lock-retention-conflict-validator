#!/usr/bin/env python3
"""离线删除校验入口。

读取本地 JSON 输入，输出每个对象版本的最终状态与拒绝证据。
全程不访问网络，所有策略均来自输入中的离线配置。

输入 JSON 结构示例见 examples/request.json。
"""

import argparse
import json
from datetime import datetime
from typing import Any, Dict, List

from object_lock import (
    DeleteRequest,
    ObjectVersion,
    PolicyRegistry,
    RetentionEngine,
    RetentionMode,
)


def _parse_time(value: Any) -> datetime:
    if value is None:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def _build_versions(payload: List[Dict[str, Any]]) -> List[ObjectVersion]:
    versions = []
    for item in payload:
        versions.append(
            ObjectVersion(
                version_id=str(item["version_id"]),
                retention_mode=RetentionMode(
                    item.get("retention_mode", "NONE")
                ),
                retention_until=_parse_time(item.get("retention_until")),
                legal_hold=bool(item.get("legal_hold", False)),
                delete_marker=bool(item.get("delete_marker", False)),
                policy_version=str(item["policy_version"]),
                object_key=item.get("object_key"),
            )
        )
    return versions


def run(payload: Dict[str, Any]) -> Dict[str, Any]:
    registry = PolicyRegistry.from_dict_list(payload["policies"])
    clock = None
    if payload.get("now"):
        fixed_now = _parse_time(payload["now"])
        clock = lambda: fixed_now
    engine = RetentionEngine(registry, clock=clock)

    request_payload = payload.get("request", {})
    request = DeleteRequest(
        bypass_header=bool(request_payload.get("bypass_header", False)),
        bypass_authority=bool(
            request_payload.get("bypass_authority", False)
        ),
        policy_version=(
            str(request_payload["policy_version"])
            if request_payload.get("policy_version") is not None
            else None
        ),
        version_ids=request_payload.get("version_ids"),
    )

    versions = _build_versions(payload["versions"])
    result = engine.evaluate_batch(versions, request)
    return result.to_dict()


def main() -> None:
    parser = argparse.ArgumentParser(description="离线对象锁删除校验器")
    parser.add_argument("input", help="本地 JSON 输入文件路径")
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    print(json.dumps(run(payload), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
