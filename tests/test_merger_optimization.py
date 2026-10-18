# -*- coding: utf-8 -*-
"""
联系人合并优化专项测试
覆盖：姓名空格清理、分块索引正确性、大规模数据性能
"""

import os
import sys
import time
import unittest

# 添加项目根目录到Python路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from models.contact import Contact, ContactManager
from utils.merger import ContactMerger


def make_contact(name: str = "", phones=None, emails=None, org: str = "") -> Contact:
    """构造测试联系人的辅助函数"""
    contact = Contact()
    if name:
        contact.set_name(name)
    for phone in (phones or []):
        contact.add_phone(phone)
    for email in (emails or []):
        contact.add_email(email)
    if org:
        contact.set_organization(org)
    return contact


class TestNameSpaceCleaning(unittest.TestCase):
    """姓名空格清理测试"""

    def test_set_name_removes_spaces(self):
        """set_name 应删除姓名中的所有空格"""
        contact = Contact()
        contact.set_name("张 三")
        self.assertEqual(contact.name, "张三")

    def test_set_name_removes_fullwidth_spaces(self):
        """全角空格也应被删除"""
        contact = Contact()
        contact.set_name("张　三")  # U+3000 全角空格
        self.assertEqual(contact.name, "张三")

    def test_set_name_removes_tabs_and_newlines(self):
        """制表符/换行等空白字符也应被删除"""
        contact = Contact()
        contact.set_name("李\t四\n")
        self.assertEqual(contact.name, "李四")

    def test_normalize_name_keeps_other_chars(self):
        """空格清理不应影响姓名中的其他字符"""
        self.assertEqual(Contact.normalize_name("O'Brien Smith"), "O'BrienSmith")
        self.assertEqual(Contact.normalize_name("安娜-Maria 王"), "安娜-Maria王")

    def test_space_cleaning_does_not_affect_other_fields(self):
        """姓名空格清理不影响电话/邮箱等其他字段"""
        contact = Contact()
        contact.set_name("张 三")
        contact.add_phone("138 0013 8000")
        contact.add_email("zhang@example.com")
        self.assertEqual(contact.name, "张三")
        # 电话按自身规则标准化（去非数字字符），不受姓名清理影响
        self.assertEqual(contact.phones, ["13800138000"])
        self.assertEqual(contact.emails, ["zhang@example.com"])

    def test_merge_prefers_longer_name_ignoring_spaces(self):
        """合并时按去空格后的有效长度选择姓名，且结果不含空格"""
        c1 = make_contact(name="张 三 丰")   # 有效长度 3
        c2 = make_contact(name="张三丰")     # 有效长度 3
        merged = c1.merge_with(c2)
        self.assertNotIn(" ", merged.name)
        self.assertEqual(merged.name, "张三丰")


class TestBlockingCorrectness(unittest.TestCase):
    """分块索引与全量比较结果一致性测试"""

    def setUp(self):
        self.merger = ContactMerger()

    def test_space_variant_names_merged(self):
        """带空格与不带空格的同姓名联系人应被识别合并"""
        contacts = [
            make_contact(name="张三", phones=["13800138000"]),
            make_contact(name="张 三", phones=["13900139000"]),
            make_contact(name="李四", phones=["13700137000"]),
        ]
        groups = self.merger.find_duplicate_groups(contacts)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 2)

    def test_transitive_grouping_via_blocking(self):
        """间接重复（A-B 电话同，B-C 邮箱同）应归为一组"""
        contacts = [
            make_contact(name="甲", phones=["13800138000"]),
            make_contact(name="乙", phones=["13800138000"], emails=["same@example.com"]),
            make_contact(name="丙", emails=["same@example.com"]),
            make_contact(name="丁", phones=["13700137000"]),
        ]
        groups = self.merger.find_duplicate_groups(contacts)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 3)

    def test_similar_name_same_org_merged(self):
        """相似姓名 + 相同公司应合并"""
        contacts = [
            make_contact(name="张三", org="阿里巴巴"),
            make_contact(name="张 三", phones=["13800138000"], org="阿里巴巴"),
            make_contact(name="张三", phones=["13900139000"], org="腾讯"),
        ]
        groups = self.merger.find_duplicate_groups(contacts)
        # 前两个同公司同姓名合并；第三个姓名相同但公司不同，
        # 由于姓名规范后完全相同（相似度1.0），按"相同姓名"规则也会合并
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 3)

    def test_unrelated_contacts_not_merged(self):
        """无关联系人不应被误合并"""
        contacts = [
            make_contact(name="张三", phones=["13800138000"]),
            make_contact(name="李四", phones=["13900139000"]),
            make_contact(name="王五", emails=["wang@example.com"]),
        ]
        groups = self.merger.find_duplicate_groups(contacts)
        self.assertEqual(len(groups), 0)

    def test_short_phone_exact_match_only(self):
        """短号码（不足匹配位数）只有全等才合并"""
        contacts = [
            make_contact(name="甲", phones=["12345"]),
            make_contact(name="乙", phones=["12345"]),
            make_contact(name="丙", phones=["9912345"]),  # 后缀含12345但规则要求全等
        ]
        groups = self.merger.find_duplicate_groups(contacts)
        self.assertEqual(len(groups), 1)
        names = {c.name for c in groups[0]}
        self.assertEqual(names, {"甲", "乙"})


class TestMergePerformance(unittest.TestCase):
    """大规模数据性能测试"""

    def test_large_dataset_completes_quickly(self):
        """5000 个联系人（含 10% 重复）应在数秒内完成分组"""
        merger = ContactMerger()
        contacts = []
        for i in range(5000):
            contacts.append(make_contact(
                name=f"联系人{i}",
                phones=[f"138{i % 4500:08d}"[-11:].rjust(11, '1')],
                emails=[f"user{i}@example.com"],
                org=f"公司{i % 100}",
            ))
        start = time.perf_counter()
        groups = merger.find_duplicate_groups(contacts)
        elapsed = time.perf_counter() - start
        # 分块索引下应在 10 秒内完成（旧 O(n^2) 实现需要分钟级）
        self.assertLess(elapsed, 10.0, f"5000 联系人分组耗时 {elapsed:.2f}s，超出预期")
        # 应识别出重复组（号码按 4500 取模，必然产生重复）
        self.assertGreater(len(groups), 0)

    def test_merge_duplicates_in_manager_stats(self):
        """管理器合并统计信息应正确"""
        merger = ContactMerger()
        manager = ContactManager()
        manager.add_contact(make_contact(name="张三", phones=["13800138000"]))
        manager.add_contact(make_contact(name="张 三", phones=["13800138000"]))
        manager.add_contact(make_contact(name="李四", phones=["13900139000"]))

        stats = merger.merge_duplicates_in_manager(manager)

        self.assertEqual(stats['total_contacts_before'], 3)
        self.assertEqual(stats['duplicate_groups_found'], 1)
        self.assertEqual(stats['contacts_merged'], 1)
        self.assertEqual(stats['total_contacts_after'], 2)
        self.assertEqual(manager.count(), 2)


if __name__ == '__main__':
    unittest.main()
