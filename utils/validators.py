# -*- coding: utf-8 -*-
"""
数据验证工具函数
提供各种数据格式验证功能
"""

import re
from typing import Optional, Tuple, List


def validate_email(email: str) -> Tuple[bool, str]:
    """
    验证邮箱地址格式
    
    Args:
        email: 邮箱地址
        
    Returns:
        (是否有效, 错误信息)
    """
    if not email:
        return False, "邮箱地址不能为空"
    
    # 基本邮箱格式验证
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    
    if not re.match(email_pattern, email):
        return False, "邮箱地址格式不正确"
    
    # 检查长度
    if len(email) > 254:
        return False, "邮箱地址过长"
    
    # 检查本地部分长度
    local_part = email.split('@')[0]
    if len(local_part) > 64:
        return False, "邮箱地址本地部分过长"
    
    return True, ""


def validate_phone(phone: str) -> Tuple[bool, str]:
    """
    验证电话号码格式
    
    Args:
        phone: 电话号码
        
    Returns:
        (是否有效, 错误信息)
    """
    if not phone:
        return False, "电话号码不能为空"
    
    # 移除所有非数字字符进行验证
    digits_only = re.sub(r'[^\d]', '', phone)
    
    if not digits_only:
        return False, "电话号码必须包含数字"
    
    # 检查长度（支持国际号码）
    if len(digits_only) < 7:
        return False, "电话号码过短"
    
    if len(digits_only) > 15:
        return False, "电话号码过长"
    
    # 检查中国手机号码格式
    if len(digits_only) == 11 and digits_only.startswith('1'):
        mobile_pattern = r'^1[3-9]\d{9}$'
        if not re.match(mobile_pattern, digits_only):
            return False, "中国手机号码格式不正确"
    
    return True, ""


def validate_name(name: str) -> Tuple[bool, str]:
    """
    验证姓名格式
    
    Args:
        name: 姓名
        
    Returns:
        (是否有效, 错误信息)
    """
    if not name:
        return False, "姓名不能为空"
    
    # 去除前后空格
    name = name.strip()
    
    if not name:
        return False, "姓名不能为空"
    
    # 检查长度
    if len(name) > 100:
        return False, "姓名过长"
    
    # 检查是否包含特殊字符（允许中文、英文、空格、点、连字符）
    name_pattern = r'^[\u4e00-\u9fff\u3400-\u4dbfa-zA-Z\s\.\-\']+$'
    if not re.match(name_pattern, name):
        return False, "姓名包含不允许的字符"
    
    return True, ""


def validate_organization(organization: str) -> Tuple[bool, str]:
    """
    验证组织/公司名称格式
    
    Args:
        organization: 组织名称
        
    Returns:
        (是否有效, 错误信息)
    """
    if not organization:
        return True, ""  # 组织名称可以为空
    
    # 去除前后空格
    organization = organization.strip()
    
    # 检查长度
    if len(organization) > 200:
        return False, "组织名称过长"
    
    return True, ""


def validate_address(address: str) -> Tuple[bool, str]:
    """
    验证地址格式
    
    Args:
        address: 地址
        
    Returns:
        (是否有效, 错误信息)
    """
    if not address:
        return True, ""  # 地址可以为空
    
    # 去除前后空格
    address = address.strip()
    
    # 检查长度
    if len(address) > 500:
        return False, "地址过长"
    
    return True, ""


def validate_birthday(birthday: str) -> Tuple[bool, str]:
    """
    验证生日格式
    
    Args:
        birthday: 生日字符串
        
    Returns:
        (是否有效, 错误信息)
    """
    if not birthday:
        return True, ""  # 生日可以为空
    
    # 支持多种日期格式
    date_patterns = [
        r'^\d{4}-\d{2}-\d{2}$',  # YYYY-MM-DD
        r'^\d{4}/\d{2}/\d{2}$',  # YYYY/MM/DD
        r'^\d{2}-\d{2}-\d{4}$',  # DD-MM-YYYY
        r'^\d{2}/\d{2}/\d{4}$',  # DD/MM/YYYY
        r'^\d{4}\d{2}\d{2}$',    # YYYYMMDD
    ]
    
    for pattern in date_patterns:
        if re.match(pattern, birthday):
            return True, ""
    
    return False, "生日格式不正确，支持格式：YYYY-MM-DD, YYYY/MM/DD, DD-MM-YYYY, DD/MM/YYYY, YYYYMMDD"


def validate_url(url: str) -> Tuple[bool, str]:
    """
    验证URL格式
    
    Args:
        url: URL地址
        
    Returns:
        (是否有效, 错误信息)
    """
    if not url:
        return True, ""  # URL可以为空
    
    # 基本URL格式验证，支持更多协议
    url_pattern = r'^(https?|ftp)://[^\s/$.?#].[^\s]*$'
    
    if not re.match(url_pattern, url, re.IGNORECASE):
        return False, "URL格式不正确"
    
    return True, ""


