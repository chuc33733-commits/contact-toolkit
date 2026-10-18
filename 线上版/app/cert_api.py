# -*- coding: utf-8 -*-
"""证书下载器 · 线上版 HTTP 接口（与通讯录同进程、同端口、同会话）。

- 业务逻辑零改动复用 ``certdown/cert_downloader.py`` + ``certdown/exporter.py``；
- 会话沿用 ``app.session.store``（请求头 ``X-Session-Id`` / ``X-Session-Token``）；
- 作业状态挂在 ``Session.cert``（内存），不落库、不落盘用户数据；
- 错误一律返回 ``{ok:false, error:"中文原因"}`` 且 HTTP 200（仅会话无效为 401、
  并发重复启动为 409），方便前端直接展示。
"""

from __future__ import annotations

import os
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from app import cert_jobs
from app.session import store

# 让 ``import cert_downloader`` 命中 certdown 目录：
# certdown 内部用「同目录绝对导入（import exporter）」，不能按包导入。
CERT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "certdown",
)
if CERT_DIR not in sys.path:
    sys.path.insert(0, CERT_DIR)

import cert_downloader as cd  # noqa: E402

router = APIRouter(prefix="/api/cert")

_DEFAULT_TIMEOUT = 60.0
_DEFAULT_RETRIES = 3
_REPORT_FORMATS = ("csv", "xlsx", "json", "jsonl", "txt", "md", "tsv")

# 附件范围 / 产物形态选项（供 /meta 下发，前端下拉直接用）
_KINDS_CHOICES = (
    {"value": "all", "label": "全部文件（图片 / PDF / Word / Excel / 压缩包…）"},
    {"value": "image", "label": "仅图片"},
)
_BUNDLE_CHOICES = (
    {"value": "zip", "label": "ZIP 打包（全部文件 + 报告，一个压缩包）"},
    {"value": "files", "label": "逐个单文件（列出清单，逐个下载）"},
    {"value": "pdf", "label": "合并成 PDF（图片按表格顺序合成一份 PDF）"},
)
_KIND_VALUES = {c["value"] for c in _KINDS_CHOICES}
_BUNDLE_VALUES = {c["value"] for c in _BUNDLE_CHOICES}

# 字段自动识别关键词，与 cert_downloader.build_jobs 内部保持一致（仅用于回填默认下拉）
_NAME_KEYWORDS = ("姓名", "名字", "姓名（", "name")
_ID_KEYWORDS = ("身份证", "证件号", "证件编号", "身份号码")
_ATTACH_KEYWORDS = ("证书", "附件", "图片", "照片", "材料", "证明")


# ----------------------------------------------------------------------
# 辅助
# ----------------------------------------------------------------------

def _session(sid: Optional[str], token: Optional[str]):
    """与 ``app.api._session`` 等价的会话校验（避免循环导入而各写一份）。"""
    if not sid:
        raise HTTPException(status_code=401, detail="缺少会话，请刷新页面")
    session = store.get(sid, token)
    if session is None:
        raise HTTPException(status_code=401, detail="会话已过期或无效，请刷新页面")
    return session


async def _json(request: Request) -> Dict[str, Any]:
    """安全读取 JSON 请求体；解析失败返回空 dict（由各接口给出中文提示）。"""
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001 - 非法 JSON 走「缺参数」分支
        return {}
    return data if isinstance(data, dict) else {}


