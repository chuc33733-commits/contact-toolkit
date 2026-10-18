# -*- coding: utf-8 -*-
"""
文件处理工具函数
提供文件操作、编码检测、路径处理等通用功能
"""

import os
import chardet
import shutil
from typing import Optional, List, Dict, Tuple
from pathlib import Path


def detect_file_encoding(file_path: str, sample_size: int = 8192) -> str:
    """
    检测文件编码
    
    Args:
        file_path: 文件路径
        sample_size: 采样大小
        
    Returns:
        检测到的编码格式
    """
    try:
        with open(file_path, 'rb') as f:
            raw_data = f.read(sample_size)
            
        if not raw_data:
            return 'utf-8'
            
        result = chardet.detect(raw_data)
        encoding = result.get('encoding', 'utf-8')
        
        # 处理一些特殊情况
        if encoding and encoding.lower() in ['gb2312', 'gbk']:
            encoding = 'gbk'
        elif encoding and encoding.lower() in ['utf-8-sig']:
            encoding = 'utf-8-sig'
        elif not encoding or result.get('confidence', 0) < 0.7:
            encoding = 'utf-8'
            
        return encoding
        
    except Exception:
        return 'utf-8'


def read_text_file(file_path: str, encoding: Optional[str] = None) -> str:
    """
    读取文本文件
    
    Args:
        file_path: 文件路径
        encoding: 指定编码，如果为None则自动检测
        
    Returns:
        文件内容
        
    Raises:
        FileNotFoundError: 文件不存在
        UnicodeDecodeError: 编码错误
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"文件不存在: {file_path}")
    
    if encoding is None:
        encoding = detect_file_encoding(file_path)
    
    try:
        with open(file_path, 'r', encoding=encoding) as f:
            return f.read()
    except UnicodeDecodeError:
        # 如果指定编码失败，尝试其他常见编码
        encodings_to_try = ['utf-8', 'gbk', 'gb2312', 'utf-8-sig', 'latin1']
        
        for enc in encodings_to_try:
            if enc != encoding:
                try:
                    with open(file_path, 'r', encoding=enc) as f:
                        return f.read()
                except UnicodeDecodeError:
                    continue
        
        # 如果所有编码都失败，使用错误处理
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()


def write_text_file(file_path: str, content: str, encoding: str = 'utf-8') -> None:
    """
    写入文本文件
    
    Args:
        file_path: 文件路径
        content: 文件内容
        encoding: 编码格式
    """
    # 确保目录存在
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    
    with open(file_path, 'w', encoding=encoding) as f:
        f.write(content)


def get_file_size(file_path: str) -> int:
    """
    获取文件大小
    
    Args:
        file_path: 文件路径
        
    Returns:
        文件大小（字节）
    """
    try:
        return os.path.getsize(file_path)
    except OSError:
        return 0


def format_file_size(size_bytes: int) -> str:
    """
    格式化文件大小
    
    Args:
        size_bytes: 文件大小（字节）
        
    Returns:
        格式化后的文件大小字符串
    """
    if size_bytes == 0:
        return "0 B"
    
    size_names = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    
    while size_bytes >= 1024 and i < len(size_names) - 1:
        size_bytes /= 1024.0
        i += 1
    
    return f"{size_bytes:.1f} {size_names[i]}"


def get_file_info(file_path: str) -> Dict[str, str]:
    """
    获取文件信息
    
    Args:
        file_path: 文件路径
        
    Returns:
        文件信息字典
    """
    if not os.path.exists(file_path):
        return {}
    
    stat = os.stat(file_path)
    
    return {
        'name': os.path.basename(file_path),
        'path': file_path,
        'size': format_file_size(stat.st_size),
        'size_bytes': stat.st_size,
        'modified': str(stat.st_mtime),
        'extension': os.path.splitext(file_path)[1].lower(),
        'encoding': detect_file_encoding(file_path) if os.path.isfile(file_path) else None
    }


def validate_file_path(file_path: str) -> Tuple[bool, str]:
    """
    验证文件路径
    
    Args:
        file_path: 文件路径
        
    Returns:
        (是否有效, 错误信息)
    """
    if not file_path:
        return False, "文件路径不能为空"
    
    if not os.path.exists(file_path):
        return False, "文件不存在"
    
    if not os.path.isfile(file_path):
        return False, "路径不是文件"
    
    if not os.access(file_path, os.R_OK):
        return False, "文件不可读"
    
    return True, ""


def validate_directory_path(dir_path: str) -> Tuple[bool, str]:
    """
    验证目录路径
    
    Args:
        dir_path: 目录路径
        
    Returns:
        (是否有效, 错误信息)
    """
    if not dir_path:
        return False, "目录路径不能为空"
    
    if not os.path.exists(dir_path):
        return False, "目录不存在"
    
    if not os.path.isdir(dir_path):
        return False, "路径不是目录"
    
    if not os.access(dir_path, os.R_OK):
        return False, "目录不可读"
    
    return True, ""


def create_backup_filename(file_path: str) -> str:
    """
    创建备份文件名
    
    Args:
        file_path: 原文件路径
        
    Returns:
        备份文件路径
    """
    path = Path(file_path)
    timestamp = str(int(os.path.getmtime(file_path))) if os.path.exists(file_path) else "backup"
    
    backup_name = f"{path.stem}_backup_{timestamp}{path.suffix}"
    return str(path.parent / backup_name)


def backup_file(file_path: str, backup_dir: Optional[str] = None) -> str:
    """
    备份文件
    
    Args:
        file_path: 要备份的文件路径
        backup_dir: 备份目录，如果为None则在原文件目录创建备份
        
    Returns:
        备份文件路径
        
    Raises:
        FileNotFoundError: 原文件不存在
        OSError: 备份失败
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"文件不存在: {file_path}")
    
    if backup_dir:
        os.makedirs(backup_dir, exist_ok=True)
        backup_path = os.path.join(backup_dir, os.path.basename(file_path))
        backup_path = create_backup_filename(backup_path)
    else:
        backup_path = create_backup_filename(file_path)
    
    shutil.copy2(file_path, backup_path)
    return backup_path


