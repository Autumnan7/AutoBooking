# -*- coding: utf-8 -*-
"""验证码识别 v2：颜色/亮度分割 + 列投影分组 + 运算符几何判别 + 数字 OCR

与 v1（ddddocr 整串识别）的区别：
  - 先把前景字形从背景噪点中分割出来（深色字形 + 彩色噪点的特征不同）；
  - 按列投影把字符切成组，利用固定格式 a op b = ? 直接丢掉尾部的 "=" 和 "?"，
    避免尾部污染；
  - 运算符（组 2）不看 OCR，按几何特征判别：'-' 扁、'+' 宽、'*' 窄方；
  - 数字（组 1、3）裁剪后交 ddddocr 单独识别，字符集只留 0-9。

对外接口：recognize(image_bytes) -> "a<op>b"，op 为 + - * /；
无法判断的未知形状抛 ValueError（调用方刷新重试/跳过），不会静默算错。
"""
import io

import cv2
import numpy as np
from PIL import Image
from ddddocr import DdddOcr

_ocr = None

# 站点验证码原图固定为 201×61（1010 张采集样本全部一致）。
# 所有几何阈值（边框 2px、面积 30、组间距 10、运算符宽高）都是按这个分辨率标定的，
# 一旦截图尺寸因缩放/DPR/浏览器设置变化就会失效，所以识别前统一归一化。
REF_W, REF_H = 201, 61


def _normalize(img):
    """把输入统一到标定分辨率 201×61；已一致时直接返回。"""
    h, w = img.shape[:2]
    if (w, h) == (REF_W, REF_H):
        return img
    interp = cv2.INTER_AREA if w * h > REF_W * REF_H else cv2.INTER_LANCZOS4
    return cv2.resize(img, (REF_W, REF_H), interpolation=interp)


def _get_ocr():
    global _ocr
    if _ocr is None:
        _ocr = DdddOcr(show_ad=False, beta=True)
        _ocr.set_ranges("0123456789")
    return _ocr


def _raw_mask(img):
    """阈值后的前景（仍含噪点），只去掉图像四周的彩色边框。"""
    lum = img.mean(axis=2)
    mx = img.max(axis=2).astype(int)
    mn = img.min(axis=2).astype(int)
    m0 = ((lum < 180) | ((mx - mn) > 60)).astype(np.uint8)
    border = 2
    m0[:border, :] = 0
    m0[-border:, :] = 0
    m0[:, :border] = 0
    m0[:, -border:] = 0
    return m0


def _filter_components(m0):
    """只保留面积/尺寸够大的笔画，去掉彩色小噪点。"""
    n, lab, st, _ = cv2.connectedComponentsWithStats(m0, 8)
    m = np.zeros_like(m0)
    for i in range(1, n):
        x, y, w, h, area = st[i]
        if area >= 30 and (w >= 8 or h >= 8):
            m[lab == i] = 1
    return m


def _foreground_mask(img):
    """深色字形（亮度低）或高饱和彩色字形都算前景；灰色/浅色噪点被排除，
    再按连通域面积和尺寸过滤掉彩色小噪点。"""
    return _filter_components(_raw_mask(img))


def _column_groups(m, merge_gap):
    """列投影分组。数字内部的字间距 ≤6px，组与组之间（含运算符两侧）≥18px，
    所以 merge_gap 取 10 可把两位数并成一组、又不跨组粘连。"""
    col = m.sum(axis=0)
    runs, start = [], None
    for x in range(len(col)):
        if col[x] > 0 and start is None:
            start = x
        elif col[x] == 0 and start is not None:
            runs.append((start, x - 1))
            start = None
    if start is not None:
        runs.append((start, len(col) - 1))
    runs = [(a, b) for a, b in runs if b - a + 1 >= 4]

    merged = []
    for a, b in runs:
        if merged and a - merged[-1][1] - 1 <= merge_gap:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return merged


def _group_box(m, x0, x1):
    ys = np.where(m[:, x0:x1 + 1].sum(axis=1) > 0)[0]
    return x0, x1, int(ys.min()), int(ys.max()) if len(ys) else 0


