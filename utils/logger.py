# -*- coding: utf-8 -*-
"""
日志记录工具
提供应用程序日志记录功能
"""

import os
import logging
from datetime import datetime
from typing import Optional


class AppLogger:
    """应用程序日志记录器"""
    
    def __init__(self, name: str = "ContactManager", log_dir: str = "logs"):
        """
        初始化日志记录器
        
        Args:
            name: 日志记录器名称
            log_dir: 日志文件目录
        """
        self.name = name
        self.log_dir = log_dir
        self.logger = None
        self._setup_logger()
    
    def _setup_logger(self):
        """设置日志记录器"""
        # 创建日志目录
        os.makedirs(self.log_dir, exist_ok=True)
        
        # 创建日志记录器
        self.logger = logging.getLogger(self.name)
        self.logger.setLevel(logging.DEBUG)
        
        # 避免重复添加处理器
        if self.logger.handlers:
            return
        
        # 创建文件处理器
        log_filename = os.path.join(
            self.log_dir, 
            f"{self.name}_{datetime.now().strftime('%Y%m%d')}.log"
        )
        
        file_handler = logging.FileHandler(log_filename, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        
        # 创建控制台处理器
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        
        # 创建格式化器
        file_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s'
        )
        console_formatter = logging.Formatter(
            '%(levelname)s - %(message)s'
        )
        
        # 设置格式化器
        file_handler.setFormatter(file_formatter)
        console_handler.setFormatter(console_formatter)
        
        # 添加处理器
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)
    
    def debug(self, message: str, *args, **kwargs):
        """记录调试信息"""
        self.logger.debug(message, *args, **kwargs)
    
    def info(self, message: str, *args, **kwargs):
        """记录信息"""
        self.logger.info(message, *args, **kwargs)
    
    def warning(self, message: str, *args, **kwargs):
        """记录警告"""
        self.logger.warning(message, *args, **kwargs)
    
    def error(self, message: str, *args, **kwargs):
        """记录错误"""
        self.logger.error(message, *args, **kwargs)
    
    def critical(self, message: str, *args, **kwargs):
        """记录严重错误"""
        self.logger.critical(message, *args, **kwargs)
    
    def exception(self, message: str, *args, **kwargs):
        """记录异常信息"""
        self.logger.exception(message, *args, **kwargs)
    
    def log_operation(self, operation: str, details: str = "", success: bool = True):
        """
        记录操作日志
        
        Args:
            operation: 操作名称
            details: 操作详情
            success: 是否成功
        """
        status = "成功" if success else "失败"
        message = f"操作: {operation} - 状态: {status}"
        
        if details:
            message += f" - 详情: {details}"
        
        if success:
            self.info(message)
        else:
            self.error(message)
    
    def log_file_operation(self, operation: str, file_path: str, success: bool = True, error: str = ""):
        """
        记录文件操作日志
        
        Args:
            operation: 操作类型
            file_path: 文件路径
            success: 是否成功
            error: 错误信息
        """
        message = f"文件{operation}: {file_path}"
        
        if success:
            self.info(message + " - 成功")
        else:
            self.error(message + f" - 失败: {error}")
    
    def log_import_operation(self, file_path: str, contact_count: int, success: bool = True, error: str = ""):
        """
        记录导入操作日志
        
        Args:
            file_path: 导入文件路径
            contact_count: 导入的联系人数量
            success: 是否成功
            error: 错误信息
        """
        if success:
            self.info(f"导入联系人成功: {file_path} - 导入数量: {contact_count}")
        else:
            self.error(f"导入联系人失败: {file_path} - 错误: {error}")
    
    def log_export_operation(self, file_path: str, contact_count: int, success: bool = True, error: str = ""):
        """
        记录导出操作日志
        
        Args:
            file_path: 导出文件路径
            contact_count: 导出的联系人数量
            success: 是否成功
            error: 错误信息
        """
        if success:
            self.info(f"导出联系人成功: {file_path} - 导出数量: {contact_count}")
        else:
            self.error(f"导出联系人失败: {file_path} - 错误: {error}")
    
    def log_merge_operation(self, merge_stats: dict):
        """
        记录合并操作日志
        
        Args:
            merge_stats: 合并统计信息
        """
        message = (
            f"联系人合并完成 - "
            f"原始数量: {merge_stats.get('total_contacts_before', 0)}, "
            f"合并组数: {merge_stats.get('duplicate_groups_found', 0)}, "
            f"合并数量: {merge_stats.get('contacts_merged', 0)}, "
            f"最终数量: {merge_stats.get('total_contacts_after', 0)}"
        )
        self.info(message)


# 全局日志记录器实例
_logger_instance: Optional[AppLogger] = None


def get_logger() -> AppLogger:
    """
    获取全局日志记录器实例
    
    Returns:
        日志记录器实例
    """
    global _logger_instance
    
    if _logger_instance is None:
        _logger_instance = AppLogger()
    
    return _logger_instance


def setup_logger(name: str = "ContactManager", log_dir: str = "logs") -> AppLogger:
    """
    设置全局日志记录器
    
    Args:
        name: 日志记录器名称
        log_dir: 日志文件目录
        
    Returns:
        日志记录器实例
    """
    global _logger_instance
    _logger_instance = AppLogger(name, log_dir)
    return _logger_instance


# 便捷函数
def debug(message: str, *args, **kwargs):
    """记录调试信息"""
    get_logger().debug(message, *args, **kwargs)


def info(message: str, *args, **kwargs):
    """记录信息"""
    get_logger().info(message, *args, **kwargs)


def warning(message: str, *args, **kwargs):
    """记录警告"""
    get_logger().warning(message, *args, **kwargs)


def error(message: str, *args, **kwargs):
    """记录错误"""
    get_logger().error(message, *args, **kwargs)


def critical(message: str, *args, **kwargs):
    """记录严重错误"""
    get_logger().critical(message, *args, **kwargs)


def exception(message: str, *args, **kwargs):
    """记录异常信息"""
    get_logger().exception(message, *args, **kwargs)


def log_operation(operation: str, details: str = "", success: bool = True):
    """记录操作日志"""
    get_logger().log_operation(operation, details, success)


def log_file_operation(operation: str, file_path: str, success: bool = True, error: str = ""):
    """记录文件操作日志"""
    get_logger().log_file_operation(operation, file_path, success, error)


def log_import_operation(file_path: str, contact_count: int, success: bool = True, error: str = ""):
    """记录导入操作日志"""
    get_logger().log_import_operation(file_path, contact_count, success, error)


def log_export_operation(file_path: str, contact_count: int, success: bool = True, error: str = ""):
    """记录导出操作日志"""
    get_logger().log_export_operation(file_path, contact_count, success, error)


def log_merge_operation(merge_stats: dict):
    """记录合并操作日志"""
    get_logger().log_merge_operation(merge_stats)