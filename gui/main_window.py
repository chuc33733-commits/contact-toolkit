# -*- coding: utf-8 -*-
"""
主窗口GUI界面
提供通讯录处理工具的主要用户界面
"""

import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from typing import List, Optional

from models.contact import Contact, ContactManager
from processors.excel_processor import ExcelProcessor
from processors.vcf_processor import VCFProcessor
from utils.merger import ContactMerger
from utils.logger import get_logger
from config.settings import APP_NAME, APP_VERSION, WINDOW_WIDTH, WINDOW_HEIGHT
from gui.theme import (
    apply_theme,
    PAD_OUTER,
    PAD_CARD,
    PAD_GROUP,
    PAD_WIDGET,
    PAD_ROW,
)


class MainWindow:
    """主窗口类"""

    def __init__(self):
        """初始化主窗口"""
        self.root = tk.Tk()
        self.contact_manager = ContactManager()
        self.excel_processor = ExcelProcessor()
        self.vcf_processor = VCFProcessor()
        self.merger = ContactMerger()
        self.logger = get_logger()

        # 界面组件
        self.contact_tree: Optional[ttk.Treeview] = None
        self.status_var: Optional[tk.StringVar] = None
        self.progress_var: Optional[tk.DoubleVar] = None
        self.progress_bar: Optional[ttk.Progressbar] = None
        self.count_var: Optional[tk.StringVar] = None
        self.search_var: Optional[tk.StringVar] = None

        # 当前列表中展示的联系人（与 Treeview 行一一对应，通过 iid 索引）
        self._displayed_contacts: List[Contact] = []

        # 状态变量（仅在主线程读写，后台线程通过 _post_ui 间接修改界面）
        self.is_processing = False

        # 后台线程 -> 主线程 的界面更新队列：
        # tkinter 的 after() 在非 mainloop 场景下从子线程调用会抛
        # "RuntimeError: main thread is not in main loop"，
        # 因此统一改为投递到队列，由主线程定时器消费，任何场景都安全。
        self._ui_queue: "queue.Queue" = queue.Queue()

        self._setup_window()
        self._create_widgets()
        self._bind_events()

        # 启动界面更新队列的消费循环（主线程定时器）
        self.root.after(50, self._poll_ui_queue)

        self.logger.info("主窗口初始化完成")

    # ------------------------------------------------------------------
    # 窗口与布局
    # ------------------------------------------------------------------

    def _setup_window(self):
        """设置窗口属性"""
        self.root.title(f"{APP_NAME} v{APP_VERSION}")
        self.root.minsize(1000, 640)

        # 应用统一主题
        apply_theme(self.root)

        # 居中显示窗口
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() - WINDOW_WIDTH) // 2
        y = (self.root.winfo_screenheight() - WINDOW_HEIGHT) // 2
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+{x}+{y}")

    def _create_widgets(self):
        """创建界面组件"""
        main_frame = ttk.Frame(self.root)
        main_frame.pack(
            fill=tk.BOTH, expand=True,
            padx=PAD_OUTER, pady=(PAD_OUTER, PAD_ROW),
        )

        self._create_header(main_frame)
        self._create_toolbar(main_frame)
        self._create_contact_list(main_frame)
        self._create_status_bar(main_frame)

    def _create_header(self, parent):
        """创建顶部标题栏"""
        header = ttk.Frame(parent, style="Surface.TFrame", padding=(PAD_CARD, PAD_CARD - 2))
        header.pack(fill=tk.X, pady=(0, PAD_ROW))

        left = ttk.Frame(header, style="Surface.TFrame")
        left.pack(side=tk.LEFT, fill=tk.Y)

        ttk.Label(left, text=APP_NAME, style="Title.TLabel").pack(anchor=tk.W)
        ttk.Label(
            left,
            text="Excel / VCF 联系人导入 · 合并去重 · 格式转换导出",
            style="Subtitle.TLabel",
        ).pack(anchor=tk.W, pady=(2, 0))

        ttk.Button(header, text="关于", command=self._show_about).pack(
            side=tk.RIGHT, anchor=tk.E
        )
        ttk.Label(
            header, text=f"v{APP_VERSION}", style="Count.TLabel", padding=(10, 4)
        ).pack(side=tk.RIGHT, anchor=tk.E, padx=(0, PAD_WIDGET))

    def _create_toolbar(self, parent):
        """创建分组工具栏"""
        toolbar = ttk.Frame(parent, style="Surface.TFrame", padding=(PAD_CARD, PAD_CARD - 4))
        toolbar.pack(fill=tk.X, pady=(0, PAD_ROW))

        # --- 导入组 ---
        import_group = self._make_group(toolbar, "导入")
        self._make_button(import_group, "导入 Excel", self._import_excel,
                          style="Primary.TButton")
        self._make_button(import_group, "导入 VCF", self._import_vcf,
                          style="Primary.TButton")
        self._make_button(import_group, "合并VCF", self._merge_vcf_files)

        self._make_separator(toolbar)

        # --- 导出组 ---
        export_group = self._make_group(toolbar, "导出")
        self._make_button(export_group, "导出VCF", self._export_vcf)
        self._make_button(export_group, "导出Excel", self._export_excel)

        self._make_separator(toolbar)

        # --- 工具组 ---
        tools_group = self._make_group(toolbar, "工具")
        self._make_button(tools_group, "合并重复", self._merge_duplicates)
        self._make_button(tools_group, "刷新", self._refresh_contact_list)
        self._make_button(tools_group, "清空", self._clear_contacts,
                          style="Danger.TButton")

    def _make_group(self, parent, caption: str) -> ttk.Frame:
        """
        在工具栏中创建一个带说明文字的功能分组。

        Args:
            parent: 工具栏容器
            caption: 分组标题（显示在按钮上方的小字）

        Returns:
            分组容器（按钮横向放入其中）
        """
        wrapper = ttk.Frame(parent, style="Surface.TFrame")
        wrapper.pack(side=tk.LEFT, padx=(0, PAD_GROUP))

        ttk.Label(wrapper, text=caption, style="Caption.TLabel").pack(anchor=tk.W)

        body = ttk.Frame(wrapper, style="Surface.TFrame")
        body.pack(anchor=tk.W, pady=(4, 0))
        return body

    def _make_button(self, parent, text: str, command, style: str = "TButton"):
        """创建一个工具栏按钮并保持统一间距"""
        ttk.Button(parent, text=text, command=command, style=style).pack(
            side=tk.LEFT, padx=(0, PAD_WIDGET)
        )

    def _make_separator(self, parent):
        """在工具栏分组之间创建竖直分隔线"""
        sep = ttk.Separator(parent, orient=tk.VERTICAL)
        sep.pack(side=tk.LEFT, fill=tk.Y, padx=(2, PAD_GROUP + 2), pady=(16, 2))

    def _create_contact_list(self, parent):
        """创建联系人列表面板"""
        panel = ttk.Frame(parent, style="Surface.TFrame", padding=(PAD_CARD, PAD_CARD - 4))
        panel.pack(fill=tk.BOTH, expand=True, pady=(0, PAD_ROW))

        # 面板头部：标题 + 数量徽章 + 搜索框
        head = ttk.Frame(panel, style="Surface.TFrame")
        head.pack(fill=tk.X, pady=(0, PAD_ROW))

        ttk.Label(head, text="联系人列表", style="Section.TLabel").pack(side=tk.LEFT)

        self.count_var = tk.StringVar(value="共 0 人")
        ttk.Label(
            head, textvariable=self.count_var, style="Count.TLabel", padding=(8, 2)
        ).pack(side=tk.LEFT, padx=(PAD_WIDGET, 0))

        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(head, textvariable=self.search_var, width=28)
        search_entry.pack(side=tk.RIGHT)
        ttk.Label(head, text="搜索", style="Secondary.Surface.TLabel").pack(
            side=tk.RIGHT, padx=(0, PAD_WIDGET)
        )
        self.search_var.trace_add("write", lambda *_: self._refresh_contact_list())

        # 列表主体：Treeview + 滚动条
        body = ttk.Frame(panel, style="Surface.TFrame")
        body.pack(fill=tk.BOTH, expand=True)

        columns = ("name", "phone", "email", "organization", "title")
        headings = {
            "name": ("姓名", 140),
            "phone": ("电话", 140),
            "email": ("邮箱", 190),
            "organization": ("公司", 170),
            "title": ("职位", 120),
        }
        self.contact_tree = ttk.Treeview(
            body, columns=columns, show="headings", selectmode="extended"
        )
        for col in columns:
            text, width = headings[col]
            # 点击表头可按该列排序（再次点击倒序）
            self.contact_tree.heading(
                col, text=text,
                command=lambda c=col: self._sort_by_column(c, False),
            )
            self.contact_tree.column(col, width=width, minwidth=80, anchor=tk.W)

        # 斑马纹
        self.contact_tree.tag_configure("odd", background="#F8FAFC")
        self.contact_tree.tag_configure("even", background="#FFFFFF")

        v_scrollbar = ttk.Scrollbar(
            body, orient=tk.VERTICAL, command=self.contact_tree.yview
        )
        h_scrollbar = ttk.Scrollbar(
            body, orient=tk.HORIZONTAL, command=self.contact_tree.xview
        )
        self.contact_tree.configure(
            yscrollcommand=v_scrollbar.set, xscrollcommand=h_scrollbar.set
        )

        self.contact_tree.grid(row=0, column=0, sticky="nsew")
        v_scrollbar.grid(row=0, column=1, sticky="ns")
        h_scrollbar.grid(row=1, column=0, sticky="ew")
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=1)

        # 右键菜单
        self._context_menu = tk.Menu(self.contact_tree, tearoff=0)
        self._context_menu.add_command(
            label="查看详情", command=self._show_selected_contact_details
        )
        self._context_menu.add_command(
            label="删除选中联系人", command=self._delete_selected_contacts
        )

    def _create_status_bar(self, parent):
        """创建底部状态栏"""
        status_frame = ttk.Frame(parent)
        status_frame.pack(fill=tk.X)

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(
            status_frame, textvariable=self.status_var, style="Secondary.TLabel"
        ).pack(side=tk.LEFT)

        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(
            status_frame,
            variable=self.progress_var,
            maximum=100,
            length=180,
            mode="determinate",
        )
        self.progress_bar.pack(side=tk.RIGHT)

        ttk.Label(
            status_frame, text="双击查看详情 · 右键更多操作",
            style="Secondary.TLabel",
        ).pack(side=tk.RIGHT, padx=(0, PAD_ROW))

    def _bind_events(self):
        """绑定事件"""
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

        # 联系人列表交互
        self.contact_tree.bind("<Double-1>", self._on_contact_double_click)
        self.contact_tree.bind("<Button-3>", self._on_contact_right_click)
        self.contact_tree.bind("<Delete>", lambda e: self._delete_selected_contacts())

        # 键盘快捷键
        self.root.bind("<Control-o>", lambda e: self._import_excel())
        self.root.bind("<Control-s>", lambda e: self._export_vcf())
        self.root.bind("<F5>", lambda e: self._refresh_contact_list())

    # ------------------------------------------------------------------
    # 线程安全的界面更新（队列 + 主线程定时器）
    # ------------------------------------------------------------------

    def _post_ui(self, func, *args, **kwargs):
        """
        将一个界面更新操作投递到主线程执行（可从任意线程调用）。

        Args:
            func: 需要在主线程执行的可调用对象
            *args: 位置参数
            **kwargs: 关键字参数
        """
        self._ui_queue.put((func, args, kwargs))

    def _poll_ui_queue(self):
        """主线程定时器：消费后台线程投递的界面更新队列"""
        try:
            while True:
                func, args, kwargs = self._ui_queue.get_nowait()
                try:
                    func(*args, **kwargs)
                except Exception as exc:  # 单个更新失败不应中断整个队列
                    self.logger.error(f"界面更新执行失败: {exc}")
        except queue.Empty:
            pass

        # 窗口销毁后停止调度
        try:
            self.root.after(50, self._poll_ui_queue)
        except tk.TclError:
            pass

    def _update_status(self, message: str):
        """更新状态栏文本（可安全地在后台线程中调用）"""
        self._post_ui(self.status_var.set, message)

    def _update_progress(self, value: float):
        """更新进度条（可安全地在后台线程中调用）"""
        self._post_ui(self.progress_var.set, value)

    def _begin_busy(self, message: str):
        """标记开始处理（在主线程调用）：更新状态并复位进度条"""
        self.is_processing = True
        self._update_status(message)
        self._update_progress(0)

    def _end_busy(self):
        """标记处理结束（可在后台线程调用）"""
        self.is_processing = False
        self._update_progress(0)

    # ------------------------------------------------------------------
    # 列表展示
    # ------------------------------------------------------------------

    def _update_contact_count(self):
        """更新联系人数量显示"""
        count = self.contact_manager.count()
        self.count_var.set(f"共 {count} 人")

    def _match_search(self, contact: Contact, keyword: str) -> bool:
        """
        判断联系人是否命中搜索关键字。

        Args:
            contact: 联系人对象
            keyword: 已转小写的关键字

        Returns:
            姓名 / 电话 / 邮箱 / 公司 / 职位任一包含关键字即命中
        """
        fields = [contact.name, contact.organization, contact.title, contact.address]
        fields.extend(contact.nicknames)
        fields.extend(contact.categories)
        fields.extend(contact.phones)
        fields.extend(contact.emails)
        return any(keyword in (field or "").lower() for field in fields)

    def _refresh_contact_list(self):
        """按搜索关键字刷新联系人列表"""
        for item in self.contact_tree.get_children():
            self.contact_tree.delete(item)

        keyword = (self.search_var.get() or "").strip().lower()
        contacts = self.contact_manager.get_all_contacts()
        if keyword:
            contacts = [c for c in contacts if self._match_search(c, keyword)]

        self._displayed_contacts = contacts

        for index, contact in enumerate(contacts):
            name = contact.name or "未知"
            phone = contact.get_primary_phone() or "—"
            email = contact.get_primary_email() or "—"
            organization = contact.organization or "—"
            title = contact.title or "—"
            tag = "even" if index % 2 == 0 else "odd"
            self.contact_tree.insert(
                "", tk.END, iid=str(index),
                values=(name, phone, email, organization, title),
                tags=(tag,),
            )

        self._update_contact_count()
        self.logger.info(f"联系人列表已刷新，共 {len(contacts)} 个联系人")

    def _sort_by_column(self, column: str, reverse: bool):
        """
        按指定列对当前展示内容排序。

        Args:
            column: 列标识（name / phone / email / organization / title）
            reverse: 是否倒序（连续点击同一列表头时切换）
        """
        key_map = {
            "name": lambda c: (c.name or "").lower(),
            "phone": lambda c: c.get_primary_phone() or "",
            "email": lambda c: c.get_primary_email() or "",
            "organization": lambda c: (c.organization or "").lower(),
            "title": lambda c: (c.title or "").lower(),
        }
        key_func = key_map.get(column)
        if not key_func:
            return

        self._displayed_contacts.sort(key=key_func, reverse=reverse)

        # 依据排好序的 _displayed_contacts 重新排列 Treeview 行
        for index, _contact in enumerate(self._displayed_contacts):
            self.contact_tree.move(str(index), "", index)
            tag = "even" if index % 2 == 0 else "odd"
            self.contact_tree.item(str(index), tags=(tag,))

        # 再次点击同一表头时切换排序方向
        self.contact_tree.heading(
            column, command=lambda: self._sort_by_column(column, not reverse)
        )

    # ------------------------------------------------------------------
    # 导入
    # ------------------------------------------------------------------

    def _import_excel(self):
        """导入Excel文件"""
        if self.is_processing:
            messagebox.showwarning("警告", "正在处理中，请稍候...")
            return

        file_path = filedialog.askopenfilename(
            title="选择Excel文件",
            filetypes=[("Excel文件", "*.xlsx *.xls *.xlsm"), ("所有文件", "*.*")],
        )
        if not file_path:
            return

        self._begin_busy("正在导入 Excel 文件...")
        threading.Thread(
            target=self._process_excel_import, args=(file_path,), daemon=True
        ).start()

    def _process_excel_import(self, file_path: str):
        """处理Excel导入（后台线程）"""
        try:
            imported_manager, stats, error = self.excel_processor.import_from_excel(file_path)
            if error:
                raise Exception(error)

            imported_count = self._merge_imported_manager(imported_manager)

            self._update_progress(100)
            self._post_ui(self._refresh_contact_list)
            self._update_status("Excel 导入完成")
            self._post_ui(messagebox.showinfo, "成功", self._build_import_message(
                "Excel", imported_count, stats
            ))
            self.logger.log_import_operation(file_path, imported_count, True)

        except Exception as e:
            error_msg = str(e)
            self._update_status("Excel 导入失败")
            self._post_ui(
                messagebox.showerror, "错误", f"导入Excel文件失败：{error_msg}"
            )
            self.logger.log_import_operation(file_path, 0, False, error_msg)

        finally:
            self._end_busy()

    @staticmethod
    def _build_import_message(source: str, imported_count: int, stats: dict) -> str:
        """
        构造导入结果提示文本。

        Args:
            source: 数据来源说明（Excel / VCF）
            imported_count: 实际导入的联系人数
            stats: 处理器返回的统计信息

        Returns:
            提示文本
        """
        lines = [f"成功从 {source} 导入 {imported_count} 个联系人"]
        photos = stats.get('photos_imported', 0)
        if photos:
            lines.append(f"其中照片 {photos} 张")
        warnings = stats.get('warnings') or []
        if warnings:
            lines.append(f"有 {len(warnings)} 条内容被跳过：")
            lines.extend(f"· {item}" for item in warnings[:5])
            if len(warnings) > 5:
                lines.append(f"· 另有 {len(warnings) - 5} 条，详见日志")
        return "\n".join(lines)

    def _merge_imported_manager(self, imported_manager: ContactManager) -> int:
        """
        把导入结果并入主联系人列表。

        Args:
            imported_manager: 导入得到的管理器

        Returns:
            导入的联系人数量
        """
        imported_count = 0
        for contact in imported_manager.get_all_contacts():
            self.contact_manager.add_contact(contact)
            imported_count += 1
        return imported_count

    def _import_vcf(self):
        """导入VCF文件"""
        if self.is_processing:
            messagebox.showwarning("警告", "正在处理中，请稍候...")
            return

        file_path = filedialog.askopenfilename(
            title="选择VCF文件",
            filetypes=[("VCF文件", "*.vcf *.vcard"), ("所有文件", "*.*")],
        )
        if not file_path:
            return

        self._begin_busy("正在导入 VCF 文件...")
        threading.Thread(
            target=self._process_vcf_import, args=(file_path,), daemon=True
        ).start()

    def _process_vcf_import(self, file_path: str):
        """处理VCF导入（后台线程）"""
        try:
            imported_manager, stats, error = self.vcf_processor.import_from_vcf(file_path)
            if error:
                raise Exception(error)

            imported_count = self._merge_imported_manager(imported_manager)

            self._update_progress(100)
            self._post_ui(self._refresh_contact_list)
            self._update_status("VCF 导入完成")
            self._post_ui(messagebox.showinfo, "成功", self._build_import_message(
                "VCF", imported_count, stats
            ))
            self.logger.log_import_operation(file_path, imported_count, True)

        except Exception as e:
            error_msg = str(e)
            self._update_status("VCF 导入失败")
            self._post_ui(
                messagebox.showerror, "错误", f"导入VCF文件失败：{error_msg}"
            )
            self.logger.log_import_operation(file_path, 0, False, error_msg)

        finally:
            self._end_busy()

    # ------------------------------------------------------------------
    # VCF 文件合并
    # ------------------------------------------------------------------

    def _merge_vcf_files(self):
        """合并多个VCF文件"""
        if self.is_processing:
            messagebox.showwarning("警告", "正在处理中，请稍候...")
            return

        file_paths = filedialog.askopenfilenames(
            title="选择要合并的VCF文件",
            filetypes=[("VCF文件", "*.vcf *.vcard"), ("所有文件", "*.*")],
        )
        if not file_paths:
            return

        output_path = filedialog.asksaveasfilename(
            title="保存合并后的VCF文件",
            defaultextension=".vcf",
            filetypes=[("VCF文件", "*.vcf"), ("所有文件", "*.*")],
        )
        if not output_path:
            return

        self._begin_busy("正在合并 VCF 文件...")
        threading.Thread(
            target=self._process_vcf_merge,
            args=(file_paths, output_path),
            daemon=True,
        ).start()

    def _process_vcf_merge(self, file_paths: List[str], output_path: str):
        """处理VCF合并（后台线程）"""
        try:
            # merge_vcf_files 返回 (统计信息, 错误信息)
            merge_stats, error = self.vcf_processor.merge_vcf_files(
                file_paths, output_path, merge_duplicates=True
            )
            if error:
                raise Exception(error)

            self._update_progress(100)

            # 统计字段名与 VCFProcessor.merge_vcf_files 的返回保持一致
            message = (
                f"VCF文件合并完成！\n"
                f"输入文件数: {merge_stats.get('input_files', 0)}\n"
                f"导入联系人数: {merge_stats.get('total_contacts_imported', 0)}\n"
                f"发现重复组: {merge_stats.get('duplicate_groups', 0)}\n"
                f"合并数量: {merge_stats.get('duplicates_merged', 0)}\n"
                f"最终数量: {merge_stats.get('merged_contacts', 0)}\n"
                f"输出文件: {output_path}"
            )

            self._update_status("VCF 合并完成")
            self._post_ui(messagebox.showinfo, "成功", message)
            self.logger.info(f"VCF文件合并完成: {output_path}")

        except Exception as e:
            error_msg = str(e)
            self._update_status("VCF 合并失败")
            self._post_ui(
                messagebox.showerror, "错误", f"合并VCF文件失败：{error_msg}"
            )
            self.logger.error(f"VCF文件合并失败: {error_msg}")

        finally:
            self._end_busy()

    # ------------------------------------------------------------------
    # 导出
    # ------------------------------------------------------------------

    def _export_vcf(self):
        """导出为VCF文件"""
        if self.contact_manager.count() == 0:
            messagebox.showwarning("警告", "没有联系人可以导出")
            return

        file_path = filedialog.asksaveasfilename(
            title="保存VCF文件",
            defaultextension=".vcf",
            filetypes=[("VCF文件", "*.vcf"), ("所有文件", "*.*")],
        )
        if not file_path:
            return

        self._begin_busy("正在导出 VCF 文件...")
        threading.Thread(
            target=self._process_vcf_export, args=(file_path,), daemon=True
        ).start()

    def _process_vcf_export(self, file_path: str):
        """处理VCF导出（后台线程）"""
        try:
            contacts = self.contact_manager.get_all_contacts()
            self.vcf_processor.export_contacts(contacts, file_path)

            self._update_progress(100)
            self._update_status("VCF 导出完成")
            self._post_ui(
                messagebox.showinfo,
                "成功", f"成功导出 {len(contacts)} 个联系人到 {file_path}",
            )
            self.logger.log_export_operation(file_path, len(contacts), True)

        except Exception as e:
            error_msg = str(e)
            self._update_status("VCF 导出失败")
            self._post_ui(
                messagebox.showerror, "错误", f"导出VCF文件失败：{error_msg}"
            )
            self.logger.log_export_operation(file_path, 0, False, error_msg)

        finally:
            self._end_busy()

    def _export_excel(self):
        """导出为Excel文件"""
        if self.contact_manager.count() == 0:
            messagebox.showwarning("警告", "没有联系人可以导出")
            return

        file_path = filedialog.asksaveasfilename(
            title="保存Excel文件",
            defaultextension=".xlsx",
            filetypes=[("Excel文件", "*.xlsx"), ("所有文件", "*.*")],
        )
        if not file_path:
            return

        self._begin_busy("正在导出 Excel 文件...")
        threading.Thread(
            target=self._process_excel_export, args=(file_path,), daemon=True
        ).start()

    def _process_excel_export(self, file_path: str):
        """处理Excel导出（后台线程）"""
        try:
            contacts = self.contact_manager.get_all_contacts()
            self.excel_processor.export_contacts(contacts, file_path)

            self._update_progress(100)
            self._update_status("Excel 导出完成")
            self._post_ui(
                messagebox.showinfo,
                "成功", f"成功导出 {len(contacts)} 个联系人到 {file_path}",
            )
            self.logger.log_export_operation(file_path, len(contacts), True)

        except Exception as e:
            error_msg = str(e)
            self._update_status("Excel 导出失败")
            self._post_ui(
                messagebox.showerror, "错误", f"导出Excel文件失败：{error_msg}"
            )
            self.logger.log_export_operation(file_path, 0, False, error_msg)

        finally:
            self._end_busy()

    # ------------------------------------------------------------------
    # 工具操作
    # ------------------------------------------------------------------

    def _merge_duplicates(self):
        """合并重复联系人"""
        if self.contact_manager.count() == 0:
            messagebox.showwarning("警告", "没有联系人可以合并")
            return

        if self.is_processing:
            messagebox.showwarning("警告", "正在处理中，请稍候...")
            return

        result = messagebox.askyesno(
            "确认", "是否要合并重复的联系人？\n此操作不可撤销。"
        )
        if not result:
            return

        self._begin_busy("正在合并重复联系人...")
        threading.Thread(target=self._process_merge_duplicates, daemon=True).start()

    def _process_merge_duplicates(self):
        """处理重复联系人合并（后台线程）"""
        try:
            merge_stats = self.merger.merge_duplicates_in_manager(
                self.contact_manager
            )

            self._update_progress(100)
            self._post_ui(self._refresh_contact_list)

            message = (
                f"重复联系人合并完成！\n"
                f"原始数量: {merge_stats['total_contacts_before']}\n"
                f"发现重复组: {merge_stats['duplicate_groups_found']}\n"
                f"合并数量: {merge_stats['contacts_merged']}\n"
                f"最终数量: {merge_stats['total_contacts_after']}"
            )

            self._update_status("重复联系人合并完成")
            self._post_ui(messagebox.showinfo, "成功", message)
            self.logger.log_merge_operation(merge_stats)

        except Exception as e:
            error_msg = str(e)
            self._update_status("合并失败")
            self._post_ui(
                messagebox.showerror, "错误", f"合并重复联系人失败：{error_msg}"
            )
            self.logger.error(f"合并重复联系人失败: {error_msg}")

        finally:
            self._end_busy()

    def _clear_contacts(self):
        """清空所有联系人"""
        if self.contact_manager.count() == 0:
            messagebox.showinfo("提示", "联系人列表已经为空")
            return

        result = messagebox.askyesno(
            "确认",
            f"是否要清空所有 {self.contact_manager.count()} 个联系人？\n"
            f"此操作不可撤销。",
        )
        if result:
            self.contact_manager.clear()
            self._refresh_contact_list()
            self._update_status("联系人已清空")
            self.logger.info("所有联系人已清空")

    def _delete_selected_contacts(self):
        """删除列表中选中的联系人"""
        selection = self.contact_tree.selection()
        if not selection:
            return

        count = len(selection)
        result = messagebox.askyesno(
            "确认", f"是否删除选中的 {count} 个联系人？\n此操作不可撤销。"
        )
        if not result:
            return

        removed = 0
        for iid in selection:
            try:
                contact = self._displayed_contacts[int(iid)]
            except (IndexError, ValueError):
                continue
            if self.contact_manager.remove_contact(contact):
                removed += 1

        self._refresh_contact_list()
        self._update_status(f"已删除 {removed} 个联系人")
        self.logger.info(f"已删除 {removed} 个联系人")

    # ------------------------------------------------------------------
    # 联系人详情
    # ------------------------------------------------------------------

    def _get_selected_contact(self) -> Optional[Contact]:
        """
        获取当前选中的第一个联系人。

        Returns:
            选中的 Contact 对象，未选中或索引失效时返回 None
        """
        selection = self.contact_tree.selection()
        if not selection:
            return None
        try:
            return self._displayed_contacts[int(selection[0])]
        except (IndexError, ValueError):
            return None

    def _on_contact_double_click(self, event):
        """联系人双击事件：显示完整详情"""
        self._show_selected_contact_details()

    def _on_contact_right_click(self, event):
        """联系人右键事件：选中目标行并弹出上下文菜单"""
        iid = self.contact_tree.identify_row(event.y)
        if iid:
            # 若右键行不在当前选择中，则改为仅选中该行
            if iid not in self.contact_tree.selection():
                self.contact_tree.selection_set(iid)
            self._context_menu.tk_popup(event.x_root, event.y_root)

    def _show_selected_contact_details(self):
        """以详情对话框展示选中联系人"""
        contact = self._get_selected_contact()
        if contact is None:
            return

        from gui.dialogs import ContactDetailsDialog

        dialog = ContactDetailsDialog(self.root, contact.to_dict())
        dialog.show()

    # ------------------------------------------------------------------
    # 其他
    # ------------------------------------------------------------------

    def _show_about(self):
        """显示关于对话框"""
        about_text = f"""
{APP_NAME} v{APP_VERSION}

通讯录处理工具

主要功能：
• Excel / VCF 文件导入并转换为 VCF 格式
• 重复联系人智能合并（信息少者并入信息多者，信息不丢失）
• 相似姓名取简洁写法，括号备注自动移入备注字段
• 支持 姓名/昵称/电话/邮箱/公司/职位/地址/生日/农历生日/照片/网站/备注/分类

开发语言：Python + Tkinter
        """
        messagebox.showinfo("关于", about_text.strip())

    def _on_closing(self):
        """窗口关闭事件"""
        if self.is_processing:
            result = messagebox.askyesno("确认", "正在处理中，是否要强制退出？")
            if not result:
                return

        self.logger.info("应用程序退出")
        self.root.destroy()

    def run(self):
        """运行主窗口"""
        self.logger.info("启动主窗口")
        self.root.mainloop()


if __name__ == "__main__":
    app = MainWindow()
    app.run()
