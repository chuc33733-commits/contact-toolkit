# -*- coding: utf-8 -*-
"""
合并规则专项测试

规则一：优先将信息较少的联系人合并到信息较多的联系人，且不得遗漏任何一方已有的任何信息
规则二：姓名相似（如"张三（老师）"与"张三"）且 11 位电话号码相同时，
        合并后姓名取简洁名称，括号内备注内容移入备注字段
"""

import os
import sys
import unittest

# 添加项目根目录到Python路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from models.contact import Contact, ContactManager
from utils.merger import ContactMerger


def make_contact(name="", phones=None, emails=None, **fields) -> Contact:
    """
    构造测试联系人。

    Args:
        name: 姓名
        phones: 电话列表
        emails: 邮箱列表
        **fields: 其它字段（organization/title/address/birthday/lunar_birthday/
                  website/notes/categories/nicknames/photos）

    Returns:
        Contact 对象
    """
    contact = Contact()
    if name:
        contact.set_name(name)
    for phone in (phones or []):
        contact.add_phone(phone)
    for email in (emails or []):
        contact.add_email(email)
    for nickname in fields.pop('nicknames', []) or []:
        contact.add_nickname(nickname)
    for category in fields.pop('categories', []) or []:
        contact.add_category(category)
    for photo in fields.pop('photos', []) or []:
        contact.add_photo(photo)

    setters = {
        'organization': contact.set_organization,
        'title': contact.set_title,
        'address': contact.set_address,
        'birthday': contact.set_birthday,
        'lunar_birthday': contact.set_lunar_birthday,
        'website': contact.set_website,
        'notes': contact.set_notes,
    }
    for key, value in fields.items():
        if key in setters and value:
            setters[key](value)
    return contact


class TestRuleOneRicherIsSubject(unittest.TestCase):
    """规则一：信息少者并入信息多者"""

    def test_information_score_ranks_richer_higher(self):
        """信息量评分应能区分信息多寡"""
        rich = make_contact("张三丰", ["13800000000", "01088886666"],
                            ["a@example.com", "b@example.com"],
                            organization="阿里巴巴", title="架构师",
                            address="杭州市西湖区", birthday="1990-01-05",
                            categories=["同事"])
        poor = make_contact("张三丰", ["13800000000"])
        self.assertGreater(rich.information_score(), poor.information_score())

    def test_richer_contact_wins_single_value_fields(self):
        """单值字段应保留信息更完整的一方"""
        rich = make_contact("李四", ["13900000000"], organization="腾讯科技",
                            title="高级产品经理", address="深圳市南山区科技园")
        poor = make_contact("李四", ["13900000000"], title="产品")

        merged = Contact.merge_group([poor, rich])

        self.assertEqual(merged.organization, "腾讯科技")
        self.assertEqual(merged.title, "高级产品经理")
        self.assertEqual(merged.address, "深圳市南山区科技园")

    def test_list_fields_are_unioned(self):
        """电话/邮箱/昵称/分类/照片等列表字段必须取并集"""
        a = make_contact("王五", ["13800000001"], ["a@example.com"],
                         nicknames=["小五"], categories=["同事"],
                         photos=["https://example.com/a.jpg"])
        b = make_contact("王五", ["13800000002"], ["b@example.com"],
                         nicknames=["阿五"], categories=["客户"],
                         photos=["https://example.com/b.jpg"])

        merged = Contact.merge_group([a, b])

        self.assertEqual(set(merged.phones), {"13800000001", "13800000002"})
        self.assertEqual(set(merged.emails), {"a@example.com", "b@example.com"})
        self.assertEqual(set(merged.nicknames), {"小五", "阿五"})
        self.assertEqual(set(merged.categories), {"同事", "客户"})
        self.assertEqual(set(merged.photos),
                         {"https://example.com/a.jpg", "https://example.com/b.jpg"})

    def test_conflicting_single_value_recorded_in_notes(self):
        """单值字段冲突时另一方取值写入备注，保证信息不丢失"""
        richer = make_contact("赵六", ["13700000000"], ["z@example.com"],
                              address="杭州市西湖区文三路", organization="字节跳动")
        poorer = make_contact("赵六", ["13700000000"], address="北京市朝阳区")

        merged = Contact.merge_group([poorer, richer])

        self.assertEqual(merged.address, "杭州市西湖区文三路")
        self.assertIn("地址:北京市朝阳区", merged.notes)

    def test_existing_notes_from_both_kept(self):
        """双方的原有备注都要保留"""
        a = make_contact("孙七", ["13600000000"], notes="老同学")
        b = make_contact("孙七", ["13600000000"], notes="重要客户")

        merged = Contact.merge_group([a, b])

        self.assertIn("老同学", merged.notes)
        self.assertIn("重要客户", merged.notes)

    def test_no_information_lost_in_three_way_merge(self):
        """三方合并后，任何一方原有的字段值都还能找到"""
        contacts = [
            make_contact("周八", ["13800000001"], ["a@example.com"],
                         address="北京市海淀区", notes="备注A"),
            make_contact("周八", ["13800000002"], ["b@example.com"],
                         lunar_birthday="1988-08-08", notes="备注B"),
            make_contact("周八", ["13800000003"], ["c@example.com"],
                         website="https://zhouba.example.com"),
        ]

        merged = Contact.merge_group(contacts)
        haystack = " | ".join([
            merged.name, merged.notes, merged.address, merged.lunar_birthday,
            merged.website, merged.organization,
            *merged.phones, *merged.emails, *merged.nicknames, *merged.categories,
        ])

        for token in ("13800000001", "13800000002", "13800000003",
                      "a@example.com", "b@example.com", "c@example.com",
                      "北京市海淀区", "1988-08-08",
                      "https://zhouba.example.com", "备注A", "备注B"):
            self.assertIn(token, haystack, f"合并后丢失了信息: {token}")

    def test_merge_group_single_contact_returns_itself(self):
        """只有一个联系人时不做任何改动"""
        contact = make_contact("钱九", ["13500000000"])
        self.assertIs(Contact.merge_group([contact]), contact)

    def test_merge_group_empty_returns_empty_contact(self):
        """空组返回空联系人"""
        merged = Contact.merge_group([])
        self.assertIsInstance(merged, Contact)
        self.assertTrue(merged.is_empty())


