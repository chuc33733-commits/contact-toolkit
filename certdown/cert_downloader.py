#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cert_downloader.py —— 腾讯在线文档（智能表格 / 收集表）证书图片批量下载工具

功能
----
1. 读取腾讯文档智能表格（docs.qq.com/smartsheet/<doc_id>），自动解析每条记录的
   「姓名」「身份证号」以及「证书图片附件」。
2. 批量向腾讯文档后台换取附件的临时签名下载地址。
3. 并发下载图片，并以「姓名+身份证号」重命名保存到指定输出目录，同时产出 CSV 报告。

实现原理（无需登录、无需开放平台密钥）
--------------------------------------
本脚本复刻了腾讯文档前端页面使用的两个内部 HTTP 接口：

  A. 表格数据
     GET https://docs.qq.com/dop-api/opendoc
         ?id=<doc_id>&tab=<sheet_id>&startrow=0&endrow=<max_row>
          &outformat=1&normal=1&wb=1&nowb=0&noEscape=1
     -> JSON 中 clientVars.collab_client_vars.initialAttributedText.text[*].smartsheet
        是 "base64(zlib 压缩)" 的 JSON 字符串，解压后即整张表的字段定义与单元格数据。

  B. 附件下载地址
     POST https://docs.qq.com/v2/file/attachment/detail
     body: {"access_type": 0|1, "file_id": <内部 padId>, "obj_keys": [<附件哈希>, ...]}
     -> result.detail[]: [{objKey, downloadUrl|previewUrl, name, ext, ...}]
     单次最多 100 个 obj_key（前端常量 maxCount=100），脚本会自动分批。

  关键坑：file_id 必须用接口 A 返回的「内部 padId」（12 位，形如 ZGRkSKpZTWqq），
        而不是浏览器地址栏里的文档 ID（如 DWkdSa1NLcFpUV3Fx），否则返回 10016。
        下载地址有效期较短，换取后应尽快下载。

用法示例
--------
  # 基本用法：下载全部记录
  python cert_downloader.py --doc https://docs.qq.com/smartsheet/DWkdSa1NLcFpUV3Fx -o ./证书

  # 先试跑 5 条看看效果（不落盘）
  python cert_downloader.py --doc DWkdSa1NLcFpUV3Fx -o ./证书 --limit 5 --dry-run

  # 明确指定字段、姓名与身份证之间用下划线连接、16 并发
  python cert_downloader.py --doc DWkdSa1NLcFpUV3Fx -o ./证书 \
      --name-field "姓名（必填）" --id-field "身份证号（必填）" --sep "_" --concurrency 16

  # 自定义命名：按文档标题命名 / 按下载时间命名 / 加自定义前缀
  python cert_downloader.py --doc DWkdSa1NLcFpUV3Fx -o ./证书 --name-template "{title}"
  python cert_downloader.py --doc DWkdSa1NLcFpUV3Fx -o ./证书 --name-template "{name}_{datetime}"
  python cert_downloader.py --doc DWkdSa1NLcFpUV3Fx -o ./证书 \
      --name-template "{prefix}{name}_{id}" --prefix "2026继续教育_"

  # 查看所有命名变量与预设方案
  python cert_downloader.py --list-vars

  # 文档非公开（需要登录态）时，从浏览器复制 Cookie 传入
  python cert_downloader.py --doc DWkdSa1NLcFpUV3Fx -o ./证书 --cookie "uin=xxx; skey=xxx"

  # 结果报告换成 Excel / JSON（默认 CSV）
  python cert_downloader.py --doc <ID> -o ./证书 --report-format xlsx

结果报告走 exporter.py 通用导出层，格式（csv/xlsx/json/txt/md/tsv/jsonl）与
字段映射都可在不改代码的前提下配置，详见 exporter.py 文档字符串。

依赖：仅 Python 标准库（3.8+），无需 pip install。
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import logging
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

import exporter                      # 通用导出层（同目录，纯标准库）

__version__ = "1.2.0"

# --------------------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------------------

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

OPENDOC_URL = "https://docs.qq.com/dop-api/opendoc"
ATTACHMENT_DETAIL_URL = "https://docs.qq.com/v2/file/attachment/detail"

ACCESS_TYPE_DOWNLOAD = 0   # 原图 / 原文件下载地址
ACCESS_TYPE_PREVIEW = 1    # 预览地址（图片可能被压缩）

ATTACHMENT_BATCH_SIZE = 100   # 与前端常量 maxCount 一致，超过会失败
FIELD_TYPE_TEXT = 1
FIELD_TYPE_ATTACHMENT = 6

#: 附件筛选模式。``image`` = 只下载图片（历史行为，桌面版默认）；``all`` = 任意格式附件都下载
#: （PDF / Word / Excel / 压缩包 / 音频视频等），供「证书可能是扫描件或文档」的场景使用。
KINDS_IMAGE = "image"
KINDS_ALL = "all"
KINDS_CHOICES = (KINDS_IMAGE, KINDS_ALL)

RECORD_ID_RE = re.compile(r"^fr\d+$")           # 记录 ID：fr1 / fr2 ...
INVALID_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]')
ID_CARD_RE = re.compile(r"^(\d{17}[\dXx]|\d{15})$")
IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "bmp", "webp", "heic", "tif", "tiff"}

log = logging.getLogger("cert")
_print_lock = threading.Lock()


# --------------------------------------------------------------------------------------
# 异常
# --------------------------------------------------------------------------------------

class DocError(RuntimeError):
    """文档读取 / 解析失败。"""


class DownloadError(RuntimeError):
    """图片下载失败。"""


# --------------------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------------------

@dataclass
class FieldDef:
    """表格列定义。"""
    field_id: str
    title: str
    ftype: int

    @property
    def is_attachment(self) -> bool:
        return self.ftype == FIELD_TYPE_ATTACHMENT


@dataclass
class Attachment:
    """单元格中的一个附件。"""
    obj_key: str    # 32 位哈希，用于换取下载地址
    name: str       # 原始文件名
    ext: str        # 扩展名（jpg / png / ...）
    size: int
    kind: str       # image / file ...

    @property
    def is_image(self) -> bool:
        return self.kind == "image" or self.ext.lower() in IMAGE_EXTS


@dataclass
class Record:
    """表格中的一行。cells 以「字段标题 -> 单元格」形式存放。"""
    record_id: str
    cells: Dict[str, dict] = field(default_factory=dict)