def _int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _float(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _opt_int(value: Any, lo: int, hi: int) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return max(lo, min(hi, n))


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on", "是")


def _kinds(value: Any) -> str:
    """附件范围：非法值静默回落默认 ``all``（不报错）。"""
    v = str(value or "all").strip().lower()
    return v if v in _KIND_VALUES else "all"


def _bundle(value: Any) -> str:
    """产物形态：非法值静默回落默认 ``zip``（不报错）。"""
    v = str(value or "zip").strip().lower()
    return v if v in _BUNDLE_VALUES else "zip"


def _make_client(doc: str, cookie: Optional[str],
                 timeout: float = _DEFAULT_TIMEOUT, retries: int = _DEFAULT_RETRIES):
    """解析文档 ID 并构造客户端。"""
    doc_id = cd.TencentSmartsheetClient.parse_doc_id(doc)
    client = cd.TencentSmartsheetClient(doc_id, cookie=cookie,
                                        timeout=timeout, retries=retries)
    return doc_id, client


def _infer_defaults(fields) -> Dict[str, Optional[str]]:
    """回填三个下拉的默认字段（识别不到则 None，由用户手选）。"""
    def pick(fn):
        try:
            return fn().title
        except cd.DocError:
            return None

    return {
        "name_field": pick(lambda: cd.pick_field(fields, None, _NAME_KEYWORDS, "姓名")),
        "id_field": pick(lambda: cd.pick_field(fields, None, _ID_KEYWORDS, "身份证号")),
        "image_field": pick(lambda: cd.pick_attachment_field(fields, None)),
    }


def _preview_filename(job, template: str, prefix: str, sep: str) -> str:
    """预览文件名：与 ``cd.resolve_filename`` 同算法，但用示例数据（sample=True）。

    ``resolve_filename`` 未暴露 sample 开关，这里复刻其两步（build_stem -> sanitize + ext），
    以保证预览不出现真实姓名 / 身份证号。
    """
    stem = cd.sanitize_filename(cd.build_stem(job, template, prefix, sep, sample=True))
    ext = (job.attachment.ext or "").lower().lstrip(".") or "jpg"
    return f"{stem}.{ext}"


def _download(data: bytes, filename: str, media_type: str) -> Response:
    quoted = urllib.parse.quote(filename)
    return Response(
        content=data,
        media_type=media_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quoted}",
            "Cache-Control": "no-store",
        },
    )


# ----------------------------------------------------------------------
# 元信息（命名预设 / 变量）
# ----------------------------------------------------------------------

@router.get("/meta")
def cert_meta(x_session_id: Optional[str] = Header(default=None),
              x_session_token: Optional[str] = Header(default=None)):
    """命名预设与变量——数据源直接读 certdown，避免前后端两处不同步。"""
    _session(x_session_id, x_session_token)
    return {
        "ok": True,
        "presets": [{"label": label, "value": tpl} for label, tpl in cd.NAME_PRESETS],
        "variables": [{"name": name, "desc": desc} for name, desc in cd.NAME_VARIABLES],
        "default_template": cd.DEFAULT_NAME_TEMPLATE,
        "report_formats": list(_REPORT_FORMATS),
        "kinds": list(_KINDS_CHOICES),
        "bundles": list(_BUNDLE_CHOICES),
        "default_kinds": "all",
        "default_bundle": "zip",
    }


# ----------------------------------------------------------------------
# 子表 / 字段 / 预览
# ----------------------------------------------------------------------

@router.post("/sheets")
async def cert_sheets(request: Request,
                      x_session_id: Optional[str] = Header(default=None),
                      x_session_token: Optional[str] = Header(default=None)):
    _session(x_session_id, x_session_token)
    body = await _json(request)
    doc = str(body.get("doc") or "").strip()
    cookie = str(body.get("cookie") or "").strip() or None
    if not doc:
        return {"ok": False, "error": "请填写腾讯文档链接或 ID"}
    try:
        doc_id, client = _make_client(doc, cookie)
        sheets = client.list_sheets()
        if not sheets:
            return {"ok": False,
                    "error": "未获取到任何子表（文档可能为空或无访问权限；私有文档请在 Cookie 中填登录态）"}
        return {"ok": True, "doc_id": doc_id, "sheets": sheets}
    except cd.DocError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"读取子表失败：{exc}"}


