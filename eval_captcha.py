# -*- coding: utf-8 -*-
"""对 captcha_samples 里的采集样本跑「现有识别算法」（reserve.py 的 ddddocr 方案），
输出可解析率、运算符分布、失败样本等基线统计。

用法：
    python eval_captcha.py [--limit N] [--dump-fails 20]

产物：
    captcha_samples/eval_current.jsonl   每张图的识别文本 / 解析结果 / 计算答案
    若存在 captcha_samples/labels_gt.jsonl（每行 {"file":..., "expr":"a op b"}），
    会额外计算：表达式完全正确率、答案正确率（运算符统一为 + - * /）。

注意：这里刻意复刻 reserve.py 当前的参数（白名单不含 *、裁剪 0.8、3 倍放大），
跑出来的是"现状基线"，用来和改进方案对比。
"""
import argparse
import io
import json
import re
import sys
from collections import Counter
from pathlib import Path

from PIL import Image
from ddddocr import DdddOcr

ROOT = Path(__file__).resolve().parent
SAMPLES = ROOT / "captcha_samples"
OUT = SAMPLES / "eval_current.jsonl"
GT = SAMPLES / "labels_gt.jsonl"

# 与 reserve.py 当前版本完全一致（可用命令行参数覆盖以对比方案）
DEFAULT_RANGES = "0123456789+-x×÷"
DEFAULT_CROP = 0.8
ocr = DdddOcr(show_ad=False, beta=True)


def recognize(image_bytes: bytes, crop_ratio: float = DEFAULT_CROP) -> str:
    img = Image.open(io.BytesIO(image_bytes)).convert("L")
    w, h = img.size
    img = img.crop((0, 0, int(w * crop_ratio), h))
    img = img.resize((int(w * crop_ratio) * 3, h * 3), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return str(ocr.classification(buf.getvalue()))


def parse(text: str):
    """返回 (a, op, b) 或 None；op 统一成 + - * /"""
    expr = re.sub(r"\s+|\?=|=?$", "", text.strip())
    expr = expr.replace("x", "*").replace("X", "*").replace("×", "*").replace("÷", "/")
    expr = re.sub(r"[+\-*/]+$", "", expr)  # 模型偶尔把尾部 "=" 读成运算符，剥掉
    tokens = re.findall(r"\d+|[+\-*/]", expr)
    if len(tokens) != 3:
        return None
    a, op, b = tokens
    if not (a.isdigit() and b.isdigit()) or op not in "+-*/":
        return None
    return int(a), op, int(b)


def calc(expr):
    a, op, b = expr
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    return a // b if b else None


def load_gt():
    if not GT.exists():
        return {}
    gt = {}
    for line in GT.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
            p = parse(rec["expr"])
            if p:
                gt[rec["file"]] = p
        except Exception:
            continue
    return gt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 张（0=全部）")
    ap.add_argument("--dump-fails", type=int, default=15, help="打印多少条失败样本")
    ap.add_argument("--ranges", default=DEFAULT_RANGES, help="ddddocr 白名单（仅 v1）")
    ap.add_argument("--crop", type=float, default=DEFAULT_CROP, help="右侧裁剪比例（仅 v1）")
    ap.add_argument("--engine", choices=["v1", "v2"], default="v1",
                    help="v1=整串 OCR（现状）；v2=分割+几何判别（captcha_ocr.py）")
    ap.add_argument("--out", default="", help="明细输出路径（默认 captcha_samples/eval_current.jsonl）")
    args = ap.parse_args()
    out_path = Path(args.out) if args.out else OUT

    if args.engine == "v1":
        ocr.set_ranges(args.ranges)
        print(f"引擎=v1  白名单={args.ranges!r}  裁剪={args.crop}")
    else:
        import captcha_ocr
        print("引擎=v2（分割 + 运算符几何判别 + 数字 OCR）")

    files = sorted(SAMPLES.glob("collect_*.png"))
    if args.limit:
        files = files[: args.limit]
    if not files:
        raise SystemExit(f"✗ {SAMPLES} 里没有 collect_*.png 样本")

    gt = load_gt()
    total = parsed = 0
    ops = Counter()
    fail_reasons = Counter()
    big_operands = 0
    rows = []

    for f in files:
        total += 1
        if args.engine == "v2":
            try:
                text = captcha_ocr.recognize(f.read_bytes())
            except Exception as exc:
                rows.append({"file": f.name, "text": "", "error": str(exc),
                             "expr": None, "answer": None})
                continue
        else:
            text = recognize(f.read_bytes(), args.crop)
        expr = parse(text)
        row = {"file": f.name, "text": text,
               "expr": list(expr) if expr else None,
               "answer": calc(expr) if expr else None}
        rows.append(row)

        if expr:
            parsed += 1
            ops[expr[1]] += 1
            if expr[0] > 99 or expr[2] > 99:
                big_operands += 1
        else:
            tokens = re.findall(r"\d+|[+\-*/]", re.sub(r"\s+|\?=|=?$", "", text.strip()))
            if len(tokens) < 3:
                fail_reasons["缺运算符/操作数"] += 1
            elif len(tokens) > 3:
                fail_reasons["多余字符粘连"] += 1
            else:
                fail_reasons["其他"] += 1
        if total % 100 == 0:
            print(f"  ...已处理 {total}/{len(files)}", flush=True)

    out_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                        encoding="utf-8")

    print()
    print("========== 现状算法基线 ==========")
    print(f"  样本总数：{total}")
    print(f"  可解析：{parsed}（{parsed / total:.1%}）  失败：{total - parsed}")
    print(f"  运算符分布：{dict(ops)}")
    print(f"  失败原因：{dict(fail_reasons)}")
    print(f"  疑似尾部粘连（操作数>99）：{big_operands}")

    if gt:
        n = correct_expr = correct_ans = 0
        wrong = []
        for r in rows:
            g = gt.get(r["file"])
            if not g:
                continue
            n += 1
            if r["expr"] and tuple(r["expr"]) == g:
                correct_expr += 1
            if r["answer"] is not None and r["answer"] == calc(g):
                correct_ans += 1
            elif len(wrong) < args.dump_fails:
                wrong.append((r["file"], r["text"], r["expr"], list(g)))
        if n:
            print(f"  有标注样本：{n}")
            print(f"  表达式全对：{correct_expr}/{n}（{correct_expr / n:.1%}）")
            print(f"  答案正确：  {correct_ans}/{n}（{correct_ans / n:.1%}）")
            for w in wrong:
                print(f"    错例 {w[0]} 识别={w[1]!r} 解析={w[2]} 真值={w[3]}")
        else:
            print("  有 labels_gt.jsonl 但没有与样本匹配的标注")
    else:
        print(f"  （未找到 {GT.name}，只统计可解析率；补标注后可算准确率）")
        fails = [r for r in rows if r["expr"] is None]
        print(f"  失败样本示例（前 {min(args.dump_fails, len(fails))} 条）：")
        for r in fails[: args.dump_fails]:
            print(f"    {r['file']}  识别={r['text']!r}")
    print("================================")
    print(f"  明细已写入：{out_path}")


if __name__ == "__main__":
    main()