@dataclass
class Job:
    """一个待下载任务。"""
    index: int
    record_id: str
    name: str
    id_no: str
    attachment: Attachment
    seq: int = 1          # 同一记录多张图时的序号
    n_total: int = 1
    url: str = ""
    saved_as: str = ""
    status: str = "pending"
    error: str = ""

    @property
    def stem(self) -> str:
        """文件名主体：「姓名+身份证号」，同一记录多个附件时追加 _2/_3。"""
        base = f"{self.name}{self.id_no}"
        return base if self.n_total == 1 else f"{base}_{self.seq}"


# --------------------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------------------

def http_get_bytes(url: str,
                   headers: Optional[Dict[str, str]] = None,
                   timeout: float = 30.0,
                   retries: int = 3) -> bytes:
    """带重试的 HTTP 请求。

    Args:
        url: 目标地址。
        headers: 附加请求头。
        timeout: 单次超时秒数。
        retries: 最大尝试次数（仅 5xx 与网络异常会重试）。

    Returns:
        响应体字节。

    Raises:
        DocError: 重试耗尽后仍失败。
    """
    hdr = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    if headers:
        hdr.update(headers)

    last: Optional[BaseException] = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=hdr)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            last = exc
            if not (500 <= exc.code < 600) or attempt == retries:
                raise DocError(f"HTTP {exc.code} 请求失败：{url}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last = exc
            if attempt == retries:
                break
        time.sleep(0.8 * attempt)     # 线性退避，避免打爆接口
    raise DocError(f"请求失败（已重试 {retries} 次）：{url} -> {last}")


def http_post_json(url: str,
                   payload: dict,
                   headers: Optional[Dict[str, str]] = None,
                   timeout: float = 30.0,
                   retries: int = 3) -> dict:
    """POST JSON 并把响应解析为 dict。"""
    hdr = {"User-Agent": UA, "Content-Type": "application/json",
           "Origin": "https://docs.qq.com", "Accept-Language": "zh-CN,zh;q=0.9"}
    hdr.update(headers or {})

    last: Optional[BaseException] = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                         headers=hdr, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last = exc
            if not (500 <= exc.code < 600) or attempt == retries:
                raise DocError(f"HTTP {exc.code} 请求失败：{url}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError,
                json.JSONDecodeError) as exc:
            last = exc
            if attempt == retries:
                break
        time.sleep(0.8 * attempt)
    raise DocError(f"请求失败（已重试 {retries} 次）：{url} -> {last}")


# --------------------------------------------------------------------------------------
# 腾讯文档客户端
# --------------------------------------------------------------------------------------

class TencentSmartsheetClient:
    """只读的腾讯智能表格客户端（复刻前端内部接口）。

    Attributes:
        doc_id: 浏览器地址栏中的文档 ID。
        cookie: 可选 Cookie，用于访问需要登录的文档。
        timeout: 网络超时秒数。
        retries: 网络重试次数。
    """

    def __init__(self,
                 doc_id: str,
                 cookie: Optional[str] = None,
                 timeout: float = 30.0,
                 retries: int = 3) -> None:
        self.doc_id = doc_id
        self.timeout = timeout
        self.retries = retries
        self._headers = {
            "Referer": f"https://docs.qq.com/smartsheet/{doc_id}",
            "Origin": "https://docs.qq.com",
        }
        if cookie:
            self._headers["Cookie"] = cookie
        self._pad_id_cache: Dict[str, str] = {}

    # ---------- 工具 ----------

    @staticmethod
    def parse_doc_id(doc: str) -> str:
        """从 URL 或裸 ID 中提取文档 ID。

        Args:
            doc: https://docs.qq.com/smartsheet/XXXX 或 XXXX 或带 ?id= 的链接。

        Raises:
            DocError: 无法解析。
        """
        doc = doc.strip()
        for pattern in (r"/(?:smartsheet|sheet|form)/([A-Za-z0-9_\-]+)",
                        r"[?&]id=([A-Za-z0-9_\-]+)"):
            m = re.search(pattern, doc)
            if m:
                return m.group(1)
        if re.fullmatch(r"[A-Za-z0-9_\-]{8,}", doc):
            return doc
        raise DocError(f"无法从输入中解析文档 ID：{doc!r}")

    def _opendoc(self,
                 sheet_id: str = "",
                 start_row: int = 0,
                 end_row: Optional[int] = None) -> dict:
        """调用 /dop-api/opendoc 拉取表格原始数据。"""
        params = {
            "noEscape": "1",
            "enableSmartsheetSplit": "1",
            "supportOptimizedVer": "4",
            "chunkCellSize": "15000",
            "enableChunkRank": "1",
            "id": self.doc_id,
            "normal": "1",
            "outformat": "1",
            "wb": "1",
            "nowb": "0",
            "viewId": "fv1",
            "startrow": str(start_row),
        }
        if sheet_id:
            params["tab"] = sheet_id
        if end_row is not None:
            params["endrow"] = str(end_row)
        url = f"{OPENDOC_URL}?{urllib.parse.urlencode(params)}"
        raw = http_get_bytes(url, headers=self._headers,
                             timeout=self.timeout, retries=self.retries)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DocError(f"opendoc 响应不是合法 JSON（可能被重定向到登录页）：{url}") from exc

    @staticmethod
    def _collab(raw: dict) -> dict:
        """取出 collab_client_vars 并做完整性校验。"""
        try:
            return raw["clientVars"]["collab_client_vars"]
        except (KeyError, TypeError) as exc:
            raise DocError(
                "接口返回中缺少 clientVars.collab_client_vars。"
                "可能原因：文档 ID 错误、文档已删除、或未公开且未提供 Cookie") from exc

    # ---------- 子表 ----------

    def list_sheets(self) -> List[Dict[str, str]]:
        """列出文档中所有子表。

        Returns:
            [{"id": "ss_xxx", "name": "子表名"}, ...]
        """
        collab = self._collab(self._opendoc())
        sheets: List[Dict[str, str]] = []
        for group in collab.get("header") or []:
            for item in (group or {}).get("d") or []:
                if item.get("id"):
                    sheets.append({"id": item["id"], "name": item.get("name", "")})
        if not sheets and collab.get("padSubId"):
            sheets.append({"id": collab["padSubId"], "name": collab.get("padTitle", "")})
        return sheets

    def get_pad_id(self, sheet_id: str) -> str:
        """获取内部 padId（换取附件下载地址时必须使用它）。

        注意它不是浏览器地址里的文档 ID，而是 12 位内部 ID。
        """
        if sheet_id not in self._pad_id_cache:
            collab = self._collab(self._opendoc(sheet_id=sheet_id, start_row=0, end_row=1))
            self._pad_id_cache[sheet_id] = collab.get("padId") or ""
        pad_id = self._pad_id_cache[sheet_id]
        if not pad_id:
            raise DocError("未能获取内部 padId，无法换取附件下载地址")
        return pad_id

    # ---------- 表格数据 ----------

    def fetch_table(self, sheet_id: str) -> Tuple[List[FieldDef], List[Record]]:
        """拉取指定子表的字段定义与全部记录。

        Returns:
            (字段定义列表, 记录列表)

        Raises:
            DocError: 行数异常或结构解析失败。
        """
        probe = self._collab(self._opendoc(sheet_id=sheet_id, start_row=0, end_row=1))
        max_row = int(probe.get("maxRow") or 0)
        if max_row <= 0:
            raise DocError("接口未返回有效行数（maxRow=0），文档可能为空或无访问权限")

        collab = self._collab(self._opendoc(sheet_id=sheet_id, start_row=0, end_row=max_row))
        nodes = self._decode_payload(collab)
        return self._parse_payload(nodes)

    # ---------- 附件地址 ----------

    def fetch_attachment_urls(self,
                              pad_id: str,
                              obj_keys: Sequence[str],
                              access_type: int = ACCESS_TYPE_DOWNLOAD) -> Dict[str, str]:
        """批量换取附件的临时签名下载地址。

        Args:
            pad_id: 内部 padId（12 位）。
            obj_keys: 附件哈希列表。
            access_type: 0=下载地址（原图），1=预览地址。

        Returns:
            {obj_key: url}（取不到的键不会出现在结果中）。
        """
        result: Dict[str, str] = {}
        keys = list(dict.fromkeys(k for k in obj_keys if k))   # 去重保序
        for i in range(0, len(keys), ATTACHMENT_BATCH_SIZE):
            batch = keys[i:i + ATTACHMENT_BATCH_SIZE]
            resp = http_post_json(
                ATTACHMENT_DETAIL_URL,
                {"access_type": access_type, "file_id": pad_id, "obj_keys": batch},
                headers=self._headers, timeout=self.timeout, retries=self.retries,
            )
            code = resp.get("cgicode", resp.get("retcode", -1))
            if code != 0:
                raise DocError(f"获取附件地址失败：cgicode={code} msg={resp.get('msg')}")
            for item in (resp.get("result") or {}).get("detail") or []:
                url = item.get("downloadUrl") or item.get("previewUrl") or ""
                if url:
                    result[item.get("objKey", "")] = url
            log.debug("附件地址批次 %d-%d 完成", i, i + len(batch))
        return result

    # ---------- 解码 / 解析 ----------

    @staticmethod
    def _decode_payload(collab: dict) -> List[Any]:
        """把 base64+zlib 压缩的 smartsheet 载荷解成 Python 对象列表。"""
        chunks = (collab.get("initialAttributedText") or {}).get("text") or []
        nodes: List[Any] = []
        for chunk in chunks:
            blob = chunk.get("smartsheet") if isinstance(chunk, dict) else None
            if not blob:
                continue
            try:
                raw = base64.b64decode(blob + "=" * (-len(blob) % 4))
                text = zlib.decompress(raw).decode("utf-8")
            except (binascii.Error, zlib.error, UnicodeDecodeError, ValueError) as exc:
                raise DocError(f"smartsheet 载荷解压失败：{exc}") from exc
            try:
                nodes.append(json.loads(text))
            except json.JSONDecodeError as exc:
                raise DocError(f"smartsheet 载荷不是合法 JSON：{exc}") from exc
        if not nodes:
            raise DocError("未取到任何表格数据分片")
        return nodes

    @staticmethod
    def _walk(obj: Any) -> Iterable[Any]:
        """深度优先遍历所有 dict / list 节点。"""
        stack: List[Any] = [obj]
        while stack:
            cur = stack.pop()
            yield cur
            if isinstance(cur, dict):
                stack.extend(cur.values())
            elif isinstance(cur, list):
                stack.extend(cur)

    @staticmethod
    def _find_field_map(node: Any) -> Dict[str, dict]:
        """定位「字段定义表」：{field_id: {'k30': 标题, 'k31': 类型}}。

        用特征匹配而非固定路径，以兼容腾讯前端结构微调：
        所有 value 都是 dict 且同时含字符串 k30 与整数 k31。
        """
        best: Dict[str, dict] = {}
        for cur in TencentSmartsheetClient._walk(node):
            if not isinstance(cur, dict) or not cur:
                continue
            vals = list(cur.values())
            if not all(isinstance(v, dict) and isinstance(v.get("k30"), str)
                       and isinstance(v.get("k31"), int)
                       and not isinstance(v.get("k31"), bool) for v in vals):
                continue
            if any(RECORD_ID_RE.match(str(k)) for k in cur):
                continue                      # 排除记录容器
            if len(cur) > len(best):
                best = cur
        return best

    @classmethod
    def _find_record_map(cls, node: Any, field_ids: set) -> Dict[str, dict]:
        """定位「记录容器」：{frN: {'k1': {field_id: 单元格}}}。

        载荷中存在多个 key 形如 frN 的字典（单元格值、修改时间、列顺序等），
        这里用「内层 key 是否与字段 ID 集合相交」打分辨识真正的单元格表。
        """
        best: Dict[str, dict] = {}
        best_score = 0
        for cur in cls._walk(node):
            if not isinstance(cur, dict) or not cur:
                continue
            if not all(RECORD_ID_RE.match(str(k)) for k in cur):
                continue
            score = 0
            for val in cur.values():
                inner = val.get("k1") if isinstance(val, dict) else None
                if isinstance(inner, dict) and (set(inner) & field_ids):
                    score += 1
            if score > best_score:
                best, best_score = cur, score
        return best

    @classmethod
    def _parse_payload(cls, nodes: Iterable[Any]) -> Tuple[List[FieldDef], List[Record]]:
        """解析出字段定义与记录（单元格按字段标题索引）。"""
        field_map: Dict[str, dict] = {}
        record_map: Dict[str, dict] = {}

        for node in nodes:
            fm = cls._find_field_map(node)
            if len(fm) > len(field_map):
                field_map = fm
        if not field_map:
            raise DocError("未能定位字段定义，文档结构可能已变化")

        field_ids = set(field_map)
        for node in nodes:
            rm = cls._find_record_map(node, field_ids)
            if len(rm) > len(record_map):
                record_map = rm
        if not record_map:
            raise DocError("未能定位记录数据，文档结构可能已变化")

        fields = [FieldDef(fid, str(d.get("k30")), int(d.get("k31")))
                  for fid, d in field_map.items()]
        title_of = {fid: str(d.get("k30")) for fid, d in field_map.items()}

        records: List[Record] = []
        for rid, val in record_map.items():
            inner = val.get("k1") if isinstance(val, dict) else None
            cells = {title_of[fid]: cell
                     for fid, cell in (inner or {}).items() if fid in title_of}
            records.append(Record(record_id=rid, cells=cells))

        # 保持表格原有行序（fr1, fr2, fr10 ... 需按数字排序）
        records.sort(key=lambda r: int(r.record_id[2:]) if r.record_id[2:].isdigit() else 0)
        return fields, records


# --------------------------------------------------------------------------------------
# 单元格取值
# --------------------------------------------------------------------------------------

def cell_to_text(cell: Optional[dict]) -> str:
    """文本单元格 -> 字符串。

    形如：{"k1": [{"k1": "text", "k2": "张三"}], "k30": 1}
    """
    if not isinstance(cell, dict):
        return ""
    items = cell.get("k1")
    if isinstance(items, list):
        return "".join(str(it.get("k2", "")) for it in items
                       if isinstance(it, dict)).strip()
    if isinstance(items, str):
        return items.strip()
    return ""


def cell_to_attachments(cell: Optional[dict]) -> List[Attachment]:
    """附件单元格 -> Attachment 列表。

    形如：{"k6": [{"k5": "image", "k1": "a.jpg", "k2": 98744,
                   "k4": "94f5...630", "k3": "jpg"}], "k30": 6}
    """
    if not isinstance(cell, dict):
        return []
    items = cell.get("k6")
    if not isinstance(items, list):
        return []
    out: List[Attachment] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        obj_key = str(it.get("k4") or "")
        if not obj_key:
            continue
        try:
            size = int(it.get("k2") or 0)
        except (TypeError, ValueError):
            size = 0
        out.append(Attachment(
            obj_key=obj_key,
            name=str(it.get("k1") or ""),
            ext=str(it.get("k3") or "").lower().lstrip("."),
            size=size,
            kind=str(it.get("k5") or ""),
        ))
    return out


# --------------------------------------------------------------------------------------
# 字段匹配
# --------------------------------------------------------------------------------------

def pick_field(fields: Sequence[FieldDef],
               wanted: Optional[str],
               keywords: Sequence[str],
               role: str) -> FieldDef:
    """挑选目标字段：优先用户指定（精确 -> 包含），否则按关键词模糊匹配。

    Raises:
        DocError: 找不到匹配字段。
    """
    titles = [f.title for f in fields]
    if wanted:
        for f in fields:
            if f.title == wanted:
                return f
        for f in fields:
            if wanted in f.title:
                return f
        raise DocError(f"找不到{role}字段 {wanted!r}；文档中的字段有：{titles}")
    for kw in keywords:
        for f in fields:
            if kw in f.title:
                return f
    raise DocError(f"无法自动识别{role}字段（关键词 {list(keywords)}），"
                   f"请用 --name-field / --id-field / --image-field 指定；"
                   f"文档中的字段有：{titles}")


def pick_attachment_field(fields: Sequence[FieldDef], wanted: Optional[str]) -> FieldDef:
    """挑选附件字段：用户指定 > 附件类型列 > 关键词兜底。"""
    if wanted:
        return pick_field(fields, wanted, (), "附件")
    for f in fields:
        if f.is_attachment:
            return f
    return pick_field(fields, None, ("证书", "附件", "图片", "照片", "材料", "证明"), "附件")


# --------------------------------------------------------------------------------------
# 文件名 / 校验
# --------------------------------------------------------------------------------------

def sanitize_filename(name: str, repl: str = "_", max_len: int = 120) -> str:
    """清洗成合法的 Windows / macOS 文件名。"""
    name = INVALID_CHARS.sub(repl, name)
    name = re.sub(r"\s+", " ", name).strip().strip(". ")
    return name[:max_len] or "unnamed"


def mask_id(no: str) -> str:
    """身份证脱敏，仅用于日志输出，避免明文泄露。"""
    no = no or ""
    return f"{no[:4]}{'*' * max(0, len(no) - 8)}{no[-4:]}" if len(no) >= 8 else "*" * len(no)


def is_valid_id(no: str) -> bool:
    """粗校验身份证号：18 位（末位可 X）或 15 位数字。"""
    return bool(ID_CARD_RE.match((no or "").strip().upper()))


def _guess_ext(url: str) -> str:
    """从 URL 路径推断扩展名。"""
    ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lstrip(".").lower()
    return ext if ext.isalnum() and len(ext) <= 5 else ""


# 图片魔数 -> 真实扩展名。用于纠正「文件名叫 .png 实际是 JPEG」这类上传端命名错误。
MAGIC_EXTS: Tuple[Tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"BM", "bmp"),
    (b"II*\x00", "tif"),
    (b"MM\x00*", "tif"),
)


