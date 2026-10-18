# -*- coding: utf-8 -*-
"""
通讯录处理工具 · 线上版 HTTP 接口。

- 业务逻辑全部复用桌面版代码（models / processors / utils / config 一行不改）；
- 本文件只负责：路由、会话、上传/下载、以及把 Contact 序列化成前端视图。
- 数据只存活在进程内存，关页后由 sendBeacon + TTL 双重回收（不落库）。
"""

import io
import json
import os
import urllib.parse
from typing import List, Optional

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app import adapters
from app import cert_api
from app import scratch
from app.session import store
from utils.field_utils import decode_base64_payload, is_remote_uri, parse_data_uri, summarize_photo
from utils.merger import ContactMerger

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")

# ---- 二级路径部署支持 --------------------------------------------------
# Lucky 的「域名/路径」反代不会改写后端 path，故由本服务剥离前缀：
#   BASE_PATH=/tongxunlu 时，/tongxunlu/api/... 与 /api/... 命中同一套路由；
#   未设置（内网直连 http://IP:8788/）时行为与原来完全一致。
_raw_base = os.environ.get("BASE_PATH", "").strip().strip("/")
BASE_PATH = "/" + _raw_base if _raw_base else ""


class _BasePathMiddleware:
    """把 BASE_PATH 前缀从 scope['path'] 剥掉，让路由按根路径匹配。"""

    def __init__(self, app, prefix: str = ""):
        self.app = app
        self.prefix = prefix

    async def __call__(self, scope, receive, send):
        if self.prefix and scope.get("type") in ("http", "websocket"):
            path = scope.get("path") or ""
            if path == self.prefix or path.startswith(self.prefix + "/"):
                scope = dict(scope)
                new_path = path[len(self.prefix):] or "/"
                scope["path"] = new_path
                scope["raw_path"] = new_path.encode("utf-8")
                scope["root_path"] = self.prefix
        await self.app(scope, receive, send)


app = FastAPI(title="通讯录处理工具 · 线上版", docs_url=None, redoc_url=None)
app.add_middleware(_BasePathMiddleware, prefix=BASE_PATH)
app.include_router(cert_api.router)
_merger = ContactMerger()


# ----------------------------------------------------------------------
# 会话依赖
# ----------------------------------------------------------------------

def _session(sid: Optional[str], token: Optional[str]):
    if not sid:
        raise HTTPException(status_code=401, detail="缺少会话，请刷新页面")
    session = store.get(sid, token)
    if session is None:
        raise HTTPException(status_code=401, detail="会话已过期或无效，请刷新页面重新导入")
    return session


def _contact_view(contact, index: int) -> dict:
    """把 Contact 转成前端视图；照片只给摘要，避免大 base64 压垮列表。"""
    return {
        "index": index,
        "name": contact.name,
        "nicknames": contact.nicknames,
        "phones": contact.phones,
        "emails": contact.emails,
        "organization": contact.organization,
        "title": contact.title,
        "address": contact.address,
        "birthday": contact.birthday,
        "lunar_birthday": contact.lunar_birthday,
        "website": contact.website,
        "notes": contact.notes,
        "categories": contact.categories,
        "photo_count": len(contact.photos),
        "photo_summaries": [summarize_photo(p) for p in contact.photos],
        "extra_field_count": len(contact.extra_vcard_fields),
    }


def _stats_payload(session) -> dict:
    contacts = session.manager.get_all_contacts()
    return {
        "count": len(contacts),
        "with_phone": sum(1 for c in contacts if c.phones),
        "with_email": sum(1 for c in contacts if c.emails),
        "with_photo": sum(1 for c in contacts if c.photos),
        "with_birthday": sum(1 for c in contacts if c.birthday or c.lunar_birthday),
    }


# ----------------------------------------------------------------------
# 页面
# ----------------------------------------------------------------------

def _html_page(filename: str) -> HTMLResponse:
    """读取 static 下的单页并注入二级路径前缀（供前端拼所有接口地址）。"""
    path = os.path.join(STATIC_DIR, filename)
    with open(path, encoding="utf-8") as fh:
        html = fh.read()
    html = html.replace(
        "</head>",
        f'<script>window.__BASE__ = {json.dumps(BASE_PATH)};</script>\n</head>',
        1,
    )
    return HTMLResponse(html)


@app.get("/", response_class=HTMLResponse)
def portal():
    return _html_page("portal.html")


@app.get("/contacts", response_class=HTMLResponse)
def contacts_page():
    return _html_page("contacts.html")


