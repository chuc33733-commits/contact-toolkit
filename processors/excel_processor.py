# -*- coding: utf-8 -*-
"""
Excel文件处理器
支持Excel文件读取和转换为VCF格式

字段覆盖（对齐《通讯录处理软件设计文档》约定并与 vCard 属性一一对应）：
姓名 / 昵称 / 电话 / 邮箱 / 公司 / 职位 / 地址 / 生日 / 农历生日 / 照片 / 网站 / 备注 / 分类

照片列的三种写法都支持：
1. 本地文件路径（绝对路径，或相对 Excel 文件所在目录的相对路径）
2. http(s) 图片链接
3. 直接粘贴的 base64 文本 / data URI
"""

import base64
import os
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from models.contact import Contact, ContactManager
from config.settings import (
    EXCEL_COLUMN_MAPPING,
    SUPPORTED_EXCEL_FORMATS,
    EXCEL_CELL_MAX_CHARS,
)
from utils.field_utils import (
    decode_base64_payload,
    is_remote_uri,
    normalize_date_text,
    normalize_photo_value,
    parse_data_uri,
    split_photo_values,
    summarize_photo,
    to_text,
)


class ExcelProcessor:
    """Excel文件处理器"""

    def __init__(self):
        """初始化Excel处理器"""
        self.column_mapping = EXCEL_COLUMN_MAPPING
        self.supported_formats = SUPPORTED_EXCEL_FORMATS

    def is_supported_format(self, file_path: str) -> bool:
        """检查文件格式是否支持"""
        if not file_path:
            return False

        file_ext = os.path.splitext(file_path)[1].lower()
        return file_ext in self.supported_formats

    # ------------------------------------------------------------------
    # 列名识别
    # ------------------------------------------------------------------

    @staticmethod
    def normalize_header(header: Any) -> str:
        """
        归一化表头文本，用于列名匹配（去空白、全半角归一、小写）。

        Args:
            header: 原始表头

        Returns:
            归一化后的表头
        """
        text = to_text(header)
        if not text:
            return ""
        return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text).lower())

    def detect_column_mapping_candidates(self, df: pd.DataFrame) -> Dict[str, List[str]]:
        """
        自动检测Excel列标题映射（候选版：同一字段允许对应多个列）。

        匹配策略（避免"农历生日"被误判成"生日"这类歧义）：
        1. 精确匹配优先（同长度别名按字段声明顺序）；
        2. 其次按"最长别名包含"匹配（如"手机号码"→手机，"公司名称"→公司）；
        3. 一个表头只映射一个字段；一个字段可以拥有多个表头
           （如"电话1"、"电话2"、"固话"都归电话字段，按优先级排序）。

        Args:
            df: 数据表

        Returns:
            {字段名: [原始列名, ...]}（每个字段的列按优先级从高到低排列）
        """
        normalized_headers = [self.normalize_header(col) for col in df.columns]

        # 收集候选：(是否精确匹配, 别名长度, 字段声明顺序, 字段名, 表头下标)
        candidates: List[Tuple[int, int, int, str, int]] = []
        for index, header in enumerate(normalized_headers):
            if not header:
                continue
            for order, (field, possible_names) in enumerate(self.column_mapping.items()):
                for name in possible_names:
                    alias = self.normalize_header(name)
                    if not alias:
                        continue
                    if header == alias:
                        candidates.append((1, len(alias), order, field, index))
                    elif alias in header:
                        candidates.append((0, len(alias), order, field, index))

        # 精确匹配 > 更长的别名 > 字段声明顺序靠前；同分时取靠前的表头
        candidates.sort(key=lambda item: (-item[0], -item[1], item[2], item[4]))

        detected: Dict[str, List[str]] = {}
        used_headers = set()
        for _exact, _alias_len, _order, field, index in candidates:
            if index in used_headers:
                continue
            detected.setdefault(field, []).append(df.columns[index])
            used_headers.add(index)

        return detected

    def detect_column_mapping(self, df: pd.DataFrame) -> Dict[str, str]:
        """
        自动检测Excel列标题映射（兼容接口：每个字段只取优先级最高的一列）。

        Args:
            df: 数据表

        Returns:
            {字段名: 原始列名}
        """
        return {
            field: columns[0]
            for field, columns in self.detect_column_mapping_candidates(df).items()
        }

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------

    def read_excel_file(self, file_path: str, sheet_name: Optional[str] = None) -> Tuple[pd.DataFrame, str]:
        """
        读取Excel文件
        
        Args:
            file_path: Excel文件路径
            sheet_name: 工作表名称，None表示读取第一个工作表
            
        Returns:
            (DataFrame, 错误信息)
        """
        try:
            if not os.path.exists(file_path):
                return None, f"文件不存在: {file_path}"
            
            if not self.is_supported_format(file_path):
                return None, f"不支持的文件格式: {os.path.splitext(file_path)[1]}"
            
            # 读取Excel文件
            if sheet_name:
                df = pd.read_excel(file_path, sheet_name=sheet_name)
            else:
                df = pd.read_excel(file_path)
            
            if df.empty:
                return None, "Excel文件为空"
            
            return df, ""
            
        except FileNotFoundError:
            return None, f"文件未找到: {file_path}"
        except PermissionError:
            return None, f"文件访问权限不足: {file_path}"
        except pd.errors.EmptyDataError:
            return None, "Excel文件数据为空"
        except Exception as e:
            return None, f"读取Excel文件时发生错误: {str(e)}"
    
    def get_sheet_names(self, file_path: str) -> Tuple[List[str], str]:
        """
        获取Excel文件中的所有工作表名称
        
        Args:
            file_path: Excel文件路径
            
        Returns:
            (工作表名称列表, 错误信息)
        """
        try:
            if not os.path.exists(file_path):
                return [], f"文件不存在: {file_path}"
            
            if not self.is_supported_format(file_path):
                return [], f"不支持的文件格式: {os.path.splitext(file_path)[1]}"
            
            # 获取所有工作表名称
            excel_file = pd.ExcelFile(file_path)
            return excel_file.sheet_names, ""
            
        except Exception as e:
            return [], f"获取工作表名称时发生错误: {str(e)}"
    
    def preview_data(self, file_path: str, sheet_name: Optional[str] = None, max_rows: int = 10) -> Tuple[pd.DataFrame, Dict[str, str], str]:
        """
        预览Excel数据和列映射
        
        Args:
            file_path: Excel文件路径
            sheet_name: 工作表名称
            max_rows: 预览的最大行数
            
        Returns:
            (预览数据, 列映射, 错误信息)
        """
        df, error = self.read_excel_file(file_path, sheet_name)
        if error:
            return None, {}, error
        
        # 检测列映射
        column_mapping = self.detect_column_mapping(df)
        
        # 返回预览数据
        preview_df = df.head(max_rows)
        
        return preview_df, column_mapping, ""

    # ------------------------------------------------------------------
    # 行 → 联系人
    # ------------------------------------------------------------------

    def _cell_values(self, row: pd.Series, column_mapping: Dict[str, Any], field: str) -> List[str]:
        """
        读取字段对应单元格文本（支持一个字段映射到多列）。

        Args:
            row: 数据行
            column_mapping: 列映射（值可以是列名字符串，也可以是列名列表）
            field: 字段名

        Returns:
            单元格文本列表（按列顺序，已去空）
        """
        columns = column_mapping.get(field)
        if not columns:
            return []
        if isinstance(columns, str):
            columns = [columns]
        values: List[str] = []
        for column in columns:
            if column in row:
                text = to_text(row[column])
                if text:
                    values.append(text)
        return values

    def _cell(self, row: pd.Series, column_mapping: Dict[str, Any], field: str) -> str:
        """
        安全读取单元格文本（单值字段：取该字段的首选列）。

        Args:
            row: 数据行
            column_mapping: 列映射
            field: 字段名

        Returns:
            单元格文本，不存在或为空时返回空字符串
        """
        values = self._cell_values(row, column_mapping, field)
        return values[0] if values else ""

    def _multi_values(self, row: pd.Series, column_mapping: Dict[str, Any], field: str) -> List[str]:
        """
        读取字段对应的所有列，并按多值分隔符拆分为列表（电话/邮箱/昵称/分类等列表字段用）。

        Args:
            row: 数据行
            column_mapping: 列映射（值可以是列名字符串或列名列表）
            field: 字段名

        Returns:
            拆分后的值列表（按列顺序合并，已去空）
        """
        values: List[str] = []
        for text in self._cell_values(row, column_mapping, field):
            values.extend(Contact._split_multi_value(text))
        return values

    def convert_row_to_contact(self, row: pd.Series, column_mapping: Dict[str, Any],
                              base_dir: Optional[str] = None,
                              warnings: Optional[List[str]] = None,
                              invalid_emails: Optional[List[str]] = None) -> Contact:
        """
        将Excel行数据转换为Contact对象
        
        Args:
            row: pandas Series对象，表示一行数据
            column_mapping: 列映射字典（值可以是列名字符串，也可以是列名列表——
                列表字段如电话/昵称/邮箱/分类会把所有列的内容合并导入）
            base_dir: 照片相对路径的基准目录（一般为 Excel 文件所在目录）
            warnings: 可选的告警收集列表（照片读取失败等非致命问题会追加到这里）
            invalid_emails: 可选的收集列表，用于汇总"邮箱列内容不是合法邮箱"的值，
                避免这类内容被静默丢弃（例如把短号/工号填进了邮箱列）
            
        Returns:
            Contact对象
        """
        contact = Contact()

        # 姓名
        name = self._cell(row, column_mapping, 'name')
        if name:
            contact.set_name(name)

        # 昵称（支持多列、一个单元格内多个昵称）
        for nickname in self._multi_values(row, column_mapping, 'nickname'):
            contact.add_nickname(nickname)

        # 电话号码（支持多列、一个单元格内多个号码）
        for phone in self._multi_values(row, column_mapping, 'phone'):
            contact.add_phone(phone)

        # 邮箱：非法内容不再静默丢弃，而是收集起来提示用户（支持多列）
        for email in self._multi_values(row, column_mapping, 'email'):
            before = len(contact.emails)
            contact.add_email(email)
            if len(contact.emails) == before and invalid_emails is not None:
                invalid_emails.append(email)

        # 公司
        company = self._cell(row, column_mapping, 'company')
        if company:
            contact.set_organization(company)

        # 职位
        title = self._cell(row, column_mapping, 'title')
        if title:
            contact.set_title(title)

        # 地址
        address = self._cell(row, column_mapping, 'address')
        if address:
            contact.set_address(address)

        # 公历生日（Excel 日期对象会被归一为 YYYY-MM-DD）
        birthday = self._cell(row, column_mapping, 'birthday')
        if birthday:
            contact.set_birthday(birthday)

        # 农历生日
        lunar_birthday = self._cell(row, column_mapping, 'lunar_birthday')
        if lunar_birthday:
            contact.set_lunar_birthday(lunar_birthday)

        # 照片（本地路径 / 链接 / base64 皆可；同一单元格可含多张，导出时以 ", " 连接）
        raw_photo = self._cell(row, column_mapping, 'photo')
        for value in split_photo_values(raw_photo, base_dir=base_dir):
            photo, error = normalize_photo_value(value, base_dir=base_dir)
            if photo:
                contact.add_photo(photo)
            elif error and warnings is not None:
                warnings.append(f"{contact.name or '未命名'}: {error}")

        # 网站
        website = self._cell(row, column_mapping, 'website')
        if website:
            contact.set_website(website)

        # 备注
        notes = self._cell(row, column_mapping, 'notes')
        if notes:
            contact.set_notes(notes)

        # 分类标签（支持多列）
        for category in self._multi_values(row, column_mapping, 'categories'):
            contact.add_category(category)

        return contact

    def import_from_excel(self, file_path: str, sheet_name: Optional[str] = None, 
                         custom_mapping: Optional[Dict[str, str]] = None) -> Tuple[ContactManager, Dict[str, Any], str]:
        """
        从Excel文件导入联系人
        
        Args:
            file_path: Excel文件路径
            sheet_name: 工作表名称
            custom_mapping: 自定义列映射
            
        Returns:
            (ContactManager对象, 统计信息, 错误信息)
        """
        # 读取Excel文件
        df, error = self.read_excel_file(file_path, sheet_name)
        if error:
            return None, {}, error
        
        # 确定列映射：默认自动检测（同一字段可占多列，如"电话1/电话2"都会导入）
        if custom_mapping:
            column_mapping = custom_mapping
        else:
            column_mapping = self.detect_column_mapping_candidates(df)
        
        if not column_mapping:
            return None, {}, "无法识别Excel文件的列结构，请检查列标题是否正确"
        
        # 创建联系人管理器
        contact_manager = ContactManager()
        base_dir = os.path.dirname(os.path.abspath(file_path))
        warnings: List[str] = []
        invalid_emails: List[str] = []
        
        # 展示用的单列映射（每个字段取首选列）
        shown_mapping = {
            field: (columns[0] if isinstance(columns, list) else columns)
            for field, columns in column_mapping.items()
        }
        
        # 统计信息
        stats = {
            'total_rows': len(df),
            'processed_rows': 0,
            'valid_contacts': 0,
            'empty_contacts': 0,
            'error_rows': 0,
            'photos_imported': 0,
            'invalid_emails': 0,
            'warnings': warnings,
            'column_mapping': shown_mapping
        }
        
        # 逐行处理数据
        for index, row in df.iterrows():
            try:
                stats['processed_rows'] += 1
                
                # 转换为Contact对象
                contact = self.convert_row_to_contact(
                    row, column_mapping, base_dir=base_dir,
                    warnings=warnings, invalid_emails=invalid_emails
                )
                
                if contact.is_empty():
                    stats['empty_contacts'] += 1
                else:
                    contact_manager.add_contact(contact)
                    stats['valid_contacts'] += 1
                    stats['photos_imported'] += len(contact.photos)
                    
            except Exception as e:
                stats['error_rows'] += 1
                warnings.append(f"第{index + 2}行处理失败: {str(e)}")

        # 被跳过的非法邮箱内容单独提示，避免"静默丢数据"
        stats['invalid_emails'] = len(invalid_emails)
        if invalid_emails:
            samples = list(dict.fromkeys(invalid_emails))[:5]
            warnings.append(
                f"邮箱列有 {len(invalid_emails)} 个值不是合法邮箱（例: "
                f"{'、'.join(samples)}），已跳过；若该列实为短号/工号，请改用电话列或备注列"
            )
        
        return contact_manager, stats, ""
    
    def export_to_excel(self, contact_manager: ContactManager, file_path: str) -> str:
        """
        将联系人导出为Excel文件
        
        Args:
            contact_manager: 联系人管理器
            file_path: 输出文件路径
            
        Returns:
            错误信息，空字符串表示成功
        """
        try:
            contacts = contact_manager.get_all_contacts()
            if not contacts:
                return "没有联系人数据可导出"

            # 照片：base64 数据会远超 Excel 单元格上限，落地为同名图片文件夹并写入相对路径，
            # 这样导出的 Excel 仍可被本工具无损重新导入（链接类照片直接写链接）。
            photo_dir = self._prepare_photo_dir(contacts, file_path)

            data = []
            for index, contact in enumerate(contacts):
                row = {
                    '姓名': contact.name,
                    '昵称': ', '.join(contact.nicknames),
                    '电话': ', '.join(contact.phones),
                    '邮箱': ', '.join(contact.emails),
                    '公司': contact.organization,
                    '职位': contact.title,
                    '地址': contact.address,
                    '生日': contact.birthday,
                    '农历生日': contact.lunar_birthday,
                    '照片': self._photo_cell_value(contact, index, photo_dir, file_path),
                    '网站': contact.website,
                    '备注': contact.notes,
                    '分类': ', '.join(contact.categories)
                }
                data.append(row)
            
            # 创建DataFrame
            df = pd.DataFrame(data)
            
            # 导出到Excel
            df.to_excel(file_path, index=False, engine='openpyxl')
            
            return ""
            
        except PermissionError:
            return f"文件访问权限不足: {file_path}"
        except Exception as e:
            return f"导出Excel文件时发生错误: {str(e)}"

    # ------------------------------------------------------------------
    # 照片导出辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _prepare_photo_dir(contacts: List[Contact], file_path: str) -> Optional[str]:
        """
        判断是否需要为内嵌照片创建同级图片目录。

        Args:
            contacts: 待导出联系人
            file_path: 导出的 Excel 路径

        Returns:
            需要时返回图片目录路径，否则返回 None
        """
        need_dir = any(
            photo and not is_remote_uri(photo)
            for contact in contacts for photo in contact.photos
        )
        if not need_dir:
            return None
        directory = os.path.dirname(os.path.abspath(file_path))
        return os.path.join(directory, f"{os.path.splitext(os.path.basename(file_path))[0]}_photos")

    def _photo_cell_value(self, contact: Contact, index: int,
                          photo_dir: Optional[str], file_path: str) -> str:
        """
        生成"照片"列的值。

        内嵌照片会写为"Excel 同级图片目录/文件名"的相对路径（可被重新导入），
        外部链接原样保留。

        Args:
            contact: 联系人
            index: 联系人在导出列表中的下标（用于生成唯一文件名）
            photo_dir: 图片目录，None 表示没有内嵌照片
            file_path: 导出的 Excel 路径

        Returns:
            单元格文本
        """
        if not contact.photos:
            return ""

        excel_dir = os.path.dirname(os.path.abspath(file_path))
        values = []
        for position, photo in enumerate(contact.photos):
            if is_remote_uri(photo):
                values.append(photo)
                continue

            parsed = parse_data_uri(photo)
            if not parsed or not photo_dir:
                # 非规范化的照片值：能放得下就原样写出，否则给出说明
                values.append(photo if len(photo) <= EXCEL_CELL_MAX_CHARS else summarize_photo(photo))
                continue

            mime, payload = parsed
            extension = mime.split('/')[-1].replace('jpeg', 'jpg')
            safe_name = re.sub(r'[\\/:*?"<>|]', '_', contact.name or f"contact{index + 1}")
            filename = f"{index + 1:04d}_{safe_name}_{position + 1}.{extension}"
            try:
                raw = decode_base64_payload(payload)
                os.makedirs(photo_dir, exist_ok=True)
                with open(os.path.join(photo_dir, filename), 'wb') as fh:
                    fh.write(raw)
                values.append(os.path.relpath(os.path.join(photo_dir, filename), excel_dir).replace(os.sep, '/'))
            except Exception:
                values.append(summarize_photo(photo))

        return ', '.join(values)

    def import_to_manager(self, file_path: str, contact_manager: ContactManager,
                         sheet_name: Optional[str] = None,
                         custom_mapping: Optional[Dict[str, str]] = None) -> int:
        """
        直接导入Excel文件到联系人管理器
        
        Args:
            file_path: Excel文件路径
            contact_manager: 联系人管理器实例
            sheet_name: 工作表名称，None表示使用第一个工作表
            custom_mapping: 自定义列映射
            
        Returns:
            导入的联系人数量
            
        Raises:
            Exception: 导入失败时抛出异常
        """
        try:
            # 使用现有的import_from_excel方法
            imported_manager, stats, error = self.import_from_excel(file_path, sheet_name, custom_mapping)
            
            if error:
                raise Exception(error)
            
            # 将导入的联系人添加到目标管理器
            imported_count = 0
            for contact in imported_manager.get_all_contacts():
                contact_manager.add_contact(contact)
                imported_count += 1
            
            return imported_count
            
        except Exception as e:
            raise Exception(f"导入Excel文件失败: {str(e)}")

    def export_contacts(self, contacts: List[Contact], file_path: str) -> str:
        """
        导出联系人列表到Excel文件
        
        Args:
            contacts: 联系人列表
            file_path: 输出文件路径
            
        Returns:
            错误信息，空字符串表示成功
        """
        try:
            # 创建临时的ContactManager
            temp_manager = ContactManager()
            for contact in contacts:
                temp_manager.add_contact(contact)
            
            # 使用现有的export_to_excel方法
            return self.export_to_excel(temp_manager, file_path)
            
        except Exception as e:
            return f"导出Excel文件失败: {str(e)}"

    def validate_excel_structure(self, file_path: str, sheet_name: Optional[str] = None) -> Tuple[bool, List[str], str]:
        """
        验证Excel文件结构
        
        Args:
            file_path: Excel文件路径
            sheet_name: 工作表名称
            
        Returns:
            (是否有效, 建议列表, 错误信息)
        """
        df, error = self.read_excel_file(file_path, sheet_name)
        if error:
            return False, [], error
        
        suggestions = []
        column_mapping = self.detect_column_mapping(df)
        
        # 检查必要字段
        if 'name' not in column_mapping and 'phone' not in column_mapping:
            suggestions.append("建议至少包含姓名或电话号码列")
            return False, suggestions, ""
        
        # 检查数据质量
        if len(df) == 0:
            suggestions.append("Excel文件没有数据行")
            return False, suggestions, ""
        
        # 提供改进建议
        missing_fields = []
        for field in ['name', 'phone', 'email']:
            if field not in column_mapping:
                field_names = {
                    'name': '姓名',
                    'phone': '电话',
                    'email': '邮箱'
                }
                missing_fields.append(field_names[field])
        
        if missing_fields:
            suggestions.append(f"建议添加以下列: {', '.join(missing_fields)}")
        
        # 检查数据完整性
        if 'name' in column_mapping:
            empty_names = df[column_mapping['name']].isna().sum()
            if empty_names > 0:
                suggestions.append(f"发现{empty_names}行姓名为空")
        
        if 'phone' in column_mapping:
            empty_phones = df[column_mapping['phone']].isna().sum()
            if empty_phones > 0:
                suggestions.append(f"发现{empty_phones}行电话为空")
        
        return True, suggestions, ""
