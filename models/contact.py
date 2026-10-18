# -*- coding: utf-8 -*-
"""
联系人数据模型

字段设计对齐 vCard 3.0 与《通讯录处理软件设计文档》中约定的字段集合：
基本信息（姓名/昵称）、通信信息（电话/邮箱/网站）、工作信息（公司/职位/地址）、
个人信息（公历生日/农历生日/照片/地址）、其他信息（备注/分类）。

合并约定（对应需求中的两条合并规则）：
1. 信息较少的联系人并入信息较多的联系人（以信息量评分确定合并主体），
   并通过"列表字段取并集 + 单值字段冲突留痕到备注"保证不丢失任何一方已有的信息。
2. 姓名基础部分相同（如"张三（老师）"与"张三"）时，合并后取最简洁姓名，
   括号内的注解（如"免师"）移入备注字段。
"""

import re
import unicodedata
from typing import Any, Dict, List, Optional, Sequence, Tuple
from datetime import datetime

from config.settings import MERGE_NOTE_SEPARATOR
from utils.field_utils import normalize_date_text, to_text

# 姓名中的括号注解：全角/半角圆括号、方括号（如 张三（老师）、张三(免师)、张三【免师】）
NAME_ANNOTATION_PATTERN = re.compile(r'[（(【\[][^（()）【】\[\]]{1,60}[）)】\]]')

# 已经规范成 YYYY-MM-DD 的日期文本（单值日期字段优选此类值）
DATE_LIKE_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2}$')

# 单值字段：(属性名, 中文标签)，两者冲突时保留更完整的一方、另一方记入备注
SINGLE_VALUE_FIELDS: Tuple[Tuple[str, str], ...] = (
    ('organization', '公司'),
    ('title', '职位'),
    ('address', '地址'),
    ('birthday', '生日'),
    ('lunar_birthday', '农历生日'),
    ('website', '网站'),
)


