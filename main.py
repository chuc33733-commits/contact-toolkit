# -*- coding: utf-8 -*-
"""
通讯录处理工具 - 主程序入口
"""

import sys
import os
import tkinter as tk
from tkinter import messagebox
import traceback

# 添加项目根目录到Python路径
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

try:
    from config.settings import APP_CONFIG
    from gui.main_window import MainWindow
    from utils.logger import setup_logger, info, error
except ImportError as e:
    print(f"导入模块失败: {e}")
    print("请确保所有依赖已正确安装")
    import traceback
    traceback.print_exc()
    sys.exit(1)


def setup_application():
    """设置应用程序"""
    try:
        # 设置日志
        setup_logger()
        info("应用程序启动")
        
        # 创建主应用窗口
        app = MainWindow()
        
        return app.root, app
        
    except Exception as e:
        error_msg = f"应用程序初始化失败: {str(e)}"
        error(error_msg)
        messagebox.showerror("错误", error_msg)
        return None, None


def handle_exception(exc_type, exc_value, exc_traceback):
    """全局异常处理器"""
    if issubclass(exc_type, KeyboardInterrupt):
        # 允许Ctrl+C正常退出
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    
    error_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    error(f"未处理的异常: {error_msg}")
    
    # 显示错误对话框
    messagebox.showerror(
        "程序错误",
        f"程序遇到未处理的错误:\n\n{str(exc_value)}\n\n"
        f"详细信息已记录到日志文件中。"
    )


def main():
    """主函数"""
    try:
        # 设置全局异常处理器
        sys.excepthook = handle_exception
        
        # 设置应用程序
        root, app = setup_application()
        
        if root is None or app is None:
            sys.exit(1)
        
        # 设置窗口关闭事件
        def on_closing():
            """窗口关闭事件处理"""
            try:
                info("应用程序正在关闭")
                root.quit()
                root.destroy()
            except Exception as e:
                error(f"关闭应用程序时出错: {str(e)}")
            finally:
                sys.exit(0)
        
        root.protocol("WM_DELETE_WINDOW", on_closing)
        
        # 启动主循环
        info("进入主事件循环")
        root.mainloop()
        
    except KeyboardInterrupt:
        info("用户中断程序")
        sys.exit(0)
    except Exception as e:
        error_msg = f"程序启动失败: {str(e)}"
        error(error_msg)
        print(error_msg)
        
        # 尝试显示错误对话框
        try:
            root = tk.Tk()
            root.withdraw()  # 隐藏主窗口
            messagebox.showerror("启动错误", error_msg)
            root.destroy()
        except Exception:
            pass
        
        sys.exit(1)


if __name__ == "__main__":
    main()