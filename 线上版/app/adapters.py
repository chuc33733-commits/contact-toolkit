# -*- coding: utf-8 -*-
"""
适配层：把「浏览器上传的字节」接到项目原有的「路径型」IO 接口上。

原项目 82% 的核心逻辑（models / processors / utils / config）**一行不改**复用；
这里只做一件事：把上传字节落到会话的临时目录，再调用既有方法，最后把结果
转回字节返回给浏览器。

关键硬约束（已在方案中确认）：
1. 浏览器不可能让服务器读到客户端本地照片路径 → 照片只支持 URI / base64，
   或用户上传「Excel + 照片目录」打成的 zip 包（解压后以 Excel 所在目录为基准）。
2. 导出 Excel 含内嵌照片时会另写同级 `xxx_photos/` 目录 → 线上必须打包成 zip
   下载，否则照片会静默丢失。
"""

import io
import os
import shutil
import tempfile
import zipfile
from typing import Any, Dict, List, Optional, Tuple

from models.contact import ContactManager
from processors.excel_processor import ExcelProcessor
from processors.vcf_processor import VCFProcessor

# 单文件大小上限（方案 §七：限制单文件 20MB）
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# 可被 Excel 处理器读取的扩展名（补齐 .xlsm，修复原「界面给选、实际报错」缺陷）
EXCEL_EXTENSIONS = ('.xlsx', '.xlsm', '.xls')
VCF_EXTENSIONS = ('.vcf', '.vcard')

_vcf = VCFProcessor()
_excel = ExcelProcessor()


# ----------------------------------------------------------------------
# 导入
# ----------------------------------------------------------------------

def import_vcf_bytes(filename: str, data: bytes, workdir: str) -> Tuple[Optional[ContactManager], Dict[str, Any], str]:
    """
    导入单个 VCF 字节流。

    Args:
        filename: 原始文件名（仅用于扩展名校验与展示）
        data: 文件字节
        workdir: 会话临时目录

    Returns:
        (ContactManager, 统计信息, 错误信息)
    """
    if len(data) > MAX_UPLOAD_BYTES:
        return None, {}, f"文件过大：{filename}（上限 {MAX_UPLOAD_BYTES // 1024 // 1024}MB）"
    if not filename.lower().endswith(VCF_EXTENSIONS):
        return None, {}, f"不支持的 VCF 格式：{filename}"

    path = _write_temp(workdir, filename, data)
    try:
        manager, stats, error = _vcf.import_from_vcf(path)
    finally:
        _safe_remove(path)
    if error:
        return None, {}, error
    stats['filename'] = filename
    return manager, stats, ""


def import_excel_bytes(filename: str, data: bytes, workdir: str) -> Tuple[Optional[ContactManager], Dict[str, Any], str]:
    """
    导入 Excel 字节流；可自动识别 zip 包（内含 Excel + 照片目录）。

    - 若上传的是 zip：解压到会话子目录，取其中的 Excel 文件导入，
      照片相对路径以 Excel 所在目录为基准（复用原处理器逻辑）。
    - 否则按单个 Excel 文件导入（照片仅支持 URI / base64）。

    Args:
        filename: 原始文件名
        data: 文件字节
        workdir: 会话临时目录

    Returns:
        (ContactManager, 统计信息, 错误信息)
    """
    if len(data) > MAX_UPLOAD_BYTES:
        return None, {}, f"文件过大：{filename}（上限 {MAX_UPLOAD_BYTES // 1024 // 1024}MB）"

    lower = filename.lower()
    # 先按扩展名判断：.xlsx/.xlsm 本身就是 zip 容器，若先做 magic number 判断
    # 会被误路由到「解压 zip」分支（解出来的是一堆 xml，找不到 Excel 而报错）。
    if lower.endswith(EXCEL_EXTENSIONS):
        path = _write_temp(workdir, filename, data)
        try:
            manager, stats, error = _excel.import_from_excel(path)
        finally:
            _safe_remove(path)
        if error:
            return None, {}, error
        stats['filename'] = filename
        return manager, stats, ""

    # 非 Excel 扩展名：可能是 zip 包（内含 Excel + 照片目录），或扩展名缺失的字节流
    if lower.endswith('.zip') or _looks_like_zip(data):
        return _import_excel_zip(filename, data, workdir)

    return None, {}, f"不支持的 Excel 格式：{filename}"