def sniff_ext(data: bytes) -> str:
    """按魔数判断图片真实格式。

    Args:
        data: 文件头部字节（>= 12 字节即可）。

    Returns:
        扩展名小写；无法识别时返回空串。
    """
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"hevc", b"mif1"):
        return "heic"
    for magic, ext in MAGIC_EXTS:
        if data.startswith(magic):
            return ext
    return ""



def _unique_path(path: Path, overwrite: bool) -> Path:
    """同名冲突时追加 _2 / _3 ...（同一人重复提交的场景）。"""
    if overwrite or not path.exists():
        return path
    i = 2
    while True:
        cand = path.with_name(f"{path.stem}_{i}{path.suffix}")
        if not cand.exists():
            return cand
        i += 1


# 本批次已分配出去的文件名（小写）。并发下两个线程可能同时判定「目标文件不存在」，
# 仅靠磁盘探测会双双写同一个路径，后写的覆盖先写的——用内存集合 + 锁把「探测 + 占位」原子化。
_path_lock = threading.Lock()
_allocated: set = set()


def allocate_path(out_dir: Path,
                  stem: str,
                  ext: str,
                  overwrite: bool,
                  pre_existing: Optional[set] = None) -> Tuple[Path, bool]:
    """为一次下载分配落盘路径（并发安全）。

    区分两种「同名」场景，语义完全不同：
      * 程序**启动前**目录里就有的同名文件 -> 判定为上次跑过，直接跳过（断点续跑）
      * 本批次内后来者撞名（同一人重复提交多条记录） -> 追加 _2 / _3，两份都保留

    Args:
        out_dir: 输出目录。
        stem: 文件名主体。
        ext: 扩展名。
        overwrite: 是否覆盖已存在文件。
        pre_existing: 下载开始前输出目录内已有的文件名集合（小写）。

    Returns:
        ``(路径, 是否应跳过)``。跳过时路径为原本已存在的文件。
    """
    base = out_dir / f"{stem}.{ext}"
    pre = pre_existing or set()
    with _path_lock:
        if base.name.lower() in pre and not overwrite:
            return base, True
        if overwrite:
            _allocated.add(base.name.lower())
            return base, False
        target, i = base, 2
        while target.exists() or target.name.lower() in _allocated:
            target = out_dir / f"{stem}_{i}.{ext}"
            i += 1
        _allocated.add(target.name.lower())
        return target, False


