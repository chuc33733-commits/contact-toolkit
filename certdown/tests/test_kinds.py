#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""附件范围（kinds）与「非图片附件」下载的单元测试。

覆盖：
  * build_jobs(kinds="image") 只留图片（历史行为不变）
  * build_jobs(kinds="all") 保留 PDF / Word / 压缩包等任意附件
  * 同一记录「图片 + PDF」混合时按 _2/_3 正确编号
  * download_one 对非图片附件：不按图片魔数改扩展名、不因体积 <512B 判失败、
    猜不到扩展名时退化成 .bin 而不是 .jpg
  * 小体积 PDF（真实字节数远小于 512）能正常落盘

运行：python3 tests/test_kinds.py
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cert_downloader as cd  # noqa: E402

NAME_FIELD = "姓名（必填）"
ID_FIELD = "身份证号（必填）"
ATT_FIELD = "证书附件"

JPEG = b"\xff\xd8\xff" + b"\x00" * 900          # >512B 的假 JPEG
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"   # 仅 44 字节
DOCX = b"PK\x03\x04" + b"\x00" * 300            # 假 docx（zip 容器）
TINY_BIN = b"x" * 20                            # 极小且无魔数


def cell_of(*atts) -> dict:
    """构造附件单元格。每个 att 为 (ext, size, kind)。"""
    items = []
    for i, (ext, size, kind) in enumerate(atts, start=1):
        items.append({"k5": kind, "k1": f"原始文件{i}.{ext}", "k2": size,
                      "k4": f"objkey{i:02d}" + "0" * 26, "k3": ext})
    return {"k6": items, "k30": cd.FIELD_TYPE_ATTACHMENT}


def rec(rid: str, name: str, id_no: str, *atts) -> cd.Record:
    return cd.Record(record_id=rid, cells={
        NAME_FIELD: {"k1": [{"k2": name}], "k30": cd.FIELD_TYPE_TEXT},
        ID_FIELD: {"k1": [{"k2": id_no}], "k30": cd.FIELD_TYPE_TEXT},
        ATT_FIELD: cell_of(*atts),
    })


FIELDS = [
    cd.FieldDef(field_id="f1", title=NAME_FIELD, ftype=cd.FIELD_TYPE_TEXT),
    cd.FieldDef(field_id="f2", title=ID_FIELD, ftype=cd.FIELD_TYPE_TEXT),
    cd.FieldDef(field_id="f3", title=ATT_FIELD, ftype=cd.FIELD_TYPE_ATTACHMENT),
]

ID_A = "510101200001011234"
ID_B = "51010120000101123X"

RECORDS = [
    # 张三：一张图 + 一个 PDF（混合）
    rec("fr1", "张三", ID_A, ("jpg", 1024, "image"), ("pdf", 44, "file")),
    # 李四：只有 PDF
    rec("fr2", "李四", ID_B, ("pdf", 2048, "file")),
    # 王五：只有 docx
    rec("fr3", "王五", ID_A, ("docx", 4096, "file")),
    # 赵六：只有图片
    rec("fr4", "赵六", ID_B, ("png", 1024, "image")),
]


class _FakeClient:
    """只实现 build_jobs / download_one 用到的部分。"""

    _headers = {"Referer": "https://docs.qq.com/smartsheet/X"}

    def fetch_table(self, sheet_id):
        return FIELDS, RECORDS

    def get_pad_id(self, sheet_id):
        return "pad000000000"

    def fetch_attachment_urls(self, pad_id, obj_keys, access_type=0):
        return {k: f"https://example.invalid/{k}" for k in obj_keys}


