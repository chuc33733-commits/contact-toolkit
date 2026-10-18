# -*- coding: utf-8 -*-
"""
工具模块测试用例
"""

import unittest
import sys
import os
import tempfile
from unittest.mock import patch

# 添加项目根目录到Python路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from models.contact import Contact, ContactManager
from utils.merger import ContactMerger
from utils.file_utils import *
from utils.validators import *


class TestContactMerger(unittest.TestCase):
    """联系人合并器测试"""
    
    def setUp(self):
        """测试前准备"""
        self.merger = ContactMerger()
        
        # 创建测试联系人
        self.contact1 = Contact()
        self.contact1.set_name("张三")
        self.contact1.add_phone("13800138000")
        self.contact1.add_email("zhang@example.com")
        
        self.contact2 = Contact()
        self.contact2.set_name("张 三")  # 名字相似
        self.contact2.add_phone("138-0013-8000")  # 电话相同但格式不同
        
        self.contact3 = Contact()
        self.contact3.set_name("李四")
        self.contact3.add_phone("13900139000")
        self.contact3.add_email("zhang@example.com")  # 邮箱相同
        
        self.contact4 = Contact()
        self.contact4.set_name("王五")
        self.contact4.add_phone("13700137000")
        self.contact4.add_email("wang@example.com")
    
    def test_normalize_phone(self):
        """测试电话号码标准化"""
        normalized = self.merger.normalize_phone_for_comparison("138-0013-8000")
        self.assertEqual(normalized, "13800138000")
        
        normalized = self.merger.normalize_phone_for_comparison("+86 138 0013 8000")
        self.assertEqual(normalized, "13800138000")
        
        normalized = self.merger.normalize_phone_for_comparison("(138) 0013-8000")
        self.assertEqual(normalized, "13800138000")
    
    def test_phone_match(self):
        """测试电话号码匹配"""
        self.assertTrue(self.merger.phones_match("13800138000", "138-0013-8000"))
        self.assertTrue(self.merger.phones_match("+86 138 0013 8000", "13800138000"))
        self.assertFalse(self.merger.phones_match("13800138000", "13900139000"))
        self.assertFalse(self.merger.phones_match("", "13800138000"))
    
    def test_name_similarity(self):
        """测试姓名相似度计算"""
        similarity = self.merger.calculate_name_similarity("张三", "张 三")
        self.assertGreater(similarity, 0.8)
        
        similarity = self.merger.calculate_name_similarity("张三", "李四")
        self.assertLess(similarity, 0.5)
        
        similarity = self.merger.calculate_name_similarity("", "张三")
        self.assertEqual(similarity, 0.0)
    
    def test_has_common_phone(self):
        """测试共同电话检查"""
        self.assertTrue(self.merger.contacts_have_common_phone(self.contact1, self.contact2))
        self.assertFalse(self.merger.contacts_have_common_phone(self.contact1, self.contact4))
    
    def test_has_common_email(self):
        """测试共同邮箱检查"""
        self.assertTrue(self.merger.contacts_have_common_email(self.contact1, self.contact3))
        self.assertFalse(self.merger.contacts_have_common_email(self.contact1, self.contact4))
    
    def test_should_merge(self):
        """测试是否应该合并"""
        # 相同电话号码
        should_merge, reason = self.merger.should_merge_contacts(self.contact1, self.contact2)
        self.assertTrue(should_merge)
        self.assertIn("电话", reason)
        
        # 相同邮箱
        should_merge, reason = self.merger.should_merge_contacts(self.contact1, self.contact3)
        self.assertTrue(should_merge)
        
        # 不相关联系人
        should_merge, reason = self.merger.should_merge_contacts(self.contact1, self.contact4)
        self.assertFalse(should_merge)
    
    def test_find_duplicate_groups(self):
        """测试查找重复组"""
        contacts = [self.contact1, self.contact2, self.contact3, self.contact4]
        groups = self.merger.find_duplicate_groups(contacts)
        
        # 应该找到至少一个重复组
        self.assertGreater(len(groups), 0)
        
        # 检查第一个组包含相关联系人
        first_group = groups[0]
        self.assertGreater(len(first_group), 1)
    
    def test_merge_group(self):
        """测试合并组"""
        contacts = [self.contact1, self.contact2]
        merged = self.merger.merge_contact_group(contacts)
        
        self.assertIsInstance(merged, Contact)
        # 验证合并结果（姓名空格已按规则清理，选择去空格后更长的）
        self.assertEqual(merged.name, "张三")
        self.assertIn("13800138000", merged.phones)
    
    def test_merge_duplicates_in_manager(self):
        """测试在管理器中合并重复联系人"""
        manager = ContactManager()
        manager.add_contact(self.contact1)
        manager.add_contact(self.contact2)
        manager.add_contact(self.contact3)
        manager.add_contact(self.contact4)
        
        stats = self.merger.merge_duplicates_in_manager(manager)
        
        self.assertIn('total_contacts_before', stats)
        self.assertIn('total_contacts_after', stats)
        self.assertIn('contacts_merged', stats)
    
    def test_preview_merge_operations(self):
        """测试预览合并操作"""
        contacts = [self.contact1, self.contact2, self.contact3, self.contact4]
        preview = self.merger.preview_merge_operations(contacts)
        
        self.assertIsInstance(preview, list)
        if preview:  # 如果有预览结果
            first_preview = preview[0]
            self.assertIn('original_contacts', first_preview)
            self.assertIn('merged_contact', first_preview)
            self.assertIn('merge_count', first_preview)
            self.assertIn('merge_reasons', first_preview)


