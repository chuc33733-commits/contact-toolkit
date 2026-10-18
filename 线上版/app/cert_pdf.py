# -*- coding: utf-8 -*-
"""图片 → PDF 合并器（内存恒定，不随图片数量增长）。

为什么不用 ``Pillow`` 的 ``save_all``
------------------------------------
Pillow 的 PDF writer 会把**所有页的像素同时驻留内存**（本机实测约 7.6 MB/张，
300 张 1600×1200 的图峰值 2.2 GB）。证书批量场景动辄 300+ 张，直接打爆容器内存上限。

本模块改为「流式拼装」：

* **JPEG 源文件逐字节原样嵌入**（``/DCTDecode``）——不解码、不重编码、不损画质、极快；
* 其它格式（PNG / BMP / WEBP…）**逐张**解码成 JPEG 后嵌入，用完立即释放；
* 带 EXIF 旋转标记的 JPEG 会先纠正方向再嵌入（多数 PDF 阅读器不认 EXIF 方向）；
* 每页 A4（横图自动用 A4 横向），等比缩放居中留边；
* 输出边写边落盘，峰值内存 ≈ 单张图片，与图片总数无关。

对外只有一个函数：:func:`images_to_pdf`。
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageOps

# A4 尺寸（PDF 单位：pt，1pt = 1/72 inch）
A4_SHORT = 595.276
A4_LONG = 841.89
MARGIN = 18.0                 # 页边距（pt）

#: 需要「原样嵌入」的 JPEG 颜色模式 -> PDF 色彩空间
_JPEG_COLORSPACE = {"L": "/DeviceGray", "RGB": "/DeviceRGB", "CMYK": "/DeviceCMYK"}

#: 非 JPEG 图片重编码时的 JPEG 质量
_REENCODE_QUALITY = 90

_CHUNK = 1 << 20              # 流式拷贝的块大小


class _Writer:
    """极简 PDF 写入器：负责对象编号、偏移记录与交叉引用表。"""

    def __init__(self, fp) -> None:
        self.fp = fp
        self.offsets: Dict[int, int] = {}

    def obj(self, num: int, body: bytes) -> None:
        self.offsets[num] = self.fp.tell()
        self.fp.write(b"%d 0 obj\n" % num)
        self.fp.write(body)
        self.fp.write(b"\nendobj\n")

    def stream(self, num: int, extra: bytes, data: bytes) -> None:
        self.offsets[num] = self.fp.tell()
        self.fp.write(b"%d 0 obj\n<< /Length %d" % (num, len(data)))
        if extra:
            self.fp.write(b" " + extra)
        self.fp.write(b" >>\nstream\n")
        self.fp.write(data)
        self.fp.write(b"\nendstream\nendobj\n")

    def stream_from_file(self, num: int, extra: bytes, path: str) -> None:
        """把外部文件的字节原样流进 stream（用于 JPEG 原样嵌入）。"""
        size = os.path.getsize(path)
        self.offsets[num] = self.fp.tell()
        self.fp.write(b"%d 0 obj\n<< /Length %d" % (num, size))
        if extra:
            self.fp.write(b" " + extra)
        self.fp.write(b" >>\nstream\n")
        with open(path, "rb") as src:
            while True:
                chunk = src.read(_CHUNK)
                if not chunk:
                    break
                self.fp.write(chunk)
        self.fp.write(b"\nendstream\nendobj\n")

    def xref_and_trailer(self, total_objs: int, root_num: int = 1) -> None:
        start = self.fp.tell()
        self.fp.write(b"xref\n0 %d\n" % (total_objs + 1))
        self.fp.write(b"0000000000 65535 f \n")
        for num in range(1, total_objs + 1):
            self.fp.write(b"%010d 00000 n \n" % self.offsets.get(num, 0))
        self.fp.write(b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
                      % (total_objs + 1, root_num, start))


def _page_box(iw: int, ih: int) -> Tuple[float, float, float, float, float, float]:
    """按图片宽高比选 A4 横/纵，返回 (页宽, 页高, 画宽, 画高, x, y)。"""
    if iw >= ih:
        pw, ph = A4_LONG, A4_SHORT
    else:
        pw, ph = A4_SHORT, A4_LONG
    avail_w, avail_h = pw - 2 * MARGIN, ph - 2 * MARGIN
    scale = min(avail_w / float(iw), avail_h / float(ih))
    sw, sh = iw * scale, ih * scale
    return pw, ph, sw, sh, (pw - sw) / 2.0, (ph - sh) / 2.0


def _prepare_image(path: Path) -> Tuple[str, str, bytes, int, int]:
    """决定一张图怎么进 PDF。

    Returns:
        ``(kind, colorspace, payload, width, height)``，其中 ``kind`` 为
        ``"file"``（payload 为空串，表示原样嵌入该文件）或 ``"bytes"``。

    Raises:
        Exception: 图片无法识别/解码时向上抛，由调用方计入 skipped。
    """
    with Image.open(path) as im:
        try:
            orientation = int(im.getexif().get(274, 1) or 1)
        except Exception:                      # noqa: BLE001 - 元数据坏了不影响出图
            orientation = 1

        # 干净方向的标准 JPEG：原样嵌入，一个像素都不用解
        if ((im.format or "").upper() == "JPEG"
                and im.mode in ("L", "RGB")
                and orientation in (0, 1)):
            return "file", _JPEG_COLORSPACE[im.mode], b"", im.width, im.height

        # 其余情况必须碰像素：手机拍的证书常带 EXIF 方向标记，PDF 阅读器基本不认，先摆正
        if orientation not in (0, 1):
            im = ImageOps.exif_transpose(im)
        if im.mode not in ("L", "RGB"):
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=_REENCODE_QUALITY, optimize=True)
        return ("bytes", _JPEG_COLORSPACE[im.mode], buf.getvalue(),
                im.width, im.height)


def images_to_pdf(paths: Sequence[os.PathLike],
                  out_path: os.PathLike,
                  on_progress: Optional[Callable[[int, int], None]] = None,
                  ) -> Dict[str, object]:
    """把若干图片按给定顺序合并成一份 PDF。

    Args:
        paths: 图片路径，顺序即页序。
        out_path: 输出 PDF 路径。
        on_progress: 可选回调 ``(已完成页数, 总页数)``，每页调用一次。

    Returns:
        ``{"pages": int, "skipped": [(文件名, 原因), ...], "bytes": int}``

    Raises:
        ValueError: 一张可用的图都没有。
    """
    out_path = Path(out_path)
    total = len(paths)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 第一趟：只读文件头，拿到每张图的尺寸/编码方式（JPEG 走原样嵌入，不碰像素）
    prepared: List[Tuple[str, str, bytes, int, int, str]] = []
    skipped: List[Tuple[str, str]] = []
    for p in paths:
        p = Path(p)
        try:
            kind, cs, payload, iw, ih = _prepare_image(p)
        except Exception as exc:               # noqa: BLE001 - 单张坏图不能拖垮整批
            skipped.append((p.name, f"{type(exc).__name__}: {exc}"))
            continue
        if iw <= 0 or ih <= 0:
            skipped.append((p.name, "图片尺寸无效"))
            continue
        prepared.append((kind, cs, payload, iw, ih, str(p)))

    if not prepared:
        raise ValueError("没有可合并的图片（全部无法解析）")

    n = len(prepared)
    total_objs = 2 + 3 * n            # 1=Catalog 2=Pages，其后每页 3 个对象
    with open(out_path, "wb") as fp:
        w = _Writer(fp)
        fp.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")

        # 对象编号：1=Catalog，2=Pages，此后每页占 3 个（XObject / 内容流 / Page）
        kids = b" ".join(b"%d 0 R" % (5 + 3 * i) for i in range(n))
        w.obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")
        w.obj(2, b"<< /Type /Pages /Count %d /Kids [ %s ] >>" % (n, kids))

        for i, (kind, cs, payload, iw, ih, src) in enumerate(prepared):
            xobj_num = 3 + 3 * i
            content_num = xobj_num + 1
            page_num = xobj_num + 2
            name = ("/Im%d" % i).encode("ascii")

            extra = (b"/Type /XObject /Subtype /Image /Width %d /Height %d "
                     b"/ColorSpace %s /BitsPerComponent 8 /Filter /DCTDecode"
                     % (iw, ih, cs.encode("ascii")))
            if kind == "file":
                w.stream_from_file(xobj_num, extra, src)
            else:
                w.stream(xobj_num, extra, payload)

            pw, ph, sw, sh, tx, ty = _page_box(iw, ih)
            content = ("q\n%.4f 0 0 %.4f %.4f %.4f cm\n%s Do\nQ\n"
                       % (sw, sh, tx, ty, name.decode("ascii"))).encode("ascii")
            w.stream(content_num, b"", content)

            w.obj(page_num,
                  b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.3f %.3f] "
                  b"/Resources << /XObject << %s %d 0 R >> >> /Contents %d 0 R >>"
                  % (pw, ph, name, xobj_num, content_num))

            if on_progress is not None:
                try:
                    on_progress(i + 1, n)
                except Exception:              # noqa: BLE001 - 进度回调失败不影响出 PDF
                    pass

        w.xref_and_trailer(total_objs, root_num=1)
        size = fp.tell()

    return {"pages": n, "skipped": skipped, "bytes": size}
