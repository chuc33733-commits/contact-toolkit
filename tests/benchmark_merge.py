# -*- coding: utf-8 -*-
"""
新旧去重算法对比基准：
- 旧算法：O(n^2) 全量两两比较（复刻原 find_duplicate_groups 实现）
- 新算法：分块索引 + 特征缓存
验证两者在真实数据上的分组结果一致，并对比耗时。
"""

import os
import sys
import time

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from models.contact import Contact
from processors.vcf_processor import VCFProcessor
from utils.merger import ContactMerger


def old_find_duplicate_groups(merger: ContactMerger, contacts):
    """复刻优化前的 O(n^2) 全量两两比较实现，用于对比"""
    if not contacts:
        return []
    parent = list(range(len(contacts)))

    def find(x):
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(x, y):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    for i in range(len(contacts)):
        for j in range(i + 1, len(contacts)):
            should_merge, _ = merger.should_merge_contacts(contacts[i], contacts[j])
            if should_merge:
                union(i, j)

    groups = {}
    for i, contact in enumerate(contacts):
        root = find(i)
        groups.setdefault(root, []).append(contact)
    return [g for g in groups.values() if len(g) > 1]


def group_signature(groups):
    """生成组签名用于结果一致性比较（按姓名+电话排序的指纹）"""
    sig = []
    for group in groups:
        members = sorted((c.name, tuple(sorted(c.phones))) for c in group)
        sig.append((len(group), tuple(members)))
    return sorted(sig)


def main():
    processor = VCFProcessor()
    merger = ContactMerger()

    vcf_path = os.path.join(project_root, "所有联系人.vcf")
    manager, stats, error = processor.import_from_vcf(vcf_path)
    if error:
        print(f"导入失败: {error}")
        sys.exit(1)

    base_contacts = manager.get_all_contacts()
    print(f"真实数据: {len(base_contacts)} 个联系人 (编码: {stats.get('file_encoding')})")

    # ---- 真实数据：结果一致性 + 耗时对比 ----
    start = time.perf_counter()
    old_groups = old_find_duplicate_groups(merger, base_contacts)
    old_time = time.perf_counter() - start

    start = time.perf_counter()
    new_groups = merger.find_duplicate_groups(base_contacts)
    new_time = time.perf_counter() - start

    same = group_signature(old_groups) == group_signature(new_groups)
    print(f"\n[真实数据 {len(base_contacts)} 条]")
    print(f"  旧算法: {old_time:.3f}s, 重复组 {len(old_groups)} 个")
    print(f"  新算法: {new_time:.3f}s, 重复组 {len(new_groups)} 个")
    print(f"  分组结果一致: {same}")
    if new_time > 0:
        print(f"  加速比: {old_time / new_time:.1f}x")

    # ---- 放大 5 倍数据：性能压力对比 ----
    scaled = base_contacts * 5
    start = time.perf_counter()
    old_groups_s = old_find_duplicate_groups(merger, scaled)
    old_time_s = time.perf_counter() - start

    start = time.perf_counter()
    new_groups_s = merger.find_duplicate_groups(scaled)
    new_time_s = time.perf_counter() - start

    same_s = group_signature(old_groups_s) == group_signature(new_groups_s)
    print(f"\n[放大数据 {len(scaled)} 条]")
    print(f"  旧算法: {old_time_s:.3f}s, 重复组 {len(old_groups_s)} 个")
    print(f"  新算法: {new_time_s:.3f}s, 重复组 {len(new_groups_s)} 个")
    print(f"  分组结果一致: {same_s}")
    if new_time_s > 0:
        print(f"  加速比: {old_time_s / new_time_s:.1f}x")

    # ---- 姓名空格清理端到端验证 ----
    print("\n[姓名空格清理验证]")
    space_names = [c.name for c in base_contacts if " " in c.name or "　" in c.name]
    print(f"  导入后姓名仍含空格的数量: {len(space_names)} (应为 0)")


if __name__ == "__main__":
    main()