@router.post("/fields")
async def cert_fields(request: Request,
                      x_session_id: Optional[str] = Header(default=None),
                      x_session_token: Optional[str] = Header(default=None)):
    _session(x_session_id, x_session_token)
    body = await _json(request)
    doc = str(body.get("doc") or "").strip()
    cookie = str(body.get("cookie") or "").strip() or None
    sheet = str(body.get("sheet") or "").strip()
    if not doc:
        return {"ok": False, "error": "请填写腾讯文档链接或 ID"}
    if not sheet:
        return {"ok": False, "error": "请先获取并选择子表"}
    try:
        _doc_id, client = _make_client(doc, cookie)
        fields, records = client.fetch_table(sheet)
        return {
            "ok": True,
            "records": len(records),
            "fields": [{"title": f.title, "ftype": f.ftype,
                        "is_attachment": f.is_attachment} for f in fields],
            "defaults": _infer_defaults(fields),
        }
    except cd.DocError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"解析字段失败：{exc}"}


@router.post("/preview")
async def cert_preview(request: Request,
                       x_session_id: Optional[str] = Header(default=None),
                       x_session_token: Optional[str] = Header(default=None)):
    _session(x_session_id, x_session_token)
    body = await _json(request)
    doc = str(body.get("doc") or "").strip()
    cookie = str(body.get("cookie") or "").strip() or None
    sheet = str(body.get("sheet") or "").strip()
    if not doc:
        return {"ok": False, "error": "请填写腾讯文档链接或 ID"}
    if not sheet:
        return {"ok": False, "error": "请先获取并选择子表"}

    name_field = body.get("name_field") or None
    id_field = body.get("id_field") or None
    image_field = body.get("image_field") or None
    template = str(body.get("template") or cd.DEFAULT_NAME_TEMPLATE)
    prefix = str(body.get("prefix") or "")
    sep = str(body.get("sep") or "")
    limit = _opt_int(body.get("limit"), 1, 100000)
    strict_id = _bool(body.get("strict_id"), True)
    file_kinds = _kinds(body.get("file_kinds"))

    try:
        _doc_id, client = _make_client(doc, cookie)
        # 先取记录总数，用于统计「跳过」条数（build_jobs 只回传任务，不回传跳过数）
        _fields, records = client.fetch_table(sheet)
        # 按「不限量」构造一遍：limit 只是截断，不代表记录有问题，
        # 所以「跳过」必须按全量算，否则预览会显示成「跳过 303 条」误导用户。
        jobs_all, _ = cd.build_jobs(client, sheet, name_field, id_field, image_field,
                                    None, strict_id=strict_id, kinds=file_kinds)
        valid_records = len({j.record_id for j in jobs_all})
        skipped = max(0, len(records) - valid_records)
        jobs = jobs_all[:limit] if limit else jobs_all
        samples = [{
            "index": j.index,
            "filename": _preview_filename(j, template, prefix, sep),
            "source_name": j.attachment.name,
        } for j in jobs[:30]]
        log = [
            f"使用子表：{sheet}",
            f"解析完成：记录 {len(records)} 条 → 下载任务 {len(jobs_all)} 个，跳过 {skipped} 条",
            "预览使用示例数据（姓名=张三、身份证=示例号码），不会显示真实隐私信息",
        ]
        if limit and len(jobs_all) > limit:
            log.append(f"已按数量上限截断为前 {len(jobs)} 个任务（预览与下载都只处理这些）")
        return {"ok": True, "total_jobs": len(jobs), "skipped": skipped,
                "samples": samples, "log": log}
    except cd.DocError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"预览失败：{exc}"}


# ----------------------------------------------------------------------
# 作业：启动 / 进度 / 停止 / 下载
# ----------------------------------------------------------------------

