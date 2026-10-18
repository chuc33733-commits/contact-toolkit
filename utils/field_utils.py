# -*- coding: utf-8 -*-
"""
字段值规范化工具

集中处理"外部输入 → 模型字段"的格式归一，避免各处理器各写一套：
- Excel 单元格值（含 NaN / 数值 / 日期对象）统一转字符串
- 日期文本统一为 YYYY-MM-DD
- 照片值统一为 http(s) URI 或 data URI（便于去重、合并与 vCard 导出）
"""

import base64
import mimetypes
import os
import re
from typing import List, Optional, Tuple

from config.settings import PHOTO_MAX_BYTES, PHOTO_URI_SCHEMES

# 常见的"缺失值"文本，出现时视为空
NULL_TEXTS = frozenset({'nan', 'none', 'null', 'nat', 'n/a', '-', '—', ''})

# 日期文本：2020年1月5日 / 2020-01-05 / 2020/1/5 / 2020.1.5 / 20200105
_DATE_PATTERNS = (
    re.compile(r'^(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?$'),
    re.compile(r'^(\d{4})(\d{2})(\d{2})$'),
)
# 无年份的生日：--0415 / --04-15 / 04-15（保留原始形态，仅做分隔符归一）
_YEARLESS_DATE_RE = re.compile(r'^-{0,2}\s*(\d{1,2})\s*[-/.月]?\s*(\d{1,2})\s*日?$')

# base64 数据：长度足够且只含 base64 字符集
_BASE64_RE = re.compile(r'^[A-Za-z0-9+/\s]+={0,2}$')
# data URI：参数既可能是 "key=value"（charset=utf-8），也可能是无值的 "base64"
_DATA_URI_RE = re.compile(
    r'^data:(?P<mime>[-\w.+/]+)(?P<params>(?:;[-\w.]+(?:=[^,;]*)?)*),(?P<payload>.*)$',
    re.S,
)

_IMAGE_MIME_BY_EXT = {
    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.jpe': 'image/jpeg',
    '.png': 'image/png', '.gif': 'image/gif', '.bmp': 'image/bmp',
    '.webp': 'image/webp', '.heic': 'image/heic', '.heif': 'image/heif',
    '.tif': 'image/tiff', '.tiff': 'image/tiff',
}


def is_null_text(value: str) -> bool:
    """
    判断文本是否代表"空值"。

    Args:
        value: 待判断文本

    Returns:
        True 表示应视为空
    """
    return value is None or value.strip().lower() in NULL_TEXTS


def to_text(value) -> str:
    """
    把任意单元格值转为干净的字符串。

    Args:
        value: 单元格原始值（str / int / float / datetime / NaN 等）

    Returns:
        去除首尾空白的字符串；空值返回空字符串
    """
    if value is None:
        return ""

    # pandas 的 NaN 是 float，且 NaN != NaN，用自身比较判断
    if isinstance(value, float):
        if value != value:  # NaN
            return ""
        # 电话号码常被 Excel 读成浮点数：13800138000.0 → 13800138000
        if value.is_integer():
            return str(int(value))
        return repr(value).rstrip('0').rstrip('.')

    text = str(value).strip()
    # datetime / Timestamp → 日期或日期时间文本
    if hasattr(value, 'strftime'):
        if getattr(value, 'hour', 0) or getattr(value, 'minute', 0) or getattr(value, 'second', 0):
            return value.strftime('%Y-%m-%d %H:%M:%S')
        return value.strftime('%Y-%m-%d')

    return "" if is_null_text(text) else text


def normalize_date_text(value) -> str:
    """
    将日期文本规范化为 YYYY-MM-DD。

    支持: 2020年1月5日 / 2020-1-5 / 2020/01/05 / 2020.1.5 / 20200105 / Excel 日期对象。
    农历等无法解析的自由文本原样保留，不做破坏性改写。

    Args:
        value: 原始日期值

    Returns:
        规范化后的日期文本（无法识别时返回原文本）
    """
    text = to_text(value)
    if not text:
        return ""

    for pattern in _DATE_PATTERNS:
        match = pattern.match(text)
        if match:
            year, month, day = (int(part) for part in match.groups())
            if 1 <= month <= 12 and 1 <= day <= 31:
                return f"{year:04d}-{month:02d}-{day:02d}"

    # 无年份：--0415 → 04-15（保留"无年份"语义不变）
    if text.startswith('--') or text.startswith('-'):
        match = _YEARLESS_DATE_RE.match(text)
        if match:
            month, day = (int(part) for part in match.groups())
            if 1 <= month <= 12 and 1 <= day <= 31:
                prefix = '--' if text.startswith('--') else ''
                return f"{prefix}{month:02d}-{day:02d}"

    return text