def get_supported_file_extensions() -> Dict[str, List[str]]:
    """
    获取支持的文件扩展名
    
    Returns:
        文件类型到扩展名列表的映射
    """
    return {
        'excel': ['.xlsx', '.xls', '.xlsm'],
        'vcf': ['.vcf', '.vcard'],
        'text': ['.txt', '.csv'],
        'all': ['*.*']
    }


def is_supported_file(file_path: str, file_type: str = 'all') -> bool:
    """
    检查文件是否为支持的类型
    
    Args:
        file_path: 文件路径
        file_type: 文件类型 ('excel', 'vcf', 'text', 'all')
        
    Returns:
        是否支持
    """
    if not os.path.exists(file_path):
        return False
    
    extension = os.path.splitext(file_path)[1].lower()
    supported_extensions = get_supported_file_extensions()
    
    if file_type == 'all':
        all_extensions = []
        for exts in supported_extensions.values():
            if exts != ['*.*']:
                all_extensions.extend(exts)
        return extension in all_extensions
    
    return extension in supported_extensions.get(file_type, [])


def get_file_type(file_path: str) -> Optional[str]:
    """
    根据文件扩展名判断文件类型
    
    Args:
        file_path: 文件路径
        
    Returns:
        文件类型或None
    """
    extension = os.path.splitext(file_path)[1].lower()
    supported_extensions = get_supported_file_extensions()
    
    for file_type, extensions in supported_extensions.items():
        if file_type != 'all' and extension in extensions:
            return file_type
    
    return None


def clean_filename(filename: str) -> str:
    """
    清理文件名，移除不合法字符
    
    Args:
        filename: 原文件名
        
    Returns:
        清理后的文件名
    """
    # Windows不允许的字符
    invalid_chars = '<>:"/\\|?*'
    
    for char in invalid_chars:
        filename = filename.replace(char, '_')
    
    # 移除前后空格和点
    filename = filename.strip(' .')
    
    # 确保文件名不为空
    if not filename:
        filename = "untitled"
    
    return filename


def ensure_unique_filename(file_path: str) -> str:
    """
    确保文件名唯一，如果文件已存在则添加数字后缀
    
    Args:
        file_path: 文件路径
        
    Returns:
        唯一的文件路径
    """
    if not os.path.exists(file_path):
        return file_path
    
    path = Path(file_path)
    base_name = path.stem
    extension = path.suffix
    directory = path.parent
    
    counter = 1
    while True:
        new_name = f"{base_name}_{counter}{extension}"
        new_path = directory / new_name
        
        if not os.path.exists(new_path):
            return str(new_path)
        
        counter += 1


def get_temp_filename(prefix: str = "temp", suffix: str = ".tmp") -> str:
    """
    获取临时文件名
    
    Args:
        prefix: 文件名前缀
        suffix: 文件扩展名
        
    Returns:
        临时文件路径
    """
    import tempfile
    import uuid
    
    temp_dir = tempfile.gettempdir()
    unique_id = str(uuid.uuid4())[:8]
    temp_filename = f"{prefix}_{unique_id}{suffix}"
    
    return os.path.join(temp_dir, temp_filename)


def safe_delete_file(file_path: str) -> bool:
    """
    安全删除文件
    
    Args:
        file_path: 文件路径
        
    Returns:
        是否删除成功
    """
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
        return True
    except OSError:
        return False


def count_lines_in_file(file_path: str) -> int:
    """
    统计文件行数
    
    Args:
        file_path: 文件路径
        
    Returns:
        行数
    """
    try:
        with open(file_path, 'r', encoding=detect_file_encoding(file_path)) as f:
            return sum(1 for _ in f)
    except Exception:
        return 0