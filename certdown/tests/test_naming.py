#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""命名模板引擎单元测试（仅用标准库 unittest，任何环境都能跑）。

运行：python -m unittest discover -s tests -v
    或 python tests/test_naming.py
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cert_downloader as cd            # noqa: E402


def make_job(name: str = "张三",
             id_no: str = "510101200001011234",
             att_name: str = "证书扫描件.jpg",
             ext: str = "jpg",
             seq: int = 1,
             n_total: int = 1,
             index: int = 1,
             record_id: str = "fr1") -> cd.Job:
    """构造一个测试用 Job。"""
    return cd.Job(
        index=index, record_id=record_id, name=name, id_no=id_no,
        attachment=cd.Attachment(obj_key="a" * 32, name=att_name, ext=ext,
                                 size=1024, kind="image"),
        seq=seq, n_total=n_total)


class TestBuildStem(unittest.TestCase):
    """模板渲染。"""

    def test_default_equals_legacy_behavior(self) -> None:
        """默认模板必须与改造前的「姓名+身份证」行为完全一致。"""
        job = make_job()
        self.assertEqual(cd.build_stem(job), "张三510101200001011234")
        self.assertEqual(cd.build_stem(job, sep="_"), "张三_510101200001011234")

    def test_multi_image_suffix(self) -> None:
        """同一记录多张图时第 2 张起追加 _2 / _3。"""
        j1 = make_job(seq=1, n_total=2)
        j2 = make_job(seq=2, n_total=2)
        self.assertEqual(cd.build_stem(j1), "张三510101200001011234")
        self.assertEqual(cd.build_stem(j2), "张三510101200001011234_2")

    def test_title_variable(self) -> None:
        """{title} 取附件原始文件名（去扩展名）。"""
        job = make_job(att_name="无尘_2026-07-22 21.45.58_1000046414_01.jpg")
        self.assertEqual(cd.build_stem(job, "{title}"),
                         "无尘_2026-07-22 21.45.58_1000046414_01")

    def test_title_fallback_when_empty(self) -> None:
        """附件没有原始文件名时回退到姓名，避免出现空文件名。"""
        job = make_job(att_name="")
        self.assertEqual(cd.build_stem(job, "{title}"), "张三")

    def test_time_variables(self) -> None:
        """{date} / {time} / {datetime} 走传入的时间基准。"""
        job = make_job()
        now = datetime(2026, 9, 9, 13, 5, 7)
        self.assertEqual(cd.build_stem(job, "{date}", now=now), "20260909")
        self.assertEqual(cd.build_stem(job, "{time}", now=now), "130507")
        self.assertEqual(cd.build_stem(job, "{datetime}", now=now), "20260909_130507")

    def test_prefix_variable(self) -> None:
        """{prefix} 拼在最前面。"""
        job = make_job()
        self.assertEqual(cd.build_stem(job, "{prefix}{name}_{id}", prefix="2026春_"),
                         "2026春_张三_510101200001011234")

    def test_id_variants(self) -> None:
        """身份证脱敏与后四位。"""
        job = make_job()
        self.assertEqual(cd.build_stem(job, "{id_masked}"), "5101**********1234")
        self.assertEqual(cd.build_stem(job, "{id_last4}"), "1234")

    def test_index_zero_padding(self) -> None:
        """{index:03} 支持补零。"""
        self.assertEqual(cd.build_stem(make_job(index=7), "{index:03}_{name}"), "007_张三")

    def test_unknown_variable_kept(self) -> None:
        """未知变量原样保留，不抛异常（方便用户发现拼写错误）。"""
        self.assertEqual(cd.build_stem(make_job(), "{name}_{nope}"), "张三_{nope}")

    def test_empty_template_falls_back(self) -> None:
        """空模板回退到默认，不会生成空文件名。"""
        self.assertEqual(cd.build_stem(make_job(), ""), "张三510101200001011234")

    def test_sample_mode_hides_real_data(self) -> None:
        """预览模式用示例值，不泄露真实姓名/身份证。"""
        job = make_job(name="真名", id_no="510101200001011234")
        stem = cd.build_stem(job, "{name}{id}", sample=True)
        self.assertNotIn("真名", stem)
        self.assertNotIn("510101200001011234", stem)

    def test_all_presets_render(self) -> None:
        """所有预设方案都能渲染出非空且合法的文件名。"""
        for label, tpl in cd.NAME_PRESETS:
            with self.subTest(preset=label):
                name = cd.resolve_filename(make_job(), tpl, "前缀_", "_")
                self.assertTrue(name.endswith(".jpg"))
                self.assertGreater(len(name), len(".jpg"))
                self.assertEqual(name, cd.sanitize_filename(name))  # 不应残留非法字符

    def test_sanitize_illegal_chars(self) -> None:
        """姓名/标题里带 Windows 非法字符时会被替换。"""
        job = make_job(name='李/四\\:*?"<>|')
        name = cd.resolve_filename(job, "{name}")
        for ch in '/\\:*?"<>|':
            self.assertNotIn(ch, name)

    def test_long_name_truncated(self) -> None:
        """超长姓名被截断到 120 字符以内。"""
        job = make_job(name="啊" * 300)
        stem = cd.sanitize_filename(cd.build_stem(job, "{name}"))
        self.assertLessEqual(len(stem), 120)


