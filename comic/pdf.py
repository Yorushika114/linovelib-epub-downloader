"""把 JPEG 字节流合成 PDF。"""

from __future__ import annotations

import io

from PIL import Image


def assemble_pdf(jpeg_sources: list[bytes], out_path: str, dpi: int = 150) -> str:
    """按顺序把一组 JPEG 字节合成为多页 PDF，返回写入路径。

    图片已由浏览器解码为等宽的页（单页/跨页），这里按原本比例逐页排布，
    混合尺寸也可正常输出。空列表会抛出 ValueError。
    """
    if not jpeg_sources:
        raise ValueError("没有可写入的图片")

    images: list[Image.Image] = []
    for b in jpeg_sources:
        im = Image.open(io.BytesIO(b))
        im.load()
        if im.mode != "RGB":
            im = im.convert("RGB")
        images.append(im)

    images[0].save(
        out_path,
        "PDF",
        save_all=True,
        append_images=images[1:],
        resolution=dpi,
    )
    return out_path
