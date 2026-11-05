# -*- coding: utf-8 -*-
"""验证码采集脚本（只做采集，不产生任何真实预约）

流程：登录 WebVPN -> 仪器预约 -> 选设备 -> 选日期/时间 -> 点「立即提交」
     等验证码弹窗出现后：截图 -> 没见过的才落盘 -> 点图片刷新 -> 继续截图……
     全程不点「确定」，所以不会提交答案、不会约到时段。
     重复图（字节相同）和加载失败的占位破图直接丢弃不落盘；
     连吃 5 张旧图才转「关弹窗重新提交」路径，连续 4 次重开无新图则提示限流可能。

用法：
    python collect_captchas.py [采集张数，默认 200] [--manual]
    --manual：跳过自动登录/导航，你自己在浏览器里登录并进入该设备的「自主上机」
              预约页，回车后脚本从「选最后一天 + 选时段 + 提交」开始接手。

产物（目录已在 .gitignore 中）：
    captcha_samples/collect_*.png          验证码原图（只存新图）
    captcha_samples/collect_manifest.jsonl 每张图的记录：文件名/时间/sha1/字节数
"""
import configparser
import hashlib
import io
import json
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

# ---------------- 配置（与 reserve.py 共用 config.ini） ----------------
_cfg = configparser.ConfigParser()
if not _cfg.read(Path(__file__).with_name("config.ini"), encoding="utf-8-sig"):
    raise SystemExit("✗ 未找到 config.ini（完整格式见 README 第二节）")

STUDENT_ID = _cfg.get("account", "student_id", fallback="").strip()
PASSWORD = _cfg.get("account", "password", fallback="").strip()
if not STUDENT_ID or not PASSWORD:
    raise SystemExit("✗ 请在 config.ini [account] 填好学号密码")

# 仪器列表与 reserve.py 保持一致（换设备改这里的 index 即可）
INSTRUMENTS = [
    {"name": "通信场景仿真攻防对抗实验教学设备", "click": None,    "tag": ""},
    {"name": "可重构高性能无损网络实验教学设备",   "click": "first", "tag": "S2507234"},
    {"name": "可重构高性能无损网络实验教学设备",   "click": "nth1",  "tag": "S2507235"},
    {"name": "可编程SDN网络技术实验教学设备",      "click": None,    "tag": ""},
    {"name": "5G-A通感一体化网络实验教学设备",     "click": None,    "tag": ""},
]
try:
    _choice = int(_cfg.get("instrument", "index", fallback="1"))
except ValueError:
    _choice = 0
if not (1 <= _choice <= len(INSTRUMENTS)):
    raise SystemExit("✗ config.ini [instrument] index 应为 1-5")
_chosen = INSTRUMENTS[_choice - 1]
SEARCH_CATEGORY = _chosen["name"]
CLICK_STRATEGY = _chosen["click"]

URL = ("https://webvpn.uestc.edu.cn/https/77726476706e69737468656265737421f4ee0f8922237c53300d8db9d6562d/customer/index/index.html")

_args = [a for a in sys.argv[1:] if not a.startswith("-")]
MANUAL = any(a in ("-m", "--manual") for a in sys.argv[1:])
TARGET = int(_args[0]) if _args else 200
if TARGET < 1:
    raise SystemExit("✗ 采集张数应为正整数")

SAMPLES_DIR = Path(__file__).with_name("captcha_samples")
MANIFEST = SAMPLES_DIR / "collect_manifest.jsonl"


def dump_debug(page, tag):
    """把当前整页截图 + 所有 frame 的 URL 存/打下来，用于判断流程断住时页面到底停在哪一步"""
    for fr in page.frames:
        print(f"    [frame] {fr.url[:140]}")
    try:
        SAMPLES_DIR.mkdir(exist_ok=True)
        p = SAMPLES_DIR / f"debug_{datetime.now().strftime('%H%M%S')}_{tag}.png"
        page.screenshot(path=str(p), full_page=True)
        print(f"  已保存故障现场截图：{p.name}")
    except Exception as exc:
        print(f"  ⚠ 现场截图失败：{str(exc).splitlines()[0]}")


def safe_click(locator, desc, page):
    """点击失败不崩溃，暂停等人工接管（采集是长时间任务，别因为一次点击失败全盘中断）"""
    try:
        locator.scroll_into_view_if_needed()
        page.wait_for_timeout(150)
        locator.click(timeout=6000)
        return True
    except Exception as exc:
        print(f"  ⚠ 点击「{desc}」失败：{str(exc).splitlines()[0]}")
        input("  请在浏览器里手动完成该步，然后回车继续...")
        return False


