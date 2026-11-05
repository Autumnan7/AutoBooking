# -*- coding: utf-8 -*-
"""导出验证码识别流程的中间步骤图（v1 整串 OCR / v2 分割+几何判别）。

用法：
    python visualize_captcha.py captcha_samples/collect_20261105_131511.png
    # 默认输出到 docs/captcha_pipeline/

产物：每步一张 PNG，供文档/排查参考。
"""
import argparse
import io
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from ddddocr import DdddOcr

import captcha_ocr


def _save(img, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), img)


def _label(img, text, scale=0.7):
    """在图片下方加一行 ASCII 说明（无需中文字体）"""
    pad = 36
    canvas = cv2.copyMakeBorder(img, 10, pad, 10, 10, cv2.BORDER_CONSTANT, value=(255, 255, 255))
    cv2.putText(canvas, text, (10, canvas.shape[0] - 12),
                cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 2)
    return canvas


def v1_steps(pil_img, out: Path, ocr):
    """v1：灰度 -> 右侧裁 80% -> 3 倍放大 -> ddddocr 整串识别"""
    steps = {}
    pil_img.save(out / "v1_01_original.png")

    gray = pil_img.convert("L")
    gray.save(out / "v1_02_grayscale.png")
    steps["gray"] = gray

    w, h = gray.size
    crop = gray.crop((0, 0, int(w * 0.8), h))
    crop.save(out / "v1_03_crop_right80.png")
    steps["crop"] = crop

    up = crop.resize((crop.width * 3, crop.height * 3), Image.LANCZOS)
    up.save(out / "v1_04_upscale3x.png")
    steps["up"] = up

    buf = io.BytesIO()
    up.save(buf, format="PNG")
    text = str(ocr.classification(buf.getvalue()))
    canvas = cv2.cvtColor(np.array(up.convert("RGB")), cv2.COLOR_RGB2BGR)
    _save(_label(canvas, f"ddddocr output: {text!r}"), out / "v1_05_ocr_result.png")
    return text


def v2_steps(bgr, out: Path):
    """v2：前景分割 -> 噪点过滤 -> 列投影分组 -> 运算符几何判别 -> 数字 OCR"""
    _save(bgr, out / "v2_01_original.png")

    # step 1: 亮度/饱和度阈值（未去边框、未过滤）
    lum = bgr.mean(axis=2)
    mx = bgr.max(axis=2).astype(int)
    mn = bgr.min(axis=2).astype(int)
    raw = ((lum < 180) | ((mx - mn) > 60)).astype(np.uint8)
    _save(_label(raw * 255, "step1 threshold: lum<180 or sat>60 (border still on)"),
          out / "v2_02_threshold_raw.png")

    # step 2: 去边框 + 连通域过滤
    m = captcha_ocr._foreground_mask(bgr)
    _save(_label(m * 255, "step2 border removed + tiny color specks filtered"),
          out / "v2_03_components_filtered.png")

    # step 3: 列投影 + 分组
    groups = captcha_ocr._column_groups(m, 10)
    scale, height = 5, 120
    W = m.shape[1] * scale
    chart = np.full((height + 40, W + 20, 3), 255, np.uint8)
    col = m.sum(axis=0)
    cmax = max(1, int(col.max()))
    for x, c in enumerate(col):
        hgt = int(c / cmax * height)
        if hgt:
            cv2.rectangle(chart, (10 + x * scale, height + 10 - hgt),
                          (10 + x * scale + scale - 1, height + 10), (90, 90, 90), -1)
    for i, (a, b) in enumerate(groups):
        color = (0, 170, 0) if i < 3 else (150, 150, 150)
        cv2.rectangle(chart, (10 + a * scale, 5),
                      (10 + b * scale + scale - 1, height + 10), color, 1)
    _save(_label(chart, "step3 column projection; green=a/op/b, gray= =/?"),
          out / "v2_04_column_projection.png")

    # step 4: 在原图上画分组框
    boxes = bgr.copy()
    names = ["a", "op", "b", "=", "?"]
    for i, (x0, x1) in enumerate(groups):
        x0b, x1b, y0, y1 = captcha_ocr._group_box(m, x0, x1)
        color = (0, 170, 0) if i < 3 else (150, 150, 150)
        cv2.rectangle(boxes, (x0b - 2, y0 - 2), (x1b + 2, y1 + 2), color, 1)
        cv2.putText(boxes, names[i] if i < len(names) else f"g{i}",
                    (x0b, max(10, y0 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)
    _save(_label(boxes, "step4 groups: a / op / b kept, = ? dropped"),
          out / "v2_05_groups.png")

    # step 5: 运算符判别
    op_x0, op_x1 = groups[1]
    x0b, x1b, y0, y1 = captcha_ocr._group_box(m, op_x0, op_x1)
    op_crop = bgr[max(0, y0 - 2):y1 + 3, max(0, x0b - 2):x1b + 3]
    op_crop = cv2.resize(op_crop, None, fx=6, fy=6, interpolation=cv2.INTER_NEAREST)
    op = captcha_ocr._classify_operator(m, op_x0, op_x1)
    op_w, op_h = x1b - x0b + 1, y1 - y0 + 1
    _save(_label(op_crop, f"step5 operator: w={op_w} h={op_h} => '{op}'"),
          out / "v2_06_operator.png")

    # step 6: 数字裁剪 + 背景置白 + 放大
    digit_imgs = []
    parsed = {}
    for label, box in (("a", groups[0]), ("b", groups[2])):
        x0b, x1b, y0, y1 = captcha_ocr._group_box(m, *box)
        crop = bgr[max(0, y0 - 1):y1 + 2, max(0, x0b - 1):x1b + 2].copy()
        crop[m[max(0, y0 - 1):y1 + 2, max(0, x0b - 1):x1b + 2] == 0] = (255, 255, 255)
        crop = cv2.resize(crop, None, fx=4, fy=4, interpolation=cv2.INTER_LANCZOS4)
        parsed[label] = captcha_ocr._read_digits(bgr, m, *box)
        digit_imgs.append(_label(crop, f"{label} = {parsed[label]!r}", 0.6))
    hmax = max(d.shape[0] for d in digit_imgs)
    digit_imgs = [cv2.copyMakeBorder(d, 0, hmax - d.shape[0], 0, 0,
                                     cv2.BORDER_CONSTANT, value=(255, 255, 255))
                  for d in digit_imgs]
    _save(np.hstack(digit_imgs), out / "v2_07_digits.png")

    text = f"{parsed['a']}{op}{parsed['b']}"
    return text, dict(w=op_w, h=op_h, op=op, groups=groups)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sample", help="验证码 png 路径")
    ap.add_argument("--out", default="docs/captcha_pipeline", help="输出目录")
    args = ap.parse_args()

    src = Path(args.sample)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    pil_img = Image.open(src).convert("RGB")
    bgr = cv2.imdecode(np.frombuffer(src.read_bytes(), np.uint8), cv2.IMREAD_COLOR)

    ocr = DdddOcr(show_ad=False, beta=True)
    ocr.set_ranges("0123456789+-*xX")

    v1_text = v1_steps(pil_img, out, ocr)
    v2_text, info = v2_steps(bgr, out)

    print(f"样本: {src}")
    print(f"  v1 输出: {v1_text!r}")
    print(f"  v2 输出: {v2_text!r}  (op 特征 w={info['w']} h={info['h']} => {info['op']!r})")
    print(f"  中间图已写入: {out.resolve()}")


if __name__ == "__main__":
    main()