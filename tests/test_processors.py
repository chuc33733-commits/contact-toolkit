# -*- coding: utf-8 -*-
"""
处理器模块测试用例
"""

import unittest
import sys
import os
import tempfile
import pandas as pd
from io import StringIO

# 添加项目根目录到Python路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from models.contact import Contact, ContactManager
from processors.excel_processor import ExcelProcessor
from processors.vcf_processor import VCFProcessor


class TestExcelProcessor(unittest.TestCase):
    """Excel处理器测试"""
    
    def setUp(self):
        """测试前准备"""
        self.processor = ExcelProcessor()
        
        # 创建测试数据
        self.test_data = {
            '姓名': ['张三', '李四', '王五'],
            '电话': ['13800138000', '13900139000', '13700137000'],
            '邮箱': ['zhang@example.com', 'li@example.com', 'wang@example.com'],
            '公司': ['公司A', '公司B', '公司C'],
            '职位': ['经理', '主管', '员工']
        }
        
        # 创建临时Excel文件
        self.temp_file = tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False)
        self.temp_file.close()
        
        df = pd.DataFrame(self.test_data)
        df.to_excel(self.temp_file.name, index=False)
    
    def tearDown(self):
        """测试后清理"""
        try:
            os.unlink(self.temp_file.name)
        except:
            pass
    
    def test_read_excel(self):
        """测试读取Excel文件"""
        df, error_msg = self.processor.read_excel_file(self.temp_file.name)
        
        self.assertEqual(error_msg, "")
        self.assertIsInstance(df, pd.DataFrame)
        self.assertEqual(len(df), 3)
        self.assertIn('姓名', df.columns)
        self.assertIn('电话', df.columns)
    
    def test_get_sheet_names(self):
        """测试获取工作表名称"""
        sheet_names, error_msg = self.processor.get_sheet_names(self.temp_file.name)
        
        self.assertEqual(error_msg, "")
        self.assertIsInstance(sheet_names, list)
        self.assertGreater(len(sheet_names), 0)
    
    def test_preview_data(self):
        """测试预览Excel数据"""
        df, mapping, error_msg = self.processor.preview_data(self.temp_file.name, max_rows=2)
        
        self.assertEqual(error_msg, "")
        self.assertIsInstance(df, pd.DataFrame)
        self.assertIsInstance(mapping, dict)
        self.assertLessEqual(len(df), 2)
    
    def test_detect_column_mapping(self):
        """测试列映射检测"""
        df = pd.DataFrame(self.test_data)
        mapping = self.processor.detect_column_mapping(df)
        
        self.assertIsInstance(mapping, dict)
        self.assertIn('name', mapping)
        self.assertIn('phone', mapping)
        self.assertIn('email', mapping)
    
    def test_row_to_contact(self):
        """测试Excel行转换为联系人"""
        # 测试行转换为联系人
        row_data = pd.Series({
            '姓名': '张三',
            '电话': '13800138000',
            '邮箱': 'zhangsan@example.com',
            '公司': '公司A'
        })
        
        column_mapping = {
            'name': '姓名',
            'phone': '电话',
            'email': '邮箱',
            'company': '公司'
        }
        
        contact = self.processor.convert_row_to_contact(row_data, column_mapping)
        
        self.assertEqual(contact.name, '张三')
        self.assertIn('13800138000', contact.phones)
        self.assertIn('zhangsan@example.com', contact.emails)
        self.assertEqual(contact.organization, '公司A')
    
    def test_import_from_excel(self):
        """测试从Excel导入联系人"""
        manager = ContactManager()
        
        contact_manager, stats, error_msg = self.processor.import_from_excel(self.temp_file.name)
        
        self.assertEqual(error_msg, "")
        self.assertIsNotNone(contact_manager)
        self.assertGreater(stats['valid_contacts'], 0)
        self.assertEqual(contact_manager.count(), 3)
        
        # 检查导入的联系人
        contacts = contact_manager.get_all_contacts()
        names = [contact.name for contact in contacts]
        self.assertIn('张三', names)
        self.assertIn('李四', names)
        self.assertIn('王五', names)
    
    def test_export_to_excel(self):
        """测试导出到Excel文件"""
        # 创建联系人管理器和联系人
        manager = ContactManager()
        
        contact1 = Contact()
        contact1.set_name("张三")
        contact1.add_phone("13800138000")
        contact1.add_email("zhangsan@example.com")
        
        contact2 = Contact()
        contact2.set_name("李四")
        contact2.add_phone("13900139000")
        contact2.add_email("lisi@example.com")
        
        manager.add_contact(contact1)
        manager.add_contact(contact2)
        
        # 导出到Excel文件
        error_msg = self.processor.export_to_excel(manager, self.temp_file.name)
        
        self.assertEqual(error_msg, "")
        
        # 验证文件是否创建
        self.assertTrue(os.path.exists(self.temp_file.name))