def ensure_calendar(iframe1, timeout_s=10):
    """确认 laydate 日历弹层在页面上。它平时可能被关掉（ESC/关弹窗会连带关），
    需要点一下「预约时段」的日期输入框才会重新弹出。
    注意：日期值常是 JS 赋的 value 属性值，CSS [value*=...] 匹配不到，
    所以按输入框的实时 value 找；iframe 加载慢，整体做重试。"""
    cells = iframe1.locator("td[onclick]")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if cells.count() > 0:
                return True
        except Exception:
            pass
        try:
            inputs = iframe1.locator("input")
            for k in range(inputs.count()):
                el = inputs.nth(k)
                try:
                    # 找实时 value 里带日期短横线（YYYY-MM-DD）且可见的输入框
                    if el.evaluate("e => (e.value || '').includes('-')") and el.is_visible():
                        el.click(timeout=2000)
                        break
                except Exception:
                    continue
        except Exception:
            pass
        time.sleep(0.5)
    print("  ⚠ 多次尝试仍无法唤出日历弹层")
    return False


def last_displayed_day(iframe1, timeout_s=20):
    """日历日期格子按 DOM 顺序排列，取最后一个即界面显示的最后一天。
    先确保 laydate 弹层是开的，再等格子出现读取（count/wait 语义见 ensure_calendar）。
    返回 "YYYY-MM-DD" 字符串；解析不到返回 None。"""
    if not ensure_calendar(iframe1, timeout_s=5):
        return None
    cells = iframe1.locator("td[onclick]")
    try:
        cells.first.wait_for(state="attached", timeout=timeout_s * 1000)
    except Exception as exc:
        print(f"  ⚠ 等了 {timeout_s}s 日历格子仍未出现：{str(exc).splitlines()[0]}")
        return None
    onclick = cells.last.get_attribute("onclick") or ""
    m = re.search(r"\d{4}-\d{2}-\d{2}", onclick)
    if not m:
        print(f"  ⚠ 最后一天格子的 onclick 里没有日期：{onclick[:80]!r}")
        return None
    return m.group(0)


def quick_submit(page, iframe1):
    """表单通常还保留着上次的日期/时段选择，直接再点一次「立即提交」就能重开验证码弹窗"""
    try:
        iframe1.get_by_text("立即提交").click(timeout=4000)
        page.locator("#img2").wait_for(state="visible", timeout=4000)
        return True
    except Exception:
        return False


def open_captcha_dialog(page, iframe1, date_str, start_id):
    """走一次选日期+选时段+提交，把验证码弹窗打开。返回是否成功。"""
    ensure_calendar(iframe1)
    if not safe_click(iframe1.locator(f'td[onclick*="{date_str}"]'), f"{date_str[-2:]} 号", page):
        return False
    # 点天之后才切到表单页（出现「立即提交」），等它再操作，别抢跑
    try:
        iframe1.get_by_text("立即提交").first.wait_for(state="visible", timeout=8000)
    except Exception:
        print("  ⚠ 点天后没进入预约表单（等不到「立即提交」）")
        return False
    page.wait_for_timeout(400)
    if not safe_click(iframe1.locator(f'[id="{start_id}"]'), f"起始格 {start_id}", page):
        return False
    if not safe_click(iframe1.locator(f'[id="{start_id + 4}"]'), f"结束格 {start_id + 4}", page):
        return False
    page.wait_for_timeout(200)
    safe_click(iframe1.locator(".layui-icon"), "勾选确认", page)
    if not safe_click(iframe1.get_by_text("立即提交"), "立即提交", page):
        return False
    page.wait_for_timeout(1200)
    try:
        page.locator("#img2").wait_for(state="visible", timeout=5000)
        return True
    except Exception:
        print("  ⚠ 提交后没出现验证码弹窗")
        return False


def close_captcha_dialog(page):
    """尽力关闭验证码弹窗：取消按钮 -> 右上角 X -> ESC，最后人工兜底"""
    for desc, loc in (("取消", page.get_by_text("取消")),
                      ("关闭X", page.locator(".layui-layer-close"))):
        try:
            if loc.first.is_visible(timeout=500):
                loc.first.click()
                page.wait_for_timeout(300)
                if not page.locator("#MathVerifyText").is_visible():
                    return True
        except Exception:
            pass
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    if not page.locator("#MathVerifyText").is_visible():
        return True
    input("  ⚠ 弹窗关不上，请手动关闭后回车继续...")
    return not page.locator("#MathVerifyText").is_visible()


