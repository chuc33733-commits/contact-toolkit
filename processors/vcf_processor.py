# -*- coding: utf-8 -*-
"""
VCF文件处理器
支持VCF文件读写和解析

重要修复：vCard 的长行会按 RFC 2425 折行，续行以「空格/制表符」开头。
照片（PHOTO）的 base64 数据几乎必然是折行的，旧实现在解析和导出时直接
strip 每一行，会把折行压掉并破坏照片数据。本实现统一做了折行还原
（unfold）与折行输出（fold），保证照片、长备注等字段可以完整往返。
"""

import os
import quopri
import re
from typing import Any, Dict, List, Optional, Tuple

import chardet

from models.contact import Contact, ContactManager
from config.settings import (
    VCF_FIELD_MAPPING,
    DEFAULT_ENCODING,
    FALLBACK_ENCODINGS,
    LUNAR_BIRTHDAY_VCARD_KEYS,
    VCF_GENERATED_PROPERTIES,
    VCARD_FOLD_WIDTH,
)
from utils.field_utils import is_data_uri, is_remote_uri, parse_data_uri, normalize_photo_value
from utils.merger import ContactMerger

# 单值属性：同一次解析中出现多次时，只取首个，其余原样保留到 extra_vcard_fields
SINGLE_VALUE_PROPERTIES = {
    'ORG': 'organization',
    'TITLE': 'title',
    'ADR': 'address',
    'BDAY': 'birthday',
    'URL': 'website',
}

# 图片类型参数 → MIME
_PHOTO_MIME_BY_TYPE = {
    'JPEG': 'image/jpeg',
    'JPG': 'image/jpeg',
    'PNG': 'image/png',
    'GIF': 'image/gif',
    'BMP': 'image/bmp',
    'WEBP': 'image/webp',
    'TIFF': 'image/tiff',
    'HEIC': 'image/heic',
}

# MIME → vCard TYPE 参数（导出用）
_PHOTO_TYPE_BY_MIME = {
    'image/jpeg': 'JPEG',
    'image/png': 'PNG',
    'image/gif': 'GIF',
    'image/bmp': 'BMP',
    'image/webp': 'WEBP',
    'image/tiff': 'TIFF',
    'image/heic': 'HEIC',
}


