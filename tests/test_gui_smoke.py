# -*- coding: utf-8 -*-
"""
GUI 冒烟测试
通过 mock 文件对话框 / 消息框，对主窗口的核心交互流程做端到端验证：
导入 Excel / VCF、搜索过滤、列排序、合并重复、删除、清空、导出、合并 VCF 文件
"""

import os
import sys
import tempfile
import time
import unittest
from unittest import mock

# 添加项目根目录到Python路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

import tkinter as tk  # noqa: E402

from gui import main_window as mw  # noqa: E402


# 测试用样例文件（位于项目根目录）
SAMPLE_EXCEL = os.path.join(project_root, "联系人导入模板.xlsx")
SAMPLE_FULL_TEMPLATE = os.path.join(project_root, "联系人导入模板_全字段.xlsx")
SAMPLE_VCF = os.path.join(project_root, "11.vcf")
SAMPLE_VCF_2 = os.path.join(project_root, "去重后.vcf")


class MainWindowSmokeTest(unittest.TestCase):
    """主窗口核心交互冒烟测试"""

    @classmethod
    def setUpClass(cls):
        """创建主窗口实例（整个测试类共用一个窗口）"""
        cls.app = mw.MainWindow()
        cls.root = cls.app.root
        # 消息框全部静默处理，避免阻塞测试
        cls._msg_patcher = mock.patch.object(mw.messagebox, "showinfo")
        cls._msg_patcher.start()
        cls._warn_patcher = mock.patch.object(mw.messagebox, "showwarning")
        cls._warn_patcher.start()

    @classmethod
    def tearDownClass(cls):
        cls._msg_patcher.stop()
        cls._warn_patcher.stop()
        try:
            cls.root.destroy()
        except tk.TclError:
            pass

    def setUp(self):
        # 每个用例开始前清空联系人，保证互不影响
        self.app.contact_manager.clear()
        self.app.search_var.set("")
        self.app._refresh_contact_list()
        self.pump()

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def pump(self, seconds: float = 0.05):
        """驱动 Tk 事件循环，让 after() 调度的界面更新得到执行"""
        deadline = time.time() + seconds
        while time.time() < deadline:
            self.root.update()

    def wait_idle(self, timeout: float = 30.0):
        """等待后台线程处理完成"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.root.update()
            if not self.app.is_processing:
                # 再多跑一拍，确保 after(0, ...) 回调都执行完
                self.pump()
                return
            time.sleep(0.05)
        self.fail("后台处理超时未结束")

    def tree_rows(self) -> int:
        """当前列表行数"""
        return len(self.app.contact_tree.get_children())

    # ------------------------------------------------------------------
    # 用例
    # ------------------------------------------------------------------

    def test_01_initial_state(self):
        """初始状态：列表为空、计数为零、状态就绪"""
        self.assertEqual(self.app.contact_manager.count(), 0)
        self.assertEqual(self.tree_rows(), 0)
        self.assertEqual(self.app.count_var.get(), "共 0 人")
        self.assertEqual(self.app.status_var.get(), "就绪")

    def test_02_import_excel(self):
        """导入 Excel：联系人和列表行数增加"""
        self.assertTrue(os.path.exists(SAMPLE_EXCEL), "样例 Excel 不存在")
        with mock.patch.object(
            mw.filedialog, "askopenfilename", return_value=SAMPLE_EXCEL
        ):
            self.app._import_excel()
        self.wait_idle()

        count = self.app.contact_manager.count()
        self.assertGreater(count, 0, "Excel 导入后联系人为空")
        self.assertEqual(self.tree_rows(), count)
        self.assertEqual(self.app.count_var.get(), f"共 {count} 人")
        self.assertEqual(self.app.status_var.get(), "Excel 导入完成")

    def test_03_import_vcf(self):
        """导入 VCF：联系人和列表行数增加"""
        self.assertTrue(os.path.exists(SAMPLE_VCF), "样例 VCF 不存在")
        with mock.patch.object(
            mw.filedialog, "askopenfilename", return_value=SAMPLE_VCF
        ):
            self.app._import_vcf()
        self.wait_idle()

        count = self.app.contact_manager.count()
        self.assertGreater(count, 0, "VCF 导入后联系人为空")
        self.assertEqual(self.tree_rows(), count)
        self.assertEqual(self.app.status_var.get(), "VCF 导入完成")

    def test_04_import_cancel(self):
        """取消文件选择：不发生任何变化"""
        with mock.patch.object(mw.filedialog, "askopenfilename", return_value=""):
            self.app._import_excel()
            self.app._import_vcf()
        self.pump()
        self.assertEqual(self.app.contact_manager.count(), 0)
        self.assertFalse(self.app.is_processing)

    def test_05_import_bad_file(self):
        """导入不存在的文件：状态变为失败且不崩溃"""
        bad_path = os.path.join(tempfile.gettempdir(), "not_exist_12345.xlsx")
        with mock.patch.object(mw.messagebox, "showerror") as err:
            with mock.patch.object(
                mw.filedialog, "askopenfilename", return_value=bad_path
            ):
                self.app._import_excel()
            self.wait_idle()
        self.assertTrue(err.called, "导入失败未弹出错误提示")
        self.assertEqual(self.app.status_var.get(), "Excel 导入失败")
        self.assertEqual(self.app.contact_manager.count(), 0)

    def test_06_search_filter(self):
        """搜索过滤：仅展示匹配的联系人，清空关键字后恢复"""
        with mock.patch.object(
            mw.filedialog, "askopenfilename", return_value=SAMPLE_VCF
        ):
            self.app._import_vcf()
        self.wait_idle()
        total = self.tree_rows()
        self.assertGreater(total, 0)

        # 取第一行姓名作为关键字
        first = self.app._displayed_contacts[0]
        keyword = first.name[:2] if first.name and len(first.name) >= 2 else (
            first.get_primary_phone()[:4] if first.get_primary_phone() else ""
        )
        self.assertTrue(keyword, "无法构造搜索关键字")

        self.app.search_var.set(keyword)
        self.pump()
        filtered = self.tree_rows()
        self.assertGreater(filtered, 0)
        self.assertLess(filtered, total, "搜索未起到过滤作用")

        self.app.search_var.set("")
        self.pump()
        self.assertEqual(self.tree_rows(), total)

    def test_07_sort_by_column(self):
        """列排序：按姓名排序后首行应不大于次行，再点一次倒序"""
        with mock.patch.object(
            mw.filedialog, "askopenfilename", return_value=SAMPLE_VCF
        ):
            self.app._import_vcf()
        self.wait_idle()

        self.app._sort_by_column("name", False)
        self.pump()
        names = [c.name or "" for c in self.app._displayed_contacts]
        self.assertEqual(names, sorted(names, key=str.lower))

        self.app._sort_by_column("name", True)
        self.pump()
        names = [c.name or "" for c in self.app._displayed_contacts]
        self.assertEqual(names, sorted(names, key=str.lower, reverse=True))

    def test_08_merge_duplicates(self):
        """合并重复：数量不增，统计字段完整"""
        with mock.patch.object(
            mw.filedialog, "askopenfilename", return_value=SAMPLE_VCF
        ):
            self.app._import_vcf()
        self.wait_idle()
        before = self.app.contact_manager.count()

        with mock.patch.object(mw.messagebox, "askyesno", return_value=True):
            self.app._merge_duplicates()
        self.wait_idle()

        after = self.app.contact_manager.count()
        self.assertLessEqual(after, before, "合并后联系人数量反而增加")
        self.assertEqual(self.tree_rows(), after)
        self.assertEqual(self.app.count_var.get(), f"共 {after} 人")

    def test_09_export_vcf(self):
        """导出 VCF：文件生成且非空"""
        with mock.patch.object(
            mw.filedialog, "askopenfilename", return_value=SAMPLE_VCF
        ):
            self.app._import_vcf()
        self.wait_idle()

        fd, out_path = tempfile.mkstemp(suffix=".vcf")
        os.close(fd)
        try:
            with mock.patch.object(
                mw.filedialog, "asksaveasfilename", return_value=out_path
            ):
                self.app._export_vcf()
            self.wait_idle()
            self.assertTrue(os.path.exists(out_path))
            self.assertGreater(os.path.getsize(out_path), 0, "导出的 VCF 为空")
        finally:
            os.unlink(out_path)

    def test_10_export_excel(self):
        """导出 Excel：文件生成且非空"""
        with mock.patch.object(
            mw.filedialog, "askopenfilename", return_value=SAMPLE_EXCEL
        ):
            self.app._import_excel()
        self.wait_idle()

        fd, out_path = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        try:
            with mock.patch.object(
                mw.filedialog, "asksaveasfilename", return_value=out_path
            ):
                self.app._export_excel()
            self.wait_idle()
            self.assertTrue(os.path.exists(out_path))
            self.assertGreater(os.path.getsize(out_path), 0, "导出的 Excel 为空")
        finally:
            os.unlink(out_path)

    def test_11_export_empty_warning(self):
        """空列表导出：弹警告且不创建文件"""
        with mock.patch.object(mw.messagebox, "showwarning") as warn:
            self.app._export_vcf()
            self.app._export_excel()
        self.pump()
        self.assertEqual(warn.call_count, 2)

    def test_12_merge_vcf_files(self):
        """合并 VCF 文件：输出文件生成且非空"""
        fd, out_path = tempfile.mkstemp(suffix=".vcf")
        os.close(fd)
        try:
            with mock.patch.object(
                mw.filedialog, "askopenfilenames",
                return_value=[SAMPLE_VCF, SAMPLE_VCF_2],
            ), mock.patch.object(
                mw.filedialog, "asksaveasfilename", return_value=out_path
            ):
                self.app._merge_vcf_files()
            self.wait_idle(timeout=60.0)
            self.assertTrue(os.path.exists(out_path))
            self.assertGreater(os.path.getsize(out_path), 0, "合并输出为空")
            # 合并流程必须真正走完（回归：曾经把返回值元组当字典用导致必然报错）
            self.assertEqual(self.app.status_var.get(), "VCF 合并完成")
        finally:
            os.unlink(out_path)

    def test_13_clear_contacts(self):
        """清空：联系人与列表归零；空列表再次清空只提示"""
        with mock.patch.object(
            mw.filedialog, "askopenfilename", return_value=SAMPLE_VCF
        ):
            self.app._import_vcf()
        self.wait_idle()
        self.assertGreater(self.app.contact_manager.count(), 0)

        with mock.patch.object(mw.messagebox, "askyesno", return_value=True):
            self.app._clear_contacts()
        self.pump()
        self.assertEqual(self.app.contact_manager.count(), 0)
        self.assertEqual(self.tree_rows(), 0)
        self.assertEqual(self.app.count_var.get(), "共 0 人")

        # 空列表再清空 → 仅提示
        with mock.patch.object(mw.messagebox, "showinfo") as info:
            self.app._clear_contacts()
        self.assertTrue(info.called)

    def test_14_delete_selected(self):
        """删除选中联系人：数量减一"""
        with mock.patch.object(
            mw.filedialog, "askopenfilename", return_value=SAMPLE_VCF
        ):
            self.app._import_vcf()
        self.wait_idle()
        before = self.app.contact_manager.count()

        first_iid = self.app.contact_tree.get_children()[0]
        self.app.contact_tree.selection_set(first_iid)
        with mock.patch.object(mw.messagebox, "askyesno", return_value=True):
            self.app._delete_selected_contacts()
        self.pump()

        self.assertEqual(self.app.contact_manager.count(), before - 1)
        self.assertEqual(self.tree_rows(), before - 1)

    def test_15_contact_details_dialog(self):
        """双击查看详情：详情对话框正常创建并展示数据"""
        with mock.patch.object(
            mw.filedialog, "askopenfilename", return_value=SAMPLE_VCF
        ):
            self.app._import_vcf()
        self.wait_idle()

        first_iid = self.app.contact_tree.get_children()[0]
        self.app.contact_tree.selection_set(first_iid)

        # 在非阻塞模式下打开对话框，验证存在后关闭
        contact = self.app._get_selected_contact()
        self.assertIsNotNone(contact)

        from gui.dialogs import ContactDetailsDialog

        dialog = ContactDetailsDialog(self.root, contact.to_dict())
        self.pump()
        self.assertTrue(dialog.dialog.winfo_exists())
        title = dialog.dialog.title()
        self.assertEqual(title, "联系人详情")
        dialog.dialog.destroy()
        self.pump()

    def test_16_import_full_field_template(self):
        """导入全字段模板：新字段与照片进入列表，导出 Excel 正常"""
        if not os.path.exists(SAMPLE_FULL_TEMPLATE):
            self.skipTest("全字段模板不存在，请先运行 generate_excel_template.py")

        with mock.patch.object(
            mw.filedialog, "askopenfilename", return_value=SAMPLE_FULL_TEMPLATE
        ):
            self.app._import_excel()
        self.wait_idle()

        contacts = self.app.contact_manager.get_all_contacts()
        self.assertGreater(len(contacts), 0, "全字段模板导入后联系人为空")
        self.assertTrue(any(c.photos for c in contacts), "照片字段未被导入")
        self.assertTrue(any(c.lunar_birthday for c in contacts), "农历生日未被导入")
        self.assertEqual(self.app.status_var.get(), "Excel 导入完成")

        # 导出 Excel 并清理照片目录
        out_dir = tempfile.mkdtemp(prefix="gui_export_")
        out_path = os.path.join(out_dir, "out.xlsx")
        try:
            with mock.patch.object(
                mw.filedialog, "asksaveasfilename", return_value=out_path
            ):
                self.app._export_excel()
            self.wait_idle()
            self.assertTrue(os.path.exists(out_path))
            self.assertGreater(os.path.getsize(out_path), 0)
        finally:
            import shutil
            shutil.rmtree(out_dir, ignore_errors=True)

    def test_17_merge_rules_end_to_end(self):
        """GUI 端到端：括注姓名取简洁名、信息少者并入信息多者"""
        vcf_text = """BEGIN:VCARD
VERSION:3.0
FN:张三（老师）
TEL;TYPE=CELL:13812345678
END:VCARD

BEGIN:VCARD
VERSION:3.0
FN:张三
TEL;TYPE=CELL:13812345678
ADR:;;成都市高新区;;;;
EMAIL:xiao@example.com
END:VCARD
"""
        fd, path = tempfile.mkstemp(suffix=".vcf")
        os.close(fd)
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(vcf_text)

        try:
            with mock.patch.object(
                mw.filedialog, "askopenfilename", return_value=path
            ):
                self.app._import_vcf()
            self.wait_idle()
            self.assertEqual(self.app.contact_manager.count(), 2)

            with mock.patch.object(mw.messagebox, "askyesno", return_value=True):
                self.app._merge_duplicates()
            self.wait_idle()

            self.assertEqual(self.app.contact_manager.count(), 1)
            merged = self.app.contact_manager.get_all_contacts()[0]
            self.assertEqual(merged.name, "张三")
            self.assertIn("老师", merged.notes)
            self.assertEqual(merged.address, "成都市高新区")
            self.assertEqual(merged.emails, ["xiao@example.com"])
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
