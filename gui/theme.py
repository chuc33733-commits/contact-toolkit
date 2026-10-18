# -*- coding: utf-8 -*-
"""
界面主题模块
集中管理配色、字体与 ttk 样式，保证整个应用视觉风格统一
"""

import tkinter as tk
from tkinter import ttk


# ---------------------------------------------------------------------------
# 设计令牌（Design Tokens）
# ---------------------------------------------------------------------------

# 字体：Windows 下使用微软雅黑，保证中文渲染清晰
FONT_FAMILY = "Microsoft YaHei UI"

FONT_SIZE_NORMAL = 10
FONT_SIZE_SMALL = 9
FONT_SIZE_TITLE = 15
FONT_SIZE_SECTION = 11

FONT_NORMAL = (FONT_FAMILY, FONT_SIZE_NORMAL)
FONT_SMALL = (FONT_FAMILY, FONT_SIZE_SMALL)
FONT_BOLD = (FONT_FAMILY, FONT_SIZE_NORMAL, "bold")
FONT_TITLE = (FONT_FAMILY, FONT_SIZE_TITLE, "bold")
FONT_SECTION = (FONT_FAMILY, FONT_SIZE_SECTION, "bold")

# 配色：浅色现代扁平风格
COLOR_BG = "#F2F4F8"            # 窗口底色
COLOR_SURFACE = "#FFFFFF"       # 卡片底色
COLOR_SURFACE_ALT = "#F8FAFC"   # 表头 / 斑马纹
COLOR_BORDER = "#E2E8F0"        # 边框
COLOR_TEXT = "#1F2937"          # 主文本
COLOR_TEXT_SECONDARY = "#64748B"  # 次要文本
COLOR_PRIMARY = "#2563EB"       # 主色（蓝）
COLOR_PRIMARY_HOVER = "#1D4ED8"
COLOR_PRIMARY_ACTIVE = "#1E40AF"
COLOR_PRIMARY_DISABLED = "#93C5FD"
COLOR_DANGER = "#DC2626"        # 危险操作（红）
COLOR_DANGER_HOVER = "#B91C1C"
COLOR_DANGER_ACTIVE = "#991B1B"
COLOR_TROUGH = "#E5E7EB"        # 进度条槽

# 统一间距
PAD_OUTER = 14      # 窗口外边距
PAD_CARD = 14       # 卡片内边距
PAD_GROUP = 10      # 组间距
PAD_WIDGET = 6      # 控件间距
PAD_ROW = 8         # 行间距