class Contact:
    """联系人数据模型"""

    def __init__(self):
        """初始化联系人对象"""
        self.name: str = ""                     # 姓名
        self.nicknames: List[str] = []          # 昵称/别名列表
        self.phones: List[str] = []             # 电话号码列表
        self.emails: List[str] = []             # 邮箱列表
        self.organization: str = ""             # 组织/公司
        self.title: str = ""                    # 职位
        self.address: str = ""                  # 地址
        self.birthday: str = ""                 # 公历生日
        self.lunar_birthday: str = ""           # 农历生日
        self.photos: List[str] = []             # 照片列表（data URI 或 http(s) URI）
        self.website: str = ""                  # 网站/主页
        self.notes: str = ""                    # 备注
        self.categories: List[str] = []         # 分类标签
        self.extra_vcard_fields: List[str] = [] # 未建模的 vCard 属性行（原样保留，如 IMPP/X-PHONETIC-*）
        self.created_at: datetime = datetime.now()  # 创建时间
        self.updated_at: datetime = datetime.now()  # 更新时间
        self.raw_vcard: str = ""                # 原始vCard数据（仅作追溯用）

    # ------------------------------------------------------------------
    # 增删改
    # ------------------------------------------------------------------

    def add_phone(self, phone: str) -> None:
        """添加电话号码"""
        if phone and phone.strip():
            normalized_phone = self.normalize_phone(phone.strip())
            if normalized_phone:
                # 检查标准化后的号码是否已存在（考虑现有号码也需要标准化比较）
                existing_normalized = [self.normalize_phone(p) for p in self.phones]
                if normalized_phone not in existing_normalized:
                    self.phones.append(normalized_phone)
                    self.updated_at = datetime.now()

    def add_email(self, email: str) -> None:
        """添加邮箱地址"""
        if email and email.strip():
            email = email.strip().lower()
            if self.is_valid_email(email) and email not in self.emails:
                self.emails.append(email)
                self.updated_at = datetime.now()

    def add_category(self, category: str) -> None:
        """添加分类标签"""
        if category and category.strip():
            category = category.strip()
            if category not in self.categories:
                self.categories.append(category)
                self.updated_at = datetime.now()

    def add_nickname(self, nickname: str) -> None:
        """
        添加昵称/别名（自动去重）。

        Args:
            nickname: 昵称文本，可用中英文逗号/分号分隔多个
        """
        for part in self._split_multi_value(nickname):
            if part not in self.nicknames:
                self.nicknames.append(part)
                self.updated_at = datetime.now()

    def add_photo(self, photo: str) -> None:
        """
        添加照片（应为 data URI 或 http(s) URI，由 field_utils 规范化后传入）。

        Args:
            photo: 照片值
        """
        if photo and photo.strip():
            photo = photo.strip()
            if photo not in self.photos:
                self.photos.append(photo)
                self.updated_at = datetime.now()

    def add_extra_vcard_field(self, line: str) -> None:
        """
        记录未建模的 vCard 属性行，导出时原样保留，避免信息丢失。

        Args:
            line: 已展开（去折行）的属性行
        """
        if line and line.strip():
            line = line.strip()
            if line not in self.extra_vcard_fields:
                self.extra_vcard_fields.append(line)

    @staticmethod
    def normalize_name(name: str) -> str:
        """
        清理姓名：删除姓名中的所有空白字符（含全角空格、制表符等）。

        仅做空白字符清理，不做大小写或全半角转换，以保证姓名原貌不被破坏。
        比较级别的归一化（大小写、全半角）由合并器负责。

        Args:
            name: 原始姓名

        Returns:
            去除所有空白字符后的姓名
        """
        if not name:
            return ""
        # re 的 \s 在 str 模式下匹配所有 Unicode 空白（含 U+3000 全角空格）
        return re.sub(r'\s+', '', name)

    def set_name(self, name: str) -> None:
        """设置姓名（自动删除姓名中的空格）"""
        cleaned = self.normalize_name(name)
        if cleaned:
            self.name = cleaned
            self.updated_at = datetime.now()

    def set_organization(self, organization: str) -> None:
        """设置组织/公司"""
        if organization and organization.strip():
            self.organization = organization.strip()
            self.updated_at = datetime.now()

    def set_title(self, title: str) -> None:
        """设置职位"""
        if title and title.strip():
            self.title = title.strip()
            self.updated_at = datetime.now()

    def set_address(self, address: str) -> None:
        """设置地址"""
        if address and address.strip():
            self.address = address.strip()
            self.updated_at = datetime.now()

    def set_birthday(self, birthday: str) -> None:
        """设置公历生日（自动把常见日期写法归一为 YYYY-MM-DD）"""
        cleaned = normalize_date_text(birthday)
        if cleaned:
            self.birthday = cleaned
            self.updated_at = datetime.now()

    def set_lunar_birthday(self, lunar_birthday: str) -> None:
        """设置农历生日（自动归一日期写法，无法解析的农历文本原样保留）"""
        cleaned = normalize_date_text(lunar_birthday)
        if cleaned:
            self.lunar_birthday = cleaned
            self.updated_at = datetime.now()

    def set_website(self, website: str) -> None:
        """设置网站/主页"""
        cleaned = to_text(website)
        if cleaned:
            self.website = cleaned
            self.updated_at = datetime.now()

    def set_raw_vcard(self, raw_vcard: str) -> None:
        """设置原始vCard数据"""
        if raw_vcard and raw_vcard.strip():
            self.raw_vcard = raw_vcard.strip()
            self.updated_at = datetime.now()

    def set_notes(self, notes: str) -> None:
        """设置备注"""
        if notes and notes.strip():
            self.notes = notes.strip()
            self.updated_at = datetime.now()

    def append_notes(self, extra: str) -> None:
        """
        追加备注（去重，不覆盖已有内容）。

        Args:
            extra: 追加的备注片段
        """
        fragment = to_text(extra)
        if not fragment:
            return
        parts = [p for p in re.split(r'[;；]', self.notes) if p.strip()]
        if fragment in [p.strip() for p in parts]:
            return
        self.notes = Contact._join_notes([self.notes, fragment])

    @staticmethod
    def _split_multi_value(value: str) -> List[str]:
        """
        把多值文本拆成列表（支持中英文逗号、分号、竖线）。

        Args:
            value: 多值文本

        Returns:
            去空后的值列表
        """
        text = to_text(value)
        if not text:
            return []
        parts = re.split(r'[;,；，|、]', text)
        return [p.strip() for p in parts if p and p.strip()]

    def cleanup_duplicate_phones(self) -> int:
        """清理重复的电话号码"""
        if not self.phones:
            return 0

        original_count = len(self.phones)
        normalized_phones = []
        seen_normalized = set()

        for phone in self.phones:
            normalized = self.normalize_phone(phone)
            if normalized and normalized not in seen_normalized:
                normalized_phones.append(normalized)
                seen_normalized.add(normalized)

        self.phones = normalized_phones
        removed_count = original_count - len(self.phones)

        if removed_count > 0:
            self.updated_at = datetime.now()

        return removed_count

    @staticmethod
    def normalize_phone(phone: str) -> str:
        """标准化电话号码格式"""
        if not phone:
            return ""

        # 移除所有非数字字符，保留+号
        phone = re.sub(r'[^\d+]', '', phone)

        # 如果以+86开头，移除+86
        if phone.startswith('+86'):
            phone = phone[3:]
        elif phone.startswith('86') and len(phone) > 11:
            phone = phone[2:]

        # 移除开头的0
        phone = phone.lstrip('0')

        return phone

    @staticmethod
    def is_valid_email(email: str) -> bool:
        """验证邮箱地址格式"""
        if not email:
            return False

        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return bool(re.match(pattern, email))

    def is_empty(self) -> bool:
        """检查联系人是否为空（任一字段有内容即视为非空）"""
        return not any((
            self.name, self.nicknames, self.phones, self.emails,
            self.organization, self.title, self.address, self.birthday,
            self.lunar_birthday, self.photos, self.website, self.notes,
            self.categories,
        ))

    def has_phone(self, phone: str) -> bool:
        """检查是否包含指定电话号码"""
        normalized_phone = self.normalize_phone(phone)
        return normalized_phone in self.phones

    def has_email(self, email: str) -> bool:
        """检查是否包含指定邮箱"""
        return email.lower().strip() in self.emails

    def has_photo(self) -> bool:
        """是否包含照片"""
        return bool(self.photos)

    def get_primary_phone(self) -> Optional[str]:
        """获取主要电话号码"""
        return self.phones[0] if self.phones else None

    def get_primary_email(self) -> Optional[str]:
        """获取主要邮箱地址"""
        return self.emails[0] if self.emails else None

    def get_primary_photo(self) -> Optional[str]:
        """获取主要照片"""
        return self.photos[0] if self.photos else None

    def get_nickname_text(self) -> str:
        """获取昵称文本（多个以逗号连接）"""
        return ', '.join(self.nicknames)

    # ------------------------------------------------------------------
    # 姓名拆分（合并规则二的基础）
    # ------------------------------------------------------------------

    @staticmethod
    def split_name_annotations(name: str) -> Tuple[str, List[str]]:
        """
        把姓名拆分为"基础姓名 + 括号注解"。

        例：
            '张三（老师）' -> ('张三', ['免师'])
            '张三'        -> ('张三', [])
            '（未知）'     -> ('（未知）', [])   # 整体被括号占据时不拆分，保留原名

        Args:
            name: 原始姓名

        Returns:
            (基础姓名, 括号注解列表)
        """
        cleaned = Contact.normalize_name(name)
        if not cleaned:
            return "", []

        annotations = []
        for match in NAME_ANNOTATION_PATTERN.finditer(cleaned):
            inner = match.group(0)[1:-1].strip()
            if inner:
                annotations.append(inner)

        base = NAME_ANNOTATION_PATTERN.sub('', cleaned).strip()
        if not base:
            # 拆完什么都不剩，说明括号内容是姓名主体，不做拆分
            return cleaned, []
        return base, annotations

    @staticmethod
    def name_compare_key(name: str) -> str:
        """
        姓名比较键：去空白 + NFKC 全半角归一 + 小写。

        Args:
            name: 姓名

        Returns:
            比较用键
        """
        return re.sub(r'\s+', '', unicodedata.normalize('NFKC', name or '').lower())

    @classmethod
    def choose_merged_name(cls, contacts: Sequence['Contact']) -> Tuple[str, List[str], List[str]]:
        """
        决定一组联系人合并后的姓名（合并规则二）。

        规则：
        1. 去掉括号注解后的"基础姓名"相同（如 '张三（老师）' 与 '张三'）时，
           取最简洁的写法作为合并后姓名，括号内注解统一移至备注；
        2. 基础姓名不同时，保留信息更完整的姓名（更长），其余姓名作为别名进入昵称字段，
           保证任何一方原有的姓名都不会丢失。

        Args:
            contacts: 待合并的联系人列表（已按信息量从多到少排序）

        Returns:
            (合并后姓名, 需要移入备注的括注列表, 需要移入昵称的其它姓名列表)
        """
        entries: List[Tuple[str, str, List[str]]] = []  # (展示用基础名, 比较键, 注解)
        for contact in contacts:
            if not contact.name:
                continue
            base, annotations = cls.split_name_annotations(contact.name)
            entries.append((base, cls.name_compare_key(base), annotations))

        if not entries:
            return "", [], []

        # 统计各基础名的出现情况：出现次数多的优先，其次优先"无注解"的简洁写法
        stats: Dict[str, Dict[str, Any]] = {}
        for order, (base, key, annotations) in enumerate(entries):
            item = stats.setdefault(key, {
                'count': 0, 'plain_count': 0, 'plain_display': '', 'display': base, 'order': order,
            })
            item['count'] += 1
            if not annotations:
                item['plain_count'] += 1
                if not item['plain_display']:
                    item['plain_display'] = base
            if not item['display']:
                item['display'] = base

        def rank(key: str):
            item = stats[key]
            display = item['plain_display'] or item['display']
            # 出现次数 → 简洁写法数 → 名称长度（更完整者胜出）→ 首次出现顺序
            return (-item['count'], -item['plain_count'], -len(display), item['order'])

        best_key = min(stats, key=rank)
        best_item = stats[best_key]
        merged_name = best_item['plain_display'] or best_item['display']

        # 注解：仅收集与合并后姓名同源的括注，避免与"其它姓名"重复记录
        annotations: List[str] = []
        other_names: List[str] = []
        for contact in contacts:
            if not contact.name:
                continue
            base, anns = cls.split_name_annotations(contact.name)
            if cls.name_compare_key(base) == best_key:
                for ann in anns:
                    if ann not in annotations:
                        annotations.append(ann)
            else:
                if contact.name not in other_names:
                    other_names.append(contact.name)

        # 完全相同姓名的重复项不算"其它姓名"
        other_names = [n for n in other_names if cls.name_compare_key(n) != best_key]
        return merged_name, annotations, other_names

    # ------------------------------------------------------------------
    # 信息量评分（合并规则一的基础）
    # ------------------------------------------------------------------

    def information_score(self) -> int:
        """
        计算联系人信息量评分，用于确定合并主体（信息少的并入信息多的）。

        Returns:
            综合评分（越大表示信息越丰富）
        """
        score = 0
        if self.name:
            score += 3
        score += len(self.phones) * 3
        score += len(self.emails) * 3
        score += len(self.nicknames) * 2
        score += len(self.photos) * 2
        score += len(self.categories)
        for value in (self.organization, self.title, self.address,
                      self.birthday, self.lunar_birthday, self.website):
            if value:
                score += 1
        if self.notes:
            score += 1
        if self.extra_vcard_fields:
            score += 1
        return score

    def content_length(self) -> int:
        """
        计算文本内容总长度，作为信息量评分相同时的次级比较依据。

        不计入照片数据（base64 会淹没其它字段的权重）。

        Returns:
            文本长度合计
        """
        total = len(self.name) + len(self.notes)
        for value in (self.organization, self.title, self.address,
                      self.birthday, self.lunar_birthday, self.website):
            total += len(value)
        for group in (self.phones, self.emails, self.nicknames, self.categories):
            total += sum(len(item) for item in group)
        return total

    # ------------------------------------------------------------------
    # 合并
    # ------------------------------------------------------------------

    @staticmethod
    def _prefer_richer_value(value1: str, value2: str) -> str:
        """
        在两个单值字段中择优：优先非空，都非空时保留更长的（信息更完整）。

        Args:
            value1: 字段值1
            value2: 字段值2

        Returns:
            择优后的字段值
        """
        if not value1:
            return value2 or ""
        if not value2:
            return value1
        return value1 if len(value1) >= len(value2) else value2

    @staticmethod
    def _join_notes(parts: Sequence[str]) -> str:
        """
        拼接备注片段（去空、去重、保持顺序）。

        Args:
            parts: 备注片段序列

        Returns:
            拼接后的备注
        """
        seen = set()
        result = []
        for part in parts:
            fragment = to_text(part)
            if not fragment:
                continue
            key = fragment.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(fragment)
        return MERGE_NOTE_SEPARATOR.join(result)

    @staticmethod
    def _resolve_single_field(values: Sequence[str], prefer_date: bool) -> Tuple[str, List[str]]:
        """
        解析多个联系人同一单值字段的取值。

        取"信息更完整"的一方作为保留值（输入已按信息量降序），其余不同的取值
        作为冲突项返回，由调用方写入备注，确保信息不丢失。

        Args:
            values: 按信息量降序排列的各联系人字段值
            prefer_date: 是否优先生效日期形态的值（生日字段）

        Returns:
            (保留值, 冲突值列表)
        """
        ordered: List[str] = []
        for raw in values:
            value = to_text(raw)
            if value and value not in ordered:
                ordered.append(value)

        if not ordered:
            return "", []

        kept = ordered[0]
        if prefer_date:
            date_like = [value for value in ordered if DATE_LIKE_PATTERN.match(value)]
            if date_like:
                kept = date_like[0]

        conflicts = [value for value in ordered if value != kept]
        return kept, conflicts

    @classmethod
    def merge_group(cls, contacts: Sequence['Contact']) -> 'Contact':
        """
        合并一组联系人（合并规则一 + 规则二的统一实现）。

        合并主体为信息量最多的联系人；所有列表字段取并集；单值字段冲突时保留
        更完整的一方并把另一方写入备注，从而不遗漏任何一方已有的信息。

        Args:
            contacts: 待合并的联系人列表

        Returns:
            合并后的新联系人对象
        """
        valid = [c for c in contacts if isinstance(c, Contact)]
        if not valid:
            return cls()
        if len(valid) == 1:
            return valid[0]

        # 信息多的排前面 → 合并主体是第一个（排序稳定，同分保持原有顺序）
        ordered = sorted(
            valid,
            key=lambda c: (c.information_score(), c.content_length()),
            reverse=True,
        )

        merged = cls()

        # ---- 姓名（含括注转移、其它姓名入昵称） ----
        merged_name, annotations, other_names = cls.choose_merged_name(ordered)
        merged.name = merged_name or ordered[0].name

        # ---- 列表字段：并集 ----
        merged.phones = cls._union_list(
            (c.phones for c in ordered), normalize=cls.normalize_phone
        )
        merged.emails = cls._union_list(
            (c.emails for c in ordered), normalize=lambda e: (e or '').strip().lower()
        )
        merged.nicknames = cls._union_list(
            [c.nicknames for c in ordered] + [other_names]
        )
        merged.photos = cls._union_list((c.photos for c in ordered))
        merged.categories = cls._union_list((c.categories for c in ordered))
        merged.extra_vcard_fields = cls._union_list((c.extra_vcard_fields for c in ordered))

        # ---- 单值字段：保留更完整者，冲突值记入备注 ----
        conflict_fragments: List[str] = []
        for attr, label in SINGLE_VALUE_FIELDS:
            kept, conflicts = cls._resolve_single_field(
                [getattr(c, attr) for c in ordered],
                prefer_date=attr in ('birthday', 'lunar_birthday'),
            )
            setattr(merged, attr, kept)
            for value in conflicts:
                conflict_fragments.append(f"{label}:{value}")

        # ---- 备注：原有备注 + 姓名括注 + 冲突留痕 ----
        merged.notes = cls._join_notes(
            [c.notes for c in ordered] + annotations + conflict_fragments
        )

        # ---- 原始 vCard 仅作追溯，取最完整的一份 ----
        raw_candidates = [c.raw_vcard for c in ordered if c.raw_vcard]
        if raw_candidates:
            merged.raw_vcard = max(raw_candidates, key=len)

        merged.created_at = min(c.created_at for c in ordered)
        merged.updated_at = datetime.now()

        return merged

    @staticmethod
    def _union_list(groups, normalize=None) -> List[str]:
        """
        合并多个列表字段为并集（按规范化结果去重，保留首次出现的原值）。

        Args:
            groups: 可迭代的多个值列表
            normalize: 可选的规范化函数，用于判定重复

        Returns:
            去重后的并集列表
        """
        result: List[str] = []
        seen = set()
        for group in groups:
            for item in group or ():
                value = to_text(item)
                if not value:
                    continue
                key = normalize(value) if normalize else value
                if not key or key in seen:
                    continue
                seen.add(key)
                result.append(value)
        return result

    def merge_with(self, other: 'Contact') -> 'Contact':
        """
        与另一个联系人合并（等价于两人成组的合并，遵循统一合并规则）。

        Args:
            other: 另一个联系人

        Returns:
            合并后的新联系人；other 非法时返回自身
        """
        if not isinstance(other, Contact):
            return self
        return Contact.merge_group([self, other])

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            'name': self.name,
            'nicknames': self.nicknames,
            'phones': self.phones,
            'emails': self.emails,
            'organization': self.organization,
            'title': self.title,
            'address': self.address,
            'birthday': self.birthday,
            'lunar_birthday': self.lunar_birthday,
            'photos': self.photos,
            'website': self.website,
            'notes': self.notes,
            'categories': self.categories,
            'extra_vcard_fields': self.extra_vcard_fields,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
            'raw_vcard': self.raw_vcard
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Contact':
        """从字典创建联系人对象"""
        contact = cls()
        contact.name = data.get('name', '')
        contact.nicknames = list(data.get('nicknames', []) or [])
        contact.phones = list(data.get('phones', []) or [])
        contact.emails = list(data.get('emails', []) or [])
        contact.organization = data.get('organization', '')
        contact.title = data.get('title', '')
        contact.address = data.get('address', '')
        contact.birthday = data.get('birthday', '')
        contact.lunar_birthday = data.get('lunar_birthday', '')
        contact.photos = list(data.get('photos', []) or [])
        contact.website = data.get('website', '')
        contact.notes = data.get('notes', '')
        contact.categories = list(data.get('categories', []) or [])
        contact.extra_vcard_fields = list(data.get('extra_vcard_fields', []) or [])
        contact.raw_vcard = data.get('raw_vcard', '')

        # 处理时间戳
        if 'created_at' in data:
            try:
                contact.created_at = datetime.fromisoformat(data['created_at'])
            except (ValueError, TypeError):
                contact.created_at = datetime.now()

        if 'updated_at' in data:
            try:
                contact.updated_at = datetime.fromisoformat(data['updated_at'])
            except (ValueError, TypeError):
                contact.updated_at = datetime.now()

        return contact

    def __str__(self) -> str:
        """字符串表示"""
        parts = []
        if self.name:
            parts.append(f"姓名: {self.name}")
        if self.phones:
            parts.append(f"电话: {', '.join(self.phones)}")
        if self.emails:
            parts.append(f"邮箱: {', '.join(self.emails)}")
        if self.organization:
            parts.append(f"公司: {self.organization}")

        return " | ".join(parts) if parts else "空联系人"

    def __repr__(self) -> str:
        """对象表示"""
        return f"Contact(name='{self.name}', phones={len(self.phones)}, emails={len(self.emails)})"

    def __eq__(self, other) -> bool:
        """相等比较"""
        if not isinstance(other, Contact):
            return False

        return (self.name == other.name and
                set(self.phones) == set(other.phones) and
                set(self.emails) == set(other.emails))

    def __hash__(self) -> int:
        """哈希值"""
        return hash((self.name, tuple(sorted(self.phones)), tuple(sorted(self.emails))))


