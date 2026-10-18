# -*- coding: utf-8 -*-
"""证书下载任务的**内存作业管理器**（后台线程 + 进度 + 取消 + 结果）。

设计约束：
- 业务逻辑零改动复用 ``certdown``（``download_one`` / ``build_jobs`` / ``write_report`` ...），
  这里只在其外面套一层「可查询进度 / 可取消」的作业壳；
- 作业状态只存在内存（由 ``app.api`` 挂在 ``Session.cert`` 上），关页随会话回收；
- 落盘只发生在会话临时目录（容器内位于 tmpfs ``/tmp``）。

进程内单 worker，无需考虑跨进程共享。
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
import time
import uuid
import zipfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any, Dict, List, Optional

# certdown 是纯标准库脚本，内部用「同目录绝对导入」（import exporter），
# 所以把它的目录注入 sys.path 后直接 import，而不是按包方式导入。
CERT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "certdown",
)
if CERT_DIR not in sys.path:
    sys.path.insert(0, CERT_DIR)

import cert_downloader as cd  # noqa: E402

from app import scratch  # noqa: E402

#: 前端最多回看的日志条数（最新的在数组末尾）。
MAX_LOGS = 300

_STATUS_TAG = {"ok": "OK  ", "skipped": "SKIP", "failed": "FAIL", "cancelled": "CANC"}


def _size_of(path: Path) -> int:
    """单个文件的字节数；不存在或取不到时返回 0。"""
    try:
        return path.stat().st_size
    except OSError:
        return 0


#: 本身已是压缩格式的扩展名 —— 再用 deflate 只会白烧 CPU，体积几乎不变。
#: 证书场景（jpg/png 扫描件、pdf 证明）几乎全落在这一类。
_PRECOMPRESSED_EXTS = {
    "jpg", "jpeg", "png", "gif", "webp", "heic", "heif", "avif", "tif", "tiff",
    "pdf", "zip", "gz", "bz2", "xz", "7z", "rar",
    "docx", "xlsx", "pptx", "odt", "ods", "odp",
    "mp3", "mp4", "m4a", "aac", "ogg", "webm", "mov", "avi", "mkv",
}


def _zip_add(zf: zipfile.ZipFile, path: Path, arcname: Optional[str] = None) -> None:
    """把文件写进 zip；已是压缩格式的用 STORED，其余用 DEFLATED。"""
    ext = path.suffix.lower().lstrip(".")
    method = zipfile.ZIP_STORED if ext in _PRECOMPRESSED_EXTS else zipfile.ZIP_DEFLATED
    zf.write(path, arcname=arcname or path.name, compress_type=method)


class CertJob:
    """一次证书下载作业的内存状态机。

    状态：``idle``（未用）→ ``running`` → ``done`` / ``error`` / ``cancelled``。

    Attributes:
        workdir: 会话临时目录（产物 zip/pdf 落在其下）。
        out_dir: 本次作业**专属**的输出目录（``workdir/cert_<job_id前8位>``）。
            每次「开始下载」都用新目录，避免同一会话里换产物形态时把上一次的
            文件当成「已存在」全部跳过（那会让 pdf 形态直接报「没有可合并的图片」）。
        cancel_event: 传给 ``download_one`` 的取消信号。
    """

    def __init__(self, workdir: str) -> None:
        self.job_id = uuid.uuid4().hex
        self.workdir = Path(workdir)
        self.out_dir = self.workdir / f"cert_{self.job_id[:8]}"
        self.cancel_event = threading.Event()

        self._lock = threading.Lock()
        self.state = "idle"
        self.phase = ""
        self.total = 0
        self.done = 0
        self.ok = 0
        self.skipped = 0
        self.failed = 0
        self.cancelled = 0
        self.error = ""
        self.logs: "deque[str]" = deque(maxlen=MAX_LOGS)
        self.result: Optional[Dict[str, Any]] = None
        # 保留任务列表，供 /api/cert/report 现场重导出其它格式
        self.jobs: List[Any] = []
        self._thread: Optional[threading.Thread] = None
        # 产物形态（zip | files | pdf）与附件范围（image | all）
        self.bundle = "zip"
        self.file_kinds = "all"

    # ------------------------------------------------------------------
    # 进度上报（均加锁，供后台线程与请求线程并发访问）
    # ------------------------------------------------------------------

    def _log(self, message: str) -> None:
        with self._lock:
            self.logs.append(message)

    def _set_phase(self, phase: str) -> None:
        with self._lock:
            self.phase = phase

    def _set_total(self, total: int) -> None:
        with self._lock:
            self.total = total

    def _record(self, status: str) -> None:
        """按单个任务的结果累加进度计数。"""
        with self._lock:
            self.done += 1
            if status == "ok":
                self.ok += 1
            elif status == "skipped":
                self.skipped += 1
            elif status == "cancelled":
                self.cancelled += 1
            else:
                self.failed += 1

    def snapshot(self) -> Dict[str, Any]:
        """返回给前端的一次性进度快照。"""
        with self._lock:
            return {
                "job_id": self.job_id,
                "state": self.state,
                "phase": self.phase,
                "total": self.total,
                "done": self.done,
                "ok": self.ok,
                "skipped": self.skipped,
                "failed": self.failed,
                "cancelled": self.cancelled,
                "error": self.error,
                "logs": list(self.logs),
                "result": self.result,
                "bundle": self.bundle,
                "file_kinds": self.file_kinds,
            }

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self, params: Dict[str, Any]) -> None:
        """在后台线程启动下载作业。``params`` 见 ``cert_api.cert_start``。"""
        with self._lock:
            self.state = "running"
            self.error = ""
            self.phase = "排队中"
            self.bundle = params.get("bundle") or "zip"
            self.file_kinds = params.get("file_kinds") or "all"
        self._thread = threading.Thread(target=self._execute, args=(params,), daemon=True)
        self._thread.start()

    def request_cancel(self) -> None:
        """置取消信号；未开始的任务会被 ``download_one`` 跳过。

        已提交的 HTTP 请求会跑完当前请求才停（原工具的已知限制）。
        """
        self.cancel_event.set()
        self._log("已请求停止，等待当前请求结束…")

    # ------------------------------------------------------------------
    # 主流程（在后台线程中执行）
    # ------------------------------------------------------------------

    def _execute(self, p: Dict[str, Any]) -> None:
        t0 = time.time()
        try:
            # 0. 空间预检：宁可在开始前给一句中文原因，也不要下载/打包到一半 ENOSPC
            scratch.ensure_free(scratch.MIN_FREE_BYTES, "开始下载")

            out_dir = self.out_dir
            out_dir.mkdir(parents=True, exist_ok=True)
            self._log(f"临时区：{scratch.root()}（可用 {scratch.mb(scratch.free_bytes())}）")

            client = cd.TencentSmartsheetClient(
                p["doc_id"], cookie=p.get("cookie") or None,
                timeout=p["timeout"], retries=p["retries"])

            # 1. 读取子表 + 构造任务
            self._set_phase("读取子表")
            self._log(f"使用子表：{p['sheet']}")
            # 先用 limit=None 拿全量（kinds 会让一条记录可能产出多个任务，截断放到构造之后），
            # 再按用户上限自行截断，保证「数量上限」精确等于任务条数。
            jobs_all, _fields = cd.build_jobs(
                client, p["sheet"], p["name_field"], p["id_field"], p["image_field"],
                None, strict_id=p["strict_id"], kinds=p.get("file_kinds") or "all")
            jobs = jobs_all[:p["limit"]] if p["limit"] else jobs_all
            if not jobs:
                self._fail("没有生成任何下载任务（可能姓名/身份证/图片列为空）")
                return
            self.jobs = jobs
            self._set_total(len(jobs))
            self._log(f"解析完成：下载任务 {len(jobs)} 个")

            # 2. 批量换取附件临时下载地址
            self._set_phase("换取下载地址")
            pad_id = client.get_pad_id(p["sheet"])
            url_map = client.fetch_attachment_urls(
                pad_id, [j.attachment.obj_key for j in jobs], p["access_type"])
            no_url = 0
            for j in jobs:
                j.url = url_map.get(j.attachment.obj_key, "")
                if not j.url:
                    j.status, j.error = "failed", "未取到下载地址"
                    no_url += 1
            self._log(f"取到下载地址 {len(jobs) - no_url}/{len(jobs)}")
            for j in jobs:
                if not j.url:
                    self._record("failed")

            # 3. 并发下载
            self._set_phase("下载中")
            cd.reset_allocated()
            pre = cd.snapshot_existing(out_dir, p["overwrite"])
            pending = [j for j in jobs if j.url]
            self._log(f"开始下载（并发 {p['concurrency']}）...")
            worker = partial(
                cd.download_one, client=client, out_dir=out_dir, sep=p["sep"],
                overwrite=p["overwrite"], timeout=p["timeout"], retries=p["retries"],
                name_template=p["template"], prefix=p["prefix"],
                cancel=self.cancel_event, pre_existing=pre)
            with ThreadPoolExecutor(max_workers=max(1, p["concurrency"])) as pool:
                futs = [pool.submit(worker, j) for j in pending]
                for fut in as_completed(futs):
                    job = fut.result()
                    self._record(job.status)
                    tag = _STATUS_TAG.get(job.status, "????")
                    self._log(f"[{tag}] {job.index:>4} {job.name} "
                              f"{cd.mask_id(job.id_no)} {job.saved_as or job.error}")

            # 4. 写报告（走通用导出层）
            self._set_phase("写报告")
            report_ext = cd.exporter.get_format(p["report_format"]).ext
            report_name = f"download_report.{report_ext}"
            cd.write_report(out_dir / report_name, jobs,
                            fmt=p["report_format"], mapping="cert")

            # 5. 按产物形态产出：zip（打包）/ files（不打包）/ pdf（图片合并 + 非图片另打包）
            bundle = p.get("bundle") or "zip"
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            zip_path: Optional[Path] = None
            zip_name: Optional[str] = None
            pdf_path: Optional[Path] = None
            pdf_name: Optional[str] = None
            extras_path: Optional[Path] = None
            extras_name: Optional[str] = None

            ok_jobs = [j for j in jobs if j.status == "ok" and j.saved_as]
            image_jobs = [j for j in ok_jobs if j.attachment.is_image]
            other_jobs = [j for j in ok_jobs if not j.attachment.is_image]

            if bundle == "files":
                # 不打包：产物就是 out_dir 里的文件本身，前端走 /api/cert/files + /file
                self._set_phase("整理文件")
            elif bundle == "pdf":
                # 合并 PDF 前先确认放得下（PDF ≈ 原件之和）
                payload = sum(_size_of(out_dir / j.saved_as) for j in ok_jobs)
                scratch.ensure_free(int(payload * scratch.BUNDLE_OVERHEAD_RATIO)
                                    + scratch.BUNDLE_OVERHEAD_FIXED, "合并 PDF")
                self._set_phase("合并 PDF")
                pdf_path = self.workdir / "cert_result.pdf"
                pdf_name = f"证书_{p['doc_id']}_{stamp}.pdf"
                self._merge_pdf(jobs, out_dir, pdf_path)
                if other_jobs:
                    self._set_phase("打包非图片附件")
                    extras_path = self.workdir / "cert_extras.zip"
                    extras_name = f"证书_非图片附件_{p['doc_id']}_{stamp}.zip"
                    with zipfile.ZipFile(extras_path, "w", zipfile.ZIP_DEFLATED) as zf:
                        for j in other_jobs:
                            f = out_dir / j.saved_as
                            if f.is_file():
                                _zip_add(zf, f)
                        rpt = out_dir / report_name
                        if rpt.is_file():
                            _zip_add(zf, rpt)
            else:  # zip（默认，历史行为不变）
                # 打包时「原件 + 压缩包」会同时存在，必须先确认放得下
                payload = sum(_size_of(out_dir / j.saved_as) for j in ok_jobs)
                scratch.ensure_free(int(payload * scratch.BUNDLE_OVERHEAD_RATIO)
                                    + scratch.BUNDLE_OVERHEAD_FIXED, "打包 ZIP")
                self._set_phase("打包中")
                zip_name = f"证书_{p['doc_id']}_{stamp}.zip"
                zip_path = self.workdir / "cert_result.zip"
                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                    for f in sorted(out_dir.iterdir()):
                        if f.is_file():
                            _zip_add(zf, f)

            elapsed = round(time.time() - t0, 1)
            # 打包完成后中间文件已经进了 zip/pdf，删掉释放空间（files 形态要留着）
            if bundle != "files":
                freed = scratch.dir_size(out_dir)
                shutil.rmtree(out_dir, ignore_errors=True)
                if freed:
                    self._log(f"已清理中间文件释放 {scratch.mb(freed)}"
                              f"（临时区剩余 {scratch.mb(scratch.free_bytes())}）")
            with self._lock:
                self.result = {
                    "bundle": bundle,
                    "zip": str(zip_path) if zip_path else None,
                    "zip_name": zip_name,
                    "pdf": str(pdf_path) if pdf_path else None,
                    "pdf_name": pdf_name,
                    "extras": str(extras_path) if extras_path else None,
                    "extras_name": extras_name,
                    "report_name": report_name,
                    "total": self.total,
                    "ok": self.ok,
                    "skipped": self.skipped,
                    "failed": self.failed,
                    "cancelled": self.cancelled,
                    "image_count": len(image_jobs),
                    "other_count": len(other_jobs),
                    "elapsed_sec": elapsed,
                }
                self.state = "cancelled" if self.cancel_event.is_set() else "done"
                self.phase = "完成"
            if self.state == "cancelled":
                self._log("已停止：未开始的任务已取消")
            self._log(f"完成：成功 {self.ok} / 跳过 {self.skipped} / "
                      f"失败 {self.failed} / 取消 {self.cancelled}，用时 {elapsed} 秒")
        except cd.DocError as exc:
            self._fail(str(exc))
        except Exception as exc:  # noqa: BLE001 - 汇总成前端可读错误
            self._fail(self._friendly_error(exc))
            # 失败时也别把半成品留在临时区占着地方
            shutil.rmtree(self.out_dir, ignore_errors=True)

    def _merge_pdf(self, jobs: List[Any], out_dir: Path, pdf_path: Path) -> None:
        """按表格记录顺序把图片附件合并成一份 PDF。

        实际合并交给 ``app.cert_pdf``：它逐张流式拼装（JPEG 原样嵌入、非 JPEG 逐张转码后
        立即释放），内存恒定，不会像 Pillow ``save_all`` 那样把所有页的像素同时驻留内存
        （300+ 张图会打爆容器）。这里只负责按记录顺序收集图片路径、推进进度、汇总日志。
        """
        from app import cert_pdf  # 只在合并 PDF 时引入，避免其他形态无谓依赖

        # image_paths 必须按表格记录顺序（jobs 顺序），只取已成功下载的图片附件
        image_paths = [out_dir / j.saved_as for j in jobs
                       if j.status == "ok" and j.saved_as and j.attachment.is_image
                       and (out_dir / j.saved_as).is_file()]

        def _on_page(done: int, total: int) -> None:
            self._set_phase(f"合并 PDF {done}/{total}")
            if done == 1 or done == total or done % 20 == 0:
                self._log(f"合并 PDF {done}/{total}")

        try:
            info = cert_pdf.images_to_pdf(image_paths, pdf_path, on_progress=_on_page)
        except ValueError as exc:          # 一张可用图都没有
            raise cd.DocError("没有可合并的图片附件（当前附件都是非图片文件）") from exc

        if info["skipped"]:
            names = "、".join(n for n, _ in info["skipped"])
            self._log(f"合并 PDF：成功 {info['pages']} 张，跳过 "
                      f"{len(info['skipped'])} 张（格式不支持）：{names}")
        else:
            self._log(f"合并 PDF：成功 {info['pages']} 张 → {pdf_path.name}")

    def list_files(self) -> List[Dict[str, Any]]:
        """返回输出目录里的文件清单（按文件名排序，报告排在最后）。

        每项：``{"name": str, "size": int, "is_image": bool, "kind": str}``，
        ``kind`` ∈ ``{"image", "report", "other"}``。只列 ``out_dir`` 下的普通文件，不递归。
        """
        out_dir = self.out_dir
        items: List[Dict[str, Any]] = []
        if not out_dir.is_dir():
            return items
        # 以任务结果判定图片（与下载时 is_image 一致），而非只看扩展名
        img_names = {j.saved_as for j in self.jobs
                     if j.status == "ok" and j.saved_as and j.attachment.is_image}
        for f in out_dir.iterdir():
            if not f.is_file():
                continue
            name = f.name
            if name.startswith("download_report."):
                kind = "report"
            elif name in img_names:
                kind = "image"
            else:
                kind = "other"
            try:
                size = f.stat().st_size
            except OSError:
                size = 0
            items.append({"name": name, "size": size,
                          "is_image": kind == "image", "kind": kind})
        # 稳定排序：图片/其它在前按名字，报告统一排最后
        items.sort(key=lambda it: (it["kind"] == "report", it["name"]))
        return items

    def cleanup(self) -> None:
        """删除本作业的输出目录与产物文件（同一会话重新开始下载前调用）。

        不删的话，tmpfs 里会堆上一次跑剩下的图片；而且旧产物被新作业覆盖前
        ``/download`` 还能下到上一轮的 zip/pdf，容易让人误以为下载的是本次结果。
        """
        shutil.rmtree(self.out_dir, ignore_errors=True)
        for name in ("cert_result.zip", "cert_result.pdf", "cert_extras.zip"):
            try:
                (self.workdir / name).unlink()
            except OSError:
                pass

    def _fail(self, reason: str) -> None:
        with self._lock:
            self.error = reason
            self.state = "error"
            self.phase = ""
        self._log(f"失败：{reason}")

    @staticmethod
    def _friendly_error(exc: BaseException) -> str:
        """把底层异常翻成用户能看懂的中文（空间类错误单独处理）。"""
        if isinstance(exc, scratch.ScratchFull):
            return str(exc)
        if isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:
            return ("临时空间不足（磁盘写满）。请减少「数量上限」后重试，"
                    "或联系管家扩容后重跑。")
        if isinstance(exc, cd.DocError):
            return str(exc)
        return f"{type(exc).__name__}: {exc}"
