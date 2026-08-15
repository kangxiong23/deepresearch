#!/usr/bin/env python3
# One-time 维护脚本：修复 tree.json 中“assistant 先于 thinking”的消息顺序错误。
#
# 旧格式（v2.0 及以下）中，一轮回复由三个并列 MessageNode 组成：
#     user → thinking → assistant
# 若数据里出现 assistant 紧邻 thinking 且顺序颠倒（assistant 在前），
# 本脚本将其交换为 thinking → assistant。
#
# 默认 dry-run：只打印受影响对话，不改文件。
# 加 --execute 才真正重写 tree.json（原子写入）。
#
# 用法（在项目根目录运行）：
#   python scripts/fix_thinking_order.py            # dry-run
#   python scripts/fix_thinking_order.py --execute  # 真正修复

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

# 确保项目根目录在 sys.path 中，使 `import config` 可用
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as app_config


def _reorder_group(nodes: list[dict]) -> list[dict]:
    """交换相邻的 assistant→thinking 为 thinking→assistant，直至收敛。"""
    order = list(nodes)
    changed = True
    while changed:
        changed = False
        for i in range(len(order) - 1):
            a, b = order[i], order[i + 1]
            if a.get("role") == "assistant" and b.get("role") == "thinking":
                order[i], order[i + 1] = b, a
                changed = True
    return order


def _atomic_replace(src: Path, dst: Path) -> None:
    os.replace(src, dst)


def main() -> int:
    execute = "--execute" in sys.argv[1:]

    tree_path = Path(app_config.TREE_STORE_PATH) / "tree.json"
    if not tree_path.exists():
        print("[fix_thinking_order] tree.json 不存在，跳过")
        return 0

    try:
        data = json.loads(tree_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[fix_thinking_order] 读取失败: {e}")
        return 1

    nodes = data.get("nodes", [])
    if not isinstance(nodes, list):
        print("[fix_thinking_order] tree.json 结构异常，跳过")
        return 1

    by_parent: dict[str, list[dict]] = defaultdict(list)
    for n in nodes:
        pid = n.get("parent_id")
        by_parent[pid if pid is not None else ""].append(n)

    affected: list[tuple[str, list[dict]]] = []
    for pid, children in by_parent.items():
        message_children = [c for c in children if c.get("node_type") == "message"]
        if not message_children:
            continue
        # 消息节点应只与消息节点同父；若混入非消息节点则跳过（防御）
        if len(message_children) != len(children):
            print(f"[fix_thinking_order] 父节点 {pid!r} 混有非消息子节点，跳过")
            continue
        message_children.sort(key=lambda n: n.get("sort_order", 0))
        reordered = _reorder_group(message_children)
        if [n.get("id") for n in reordered] != [n.get("id") for n in message_children]:
            affected.append((pid, reordered))

    if not affected:
        print("[fix_thinking_order] 未发现顺序错误，无需修复")
        return 0

    print(f"[fix_thinking_order] 发现 {len(affected)} 个对话存在顺序错误：")
    for pid, reordered in affected:
        seq = [f"{n.get('role')}({n.get('id', '')[:8]})" for n in reordered]
        print(f"  父节点 {pid!r}: {' -> '.join(seq)}")

    if execute:
        for pid, reordered in affected:
            # 重写该父节点下的消息节点顺序并重排 sort_order
            for i, n in enumerate(reordered):
                n["sort_order"] = i
            # 替换回 nodes 列表中属于该父节点的消息节点
            pid_key = pid or None
            to_remove = {
                n.get("id")
                for n in by_parent[pid]
                if n.get("node_type") == "message"
            }
            data["nodes"] = [n for n in data["nodes"] if n.get("id") not in to_remove]
            for n in reordered:
                n["parent_id"] = pid_key
                data["nodes"].append(n)

        tmp_path = tree_path.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _atomic_replace(tmp_path, tree_path)
        print(f"[fix_thinking_order] 已修复并写入 {tree_path}")
    else:
        print("[fix_thinking_order] dry-run 模式：未做任何修改（加 --execute 真正修复）")

    return 0


if __name__ == "__main__":
    sys.exit(main())