def normalize_phone_number(phone: str) -> str:
    """
    标准化电话号码
    
    Args:
        phone: 原始电话号码
        
    Returns:
        标准化后的电话号码
    """
    if not phone:
        return ""
    
    # 移除所有非数字字符
    digits_only = re.sub(r'[^\d]', '', phone)
    
    # 处理中国手机号码 - 直接返回数字
    if len(digits_only) == 11 and digits_only.startswith('1'):
        return digits_only
    
    # 处理固定电话号码（如010-12345678）
    if len(digits_only) >= 10:
        if digits_only.startswith('010'):
            return digits_only
        elif digits_only.startswith('0'):
            return digits_only
    
    # 其他格式保持原样
    return digits_only if digits_only else phone


def normalize_email(email: str) -> str:
    """
    标准化邮箱地址
    
    Args:
        email: 原始邮箱地址
        
    Returns:
        标准化后的邮箱地址
    """
    if not email:
        return ""
    
    # 转换为小写并去除前后空格
    return email.lower().strip()


def normalize_name(name: str) -> str:
    """
    标准化姓名
    
    Args:
        name: 原始姓名
        
    Returns:
        标准化后的姓名
    """
    if not name:
        return ""
    
    # 去除前后空格，合并多个空格为一个
    normalized = re.sub(r'\s+', ' ', name.strip())
    
    # 首字母大写（仅对英文名）
    if re.match(r'^[a-zA-Z\s\.\-\']+$', normalized):
        normalized = normalized.title()
    
    return normalized


def validate_contact_data(contact_data: dict) -> Tuple[bool, List[str]]:
    """
    验证联系人数据
    
    Args:
        contact_data: 联系人数据字典
        
    Returns:
        (是否有效, 错误信息列表)
    """
    errors = []
    
    # 验证姓名
    name = contact_data.get('name', '')
    is_valid, error = validate_name(name)
    if not is_valid:
        errors.append(f"姓名: {error}")
    
    # 验证电话号码
    phones = contact_data.get('phones', [])
    if isinstance(phones, str):
        phones = [phones]
    
    for i, phone in enumerate(phones):
        is_valid, error = validate_phone(phone)
        if not is_valid:
            errors.append(f"电话号码{i+1}: {error}")
    
    # 验证邮箱
    emails = contact_data.get('emails', [])
    if isinstance(emails, str):
        emails = [emails]
    
    for i, email in enumerate(emails):
        if email:  # 邮箱可以为空
            is_valid, error = validate_email(email)
            if not is_valid:
                errors.append(f"邮箱{i+1}: {error}")
    
    # 验证组织
    organization = contact_data.get('organization', '')
    if organization:
        is_valid, error = validate_organization(organization)
        if not is_valid:
            errors.append(f"组织: {error}")
    
    # 验证地址
    address = contact_data.get('address', '')
    if address:
        is_valid, error = validate_address(address)
        if not is_valid:
            errors.append(f"地址: {error}")
    
    # 验证生日
    birthday = contact_data.get('birthday', '')
    if birthday:
        is_valid, error = validate_birthday(birthday)
        if not is_valid:
            errors.append(f"生日: {error}")
    
    # 验证网站
    website = contact_data.get('website', '')
    if website:
        is_valid, error = validate_url(website)
        if not is_valid:
            errors.append(f"网站: {error}")
    
    return len(errors) == 0, errors


def sanitize_input(text: str) -> str:
    """
    清理输入文本，移除潜在的危险字符
    
    Args:
        text: 输入文本
        
    Returns:
        清理后的文本
    """
    if not text:
        return ""
    
    # 移除控制字符
    text = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', text)
    
    # 去除前后空格
    text = text.strip()
    
    return text


def is_empty_contact(contact_data: dict) -> bool:
    """
    检查联系人数据是否为空
    
    Args:
        contact_data: 联系人数据字典
        
    Returns:
        是否为空联系人
    """
    # 检查关键字段
    key_fields = ['name', 'phones', 'emails']
    
    for field in key_fields:
        value = contact_data.get(field)
        
        if isinstance(value, str) and value.strip():
            return False
        elif isinstance(value, list) and any(item.strip() for item in value if isinstance(item, str)):
            return False
    
    return True


def validate_file_format(file_path: str, expected_format: str) -> Tuple[bool, str]:
    """
    验证文件格式
    
    Args:
        file_path: 文件路径
        expected_format: 期望的文件格式 ('excel', 'vcf')
        
    Returns:
        (是否有效, 错误信息)
    """
    import os
    
    if not os.path.exists(file_path):
        return False, "文件不存在"
    
    extension = os.path.splitext(file_path)[1].lower()
    
    if expected_format == 'excel':
        valid_extensions = ['.xlsx', '.xls', '.xlsm']
        if extension not in valid_extensions:
            return False, f"不支持的Excel文件格式，支持的格式: {', '.join(valid_extensions)}"
    
    elif expected_format == 'vcf':
        valid_extensions = ['.vcf', '.vcard']
        if extension not in valid_extensions:
            return False, f"不支持的VCF文件格式，支持的格式: {', '.join(valid_extensions)}"
    
    else:
        return False, f"不支持的文件格式: {expected_format}"
    
    return True, ""