@app.get("/cert", response_class=HTMLResponse)
def cert_page():
    return _html_page("cert.html")


@app.get("/api/health")
def health():
    """健康检查（healthcheck 只看状态码；额外字段用于排查临时区/空间问题）。"""
    return {"ok": True, "sessions": store.count(), "scratch": scratch.usage()}


# ----------------------------------------------------------------------
# 会话
# ----------------------------------------------------------------------

@app.post("/api/session")
def create_session():
    session = store.create()
    return {"sid": session.sid, "token": session.token}


@app.post("/api/session/close")
def close_session(x_session_id: Optional[str] = Header(default=None)):
    if x_session_id:
        store.drop(x_session_id)
    return {"ok": True}


# ----------------------------------------------------------------------
# 导入
# ----------------------------------------------------------------------

@app.post("/api/import/vcf")
async def import_vcf(
    files: List[UploadFile] = File(...),
    x_session_id: Optional[str] = Header(default=None),
    x_session_token: Optional[str] = Header(default=None),
):
    session = _session(x_session_id, x_session_token)
    results = []
    for upload in files:
        data = await upload.read()
        manager, stats, error = adapters.import_vcf_bytes(upload.filename, data, session.workdir)
        if error:
            results.append({"filename": upload.filename, "ok": False, "error": error})
            session.add_log("import-vcf", f"{upload.filename} 导入失败：{error}")
            continue
        for contact in manager.get_all_contacts():
            session.manager.add_contact(contact)
        results.append({
            "filename": upload.filename,
            "ok": True,
            "imported": stats.get("valid_contacts", 0),
            "photos": stats.get("photos_imported", 0),
            "encoding": stats.get("file_encoding", ""),
        })
        session.add_log("import-vcf",
                        f"{upload.filename} 导入 {stats.get('valid_contacts', 0)} 条",
                        imported=stats.get("valid_contacts", 0))
    return {"results": results, "stats": _stats_payload(session)}


@app.post("/api/import/excel")
async def import_excel(
    files: List[UploadFile] = File(...),
    x_session_id: Optional[str] = Header(default=None),
    x_session_token: Optional[str] = Header(default=None),
):
    session = _session(x_session_id, x_session_token)
    results = []
    for upload in files:
        data = await upload.read()
        manager, stats, error = adapters.import_excel_bytes(upload.filename, data, session.workdir)
        if error:
            results.append({"filename": upload.filename, "ok": False, "error": error})
            session.add_log("import-excel", f"{upload.filename} 导入失败：{error}")
            continue
        for contact in manager.get_all_contacts():
            session.manager.add_contact(contact)
        results.append({
            "filename": upload.filename,
            "ok": True,
            "imported": stats.get("valid_contacts", 0),
            "photos": stats.get("photos_imported", 0),
            "warnings": stats.get("warnings", []),
            "columns": stats.get("column_mapping", {}),
            "from_zip": stats.get("from_zip", False),
        })
        session.add_log("import-excel",
                        f"{upload.filename} 导入 {stats.get('valid_contacts', 0)} 条",
                        imported=stats.get("valid_contacts", 0))
    return {"results": results, "stats": _stats_payload(session)}


# ----------------------------------------------------------------------
# 联系人列表
# ----------------------------------------------------------------------

@app.get("/api/contacts")
def list_contacts(
    q: Optional[str] = None,
    sort: str = "default",
    x_session_id: Optional[str] = Header(default=None),
    x_session_token: Optional[str] = Header(default=None),
):
    session = _session(x_session_id, x_session_token)
    contacts = session.manager.get_all_contacts()

    indexed = list(enumerate(contacts))
    if q:
        needle = q.strip().lower()
        indexed = [
            (i, c) for i, c in indexed
            if needle in (c.name or "").lower()
            or any(needle in p.lower() for p in c.phones)
            or any(needle in e.lower() for e in c.emails)
            or needle in (c.organization or "").lower()
        ]

    if sort == "name":
        indexed.sort(key=lambda pair: pair[1].name or "")
    elif sort == "updated":
        indexed.sort(key=lambda pair: pair[1].updated_at, reverse=True)

    return {
        "contacts": [_contact_view(c, i) for i, c in indexed],
        "stats": _stats_payload(session),
        "log": session.log[-50:],
    }