def _import_excel_zip(filename: str, data: bytes, workdir: str) -> Tuple[Optional[ContactManager], Dict[str, Any], str]:
    """解压 zip 后导入其中的 Excel（照片以 Excel 同级目录为基准）。"""
    extract_dir = tempfile.mkdtemp(prefix="xlsx-", dir=workdir)
    try:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                _safe_extract(zf, extract_dir)
        except zipfile.BadZipFile:
            return None, {}, f"无法解析压缩包：{filename}"

        excel_path = _find_first(extract_dir, EXCEL_EXTENSIONS)
        if not excel_path:
            return None, {}, f"压缩包内未找到 Excel 文件（{filename}）"

        manager, stats, error = _excel.import_from_excel(excel_path)
        if error:
            return None, {}, error
        stats['filename'] = filename
        stats['from_zip'] = True
        return manager, stats, ""
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)


# ----------------------------------------------------------------------
# 导出
# ----------------------------------------------------------------------

def export_vcf_bytes(manager: ContactManager, workdir: str) -> Tuple[Optional[bytes], str]:
    """导出 VCF 字节流。"""
    path = os.path.join(workdir, "merged.vcf")
    error = _vcf.export_to_vcf(manager, path)
    if error:
        return None, error
    try:
        with open(path, 'rb') as fh:
            return fh.read(), ""
    finally:
        _safe_remove(path)


def export_excel_bytes(manager: ContactManager, workdir: str) -> Tuple[Optional[bytes], str, str]:
    """
    导出 Excel 字节流；含内嵌照片时自动打包为 zip。

    Returns:
        (字节内容, 建议文件名, 错误信息)
    """
    out_dir = tempfile.mkdtemp(prefix="xlsxout-", dir=workdir)
    try:
        excel_path = os.path.join(out_dir, "merged.xlsx")
        error = _excel.export_to_excel(manager, excel_path)
        if error:
            return None, "", error

        photo_dir = os.path.join(out_dir, "merged_photos")
        if os.path.isdir(photo_dir) and os.listdir(photo_dir):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.write(excel_path, "merged.xlsx")
                for root, _dirs, files in os.walk(photo_dir):
                    for name in files:
                        full = os.path.join(root, name)
                        zf.write(full, os.path.relpath(full, out_dir))
            return buffer.getvalue(), "merged.zip", ""

        with open(excel_path, 'rb') as fh:
            return fh.read(), "merged.xlsx", ""
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


def export_vcards_text(manager: ContactManager) -> str:
    """
    生成「全部联系人」的合并 VCF 文本（纯内存，用于与基线对照/G2 校验）。

    Args:
        manager: 联系人管理器

    Returns:
        VCF 文本
    """
    cards = [_vcf.contact_to_vcard(c) for c in manager.get_all_contacts()]
    return '\n\n'.join(cards)


# ----------------------------------------------------------------------
# 辅助
# ----------------------------------------------------------------------

def _write_temp(workdir: str, filename: str, data: bytes) -> str:
    """把字节写到会话临时目录，返回路径（文件名做安全化处理）。"""
    safe = os.path.basename(filename).replace(os.sep, '_') or "upload.bin"
    path = os.path.join(workdir, f"{tempfile._get_candidate_names().__next__()}_{safe}")
    with open(path, 'wb') as fh:
        fh.write(data)
    return path


def _safe_remove(path: str) -> None:
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def _looks_like_zip(data: bytes) -> bool:
    """通过 magic number 判断是否为 zip（PK\\x03\\x04）。"""
    return data[:4] == b'PK\x03\x04'


def _safe_extract(zf: zipfile.ZipFile, target: str) -> None:
    """防止 zip 路径穿越（Zip Slip）。"""
    base = os.path.abspath(target)
    for member in zf.namelist():
        dest = os.path.abspath(os.path.join(target, member))
        if not dest.startswith(base + os.sep) and dest != base:
            raise zipfile.BadZipFile(f"压缩包含非法路径：{member}")
    zf.extractall(target)


def _find_first(root: str, extensions: Tuple[str, ...]) -> Optional[str]:
    """在目录树中找第一个匹配扩展名的文件（跳过 __MACOSX 等垃圾）。"""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != '__MACOSX']
        for name in sorted(filenames):
            if name.startswith('._'):
                continue
            if name.lower().endswith(extensions):
                return os.path.join(dirpath, name)
    return None