@router.post("/start")
async def cert_start(request: Request,
                     x_session_id: Optional[str] = Header(default=None),
                     x_session_token: Optional[str] = Header(default=None)):
    session = _session(x_session_id, x_session_token)
    body = await _json(request)
    doc = str(body.get("doc") or "").strip()
    cookie = str(body.get("cookie") or "").strip() or None
    sheet = str(body.get("sheet") or "").strip()
    if not doc:
        return {"ok": False, "error": "请填写腾讯文档链接或 ID"}
    if not sheet:
        return {"ok": False, "error": "请先获取并选择子表"}

    existing = session.cert.get("job")
    if existing is not None and existing.state == "running":
        return JSONResponse(
            status_code=409,
            content={"ok": False, "error": "当前会话已有证书下载任务正在运行，请先等待完成或停止"})

    try:
        doc_id = cd.TencentSmartsheetClient.parse_doc_id(doc)
    except cd.DocError as exc:
        return {"ok": False, "error": str(exc)}

    report_format = str(body.get("report_format") or "csv").lower().lstrip(".")
    if report_format not in _REPORT_FORMATS:
        report_format = "csv"

    params: Dict[str, Any] = {
        "doc_id": doc_id,
        "cookie": cookie,
        "sheet": sheet,
        "name_field": body.get("name_field") or None,
        "id_field": body.get("id_field") or None,
        "image_field": body.get("image_field") or None,
        "template": str(body.get("template") or cd.DEFAULT_NAME_TEMPLATE),
        "prefix": str(body.get("prefix") or ""),
        "sep": str(body.get("sep") or ""),
        "concurrency": _int(body.get("concurrency"), 8, 1, 32),
        "timeout": _float(body.get("timeout"), _DEFAULT_TIMEOUT, 5.0, 600.0),
        "retries": _int(body.get("retries"), _DEFAULT_RETRIES, 1, 10),
        "limit": _opt_int(body.get("limit"), 1, 100000),
        "overwrite": _bool(body.get("overwrite"), False),
        "strict_id": _bool(body.get("strict_id"), True),
        "access_type": 1 if _int(body.get("access_type"), 0, 0, 1) == 1 else 0,
        "report_format": report_format,
        "file_kinds": _kinds(body.get("file_kinds")),
        "bundle": _bundle(body.get("bundle")),
    }
    # 同一会话重新开始下载：先把上一轮作业清干净（输出目录 + 旧产物），
    # 否则新作业会把上一轮的文件当成「已存在」全部跳过（换产物形态时尤其致命）。
    prev = session.cert.get("job")
    if prev is not None:
        try:
            prev.cleanup()
        except Exception:                      # noqa: BLE001 - 清理失败不该挡住新作业
            pass
    job = cert_jobs.CertJob(session.workdir)
    session.cert["job"] = job
    job.start(params)
    return {"ok": True, "job_id": job.job_id}


@router.get("/progress")
def cert_progress(x_session_id: Optional[str] = Header(default=None),
                  x_session_token: Optional[str] = Header(default=None)):
    session = _session(x_session_id, x_session_token)
    job = session.cert.get("job")
    if job is None:
        return {"state": "idle", "phase": "", "total": 0, "done": 0, "ok": 0,
                "skipped": 0, "failed": 0, "cancelled": 0, "error": "",
                "logs": [], "result": None}
    return job.snapshot()


@router.post("/stop")
def cert_stop(x_session_id: Optional[str] = Header(default=None),
              x_session_token: Optional[str] = Header(default=None)):
    session = _session(x_session_id, x_session_token)
    job = session.cert.get("job")
    if job is not None and job.state == "running":
        job.request_cancel()
    return {"ok": True}


@router.get("/download")
def cert_download(x_session_id: Optional[str] = Header(default=None),
                  x_session_token: Optional[str] = Header(default=None)):
    session = _session(x_session_id, x_session_token)
    job = session.cert.get("job")
    result = job.result if job is not None else None
    if not result:
        return JSONResponse(status_code=200,
                            content={"ok": False, "error": "还没有可下载的结果，请先开始下载"})
    bundle = result.get("bundle") or "zip"

    if bundle == "files":
        return JSONResponse(status_code=200, content={
            "ok": False,
            "error": "当前产物形态是「逐个单文件」，请用文件清单逐个下载"})

    if bundle == "pdf":
        path, name, media = result.get("pdf"), result.get("pdf_name"), "application/pdf"
    else:
        path, name, media = result.get("zip"), result.get("zip_name"), "application/zip"

    if not path or not os.path.isfile(path):
        return JSONResponse(status_code=200,
                            content={"ok": False, "error": "还没有可下载的结果，请先开始下载"})
    return FileResponse(path, media_type=media,
                        filename=name or "certificates.zip")