def apply_theme(root: tk.Misc) -> ttk.Style:
    """
    为应用配置统一的 ttk 主题样式。

    以 clam 主题为基底（可定制性最好），覆写按钮、标签、树形视图、
    进度条等控件的样式，使界面呈现统一的现代扁平风格。

    Args:
        root: Tk 根窗口或任意控件（用于获取默认字体）

    Returns:
        配置完成的 ttk.Style 实例
    """
    style = ttk.Style(root)
    # clam 是唯一允许完全自定义背景/边框的内置主题
    style.theme_use("clam")

    # ----- 全局基础 -----
    style.configure(
        ".",
        background=COLOR_BG,
        foreground=COLOR_TEXT,
        font=FONT_NORMAL,
        bordercolor=COLOR_BORDER,
        lightcolor=COLOR_SURFACE,
        darkcolor=COLOR_BORDER,
        troughcolor=COLOR_TROUGH,
        focuscolor=COLOR_PRIMARY,
    )

    # ----- 框架 -----
    style.configure("TFrame", background=COLOR_BG)
    style.configure("Surface.TFrame", background=COLOR_SURFACE)

    # ----- 标签 -----
    style.configure("TLabel", background=COLOR_BG, foreground=COLOR_TEXT)
    style.configure(
        "Surface.TLabel", background=COLOR_SURFACE, foreground=COLOR_TEXT
    )
    style.configure(
        "Secondary.TLabel", background=COLOR_BG, foreground=COLOR_TEXT_SECONDARY,
        font=FONT_SMALL,
    )
    style.configure(
        "Secondary.Surface.TLabel",
        background=COLOR_SURFACE, foreground=COLOR_TEXT_SECONDARY,
        font=FONT_SMALL,
    )
    style.configure(
        "Title.TLabel", background=COLOR_SURFACE, foreground=COLOR_TEXT,
        font=FONT_TITLE,
    )
    style.configure(
        "Subtitle.TLabel", background=COLOR_SURFACE,
        foreground=COLOR_TEXT_SECONDARY, font=FONT_SMALL,
    )
    style.configure(
        "Section.TLabel", background=COLOR_SURFACE, foreground=COLOR_TEXT,
        font=FONT_SECTION,
    )
    style.configure(
        "Caption.TLabel", background=COLOR_SURFACE,
        foreground=COLOR_TEXT_SECONDARY, font=FONT_SMALL,
    )
    style.configure(
        "Count.TLabel", background=COLOR_SURFACE_ALT,
        foreground=COLOR_PRIMARY, font=FONT_BOLD,
    )

    # ----- 按钮（默认：白底灰边扁平风） -----
    # 注意：clam 主题为 TButton 内置 width=-11（最小 11 个平均字符宽），
    # 短文本按钮会被强制拉宽导致工具栏溢出，这里收窄为 -7 让按钮贴合文本。
    style.configure(
        "TButton",
        font=FONT_NORMAL,
        padding=(12, 6),
        width=-7,
        background=COLOR_SURFACE,
        foreground=COLOR_TEXT,
        bordercolor=COLOR_BORDER,
        focusthickness=0,
        shiftrelief=0,
    )
    style.map(
        "TButton",
        background=[("disabled", COLOR_SURFACE_ALT), ("active", COLOR_SURFACE_ALT)],
        foreground=[("disabled", COLOR_TEXT_SECONDARY)],
        bordercolor=[("active", COLOR_TEXT_SECONDARY)],
    )

    # 主按钮：实心蓝底白字
    style.configure(
        "Primary.TButton",
        font=FONT_BOLD,
        padding=(12, 6),
        background=COLOR_PRIMARY,
        foreground="#FFFFFF",
        bordercolor=COLOR_PRIMARY,
    )
    style.map(
        "Primary.TButton",
        background=[
            ("disabled", COLOR_PRIMARY_DISABLED),
            ("pressed", COLOR_PRIMARY_ACTIVE),
            ("active", COLOR_PRIMARY_HOVER),
        ],
        bordercolor=[
            ("disabled", COLOR_PRIMARY_DISABLED),
            ("active", COLOR_PRIMARY_HOVER),
        ],
        foreground=[("disabled", "#FFFFFF")],
    )

    # 危险按钮：实心红底白字（用于清空等破坏性操作）
    style.configure(
        "Danger.TButton",
        font=FONT_NORMAL,
        padding=(12, 6),
        background=COLOR_DANGER,
        foreground="#FFFFFF",
        bordercolor=COLOR_DANGER,
    )
    style.map(
        "Danger.TButton",
        background=[
            ("disabled", "#FCA5A5"),
            ("pressed", COLOR_DANGER_ACTIVE),
            ("active", COLOR_DANGER_HOVER),
        ],
        bordercolor=[("disabled", "#FCA5A5"), ("active", COLOR_DANGER_HOVER)],
    )

    # ----- 输入框 -----
    style.configure(
        "TEntry",
        padding=(8, 6),
        fieldbackground=COLOR_SURFACE,
        foreground=COLOR_TEXT,
        bordercolor=COLOR_BORDER,
        insertcolor=COLOR_TEXT,
    )
    style.map(
        "TEntry",
        bordercolor=[("focus", COLOR_PRIMARY)],
        lightcolor=[("focus", COLOR_PRIMARY)],
    )

    # ----- 树形视图（联系人列表） -----
    style.configure(
        "Treeview",
        font=FONT_NORMAL,
        rowheight=30,
        background=COLOR_SURFACE,
        fieldbackground=COLOR_SURFACE,
        foreground=COLOR_TEXT,
        bordercolor=COLOR_BORDER,
    )
    style.map(
        "Treeview",
        background=[("selected", COLOR_PRIMARY)],
        foreground=[("selected", "#FFFFFF")],
    )
    style.configure(
        "Treeview.Heading",
        font=FONT_BOLD,
        background=COLOR_SURFACE_ALT,
        foreground="#374151",
        padding=(8, 8),
        bordercolor=COLOR_BORDER,
    )
    style.map(
        "Treeview.Heading",
        background=[("active", "#EDF2F7")],
    )

    # ----- 进度条 -----
    style.configure(
        "Horizontal.TProgressbar",
        background=COLOR_PRIMARY,
        troughcolor=COLOR_TROUGH,
        bordercolor=COLOR_TROUGH,
        lightcolor=COLOR_PRIMARY,
        darkcolor=COLOR_PRIMARY,
    )

    # ----- 分隔线 -----
    style.configure("TSeparator", background=COLOR_BORDER)

    # ----- LabelFrame（对话框内分组） -----
    style.configure(
        "TLabelframe",
        background=COLOR_SURFACE,
        bordercolor=COLOR_BORDER,
        font=FONT_NORMAL,
    )
    style.configure(
        "TLabelframe.Label",
        background=COLOR_SURFACE,
        foreground=COLOR_TEXT_SECONDARY,
        font=FONT_SMALL,
    )

    # ----- 滚动条 -----
    style.configure(
        "Vertical.TScrollbar",
        background=COLOR_SURFACE_ALT,
        troughcolor=COLOR_SURFACE,
        bordercolor=COLOR_BORDER,
        arrowcolor=COLOR_TEXT_SECONDARY,
    )
    style.configure(
        "Horizontal.TScrollbar",
        background=COLOR_SURFACE_ALT,
        troughcolor=COLOR_SURFACE,
        bordercolor=COLOR_BORDER,
        arrowcolor=COLOR_TEXT_SECONDARY,
    )

    # Tk 顶层控件默认底色
    root.configure(background=COLOR_BG)
    root.option_add("*Font", FONT_NORMAL)

    return style