class TestFileUtils(unittest.TestCase):
    """文件工具测试"""
    
    def setUp(self):
        """测试前准备"""
        self.temp_file = tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8')
        self.temp_file.write("测试内容\n第二行")
        self.temp_file.close()
    
    def tearDown(self):
        """测试后清理"""
        try:
            os.unlink(self.temp_file.name)
        except:
            pass
    
    def test_detect_file_encoding(self):
        """测试文件编码检测"""
        encoding = detect_file_encoding(self.temp_file.name)
        self.assertIsInstance(encoding, str)
        self.assertIn(encoding.lower(), ['utf-8', 'utf-8-sig', 'gbk', 'gb2312'])
    
    def test_read_text_file(self):
        """测试读取文本文件"""
        content = read_text_file(self.temp_file.name)
        self.assertIsInstance(content, str)
        self.assertIn("测试内容", content)
    
    def test_write_text_file(self):
        """测试写入文本文件"""
        temp_write_file = tempfile.NamedTemporaryFile(delete=False)
        temp_write_file.close()
        
        try:
            test_content = "写入测试内容"
            write_text_file(temp_write_file.name, test_content)
            
            # 验证写入内容
            content = read_text_file(temp_write_file.name)
            self.assertEqual(content, test_content)
        finally:
            try:
                os.unlink(temp_write_file.name)
            except:
                pass
    
    def test_get_file_size(self):
        """测试获取文件大小"""
        size = get_file_size(self.temp_file.name)
        self.assertIsInstance(size, int)
        self.assertGreater(size, 0)
    
    def test_get_file_info(self):
        """测试获取文件信息"""
        info = get_file_info(self.temp_file.name)
        self.assertIsInstance(info, dict)
        self.assertIn('size', info)
        self.assertIn('modified', info)
        self.assertIn('extension', info)
    
    def test_validate_file_path(self):
        """测试文件路径验证"""
        is_valid, _ = validate_file_path(self.temp_file.name)
        self.assertTrue(is_valid)
        
        is_valid, _ = validate_file_path("/invalid/path/file.txt")
        self.assertFalse(is_valid)
        
        is_valid, _ = validate_file_path("")
        self.assertFalse(is_valid)
    
    def test_create_backup_filename(self):
        """测试创建备份文件名"""
        backup_name = create_backup_filename("test.txt")
        self.assertIn("test", backup_name)
        self.assertIn("backup", backup_name)
        self.assertTrue(backup_name.endswith(".txt"))
    
    def test_get_supported_file_extensions(self):
        """测试获取支持的文件扩展名"""
        extensions = get_supported_file_extensions()
        self.assertIn('excel', extensions)
        self.assertIn('vcf', extensions)
        
        excel_exts = extensions['excel']
        self.assertIn('.xlsx', excel_exts)
        self.assertIn('.xls', excel_exts)
        
        vcf_exts = extensions['vcf']
        self.assertIn('.vcf', vcf_exts)
        self.assertIn('.vcard', vcf_exts)
    
    def test_is_supported_file(self):
        """测试文件类型支持检查"""
        # 创建临时Excel文件
        with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as f:
            temp_excel = f.name
        
        try:
            self.assertTrue(is_supported_file(temp_excel, "excel"))
            self.assertFalse(is_supported_file("nonexistent.xlsx", "excel"))
        finally:
            os.unlink(temp_excel)
    
    def test_clean_filename(self):
        """测试文件名清理"""
        clean_name = clean_filename("test<>:\"|?*file.txt")
        self.assertNotIn('<', clean_name)
        self.assertNotIn('>', clean_name)
        self.assertNotIn(':', clean_name)
        self.assertNotIn('"', clean_name)
        self.assertNotIn('|', clean_name)
        self.assertNotIn('?', clean_name)
        self.assertNotIn('*', clean_name)
    
    def test_ensure_unique_filename(self):
        """测试确保文件名唯一"""
        # 创建临时文件
        with tempfile.NamedTemporaryFile(delete=False) as f:
            temp_file = f.name
        
        try:
            # 测试已存在文件的唯一名称生成
            unique_name = ensure_unique_filename(temp_file)
            self.assertNotEqual(unique_name, temp_file)
            self.assertIn("_1", unique_name)
        finally:
            os.unlink(temp_file)
    
    def test_count_file_lines(self):
        """测试统计文件行数"""
        lines = count_lines_in_file(self.temp_file.name)
        self.assertEqual(lines, 2)