def reset_allocated() -> None:
    """清空本批次已分配文件名集合。

    每批任务开始前调用一次；否则同一进程内跑第二个输出目录时，
    会因残留记录把同名文件误判成冲突而写成 _2。
    """
    with _path_lock:
        _allocated.clear()


def snapshot_existing(out_dir: Path, overwrite: bool = False) -> set:
    """下载开始前快照输出目录已有文件名（小写）。

    Args:
        out_dir: 输出目录。
        overwrite: 覆盖模式下不需要快照（一律覆盖，不做跳过判定）。

    Returns:
        文件名集合；覆盖模式下返回空集合。
    """
    if overwrite or not out_dir.exists():
        return set()
    return {p.name.lower() for p in out_dir.iterdir()}


# --------------------------------------------------------------------------------------
# 命名模板引擎
# --------------------------------------------------------------------------------------
# 用自定义模板而非硬编码规则，让用户能自由组合「姓名 / 身份证 / 文档标题 / 下载时间 / 自定义前缀」。
# 采用 re 替换而不是 str.format：模板里出现单个花括号或用户自定义文本时 format 会抛异常。

DEFAULT_NAME_TEMPLATE = "{name}{sep}{id}{seq_suffix}"

#: 变量名 -> 说明，GUI 与 --list-vars 共用同一份数据源，避免两处不同步。
NAME_VARIABLES: Tuple[Tuple[str, str], ...] = (
    ("name", "姓名"),
    ("id", "身份证号（完整）"),
    ("id_masked", "身份证号（脱敏，中间打星）"),
    ("id_last4", "身份证号后 4 位"),
    ("title", "文档/附件标题（上传时的原始文件名，不含扩展名）"),
    ("prefix", "自定义前缀"),
    ("sep", "姓名与身份证之间的分隔符（沿用 --sep）"),
    ("date", "下载日期 20260909"),
    ("time", "下载时间 125030"),
    ("datetime", "下载日期时间 20260909_125030"),
    ("index", "记录行号（从 1 开始）"),
    ("seq", "同一记录多个附件时的序号（1,2,3…）"),
    ("seq_suffix", "同一记录多个附件时自动追加 _2/_3，单个时为空"),
    ("record", "记录 ID（fr1、fr2…）"),
)

