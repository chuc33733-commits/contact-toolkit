# -*- coding: utf-8 -*-
"""
字段扩充专项测试（含农历生日、照片）

覆盖：
1. Excel 列名识别（精确优先，避免"农历生日"被误判为"生日"）
2. Excel 新字段导入：昵称 / 农历生日 / 照片 / 网站 / 分类
3. 照片三种来源：本地相对路径、http(s) 链接、base64/data URI
4. 日期归一与非法内容告警（不静默丢数据）
5. VCF 折行（RFC 2425）还原与输出：照片 base64 完整往返
6. 农历生日（X-ALTBDAY 等）与未建模属性的保留
7. 真实 iPhone 导出数据中的折行照片必须能被完整解析
"""

import base64
import os
import struct
import shutil
import sys
import tempfile
import unittest
import zlib
from datetime import datetime

import pandas as pd

# 添加项目根目录到Python路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from models.contact import Contact, ContactManager
from processors.excel_processor import ExcelProcessor
from processors.vcf_processor import VCFProcessor
from config.settings import VCARD_FOLD_WIDTH
from utils.field_utils import parse_data_uri, photo_payload_size

SAMPLE_IPHONE_VCF = os.path.join(project_root, "所有联系人.vcf")


def write_png(path: str, size: int = 8, color=(37, 99, 235)) -> None:
    """
    生成一张纯色 PNG（用于照片导入测试，避免依赖 Pillow）。

    Args:
        path: 输出路径
        size: 边长
        color: RGB 颜色
    """
    raw = b''.join(b'\x00' + bytes(color) * size for _ in range(size))

    def chunk(tag: bytes, data: bytes) -> bytes:
        payload = struct.pack('>I', len(data)) + tag + data
        return payload + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b'\x89PNG\r\n\x1a\n'
    png += chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 2, 0, 0, 0))
    png += chunk(b'IDAT', zlib.compress(raw, 9))
    png += chunk(b'IEND', b'')
    with open(path, 'wb') as fh:
        fh.write(png)


