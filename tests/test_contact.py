# -*- coding: utf-8 -*-
"""
联系人模型测试用例
"""

import unittest
import sys
import os
from datetime import datetime

# 添加项目根目录到Python路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from models.contact import Contact, ContactManager


class TestContact(unittest.TestCase):
    """联系人类测试"""
    
    def setUp(self):
        """测试前准备"""
        self.contact = Contact()
        self.contact.set_name("张三")
        self.contact.add_phone("13800138000")
        self.contact.add_email("zhangsan@example.com")
    
    def test_init(self):
        """测试联系人初始化"""
        contact = Contact()
        self.assertEqual(contact.name, "")
        self.assertEqual(contact.phones, [])
        self.assertEqual(contact.emails, [])
        self.assertEqual(contact.organization, "")
        self.assertEqual(contact.title, "")
        self.assertEqual(contact.address, "")
        self.assertEqual(contact.birthday, "")
        self.assertEqual(contact.notes, "")
        self.assertEqual(contact.categories, [])
        self.assertIsInstance(contact.created_at, datetime)
        self.assertIsInstance(contact.updated_at, datetime)
    
    def test_add_phone(self):
        """测试添加电话"""
        self.contact.add_phone("13800138000")
        self.assertIn("13800138000", self.contact.phones)
        
        # 测试重复添加
        self.contact.add_phone("13800138000")
        self.assertEqual(self.contact.phones.count("13800138000"), 1)
    
    def test_add_email(self):
        """测试添加邮箱"""
        self.contact.add_email("test@example.com")
        self.assertIn("test@example.com", self.contact.emails)
        
        # 测试重复添加
        self.contact.add_email("test@example.com")
        self.assertEqual(self.contact.emails.count("test@example.com"), 1)
    
    def test_add_category(self):
        """测试添加分类"""
        self.contact.add_category("朋友")
        self.assertIn("朋友", self.contact.categories)
        
        # 测试重复添加
        self.contact.add_category("朋友")
        self.assertEqual(self.contact.categories.count("朋友"), 1)
    
    def test_normalize_phone(self):
        """测试电话号码标准化"""
        # 测试中国手机号码
        normalized = self.contact.normalize_phone("138-0013-8000")
        self.assertEqual(normalized, "13800138000")
        
        normalized = self.contact.normalize_phone("+86 138 0013 8000")
        self.assertEqual(normalized, "13800138000")
        
        # 测试固定电话
        normalized = self.contact.normalize_phone("010-12345678")
        self.assertEqual(normalized, "1012345678")
    
    def test_validate_email(self):
        """测试邮箱验证"""
        self.assertTrue(Contact.is_valid_email("test@example.com"))
        self.assertTrue(Contact.is_valid_email("user.name@domain.co.uk"))
        self.assertFalse(Contact.is_valid_email("invalid-email"))
        self.assertFalse(Contact.is_valid_email("@example.com"))
        self.assertFalse(Contact.is_valid_email("test@"))
    
    def test_is_empty(self):
        """测试空联系人检查"""
        empty_contact = Contact()
        self.assertTrue(empty_contact.is_empty())
        
        self.contact.add_phone("13800138000")
        self.assertFalse(self.contact.is_empty())
    
    def test_has_phone(self):
        """测试电话号码检查"""
        self.contact.add_phone("13800138000")
        self.assertTrue(self.contact.has_phone("13800138000"))
        self.assertTrue(self.contact.has_phone("138-0013-8000"))  # 标准化后匹配
        self.assertFalse(self.contact.has_phone("13900139000"))
    
    def test_has_email(self):
        """测试邮箱检查"""
        self.contact.add_email("test@example.com")
        self.assertTrue(self.contact.has_email("test@example.com"))
        self.assertTrue(self.contact.has_email("TEST@EXAMPLE.COM"))  # 大小写不敏感
        self.assertFalse(self.contact.has_email("other@example.com"))
    
    def test_get_primary_phone(self):
        """测试获取主要电话号码"""
        self.assertEqual(self.contact.get_primary_phone(), "13800138000")
        
        self.contact.add_phone("13800138000")
        self.contact.add_phone("13900139000")
        self.assertEqual(self.contact.get_primary_phone(), "13800138000")
    
    def test_get_primary_email(self):
        """测试获取主要邮箱地址"""
        self.assertEqual(self.contact.get_primary_email(), "zhangsan@example.com")
        
        self.contact.add_email("test1@example.com")
        self.contact.add_email("test2@example.com")
        self.assertEqual(self.contact.get_primary_email(), "zhangsan@example.com")
    
    def test_merge_with(self):
        """测试合并联系人"""
        other = Contact()
        other.set_name("李四")
        other.add_phone("13900139000")
        other.add_email("lisi@example.com")
        
        merged = self.contact.merge_with(other)
        
        self.assertEqual(merged.name, "张三")  # 选择更长的名字
        self.assertIn("13800138000", merged.phones)
        self.assertIn("13900139000", merged.phones)
        self.assertIn("zhangsan@example.com", merged.emails)
        self.assertIn("lisi@example.com", merged.emails)
    
    def test_to_dict(self):
        """测试转换为字典"""
        self.contact.add_email("test@example.com")
        self.contact.organization = "测试公司"
        
        data = self.contact.to_dict()
        
        self.assertEqual(data['name'], "张三")
        self.assertEqual(data['phones'], ["13800138000"])
        self.assertEqual(data['emails'], ["zhangsan@example.com", "test@example.com"])
        self.assertEqual(data['organization'], "测试公司")
    
    def test_from_dict(self):
        """测试从字典创建联系人"""
        data = {
            'name': '李四',
            'phones': ['13900139000', '02088888888'],
            'emails': ['lisi@example.com'],
            'organization': '公司B',
            'title': '经理',
            'address': '北京市朝阳区',
            'birthday': '1990-01-01',
            'notes': '重要客户',
            'categories': ['工作', '重要']
        }
        
        contact = Contact.from_dict(data)
        
        self.assertEqual(contact.name, '李四')
        self.assertEqual(contact.phones, ['13900139000', '02088888888'])
        self.assertEqual(contact.emails, ['lisi@example.com'])
        self.assertEqual(contact.organization, '公司B')
        self.assertEqual(contact.title, '经理')
        self.assertEqual(contact.address, '北京市朝阳区')
        self.assertEqual(contact.birthday, '1990-01-01')
        self.assertEqual(contact.notes, '重要客户')
        self.assertEqual(contact.categories, ['工作', '重要'])
    
    def test_equality(self):
        """测试相等性比较"""
        other = Contact()
        other.set_name("张三")
        other.add_phone("13800138000")
        other.add_email("zhangsan@example.com")
        
        self.assertEqual(self.contact, other)
        
        # 修改一个属性后应该不相等
        other.set_name("李四")
        self.assertNotEqual(self.contact, other)
    
    def test_hash(self):
        """测试哈希值"""
        other = Contact()
        other.set_name("张三")
        other.add_phone("13800138000")
        other.add_email("zhangsan@example.com")
        self.assertEqual(hash(self.contact), hash(other))
        
        # 测试可以作为字典键
        contact_dict = {self.contact: "value"}
        self.assertEqual(contact_dict[other], "value")