@router.get("/files")
def cert_files(x_session_id: Optional[str] = Header(default=None),
               x_session_token: Optional[str] = Header(default=None)):
    """列出输出目录里的产物文件清单（files 形态逐个下载用）。"""
    session = _session(x_session_id, x_session_token)
    job = session.cert.get("job")
    if job is None:
        return {"ok": False, "error": "还没有可列出的文件，请先开始下载"}
    # zip / pdf 形态打包后中间文件已被清理（内容都在产物里），清单没有意义
    if job.bundle != "files":
        return {"ok": False,
                "error": "当前产物形态不是「逐个单文件」，请用上方的下载按钮取产物"}
    items = job.list_files()
    counts = {
        "image": sum(1 for it in items if it["kind"] == "image"),
        "other": sum(1 for it in items if it["kind"] == "other"),
        "report": sum(1 for it in items if it["kind"] == "report"),
        "total": len(items),
    }
    return {"ok": True, "bundle": job.bundle, "items": items, "counts": counts}


@router.get("/file")
def cert_file(name: str = "",
              x_session_id: Optional[str] = Header(default=None),
              x_session_token: Optional[str] = Header(default=None)):
    """下载输出目录里的单个文件（文件名做目录穿越校验）。"""
    session = _session(x_session_id, x_session_token)
    # 参数名只能用 name：fmt/cmd/exec 之类的词会被 Lucky 的 CorazaWAF 规则 932250 拦成 403。
    name = (name or "").strip()
    if (not name
            or name != os.path.basename(name)
            or "/" in name or "\\" in name or ".." in name
            or name.startswith(".")):
        return JSONResponse(status_code=200,
                            content={"ok": False, "error": "文件名不合法"})
    job = session.cert.get("job")
    if job is None:
        return JSONResponse(status_code=200,
                            content={"ok": False, "error": "还没有可下载的文件，请先开始下载"})
    out_root = job.out_dir.resolve()
    target = (out_root / name).resolve()
    if not str(target).startswith(str(out_root) + os.sep) or not target.is_file():
        return JSONResponse(status_code=200,
                            content={"ok": False, "error": "文件不存在"})
    return FileResponse(str(target), filename=name)


@router.get("/extras")
def cert_extras(x_session_id: Optional[str] = Header(default=None),
                x_session_token: Optional[str] = Header(default=None)):
    """下载非图片附件 ZIP（仅 pdf 形态且存在非图片附件时有意义）。"""
    session = _session(x_session_id, x_session_token)
    job = session.cert.get("job")
    result = job.result if job is not None else None
    extras = result.get("extras") if result else None
    if not extras or not os.path.isfile(extras):
        return JSONResponse(status_code=200,
                            content={"ok": False, "error": "没有非图片附件可下载"})
    return FileResponse(extras, media_type="application/zip",
                        filename=result.get("extras_name") or "cert_extras.zip")


@router.get("/report")
def cert_report(format: str = "csv",
                x_session_id: Optional[str] = Header(default=None),
                x_session_token: Optional[str] = Header(default=None)):
    session = _session(x_session_id, x_session_token)
    job = session.cert.get("job")
    if job is None or not job.jobs:
        return JSONResponse(status_code=200,
                            content={"ok": False, "error": "还没有可导出的报告，请先开始下载"})
    # 参数名必须是 format 而不是 fmt：Lucky 上的 CorazaWAF 规则 932250
    # 把字面量 fmt 当作 Unix 命令（coreutils fmt）拦截，URL 里出现 fmt 一律 403。
    name = (format or "csv").lower().lstrip(".")
    try:
        data, meta = cd.exporter.export_bytes(job.jobs, fmt=name, mapping="cert")
    except KeyError:
        return JSONResponse(status_code=200,
                            content={"ok": False, "error": f"不支持的报告格式：{name}"})
    return _download(data, f"download_report.{meta.extension}", meta.mime)