#: 预设命名方案，GUI 下拉框直接读取这里。
NAME_PRESETS: Tuple[Tuple[str, str], ...] = (
    ("姓名+身份证号（推荐）", "{name}{sep}{id}{seq_suffix}"),
    ("姓名_身份证号", "{name}_{id}{seq_suffix}"),
    ("身份证号", "{id}{seq_suffix}"),
    ("姓名", "{name}{seq_suffix}"),
    ("文档标题（原始文件名）", "{title}{seq_suffix}"),
    ("下载时间", "{datetime}{seq_suffix}"),
    ("姓名_下载时间", "{name}_{datetime}{seq_suffix}"),
    ("自定义前缀+姓名+身份证号", "{prefix}{name}{sep}{id}{seq_suffix}"),
    ("自定义前缀+文档标题", "{prefix}{title}{seq_suffix}"),
    ("序号_姓名", "{index:03}_{name}{seq_suffix}"),
)

_VAR_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)(?::([^}]*))?\}")


def build_stem(job: "Job",
               template: str = DEFAULT_NAME_TEMPLATE,
               prefix: str = "",
               sep: str = "",
               now: Optional[datetime] = None,
               sample: bool = False) -> str:
    """按模板渲染文件名主体（不含扩展名、不含非法字符清洗）。

    Args:
        job: 下载任务。
        template: 命名模板，如 ``{name}_{id}``。
        prefix: 自定义前缀，供 ``{prefix}`` 变量使用。
        sep: ``{sep}`` 变量的值（对应旧的 --sep）。
        now: 时间基准；None 表示取当前时间（预览与真实下载统一走这里）。
        sample: 预览模式。此时姓名/身份证使用示例值，避免预览泄露真实隐私数据。

    Returns:
        文件名主体字符串（尚未清洗非法字符）。
    """
    moment = now or datetime.now()
    src_name = "张三" if sample else job.name
    src_id = "510101200001011234" if sample else job.id_no
    # 附件原始文件名去掉扩展名；为空时退化成「姓名」，避免出现空文件名
    title = os.path.splitext(job.attachment.name)[0].strip() if job.attachment.name else ""
    if not title:
        title = src_name or job.record_id

    ctx: Dict[str, str] = {
        "name": src_name,
        "id": src_id,
        "id_masked": mask_id(src_id),
        "id_last4": src_id[-4:] if src_id else "",
        "title": title,
        "prefix": prefix,
        "sep": sep,
        "date": moment.strftime("%Y%m%d"),
        "time": moment.strftime("%H%M%S"),
        "datetime": moment.strftime("%Y%m%d_%H%M%S"),
        "index": str(job.index),
        "seq": str(job.seq),
        # 第 1 张不加后缀（保持「张三xxx.jpg」），第 2 张起才追加 _2/_3，
        # 否则单图与多图的命名风格不一致，用户还要额外重命名
        "seq_suffix": "" if job.seq <= 1 else f"_{job.seq}",
        "record": job.record_id,
    }

    def _repl(m: "re.Match[str]") -> str:
        key = m.group(1).lower()
        if key not in ctx:
            return m.group(0)                      # 未知变量原样保留，方便用户发现拼写错误
        spec = m.group(2)
        if spec and key == "index":                # 支持 {index:03} 补零
            try:
                return format(int(ctx[key]), spec)
            except (ValueError, TypeError):
                return ctx[key]
        return ctx[key]

    return _VAR_RE.sub(_repl, template or DEFAULT_NAME_TEMPLATE)