class VCFProcessor:
    """VCF文件处理器"""
    
    def __init__(self):
        """初始化VCF处理器"""
        self.field_mapping = VCF_FIELD_MAPPING
        self.default_encoding = DEFAULT_ENCODING
        self.fallback_encodings = FALLBACK_ENCODINGS
        self.merger = ContactMerger()
    
    def detect_encoding(self, file_path: str) -> str:
        """检测文件编码"""
        try:
            with open(file_path, 'rb') as f:
                raw_data = f.read()
                result = chardet.detect(raw_data)
                encoding = result.get('encoding', self.default_encoding)
                
                # 如果检测到的编码置信度太低，使用默认编码
                if result.get('confidence', 0) < 0.7:
                    encoding = self.default_encoding
                
                return encoding
        except Exception:
            return self.default_encoding
    
    def read_vcf_file(self, file_path: str, encoding: Optional[str] = None) -> Tuple[str, str]:
        """
        读取VCF文件内容
        
        Args:
            file_path: VCF文件路径
            encoding: 指定编码，None表示自动检测
            
        Returns:
            (文件内容, 错误信息)
        """
        if not os.path.exists(file_path):
            return "", f"文件不存在: {file_path}"
        
        if not file_path.lower().endswith(('.vcf', '.vcard')):
            return "", f"不支持的文件格式: {os.path.splitext(file_path)[1]}"
        
        # 确定编码
        if not encoding:
            encoding = self.detect_encoding(file_path)
        
        # 尝试读取文件
        encodings_to_try = [encoding] + [enc for enc in self.fallback_encodings if enc != encoding]
        
        for enc in encodings_to_try:
            try:
                with open(file_path, 'r', encoding=enc) as f:
                    content = f.read()
                    return content, ""
            except UnicodeDecodeError:
                continue
            except Exception as e:
                return "", f"读取文件时发生错误: {str(e)}"
        
        return "", f"无法使用任何编码读取文件: {file_path}"

    # ------------------------------------------------------------------
    # 折行（RFC 2425）与值转义
    # ------------------------------------------------------------------

    @staticmethod
    def _is_quoted_printable(field_part: str) -> bool:
        """判断属性是否使用 QUOTED-PRINTABLE 编码。"""
        return 'QUOTED-PRINTABLE' in field_part.upper()

    def unfold_lines(self, text: str) -> List[str]:
        """
        还原 vCard 的折行，得到逻辑行列表。

        规则：
        1. 以空格或制表符开头的物理行，是上一行的续行（去掉首个空白字符）；
        2. QUOTED-PRINTABLE 编码的属性，以 '=' 结尾表示软换行，与下一行直接拼接。

        Args:
            text: 原始 vCard 文本

        Returns:
            逻辑行列表（已去除空行）
        """
        raw_lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
        logical: List[str] = []

        for raw in raw_lines:
            if raw[:1] in (' ', '\t') and logical:
                logical[-1] += raw[1:]
                continue
            if logical and self._is_quoted_printable(logical[-1].split(':', 1)[0]) \
                    and logical[-1].rstrip().endswith('='):
                logical[-1] = logical[-1].rstrip()[:-1] + raw
                continue
            logical.append(raw)

        return [line for line in logical if line.strip()]

    @staticmethod
    def fold_line(line: str, limit: int = VCARD_FOLD_WIDTH) -> List[str]:
        """
        按 RFC 2425 折行（按 UTF-8 字节数切分，不切断多字节字符）。

        Args:
            line: 逻辑行
            limit: 每个物理行的最大字节数

        Returns:
            物理行列表（续行以单个空格开头）
        """
        if len(line.encode('utf-8')) <= limit:
            return [line]

        chunks: List[str] = []
        current = ''
        current_bytes = 0
        allowed = limit
        for char in line:
            size = len(char.encode('utf-8'))
            if current_bytes + size > allowed:
                chunks.append(current)
                current = ''
                current_bytes = 0
                allowed = limit - 1  # 续行第一列是折行用的空格
            current += char
            current_bytes += size
        if current:
            chunks.append(current)

        return [chunks[0]] + [' ' + chunk for chunk in chunks[1:]]

    @staticmethod
    def _parse_property(field_part: str) -> Tuple[str, Dict[str, str]]:
        """
        解析属性名与参数。

        Args:
            field_part: 冒号前的部分，如 "TEL;type=CELL;type=pref"

        Returns:
            (大写属性名, 参数键值对)
        """
        segments = field_part.split(';')
        name = segments[0].strip().upper()
        params: Dict[str, str] = {}
        for segment in segments[1:]:
            if '=' in segment:
                key, value = segment.split('=', 1)
                params[key.strip().upper()] = value.strip()
            elif segment.strip():
                # 无键参数（如 PHOTO;JPEG）表示图片类型
                params.setdefault('TYPE', segment.strip().upper())
        return name, params

    @staticmethod
    def _unescape_value(value: str) -> str:
        """
        还原 vCard 文本值中的转义（\\n、\\,、\\;、\\\\）。

        Args:
            value: 原始值

        Returns:
            还原后的文本
        """
        if not value:
            return ""
        text = re.sub(r'\\[nN]', '\n', value)
        return re.sub(r'\\([\\;,])', r'\1', text)

    @staticmethod
    def _escape_value(value: str) -> str:
        """
        转义 vCard 文本值中的特殊字符。

        Args:
            value: 原始文本

        Returns:
            转义后的文本
        """
        if not value:
            return ""
        text = value.replace('\\', '\\\\')
        text = text.replace('\r\n', '\n').replace('\r', '\n').replace('\n', '\\n')
        return text.replace(';', '\\;').replace(',', '\\,')

    @staticmethod
    def _decode_quoted_printable(value: str, encoding: str = 'utf-8') -> str:
        """
        解码 QUOTED-PRINTABLE 值（兼容 vCard 2.1）。

        Args:
            value: 编码值
            encoding: 原始字符集

        Returns:
            解码后的文本；解码失败时返回原值
        """
        try:
            raw = quopri.decodestring(value.encode('ascii', errors='ignore'))
            return raw.decode(encoding, errors='replace')
        except Exception:
            return value

    # ------------------------------------------------------------------
    # 解析
    # ------------------------------------------------------------------

    def parse_vcard(self, vcard_text: str, encoding: str = 'utf-8') -> Contact:
        """
        解析单个vCard文本为Contact对象
        
        Args:
            vcard_text: vCard文本内容
            encoding: 文件编码（用于 QUOTED-PRINTABLE 解码）
            
        Returns:
            Contact对象
        """
        contact = Contact()
        # 保存原始vCard数据（仅作追溯，导出统一由数据模型生成）
        contact.set_raw_vcard(vcard_text)

        for line in self.unfold_lines(vcard_text):
            if ':' not in line:
                continue

            field_part, raw_value = line.split(':', 1)
            property_name, params = self._parse_property(field_part)
            if property_name in ('BEGIN', 'END', 'VERSION', 'PRODID', 'REV'):
                continue

            if params.get('ENCODING', '').upper() == 'QUOTED-PRINTABLE':
                raw_value = self._decode_quoted_printable(raw_value, encoding)

            value = raw_value.strip()
            if not value:
                continue

            # ---- 姓名 ----
            if property_name == 'FN':
                contact.set_name(self._unescape_value(value))
                continue

            if property_name == 'N':
                # N字段格式: 姓;名;中间名;前缀;后缀
                name_parts = value.split(';')
                if len(name_parts) >= 2 and not contact.name:
                    full_name = f"{name_parts[0].strip()}{name_parts[1].strip()}"
                    if full_name:
                        contact.set_name(self._unescape_value(full_name))
                continue

            # ---- 昵称/别名 ----
            if property_name == 'NICKNAME':
                for nickname in self._unescape_value(value).replace(';', ',').split(','):
                    if nickname.strip():
                        contact.add_nickname(nickname.strip())
                continue

            # ---- 电话 ----
            if property_name == 'TEL':
                phone = re.sub(r'[^\d+\-\(\)\s]', '', value)
                if phone:
                    contact.add_phone(phone)
                continue

            # ---- 邮箱 ----
            if property_name == 'EMAIL':
                contact.add_email(value)
                continue

            # ---- 公司 ----
            if property_name == 'ORG':
                # ORG 规范为"公司名;部门"，用 ';' 分隔后拼回更贴近用户习惯的写法
                org_parts = [self._unescape_value(part).strip()
                             for part in raw_value.split(';')]
                org_value = ' '.join(part for part in org_parts if part)
                self._assign_single_value(contact, 'organization', 'ORG', line,
                                          org_value, setter=contact.set_organization)
                continue

            # ---- 职位 ----
            if property_name == 'TITLE':
                self._assign_single_value(contact, 'title', 'TITLE', line,
                                          self._unescape_value(value), setter=contact.set_title)
                continue

            # ---- 地址 ----
            if property_name == 'ADR':
                addr_components = [part.strip() for part in value.split(';') if part.strip()]
                if addr_components:
                    self._assign_single_value(contact, 'address', 'ADR', line,
                                              self._unescape_value(' '.join(addr_components)),
                                              setter=contact.set_address)
                continue

            # ---- 公历生日 ----
            if property_name == 'BDAY':
                self._assign_single_value(contact, 'birthday', 'BDAY', line, value,
                                          setter=contact.set_birthday)
                continue

            # ---- 农历生日（Android 的 X-ALTBDAY 等写法） ----
            if property_name in LUNAR_BIRTHDAY_VCARD_KEYS:
                self._assign_single_value(contact, 'lunar_birthday', property_name, line,
                                          self._unescape_value(value),
                                          setter=contact.set_lunar_birthday)
                continue

            # ---- 网站 ----
            if property_name == 'URL':
                self._assign_single_value(contact, 'website', 'URL', line,
                                          self._unescape_value(value), setter=contact.set_website)
                continue

            # ---- 照片 ----
            if property_name == 'PHOTO':
                photo = self._parse_photo(value, params)
                if photo:
                    contact.add_photo(photo)
                continue

            # ---- 备注 ----
            if property_name == 'NOTE':
                note = self._unescape_value(value)
                if contact.notes:
                    contact.append_notes(note)
                else:
                    contact.set_notes(note)
                continue

            # ---- 分类 ----
            if property_name == 'CATEGORIES':
                for category in self._unescape_value(value).split(','):
                    if category.strip():
                        contact.add_category(category.strip())
                continue

            # ---- 其余属性原样保留，避免信息丢失（IMPP、X-PHONETIC-*、X-SOCIALPROFILE 等） ----
            if property_name not in VCF_GENERATED_PROPERTIES:
                contact.add_extra_vcard_field(line)

        return contact

    @staticmethod
    def _assign_single_value(contact: Contact, attr: str, property_name: str,
                             raw_line: str, value: str, setter) -> None:
        """
        为单值字段赋值；若同属性重复出现且取值不同，把多余的一行原样保留下来。

        Args:
            contact: 联系人对象
            attr: 目标属性名（用于判断是否已赋值）
            property_name: vCard 属性名
            raw_line: 原始属性行
            value: 解析出的值
            setter: 赋值方法
        """
        if not value:
            return
        current = getattr(contact, attr, "")
        if not current:
            setter(value)
        elif current != value:
            # 同一属性出现多个不同取值（如家庭/工作地址）：保留首个，其余原样存档
            contact.add_extra_vcard_field(raw_line)

    def _parse_photo(self, value: str, params: Dict[str, str]) -> str:
        """
        解析 PHOTO 属性为规范化的照片值（data URI 或链接）。

        Args:
            value: PHOTO 值（base64 或 URI）
            params: 属性参数（TYPE / VALUE / MEDIATYPE 等）

        Returns:
            规范化照片值；无法识别时返回空字符串
        """
        if is_remote_uri(value):
            return value

        if is_data_uri(value):
            return re.sub(r'\s+', '', value)

        compact = re.sub(r'\s+', '', value)

        # base64：vCard 3.0 用 ENCODING=b，2.1 用 ENCODING=BASE64
        encoding = params.get('ENCODING', '').upper()
        if encoding in ('B', 'BASE64'):
            mime = self._photo_mime(params)
            normalized, _error = normalize_photo_value(f"data:{mime};base64,{compact}")
            return normalized

        # 未声明编码：交给统一解析（依次尝试本地文件路径与裸 base64）
        normalized, _error = normalize_photo_value(value)
        return normalized

    @staticmethod
    def _photo_mime(params: Dict[str, str]) -> str:
        """
        从属性参数推断照片 MIME 类型。

        Args:
            params: 属性参数

        Returns:
            MIME 类型，默认 image/jpeg
        """
        for key in ('MEDIATYPE', 'TYPE', 'FMTTYPE'):
            raw = params.get(key, '')
            if not raw:
                continue
            candidate = raw.split('/')[-1].upper()
            if candidate in _PHOTO_MIME_BY_TYPE:
                return _PHOTO_MIME_BY_TYPE[candidate]
            if raw.lower().startswith('image/'):
                return raw.lower()
        return 'image/jpeg'
    
    def parse_vcf_content(self, content: str, encoding: str = 'utf-8') -> List[Contact]:
        """
        解析VCF文件内容
        
        Args:
            content: VCF文件内容
            encoding: 文件编码
            
        Returns:
            Contact对象列表
        """
        contacts = []
        vcards: List[str] = []
        current: List[str] = []
        in_vcard = False

        # 基于"折行还原后"的逻辑行切分，避免把照片 base64 的续行当成独立字段
        for line in self.unfold_lines(content):
            stripped = line.strip()
            upper = stripped.upper()
            if upper == 'BEGIN:VCARD':
                in_vcard = True
                current = [line]
            elif upper == 'END:VCARD':
                if in_vcard:
                    current.append(line)
                    vcards.append('\n'.join(current))
                    current = []
                in_vcard = False
            elif in_vcard:
                current.append(line)

        # 解析每个vCard
        for vcard_text in vcards:
            try:
                contact = self.parse_vcard(vcard_text, encoding=encoding)
                if not contact.is_empty():
                    contacts.append(contact)
            except Exception as e:
                print(f"解析vCard时发生错误: {str(e)}")
                continue
        
        return contacts
    
    def import_from_vcf(self, file_path: str, encoding: Optional[str] = None) -> Tuple[ContactManager, Dict[str, Any], str]:
        """
        从VCF文件导入联系人
        
        Args:
            file_path: VCF文件路径
            encoding: 指定编码
            
        Returns:
            (ContactManager对象, 统计信息, 错误信息)
        """
        # 读取VCF文件
        content, error = self.read_vcf_file(file_path, encoding)
        if error:
            return None, {}, error

        used_encoding = encoding or self.detect_encoding(file_path)

        # 解析VCF内容
        contacts = self.parse_vcf_content(content, encoding=used_encoding)
        
        # 创建联系人管理器
        contact_manager = ContactManager()
        
        # 统计信息
        stats = {
            'total_vcards': len(contacts),
            'valid_contacts': 0,
            'empty_contacts': 0,
            'photos_imported': sum(len(c.photos) for c in contacts),
            'file_encoding': used_encoding
        }
        
        # 添加联系人到管理器
        for contact in contacts:
            if contact.is_empty():
                stats['empty_contacts'] += 1
            else:
                contact_manager.add_contact(contact)
                stats['valid_contacts'] += 1
        
        return contact_manager, stats, ""

    # ------------------------------------------------------------------
    # 导出
    # ------------------------------------------------------------------

    @staticmethod
    def _build_n_line(name: str) -> str:
        """
        由姓名生成 vCard 的 N 属性。

        中文姓名按"首字为姓、其余为名"拆分（张三 → 张;三;;;），
        非中文姓名不强行拆分，整体写入姓氏槽位。

        Args:
            name: 姓名

        Returns:
            N 属性行
        """
        is_cjk = all('\u4e00' <= char <= '\u9fff' or char in '·•' for char in name)
        if is_cjk and len(name) >= 2:
            return f"N:{name[0]};{name[1:]};;;"
        return f"N:{name};;;;"

    def _photo_to_vcard_lines(self, photo: str) -> List[str]:
        """
        把照片值转换为 PHOTO 属性行（长 base64 会被折行）。

        Args:
            photo: 规范化照片值

        Returns:
            PHOTO 属性行列表（已折行）
        """
        if is_remote_uri(photo):
            return self.fold_line(f"PHOTO;VALUE=URI:{photo}")

        parsed = parse_data_uri(photo)
        if not parsed:
            return []
        mime, payload = parsed
        extension = _PHOTO_TYPE_BY_MIME.get(mime.lower(), 'JPEG')
        return self.fold_line(f"PHOTO;ENCODING=b;TYPE={extension}:{re.sub(r'\s+', '', payload)}")

    def contact_to_vcard(self, contact: Contact) -> str:
        """
        将Contact对象转换为vCard文本。

        统一由数据模型生成（不再直接沿用原始文本），这样合并后的字段变化
        （电话去重、姓名精简、括注转移等）才会真实反映到导出结果里；
        未建模的原始属性行会原样追加，保证信息不丢失。

        Args:
            contact: Contact对象
            
        Returns:
            vCard文本（已按 RFC 2425 折行）
        """
        lines = ['BEGIN:VCARD', 'VERSION:3.0']

        # 姓名
        if contact.name:
            lines.append(f"FN:{self._escape_value(contact.name)}")
            lines.append(self._build_n_line(contact.name))

        # 昵称/别名
        for nickname in contact.nicknames:
            lines.append(f"NICKNAME:{self._escape_value(nickname)}")

        # 电话（去重后输出）
        normalized_phones = []
        seen_phones = set()
        for phone in contact.phones:
            normalized = contact.normalize_phone(phone)
            if normalized and normalized not in seen_phones:
                seen_phones.add(normalized)
                normalized_phones.append(phone)
        for index, phone in enumerate(normalized_phones):
            lines.append(f"TEL;TYPE=CELL:{phone}" if index == 0 else f"TEL:{phone}")

        # 邮箱
        for email in contact.emails:
            lines.append(f"EMAIL:{email}")

        # 组织
        if contact.organization:
            lines.append(f"ORG:{self._escape_value(contact.organization)}")

        # 职位
        if contact.title:
            lines.append(f"TITLE:{self._escape_value(contact.title)}")

        # 地址
        if contact.address:
            lines.append(f"ADR:;;{self._escape_value(contact.address)};;;;")

        # 公历生日
        if contact.birthday:
            lines.append(f"BDAY:{contact.birthday}")

        # 农历生日（Android 通用写法 X-ALTBDAY）
        if contact.lunar_birthday:
            lines.append(f"X-ALTBDAY:{contact.lunar_birthday}")

        # 网站
        if contact.website:
            lines.append(f"URL:{contact.website}")

        # 照片
        for photo in contact.photos:
            lines.extend(self._photo_to_vcard_lines(photo))

        # 备注
        if contact.notes:
            lines.append(f"NOTE:{self._escape_value(contact.notes)}")

        # 分类
        if contact.categories:
            lines.append(f"CATEGORIES:{','.join(contact.categories)}")

        # 未建模属性原样保留
        lines.extend(contact.extra_vcard_fields)

        lines.append('END:VCARD')

        # 统一折行输出（照片 base64 等超长行必须折行才能被其它工具正确读取）
        folded: List[str] = []
        for line in lines:
            folded.extend(self.fold_line(line))

        return '\n'.join(folded)
    
    def export_to_vcf(self, contact_manager: ContactManager, file_path: str, encoding: str = 'utf-8') -> str:
        """
        将联系人导出为VCF文件
        
        Args:
            contact_manager: 联系人管理器
            file_path: 输出文件路径
            encoding: 文件编码
            
        Returns:
            错误信息，空字符串表示成功
        """
        try:
            contacts = contact_manager.get_all_contacts()
            if not contacts:
                return "没有联系人数据可导出"
            
            # 生成VCF内容
            vcf_content = []
            for contact in contacts:
                vcard = self.contact_to_vcard(contact)
                vcf_content.append(vcard)
            
            # 写入文件
            full_content = '\n\n'.join(vcf_content)
            with open(file_path, 'w', encoding=encoding) as f:
                f.write(full_content)
            
            return ""
            
        except PermissionError:
            return f"文件访问权限不足: {file_path}"
        except Exception as e:
            return f"导出VCF文件时发生错误: {str(e)}"
    
    def merge_vcf_files(self, file_paths: List[str], output_path: str, 
                       merge_duplicates: bool = True) -> Tuple[Dict[str, Any], str]:
        """
        合并多个VCF文件
        
        Args:
            file_paths: VCF文件路径列表
            output_path: 输出文件路径
            merge_duplicates: 是否合并重复联系人
            
        Returns:
            (统计信息, 错误信息)
        """
        if not file_paths:
            return {}, "没有指定要合并的文件"
        
        # 创建联系人管理器
        contact_manager = ContactManager()
        
        # 统计信息
        stats = {
            'input_files': len(file_paths),
            'total_contacts_imported': 0,
            'total_contacts_after_merge': 0,
            'duplicates_merged': 0,
            'duplicate_groups': 0,
            'failed_files': []
        }
        
        # 导入所有文件
        for file_path in file_paths:
            try:
                temp_manager, file_stats, error = self.import_from_vcf(file_path)
                if error:
                    stats['failed_files'].append({'file': file_path, 'error': error})
                    continue
                
                # 添加联系人到主管理器
                for contact in temp_manager.get_all_contacts():
                    contact_manager.add_contact(contact)
                
                stats['total_contacts_imported'] += file_stats['valid_contacts']
                
            except Exception as e:
                stats['failed_files'].append({'file': file_path, 'error': str(e)})
        
        # 合并重复联系人：与"合并重复"按钮使用同一套规则
        if merge_duplicates:
            merge_stats = self.merger.merge_duplicates_in_manager(contact_manager)
            stats['duplicates_merged'] = merge_stats['contacts_merged']
            stats['duplicate_groups'] = merge_stats['duplicate_groups_found']

        stats['total_contacts'] = stats['total_contacts_imported']
        stats['merged_contacts'] = contact_manager.count()
        stats['total_contacts_after_merge'] = contact_manager.count()
        
        # 导出合并后的文件
        export_error = self.export_to_vcf(contact_manager, output_path)
        if export_error:
            return stats, export_error
        
        return stats, ""
    
    def validate_vcf_file(self, file_path: str) -> Tuple[bool, List[str], str]:
        """
        验证VCF文件格式
        
        Args:
            file_path: VCF文件路径
            
        Returns:
            (是否有效, 问题列表, 错误信息)
        """
        content, error = self.read_vcf_file(file_path)
        if error:
            return False, [], error
        
        issues = []
        
        # 检查基本格式
        if 'BEGIN:VCARD' not in content:
            issues.append("文件不包含有效的vCard格式")
            return False, issues, ""
        
        if 'END:VCARD' not in content:
            issues.append("vCard格式不完整，缺少END:VCARD")
            return False, issues, ""
        
        # 解析并检查联系人
        contacts = self.parse_vcf_content(content)
        
        if not contacts:
            issues.append("文件中没有有效的联系人数据")
            return False, issues, ""
        
        # 检查数据质量
        empty_names = sum(1 for c in contacts if not c.name)
        if empty_names > 0:
            issues.append(f"发现{empty_names}个联系人没有姓名")
        
        no_contact_info = sum(1 for c in contacts if not c.phones and not c.emails)
        if no_contact_info > 0:
            issues.append(f"发现{no_contact_info}个联系人没有电话或邮箱信息")
        
        return True, issues, ""
    
    def import_to_manager(self, file_path: str, contact_manager: ContactManager, 
                         encoding: Optional[str] = None) -> int:
        """
        直接导入VCF文件到联系人管理器
        
        Args:
            file_path: VCF文件路径
            contact_manager: 联系人管理器实例
            encoding: 指定编码，None表示自动检测
            
        Returns:
            导入的联系人数量
            
        Raises:
            Exception: 导入失败时抛出异常
        """
        try:
            # 使用现有的import_from_vcf方法
            imported_manager, stats, error = self.import_from_vcf(file_path, encoding)
            
            if error:
                raise Exception(error)
            
            # 将导入的联系人添加到目标管理器
            imported_count = 0
            for contact in imported_manager.get_all_contacts():
                contact_manager.add_contact(contact)
                imported_count += 1
            
            return imported_count
            
        except Exception as e:
            raise Exception(f"导入VCF文件失败: {str(e)}")

    def export_contacts(self, contacts: List[Contact], file_path: str, encoding: str = 'utf-8') -> str:
        """
        导出联系人列表到VCF文件
        
        Args:
            contacts: 联系人列表
            file_path: 输出文件路径
            encoding: 文件编码
            
        Returns:
            错误信息，空字符串表示成功
        """
        try:
            # 创建临时的ContactManager
            temp_manager = ContactManager()
            for contact in contacts:
                temp_manager.add_contact(contact)
            
            # 使用现有的export_to_vcf方法
            return self.export_to_vcf(temp_manager, file_path, encoding)
            
        except Exception as e:
            return f"导出VCF文件失败: {str(e)}"

    def get_vcf_statistics(self, file_path: str) -> Tuple[Dict[str, Any], str]:
        """
        获取VCF文件统计信息
        
        Args:
            file_path: VCF文件路径
            
        Returns:
            (        统计信息, 错误信息)
        """
        content, error = self.read_vcf_file(file_path)
        if error:
            return {}, error
        
        contacts = self.parse_vcf_content(content)
        
        stats = {
            'total_contacts': len(contacts),
            'contacts_with_name': sum(1 for c in contacts if c.name),
            'contacts_with_phone': sum(1 for c in contacts if c.phones),
            'contacts_with_email': sum(1 for c in contacts if c.emails),
            'contacts_with_organization': sum(1 for c in contacts if c.organization),
            'contacts_with_photo': sum(1 for c in contacts if c.photos),
            'contacts_with_lunar_birthday': sum(1 for c in contacts if c.lunar_birthday),
            'total_phone_numbers': sum(len(c.phones) for c in contacts),
            'total_email_addresses': sum(len(c.emails) for c in contacts),
            'file_encoding': self.detect_encoding(file_path)
        }
        
        return stats, ""
