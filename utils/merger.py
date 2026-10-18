# -*- coding: utf-8 -*-
"""
联系人合并算法工具
用于识别和合并重复联系人

优化要点（参考联系人去重的常用策略与最佳实践）：
1. 规范化优先：姓名/电话/邮箱/公司在比较前统一规范化；
   姓名删除所有空白字符（含全角空格），并做 NFKC 全半角归一 + 小写化用于比较。
2. 特征缓存：每个联系人的规范化特征只计算一次，避免在两两比较中重复正则运算。
3. 分块索引（blocking）：用电话后缀、邮箱、规范姓名、公司建立倒排索引生成候选对，
   只在候选对之间做精细比较，将 O(n^2) 全量两两比较降为近似 O(n)。
   分块键的设计与合并判定规则一一对应，保证结果与全量比较完全一致。
4. 并查集传递闭包：保持"间接重复也能归为一组"的传递分组语义。
"""

import re
import unicodedata
from typing import List, Dict, Set, Tuple, Optional
from difflib import SequenceMatcher
from models.contact import Contact, ContactManager
from config.settings import MERGE_SIMILARITY_THRESHOLD, PHONE_MATCH_THRESHOLD, CN_MOBILE_LENGTH


class _ContactFeatures:
    """联系人规范化特征（每次去重流程中一次性计算并复用）"""

    __slots__ = ('norm_name', 'base_name', 'phone_keys', 'mobiles', 'norm_emails', 'norm_org')

    def __init__(self):
        self.norm_name: str = ""            # 规范化姓名（去空格 + NFKC + 小写）
        self.base_name: str = ""            # 去掉括号注解后的规范化姓名（"张三（老师）" → "张三"）
        self.phone_keys: Set[str] = set()   # 电话分块键（后缀/全号）
        self.mobiles: Set[str] = set()      # 11 位手机号（用于"11位号码相同"的判定）
        self.norm_emails: Set[str] = set()  # 规范化邮箱集合
        self.norm_org: str = ""             # 规范化公司名（去空格 + 小写）