class TempDirTestCase(unittest.TestCase):
    """提供临时目录的测试基类"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="contact_test_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def path(self, name: str) -> str:
        """返回临时目录下的文件路径"""
        return os.path.join(self.tmpdir, name)

    def write_excel(self, data: dict, name: str = "in.xlsx") -> str:
        """把字典写成 Excel 文件并返回路径"""
        target = self.path(name)
        pd.DataFrame(data).to_excel(target, index=False, engine='openpyxl')
        return target


class TestExcelColumnDetection(unittest.TestCase):
    """Excel 列名识别测试"""

    def setUp(self):
        self.processor = ExcelProcessor()

    def test_detect_all_fields(self):
        """13 个字段的表头都能正确识别"""
        df = pd.DataFrame(columns=[
            '姓名', '昵称', '电话', '邮箱', '公司', '职位', '地址',
            '生日', '农历生日', '照片', '网站', '备注', '分类',
        ])
        mapping = self.processor.detect_column_mapping(df)
        self.assertEqual(mapping, {
            'name': '姓名', 'nickname': '昵称', 'phone': '电话', 'email': '邮箱',
            'company': '公司', 'title': '职位', 'address': '地址', 'birthday': '生日',
            'lunar_birthday': '农历生日', 'photo': '照片', 'website': '网站',
            'notes': '备注', 'categories': '分类',
        })

    def test_lunar_birthday_not_confused_with_birthday(self):
        """关键歧义：'农历生日' 必须映射到 lunar_birthday 而不是 birthday"""
        df = pd.DataFrame(columns=['农历生日', '生日'])
        mapping = self.processor.detect_column_mapping(df)
        self.assertEqual(mapping['lunar_birthday'], '农历生日')
        self.assertEqual(mapping['birthday'], '生日')

    def test_alias_and_substring_matching(self):
        """支持别名与包含匹配（手机号码 → 电话，公司名称 → 公司）"""
        df = pd.DataFrame(columns=['联系人', '手机号码', '电子邮件', '公司名称', '工作单位', '头像'])
        mapping = self.processor.detect_column_mapping(df)
        self.assertEqual(mapping['name'], '联系人')
        self.assertEqual(mapping['phone'], '手机号码')
        self.assertEqual(mapping['email'], '电子邮件')
        self.assertIn(mapping['company'], ('公司名称', '工作单位'))
        self.assertEqual(mapping['photo'], '头像')

    def test_no_duplicate_assignment(self):
        """同一字段不会被重复占用，同一表头只映射一个字段"""
        df = pd.DataFrame(columns=['电话', '手机', '邮箱', '邮件地址'])
        mapping = self.processor.detect_column_mapping(df)
        self.assertEqual(len(set(mapping.values())), len(mapping))


class TestExcelFieldImport(TempDirTestCase):
    """Excel 新字段导入测试"""

    def setUp(self):
        super().setUp()
        self.processor = ExcelProcessor()

    def test_import_all_new_fields(self):
        """昵称/农历生日/网站/分类 等新字段应完整导入"""
        path = self.write_excel({
            '姓名': ['张三'],
            '昵称': ['小三, 老张'],
            '电话': ['13800138000, 010-12345678'],
            '邮箱': ['zhangsan@example.com'],
            '公司': ['阿里巴巴集团'],
            '职位': ['软件工程师'],
            '地址': ['北京市朝阳区'],
            '生日': ['1985年6月20日'],
            '农历生日': ['1989-12-08'],
            '网站': ['https://zhangsan.example.com'],
            '备注': ['重要客户'],
            '分类': ['客户；重要'],
        })

        manager, stats, error = self.processor.import_from_excel(path)

        self.assertEqual(error, "")
        self.assertEqual(stats['valid_contacts'], 1)
        contact = manager.get_all_contacts()[0]
        self.assertEqual(contact.name, '张三')
        self.assertEqual(contact.nicknames, ['小三', '老张'])
        self.assertEqual(contact.phones, ['13800138000', '1012345678'])
        self.assertEqual(contact.emails, ['zhangsan@example.com'])
        self.assertEqual(contact.organization, '阿里巴巴集团')
        self.assertEqual(contact.title, '软件工程师')
        self.assertEqual(contact.address, '北京市朝阳区')
        self.assertEqual(contact.birthday, '1985-06-20')
        self.assertEqual(contact.lunar_birthday, '1989-12-08')
        self.assertEqual(contact.website, 'https://zhangsan.example.com')
        self.assertEqual(contact.notes, '重要客户')
        self.assertEqual(contact.categories, ['客户', '重要'])

    def test_birthday_formats_normalized(self):
        """各类日期写法（含 Excel 日期单元格）统一归一为 YYYY-MM-DD"""
        path = self.write_excel({
            '姓名': ['甲', '乙', '丙', '丁', '戊'],
            '生日': [datetime(1990, 1, 15), '1985年6月20日', '1992/03/10',
                     '19881205', '1991.9.25'],
        })

        manager, _stats, error = self.processor.import_from_excel(path)

        self.assertEqual(error, "")
        values = [c.birthday for c in manager.get_all_contacts()]
        self.assertEqual(values, ['1990-01-15', '1985-06-20', '1992-03-10',
                                  '1988-12-05', '1991-09-25'])

    def test_lunar_birthday_free_text_preserved(self):
        """无法解析的农历自由文本原样保留，不做破坏性改写"""
        path = self.write_excel({'姓名': ['甲'], '农历生日': ['农历五月初五']})
        manager, _stats, _error = self.processor.import_from_excel(path)
        self.assertEqual(manager.get_all_contacts()[0].lunar_birthday, '农历五月初五')

    def test_photo_from_relative_path(self):
        """照片支持相对路径（相对 Excel 文件所在目录）"""
        photo_name = '示例头像.png'
        write_png(self.path(photo_name))
        path = self.write_excel({'姓名': ['张三'], '照片': [photo_name]})

        manager, stats, _error = self.processor.import_from_excel(path)

        self.assertEqual(stats['photos_imported'], 1)
        photo = manager.get_all_contacts()[0].photos[0]
        self.assertTrue(photo.startswith('data:image/png;base64,'))
        self.assertGreater(photo_payload_size(photo), 0)

    def test_photo_from_url(self):
        """照片支持 http(s) 链接"""
        url = 'https://example.com/avatar/lisi.jpg'
        path = self.write_excel({'姓名': ['李四'], '照片': [url]})

        manager, stats, _error = self.processor.import_from_excel(path)

        self.assertEqual(stats['photos_imported'], 1)
        self.assertEqual(manager.get_all_contacts()[0].photos[0], url)

    def test_photo_from_base64_text(self):
        """照片支持直接粘贴的 base64 文本"""
        payload = base64.b64encode(b'\xff\xd8\xff' + b'x' * 200).decode()
        path = self.write_excel({'姓名': ['王五'], '照片': [payload]})

        manager, stats, _error = self.processor.import_from_excel(path)

        self.assertEqual(stats['photos_imported'], 1)
        photo = manager.get_all_contacts()[0].photos[0]
        self.assertTrue(photo.startswith('data:image/jpeg;base64,'))
        self.assertGreater(photo_payload_size(photo), 200)

    def test_photo_missing_file_reported_not_silent(self):
        """照片路径不存在时不静默丢弃，而是给出告警"""
        path = self.write_excel({'姓名': ['赵六'], '照片': ['不存在的照片.jpg']})

        manager, stats, _error = self.processor.import_from_excel(path)

        self.assertEqual(stats['photos_imported'], 0)
        self.assertTrue(stats['warnings'], "照片读取失败未产生任何提示")
        self.assertIn('照片', stats['warnings'][0])

    def test_invalid_email_reported(self):
        """邮箱列内容非法时应汇总提示，而不是静默丢弃"""
        path = self.write_excel({
            '姓名': ['甲', '乙'],
            '邮箱': ['61999', 'ok@example.com'],
        })

        manager, stats, _error = self.processor.import_from_excel(path)

        contacts = manager.get_all_contacts()
        self.assertEqual(contacts[0].emails, [])
        self.assertEqual(contacts[1].emails, ['ok@example.com'])
        self.assertEqual(stats['invalid_emails'], 1)
        self.assertTrue(any('邮箱' in w for w in stats['warnings']))

    def test_export_excel_contains_new_columns(self):
        """导出 Excel 应包含新增字段列"""
        manager = ContactManager()
        contact = Contact()
        contact.set_name("张三")
        contact.add_nickname("小三")
        contact.set_lunar_birthday("1989-12-08")
        contact.set_website("https://example.com")
        contact.add_category("客户")
        manager.add_contact(contact)

        out = self.path('out.xlsx')
        error = self.processor.export_to_excel(manager, out)

        self.assertEqual(error, "")
        df = pd.read_excel(out)
        for column in ('姓名', '昵称', '农历生日', '照片', '网站', '分类'):
            self.assertIn(column, df.columns)

    def test_export_then_reimport_keeps_photo(self):
        """导出 Excel 后重新导入，内嵌照片应无损（落地到同级图片目录）"""
        write_png(self.path('头像.png'))
        path = self.write_excel({'姓名': ['张三'], '照片': ['头像.png']})
        manager, stats, _error = self.processor.import_from_excel(path)
        original = manager.get_all_contacts()[0].photos[0]

        out = self.path('export.xlsx')
        self.assertEqual(self.processor.export_to_excel(manager, out), "")
        self.assertTrue(os.path.isdir(self.path('export_photos')))

        manager2, stats2, _error2 = self.processor.import_from_excel(out)

        self.assertEqual(stats2['photos_imported'], 1)
        self.assertEqual(manager2.get_all_contacts()[0].photos[0], original)
        self.assertEqual(photo_payload_size(original),
                         photo_payload_size(manager2.get_all_contacts()[0].photos[0]))


class TestVcfFoldingAndNewFields(TempDirTestCase):
    """VCF 折行与新字段测试"""

    def setUp(self):
        super().setUp()
        self.processor = VCFProcessor()

    @staticmethod
    def _fold(value: str, head: str, width: int = 74) -> str:
        """
        按 RFC 2425 折行构造属性文本（续行以空格开头）。

        Args:
            value: 属性值
            head: 属性名部分（如 "PHOTO;ENCODING=b;TYPE=JPEG"）
            width: 续行内容宽度

        Returns:
            折行后的完整属性文本
        """
        lines = [f"{head}:{value[:width]}"]
        rest = value[width:]
        while rest:
            lines.append(' ' + rest[:width])
            rest = rest[width:]
        return '\n'.join(lines)

    def test_folded_photo_parsed_intact(self):
        """折行的照片 base64 必须被完整还原（回归：旧实现会截断照片）"""
        payload = base64.b64encode(bytes(range(256)) * 40).decode()
        vcard = '\n'.join([
            'BEGIN:VCARD', 'VERSION:3.0', 'FN:折行测试',
            self._fold(payload, 'PHOTO;ENCODING=b;TYPE=JPEG'),
            'END:VCARD',
        ])

        contacts = self.processor.parse_vcf_content(vcard)

        self.assertEqual(len(contacts), 1)
        self.assertEqual(len(contacts[0].photos), 1)
        parsed = parse_data_uri(contacts[0].photos[0])
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed[1], payload, "折行还原后照片 base64 不完整")

    def test_export_photo_is_folded_and_roundtrips(self):
        """导出的照片必须折行输出，且能无损重新导入"""
        payload = base64.b64encode(b'\xff\xd8\xff\xe0' + b'y' * 5000).decode()
        contact = Contact()
        contact.set_name("照片测试")
        contact.add_phone("13800138000")
        contact.add_photo(f"data:image/jpeg;base64,{payload}")

        vcard = self.processor.contact_to_vcard(contact)

        physical_lines = vcard.split('\n')
        self.assertGreater(len(physical_lines), 5, "超长照片行没有被折行")
        for line in physical_lines:
            self.assertLessEqual(len(line.encode('utf-8')), VCARD_FOLD_WIDTH,
                                 f"物理行超过折行宽度: {line[:40]}")

        # 往返
        manager = ContactManager()
        manager.add_contact(contact)
        out = self.path('out.vcf')
        self.assertEqual(self.processor.export_to_vcf(manager, out), "")

        manager2, stats, _error = self.processor.import_from_vcf(out)
        self.assertEqual(stats['photos_imported'], 1)
        parsed = parse_data_uri(manager2.get_all_contacts()[0].photos[0])
        self.assertEqual(parsed[1], payload, "导出往返后照片数据不一致")

    def test_photo_uri_form_exported(self):
        """链接型照片导出为 VALUE=URI 形式"""
        contact = Contact()
        contact.set_name("链接照片")
        contact.add_photo("https://example.com/a.jpg")

        vcard = self.processor.contact_to_vcard(contact)

        self.assertIn("PHOTO;VALUE=URI:https://example.com/a.jpg", vcard)

    def test_lunar_birthday_variants_parsed(self):
        """X-ALTBDAY / X-BIRTHDAY-LUNAR 等农历生日写法都能识别"""
        for key in ('X-ALTBDAY', 'X-BIRTHDAY-LUNAR', 'X-LUNAR-BIRTHDAY'):
            vcard = '\n'.join(['BEGIN:VCARD', 'VERSION:3.0', 'FN:农历',
                               f'{key}:1989-12-08', 'END:VCARD'])
            contact = self.processor.parse_vcard(vcard)
            self.assertEqual(contact.lunar_birthday, '1989-12-08', f"{key} 未被识别")

    def test_lunar_birthday_exported(self):
        """农历生日导出为 X-ALTBDAY"""
        contact = Contact()
        contact.set_name("农历")
        contact.set_lunar_birthday("1989-12-08")

        self.assertIn("X-ALTBDAY:1989-12-08", self.processor.contact_to_vcard(contact))

    def test_nickname_website_categories_parsed(self):
        """昵称、网站、分类应被解析"""
        vcard = '\n'.join([
            'BEGIN:VCARD', 'VERSION:3.0', 'FN:张三',
            'NICKNAME:小三,老张', 'URL:https://example.com',
            'CATEGORIES:客户,重要', 'END:VCARD',
        ])

        contact = self.processor.parse_vcard(vcard)

        self.assertEqual(contact.nicknames, ['小三', '老张'])
        self.assertEqual(contact.website, 'https://example.com')
        self.assertEqual(contact.categories, ['客户', '重要'])

    def test_unmodeled_properties_preserved(self):
        """未建模属性（IMPP、X-PHONETIC-*）应原样保留并重新导出"""
        vcard = '\n'.join([
            'BEGIN:VCARD', 'VERSION:3.0', 'FN:测试',
            'IMPP;X-SERVICE-TYPE=QQ;type=pref:x-apple:2443709663',
            'X-PHONETIC-LAST-NAME:Liu',
            'END:VCARD',
        ])

        contact = self.processor.parse_vcard(vcard)

        self.assertEqual(len(contact.extra_vcard_fields), 2)
        exported = self.processor.contact_to_vcard(contact)
        self.assertIn('IMPP;X-SERVICE-TYPE=QQ;type=pref:x-apple:2443709663', exported)
        self.assertIn('X-PHONETIC-LAST-NAME:Liu', exported)

    def test_multiple_adr_kept_in_extras(self):
        """同属性的多个不同取值（如工作地址）不丢失"""
        vcard = '\n'.join([
            'BEGIN:VCARD', 'VERSION:3.0', 'FN:测试',
            'ADR:;;北京市朝阳区;;;;',
            'ADR;TYPE=WORK:;;上海市浦东新区;;;;',
            'END:VCARD',
        ])

        contact = self.processor.parse_vcard(vcard)

        self.assertEqual(contact.address, '北京市朝阳区')
        self.assertIn('ADR;TYPE=WORK:;;上海市浦东新区;;;;', contact.extra_vcard_fields)

    def test_quoted_printable_decoded(self):
        """vCard 2.1 的 QUOTED-PRINTABLE（含软换行）应能正确解码"""
        vcard = '\n'.join([
            'BEGIN:VCARD', 'VERSION:2.1',
            'FN;ENCODING=QUOTED-PRINTABLE;CHARSET=UTF-8:=E5=BC=A0=',
            '=E4=B8=89',
            'TEL:13800138000',
            'END:VCARD',
        ])

        contact = self.processor.parse_vcard(vcard)

        self.assertEqual(contact.name, '张三')

    def test_vcf_export_import_all_new_fields(self):
        """VCF 往返：新字段全部保留"""
        contact = Contact()
        contact.set_name("全字段")
        contact.add_nickname("小名")
        contact.add_phone("13800138000")
        contact.add_email("a@example.com")
        contact.set_organization("测试公司")
        contact.set_title("工程师")
        contact.set_address("成都市高新区")
        contact.set_birthday("1990-01-15")
        contact.set_lunar_birthday("1989-12-08")
        contact.set_website("https://example.com")
        contact.add_category("同事")
        contact.add_photo("https://example.com/photo.jpg")

        manager = ContactManager()
        manager.add_contact(contact)
        out = self.path('all.vcf')
        self.assertEqual(self.processor.export_to_vcf(manager, out), "")

        manager2, _stats, _error = self.processor.import_from_vcf(out)
        result = manager2.get_all_contacts()[0]

        self.assertEqual(result.name, '全字段')
        self.assertEqual(result.nicknames, ['小名'])
        self.assertEqual(result.phones, ['13800138000'])
        self.assertEqual(result.emails, ['a@example.com'])
        self.assertEqual(result.organization, '测试公司')
        self.assertEqual(result.title, '工程师')
        self.assertEqual(result.address, '成都市高新区')
        self.assertEqual(result.birthday, '1990-01-15')
        self.assertEqual(result.lunar_birthday, '1989-12-08')
        self.assertEqual(result.website, 'https://example.com')
        self.assertEqual(result.categories, ['同事'])
        self.assertEqual(result.photos, ['https://example.com/photo.jpg'])


class TestRealIphoneData(unittest.TestCase):
    """真实数据验证（iPhone 导出的 vCard 使用标准折行存放照片）"""

    def setUp(self):
        self.processor = VCFProcessor()

    @unittest.skipUnless(os.path.exists(SAMPLE_IPHONE_VCF), "样例 VCF 不存在")
    def test_folded_photos_in_real_data_are_intact(self):
        """真实 iPhone 导出文件里的折行照片应能完整解析为有效图片"""
        with open(SAMPLE_IPHONE_VCF, 'r', encoding='utf-8') as fh:
            content = fh.read()
        if 'PHOTO' not in content:
            self.skipTest("样例文件不含照片，跳过")

        manager, stats, error = self.processor.import_from_vcf(SAMPLE_IPHONE_VCF)

        self.assertEqual(error, "")
        self.assertGreater(stats['photos_imported'], 0, "真实数据中的照片没有被解析出来")

        for contact in manager.get_all_contacts():
            for photo in contact.photos:
                parsed = parse_data_uri(photo)
                self.assertIsNotNone(parsed, f"{contact.name} 的照片值不规范")
                binary = base64.b64decode(parsed[1])
                self.assertTrue(
                    binary.startswith(b'\xff\xd8') or binary.startswith(b'\x89PNG'),
                    f"{contact.name} 的照片不是有效图片: {binary[:8]!r}",
                )
                self.assertGreater(len(binary), 1024,
                                   f"{contact.name} 的照片数据疑似被截断")

    @unittest.skipUnless(os.path.exists(SAMPLE_IPHONE_VCF), "样例 VCF 不存在")
    def test_real_data_export_roundtrip_keeps_photos(self):
        """真实数据导出后重新导入，照片与联系人数量都应保持"""
        manager, stats, _error = self.processor.import_from_vcf(SAMPLE_IPHONE_VCF)
        if stats['photos_imported'] == 0:
            self.skipTest("样例文件不含照片，跳过")

        tmpdir = tempfile.mkdtemp(prefix="contact_roundtrip_")
        try:
            out = os.path.join(tmpdir, "out.vcf")
            self.assertEqual(self.processor.export_to_vcf(manager, out), "")
            manager2, stats2, _error2 = self.processor.import_from_vcf(out)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

        self.assertEqual(manager2.count(), manager.count())
        self.assertEqual(stats2['photos_imported'], stats['photos_imported'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
