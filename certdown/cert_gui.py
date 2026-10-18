# -*- coding: utf-8 -*-
"""腾讯文档证书下载器 · 图形界面

把 ``cert_downloader`` 的下载内核包一层 tkinter 界面，双击即可使用：
填文档链接 → 获取子表 → 解析字段 → 选输出目录 → 开始下载。

命令行（便于自动化验证）::

    cert_gui.py --selftest <文档链接/ID> <输出目录> [数量上限]

``--selftest`` 不打开窗口，跑一遍完整下载流程并把结果打印到控制台。
"""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# certdown 内部是「同目录绝对导入」（import exporter），所以把所在目录放进 sys.path
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import cert_downloader as cd  # noqa: E402

APP_TITLE = f"腾讯文档证书批量下载器 v{cd.__version__}"
CONFIG_PATH = Path.home() / ".tencent_cert_gui.json"

REPORT_FORMATS = ("csv", "xlsx", "json", "jsonl", "txt", "md", "tsv")
KIND_LABELS = {"仅图片（证书扫描件）": cd.KINDS_IMAGE, "全部文件（PDF/Word/压缩包等）": cd.KINDS_ALL}


class QueueLogHandler(logging.Handler):
    """把内核日志送进界面队列。"""

    def __init__(self, q: "queue.Queue[str]") -> None:
        super().__init__()
        self.q = q

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102
        try:
            self.q.put(self.format(record))
        except Exception:  # noqa: BLE001 - 日志绝不能反过来把界面搞崩
            pass


def sample_job() -> "cd.Job":
    """命名预览用的示例任务（姓名/身份证为虚构值）。"""
    att = cd.Attachment(obj_key="", name="示例证书.jpg", ext="jpg", size=0, kind="image")
    return cd.Job(index=1, record_id="fr1", name="张三", id_no="510101200001011234", attachment=att)