class TestVCFProcessor(unittest.TestCase):
    """VCF处理器测试"""
    
    def setUp(self):
        """测试前准备"""
        self.processor = VCFProcessor()
        
        # 创建测试VCF内容
        self.test_vcf_content = """BEGIN:VCARD
VERSION:3.0
FN:张三
TEL:13800138000
EMAIL:zhang@example.com
ORG:公司A
TITLE:经理
END:VCARD

BEGIN:VCARD
VERSION:3.0
FN:李四
TEL:13900139000
EMAIL:li@example.com
ORG:公司B
TITLE:主管
END:VCARD"""
        
        # 创建临时VCF文件
        self.temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.vcf', delete=False, encoding='utf-8')
        self.temp_file.write(self.test_vcf_content)
        self.temp_file.close()
    
    def tearDown(self):
        """测试后清理"""
        try:
            os.unlink(self.temp_file.name)
        except:
            pass
    
    def test_detect_encoding(self):
        """测试编码检测"""
        encoding = self.processor.detect_encoding(self.temp_file.name)
        self.assertIsInstance(encoding, str)
        self.assertIn(encoding.lower(), ['utf-8', 'utf-8-sig', 'gbk', 'gb2312'])
    
    def test_read_vcf_file(self):
        """测试读取VCF文件"""
        content, error_msg = self.processor.read_vcf_file(self.temp_file.name)
        
        self.assertEqual(error_msg, "")
        self.assertIsInstance(content, str)
        self.assertIn('BEGIN:VCARD', content)
        self.assertIn('张三', content)
    
    def test_parse_vcard(self):
        """测试解析单个vCard"""
        vcard_text = """BEGIN:VCARD
VERSION:3.0
FN:张三
TEL:13800138000
EMAIL:zhang@example.com
ORG:公司A
TITLE:经理
END:VCARD"""
        
        contact = self.processor.parse_vcard(vcard_text)
        
        self.assertIsInstance(contact, Contact)
        self.assertEqual(contact.name, '张三')
        self.assertIn('13800138000', contact.phones)
        self.assertIn('zhang@example.com', contact.emails)
        self.assertEqual(contact.organization, '公司A')
        self.assertEqual(contact.title, '经理')
    
    def test_parse_vcf_content(self):
        """测试解析VCF内容"""
        contacts = self.processor.parse_vcf_content(self.test_vcf_content)
        
        self.assertIsInstance(contacts, list)
        self.assertEqual(len(contacts), 2)
        
        names = [contact.name for contact in contacts]
        self.assertIn('张三', names)
        self.assertIn('李四', names)
    
    def test_import_from_vcf(self):
        """测试从VCF导入联系人"""
        manager = ContactManager()
        
        contact_manager, stats, error_msg = self.processor.import_from_vcf(
            self.temp_file.name
        )
        
        self.assertEqual(error_msg, "")
        self.assertIsNotNone(contact_manager)
        self.assertEqual(stats['valid_contacts'], 2)
        self.assertEqual(contact_manager.count(), 2)
        
        # 检查导入的联系人
        contacts = contact_manager.get_all_contacts()
        names = [contact.name for contact in contacts]
        self.assertIn('张三', names)
        self.assertIn('李四', names)
    
    def test_contact_to_vcard(self):
        """测试联系人转换为VCard格式"""
        contact = Contact()
        contact.set_name("张三")
        contact.add_phone("13800138000")
        contact.add_email("zhangsan@example.com")
        contact.organization = "测试公司"
        
        vcard = self.processor.contact_to_vcard(contact)
        
        self.assertIn("FN:张三", vcard)
        self.assertIn("TEL;TYPE=CELL:13800138000", vcard)
        self.assertIn("EMAIL:zhangsan@example.com", vcard)
        self.assertIn("ORG:测试公司", vcard)
    
    def test_export_to_vcf(self):
        """测试导出到VCF文件"""
        # 创建联系人管理器和联系人
        manager = ContactManager()
        
        contact1 = Contact()
        contact1.set_name("张三")
        contact1.add_phone("13800138000")
        contact1.add_email("zhangsan@example.com")
        
        contact2 = Contact()
        contact2.set_name("李四")
        contact2.add_phone("13900139000")
        contact2.add_email("lisi@example.com")
        
        manager.add_contact(contact1)
        manager.add_contact(contact2)
        
        # 导出到VCF文件
        error_msg = self.processor.export_to_vcf(manager, self.temp_file.name)
        
        self.assertEqual(error_msg, "")
        
        # 验证文件内容
        with open(self.temp_file.name, 'r', encoding='utf-8') as f:
            content = f.read()
            self.assertIn("FN:张三", content)
            self.assertIn("FN:李四", content)
    
    def test_merge_vcf_files(self):
        """测试合并VCF文件"""
        # 创建第二个VCF文件
        second_vcf_content = """BEGIN:VCARD
VERSION:3.0
FN:王五
TEL:13700137000
EMAIL:wang@example.com
ORG:公司C
END:VCARD"""
        
        temp_file2 = tempfile.NamedTemporaryFile(mode='w', suffix='.vcf', delete=False, encoding='utf-8')
        temp_file2.write(second_vcf_content)
        temp_file2.close()
        
        # 合并文件
        temp_merged_file = tempfile.NamedTemporaryFile(mode='w', suffix='.vcf', delete=False, encoding='utf-8')
        temp_merged_file.close()
        
        try:
            stats, error_msg = self.processor.merge_vcf_files(
                [self.temp_file.name, temp_file2.name],
                temp_merged_file.name
            )
            
            self.assertEqual(error_msg, "")
            self.assertEqual(stats['input_files'], 2)
            self.assertGreater(stats['total_contacts_imported'], 0)
            
            # 验证合并后的文件
            with open(temp_merged_file.name, 'r', encoding='utf-8') as f:
                content = f.read()
            
            self.assertIn('张三', content)
            self.assertIn('李四', content)
            self.assertIn('王五', content)
            
        finally:
            try:
                os.unlink(temp_file2.name)
                os.unlink(temp_merged_file.name)
            except:
                pass
    
    def test_validate_vcf_file(self):
        """测试VCF文件验证"""
        is_valid, errors, error_msg = self.processor.validate_vcf_file(self.temp_file.name)
        self.assertEqual(error_msg, "")
        self.assertTrue(is_valid)
        self.assertIsInstance(errors, list)
        
        # 测试无效文件
        invalid_file = tempfile.NamedTemporaryFile(mode='w', suffix='.vcf', delete=False, encoding='utf-8')
        invalid_file.write("这不是一个有效的VCF文件")
        invalid_file.close()
        
        try:
            is_valid, errors, error_msg = self.processor.validate_vcf_file(invalid_file.name)
            self.assertFalse(is_valid)
            self.assertIsNotNone(error_msg)
        finally:
            try:
                os.unlink(invalid_file.name)
            except:
                pass
    
    def test_get_vcf_stats(self):
        """测试获取VCF统计信息"""
        stats, error_msg = self.processor.get_vcf_statistics(self.temp_file.name)
        
        self.assertEqual(error_msg, "")
        self.assertIsInstance(stats, dict)
        self.assertIn('total_contacts', stats)
        self.assertIn('contacts_with_phone', stats)
        self.assertIn('contacts_with_email', stats)
        self.assertEqual(stats['total_contacts'], 2)


if __name__ == '__main__':
    unittest.main()