def resolve_filename(job: "Job",
                     template: str = DEFAULT_NAME_TEMPLATE,
                     prefix: str = "",
                     sep: str = "",
                     now: Optional[datetime] = None) -> str:
    """渲染模板 -> 清洗非法字符 -> 拼上扩展名，得到最终文件名。"""
    stem = sanitize_filename(build_stem(job, template, prefix, sep, now))
    ext = (job.attachment.ext or "").lower().lstrip(".") or "jpg"
    return f"{stem}.{ext}"


# --------------------------------------------------------------------------------------
# 任务构建与下载
# --------------------------------------------------------------------------------------

def build_jobs(client: TencentSmartsheetClient,
               sheet_id: str,
               name_field: Optional[str],
               id_field: Optional[str],
               attach_field: Optional[str],
               limit: Optional[int],
               strict_id: bool,
               kinds: str = KINDS_IMAGE) -> Tuple[List[Job], List[FieldDef]]:
    """拉取表格并构造下载任务。

    Args:
        kinds: ``image`` 只保留图片附件（历史行为）；``all`` 保留任意格式附件
            （PDF / Word / Excel / 压缩包等）。非图片附件在 ``image`` 模式下会被跳过。

    Returns:
        (任务列表, 字段定义列表)
    """
    fields, records = client.fetch_table(sheet_id)
    if not records:
        raise DocError("表格中没有解析到任何记录")

    f_name = pick_field(fields, name_field, ("姓名", "名字", "姓名（", "name"), "姓名")
    f_id = pick_field(fields, id_field, ("身份证", "证件号", "证件编号", "身份号码"), "身份证号")
    f_att = pick_attachment_field(fields, attach_field)
    log.info("字段匹配 -> 姓名：%r  身份证：%r  附件：%r  附件范围：%s",
             f_name.title, f_id.title, f_att.title,
             "仅图片" if kinds != KINDS_ALL else "全部文件")

    jobs: List[Job] = []
    skipped = 0
    for idx, rec in enumerate(records, start=1):
        if limit and len(jobs) >= limit:
            break

        name = cell_to_text(rec.cells.get(f_name.title))
        id_no = cell_to_text(rec.cells.get(f_id.title)).strip().upper()
        atts = cell_to_attachments(rec.cells.get(f_att.title))
        if kinds != KINDS_ALL:
            atts = [a for a in atts if a.is_image]

        if not name or not id_no:
            skipped += 1
            log.warning("跳过记录 %s：姓名或身份证号为空（name=%r id=%s）",
                        rec.record_id, name, mask_id(id_no))
            continue
        if strict_id and not is_valid_id(id_no):
            skipped += 1
            log.warning("跳过记录 %s：身份证号格式不合法 %s", rec.record_id, mask_id(id_no))
            continue
        if not atts:
            skipped += 1
            log.warning("跳过记录 %s（%s %s）：没有%s附件",
                        rec.record_id, name, mask_id(id_no),
                        "证书图片" if kinds != KINDS_ALL else "")
            continue

        for seq, att in enumerate(atts, start=1):
            jobs.append(Job(index=idx, record_id=rec.record_id, name=name,
                            id_no=id_no, attachment=att,
                            seq=seq, n_total=len(atts)))

    log.info("解析完成：记录 %d 条 -> 下载任务 %d 个，跳过 %d 条",
             len(records), len(jobs), skipped)
    return jobs, fields


def download_one(job: Job,
                 client: TencentSmartsheetClient,
                 out_dir: Path,
                 sep: str = "",
                 overwrite: bool = False,
                 timeout: float = 60.0,
                 retries: int = 3,
                 name_template: str = DEFAULT_NAME_TEMPLATE,
                 prefix: str = "",
                 cancel: Optional[threading.Event] = None,
                 pre_existing: Optional[set] = None) -> Job:
    """下载单个附件并按命名模板保存（线程安全）。

    Args:
        job: 下载任务。
        client: 腾讯文档客户端（复用其请求头）。
        out_dir: 输出目录。
        sep: ``{sep}`` 变量值。
        overwrite: 是否覆盖同名文件。
        timeout: 单次请求超时秒数。
        retries: 失败重试次数。
        name_template: 命名模板，见 ``NAME_PRESETS``。
        prefix: 自定义前缀。
        cancel: 取消信号；GUI 点「停止」时置位，未开始的任务直接跳过。

    Returns:
        回填了 status / saved_as / error 的同一个 Job 对象。
    """
    try:
        if cancel is not None and cancel.is_set():
            job.status, job.error = "cancelled", "用户取消"
            return job
        if not job.url:
            raise DownloadError("未取到下载地址")

        stem = sanitize_filename(build_stem(job, name_template, prefix, sep))
        is_img = job.attachment.is_image
        # 非图片附件（PDF/Word/压缩包…）猜不到扩展名时退化成 .bin，不要硬塞 .jpg
        ext = job.attachment.ext or _guess_ext(job.url) or ("jpg" if is_img else "bin")
        target, skip = allocate_path(out_dir, stem, ext, overwrite, pre_existing)

        if skip:
            job.saved_as, job.status = target.name, "skipped"
            return job

        raw = http_get_bytes(job.url, headers=client._headers,
                             timeout=timeout, retries=retries)
        # 「响应体过小 = 错误页」只对图片成立；PDF/文档体积可以很小，不能据此判失败
        if is_img and len(raw) < 512:
            raise DownloadError(f"响应体仅 {len(raw)} 字节，可能不是有效图片")

        # 按魔数校正扩展名：上传端常把 JPEG 存成 .png，直接用原扩展名会导致看图软件打不开。
        # 只对图片做——非图片文件同样可能撞上图片魔数（如以 'BM' 开头的二进制），误改会打不开。
        real = sniff_ext(raw[:16]) if is_img else ""
        if real and real != ext:
            log.debug("扩展名校正 %s -> %s（%s）", ext, real, stem)
            ext = real
            target, skip = allocate_path(out_dir, stem, ext, overwrite, pre_existing)
            if skip:                      # 校正后的名字恰好是上次跑过的旧文件
                job.saved_as, job.status = target.name, "skipped"
                return job

        target.write_bytes(raw)

        job.saved_as, job.status = target.name, "ok"
    except Exception as exc:                       # noqa: BLE001 - 汇总进报告
        job.status, job.error = "failed", f"{type(exc).__name__}: {exc}"
    finally:
        with _print_lock:
            tag = {"ok": "OK  ", "skipped": "SKIP", "failed": "FAIL",
                   "cancelled": "CANC"}.get(job.status, "????")
            log.info("[%s] %-4d %-8s %s  %s", tag, job.index, job.name,
                     mask_id(job.id_no), job.saved_as or job.error)
    return job