class ContactMerger:
    """联系人合并器"""

    def __init__(self, similarity_threshold: float = MERGE_SIMILARITY_THRESHOLD,
                 phone_match_threshold: int = PHONE_MATCH_THRESHOLD):
        """
        初始化合并器

        Args:
            similarity_threshold: 姓名相似度阈值
            phone_match_threshold: 电话号码匹配的最少位数
        """
        self.similarity_threshold = similarity_threshold
        self.phone_match_threshold = phone_match_threshold

    # ------------------------------------------------------------------
    # 规范化
    # ------------------------------------------------------------------

    def normalize_phone_for_comparison(self, phone: str) -> str:
        """
        标准化电话号码用于比较

        Args:
            phone: 原始电话号码

        Returns:
            标准化后的电话号码
        """
        if not phone:
            return ""

        # 移除所有非数字字符
        normalized = re.sub(r'[^\d]', '', phone)

        # 移除国家代码
        if normalized.startswith('86') and len(normalized) > 11:
            normalized = normalized[2:]

        # 移除前导0
        normalized = normalized.lstrip('0')

        return normalized

    def normalize_name_for_comparison(self, name: str) -> str:
        """
        标准化姓名用于比较：删除所有空白字符（含全角空格）、
        NFKC 全半角归一、转小写。

        Args:
            name: 原始姓名

        Returns:
            标准化后的姓名
        """
        if not name:
            return ""
        # NFKC 将全角字母/数字归为半角；\s 匹配含 U+3000 在内的所有 Unicode 空白
        return re.sub(r'\s+', '', unicodedata.normalize('NFKC', name).lower())

    def normalize_name_base(self, name: str) -> str:
        """
        取姓名的"基础部分"用于比较：先剥离括号注解（如"张三（老师）"→"张三"），
        再做与 normalize_name_for_comparison 相同的归一化。

        Args:
            name: 原始姓名

        Returns:
            规范化后的基础姓名
        """
        base, _annotations = Contact.split_name_annotations(name)
        return self.normalize_name_for_comparison(base)

    # ------------------------------------------------------------------
    # 特征提取（一次性计算，供分块与判定复用）
    # ------------------------------------------------------------------

    def _extract_features(self, contact: Contact) -> _ContactFeatures:
        """
        提取联系人的规范化特征。

        电话分块键规则（与 phones_match 判定逻辑严格等价）：
        - 号码长度 >= phone_match_threshold：取末尾 phone_match_threshold 位作为键；
        - 号码长度 < phone_match_threshold：以全号作为键（此时只有全等才能匹配）。

        Args:
            contact: 联系人对象

        Returns:
            规范化特征
        """
        features = _ContactFeatures()
        features.norm_name = self.normalize_name_for_comparison(contact.name)
        features.base_name = self.normalize_name_base(contact.name)

        for phone in contact.phones:
            normalized = self.normalize_phone_for_comparison(phone)
            if not normalized:
                continue
            if len(normalized) >= self.phone_match_threshold:
                features.phone_keys.add(normalized[-self.phone_match_threshold:])
            else:
                features.phone_keys.add(normalized)
            if len(normalized) == CN_MOBILE_LENGTH:
                features.mobiles.add(normalized)

        features.norm_emails = {
            email.lower().strip() for email in contact.emails if email and email.strip()
        }

        if contact.organization:
            features.norm_org = re.sub(r'\s+', '', contact.organization.lower())

        return features

    # ------------------------------------------------------------------
    # 成对判定
    # ------------------------------------------------------------------

    def phones_match(self, phone1: str, phone2: str) -> bool:
        """
        检查两个电话号码是否匹配

        Args:
            phone1: 电话号码1
            phone2: 电话号码2

        Returns:
            是否匹配
        """
        norm1 = self.normalize_phone_for_comparison(phone1)
        norm2 = self.normalize_phone_for_comparison(phone2)

        if not norm1 or not norm2:
            return False

        # 完全匹配
        if norm1 == norm2:
            return True

        # 检查后缀匹配（至少匹配指定位数）
        min_len = min(len(norm1), len(norm2))
        if min_len >= self.phone_match_threshold:
            # 检查后缀是否匹配
            if norm1[-self.phone_match_threshold:] == norm2[-self.phone_match_threshold:]:
                return True

        return False

    def calculate_name_similarity(self, name1: str, name2: str) -> float:
        """
        计算姓名相似度（自动忽略空格、大小写与全半角差异）

        Args:
            name1: 姓名1
            name2: 姓名2

        Returns:
            相似度分数 (0-1)
        """
        norm1 = self.normalize_name_for_comparison(name1)
        norm2 = self.normalize_name_for_comparison(name2)
        return self._normalized_name_similarity(norm1, norm2)

    def _normalized_name_similarity(self, norm1: str, norm2: str) -> float:
        """
        对已规范化的姓名计算相似度（内部复用，避免重复规范化）。

        Args:
            norm1: 规范化姓名1
            norm2: 规范化姓名2

        Returns:
            相似度分数 (0-1)
        """
        if not norm1 or not norm2:
            return 0.0

        if norm1 == norm2:
            return 1.0

        # 使用序列匹配器计算相似度
        similarity = SequenceMatcher(None, norm1, norm2).ratio()

        # 检查是否包含关系
        if norm1 in norm2 or norm2 in norm1:
            similarity = max(similarity, 0.8)

        return similarity

    def contacts_have_common_phone(self, contact1: Contact, contact2: Contact) -> bool:
        """
        检查两个联系人是否有共同的电话号码

        Args:
            contact1: 联系人1
            contact2: 联系人2

        Returns:
            是否有共同电话号码
        """
        for phone1 in contact1.phones:
            for phone2 in contact2.phones:
                if self.phones_match(phone1, phone2):
                    return True
        return False

    def contacts_have_common_email(self, contact1: Contact, contact2: Contact) -> bool:
        """
        检查两个联系人是否有共同的邮箱

        Args:
            contact1: 联系人1
            contact2: 联系人2

        Returns:
            是否有共同邮箱
        """
        emails1 = {email.lower().strip() for email in contact1.emails}
        emails2 = {email.lower().strip() for email in contact2.emails}
        return bool(emails1 & emails2)

    def _names_are_similar(self, f1: _ContactFeatures, f2: _ContactFeatures) -> bool:
        """
        判断两个联系人的姓名是否"相似"。

        除整名相似度外，还比较剥离括号注解后的基础姓名：
        "张三（老师）" 与 "张三" 的基础姓名相同，视为相似。

        Args:
            f1: 联系人1的规范化特征
            f2: 联系人2的规范化特征

        Returns:
            是否相似
        """
        if f1.base_name and f1.base_name == f2.base_name:
            return True
        if not f1.norm_name or not f2.norm_name:
            return False
        return self._normalized_name_similarity(f1.norm_name, f2.norm_name) >= self.similarity_threshold

    def _should_merge_features(self, f1: _ContactFeatures, f2: _ContactFeatures) -> Tuple[bool, str]:
        """
        基于预提取特征判断两个联系人是否应该合并（内部高性能路径）。

        Args:
            f1: 联系人1的规范化特征
            f2: 联系人2的规范化特征

        Returns:
            (是否应该合并, 合并原因)
        """
        # 检查电话号码匹配（分块键等价于后缀匹配规则）
        if f1.phone_keys & f2.phone_keys:
            # 需求中明确的场景：姓名相似（如"张三（老师）"与"张三"）且 11 位电话号码相同
            if (f1.mobiles & f2.mobiles) and self._names_are_similar(f1, f2):
                return True, "相似姓名且11位电话号码相同"
            return True, "相同电话号码"

        # 检查邮箱匹配
        if f1.norm_emails & f2.norm_emails:
            return True, "相同邮箱地址"

        # 检查姓名相似度
        if f1.norm_name and f2.norm_name:
            similarity = self._normalized_name_similarity(f1.norm_name, f2.norm_name)
            if similarity >= self.similarity_threshold:
                # 姓名高度相似 + 相同公司（公司名同样忽略空格差异）
                if f1.norm_org and f1.norm_org == f2.norm_org:
                    return True, f"相似姓名({similarity:.2f})和相同公司"

                # 姓名完全相同（去空格/大小写/全半角后）
                if similarity == 1.0:
                    return True, "相同姓名"

        return False, ""

    def should_merge_contacts(self, contact1: Contact, contact2: Contact) -> Tuple[bool, str]:
        """
        判断两个联系人是否应该合并

        Args:
            contact1: 联系人1
            contact2: 联系人2

        Returns:
            (是否应该合并, 合并原因)
        """
        return self._should_merge_features(
            self._extract_features(contact1),
            self._extract_features(contact2),
        )

    # ------------------------------------------------------------------
    # 分块索引（blocking）：生成候选对
    # ------------------------------------------------------------------

    def _generate_candidate_pairs(self, features_list: List[_ContactFeatures]) -> Set[Tuple[int, int]]:
        """
        通过倒排索引生成候选联系人对，避免 O(n^2) 全量两两比较。

        分块键与合并规则一一对应，保证不漏掉任何本应合并的对：
        - 电话分块键相同  -> 覆盖"相同电话号码"规则
        - 邮箱相同        -> 覆盖"相同邮箱地址"规则
        - 规范姓名相同    -> 覆盖"相同姓名"(相似度=1.0) 规则
        - 规范公司相同    -> 覆盖"相似姓名+相同公司"规则（仅在公司块内做模糊姓名比较）

        Args:
            features_list: 所有联系人的规范化特征

        Returns:
            候选下标对集合 (i, j)，i < j
        """
        phone_index: Dict[str, List[int]] = {}
        email_index: Dict[str, List[int]] = {}
        name_index: Dict[str, List[int]] = {}
        org_index: Dict[str, List[int]] = {}

        for i, features in enumerate(features_list):
            for key in features.phone_keys:
                phone_index.setdefault(key, []).append(i)
            for email in features.norm_emails:
                email_index.setdefault(email, []).append(i)
            if features.norm_name:
                name_index.setdefault(features.norm_name, []).append(i)
            # 模糊姓名比较只在公司块内进行；无姓名的联系人无需进入公司块
            if features.norm_org and features.norm_name:
                org_index.setdefault(features.norm_org, []).append(i)

        candidates: Set[Tuple[int, int]] = set()

        def _collect(index: Dict[str, List[int]]) -> None:
            for indices in index.values():
                size = len(indices)
                if size < 2:
                    continue
                for a in range(size):
                    ia = indices[a]
                    for b in range(a + 1, size):
                        ib = indices[b]
                        candidates.add((ia, ib) if ia < ib else (ib, ia))

        _collect(phone_index)
        _collect(email_index)
        _collect(name_index)
        _collect(org_index)

        return candidates

    # ------------------------------------------------------------------
    # 分组与合并
    # ------------------------------------------------------------------

    def find_duplicate_groups(self, contacts: List[Contact]) -> List[List[Contact]]:
        """
        查找重复联系人组

        Args:
            contacts: 联系人列表

        Returns:
            重复联系人组列表
        """
        if not contacts:
            return []

        n = len(contacts)

        # 一次性提取所有联系人的规范化特征
        features_list = [self._extract_features(contact) for contact in contacts]

        # 使用并查集算法找到所有相关联的联系人
        parent = list(range(n))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]  # 路径压缩（迭代实现，避免深递归）
                x = parent[x]
            return x

        def union(x, y):
            px, py = find(x), find(y)
            if px != py:
                parent[px] = py

        # 只检查分块索引生成的候选对，而非全量两两比较
        for i, j in self._generate_candidate_pairs(features_list):
            should_merge, _ = self._should_merge_features(features_list[i], features_list[j])
            if should_merge:
                union(i, j)

        # 按组分类联系人
        groups: Dict[int, List[Contact]] = {}
        for i, contact in enumerate(contacts):
            root = find(i)
            groups.setdefault(root, []).append(contact)

        # 只返回包含多个联系人的组
        return [group for group in groups.values() if len(group) > 1]

    def merge_contact_group(self, contacts: List[Contact]) -> Contact:
        """
        合并一组联系人。

        实际合并规则集中在 Contact.merge_group 中，保证"合并重复"与
        "合并VCF"等多条入口使用同一套规则（信息少者并入信息多者、
        姓名取简洁写法、括注移入备注、字段冲突留痕不丢信息）。

        Args:
            contacts: 要合并的联系人列表

        Returns:
            合并后的联系人
        """
        if not contacts:
            return Contact()

        return Contact.merge_group(contacts)

    def merge_duplicates_in_manager(self, contact_manager: ContactManager) -> Dict[str, int]:
        """
        在联系人管理器中合并重复联系人

        Args:
            contact_manager: 联系人管理器

        Returns:
            合并统计信息
        """
        contacts = contact_manager.get_all_contacts()
        duplicate_groups = self.find_duplicate_groups(contacts)

        stats = {
            'total_contacts_before': len(contacts),
            'duplicate_groups_found': len(duplicate_groups),
            'contacts_merged': 0,
            'total_contacts_after': 0
        }

        if not duplicate_groups:
            stats['total_contacts_after'] = contact_manager.count()
            return stats

        # 一次性重建联系人列表，避免逐个 remove（O(n) 列表查找）造成的二次方开销
        grouped_ids: Set[int] = set()
        merged_contacts: List[Contact] = []

        for group in duplicate_groups:
            for contact in group:
                grouped_ids.add(id(contact))
            merged_contacts.append(self.merge_contact_group(group))
            stats['contacts_merged'] += len(group) - 1

        kept_contacts = [c for c in contacts if id(c) not in grouped_ids]
        contact_manager.contacts = kept_contacts + merged_contacts

        stats['total_contacts_after'] = contact_manager.count()

        return stats

    # ------------------------------------------------------------------
    # 预览与统计
    # ------------------------------------------------------------------

    def preview_merge_operations(self, contacts: List[Contact]) -> List[Dict]:
        """
        预览合并操作

        Args:
            contacts: 联系人列表

        Returns:
            合并操作预览列表
        """
        duplicate_groups = self.find_duplicate_groups(contacts)
        preview = []

        for group in duplicate_groups:
            if len(group) > 1:
                merged = self.merge_contact_group(group)

                operation = {
                    'original_contacts': [
                        {
                            'name': contact.name,
                            'phones': contact.phones,
                            'emails': contact.emails,
                            'organization': contact.organization,
                            'notes': contact.notes
                        }
                        for contact in group
                    ],
                    'merged_contact': {
                        'name': merged.name,
                        'phones': merged.phones,
                        'emails': merged.emails,
                        'organization': merged.organization,
                        'nicknames': merged.nicknames,
                        'notes': merged.notes
                    },
                    'merge_count': len(group),
                    'merge_reasons': []
                }

                # 分析合并原因
                for i in range(len(group)):
                    for j in range(i + 1, len(group)):
                        should_merge, reason = self.should_merge_contacts(group[i], group[j])
                        if should_merge and reason not in operation['merge_reasons']:
                            operation['merge_reasons'].append(reason)

                preview.append(operation)

        return preview

    def find_potential_duplicates_by_phone(self, contacts: List[Contact]) -> Dict[str, List[Contact]]:
        """
        根据电话号码查找潜在重复联系人

        Args:
            contacts: 联系人列表

        Returns:
            电话号码到联系人列表的映射
        """
        phone_map = {}

        for contact in contacts:
            for phone in contact.phones:
                normalized_phone = self.normalize_phone_for_comparison(phone)
                if normalized_phone:
                    if normalized_phone not in phone_map:
                        phone_map[normalized_phone] = []
                    phone_map[normalized_phone].append(contact)

        # 只返回有重复的电话号码
        return {phone: contacts_list for phone, contacts_list in phone_map.items()
                if len(contacts_list) > 1}

    def find_potential_duplicates_by_email(self, contacts: List[Contact]) -> Dict[str, List[Contact]]:
        """
        根据邮箱查找潜在重复联系人

        Args:
            contacts: 联系人列表

        Returns:
            邮箱到联系人列表的映射
        """
        email_map = {}

        for contact in contacts:
            for email in contact.emails:
                normalized_email = email.lower().strip()
                if normalized_email:
                    if normalized_email not in email_map:
                        email_map[normalized_email] = []
                    email_map[normalized_email].append(contact)

        # 只返回有重复的邮箱
        return {email: contacts_list for email, contacts_list in email_map.items()
                if len(contacts_list) > 1}

    def find_potential_duplicates_by_name(self, contacts: List[Contact]) -> List[Tuple[Contact, Contact, float]]:
        """
        根据姓名相似度查找潜在重复联系人

        Args:
            contacts: 联系人列表

        Returns:
            (联系人1, 联系人2, 相似度)的列表
        """
        potential_duplicates = []

        # 姓名只规范化一次，避免在双层循环中重复正则运算
        normalized_names = [self.normalize_name_for_comparison(c.name) for c in contacts]

        for i in range(len(contacts)):
            if not normalized_names[i]:
                continue
            for j in range(i + 1, len(contacts)):
                if not normalized_names[j]:
                    continue
                similarity = self._normalized_name_similarity(normalized_names[i], normalized_names[j])
                if similarity >= self.similarity_threshold:
                    potential_duplicates.append((contacts[i], contacts[j], similarity))

        # 按相似度降序排序
        potential_duplicates.sort(key=lambda x: x[2], reverse=True)

        return potential_duplicates

    def get_merge_statistics(self, contacts: List[Contact]) -> Dict[str, int]:
        """
        获取合并统计信息

        Args:
            contacts: 联系人列表

        Returns:
            统计信息字典
        """
        duplicate_groups = self.find_duplicate_groups(contacts)
        phone_duplicates = self.find_potential_duplicates_by_phone(contacts)
        email_duplicates = self.find_potential_duplicates_by_email(contacts)
        name_duplicates = self.find_potential_duplicates_by_name(contacts)

        return {
            'total_contacts': len(contacts),
            'duplicate_groups': len(duplicate_groups),
            'contacts_in_duplicate_groups': sum(len(group) for group in duplicate_groups),
            'potential_merges': sum(len(group) - 1 for group in duplicate_groups),
            'phone_based_duplicates': len(phone_duplicates),
            'email_based_duplicates': len(email_duplicates),
            'name_based_duplicates': len(name_duplicates),
            'contacts_after_merge': len(contacts) - sum(len(group) - 1 for group in duplicate_groups)
        }
