# -*- coding: utf-8 -*-
"""
对话框模块
提供各种用户交互对话框
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from typing import Optional, List, Dict, Any, Callable

from gui.theme import COLOR_SURFACE
from utils.field_utils import summarize_photo


class ProgressDialog:
    """进度对话框"""

    def __init__(self, parent, title: str = "处理中", message: str = "请稍候..."):
        """
        初始化进度对话框

        Args:
            parent: 父窗口
            title: 对话框标题
            message: 显示消息
        """
        self.parent = parent
        self.dialog = None
        self.progress_var = None
        self.message_var = None
        self.is_cancelled = False

        self._create_dialog(title, message)

    def _create_dialog(self, title: str, message: str):
        """创建对话框"""
        self.dialog = tk.Toplevel(self.parent)
        self.dialog.title(title)
        self.dialog.geometry("400x150")
        self.dialog.resizable(False, False)
        self.dialog.transient(self.parent)
        self.dialog.grab_set()

        # 居中显示
        self.dialog.update_idletasks()
        x = (self.dialog.winfo_screenwidth() - 400) // 2
        y = (self.dialog.winfo_screenheight() - 150) // 2
        self.dialog.geometry(f"400x150+{x}+{y}")

        # 创建内容
        main_frame = ttk.Frame(self.dialog, padding=20)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 消息标签
        self.message_var = tk.StringVar(value=message)
        message_label = ttk.Label(main_frame, textvariable=self.message_var)
        message_label.pack(pady=(0, 10))

        # 进度条
        self.progress_var = tk.DoubleVar()
        progress_bar = ttk.Progressbar(
            main_frame,
            variable=self.progress_var,
            maximum=100,
            length=300
        )
        progress_bar.pack(pady=(0, 10))

        # 取消按钮
        cancel_button = ttk.Button(
            main_frame,
            text="取消",
            command=self._on_cancel
        )
        cancel_button.pack()

        # 禁用关闭按钮
        self.dialog.protocol("WM_DELETE_WINDOW", self._on_cancel)

    def _on_cancel(self):
        """取消按钮点击事件"""
        self.is_cancelled = True

    def update_progress(self, value: float, message: str = None):
        """
        更新进度

        Args:
            value: 进度值 (0-100)
            message: 可选的消息更新
        """
        if self.dialog and self.dialog.winfo_exists():
            self.progress_var.set(value)
            if message:
                self.message_var.set(message)
            self.dialog.update_idletasks()

    def close(self):
        """关闭对话框"""
        if self.dialog and self.dialog.winfo_exists():
            self.dialog.destroy()


class FileSelectionDialog:
    """文件选择对话框"""

    @staticmethod
    def select_excel_file(parent, title: str = "选择Excel文件") -> Optional[str]:
        """
        选择Excel文件

        Args:
            parent: 父窗口
            title: 对话框标题

        Returns:
            选择的文件路径或None
        """
        return filedialog.askopenfilename(
            parent=parent,
            title=title,
            filetypes=[
                ("Excel文件", "*.xlsx *.xls *.xlsm"),
                ("所有文件", "*.*")
            ]
        )

    @staticmethod
    def select_vcf_file(parent, title: str = "选择VCF文件") -> Optional[str]:
        """
        选择VCF文件

        Args:
            parent: 父窗口
            title: 对话框标题

        Returns:
            选择的文件路径或None
        """
        return filedialog.askopenfilename(
            parent=parent,
            title=title,
            filetypes=[
                ("VCF文件", "*.vcf *.vcard"),
                ("所有文件", "*.*")
            ]
        )

    @staticmethod
    def select_multiple_vcf_files(parent, title: str = "选择VCF文件") -> List[str]:
        """
        选择多个VCF文件

        Args:
            parent: 父窗口
            title: 对话框标题

        Returns:
            选择的文件路径列表
        """
        return list(filedialog.askopenfilenames(
            parent=parent,
            title=title,
            filetypes=[
                ("VCF文件", "*.vcf *.vcard"),
                ("所有文件", "*.*")
            ]
        ))

    @staticmethod
    def save_vcf_file(parent, title: str = "保存VCF文件", default_name: str = "contacts.vcf") -> Optional[str]:
        """
        保存VCF文件

        Args:
            parent: 父窗口
            title: 对话框标题
            default_name: 默认文件名

        Returns:
            保存的文件路径或None
        """
        return filedialog.asksaveasfilename(
            parent=parent,
            title=title,
            defaultextension=".vcf",
            initialfile=default_name,
            filetypes=[
                ("VCF文件", "*.vcf"),
                ("所有文件", "*.*")
            ]
        )

    @staticmethod
    def save_excel_file(parent, title: str = "保存Excel文件", default_name: str = "contacts.xlsx") -> Optional[str]:
        """
        保存Excel文件

        Args:
            parent: 父窗口
            title: 对话框标题
            default_name: 默认文件名

        Returns:
            保存的文件路径或None
        """
        return filedialog.asksaveasfilename(
            parent=parent,
            title=title,
            defaultextension=".xlsx",
            initialfile=default_name,
            filetypes=[
                ("Excel文件", "*.xlsx"),
                ("所有文件", "*.*")
            ]
        )


class ConfirmDialog:
    """确认对话框"""

    @staticmethod
    def ask_yes_no(parent, title: str, message: str) -> bool:
        """
        询问是/否

        Args:
            parent: 父窗口
            title: 对话框标题
            message: 消息内容

        Returns:
            用户选择结果
        """
        return messagebox.askyesno(title, message, parent=parent)

    @staticmethod
    def ask_ok_cancel(parent, title: str, message: str) -> bool:
        """
        询问确定/取消

        Args:
            parent: 父窗口
            title: 对话框标题
            message: 消息内容

        Returns:
            用户选择结果
        """
        return messagebox.askokcancel(title, message, parent=parent)


class MessageDialog:
    """消息对话框"""

    @staticmethod
    def show_info(parent, title: str, message: str):
        """
        显示信息

        Args:
            parent: 父窗口
            title: 对话框标题
            message: 消息内容
        """
        messagebox.showinfo(title, message, parent=parent)

    @staticmethod
    def show_warning(parent, title: str, message: str):
        """
        显示警告

        Args:
            parent: 父窗口
            title: 对话框标题
            message: 消息内容
        """
        messagebox.showwarning(title, message, parent=parent)

    @staticmethod
    def show_error(parent, title: str, message: str):
        """
        显示错误

        Args:
            parent: 父窗口
            title: 对话框标题
            message: 消息内容
        """
        messagebox.showerror(title, message, parent=parent)


class ContactDetailsDialog:
    """联系人详情对话框"""

    def __init__(self, parent, contact_data: Dict[str, Any], title: str = "联系人详情"):
        """
        初始化联系人详情对话框

        Args:
            parent: 父窗口
            contact_data: 联系人数据
            title: 对话框标题
        """
        self.parent = parent
        self.contact_data = contact_data
        self.dialog = None

        self._create_dialog(title)

    def _create_dialog(self, title: str):
        """创建对话框"""
        self.dialog = tk.Toplevel(self.parent)
        self.dialog.title(title)
        self.dialog.geometry("500x600")
        self.dialog.resizable(True, True)
        self.dialog.transient(self.parent)
        self.dialog.grab_set()

        # 相对父窗口居中显示
        self.dialog.update_idletasks()
        try:
            px = self.parent.winfo_rootx()
            py = self.parent.winfo_rooty()
            pw = self.parent.winfo_width()
            ph = self.parent.winfo_height()
            x = px + max((pw - 500) // 2, 0)
            y = py + max((ph - 600) // 2, 0)
        except Exception:
            x = (self.dialog.winfo_screenwidth() - 500) // 2
            y = (self.dialog.winfo_screenheight() - 600) // 2
        self.dialog.geometry(f"500x600+{x}+{y}")

        # 创建内容
        self._create_content()

    def _create_content(self):
        """创建对话框内容"""
        main_frame = ttk.Frame(self.dialog, padding=12, style="Surface.TFrame")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 按钮框架先固定在底部，避免被侧向 pack 的内容挤占
        button_frame = ttk.Frame(main_frame, style="Surface.TFrame")
        button_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(10, 0))
        ttk.Button(
            button_frame, text="关闭", command=self.dialog.destroy
        ).pack(side=tk.RIGHT)

        # 内容滚动区
        content = ttk.Frame(main_frame, style="Surface.TFrame")
        content.pack(fill=tk.BOTH, expand=True)

        canvas = tk.Canvas(
            content, background=COLOR_SURFACE, highlightthickness=0
        )
        scrollbar = ttk.Scrollbar(content, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas, style="Surface.TFrame")

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas_window = canvas.create_window(
            (0, 0), window=scrollable_frame, anchor="nw"
        )
        canvas.configure(yscrollcommand=scrollbar.set)

        # 内容宽度随对话框宽度自适应
        canvas.bind(
            "<Configure>",
            lambda e: canvas.itemconfigure(canvas_window, width=e.width),
        )
        # 鼠标滚轮滚动（对话框销毁后自动失效，防止向已销毁控件发命令）
        def _on_mousewheel(event):
            if canvas.winfo_exists():
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        # 添加联系人信息
        self._add_contact_info(scrollable_frame)

        # 布局
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _add_contact_info(self, parent):
        """添加联系人信息"""
        # 基本信息
        basic_frame = ttk.LabelFrame(parent, text="基本信息", padding=10)
        basic_frame.pack(fill=tk.X, pady=(0, 10))

        self._add_info_row(basic_frame, "姓名", self.contact_data.get('name', ''))
        nicknames = self.contact_data.get('nicknames', [])
        if nicknames:
            self._add_info_row(basic_frame, "昵称", ', '.join(nicknames))
        self._add_info_row(basic_frame, "公司", self.contact_data.get('organization', ''))
        self._add_info_row(basic_frame, "职位", self.contact_data.get('title', ''))

        # 联系方式
        contact_frame = ttk.LabelFrame(parent, text="联系方式", padding=10)
        contact_frame.pack(fill=tk.X, pady=(0, 10))

        phones = self.contact_data.get('phones', [])
        if phones:
            for i, phone in enumerate(phones):
                self._add_info_row(contact_frame, f"电话{i+1}", phone)
        else:
            self._add_info_row(contact_frame, "电话", "无")

        emails = self.contact_data.get('emails', [])
        if emails:
            for i, email in enumerate(emails):
                self._add_info_row(contact_frame, f"邮箱{i+1}", email)
        else:
            self._add_info_row(contact_frame, "邮箱", "无")

        self._add_info_row(contact_frame, "网站", self.contact_data.get('website', ''))

        # 其他信息
        other_frame = ttk.LabelFrame(parent, text="其他信息", padding=10)
        other_frame.pack(fill=tk.X, pady=(0, 10))

        self._add_info_row(other_frame, "地址", self.contact_data.get('address', ''))
        self._add_info_row(other_frame, "生日", self.contact_data.get('birthday', ''))
        self._add_info_row(other_frame, "农历生日", self.contact_data.get('lunar_birthday', ''))

        # 照片：只展示摘要，避免在界面上渲染超大 base64
        photos = self.contact_data.get('photos', [])
        if photos:
            for i, photo in enumerate(photos):
                summary = summarize_photo(photo)
                self._add_info_row(other_frame, f"照片{i+1}", summary)
        else:
            self._add_info_row(other_frame, "照片", "无")

        self._add_info_row(other_frame, "备注", self.contact_data.get('notes', ''))

        # 分类标签
        categories = self.contact_data.get('categories', [])
        if categories:
            self._add_info_row(other_frame, "分类", ', '.join(categories))

        # 原样保留的 vCard 属性（如 IMPP、X-PHONETIC-*）
        extras = self.contact_data.get('extra_vcard_fields', [])
        if extras:
            extra_frame = ttk.LabelFrame(parent, text="保留的原始字段", padding=10)
            extra_frame.pack(fill=tk.X, pady=(0, 10))
            for i, line in enumerate(extras):
                self._add_info_row(extra_frame, f"字段{i+1}", line)

    def _add_info_row(self, parent, label: str, value: str):
        """添加信息行（长文本自动换行，避免撑破对话框）"""
        if not value:
            value = "无"

        row_frame = ttk.Frame(parent, style="Surface.TFrame")
        row_frame.pack(fill=tk.X, pady=3)

        label_widget = ttk.Label(
            row_frame, text=f"{label}:", width=10, anchor="nw",
            style="Surface.TLabel",
        )
        label_widget.pack(side=tk.LEFT, anchor=tk.N)

        value_widget = ttk.Label(
            row_frame, text=value, anchor="w", wraplength=360,
            justify=tk.LEFT, style="Surface.TLabel",
        )
        value_widget.pack(side=tk.LEFT, fill=tk.X, expand=True)

    def show(self):
        """显示对话框"""
        self.dialog.wait_window()


class MergePreviewDialog:
    """合并预览对话框"""

    def __init__(self, parent, merge_operations: List[Dict], title: str = "合并预览"):
        """
        初始化合并预览对话框

        Args:
            parent: 父窗口
            merge_operations: 合并操作列表
            title: 对话框标题
        """
        self.parent = parent
        self.merge_operations = merge_operations
        self.dialog = None
        self.result = False

        self._create_dialog(title)

    def _create_dialog(self, title: str):
        """创建对话框"""
        self.dialog = tk.Toplevel(self.parent)
        self.dialog.title(title)
        self.dialog.geometry("800x600")
        self.dialog.resizable(True, True)
        self.dialog.transient(self.parent)
        self.dialog.grab_set()

        # 居中显示
        self.dialog.update_idletasks()
        x = (self.dialog.winfo_screenwidth() - 800) // 2
        y = (self.dialog.winfo_screenheight() - 600) // 2
        self.dialog.geometry(f"800x600+{x}+{y}")

        # 创建内容
        self._create_content()

    def _create_content(self):
        """创建对话框内容"""
        main_frame = ttk.Frame(self.dialog, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 标题
        title_label = ttk.Label(
            main_frame,
            text=f"发现 {len(self.merge_operations)} 组重复联系人，是否继续合并？",
            font=("", 12, "bold")
        )
        title_label.pack(pady=(0, 10))

        # 创建树形视图
        columns = ("原始联系人", "合并后", "合并原因")
        tree = ttk.Treeview(main_frame, columns=columns, show="headings", height=15)

        for col in columns:
            tree.heading(col, text=col)
            tree.column(col, width=250, minwidth=200)

        # 添加合并操作数据
        for i, operation in enumerate(self.merge_operations):
            original_names = [contact['name'] for contact in operation['original_contacts']]
            merged_name = operation['merged_contact']['name']
            reasons = ', '.join(operation['merge_reasons'])

            tree.insert("", tk.END, values=(
                ' + '.join(original_names),
                merged_name,
                reasons
            ))

        # 滚动条
        scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)

        # 布局树形视图
        tree_frame = ttk.Frame(main_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # 按钮框架
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X)

        ttk.Button(button_frame, text="确定合并", command=self._on_confirm).pack(side=tk.RIGHT, padx=(5, 0))
        ttk.Button(button_frame, text="取消", command=self._on_cancel).pack(side=tk.RIGHT)

    def _on_confirm(self):
        """确认合并"""
        self.result = True
        self.dialog.destroy()

    def _on_cancel(self):
        """取消合并"""
        self.result = False
        self.dialog.destroy()

    def show(self) -> bool:
        """
        显示对话框

        Returns:
            用户选择结果
        """
        self.dialog.wait_window()
        return self.result


class InputDialog:
    """输入对话框"""

    def __init__(self, parent, title: str, prompt: str, default_value: str = ""):
        """
        初始化输入对话框

        Args:
            parent: 父窗口
            title: 对话框标题
            prompt: 提示文本
            default_value: 默认值
        """
        self.parent = parent
        self.dialog = None
        self.result = None
        self.entry_var = tk.StringVar(value=default_value)

        self._create_dialog(title, prompt)

    def _create_dialog(self, title: str, prompt: str):
        """创建对话框"""
        self.dialog = tk.Toplevel(self.parent)
        self.dialog.title(title)
        self.dialog.geometry("400x150")
        self.dialog.resizable(False, False)
        self.dialog.transient(self.parent)
        self.dialog.grab_set()

        # 居中显示
        self.dialog.update_idletasks()
        x = (self.dialog.winfo_screenwidth() - 400) // 2
        y = (self.dialog.winfo_screenheight() - 150) // 2
        self.dialog.geometry(f"400x150+{x}+{y}")

        # 创建内容
        main_frame = ttk.Frame(self.dialog, padding=20)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 提示标签
        prompt_label = ttk.Label(main_frame, text=prompt)
        prompt_label.pack(pady=(0, 10))

        # 输入框
        entry = ttk.Entry(main_frame, textvariable=self.entry_var, width=40)
        entry.pack(pady=(0, 10))
        entry.focus()

        # 按钮框架
        button_frame = ttk.Frame(main_frame)
        button_frame.pack()

        ttk.Button(button_frame, text="确定", command=self._on_ok).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(button_frame, text="取消", command=self._on_cancel).pack(side=tk.LEFT)

        # 绑定回车键
        entry.bind("<Return>", lambda e: self._on_ok())
        self.dialog.bind("<Escape>", lambda e: self._on_cancel())

    def _on_ok(self):
        """确定按钮"""
        self.result = self.entry_var.get()
        self.dialog.destroy()

    def _on_cancel(self):
        """取消按钮"""
        self.result = None
        self.dialog.destroy()

    def show(self) -> Optional[str]:
        """
        显示对话框

        Returns:
            用户输入的值或None
        """
        self.dialog.wait_window()
        return self.result