class TestValidators(unittest.TestCase):
    """验证器测试"""
    
    def test_validate_email(self):
        """测试邮箱验证"""
        is_valid, msg = validate_email("test@example.com")
        self.assertTrue(is_valid)
        
        is_valid, msg = validate_email("user.name@domain.co.uk")
        self.assertTrue(is_valid)
        
        is_valid, msg = validate_email("user+tag@example.com")
        self.assertTrue(is_valid)
        
        is_valid, msg = validate_email("invalid-email")
        self.assertFalse(is_valid)
        
        is_valid, msg = validate_email("@example.com")
        self.assertFalse(is_valid)
        
        is_valid, msg = validate_email("test@")
        self.assertFalse(is_valid)
        
        is_valid, msg = validate_email("")
        self.assertFalse(is_valid)
    
    def test_validate_phone(self):
        """测试电话验证"""
        is_valid, msg = validate_phone("13800138000")
        self.assertTrue(is_valid)
        
        is_valid, msg = validate_phone("138-0013-8000")
        self.assertTrue(is_valid)
        
        is_valid, msg = validate_phone("+86 138 0013 8000")
        self.assertTrue(is_valid)
        
        is_valid, msg = validate_phone("010-12345678")
        self.assertTrue(is_valid)
        
        is_valid, msg = validate_phone("123")
        self.assertFalse(is_valid)
        
        is_valid, msg = validate_phone("abc")
        self.assertFalse(is_valid)
        
        is_valid, msg = validate_phone("")
        self.assertFalse(is_valid)
    
    def test_validate_name(self):
        """测试姓名验证"""
        is_valid, msg = validate_name("张三")
        self.assertTrue(is_valid)
        
        is_valid, msg = validate_name("John Smith")
        self.assertTrue(is_valid)
        
        is_valid, msg = validate_name("李小明")
        self.assertTrue(is_valid)
        
        is_valid, msg = validate_name("")
        self.assertFalse(is_valid)
        
        is_valid, msg = validate_name("   ")
        self.assertFalse(is_valid)
        
        is_valid, msg = validate_name("a" * 101)  # 太长
        self.assertFalse(is_valid)
    
    def test_validate_organization(self):
        """测试组织验证"""
        is_valid, msg = validate_organization("北京科技有限公司")
        self.assertTrue(is_valid)
        
        is_valid, msg = validate_organization("Google Inc.")
        self.assertTrue(is_valid)
        
        is_valid, msg = validate_organization("")
        self.assertTrue(is_valid)  # 可以为空
        
        is_valid, msg = validate_organization("a" * 201)  # 太长
        self.assertFalse(is_valid)
    
    def test_validate_address(self):
        """测试地址验证"""
        is_valid, msg = validate_address("北京市朝阳区")
        self.assertTrue(is_valid)
        
        is_valid, msg = validate_address("123 Main St, New York")
        self.assertTrue(is_valid)
        
        is_valid, msg = validate_address("")
        self.assertTrue(is_valid)  # 地址可以为空
        
        is_valid, msg = validate_address("a" * 501)  # 过长的地址
        self.assertFalse(is_valid)
    
    def test_validate_birthday(self):
        """测试生日验证"""
        is_valid, msg = validate_birthday("1990-01-01")
        self.assertTrue(is_valid)
        
        is_valid, msg = validate_birthday("01/01/1990")
        self.assertTrue(is_valid)
        
        is_valid, msg = validate_birthday("")
        self.assertTrue(is_valid)  # 生日可以为空
        
        is_valid, msg = validate_birthday("invalid-date")
        self.assertFalse(is_valid)
    
    def test_validate_url(self):
        """测试URL验证"""
        is_valid, msg = validate_url("http://example.com")
        self.assertTrue(is_valid)
        
        is_valid, msg = validate_url("https://www.example.com")
        self.assertTrue(is_valid)
        
        is_valid, msg = validate_url("ftp://ftp.example.com")
        self.assertTrue(is_valid)
        
        is_valid, msg = validate_url("")
        self.assertTrue(is_valid)  # 可以为空
        
        is_valid, msg = validate_url("invalid-url")
        self.assertFalse(is_valid)
        
        is_valid, msg = validate_url("example.com")
        self.assertFalse(is_valid)  # 缺少协议
    
    def test_normalize_phone_number(self):
        """测试电话号码标准化"""
        self.assertEqual(normalize_phone_number("138-0013-8000"), "13800138000")
        self.assertEqual(normalize_phone_number("13800138000"), "13800138000")
        self.assertEqual(normalize_phone_number("(138) 0013-8000"), "13800138000")
        self.assertEqual(normalize_phone_number(""), "")
    
    def test_normalize_email(self):
        """测试邮箱标准化"""
        self.assertEqual(normalize_email("TEST@EXAMPLE.COM"), "test@example.com")
        self.assertEqual(normalize_email("  test@example.com  "), "test@example.com")
    
    def test_normalize_name(self):
        """测试姓名标准化"""
        self.assertEqual(normalize_name("  张三  "), "张三")
        self.assertEqual(normalize_name("张  三"), "张 三")
    
    def test_validate_contact_data(self):
        """测试联系人数据验证"""
        valid_data = {
            'name': '张三',
            'phones': ['13800138000'],
            'emails': ['test@example.com'],
            'organization': '公司A',
            'birthday': '1990-01-01'
        }
        
        is_valid, errors = validate_contact_data(valid_data)
        self.assertTrue(is_valid)
        self.assertEqual(len(errors), 0)
        
        invalid_data = {
            'name': '',  # 空姓名
            'phones': ['invalid-phone'],  # 无效电话
            'emails': ['invalid-email'],  # 无效邮箱
            'birthday': 'invalid-date'  # 无效日期
        }
        
        is_valid, errors = validate_contact_data(invalid_data)
        self.assertFalse(is_valid)
        self.assertGreater(len(errors), 0)
    
    def test_sanitize_input(self):
        """测试输入清理"""
        self.assertEqual(sanitize_input("  test  "), "test")
        self.assertEqual(sanitize_input("test\n\r\t"), "test")
        self.assertEqual(sanitize_input(""), "")
    
    def test_is_empty_contact(self):
        """测试空联系人检查"""
        empty_data = {'name': '', 'phones': [], 'emails': []}
        self.assertTrue(is_empty_contact(empty_data))
        
        non_empty_data = {'name': '张三', 'phones': [], 'emails': []}
        self.assertFalse(is_empty_contact(non_empty_data))
    
    def test_validate_file_format(self):
        """测试文件格式验证"""
        # 创建临时Excel文件
        with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as f:
            temp_excel = f.name
        
        try:
            is_valid, msg = validate_file_format(temp_excel, "excel")
            self.assertTrue(is_valid)
            
            is_valid, msg = validate_file_format("test.txt", "excel")
            self.assertFalse(is_valid)
            self.assertEqual(msg, "文件不存在")
        finally:
            os.unlink(temp_excel)


if __name__ == '__main__':
    unittest.main()