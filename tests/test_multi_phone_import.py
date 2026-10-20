# -*- coding: utf-8 -*-
"""
多号码 / 多列导入回归测试

背景：用户反馈"导入多个电话号码只识别第一个"。根因有两类：
1. Excel 多电话列（如"电话1/电话2"、"手机/固话"、"mobile/tel"）——
   旧实现一个字段只占一个表头，第二列起的号码被整体丢弃；
2. 单元格内换行分隔的多个号码不会被拆分，被拼成一个超长号码。

另修复：Apple 导出的 itemN.TEL / itemN.EMAIL / itemN.ADR 前缀属性
旧实现不识别（全部落入"未建模字段"），号码/邮箱等无法导入。

覆盖：
1. Excel 多电话列 → 全部导入（含中英文别名组合）
2. 单元格内换行分隔 → 拆分导入
3. 单列逗号分隔等旧行为不变（回归保护）
4. VCF itemN.TEL / itemN.EMAIL / itemN.ADR → 正确识别；itemN.X-ABLabel 保留
"""

import os
import shutil
import sys
import tempfile
import unittest

import pandas as pd

# 添加项目根目录到Python路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from processors.excel_processor import ExcelProcessor
from processors.vcf_processor import VCFProcessor


class TempDirTestCase(unittest.TestCase):
    """提供临时目录的测试基类"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="contact_multi_test_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def write_excel(self, data: dict, name: str = "in.xlsx") -> str:
        """把字典写成 Excel 文件并返回路径"""
        target = os.path.join(self.tmpdir, name)
        pd.DataFrame(data).to_excel(target, index=False, engine='openpyxl')
        return target


class TestMultiPhoneColumns(TempDirTestCase):
    """Excel 多电话列导入"""

    def setUp(self):
        super().setUp()
        self.processor = ExcelProcessor()

    def import_phones(self, data: dict):
        """导入单行 Excel，返回第一个联系人的电话列表"""
        path = self.write_excel(data)
        manager, stats, error = self.processor.import_from_excel(path)
        self.assertEqual(error, "")
        contacts = manager.get_all_contacts()
        self.assertEqual(len(contacts), 1)
        return contacts[0].phones

    def test_two_phone_columns(self):
        """"电话1/电话2" 两列都要导入"""
        phones = self.import_phones({
            '姓名': ['张三'],
            '电话1': ['13800138000'],
            '电话2': ['13900139000'],
        })
        self.assertEqual(phones, ['13800138000', '13900139000'])

    def test_phone_columns_by_alias(self):
        """"手机/固话" 组合两列都要导入"""
        phones = self.import_phones({
            '姓名': ['张三'],
            '手机': ['13800138000'],
            '固话': ['1012345678'],
        })
        self.assertEqual(phones, ['13800138000', '1012345678'])

    def test_english_columns(self):
        """"mobile/tel" 英文列组合两列都要导入"""
        phones = self.import_phones({
            'name': ['张三'],
            'mobile': ['13800138000'],
            'tel': ['13900139000'],
        })
        self.assertEqual(phones, ['13800138000', '13900139000'])

    def test_three_columns_with_duplicate_dedup(self):
        """三列（含重复号码）→ 导入并去重，保持列顺序"""
        phones = self.import_phones({
            '姓名': ['张三'],
            '联系电话': ['13800138000'],
            '备用电话': ['13800138000, 13900139000'],
            '办公电话': ['400-820-8820'],
        })
        self.assertEqual(phones, ['13800138000', '13900139000', '4008208820'])

    def test_multiline_cell_split(self):
        """单元格内换行分隔的号码要拆分为多个"""
        phones = self.import_phones({
            '姓名': ['张三'],
            '电话': ['13800138000\n13900139000'],
        })
        self.assertEqual(phones, ['13800138000', '13900139000'])

    def test_single_column_comma_separated_regression(self):
        """回归保护：单列逗号分隔的旧行为不变"""
        phones = self.import_phones({
            '姓名': ['张三'],
            '电话': ['13800138000, 13900139000'],
        })
        self.assertEqual(phones, ['13800138000', '13900139000'])

    def test_nickname_and_email_multi_columns(self):
        """列表字段（昵称/邮箱）的多列同样合并"""
        path = self.write_excel({
            '姓名': ['张三'],
            '昵称': ['小三'],
            '别名': ['老张'],
            '邮箱': ['a@example.com'],
            '邮件地址': ['b@example.com'],
        })
        manager, _stats, error = self.processor.import_from_excel(path)
        self.assertEqual(error, "")
        contact = manager.get_all_contacts()[0]
        self.assertEqual(contact.nicknames, ['小三', '老张'])
        # '邮件地址' 是更长（更具体）的精确别名，按既有"更长别名优先"规则排在 '邮箱' 之前
        self.assertEqual(contact.emails, ['b@example.com', 'a@example.com'])


class TestColumnMappingCandidates(unittest.TestCase):
    """列映射候选逻辑：同字段可占多列、表头唯一归属"""

    def setUp(self):
        self.processor = ExcelProcessor()

    def test_same_field_multiple_columns(self):
        """phone 字段可同时占用"电话1"和"电话2"两个表头"""
        df = pd.DataFrame(columns=['姓名', '电话1', '电话2'])
        candidates = self.processor.detect_column_mapping_candidates(df)
        self.assertEqual(candidates['phone'], ['电话1', '电话2'])

    def test_header_belongs_to_single_field(self):
        """一个表头只归一个字段；字段可占多列"""
        df = pd.DataFrame(columns=['电话', '手机', '邮箱', '邮件地址'])
        candidates = self.processor.detect_column_mapping_candidates(df)
        all_columns = [col for columns in candidates.values() for col in columns]
        self.assertEqual(len(all_columns), len(set(all_columns)),
                         "同一表头被分配给了多个字段")
        self.assertEqual(candidates['phone'], ['电话', '手机'])
        # '邮件地址' 是更长（更具体）的精确别名，按既有"更长别名优先"规则排在 '邮箱' 之前
        self.assertEqual(candidates['email'], ['邮件地址', '邮箱'])

    def test_single_column_interface_unchanged(self):
        """兼容接口 detect_column_mapping 仍返回单列结构"""
        df = pd.DataFrame(columns=['姓名', '电话', '邮箱'])
        mapping = self.processor.detect_column_mapping(df)
        self.assertEqual(mapping, {'name': '姓名', 'phone': '电话', 'email': '邮箱'})


class TestVcfItemPrefixedProperties(TempDirTestCase):
    """VCF Apple itemN. 前缀属性识别"""

    def setUp(self):
        super().setUp()
        self.processor = VCFProcessor()

    def parse(self, vcard: str):
        """解析单个 vCard 文本"""
        return self.processor.parse_vcard(vcard)

    def test_item_prefixed_tel_recognized(self):
        """item1.TEL / item2.TEL 应识别为电话"""
        contact = self.parse(
            "BEGIN:VCARD\n"
            "VERSION:3.0\n"
            "FN:张三\n"
            "item1.TEL;type=pref:13800138000\n"
            "item2.TEL:1-822-727-4471\n"
            "END:VCARD\n"
        )
        self.assertEqual(contact.phones, ['13800138000', '18227274471'])

    def test_item_prefixed_email_recognized(self):
        """item1.EMAIL 应识别为邮箱"""
        contact = self.parse(
            "BEGIN:VCARD\n"
            "VERSION:3.0\n"
            "FN:张三\n"
            "item1.EMAIL:zhangsan@example.com\n"
            "END:VCARD\n"
        )
        self.assertEqual(contact.emails, ['zhangsan@example.com'])

    def test_item_prefixed_adr_recognized(self):
        """item1.ADR 应识别为地址"""
        contact = self.parse(
            "BEGIN:VCARD\n"
            "VERSION:3.0\n"
            "FN:李四\n"
            "item1.ADR;type=HOME;type=pref:;;;;;;英属维尔京群岛\n"
            "END:VCARD\n"
        )
        self.assertEqual(contact.address, '英属维尔京群岛')

    def test_item_label_still_preserved(self):
        """item1.X-ABLabel 等未建模的 item 属性仍原样保留，不丢失"""
        contact = self.parse(
            "BEGIN:VCARD\n"
            "VERSION:3.0\n"
            "FN:张三\n"
            "item1.TEL:6909 0\n"
            "item1.X-ABLabel:短号\n"
            "END:VCARD\n"
        )
        self.assertEqual(contact.phones, ['69090'])
        self.assertIn('item1.X-ABLabel:短号', contact.extra_vcard_fields)
        # item 前缀的 TEL 已被识别为电话，不再重复进入未建模字段
        self.assertFalse(
            any('TEL' in field and field.upper().startswith('ITEM1.TEL')
                for field in contact.extra_vcard_fields)
        )

    def test_plain_properties_unaffected(self):
        """回归保护：普通 TEL / EMAIL 行为不变"""
        contact = self.parse(
            "BEGIN:VCARD\n"
            "VERSION:3.0\n"
            "FN:张三\n"
            "TEL;TYPE=CELL;TYPE=VOICE:13800138000\n"
            "TEL;TYPE=WORK:1012345678\n"
            "EMAIL:a@example.com\n"
            "END:VCARD\n"
        )
        self.assertEqual(contact.phones, ['13800138000', '1012345678'])
        self.assertEqual(contact.emails, ['a@example.com'])


if __name__ == '__main__':
    unittest.main()