class ContactManager:
    """联系人管理器"""

    def __init__(self):
        """初始化联系人管理器"""
        self.contacts: List[Contact] = []

    def add_contact(self, contact: Contact) -> None:
        """添加联系人"""
        if contact and not contact.is_empty():
            self.contacts.append(contact)

    def remove_contact(self, contact: Contact) -> bool:
        """移除联系人"""
        try:
            self.contacts.remove(contact)
            return True
        except ValueError:
            return False

    def find_by_phone(self, phone: str) -> List[Contact]:
        """根据电话号码查找联系人"""
        normalized_phone = Contact.normalize_phone(phone)
        return [contact for contact in self.contacts if contact.has_phone(normalized_phone)]

    def find_by_email(self, email: str) -> List[Contact]:
        """根据邮箱查找联系人"""
        return [contact for contact in self.contacts if contact.has_email(email)]

    def find_by_name(self, name: str) -> List[Contact]:
        """根据姓名查找联系人"""
        name = name.lower().strip()
        return [contact for contact in self.contacts if name in contact.name.lower()]

    def get_all_contacts(self) -> List[Contact]:
        """获取所有联系人"""
        return self.contacts.copy()

    def clear(self) -> None:
        """清空所有联系人"""
        self.contacts.clear()

    def count(self) -> int:
        """获取联系人数量"""
        return len(self.contacts)

    def find_duplicates_by_phone(self) -> Dict[str, List[Contact]]:
        """根据电话号码查找重复联系人"""
        phone_map = {}

        for contact in self.contacts:
            for phone in contact.phones:
                if phone not in phone_map:
                    phone_map[phone] = []
                phone_map[phone].append(contact)

        # 只返回有重复的电话号码
        return {phone: contacts for phone, contacts in phone_map.items() if len(contacts) > 1}

    def merge_duplicates_by_phone(self) -> int:
        """
        合并基于电话号码的重复联系人。

        每个号码相同的联系人组使用统一的合并规则（信息少者并入信息多者、
        姓名取简洁写法、括注移入备注），返回值与旧实现保持一致。

        Returns:
            被合并掉（减少的）联系人数量
        """
        duplicates = self.find_duplicates_by_phone()
        if not duplicates:
            return 0

        # 按规范化号码分组；同一联系人可能出现在多个号码组，用并查集保证只处理一次
        groups = self._build_phone_groups(duplicates)

        grouped_ids = set()
        merged_contacts: List[Contact] = []
        merged_count = 0
        for group in groups:
            for contact in group:
                grouped_ids.add(id(contact))
            merged_contacts.append(Contact.merge_group(group))
            merged_count += len(group) - 1

        kept_contacts = [c for c in self.contacts if id(c) not in grouped_ids]
        self.contacts = kept_contacts + merged_contacts
        return merged_count

    def _build_phone_groups(self, duplicates: Dict[str, List[Contact]]) -> List[List[Contact]]:
        """
        把"号码 → 联系人"的重复映射整理为互不相交的联系人组（并查集传递合并）。

        Args:
            duplicates: find_duplicates_by_phone 的结果

        Returns:
            重复联系人组列表
        """
        all_contacts = []
        index = {}
        for contacts in duplicates.values():
            for contact in contacts:
                if id(contact) not in index:
                    index[id(contact)] = len(all_contacts)
                    all_contacts.append(contact)

        parent = list(range(len(all_contacts)))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x: int, y: int) -> None:
            px, py = find(x), find(y)
            if px != py:
                parent[px] = py

        for contacts in duplicates.values():
            first = index[id(contacts[0])]
            for contact in contacts[1:]:
                union(first, index[id(contact)])

        groups: Dict[int, List[Contact]] = {}
        for position, contact in enumerate(all_contacts):
            groups.setdefault(find(position), []).append(contact)

        return [group for group in groups.values() if len(group) > 1]

    def cleanup_all_duplicate_phones(self) -> int:
        """清理所有联系人中的重复电话号码"""
        total_removed = 0
        for contact in self.contacts:
            removed_count = contact.cleanup_duplicate_phones()
            total_removed += removed_count
        return total_removed