def _classify_operator(m, x0, x1, raw=None):
    """运算符几何判别（样本实测）：
    '-' 高 4~7；'+' 宽 15~18、高 18~21；'*' 宽 9~13、高 8~15；
    '/' 窄而高（宽 ≤13、高 ≥16，比 '*' 高、比 '+' 窄）。

    除法有两种常见渲染：
      - '/'：一条斜线，单连通域，靠“窄而高”兜底识别；
      - '÷'：一横 + 上下两点，过滤噪点会把两点删掉、只剩横杠，
        所以要在未过滤的 raw 掩码上数连通域（≥3 个且有扁长横杠）。
    其余完全对不上的形状抛 ValueError，交给上层刷新重试/跳过。
    """
    w = x1 - x0 + 1
    ys = np.where(m[:, x0:x1 + 1].sum(axis=1) > 0)[0]
    h = int(ys.max() - ys.min() + 1) if len(ys) else 0

    if raw is not None:
        n, _, st, _ = cv2.connectedComponentsWithStats(raw[:, x0:x1 + 1], 8)
        comps = [st[i] for i in range(1, n) if st[i][4] >= 4]
        # '÷'：一横 + 上下两点（过滤后只剩横杠），按除法处理
        if len(comps) >= 3 and any(c[2] >= 9 and c[3] <= 7 for c in comps):
            return "/"

    if h <= 7 and w <= 13:
        return "-"
    if w >= 14 and h >= 16:
        return "+"
    if w <= 13 and 8 <= h <= 15:
        return "*"
    # '/'：窄而高，正好落在 '*' 和 '+' 都覆盖不到的空档
    if h >= 16 and w <= 13:
        return "/"
    raise ValueError(f"未知运算符形状（w={w}, h={h}）")


def _read_digits(img, m, x0, x1):
    """裁剪单个数字组（统一补 1px 边），把背景置白去掉残留噪点，放大 3 倍后识别。"""
    x0, x1, y0, y1 = _group_box(m, x0, x1)
    x0, y0 = max(0, x0 - 1), max(0, y0 - 1)
    x1, y1 = min(img.shape[1] - 1, x1 + 1), min(img.shape[0] - 1, y1 + 1)
    crop = img[y0:y1 + 1, x0:x1 + 1].copy()
    crop[m[y0:y1 + 1, x0:x1 + 1] == 0] = (255, 255, 255)
    pil = Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
    pil = pil.resize((pil.width * 3, pil.height * 3), Image.LANCZOS)
    buf = io.BytesIO()
    pil.save(buf, format="PNG")
    return str(_get_ocr().classification(buf.getvalue())).strip()


def recognize(image_bytes: bytes) -> str:
    """返回 "a<op>b"，op 为 + - * /。无法判断的未知形状抛 ValueError，
    由调用方刷新验证码重试；重试仍失败则跳过当天，绝不静默算错。"""
    img = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("图像解码失败")
    img = _normalize(img)
    raw = _raw_mask(img)
    m = _filter_components(raw)

    # 正常是 5 组（a/op/b/=/ ?）；个别切割差异换个间距重试。
    # 只需要前 3 组，尾部 '=' '?' 有没有检出都不影响（只当前 3 组做格式校验）。
    groups = None
    for gap in (10, 8, 12, 6, 14):
        g = _column_groups(m, gap)
        if len(g) == 5:
            groups = g[:3]
            break
    if groups is None:
        for gap in (10, 8, 12, 6, 14):
            g = _column_groups(m, gap)
            if len(g) >= 3:
                groups = g[:3]
                break
    if groups is None:
        raise ValueError(f"字符分组失败（得到 {len(_column_groups(m, 10))} 组）")

    a_box, op_box, b_box = groups  # 尾部 '=' 和 '?' 已被丢弃
    op = _classify_operator(m, *op_box, raw=raw)
    a = _read_digits(img, m, *a_box)
    b = _read_digits(img, m, *b_box)
    if not (a.isdigit() and b.isdigit()):
        raise ValueError(f"数字识别失败：a={a!r} b={b!r}")
    return f"{int(a)}{op}{int(b)}"