# --------------------------------------------------------------------------------------
# 结果报告（走通用导出层，格式与字段均可配置）
# --------------------------------------------------------------------------------------

#: 下载报告的默认字段映射：key 是导出列名，sources 是候选取值路径（按顺序兜底）。
#: 同一份配置既能导出 CSV 也能导出 xlsx/json/txt，换格式不用改这里。
REPORT_MAPPING: Tuple[Dict[str, Any], ...] = (
    {"key": "序号", "sources": ("index",)},
    {"key": "记录ID", "sources": ("record_id", "recordId", "id")},
    {"key": "姓名", "sources": ("name", "姓名")},
    {"key": "身份证号", "sources": ("id_no", "idNo", "idNumber")},
    {"key": "原文件名", "sources": ("attachment.name", "attachmentName")},
    {"key": "保存文件名", "sources": ("saved_as", "savedAs", "savedAsName")},
    {"key": "状态", "sources": ("status",)},
    {"key": "错误信息", "sources": ("error", "errMsg"), "default": ""},
)

exporter.register_mapping("cert", REPORT_MAPPING)
#: 精简版映射：只要文件名对照关系，便于交给第三方核对
exporter.register_mapping("cert_minimal", (
    {"key": "姓名", "sources": ("name", "姓名")},
    {"key": "身份证号", "sources": ("id_no", "idNo")},
    {"key": "保存文件名", "sources": ("saved_as", "savedAs")},
    {"key": "状态", "sources": ("status",)},
))


def write_report(path: Path,
                 jobs: Sequence[Job],
                 fmt: Optional[str] = None,
                 mapping: Union[str, "exporter.FieldMap", Iterable[Any], None] = "cert"
                 ) -> "exporter.ExportResult":
    """把下载结果写成报告文件。

    Args:
        path: 目标路径；扩展名用于推断格式（除非显式给 fmt）。
        jobs: 下载任务列表。
        fmt: 导出格式名（csv/xlsx/json/txt/md/tsv/jsonl），None 时按扩展名推断。
        mapping: 字段映射；默认用预置的 ``"cert"``（与旧版 CSV 表头完全一致），
            也可传 ``"cert_minimal"``、FieldMap 或配置列表。

    Returns:
        ExportResult（含路径、格式、MIME、行数等元信息）。

    Examples:
        >>> write_report(Path("r.csv"), jobs)              # 默认 CSV（带 BOM）
        >>> write_report(Path("r.xlsx"), jobs)             # Excel
        >>> write_report(Path("r.json"), jobs, fmt="json") # JSON
    """
    result = exporter.export(jobs, path, fmt=fmt, mapping=mapping)
    log.info("报告已写入：%s", result)
    return result


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="cert_downloader.py",
        description="从腾讯在线文档智能表格批量下载证书图片，并以「姓名+身份证号」命名保存。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n"
               "  python cert_downloader.py --doc https://docs.qq.com/smartsheet/XXXX -o ./证书\n"
               "  python cert_downloader.py --doc XXXX -o ./证书 --limit 5 --dry-run\n",
    )
    # 不设为 required：--list-vars / --list-sheets 之外才需要它，main 里另行校验
    p.add_argument("--doc", default=None, help="腾讯文档 URL 或文档 ID（下载时必填）")
    p.add_argument("--output", "-o", default="./certificates",
                   help="图片输出目录（默认 ./certificates）")
    p.add_argument("--sheet", default=None, help="子表 ID 或名称；默认取第一个子表")
    p.add_argument("--name-field", default=None,
                   help="姓名字段标题；默认自动识别含「姓名/名字」的列")
    p.add_argument("--id-field", default=None,
                   help="身份证号字段标题；默认自动识别含「身份证/证件号」的列")
    p.add_argument("--image-field", default=None,
                   help="证书图片字段标题；默认取附件类型的列")
    p.add_argument("--kinds", choices=list(KINDS_CHOICES), default=KINDS_IMAGE,
                   help="附件范围：image=只下载图片（默认，保持历史行为）；"
                        "all=任意格式附件都下载（PDF/Word/Excel/压缩包等）")
    p.add_argument("--sep", default="",
                   help="姓名与身份证号之间的分隔符（默认空，即直接拼接）")
    p.add_argument("--name-template", default=None,
                   help="命名模板，如 '{name}_{id}'；默认 '{name}{sep}{id}{seq_suffix}'")
    p.add_argument("--prefix", default="", help="自定义前缀，配合模板中的 {prefix} 使用")
    p.add_argument("--list-vars", action="store_true",
                   help="列出命名模板可用变量与预设方案后退出")
    p.add_argument("--concurrency", "-j", type=int, default=8,
                   help="并发下载线程数（默认 8）")
    p.add_argument("--timeout", type=float, default=60.0,
                   help="单次请求超时秒数（默认 60）")
    p.add_argument("--retries", type=int, default=3,
                   help="网络失败重试次数（默认 3）")
    p.add_argument("--limit", type=int, default=None, help="只处理前 N 个任务（调试用）")
    p.add_argument("--overwrite", action="store_true", help="覆盖已存在文件（默认跳过）")
    p.add_argument("--access-type", type=int, default=ACCESS_TYPE_DOWNLOAD,
                   choices=[ACCESS_TYPE_DOWNLOAD, ACCESS_TYPE_PREVIEW],
                   help="0=原图下载地址（默认）；1=预览地址（可能被压缩）")
    p.add_argument("--no-strict-id", action="store_true",
                   help="不校验身份证号格式（默认跳过格式非法的记录）")
    p.add_argument("--cookie", default=None, help="浏览器 Cookie，用于访问需登录的文档")
    p.add_argument("--report", default=None,
                   help="结果报告路径（默认 输出目录/download_report.<格式>）")
    p.add_argument("--report-format", default=None,
                   help="报告格式：csv/xlsx/json/jsonl/txt/md/tsv（默认按扩展名推断，再默认 csv）")
    p.add_argument("--report-mapping", default="cert",
                   help="报告字段映射：cert(默认)/cert_minimal，或直接传 JSON 配置数组")
    p.add_argument("--dry-run", action="store_true", help="只解析不下载")
    p.add_argument("--list-sheets", action="store_true", help="只列出子表后退出")
    p.add_argument("--verbose", "-v", action="store_true", help="输出调试日志")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p.parse_args(argv)