def parse_data_uri(photo: str) -> Optional[Tuple[str, str]]:
    """
    解析 data URI。

    Args:
        photo: data URI 文本

    Returns:
        (mime 类型, base64 负载)，非 data URI 返回 None
    """
    match = _DATA_URI_RE.match(photo or "")
    if not match:
        return None
    return match.group('mime'), match.group('payload')


def is_data_uri(photo: str) -> bool:
    """判断照片值是否为 data URI。"""
    return bool(_DATA_URI_RE.match(photo or ""))


def is_remote_uri(photo: str) -> bool:
    """判断照片值是否为 http/https 链接。"""
    return bool(photo) and photo.lower().startswith(PHOTO_URI_SCHEMES)


def photo_payload_size(photo: str) -> int:
    """
    估算照片的二进制字节数（用于提示与限额校验）。

    Args:
        photo: data URI 或 URI

    Returns:
        字节数；非 data URI 返回 0
    """
    parsed = parse_data_uri(photo or "")
    if not parsed:
        return 0
    payload = re.sub(r'\s+', '', parsed[1])
    return max(len(payload) * 3 // 4 - payload.count('='), 0)


def _safe_isfile(path: str) -> bool:
    """
    安全判断路径是否为文件。

    超长 base64 文本被误当作路径传给 os.path.isfile 时，Windows 可能抛
    OSError/ValueError，这里统一吞掉并视为"不是文件"。

    Args:
        path: 待判断路径

    Returns:
        是否为已存在的文件
    """
    if not path or len(path) > 4096:
        return False
    try:
        return os.path.isfile(path)
    except (OSError, ValueError):
        return False


def _is_single_photo_value(text: str, base_dir: Optional[str] = None) -> bool:
    """
    判断文本是否已是「单个」可识别的照片值。

    识别范围与 normalize_photo_value 的输入一致：链接 / data URI / 已存在的
    文件（含相对 base_dir 解析）/ 裸 base64。用于拆分前先确认整体是否已是单值。

    Args:
        text: 待判断文本
        base_dir: 相对路径的基准目录

    Returns:
        是否为单个可识别的照片值
    """
    if not text:
        return False
    if is_remote_uri(text) or is_data_uri(text):
        return True

    candidate = text.strip('"').strip("'")
    if _safe_isfile(candidate):
        return True
    if base_dir and not os.path.isabs(candidate) and _safe_isfile(os.path.join(base_dir, candidate)):
        return True

    compact = re.sub(r'\s+', '', text)
    return len(compact) >= 64 and bool(_BASE64_RE.match(compact))


def split_photo_values(value, base_dir: Optional[str] = None) -> List[str]:
    """
    把一个「照片」单元格拆成多个照片值。

    导出侧把同一联系人的多张照片用 ", " 连接写进一个单元格，而 data URI 里分隔
    mime 与负载的逗号后**紧跟负载、不带空格**，因此可以安全地按「逗号 + 空白」拆分：

    1. 若按 ", " 拆开后每一段都是单个可识别的照片值 → 判定为多值，返回拆分结果
       （这样多个 data URI 也不会被当成一个整体）；
    2. 否则若整体已是单个照片值 → 原样返回（避免误拆 data URI / 含逗号的链接）；
    3. 都不满足时按 ", " 拆开返回，让每段各自报错，便于用户定位问题值。

    Args:
        value: 单元格原始值
        base_dir: 相对路径的基准目录（一般为 Excel 文件所在目录）

    Returns:
        照片值列表（可能为空）
    """
    text = to_text(value)
    if not text:
        return []

    parts = [p.strip() for p in re.split(r',\s+', text) if p.strip()]
    if len(parts) > 1 and all(_is_single_photo_value(p, base_dir) for p in parts):
        return parts
    if _is_single_photo_value(text, base_dir):
        return [text]
    return parts or [text]


def normalize_photo_value(value, base_dir: Optional[str] = None) -> Tuple[str, str]:
    """
    把照片字段的原始值规范化为 data URI 或 http(s) URI。

    支持的输入：
    1. http(s) 链接 —— 原样保留（vCard 用 VALUE=URI 表达）
    2. data URI —— 原样保留
    3. 本地文件路径（绝对或相对，相对路径按 base_dir 解析）—— 读文件并编码为 data URI
    4. 裸 base64 文本 —— 补全为 data URI（默认 JPEG）

    Args:
        value: 原始照片值
        base_dir: 相对路径的基准目录（通常为 Excel 文件所在目录）

    Returns:
        (规范化后的照片值, 错误信息)；失败时照片值为空字符串
    """
    text = to_text(value)
    if not text:
        return "", ""

    # 1) 远程链接
    if is_remote_uri(text):
        return text, ""

    # 2) data URI
    if is_data_uri(text):
        if photo_payload_size(text) > PHOTO_MAX_BYTES:
            return "", f"照片数据超过 {PHOTO_MAX_BYTES // 1024 // 1024}MB 上限，已跳过"
        return text.replace('\n', '').replace('\r', ''), ""

    # 3) 本地文件
    candidate = text.strip('"').strip("'")
    path_candidates = [candidate]
    if base_dir and not os.path.isabs(candidate):
        path_candidates.append(os.path.join(base_dir, candidate))

    for path in path_candidates:
        if _safe_isfile(path):
            size = os.path.getsize(path)
            if size > PHOTO_MAX_BYTES:
                return "", (f"照片 {os.path.basename(path)} 超过 "
                            f"{PHOTO_MAX_BYTES // 1024 // 1024}MB 上限，已跳过")
            try:
                with open(path, 'rb') as fh:
                    payload = fh.read()
            except OSError as exc:
                return "", f"读取照片文件失败: {exc}"
            mime = _IMAGE_MIME_BY_EXT.get(os.path.splitext(path)[1].lower())
            if not mime:
                mime = mimetypes.guess_type(path)[0] or 'image/jpeg'
                if not mime.startswith('image/'):
                    return "", f"{os.path.basename(path)} 不是图片文件，已跳过"
            return f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}", ""

    # 4) 裸 base64 文本
    compact = re.sub(r'\s+', '', text)
    if len(compact) >= 64 and _BASE64_RE.match(compact):
        if photo_payload_size(f"data:image/jpeg;base64,{compact}") > PHOTO_MAX_BYTES:
            return "", f"照片数据超过 {PHOTO_MAX_BYTES // 1024 // 1024}MB 上限，已跳过"
        return f"data:image/jpeg;base64,{compact}", ""

    return "", f"无法识别的照片值（既不是文件路径、链接，也不是 base64 数据）: {text[:40]}"


def decode_base64_payload(payload: str) -> bytes:
    """
    解码 data URI 中的 base64 负载。

    VCF / Excel 里的 base64 常因折行或截断导致长度不是 4 的倍数，
    此时即使 `validate=False` 也会抛 binascii.Error("Incorrect padding")。
    这里先去掉空白字符，再按需补齐 '=' 后解码。

    Args:
        payload: base64 文本（可含空白）

    Returns:
        解码后的字节
    """
    compact = re.sub(r'\s+', '', payload or "")
    compact += '=' * (-len(compact) % 4)
    return base64.b64decode(compact, validate=False)


def summarize_photo(photo: str) -> str:
    """
    生成照片的可读摘要（详情界面用，避免在界面上渲染超大 base64）。

    Args:
        photo: 规范化的照片值

    Returns:
        摘要文本，如 "内嵌JPEG 约 51.3 KB"
    """
    if not photo:
        return ""
    if is_remote_uri(photo):
        return f"链接 {photo}"

    parsed = parse_data_uri(photo)
    if not parsed:
        return photo[:60]

    mime, _ = parsed
    size_kb = photo_payload_size(photo) / 1024
    return f"内嵌{os.path.basename(mime).upper() or 'IMAGE'} 约 {size_kb:.1f} KB"