class CertGui:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("980x820")
        self.root.minsize(900, 720)

        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.ui_queue: "queue.Queue[Any]" = queue.Queue()
        self.sheets: List[Dict[str, str]] = []
        self.fields: List["cd.FieldDef"] = []
        self.cancel_event = threading.Event()
        self.running = False
        self.total = 0
        self.done = 0
        self.counts = {"ok": 0, "skipped": 0, "failed": 0, "cancelled": 0}

        self._build_vars()
        self._build_ui()
        self._load_config()
        self._setup_logging()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(120, self._pump)

    # ------------------------------------------------------------------ 变量
    def _build_vars(self) -> None:
        self.doc_var = tk.StringVar()
        self.cookie_var = tk.StringVar()
        self.sheet_var = tk.StringVar()
        self.name_field_var = tk.StringVar()
        self.id_field_var = tk.StringVar()
        self.attach_field_var = tk.StringVar()
        self.out_var = tk.StringVar(value=str(Path.home() / "证书下载"))
        self.preset_var = tk.StringVar(value=cd.NAME_PRESETS[0][0])
        self.template_var = tk.StringVar(value=cd.NAME_PRESETS[0][1])
        self.prefix_var = tk.StringVar()
        self.sep_var = tk.StringVar()
        self.concurrency_var = tk.StringVar(value="8")
        self.timeout_var = tk.StringVar(value="60")
        self.retries_var = tk.StringVar(value="3")
        self.limit_var = tk.StringVar(value="0")
        self.kinds_var = tk.StringVar(value=list(KIND_LABELS)[1])
        self.overwrite_var = tk.BooleanVar(value=False)
        self.strict_var = tk.BooleanVar(value=True)
        self.report_var = tk.StringVar(value="csv")
        self.status_var = tk.StringVar(value="就绪")
        self.preview_var = tk.StringVar()
        for v in (self.template_var, self.prefix_var, self.sep_var):
            v.trace_add("write", lambda *_: self._update_preview())
        self._update_preview()

    # ------------------------------------------------------------------ 界面
    def _build_ui(self) -> None:
        pad = {"padx": 6, "pady": 4}

        box1 = ttk.LabelFrame(self.root, text=" 1. 文档与字段 ")
        box1.pack(fill="x", padx=12, pady=(12, 6))
        ttk.Label(box1, text="文档链接或 ID：").grid(row=0, column=0, sticky="e", **pad)
        ttk.Entry(box1, textvariable=self.doc_var, width=64).grid(row=0, column=1, columnspan=3, sticky="we", **pad)
        ttk.Label(box1, text="Cookie（可选，私有文档才填）：").grid(row=1, column=0, sticky="e", **pad)
        ttk.Entry(box1, textvariable=self.cookie_var, width=64, show="*").grid(row=1, column=1, columnspan=3, sticky="we", **pad)
        ttk.Label(box1, text="子表：").grid(row=2, column=0, sticky="e", **pad)
        self.sheet_combo = ttk.Combobox(box1, textvariable=self.sheet_var, width=34, state="readonly")
        self.sheet_combo.grid(row=2, column=1, sticky="we", **pad)
        self.btn_sheets = ttk.Button(box1, text="获取子表", command=self.on_fetch_sheets)
        self.btn_sheets.grid(row=2, column=2, **pad)
        ttk.Label(box1, text="姓名字段：").grid(row=3, column=0, sticky="e", **pad)
        self.name_combo = ttk.Combobox(box1, textvariable=self.name_field_var, width=20)
        self.name_combo.grid(row=3, column=1, sticky="we", **pad)
        ttk.Label(box1, text="身份证字段：").grid(row=3, column=2, sticky="e", **pad)
        self.id_combo = ttk.Combobox(box1, textvariable=self.id_field_var, width=20)
        self.id_combo.grid(row=3, column=3, sticky="we", **pad)
        ttk.Label(box1, text="证书附件字段：").grid(row=4, column=0, sticky="e", **pad)
        self.attach_combo = ttk.Combobox(box1, textvariable=self.attach_field_var, width=20)
        self.attach_combo.grid(row=4, column=1, sticky="we", **pad)
        self.btn_fields = ttk.Button(box1, text="解析字段", command=self.on_parse_fields)
        self.btn_fields.grid(row=4, column=2, **pad)
        ttk.Label(box1, text="（下拉框留空 = 自动识别）", foreground="#666").grid(row=4, column=3, sticky="w", **pad)
        box1.columnconfigure(1, weight=1)
        box1.columnconfigure(3, weight=1)

        box2 = ttk.LabelFrame(self.root, text=" 2. 输出与命名 ")
        box2.pack(fill="x", padx=12, pady=6)
        ttk.Label(box2, text="保存到：").grid(row=0, column=0, sticky="e", **pad)
        ttk.Entry(box2, textvariable=self.out_var).grid(row=0, column=1, columnspan=2, sticky="we", **pad)
        ttk.Button(box2, text="浏览…", command=self.on_browse).grid(row=0, column=3, **pad)
        ttk.Label(box2, text="命名方式：").grid(row=1, column=0, sticky="e", **pad)
        self.preset_combo = ttk.Combobox(box2, textvariable=self.preset_var, width=30, state="readonly",
                                         values=[p[0] for p in cd.NAME_PRESETS])
        self.preset_combo.grid(row=1, column=1, sticky="we", **pad)
        self.preset_combo.bind("<<ComboboxSelected>>", self.on_preset)
        ttk.Label(box2, text="命名模板：").grid(row=2, column=0, sticky="e", **pad)
        ttk.Entry(box2, textvariable=self.template_var).grid(row=2, column=1, columnspan=2, sticky="we", **pad)
        ttk.Label(box2, text="自定义前缀：").grid(row=3, column=0, sticky="e", **pad)
        ttk.Entry(box2, textvariable=self.prefix_var, width=18).grid(row=3, column=1, sticky="w", **pad)
        ttk.Label(box2, text="分隔符：").grid(row=3, column=2, sticky="e", **pad)
        ttk.Entry(box2, textvariable=self.sep_var, width=10).grid(row=3, column=3, sticky="w", **pad)
        ttk.Label(box2, text="文件名预览：").grid(row=4, column=0, sticky="e", **pad)
        ttk.Label(box2, textvariable=self.preview_var, foreground="#0b6", font=("Microsoft YaHei UI", 10, "bold")).grid(
            row=4, column=1, columnspan=3, sticky="w", **pad)
        box2.columnconfigure(1, weight=1)
        box2.columnconfigure(2, weight=1)

        box3 = ttk.LabelFrame(self.root, text=" 3. 下载参数 ")
        box3.pack(fill="x", padx=12, pady=6)
        ttk.Label(box3, text="附件范围：").grid(row=0, column=0, sticky="e", **pad)
        ttk.Combobox(box3, textvariable=self.kinds_var, state="readonly", width=26,
                     values=list(KIND_LABELS)).grid(row=0, column=1, sticky="w", **pad)
        ttk.Label(box3, text="报告格式：").grid(row=0, column=2, sticky="e", **pad)
        ttk.Combobox(box3, textvariable=self.report_var, state="readonly", width=8,
                     values=REPORT_FORMATS).grid(row=0, column=3, sticky="w", **pad)
        ttk.Label(box3, text="并发数：").grid(row=1, column=0, sticky="e", **pad)
        ttk.Spinbox(box3, from_=1, to=32, textvariable=self.concurrency_var, width=6).grid(row=1, column=1, sticky="w", **pad)
        ttk.Label(box3, text="超时(秒)：").grid(row=1, column=2, sticky="e", **pad)
        ttk.Spinbox(box3, from_=10, to=300, textvariable=self.timeout_var, width=6).grid(row=1, column=3, sticky="w", **pad)
        ttk.Label(box3, text="重试次数：").grid(row=2, column=0, sticky="e", **pad)
        ttk.Spinbox(box3, from_=0, to=10, textvariable=self.retries_var, width=6).grid(row=2, column=1, sticky="w", **pad)
        ttk.Label(box3, text="数量上限(0=全部)：").grid(row=2, column=2, sticky="e", **pad)
        ttk.Spinbox(box3, from_=0, to=100000, textvariable=self.limit_var, width=6).grid(row=2, column=3, sticky="w", **pad)
        ttk.Checkbutton(box3, text="覆盖同名文件（默认跳过已下载）", variable=self.overwrite_var).grid(
            row=3, column=0, columnspan=2, sticky="w", **pad)
        ttk.Checkbutton(box3, text="校验身份证号格式（不合法跳过）", variable=self.strict_var).grid(
            row=3, column=2, columnspan=2, sticky="w", **pad)

        box4 = ttk.LabelFrame(self.root, text=" 4. 执行与日志 ")
        box4.pack(fill="both", expand=True, padx=12, pady=(6, 12))
        bar = ttk.Frame(box4)
        bar.pack(fill="x", padx=6, pady=6)
        self.btn_start = ttk.Button(bar, text="开始下载", command=self.on_start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(bar, text="停止", command=self.on_stop, state="disabled")
        self.btn_stop.pack(side="left", padx=6)
        ttk.Button(bar, text="打开输出目录", command=self.on_open_dir).pack(side="left", padx=6)
        ttk.Label(bar, textvariable=self.status_var).pack(side="right")
        self.progress = ttk.Progressbar(box4, mode="determinate")
        self.progress.pack(fill="x", padx=6)
        wrap = ttk.Frame(box4)
        wrap.pack(fill="both", expand=True, padx=6, pady=6)
        self.log_text = tk.Text(wrap, height=14, wrap="none", state="disabled",
                                font=("Consolas", 9))
        scroll = ttk.Scrollbar(wrap, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    # ------------------------------------------------------------------ 日志/队列
    def _setup_logging(self) -> None:
        handler = QueueLogHandler(self.log_queue)
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
        logger = logging.getLogger("cert")
        logger.setLevel(logging.INFO)
        logger.handlers[:] = [handler]
        logger.propagate = False

    def _append_log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        # 只保留最后 2000 行，长时间运行也不吃内存
        if int(self.log_text.index("end-1c").split(".")[0]) > 2000:
            self.log_text.delete("1.0", "200.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _pump(self) -> None:
        try:
            while True:
                self._append_log(self.log_queue.get_nowait())
        except queue.Empty:
            pass
        try:
            while True:
                fn, args = self.ui_queue.get_nowait()
                fn(*args)
        except queue.Empty:
            pass
        self.root.after(120, self._pump)

    def _post(self, fn, *args) -> None:
        self.ui_queue.put((fn, args))

    # ------------------------------------------------------------------ 命名预览
    def _update_preview(self) -> None:
        try:
            stem = cd.sanitize_filename(
                cd.build_stem(sample_job(), self.template_var.get(), self.prefix_var.get(),
                              self.sep_var.get(), sample=True))
            self.preview_var.set(f"{stem}.jpg")
        except Exception:  # noqa: BLE001 - 预览失败不该影响使用
            self.preview_var.set("（模板有误）")

    def on_preset(self, _event=None) -> None:
        for label, tpl in cd.NAME_PRESETS:
            if label == self.preset_var.get():
                self.template_var.set(tpl)
                break

    def on_browse(self) -> None:
        path = filedialog.askdirectory(initialdir=self.out_var.get() or str(Path.home()))
        if path:
            self.out_var.set(path)

    def on_open_dir(self) -> None:
        path = Path(self.out_var.get()).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])

    # ------------------------------------------------------------------ 网络操作
    def _client(self) -> "cd.TencentSmartsheetClient":
        doc = self.doc_var.get().strip()
        if not doc:
            raise cd.DocError("请先填写腾讯文档链接或文档 ID")
        doc_id = cd.TencentSmartsheetClient.parse_doc_id(doc)
        return cd.TencentSmartsheetClient(doc_id, cookie=self.cookie_var.get().strip() or None)

    def _busy(self, on: bool, status: str = "") -> None:
        state = "disabled" if on else "normal"
        for btn in (self.btn_sheets, self.btn_fields, self.btn_start):
            btn.configure(state=state)
        self.btn_stop.configure(state="normal" if on else "disabled")
        if status:
            self.status_var.set(status)

    def on_fetch_sheets(self) -> None:
        try:
            client = self._client()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("文档地址有误", str(exc))
            return
        self._busy(True, "正在获取子表…")

        def work() -> None:
            try:
                sheets = client.list_sheets()
                self._post(self._sheets_done, sheets, None)
            except Exception as exc:  # noqa: BLE001
                self._post(self._sheets_done, [], exc)

        threading.Thread(target=work, daemon=True).start()

    def _sheets_done(self, sheets: List[Dict[str, str]], exc: Optional[Exception]) -> None:
        self._busy(False, "就绪")
        if exc is not None:
            messagebox.showerror("获取子表失败", str(exc))
            return
        self.sheets = sheets
        if not sheets:
            messagebox.showwarning("没有子表", "文档里没有读到任何子表，请确认链接是否正确。")
            return
        self.sheet_combo.configure(values=[f"{s['name']}（{s['id']}）" for s in sheets])
        self.sheet_var.set(f"{sheets[0]['name']}（{sheets[0]['id']}）")
        self.status_var.set(f"共 {len(sheets)} 个子表")

    def _current_sheet_id(self) -> str:
        text = self.sheet_var.get().strip()
        if not text:
            raise cd.DocError("请先点「获取子表」并选择一个子表")
        for s in self.sheets:
            if text == f"{s['name']}（{s['id']}）":
                return s["id"]
        return text

    def on_parse_fields(self) -> None:
        try:
            client = self._client()
            sheet_id = self._current_sheet_id()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("参数不完整", str(exc))
            return
        self._busy(True, "正在解析字段…")

        def work() -> None:
            try:
                fields, records = client.fetch_table(sheet_id)
                self._post(self._fields_done, fields, len(records), None)
            except Exception as exc:  # noqa: BLE001
                self._post(self._fields_done, [], 0, exc)

        threading.Thread(target=work, daemon=True).start()

    def _fields_done(self, fields: List["cd.FieldDef"], n_records: int, exc: Optional[Exception]) -> None:
        self._busy(False, "就绪")
        if exc is not None:
            messagebox.showerror("解析字段失败", str(exc))
            return
        self.fields = fields
        titles = [f.title for f in fields]
        for combo in (self.name_combo, self.id_combo, self.attach_combo):
            combo.configure(values=[""] + titles)
        try:
            self.name_field_var.set(cd.pick_field(fields, None, ("姓名", "名字", "姓名（", "name"), "姓名").title)
        except Exception:  # noqa: BLE001
            self.name_field_var.set("")
        try:
            self.id_field_var.set(cd.pick_field(fields, None, ("身份证", "证件号", "证件编号", "身份号码"), "身份证号").title)
        except Exception:  # noqa: BLE001
            self.id_field_var.set("")
        try:
            self.attach_field_var.set(cd.pick_attachment_field(fields, None).title)
        except Exception:  # noqa: BLE001
            self.attach_field_var.set("")
        self.status_var.set(f"解析完成：{len(fields)} 个字段，{n_records} 条记录")

    # ------------------------------------------------------------------ 下载
    def on_start(self) -> None:
        if self.running:
            return
        try:
            client = self._client()
            sheet_id = self._current_sheet_id()
            out_dir = Path(self.out_var.get().strip()).expanduser()
            if not str(out_dir):
                raise cd.DocError("请选择保存目录")
            params = {
                "client": client,
                "sheet_id": sheet_id,
                "name_field": self.name_field_var.get().strip() or None,
                "id_field": self.id_field_var.get().strip() or None,
                "attach_field": self.attach_field_var.get().strip() or None,
                "out_dir": out_dir,
                "template": self.template_var.get().strip() or cd.DEFAULT_NAME_TEMPLATE,
                "prefix": self.prefix_var.get(),
                "sep": self.sep_var.get(),
                "concurrency": max(1, int(self.concurrency_var.get() or 8)),
                "timeout": float(self.timeout_var.get() or 60),
                "retries": max(0, int(self.retries_var.get() or 0)),
                "limit": max(0, int(self.limit_var.get() or 0)),
                "kinds": KIND_LABELS.get(self.kinds_var.get(), cd.KINDS_ALL),
                "overwrite": bool(self.overwrite_var.get()),
                "strict_id": bool(self.strict_var.get()),
                "report_format": self.report_var.get() or "csv",
            }
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("参数不完整", str(exc))
            return

        self.running = True
        self.cancel_event.clear()
        self.total = self.done = 0
        self.counts = {"ok": 0, "skipped": 0, "failed": 0, "cancelled": 0}
        self.progress.configure(value=0, maximum=1)
        self._busy(True, "准备中…")
        self._save_config()
        threading.Thread(target=self._run_download, args=(params,), daemon=True).start()

    def _run_download(self, p: Dict[str, Any]) -> None:
        t0 = time.time()
        try:
            out_dir: Path = p["out_dir"]
            out_dir.mkdir(parents=True, exist_ok=True)
            cd.log.info("保存目录：%s", out_dir)

            cd.log.info("读取子表…")
            jobs_all, _fields = cd.build_jobs(
                p["client"], p["sheet_id"], p["name_field"], p["id_field"], p["attach_field"],
                None, strict_id=p["strict_id"], kinds=p["kinds"])
            jobs = jobs_all[:p["limit"]] if p["limit"] else jobs_all
            if not jobs:
                raise cd.DocError("没有生成任何下载任务（可能姓名/身份证/附件列为空）")
            self._post(self._set_total, len(jobs))
            cd.log.info("下载任务 %d 个", len(jobs))

            cd.log.info("换取附件下载地址…")
            pad_id = p["client"].get_pad_id(p["sheet_id"])
            url_map = p["client"].fetch_attachment_urls(
                pad_id, [j.attachment.obj_key for j in jobs], cd.ACCESS_TYPE_DOWNLOAD)
            pending = []
            for j in jobs:
                j.url = url_map.get(j.attachment.obj_key, "")
                if j.url:
                    pending.append(j)
                else:
                    j.status, j.error = "failed", "未取到下载地址"
                    self._post(self._record, "failed")
            cd.log.info("取到下载地址 %d/%d", len(pending), len(jobs))

            cd.reset_allocated()
            pre = cd.snapshot_existing(out_dir, p["overwrite"])
            cd.log.info("开始下载（并发 %d）…", p["concurrency"])

            def one(job):
                r = cd.download_one(
                    job, client=p["client"], out_dir=out_dir, sep=p["sep"],
                    overwrite=p["overwrite"], timeout=p["timeout"], retries=p["retries"],
                    name_template=p["template"], prefix=p["prefix"],
                    cancel=self.cancel_event, pre_existing=pre)
                self._post(self._record, r.status)
                return r

            with ThreadPoolExecutor(max_workers=p["concurrency"]) as pool:
                futs = [pool.submit(one, j) for j in pending]
                for fut in as_completed(futs):
                    fut.result()

            cd.log.info("写报告…")
            ext = cd.exporter.get_format(p["report_format"]).ext
            cd.write_report(out_dir / f"download_report.{ext}", jobs,
                            fmt=p["report_format"], mapping="cert")

            self._post(self._finish, None, time.time() - t0)
        except Exception as exc:  # noqa: BLE001
            cd.log.error("失败：%s", exc)
            self._post(self._finish, exc, time.time() - t0)

    def _set_total(self, total: int) -> None:
        self.total = total
        self.progress.configure(maximum=max(1, total), value=0)

    def _record(self, status: str) -> None:
        self.done += 1
        self.counts[status] = self.counts.get(status, 0) + 1
        self.progress.configure(value=min(self.done, self.total))
        self.status_var.set(f"进行中 {self.done}/{self.total}")

    def _finish(self, exc: Optional[Exception], elapsed: float) -> None:
        self.running = False
        self._busy(False, "完成" if exc is None else "失败")
        c = self.counts
        summary = (f"成功 {c['ok']}，跳过 {c['skipped']}，失败 {c['failed']}，取消 {c['cancelled']}"
                   f"，耗时 {elapsed:.1f} 秒")
        if exc is not None:
            messagebox.showerror("下载失败", f"{exc}")
            self.status_var.set("失败")
            return
        self.status_var.set(summary)
        messagebox.showinfo("下载完成", f"{summary}\n\n文件已保存到：\n{self.out_var.get()}")

    def on_stop(self) -> None:
        if self.running:
            self.cancel_event.set()
            self.status_var.set("正在停止…（已开始的任务会跑完当前请求）")

    # ------------------------------------------------------------------ 配置
    def _config_dict(self) -> Dict[str, Any]:
        return {
            "doc": self.doc_var.get(),
            "out": self.out_var.get(),
            "preset": self.preset_var.get(),
            "template": self.template_var.get(),
            "prefix": self.prefix_var.get(),
            "sep": self.sep_var.get(),
            "concurrency": self.concurrency_var.get(),
            "timeout": self.timeout_var.get(),
            "retries": self.retries_var.get(),
            "limit": self.limit_var.get(),
            "kinds": self.kinds_var.get(),
            "overwrite": self.overwrite_var.get(),
            "strict": self.strict_var.get(),
            "report": self.report_var.get(),
        }

    def _save_config(self) -> None:
        try:
            CONFIG_PATH.write_text(json.dumps(self._config_dict(), ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        except OSError:
            pass

    def _load_config(self) -> None:
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        mapping = {
            "doc": self.doc_var, "out": self.out_var, "preset": self.preset_var,
            "template": self.template_var, "prefix": self.prefix_var, "sep": self.sep_var,
            "concurrency": self.concurrency_var, "timeout": self.timeout_var,
            "retries": self.retries_var, "limit": self.limit_var, "kinds": self.kinds_var,
            "report": self.report_var,
        }
        for key, var in mapping.items():
            if isinstance(data.get(key), str):
                var.set(data[key])
        self.overwrite_var.set(bool(data.get("overwrite", False)))
        self.strict_var.set(bool(data.get("strict", True)))

    def _on_close(self) -> None:
        if self.running and not messagebox.askyesno("正在下载", "下载还没结束，确定要退出吗？"):
            return
        self.cancel_event.set()
        self._save_config()
        self.root.destroy()


# ---------------------------------------------------------------------- 无界面自检
def run_selftest(doc: str, out: str, limit: int = 0) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")
    out_dir = Path(out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        doc_id = cd.TencentSmartsheetClient.parse_doc_id(doc)
        client = cd.TencentSmartsheetClient(doc_id)
        sheets = client.list_sheets()
        if not sheets:
            print("FAIL: 没有子表")
            return 1
        sheet_id = sheets[0]["id"]
        jobs_all, _ = cd.build_jobs(client, sheet_id, None, None, None, None,
                                    strict_id=True, kinds=cd.KINDS_ALL)
        jobs = jobs_all[:limit] if limit else jobs_all
        if not jobs:
            print("FAIL: 没有任务")
            return 1
        pad_id = client.get_pad_id(sheet_id)
        url_map = client.fetch_attachment_urls(
            pad_id, [j.attachment.obj_key for j in jobs], cd.ACCESS_TYPE_DOWNLOAD)
        cd.reset_allocated()
        pre = cd.snapshot_existing(out_dir, False)
        ok = 0
        for j in jobs:
            j.url = url_map.get(j.attachment.obj_key, "")
            cd.download_one(j, client=client, out_dir=out_dir, pre_existing=pre)
            ok += j.status == "ok"
        cd.write_report(out_dir / "download_report.csv", jobs, fmt="csv", mapping="cert")
        print(f"SELFTEST OK: {ok}/{len(jobs)} -> {out_dir}")
        return 0 if ok == len(jobs) else 2
    except Exception as exc:  # noqa: BLE001
        print(f"SELFTEST FAILED: {type(exc).__name__}: {exc}")
        return 1


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--version":
        print(APP_TITLE)
        return 0
    if argv and argv[0] == "--selftest":
        if len(argv) < 3:
            print("用法：cert_gui.py --selftest <文档链接/ID> <输出目录> [数量上限]")
            return 1
        limit = int(argv[3]) if len(argv) > 3 else 0
        return run_selftest(argv[1], argv[2], limit)

    root = tk.Tk()
    try:
        # Windows 高分屏下文字不糊
        if os.name == "nt":
            try:
                from ctypes import windll
                windll.shcore.SetProcessDpiAwareness(1)
            except Exception:  # noqa: BLE001
                pass
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except Exception:  # noqa: BLE001
        pass
    CertGui(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
