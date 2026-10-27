#!/usr/bin/env python3
# One-time 维护脚本：把旧式节点附件（attachment_paths，只记路径）迁移为文件快照挂载（mounts）。
#
# 背景：attachment_paths 每次构建上下文都从磁盘重读，源文件被移动/删除后
# 资料就静默消失。新式 mounts 是内容寻址快照（data/files/<sha256>），
# 与磁盘无关。
#
# 迁移上限（拒绝无上限的静默复制）：
#   单文件 20 MB、单节点累计 200 MB。超限或文件缺失的条目**保留原路径**，
#   脚本逐条报告，由用户决定是删掉还是挪到更小的目录。
#
# 默认 dry-run：只统计并打印将要做什么，不做任何修改。
# 加 --execute 才真正复制快照并改写 tree.json。
# 加 --gc 顺带清理没有任何节点引用的孤儿 blob（默认不动）。
#
# 用法（在项目根目录运行）：
#   python scripts/migrate_attachments_to_mounts.py            # dry-run
#   python scripts/migrate_attachments_to_mounts.py --execute  # 真正迁移
#   python scripts/migrate_attachments_to_mounts.py --execute --gc
#
# ⚠️ 该脚本会重写 tree.json。运行前请先备份 data/ 目录。

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as app_config  # noqa: E402
from app.storage.blob_store import (  # noqa: E402
    MAX_BLOB_BYTES,
    MAX_NODE_BYTES,
    BlobTooLarge,
    FileBlobStore,
    node_mounts_bytes,
)
from app.storage.models import ConversationNode, FolderNode  # noqa: E402
from app.storage.tree_store import TreeStore  # noqa: E402


def migrate(execute: bool) -> dict:
    """扫描整棵树，把 attachment_paths 快照为 mounts。返回统计结果。"""
    store = TreeStore()
    blobs = FileBlobStore()
    tree = store.get_tree()

    stats = {
        "nodes": 0,          # 有旧式附件的节点数
        "paths": 0,          # 旧式附件条目总数
        "migrated": 0,       # 成功快照并改写
        "missing": 0,        # 文件不存在
        "too_large": 0,      # 超过单文件/单节点上限
        "duplicate": 0,      # 同一 blob 已挂载过
        "bytes": 0,          # 快照总字节数
    }

    for node in list(tree.nodes):
        if not isinstance(node, (FolderNode, ConversationNode)):
            continue
        paths = list(getattr(node, "attachment_paths", []) or [])
        if not paths:
            continue

        stats["nodes"] += 1
        stats["paths"] += len(paths)
        remaining: list[str] = []
        mounts = list(getattr(node, "mounts", []) or [])
        seen_blobs = {str(m.get("blob_id") or "") for m in mounts}
        used = node_mounts_bytes(mounts)

        for path in paths:
            name = Path(path).name or path
            if not Path(path).exists():
                stats["missing"] += 1
                print(f"  [缺失] {path}  ← 保留原路径")
                remaining.append(path)
                continue

            try:
                record = blobs.put(path, max_bytes=MAX_BLOB_BYTES)
            except BlobTooLarge as e:
                stats["too_large"] += 1
                print(f"  [超限] {e}  ← 保留原路径")
                remaining.append(path)
                continue
            except OSError as e:
                stats["too_large"] += 1
                print(f"  [不可读] {path}: {e}  ← 保留原路径")
                remaining.append(path)
                continue

            if record["blob_id"] in seen_blobs:
                stats["duplicate"] += 1
                continue

            if used + record["size"] > MAX_NODE_BYTES:
                stats["too_large"] += 1
                print(f"  [节点超限] {path}  ← 保留原路径")
                remaining.append(path)
                continue

            seen_blobs.add(record["blob_id"])
            used += record["size"]
            mounts.append(record)
            stats["migrated"] += 1
            stats["bytes"] += record["size"]
            print(f"  [快照] {name} ({record['size']} bytes)")

        if execute and (mounts != list(getattr(node, "mounts", []) or [])):
            store.update_node(node.id, mounts=mounts, attachment_paths=remaining)

    return stats


def gc_orphans(execute: bool) -> int:
    """删除没有任何节点引用的 blob。返回候选/删除数量。"""
    store = TreeStore()
    blobs = FileBlobStore()
    referenced: set[str] = set()
    for node in store.get_tree().nodes:
        for mount in getattr(node, "mounts", []) or []:
            blob_id = str((mount or {}).get("blob_id") or "")
            if blob_id:
                referenced.add(blob_id)

    orphans = [b for b in blobs.all_blob_ids() if b not in referenced]
    for blob_id in orphans:
        size = blobs.blob_path(blob_id).stat().st_size if blobs.exists(blob_id) else 0
        print(f"  [孤儿] {blob_id[:12]}… ({size} bytes)")
        if execute:
            blobs.delete(blob_id)
    return len(orphans)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="把旧式 attachment_paths 迁移为文件快照挂载（默认 dry-run）"
    )
    parser.add_argument("--execute", action="store_true", help="真正执行（默认只打印）")
    parser.add_argument("--gc", action="store_true", help="顺带清理孤儿 blob")
    args = parser.parse_args()

    print(f"[migrate] tree: {app_config.TREE_STORE_PATH}")
    print(f"[migrate] files: {app_config.FILES_STORE_PATH}")
    print(f"[migrate] 模式: {'EXECUTE' if args.execute else 'DRY-RUN'}")
    print()

    stats = migrate(args.execute)
    print()
    print(
        f"[migrate] 节点 {stats['nodes']} 个 / 旧式条目 {stats['paths']} 条 → "
        f"快照 {stats['migrated']} 条 ({stats['bytes'] / 1024 / 1024:.1f} MB)，"
        f"已存在 {stats['duplicate']}，缺失 {stats['missing']}，超限 {stats['too_large']}"
    )

    if args.gc:
        print()
        print("[migrate] 清理孤儿 blob")
        count = gc_orphans(args.execute)
        print(f"[migrate] 孤儿 blob {count} 个")

    if not args.execute:
        print()
        print("[migrate] 这是 dry-run，没有改动任何文件。确认无误后加 --execute。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