class TestRuleTwoSimpleNameAndAnnotation(unittest.TestCase):
    """规则二：相似姓名取简洁名，括号内容移入备注"""

    def setUp(self):
        self.merger = ContactMerger()

    def test_split_name_annotations(self):
        """姓名拆分：基础名 + 括号注解"""
        self.assertEqual(Contact.split_name_annotations("张三（老师）"), ("张三", ["老师"]))
        self.assertEqual(Contact.split_name_annotations("张三(老师)"), ("张三", ["老师"]))
        self.assertEqual(Contact.split_name_annotations("张三【老师】"), ("张三", ["老师"]))
        self.assertEqual(Contact.split_name_annotations("张三"), ("张三", []))
        # 整体被括号占据时不拆分，保留原名
        self.assertEqual(Contact.split_name_annotations("（未知）"), ("（未知）", []))

    def test_same_mobile_merge_reason(self):
        """相似姓名 + 11 位电话号码相同 → 合并原因应明确"""
        a = make_contact("张三（老师）", ["13812345678"])
        b = make_contact("张三", ["13812345678"])

        should_merge, reason = self.merger.should_merge_contacts(a, b)

        self.assertTrue(should_merge)
        self.assertIn("11", reason)
        self.assertIn("电话", reason)

    def test_bracket_annotation_moved_to_notes(self):
        """合并后姓名取简洁名称，括号内容移入备注"""
        a = make_contact("张三（老师）", ["13812345678"])
        b = make_contact("张三", ["138-1234-5678"])

        merged = Contact.merge_group([a, b])

        self.assertEqual(merged.name, "张三")
        self.assertEqual(merged.notes, "老师")
        self.assertEqual(merged.phones, ["13812345678"])

    def test_bracket_annotation_with_fullwidth_and_halfwidth(self):
        """全角/半角括号都应被识别"""
        merged = Contact.merge_group([
            make_contact("张三(老师)", ["13812345678"]),
            make_contact("张三", ["13812345678"]),
        ])
        self.assertEqual(merged.name, "张三")
        self.assertIn("老师", merged.notes)

    def test_both_bracket_forms_merge_into_plain_name(self):
        """两种括注形式都并入同一人时，姓名取"张三"，两个括注都进备注"""
        merged = Contact.merge_group([
            make_contact("张三（同事）", ["13800000000"]),
            make_contact("张三（客户）", ["13800000000"]),
        ])
        self.assertEqual(merged.name, "张三")
        self.assertIn("同事", merged.notes)
        self.assertIn("客户", merged.notes)

    def test_group_merged_through_merger_end_to_end(self):
        """通过 ContactMerger 全流程合并，结果同样遵循规则二"""
        manager = ContactManager()
        manager.add_contact(make_contact("张三（老师）", ["13812345678"]))
        manager.add_contact(make_contact("张三", ["13812345678"]))
        manager.add_contact(make_contact("不相关的人", ["13900001111"]))

        stats = self.merger.merge_duplicates_in_manager(manager)

        self.assertEqual(stats['contacts_merged'], 1)
        self.assertEqual(manager.count(), 2)
        merged = [c for c in manager.get_all_contacts() if c.name == "张三"]
        self.assertEqual(len(merged), 1, "未生成简洁姓名'张三'")
        self.assertEqual(merged[0].notes, "老师")

    def test_different_names_not_lost(self):
        """姓名基础部分不同时，另一方姓名作为别名保留"""
        merged = Contact.merge_group([
            make_contact("昵称A", ["13900000000"]),
            make_contact("本名B", ["13900000000"], emails=["b@example.com"]),
        ])
        self.assertEqual(merged.name, "本名B")
        self.assertIn("昵称A", merged.nicknames)

    def test_similar_name_different_phone_not_merged(self):
        """仅姓名相似但电话不同、无共同邮箱/公司时不合并"""
        groups = self.merger.find_duplicate_groups([
            make_contact("张三（老师）", ["13812345678"]),
            make_contact("张三", ["13987654321"]),
        ])
        self.assertEqual(groups, [])

    def test_plain_same_name_keeps_name_without_annotation(self):
        """同名同人合并：姓名保持不变，不产生多余备注"""
        merged = Contact.merge_group([
            make_contact("张三", ["13800138000"]),
            make_contact("张三", ["13900139000"]),
        ])
        self.assertEqual(merged.name, "张三")
        self.assertEqual(merged.notes, "")

    def test_manager_merge_duplicates_by_phone_uses_same_rules(self):
        """ContactManager.merge_duplicates_by_phone 同样遵循规则二"""
        manager = ContactManager()
        manager.add_contact(make_contact("张三（老师）", ["13812345678"]))
        manager.add_contact(make_contact("张三", ["13812345678"]))

        merged_count = manager.merge_duplicates_by_phone()

        self.assertEqual(merged_count, 1)
        self.assertEqual(manager.count(), 1)
        remaining = manager.get_all_contacts()[0]
        self.assertEqual(remaining.name, "张三")
        self.assertEqual(remaining.notes, "老师")

    def test_manager_merge_handles_contact_in_multiple_phone_groups(self):
        """同一联系人落入多个号码组时只合并一次，不重复计数"""
        shared = make_contact("甲", ["13800000001", "13800000002"])
        manager = ContactManager()
        manager.add_contact(shared)
        manager.add_contact(make_contact("甲", ["13800000001"]))
        manager.add_contact(make_contact("甲", ["13800000002"]))

        merged_count = manager.merge_duplicates_by_phone()

        self.assertEqual(manager.count(), 1)
        self.assertEqual(merged_count, 2)
        self.assertEqual(set(manager.get_all_contacts()[0].phones),
                         {"13800000001", "13800000002"})


if __name__ == '__main__':
    unittest.main(verbosity=2)