class TestUniquePath(unittest.TestCase):
    """同名冲突处理。"""

    def test_appends_suffix(self) -> None:
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            p = d / "a.jpg"
            p.write_bytes(b"x")
            self.assertEqual(cd._unique_path(p, False).name, "a_2.jpg")
            (d / "a_2.jpg").write_bytes(b"x")
            self.assertEqual(cd._unique_path(p, False).name, "a_3.jpg")

    def test_overwrite(self) -> None:
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "a.jpg"
            p.write_bytes(b"x")
            self.assertEqual(cd._unique_path(p, True).name, "a.jpg")


class TestAllocatePath(unittest.TestCase):
    """落盘路径分配：续跑跳过 vs 同批重复追加 _2。"""

    def test_pre_existing_is_skipped(self) -> None:
        """重跑时启动前就存在的文件应被跳过。"""
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "a.jpg").write_bytes(b"x")
            target, skip = cd.allocate_path(d, "a", "jpg", False,
                                            cd.snapshot_existing(d))
            self.assertTrue(skip)
            self.assertEqual(target.name, "a.jpg")

    def test_same_batch_duplicate_gets_suffix(self) -> None:
        """同一批内两个相同文件名 -> 第二个追加 _2，两份都保留。"""
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            cd.reset_allocated()
            t1, s1 = cd.allocate_path(d, "a", "jpg", False, set())
            t1.write_bytes(b"x")                      # 第一个任务已落盘
            t2, s2 = cd.allocate_path(d, "a", "jpg", False, set())
            self.assertFalse(s1)
            self.assertFalse(s2)
            self.assertEqual(t1.name, "a.jpg")
            self.assertEqual(t2.name, "a_2.jpg")

    def test_overwrite_reuses_same_path(self) -> None:
        """覆盖模式下不跳过、不加后缀。"""
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "a.jpg").write_bytes(b"x")
            cd.reset_allocated()
            t, s = cd.allocate_path(d, "a", "jpg", True, set())
            self.assertFalse(s)
            self.assertEqual(t.name, "a.jpg")

    def test_concurrent_allocation_is_unique(self) -> None:
        """并发分配 20 个同名任务，路径必须两两不同（模拟同一人重复提交）。"""
        import threading
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            cd.reset_allocated()
            got: list = []
            lock = threading.Lock()

            def worker() -> None:
                t, _ = cd.allocate_path(d, "a", "jpg", False, set())
                t.write_bytes(b"x")
                with lock:
                    got.append(t)

            threads = [threading.Thread(target=worker) for _ in range(20)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(len({p.name for p in got}), 20, "并发下出现了重复路径")


class TestSniffExt(unittest.TestCase):
    """扩展名魔数校正。"""

    def test_magic_detection(self) -> None:
        self.assertEqual(cd.sniff_ext(b"\xff\xd8\xff\xe0" + b"0" * 32), "jpg")
        self.assertEqual(cd.sniff_ext(b"\x89PNG\r\n\x1a\n" + b"0" * 32), "png")
        self.assertEqual(cd.sniff_ext(b"RIFF\x00\x00\x00\x00WEBP" + b"0" * 8), "webp")
        self.assertEqual(cd.sniff_ext(b"not an image at all"), "")

    def test_real_files_in_output(self) -> None:
        """用已下载的真实图片校验魔数判定（存在输出目录时）。"""
        out = Path(__file__).resolve().parent.parent / "证书输出"
        samples = [p for p in out.glob("*.jp*g")][:5] + [p for p in out.glob("*.png")][:5]
        if not samples:
            self.skipTest("没有已下载的真实图片")
        for p in samples:
            with self.subTest(file=p.name):
                real = cd.sniff_ext(p.read_bytes()[:16])
                self.assertIn(real, {"jpg", "png", "webp", "gif", "bmp", "tif", "heic", ""})
                if real:
                    self.assertEqual(p.suffix.lower().lstrip("."), real,
                                     f"{p.name} 的扩展名与真实格式不符")


if __name__ == "__main__":
    unittest.main(verbosity=2)