def looks_like_captcha(shot: bytes) -> bool:
    """粗检：真验证码有不少高饱和彩色字形像素；加载失败的占位图（灰底黑字）几乎没有"""
    try:
        img = Image.open(io.BytesIO(shot)).convert("RGB")
    except Exception:
        return False
    px = list(img.getdata())[::7]
    colored = sum(1 for r, g, b in px if max(r, g, b) - min(r, g, b) > 60)
    return colored > len(px) * 0.03


def save_shot(shot: bytes, seen: set):
    """新图且看着像验证码才落盘并登记 manifest；重复（字节相同）或破图丢弃返回 None"""
    sha = hashlib.sha1(shot).hexdigest()
    if sha in seen or not looks_like_captcha(shot):
        return None
    SAMPLES_DIR.mkdir(exist_ok=True)
    ts = datetime.now()
    stem = ts.strftime("%Y%m%d_%H%M%S")
    png = SAMPLES_DIR / f"collect_{stem}.png"
    n = 1
    while png.exists():
        png = SAMPLES_DIR / f"collect_{stem}_{n}.png"
        n += 1
    png.write_bytes(shot)
    record = {"file": png.name, "time": ts.strftime("%Y-%m-%d %H:%M:%S"),
              "sha1": sha, "bytes": len(shot)}
    seen.add(sha)
    with MANIFEST.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page()
    page.on("dialog", lambda d: d.dismiss())  # 点了已被占的格子会弹 alert，直接忽略

    right_frame = page.locator("#right_container").content_frame
    if MANUAL:
        print("【手动模式】请在浏览器里登录 WebVPN，并进入该设备的「自主上机」预约页（看到日历或预约表单）。")
        print("  全程不会真实预约；脚本只负责：点最后一天 -> 选时段 -> 提交 -> 循环采验证码。")
        page.goto(URL)
        input("  准备就绪后回车开始...")
    else:
        print(f"自动登录并进入预约页（目标采集 {TARGET} 张，全程不会真实预约）...")
        try:
            page.goto(URL)
            page.get_by_role("textbox", name="请输入学号/工号").click()
            page.get_by_role("textbox", name="请输入学号/工号").fill(STUDENT_ID)
            page.get_by_role("textbox", name="请输入密码").fill(PASSWORD)
            page.get_by_role("textbox", name="请输入密码").press("Enter")
            page.get_by_role("button", name="用户登录").click()
            page.get_by_role("button", name="统一身份认证").click()
            page.get_by_text("仪器预约").click()
            page.wait_for_timeout(2000)
        except Exception as exc:
            print(f"  ⚠ 登录/导航自动流程失败：{str(exc).splitlines()[0]}")
            dump_debug(page, "login-fail")
            input("  请手动登录并进入「仪器预约」首页，然后回车继续...")

        try:
            search_box = right_frame.get_by_role("textbox", name="请输入搜索关键字")
            search_box.click()
            search_box.fill(SEARCH_CATEGORY)
            search_box.press("Enter")
            page.wait_for_timeout(1000)
            btns = right_frame.get_by_role("button", name="自主上机")
            if CLICK_STRATEGY == "nth1":
                btns.nth(1).click()
            elif CLICK_STRATEGY == "first":
                btns.first.click()
            else:
                btns.click()
            page.wait_for_timeout(1500)
        except Exception as exc:
            print(f"  ⚠ 选设备自动流程失败：{str(exc).splitlines()[0]}")
            dump_debug(page, "select-fail")
            input("  请手动进入该设备的预约页面，然后回车继续...")

    iframe1 = right_frame.locator("#Iframe1").content_frame
    # 页面先进「整月日历格」，点中某天才进入预约表单（才有「立即提交」）——先等日历格
    grid_ok = True
    try:
        iframe1.locator("td[onclick]").first.wait_for(state="attached", timeout=15000)
    except Exception:
        grid_ok = False
    page.wait_for_timeout(800)

    # 载入已有 manifest 的 sha1，跨批次也能标记重复
    seen = set()
    if MANIFEST.exists():
        for line in MANIFEST.read_text(encoding="utf-8").splitlines():
            try:
                seen.add(json.loads(line)["sha1"])
            except Exception:
                pass

    ok = False
    date_str = None
    if grid_ok:
        date_str = last_displayed_day(iframe1)
        if date_str:
            print(f"选用日历显示的最后一天：{date_str}")
        else:
            date_str = input("无法解析日历日期，手动输入目标日期（YYYY-MM-DD，留空则你自己点开弹窗）：").strip()
        for _ in range(3):
            if not date_str:
                break
            ok = open_captcha_dialog(page, iframe1, date_str, random.randint(8, 20))
            if ok:
                break
            page.wait_for_timeout(800)
        if not ok:
            dump_debug(page, "open-fail")
            input("✗ 打开验证码弹窗失败，请手动走一次到弹窗出现，然后回车开始采集...")
    else:
        # 没看到日历格：可能直接停在表单页（上次状态残留），试着直接提交
        ok = quick_submit(page, iframe1)
        if not ok:
            dump_debug(page, "grid-missing")
            input("  ⚠ 页面既没有日历格也没有可提交的表单，请手动走到验证码弹窗后回车继续...")

    saved, skipped, dup_streak, reopens_since_new = 0, 0, 0, 0
    print(f"\n开始采集（Ctrl+C 随时中断，已存的不丢；重复/破图不落盘）\n")
    try:
        while saved < TARGET:
            shot = page.locator("#img2").screenshot()
            rec = save_shot(shot, seen)
            if rec:
                saved += 1
                dup_streak = 0
                reopens_since_new = 0
                print(f"  [{saved}/{TARGET}] {rec['file']}")
            else:
                skipped += 1
                dup_streak += 1
                print(f"  [{saved}/{TARGET}] 跳过（重复或无效图）")

            if dup_streak >= 5:
                # 连吃 5 张旧图：点图刷新已失效，关弹窗重新提交换图
                dup_streak = 0
                reopens_since_new += 1
                if reopens_since_new >= 4:
                    dump_debug(page, "stuck")
                    input("  ⚠ 连续 4 次重开都没有新图，接口可能在限流。建议 Ctrl+C 收工、歇几分钟再跑；回车继续...")
                    reopens_since_new = 0
                close_captcha_dialog(page)
                if not quick_submit(page, iframe1):
                    # 光点提交开不了弹窗（表单选择丢了）：打开日历完整重选再提交
                    new_date = last_displayed_day(iframe1)
                    if new_date is None:
                        # 连日历都唤不出：可能 iframe 真被重置了。存现场图并尝试重新进预约页
                        dump_debug(page, "grid-lost")
                        print("  ↻ 日历丢失，尝试重新点「自主上机」恢复预约页...")
                        try:
                            btns = right_frame.get_by_role("button", name="自主上机")
                            if CLICK_STRATEGY == "nth1":
                                btns.nth(1).click()
                            elif CLICK_STRATEGY == "first":
                                btns.first.click()
                            else:
                                btns.click()
                            page.wait_for_timeout(2000)
                            new_date = last_displayed_day(iframe1, timeout_s=10)
                        except Exception:
                            pass
                    date_str = new_date or date_str
                    ok = bool(date_str) and open_captcha_dialog(page, iframe1, date_str, random.randint(8, 20))
                    if not ok:
                        dump_debug(page, "no-dialog")
                        input("  ⚠ 重新提交没出现弹窗，请把页面手动恢复到验证码弹窗，然后回车继续...")
            else:
                # 正常路径：点图片本体刷新（多数页面绑了 onclick 换图）
                try:
                    page.locator("#img2").click()
                except Exception:
                    pass

            # 节奏：随机间隔，每 30 张小歇、每 100 张大歇，别把验证码接口刷得太紧
            page.wait_for_timeout(random.randint(700, 1800))
            if saved and saved % 100 == 0:
                pause = random.randint(20, 40)
                print(f"  —— 已存 {saved} 张，大歇 {pause}s（防接口限流）——")
                page.wait_for_timeout(pause * 1000)
            elif saved and saved % 30 == 0:
                pause = random.randint(4, 8)
                print(f"  —— 已存 {saved} 张，休息 {pause}s ——")
                page.wait_for_timeout(pause * 1000)
    except KeyboardInterrupt:
        print("\n中断采集，已保存的样本都在 captcha_samples/。")

    print(f"\n========== 采集完成 ==========")
    print(f"  本次新图：{saved} 张（跳过重复/无效 {skipped} 张）")
    print(f"  累计入库：{len(seen)} 个不同哈希")
    print(f"  目录：{SAMPLES_DIR}")
    print("  下一步：人工对照图片把 labels 补齐（图 -> 表达式），即可用于评测/训练。")
    print("==============================")
    input("按 Enter 关闭浏览器...")
    browser.close()