class TestContactManager(unittest.TestCase):
    """联系人管理器测试"""
    
    def setUp(self):
        """测试前准备"""
        self.manager = ContactManager()
        
        # 创建测试联系人
        self.contact1 = Contact()
        self.contact1.set_name("张三")
        self.contact1.add_phone("13800138000")
        self.contact1.add_email("zhang@example.com")
        
        self.contact2 = Contact()
        self.contact2.set_name("李四")
        self.contact2.add_phone("13900139000")
        self.contact2.add_email("li@example.com")
        
        self.contact3 = Contact()
        self.contact3.set_name("王五")
        self.contact3.add_phone("13800138000")  # 与contact1相同电话
    
    def test_add_contact(self):
        """测试添加联系人"""
        contact = Contact()
        contact.set_name("李四")
        self.manager.add_contact(contact)
        
        self.assertEqual(self.manager.count(), 1)
        self.assertIn(contact, self.manager.get_all_contacts())
    
    def test_remove_contact(self):
        """测试移除联系人"""
        contact = Contact()
        contact.set_name("李四")
        self.manager.add_contact(contact)
        
        result = self.manager.remove_contact(contact)
        self.assertTrue(result)
        self.assertEqual(self.manager.count(), 0)
        
        # 测试移除不存在的联系人
        result = self.manager.remove_contact(contact)
        self.assertFalse(result)
    
    def test_find_by_phone(self):
        """测试按电话查找联系人"""
        contact1 = Contact()
        contact1.set_name("张三")
        contact1.add_phone("13800138000")
        contact2 = Contact()
        contact2.set_name("李四")
        contact2.add_phone("13900139000")
        
        self.manager.add_contact(contact1)
        self.manager.add_contact(contact2)
        
        results = self.manager.find_by_phone("13800138000")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].name, "张三")
    
    def test_find_by_email(self):
        """测试按邮箱查找联系人"""
        contact1 = Contact()
        contact1.set_name("张三")
        contact1.add_email("zhangsan@example.com")
        contact2 = Contact()
        contact2.set_name("李四")
        contact2.add_email("lisi@example.com")
        
        self.manager.add_contact(contact1)
        self.manager.add_contact(contact2)
        
        results = self.manager.find_by_email("zhangsan@example.com")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].name, "张三")
    
    def test_find_by_name(self):
        """测试按姓名查找联系人"""
        contact1 = Contact()
        contact1.set_name("张三")
        contact2 = Contact()
        contact2.set_name("李四")
        
        self.manager.add_contact(contact1)
        self.manager.add_contact(contact2)
        
        results = self.manager.find_by_name("张三")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].name, "张三")
    
    def test_get_all_contacts(self):
        """测试获取所有联系人"""
        contact1 = Contact()
        contact1.set_name("张三")
        contact2 = Contact()
        contact2.set_name("李四")
        
        self.manager.add_contact(contact1)
        self.manager.add_contact(contact2)
        
        all_contacts = self.manager.get_all_contacts()
        self.assertEqual(len(all_contacts), 2)
        self.assertIn(contact1, all_contacts)
        self.assertIn(contact2, all_contacts)
    
    def test_clear(self):
        """测试清空联系人"""
        contact = Contact()
        contact.set_name("张三")
        self.manager.add_contact(contact)
        
        self.manager.clear()
        self.assertEqual(self.manager.count(), 0)
        self.assertEqual(len(self.manager.get_all_contacts()), 0)
    
    def test_find_duplicates(self):
        """测试查找重复联系人"""
        contact1 = Contact()
        contact1.set_name("张三")
        contact1.add_phone("13800138000")
        
        contact2 = Contact()
        contact2.set_name("李四")
        contact2.add_phone("13800138000")  # 相同电话
        
        self.manager.add_contact(contact1)
        self.manager.add_contact(contact2)
        
        duplicates = self.manager.find_duplicates_by_phone()
        self.assertIn("13800138000", duplicates)
        self.assertEqual(len(duplicates["13800138000"]), 2)
    
    def test_merge_duplicates_by_phone(self):
        """测试按电话合并重复联系人"""
        contact1 = Contact()
        contact1.set_name("张三")
        contact1.add_phone("13800138000")
        contact1.add_email("zhangsan@example.com")
        
        contact2 = Contact()
        contact2.set_name("张三")
        contact2.add_phone("13800138000")  # 相同电话
        contact2.add_email("zhangsan2@example.com")
        
        self.manager.add_contact(contact1)
        self.manager.add_contact(contact2)
        
        merged_count = self.manager.merge_duplicates_by_phone()
        self.assertEqual(merged_count, 1)
        self.assertEqual(self.manager.count(), 1)
        
        # 检查合并后的联系人包含两个邮箱
        remaining_contact = self.manager.get_all_contacts()[0]
        self.assertEqual(len(remaining_contact.emails), 2)


if __name__ == '__main__':
    unittest.main()