@app.get("/api/contacts/{idx}/photo/{pos}")
def contact_photo(
    idx: int, pos: int,
    x_session_id: Optional[str] = Header(default=None),
    x_session_token: Optional[str] = Header(default=None),
):
    session = _session(x_session_id, x_session_token)
    contacts = session.manager.get_all_contacts()
    if idx < 0 or idx >= len(contacts):
        raise HTTPException(status_code=404, detail="联系人不存在")
    photos = contacts[idx].photos
    if pos < 0 or pos >= len(photos):
        raise HTTPException(status_code=404, detail="照片不存在")

    photo = photos[pos]
    if is_remote_uri(photo):
        return Response(status_code=302, headers={"Location": photo})
    parsed = parse_data_uri(photo)
    if not parsed:
        raise HTTPException(status_code=415, detail="照片不是可渲染的图片")
    mime, payload = parsed
    raw = decode_base64_payload(payload)
    return Response(content=raw, media_type=mime or "image/jpeg")


@app.delete("/api/contacts/{idx}")
def delete_contact(
    idx: int,
    x_session_id: Optional[str] = Header(default=None),
    x_session_token: Optional[str] = Header(default=None),
):
    session = _session(x_session_id, x_session_token)
    contacts = session.manager.contacts
    if idx < 0 or idx >= len(contacts):
        raise HTTPException(status_code=404, detail="联系人不存在")
    removed = contacts.pop(idx)
    session.add_log("delete", f"删除联系人：{removed.name or '未命名'}")
    return {"ok": True, "stats": _stats_payload(session)}


@app.post("/api/clear")
def clear_contacts(
    x_session_id: Optional[str] = Header(default=None),
    x_session_token: Optional[str] = Header(default=None),
):
    session = _session(x_session_id, x_session_token)
    session.manager.clear()
    session.log.clear()
    session.add_log("clear", "已清空当前会话全部联系人")
    return {"ok": True, "stats": _stats_payload(session)}


# ----------------------------------------------------------------------
# 合并
# ----------------------------------------------------------------------

@app.post("/api/merge")
def merge(
    dry_run: bool = Form(False),
    x_session_id: Optional[str] = Header(default=None),
    x_session_token: Optional[str] = Header(default=None),
):
    session = _session(x_session_id, x_session_token)
    contacts = session.manager.get_all_contacts()
    if not contacts:
        return {"ok": True, "stats": _stats_payload(session), "preview": [], "merge": {}}

    if dry_run:
        preview = _merger.preview_merge_operations(contacts)
        return {
            "ok": True,
            "dry_run": True,
            "groups": len(preview),
            "preview": preview[:200],
            "stats": _stats_payload(session),
        }

    result = _merger.merge_duplicates_in_manager(session.manager)
    session.add_log(
        "merge",
        f"合并重复：{result['total_contacts_before']} → {result['total_contacts_after']}"
        f"（合并掉 {result['contacts_merged']} 条，重复组 {result['duplicate_groups_found']}）",
    )
    return {"ok": True, "merge": result, "stats": _stats_payload(session)}


# ----------------------------------------------------------------------
# 导出
# ----------------------------------------------------------------------

@app.get("/api/export/vcf")
def export_vcf(
    x_session_id: Optional[str] = Header(default=None),
    x_session_token: Optional[str] = Header(default=None),
):
    session = _session(x_session_id, x_session_token)
    if session.manager.count() == 0:
        raise HTTPException(status_code=400, detail="没有联系人可导出")
    data, error = adapters.export_vcf_bytes(session.manager, session.workdir)
    if error:
        raise HTTPException(status_code=500, detail=error)
    session.add_log("export-vcf", f"导出 VCF（{session.manager.count()} 条）")
    return _download(data, "merged.vcf", "application/vnd.vcard")


@app.get("/api/export/excel")
def export_excel(
    x_session_id: Optional[str] = Header(default=None),
    x_session_token: Optional[str] = Header(default=None),
):
    session = _session(x_session_id, x_session_token)
    if session.manager.count() == 0:
        raise HTTPException(status_code=400, detail="没有联系人可导出")
    data, filename, error = adapters.export_excel_bytes(session.manager, session.workdir)
    if error:
        raise HTTPException(status_code=500, detail=error)
    media = "application/zip" if filename.endswith(".zip") else \
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    session.add_log("export-excel", f"导出 Excel（{session.manager.count()} 条）→ {filename}")
    return _download(data, filename, media)


@app.get("/api/template/excel")
def template_excel():
    from app.template import build_template_bytes
    data = build_template_bytes()
    return _download(data, "联系人导入模板.xlsx",
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


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


# 静态资源（若后续拆分 css/js）
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
