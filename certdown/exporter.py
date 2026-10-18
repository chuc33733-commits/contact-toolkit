#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""exporter.py —— 通用数据导出层（零第三方依赖，Python 3.8+）。

把「数据 -> 文件」这件事拆成两个可独立配置的维度：

1. **字段映射（Field / FieldMap）**：决定导出哪些列、叫什么、从哪取值。
   支持别名、多候选路径兜底、嵌套路径（``user.name`` / ``items[0].url``）、
   camelCase / snake_case / kebab-case / 中文表头自动适配、列顺序、缺失降级。

2. **导出格式（FormatSpec）**：决定文件长什么样。
   扩展名、MIME、编码、BOM、换行、写入器全部由配置决定；
   新增格式只需「写一个 writer 函数 + register_format」，核心逻辑零改动。

对外最常用的三个入口：

    export(records, "报告.xlsx")                  # 按扩展名自动选格式
    export(records, "报告.csv", mapping="cert")   # 用预置字段映射
    data, meta = export_bytes(records, fmt="json")  # Web 下载场景

内置格式：csv / tsv / json / jsonl / xlsx / txt / md。
"""

from __future__ import annotations

import csv
import datetime as _dt
import io
import json
import os
import re
import zipfile
from dataclasses import dataclass, field, is_dataclass, asdict as _asdict
from pathlib import Path
from typing import (Any, Callable, Dict, Iterable, List, Mapping, Optional,
                    Sequence, Tuple, Union)

__all__ = [
    "Field", "FieldMap", "Table", "FormatSpec", "ExportResult",
    "export", "export_bytes", "build_table", "register_format",
    "register_formatter", "get_format", "infer_format", "list_formats",
    "guess_columns", "download_headers",
]

__version__ = "1.0.0"


# --------------------------------------------------------------------------------------
# 一、取值：路径解析与命名风格自适应
# --------------------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"([^.\[\]]+)|\[(\d+)\]|\['([^']+)'\]|\[\"([^\"]+)\"\]")


def parse_path(path: str) -> List[Union[str, int]]:
    """把取值路径拆成 token 列表。

    支持 ``user.name``、``items[0].url``、``data['姓名']``、``a.b[2].c``。

    Args:
        path: 点号分隔的路径串，可带 ``[n]`` 索引或 ``['key']`` 下标。

    Returns:
        token 列表；字符串表示属性/键，整数表示下标。
    """
    tokens: List[Union[str, int]] = []
    for m in _TOKEN_RE.finditer(path):
        if m.group(1) is not None:
            tokens.append(m.group(1))
        elif m.group(2) is not None:
            tokens.append(int(m.group(2)))
        else:
            tokens.append(m.group(3) if m.group(3) is not None else m.group(4))
    return tokens


def to_snake(name: str) -> str:
    """``createdAt`` / ``Created-At`` -> ``created_at``。"""
    s = re.sub(r"[\s\-]+", "_", name)
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", s)
    return re.sub(r"_+", "_", s).strip("_").lower()


def to_camel(name: str, upper_first: bool = False) -> str:
    """``created_at`` -> ``createdAt``（或 ``CreatedAt``）。"""
    parts = [p for p in re.split(r"[_\-\s]+", name) if p]
    if not parts:
        return name
    head = parts[0][:1].upper() + parts[0][1:] if upper_first else parts[0].lower()
    return head + "".join(p[:1].upper() + p[1:] for p in parts[1:])


def name_variants(name: str) -> List[str]:
    """生成一个字段名的常见书写变体（用于跨命名风格匹配）。

    例：``createdAt`` -> ``['createdAt', 'created_at', 'created-at', 'createdat']``。
    """
    out: List[str] = []
    snake = to_snake(name)

    def add(x: str) -> None:
        if x and x not in out:
            out.append(x)

    add(name)
    add(snake)
    add(snake.replace("_", "-"))            # kebab-case
    add(to_camel(name))
    add(to_camel(name, upper_first=True))
    add(name.replace("_", "-"))
    add(name.replace("-", "_"))
    add(name.lower())
    return out


def _get_token(obj: Any, token: Union[str, int], variants: Sequence[str]) -> Tuple[bool, Any]:
    """在 obj 上按一个 token 取值，带命名变体回退。

    Returns:
        ``(是否命中, 值)``。
    """
    cands: List[Any] = [token]
    if isinstance(token, str):
        cands.extend(v for v in name_variants(token) if v != token)
    for cand in cands:
        if isinstance(obj, Mapping) and cand in obj:
            return True, obj[cand]
        if isinstance(obj, (list, tuple)) and isinstance(cand, int):
            if -len(obj) <= cand < len(obj):
                return True, obj[cand]
            continue
        if isinstance(cand, str) and hasattr(obj, cand):
            try:
                return True, getattr(obj, cand)
            except Exception:                            # noqa: BLE001 - 属性可能抛异常
                continue
    return False, None


def get_path(obj: Any, path: str, default: Any = None) -> Any:
    """按路径取值，逐段做命名风格回退；任一段取不到即返回 default。"""
    cur = obj
    for token in parse_path(path):
        if cur is None:
            return default
        hit, cur = _get_token(cur, token, ())
        if not hit:
            return default
    return cur


# --------------------------------------------------------------------------------------
# 二、字段映射
# --------------------------------------------------------------------------------------

#: 内置值转换器。formatter 可写成这些名字，也可以直接传 callable。
FORMATTERS: Dict[str, Callable[[Any], Any]] = {
    "str": lambda v: "" if v is None else str(v),
    "int": lambda v: (int(v) if str(v).strip().lstrip("-").isdigit() else 0),
    "float": lambda v: (float(v) if _is_number(v) else 0.0),
    "bool": lambda v: bool(v),
    "bool_zh": lambda v: "是" if v else "否",
    "upper": lambda v: "" if v is None else str(v).upper(),
    "lower": lambda v: "" if v is None else str(v).lower(),
    "strip": lambda v: "" if v is None else str(v).strip(),
    "join": lambda v: "、".join(str(x) for x in v) if isinstance(v, (list, tuple, set)) else v,
    "date": lambda v: _fmt_time(v, "%Y-%m-%d"),
    "datetime": lambda v: _fmt_time(v, "%Y-%m-%d %H:%M:%S"),
    "iso": lambda v: _fmt_time(v, "%Y-%m-%dT%H:%M:%S"),
    "size_kb": lambda v: f"{int(v) / 1024:.1f} KB" if _is_number(v) else v,
}


def register_formatter(name: str, fn: Callable[[Any], Any]) -> None:
    """注册一个值转换器，之后可在字段配置里用名字引用。

    Args:
        name: 转换器名称。
        fn: ``fn(value) -> new_value``。
    """
    FORMATTERS[name] = fn


def _is_number(v: Any) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _fmt_time(v: Any, fmt: str) -> Any:
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.strftime(fmt)
    if isinstance(v, (int, float)) and v > 10 ** 11:     # 毫秒时间戳
        return _dt.datetime.fromtimestamp(v / 1000).strftime(fmt)
    return v


@dataclass
class Field:
    """一列的配置。

    Attributes:
        key: 输出列名（表头文字）。
        sources: 候选取值路径，按顺序尝试，第一个「有效」的胜出——这就是跨命名风格
            与缺失字段的兜底机制。为空时退化为直接用 key 取名。
        default: 所有候选都取不到时的兜底值。
        formatter: 值转换器，可为 FORMATTERS 里的名字或 callable。
        skip_empty: 候选值为空串/None 时是否视为「没取到」继续尝试下一个候选。
        width: 仅 xlsx 使用的列宽提示（0 表示自动）。
    """

    key: str
    sources: Sequence[str] = ()
    default: Any = ""
    formatter: Optional[Union[str, Callable[[Any], Any]]] = None
    skip_empty: bool = True
    width: int = 0

    def resolve(self, record: Any) -> Any:
        """从一条记录里取出本列的值（含候选回退与转换）。"""
        for path in (self.sources or (self.key,)):
            value = get_path(record, path, _MISSING)
            if value is _MISSING:
                continue
            if self.skip_empty and (value is None or (isinstance(value, str) and not value.strip())):
                continue
            return self.apply(value)
        return self.apply(self.default)

    def apply(self, value: Any) -> Any:
        """应用 formatter；未配置或转换失败时原样返回。"""
        if not self.formatter:
            return value
        fn = FORMATTERS.get(self.formatter) if isinstance(self.formatter, str) else self.formatter
        if fn is None:
            return value
        try:
            return fn(value)
        except Exception:                                # noqa: BLE001 - 转换失败不该中断导出
            return value

    @classmethod
    def from_config(cls, cfg: Union[str, Mapping[str, Any]]) -> "Field":
        """从配置构造：字符串视为列名，字典支持完整参数。

        字典键：``key`` / ``sources``（或 ``source``）/ ``default`` / ``formatter`` /
        ``skip_empty`` / ``width``；``sources`` 也接受单个字符串。
        """
        if isinstance(cfg, str):
            return cls(key=cfg)
        sources = cfg.get("sources", cfg.get("source", ()))
        if isinstance(sources, str):
            sources = [sources]
        return cls(
            key=str(cfg.get("key") or cfg.get("name") or ""),
            sources=tuple(sources),
            default=cfg.get("default", ""),
            formatter=cfg.get("formatter"),
            skip_empty=bool(cfg.get("skip_empty", True)),
            width=int(cfg.get("width", 0)),
        )


_MISSING = object()


@dataclass
class FieldMap:
    """有序的字段集合。

    Attributes:
        fields: 字段列表，顺序即导出列顺序。
    """

    fields: List[Field] = field(default_factory=list)

    @property
    def columns(self) -> List[str]:
        """表头列名（按配置顺序）。"""
        return [f.key for f in self.fields]

    def row(self, record: Any) -> List[Any]:
        """把一条记录转成一行。"""
        return [f.resolve(record) for f in self.fields]

    def only(self, keys: Sequence[str]) -> "FieldMap":
        """只保留指定列（并按给定顺序）。"""
        idx = {k: i for i, k in enumerate(keys)}
        picked = sorted((f for f in self.fields if f.key in idx), key=lambda f: idx[f.key])
        return FieldMap(picked)

    def exclude(self, keys: Sequence[str]) -> "FieldMap":
        """排除指定列。"""
        drop = set(keys)
        return FieldMap([f for f in self.fields if f.key not in drop])

    def rename(self, aliases: Mapping[str, str]) -> "FieldMap":
        """按 ``{旧列名: 新列名}`` 重命名。"""
        return FieldMap([Field(key=aliases.get(f.key, f.key), sources=f.sources,
                               default=f.default, formatter=f.formatter,
                               skip_empty=f.skip_empty, width=f.width)
                         for f in self.fields])

    @classmethod
    def from_config(cls, cfg: Iterable[Union[str, Mapping[str, Any]]]) -> "FieldMap":
        """从配置列表构造。

        Args:
            cfg: 元素可为字符串（列名，同名取值）或字典（完整参数）。

        Returns:
            FieldMap。
        """
        fields = [Field.from_config(c) for c in cfg]
        return cls([f for f in fields if f.key])

    @classmethod
    def from_records(cls,
                     records: Sequence[Any],
                     *,
                     aliases: Optional[Mapping[str, str]] = None,
                     order: Optional[Sequence[str]] = None,
                     exclude: Sequence[str] = ()) -> "FieldMap":
        """从数据自动推断字段（列名取第一条记录里出现过的键）。

        Args:
            records: 源数据。
            aliases: ``{源键: 输出列名}``，用于中文表头等。
            order: 指定列顺序；未列出的列按发现次序排在其后。
            exclude: 要排除的源键。

        Returns:
            FieldMap。
        """
        keys = guess_columns(records)
        drop = set(exclude)
        alias = dict(aliases or {})
        fields = [Field(key=alias.get(k, k), sources=(k,)) for k in keys if k not in drop]
        if order:
            # 先记下原始下标再排序：排序过程中列表在变，直接在 key 里 index() 会错位
            idx = {k: i for i, k in enumerate(order)}
            pairs = sorted(enumerate(fields),
                           key=lambda p: (idx.get(p[1].key, len(idx)), p[0]))
            fields = [f for _, f in pairs]
        return cls(fields)


def guess_columns(records: Sequence[Any]) -> List[str]:
    """从数据中推断可用字段名（dict 取键，dataclass/对象取公共属性，保留出现顺序）。"""
    keys: List[str] = []
    for rec in records[:50]:
        if isinstance(rec, Mapping):
            found = [str(k) for k in rec.keys()]
        elif is_dataclass(rec):
            found = list(_asdict(rec).keys())
        elif hasattr(rec, "__dict__"):
            found = [k for k in vars(rec) if not k.startswith("_")]
        else:
            continue
        for k in found:
            if k not in keys:
                keys.append(k)
    return keys


# --------------------------------------------------------------------------------------
# 三、表格与格式
# --------------------------------------------------------------------------------------

@dataclass
class Table:
    """导出中间结构：表头 + 二维数据。各 writer 只依赖它，与源数据类型解耦。"""

    columns: List[str]
    rows: List[List[Any]] = field(default_factory=list)

    def records(self) -> List[Dict[str, Any]]:
        """转成 dict 列表（JSON 等结构化格式用）。"""
        return [dict(zip(self.columns, r)) for r in self.rows]


def build_table(records: Iterable[Any], mapping: Optional[FieldMap]) -> Table:
    """按字段映射把记录集转成 Table。

    Args:
        records: 任意可迭代的记录（dict / dataclass / 普通对象）。
        mapping: 字段映射；None 表示自动推断（先物化数据再猜列）。

    Returns:
        Table。
    """
    items = list(records)
    if mapping is None:
        mapping = FieldMap.from_records(items)
    return Table(columns=mapping.columns, rows=[mapping.row(r) for r in items])


@dataclass(frozen=True)
class FormatSpec:
    """一种导出格式的全部配置。

    Attributes:
        name: 格式名（如 ``csv``），也是注册表键。
        extensions: 关联的扩展名（不含点），首个为默认扩展名。
        mime: MIME 类型，供 HTTP 下载响应头使用。
        encoding: 文本编码。
        bom: 是否写 BOM（Excel 直接打开 UTF-8 CSV 需要）。
        writer: 写入函数 ``writer(fp, table, spec, options)``。
        defaults: 该格式的默认选项，调用时可逐项覆盖。
        binary: 是否二进制格式（xlsx 为 True，写文件时不做文本包装）。
    """

    name: str
    extensions: Tuple[str, ...]
    mime: str
    writer: Callable[[Any, Table, "FormatSpec", Dict[str, Any]], None]
    encoding: str = "utf-8"
    bom: bool = False
    defaults: Dict[str, Any] = field(default_factory=dict)
    binary: bool = False

    @property
    def ext(self) -> str:
        """默认扩展名（不含点）。"""
        return self.extensions[0]


#: 编码 -> BOM 字节。Excel 识别 UTF-8 CSV 靠的就是 efbbbf。
BOMS: Dict[str, bytes] = {
    "utf-8": b"\xef\xbb\xbf",
    "utf-8-sig": b"\xef\xbb\xbf",
    "utf-16": b"\xff\xfe",
    "utf-16-le": b"\xff\xfe",
    "utf-32": b"\xff\xfe\x00\x00",
}

FORMATS: Dict[str, FormatSpec] = {}


def register_format(spec: FormatSpec, *, overwrite: bool = False) -> FormatSpec:
    """注册一种导出格式——扩展新格式的唯一入口，核心逻辑无需改动。

    Args:
        spec: 格式定义。
        overwrite: 是否允许覆盖同名格式。

    Returns:
        注册后的 FormatSpec。
    """
    if spec.name in FORMATS and not overwrite:
        raise ValueError(f"格式 {spec.name!r} 已注册；如需覆盖请传 overwrite=True")
    FORMATS[spec.name] = spec
    for ext in spec.extensions:
        _EXT_INDEX[ext.lower()] = spec.name
    return spec


_EXT_INDEX: Dict[str, str] = {}


def get_format(name: str) -> FormatSpec:
    """按名字取格式；支持传扩展名（``xlsx`` / ``.xlsx``）。"""
    key = name.lower().lstrip(".")
    if key in FORMATS:
        return FORMATS[key]
    if key in _EXT_INDEX:
        return FORMATS[_EXT_INDEX[key]]
    raise KeyError(f"未注册的导出格式：{name}；可用：{list_formats()}")


def infer_format(path: Union[str, Path], default: str = "csv") -> FormatSpec:
    """按文件扩展名推断格式；无法识别时用 default。"""
    ext = os.path.splitext(str(path))[1].lstrip(".").lower()
    if ext in _EXT_INDEX:
        return FORMATS[_EXT_INDEX[ext]]
    return get_format(default)


def list_formats() -> List[str]:
    """列出已注册的格式名。"""
    return list(FORMATS)


# --------------------------------------------------------------------------------------
# 四、Writer：每种格式只关心「怎么把 Table 写成字节」
# --------------------------------------------------------------------------------------

def _text_fp(fp: Any, spec: FormatSpec, encoding: str, bom: bool):
    """把二进制流包装成文本流；需要时先落 BOM。"""
    if isinstance(fp, (io.TextIOBase, io.StringIO)):
        return fp
    # utf-8-sig / utf-16 这类 codec 自己就会写 BOM，再写一次会变成双 BOM（Excel 会显示乱码前缀）
    if bom and not encoding.lower().endswith(("-sig", "16", "32")):
        fp.write(BOMS.get(encoding.lower(), b""))
    return io.TextIOWrapper(fp, encoding=encoding, newline="", errors="replace")


def _csv_writer(fp: Any, table: Table, spec: FormatSpec, options: Dict[str, Any]) -> None:
    """CSV / TSV：分隔符、引号策略、换行符、BOM 都可配。"""
    encoding = options.get("encoding", spec.encoding)
    bom = options.get("bom", spec.bom)
    wrap = _text_fp(fp, spec, encoding, bom)
    writer = csv.writer(
        wrap,
        delimiter=options.get("delimiter", ","),
        quotechar=options.get("quotechar", '"'),
        quoting=options.get("quoting", csv.QUOTE_MINIMAL),
        lineterminator=options.get("lineterminator", "\r\n"),
    )
    if options.get("header", True):
        writer.writerow(table.columns)
    writer.writerows(table.rows)
    if not isinstance(fp, (io.TextIOBase, io.StringIO)):
        wrap.detach()


def _json_writer(fp: Any, table: Table, spec: FormatSpec, options: Dict[str, Any]) -> None:
    """JSON：支持 records / columns / table 三种布局。"""
    orient = options.get("orient", "records")
    if orient == "records":
        payload: Any = table.records()
    elif orient == "columns":
        payload = {c: [r[i] for r in table.rows] for i, c in enumerate(table.columns)}
    else:
        payload = {"columns": table.columns, "rows": table.rows}
    text = json.dumps(payload, ensure_ascii=options.get("ensure_ascii", False),
                      indent=options.get("indent", 2), default=str)
    _write_text(fp, text + options.get("line_terminator", "\n"), spec, options)


def _jsonl_writer(fp: Any, table: Table, spec: FormatSpec, options: Dict[str, Any]) -> None:
    """JSON Lines：每行一条记录，适合流式与大数据量。"""
    lines = [json.dumps(rec, ensure_ascii=options.get("ensure_ascii", False), default=str)
             for rec in table.records()]
    _write_text(fp, "\n".join(lines) + "\n", spec, options)


def _txt_writer(fp: Any, table: Table, spec: FormatSpec, options: Dict[str, Any]) -> None:
    """纯文本：默认制表符分隔，可自定义分隔符与是否输出表头。"""
    sep = options.get("delimiter", "\t")
    lines: List[str] = []
    if options.get("header", True):
        lines.append(sep.join(str(c) for c in table.columns))
    for row in table.rows:
        lines.append(sep.join("" if v is None else str(v) for v in row))
    _write_text(fp, options.get("line_terminator", "\n").join(lines)
                + options.get("line_terminator", "\n"), spec, options)


def _md_writer(fp: Any, table: Table, spec: FormatSpec, options: Dict[str, Any]) -> None:
    """Markdown 表格：便于贴进文档/工单。"""
    def esc(v: Any) -> str:
        return ("" if v is None else str(v)).replace("|", "\\|").replace("\n", " ")

    lines = ["| " + " | ".join(esc(c) for c in table.columns) + " |",
             "|" + "|".join(["---"] * len(table.columns)) + "|"]
    lines += ["| " + " | ".join(esc(v) for v in row) + " |" for row in table.rows]
    _write_text(fp, "\n".join(lines) + "\n", spec, options)


def _write_text(fp: Any, text: str, spec: FormatSpec, options: Dict[str, Any]) -> None:
    """统一的文本写盘（含 BOM 处理）。"""
    encoding = options.get("encoding", spec.encoding)
    bom = options.get("bom", spec.bom)
    data = text.encode(encoding, errors=options.get("errors", "replace"))
    if bom and not encoding.lower().endswith(("-sig", "16", "32")):
        data = BOMS.get(encoding.lower(), b"") + data
    if isinstance(fp, (io.TextIOBase, io.StringIO)):
        fp.write(text)
    else:
        fp.write(data)


# --- xlsx：手写最小 OOXML，零依赖（zipfile + XML） -------------------------------------

_XML_ESC = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _xesc(value: Any) -> str:
    """XML 文本转义，并剔除 Excel 不接受的控制字符。"""
    s = "" if value is None else str(value)
    s = _CTRL_RE.sub("", s)
    return "".join(_XML_ESC.get(ch, ch) for ch in s)


def _col_letter(index: int) -> str:
    """列序号（从 1 开始）转 Excel 列名：1->A，27->AA。"""
    s = ""
    while index > 0:
        index, rem = divmod(index - 1, 26)
        s = chr(65 + rem) + s
    return s


def _xlsx_sheet_xml(table: Table, sheet_name: str, freeze_header: bool) -> str:
    """生成 worksheet XML（字符串用 inlineStr，数字/布尔直接写 v）。"""
    parts: List[str] = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
    ]
    if freeze_header:
        parts.append('<sheetViews><sheetView workbookViewId="0">'
                     '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
                     '</sheetView></sheetViews>')
    parts.append("<sheetData>")

    def cell(ref: str, value: Any) -> str:
        if value is None or value == "":
            return f'<c r="{ref}"/>'
        if isinstance(value, bool):
            return f'<c r="{ref}" t="b"><v>{int(value)}</v></c>'
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f'<c r="{ref}"><v>{value}</v></c>'
        return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{_xesc(value)}</t></is></c>'

    if table.columns:
        row_xml = "".join(cell(f"{_col_letter(i + 1)}1", c) for i, c in enumerate(table.columns))
        parts.append(f'<row r="1">{row_xml}</row>')
    for r_idx, row in enumerate(table.rows, start=2):
        row_xml = "".join(cell(f"{_col_letter(i + 1)}{r_idx}", v) for i, v in enumerate(row))
        parts.append(f'<row r="{r_idx}">{row_xml}</row>')
    parts.append("</sheetData></worksheet>")
    return "".join(parts)


def _xlsx_writer(fp: Any, table: Table, spec: FormatSpec, options: Dict[str, Any]) -> None:
    """写出最小可用的 .xlsx（无第三方库；用 zipfile 组装 OOXML 包）。"""
    sheet_name = _xesc(options.get("sheet_name", "Sheet1"))[:31] or "Sheet1"
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{sheet_name}" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    wb_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        "</Relationships>"
    )
    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        "</styleSheet>"
    )
    sheet = _xlsx_sheet_xml(table, sheet_name, options.get("freeze_header", True))

    # fp 可能是 BytesIO（export_bytes）或文件对象（export），都能交给 zipfile
    with zipfile.ZipFile(fp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        zf.writestr("xl/styles.xml", styles)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)


# --------------------------------------------------------------------------------------
# 五、注册内置格式
# --------------------------------------------------------------------------------------

register_format(FormatSpec(
    name="csv", extensions=("csv",), mime="text/csv; charset=utf-8",
    writer=_csv_writer, encoding="utf-8", bom=True,
    defaults={"delimiter": ",", "header": True, "lineterminator": "\r\n"},
))

register_format(FormatSpec(
    name="tsv", extensions=("tsv", "tab"), mime="text/tab-separated-values; charset=utf-8",
    writer=_csv_writer, encoding="utf-8", bom=True,
    defaults={"delimiter": "\t", "header": True, "lineterminator": "\r\n"},
))

register_format(FormatSpec(
    name="json", extensions=("json",), mime="application/json; charset=utf-8",
    writer=_json_writer, encoding="utf-8", bom=False,
    defaults={"orient": "records", "indent": 2, "ensure_ascii": False},
))

register_format(FormatSpec(
    name="jsonl", extensions=("jsonl", "ndjson"), mime="application/x-ndjson",
    writer=_jsonl_writer, encoding="utf-8", bom=False,
    defaults={"ensure_ascii": False},
))

register_format(FormatSpec(
    name="xlsx",
    extensions=("xlsx",),
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    writer=_xlsx_writer, encoding="utf-8", bom=False, binary=True,
    defaults={"sheet_name": "Sheet1", "freeze_header": True},
))

register_format(FormatSpec(
    name="txt", extensions=("txt", "log"), mime="text/plain; charset=utf-8",
    writer=_txt_writer, encoding="utf-8", bom=True,
    defaults={"delimiter": "\t", "header": True, "line_terminator": "\n"},
))

register_format(FormatSpec(
    name="md", extensions=("md", "markdown"), mime="text/markdown; charset=utf-8",
    writer=_md_writer, encoding="utf-8", bom=False,
    defaults={"header": True},
))


# --------------------------------------------------------------------------------------
# 六、对外 API
# --------------------------------------------------------------------------------------

@dataclass
class ExportResult:
    """导出结果元信息，便于日志展示或构造 HTTP 响应。"""

    path: str
    format: str
    extension: str
    mime: str
    encoding: str
    bom: bool
    columns: List[str]
    row_count: int
    size: int

    def __str__(self) -> str:
        return (f"{self.path}（{self.format}，{self.row_count} 行 / {len(self.columns)} 列，"
                f"{self.size / 1024:.1f} KB）")


def _coerce_mapping(mapping: Union[None, str, FieldMap, Iterable[Any]],
                    registry: Optional[Mapping[str, FieldMap]]) -> Optional[FieldMap]:
    """把 mapping 参数归一成 FieldMap。

    Args:
        mapping: None（自动推断）/ 预置名 / FieldMap / 配置列表。
        registry: 预置映射表，默认用模块级 PRESET_MAPPINGS。

    Returns:
        FieldMap 或 None（None 表示交给 build_table 自动推断）。
    """
    if mapping is None:
        return None
    if isinstance(mapping, FieldMap):
        return mapping
    if isinstance(mapping, str):
        table = registry if registry is not None else PRESET_MAPPINGS
        if mapping not in table:
            raise KeyError(f"未注册的字段映射：{mapping}；可用：{list(table)}")
        return table[mapping]
    return FieldMap.from_config(mapping)


#: 预置字段映射注册表：``register_mapping("cert", ...)`` 之后即可用名字引用。
PRESET_MAPPINGS: Dict[str, FieldMap] = {}


def register_mapping(name: str, mapping: Union[FieldMap, Iterable[Any]]) -> FieldMap:
    """注册一个具名字段映射，供 ``export(..., mapping="名字")`` 引用。"""
    fm = mapping if isinstance(mapping, FieldMap) else FieldMap.from_config(mapping)
    PRESET_MAPPINGS[name] = fm
    return fm


def export(records: Iterable[Any],
           destination: Union[str, Path],
           *,
           fmt: Optional[str] = None,
           mapping: Union[None, str, FieldMap, Iterable[Any]] = None,
           encoding: Optional[str] = None,
           bom: Optional[bool] = None,
           mapper_registry: Optional[Mapping[str, FieldMap]] = None,
           **options: Any) -> ExportResult:
    """导出数据到文件——最常用的入口。

    Args:
        records: 数据源，元素可为 dict / dataclass / 普通对象。
        destination: 目标路径；扩展名用于推断格式（除非显式给 fmt）。
        fmt: 格式名或扩展名；缺省按扩展名推断，再缺省取 csv。
        mapping: 字段映射。可为 None（自动推断）、预置名、FieldMap 或配置列表。
        encoding: 覆盖格式默认编码。
        bom: 覆盖格式默认 BOM 设置。
        mapper_registry: 自定义预置映射表（默认 PRESET_MAPPINGS）。
        **options: 传给 writer 的其它选项（分隔符、缩进、sheet_name…）。

    Returns:
        ExportResult。

    Raises:
        KeyError: 格式或预置映射名未注册。
        OSError: 目标目录不可写。

    Examples:
        >>> export(rows, "out.xlsx")
        >>> export(rows, "out.csv", mapping="cert", bom=True)
        >>> export(rows, "out.txt", fmt="txt", delimiter=" | ")
    """
    path = Path(destination)
    spec = get_format(fmt) if fmt else infer_format(path)
    if path.suffix == "":
        path = path.with_name(f"{path.name}.{spec.ext}")
    path.parent.mkdir(parents=True, exist_ok=True)

    field_map = _coerce_mapping(mapping, mapper_registry)
    table = build_table(records, field_map)

    opts = dict(spec.defaults)
    opts.update(options)
    if encoding:
        opts["encoding"] = encoding
    if bom is not None:
        opts["bom"] = bom
    final_encoding = opts.get("encoding", spec.encoding)
    final_bom = bool(opts.get("bom", spec.bom))

    with open(path, "wb") as fp:
        spec.writer(fp, table, spec, opts)

    return ExportResult(
        path=str(path), format=spec.name, extension=spec.ext, mime=spec.mime,
        encoding=final_encoding, bom=final_bom, columns=table.columns,
        row_count=len(table.rows), size=path.stat().st_size,
    )


def export_bytes(records: Iterable[Any],
                 *,
                 fmt: str = "csv",
                 mapping: Union[None, str, FieldMap, Iterable[Any]] = None,
                 encoding: Optional[str] = None,
                 bom: Optional[bool] = None,
                 mapper_registry: Optional[Mapping[str, FieldMap]] = None,
                 **options: Any) -> Tuple[bytes, ExportResult]:
    """导出到内存，返回 ``(字节, 元信息)``——用于 HTTP 下载或测试。

    Args:
        records: 数据源。
        fmt: 格式名或扩展名。
        mapping: 字段映射，同 :func:`export`。
        encoding: 覆盖默认编码。
        bom: 覆盖默认 BOM。
        mapper_registry: 自定义预置映射表。
        **options: 传给 writer 的选项。

    Returns:
        ``(data, ExportResult)``，其中 ``ExportResult.path`` 为空串。
    """
    spec = get_format(fmt)
    field_map = _coerce_mapping(mapping, mapper_registry)
    table = build_table(records, field_map)

    opts = dict(spec.defaults)
    opts.update(options)
    if encoding:
        opts["encoding"] = encoding
    if bom is not None:
        opts["bom"] = bom
    final_encoding = opts.get("encoding", spec.encoding)
    final_bom = bool(opts.get("bom", spec.bom))

    buf = io.BytesIO()
    spec.writer(buf, table, spec, opts)
    data = buf.getvalue()
    result = ExportResult(
        path="", format=spec.name, extension=spec.ext, mime=spec.mime,
        encoding=final_encoding, bom=final_bom, columns=table.columns,
        row_count=len(table.rows), size=len(data),
    )
    return data, result


def download_headers(result: ExportResult, filename: Optional[str] = None) -> Dict[str, str]:
    """按导出结果生成 HTTP 下载响应头（MIME 在这里派上用场）。

    Args:
        result: :func:`export_bytes` 返回的元信息。
        filename: 下载文件名；缺省用导出路径的文件名。

    Returns:
        响应头字典。
    """
    name = filename or (os.path.basename(result.path) if result.path
                        else f"export.{result.extension}")
    safe = name.replace('"', "")
    return {
        "Content-Type": result.mime,
        "Content-Length": str(result.size),
        "Content-Disposition": f'attachment; filename="{safe}"; filename*=UTF-8\'\'{safe}',
    }


if __name__ == "__main__":
    demo = [
        {"name": "张三", "idNo": "510101200001011234", "meta": {"score": 91}},
        {"姓名": "李四", "id_no": "510101200001015678", "meta": {"score": 88}},
    ]
    m = FieldMap.from_config([
        {"key": "姓名", "sources": ["name", "姓名"]},
        {"key": "身份证号", "sources": ["idNo", "id_no", "idNumber"]},
        {"key": "分数", "sources": ["meta.score"], "formatter": "int"},
    ])
    for f in ("csv", "xlsx", "json", "txt", "md"):
        r = export(demo, f"_demo.{f}", mapping=m)
        print(r)