def _resolve_sheet(sheets: Sequence[Dict[str, str]], wanted: Optional[str]) -> str:
    """按 ID 或名称确定子表 ID。"""
    if not wanted:
        return sheets[0]["id"]
    for s in sheets:
        if s["id"] == wanted or s["name"] == wanted:
            return s["id"]
    for s in sheets:
        if wanted in s["name"]:
            return s["id"]
    raise DocError(f"找不到子表 {wanted!r}；可用：{[s['name'] or s['id'] for s in sheets]}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    """命令行入口。

    Returns:
        0=全部成功；1=发生错误；2=有下载失败；130=用户中断。
    """
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")

    if args.list_vars:
        print("命名模板可用变量：")
        for var, desc in NAME_VARIABLES:
            print(f"  {{{var}}}".ljust(16) + desc)
        print("\n预设方案：")
        for label, tpl in NAME_PRESETS:
            print(f"  {label}".ljust(28) + tpl)
        return 0

    if not args.doc:
        print("错误：缺少 --doc 参数（腾讯文档 URL 或文档 ID）", file=sys.stderr)
        return 1

    try:
        doc_id = TencentSmartsheetClient.parse_doc_id(args.doc)
        client = TencentSmartsheetClient(doc_id, cookie=args.cookie,
                                        timeout=args.timeout, retries=args.retries)
        log.info("文档 ID：%s", doc_id)

        sheets = client.list_sheets()
        if not sheets:
            raise DocError("未获取到任何子表")
        if args.list_sheets:
            for s in sheets:
                print(f"{s['id']}\t{s['name']}")
            return 0

        sheet_id = _resolve_sheet(sheets, args.sheet)
        log.info("使用子表：%s（%s）", sheet_id,
                 next((s["name"] for s in sheets if s["id"] == sheet_id), ""))

        jobs, _ = build_jobs(client, sheet_id,
                             args.name_field, args.id_field, args.image_field,
                             args.limit, strict_id=not args.no_strict_id,
                             kinds=args.kinds)
        if not jobs:
            log.warning("没有生成任何下载任务")
            return 1

        tpl = args.name_template or DEFAULT_NAME_TEMPLATE
        if args.dry_run:
            for j in jobs[:30]:
                print(f"{j.index:>4}  {resolve_filename(j, tpl, args.prefix, args.sep)}"
                      f"   <- {j.attachment.name}")
            print(f"... 共 {len(jobs)} 个任务（--dry-run，未下载）")
            return 0

        pad_id = client.get_pad_id(sheet_id)
        log.debug("内部 padId：%s", pad_id)
        log.info("正在换取 %d 个附件的下载地址 ...", len({j.attachment.obj_key for j in jobs}))
        url_map = client.fetch_attachment_urls(
            pad_id, [j.attachment.obj_key for j in jobs], args.access_type)
        for j in jobs:
            j.url = url_map.get(j.attachment.obj_key, "")
            if not j.url:
                j.status, j.error = "failed", "未取到下载地址"
        log.info("取到下载地址 %d/%d", sum(1 for j in jobs if j.url), len(jobs))

        out_dir = Path(args.output).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        log.info("输出目录：%s", out_dir)
        pre_existing = snapshot_existing(out_dir, args.overwrite)
        reset_allocated()

        pending = [j for j in jobs if j.url]
        log.info("开始下载（并发 %d）...", args.concurrency)
        t0 = time.time()
        worker = partial(download_one, client=client, out_dir=out_dir, sep=args.sep,
                         overwrite=args.overwrite, timeout=args.timeout,
                         retries=args.retries, name_template=tpl, prefix=args.prefix,
                         pre_existing=pre_existing)
        with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
            futs = [pool.submit(worker, j) for j in pending]
            for fut in as_completed(futs):
                fut.result()

        ok = sum(1 for j in jobs if j.status == "ok")
        skip = sum(1 for j in jobs if j.status == "skipped")
        fail = sum(1 for j in jobs if j.status == "failed")
        canc = sum(1 for j in jobs if j.status == "cancelled")
        log.info("完成：成功 %d / 跳过 %d / 失败 %d / 取消 %d，用时 %.1f 秒",
                 ok, skip, fail, canc, time.time() - t0)

        fmt = (args.report_format or "").lower() or None
        ext = exporter.get_format(fmt).ext if fmt else (
            Path(args.report).suffix.lstrip(".") if args.report else "csv")
        report_path = Path(args.report) if args.report else out_dir / f"download_report.{ext}"

        mapping: Any = (args.report_mapping or "cert").strip()
        if mapping.startswith(("[", "{")):          # 允许命令行直接塞一份 JSON 字段配置
            try:
                mapping = json.loads(mapping)
            except json.JSONDecodeError as exc:
                raise DocError(f"--report-mapping 不是合法 JSON：{exc}") from exc
        write_report(report_path, jobs, fmt=fmt, mapping=mapping)
        return 0 if fail == 0 else 2

    except DocError as exc:
        log.error("文档处理失败：%s", exc)
        return 1
    except KeyboardInterrupt:
        log.warning("已中断")
        return 130
    except Exception as exc:                        # noqa: BLE001
        log.exception("未预期的错误：%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