class BuildJobsKindsTest(unittest.TestCase):
    def _jobs(self, kinds):
        return cd.build_jobs(_FakeClient(), "ss_x", None, None, None, None,
                             strict_id=True, kinds=kinds)[0]

    def test_image_only_keeps_history(self):
        jobs = self._jobs(cd.KINDS_IMAGE)
        self.assertEqual([j.name for j in jobs], ["张三", "赵六"])
        self.assertTrue(all(j.attachment.is_image for j in jobs))

    def test_all_includes_every_format(self):
        jobs = self._jobs(cd.KINDS_ALL)
        self.assertEqual([j.name for j in jobs], ["张三", "张三", "李四", "王五", "赵六"])
        exts = sorted(j.attachment.ext for j in jobs)
        self.assertEqual(exts, ["docx", "jpg", "pdf", "pdf", "png"])

    def test_mixed_record_gets_seq_suffix(self):
        jobs = self._jobs(cd.KINDS_ALL)
        mixed = [j for j in jobs if j.name == "张三"]
        self.assertEqual([(j.seq, j.n_total) for j in mixed], [(1, 2), (2, 2)])
        self.assertEqual(cd.resolve_filename(mixed[0]), f"张三{ID_A}.jpg")
        self.assertEqual(cd.resolve_filename(mixed[1]), f"张三{ID_A}_2.pdf")

    def test_default_is_image(self):
        jobs = cd.build_jobs(_FakeClient(), "ss_x", None, None, None, None,
                             strict_id=True)[0]
        self.assertEqual(len(jobs), 2)


class DownloadNonImageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kinds_"))
        self.client = _FakeClient()
        cd.reset_allocated()

    def tearDown(self):
        cd.reset_allocated()

    def _run(self, ext, payload, kind, name="张三"):
        att = cd.Attachment(obj_key="o" * 32, name=f"orig.{ext}", ext=ext,
                            size=len(payload), kind=kind)
        job = cd.Job(index=1, record_id="fr1", name=name, id_no=ID_A,
                     attachment=att, url="https://example.invalid/x")
        real_get = cd.http_get_bytes
        cd.http_get_bytes = lambda *a, **k: payload          # noqa: ARG005
        try:
            return cd.download_one(job, self.client, self.tmp,
                                   name_template="{name}{sep}{id}{seq_suffix}",
                                   pre_existing=cd.snapshot_existing(self.tmp))
        finally:
            cd.http_get_bytes = real_get

    def test_small_pdf_downloads(self):
        job = self._run("pdf", PDF, "file")
        self.assertEqual(job.status, "ok", job.error)
        self.assertEqual(job.saved_as, f"张三{ID_A}.pdf")
        self.assertEqual((self.tmp / job.saved_as).read_bytes(), PDF)

    def test_docx_keeps_zip_magic(self):
        job = self._run("docx", DOCX, "file")
        self.assertEqual(job.status, "ok", job.error)
        self.assertEqual(job.saved_as, f"张三{ID_A}.docx")

    def test_no_sniff_for_non_image(self):
        """以 'BM' 开头的非图片文件不能被当成 bmp 改名。"""
        job = self._run("dat", b"BM" + b"\x00" * 40, "file")
        self.assertEqual(job.status, "ok", job.error)
        self.assertEqual(job.saved_as, f"张三{ID_A}.dat")

    def test_unknown_ext_falls_back_to_bin(self):
        job = self._run("", TINY_BIN, "file")
        self.assertEqual(job.status, "ok", job.error)
        self.assertEqual(job.saved_as, f"张三{ID_A}.bin")

    def test_image_sniff_still_works(self):
        """图片仍按魔数纠正扩展名（老行为不能丢）。"""
        job = self._run("png", JPEG, "image")            # 名字叫 png，实际是 JPEG
        self.assertEqual(job.status, "ok", job.error)
        self.assertEqual(job.saved_as, f"张三{ID_A}.jpg")

    def test_tiny_image_still_fails(self):
        """图片的「过小 = 错误页」判定保留。"""
        job = self._run("jpg", b"\xff\xd8\xff" + b"\x00" * 10, "image")
        self.assertEqual(job.status, "failed")
        self.assertIn("可能不是有效图片", job.error)


if __name__ == "__main__":
    unittest.main(verbosity=2)
