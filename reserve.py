import calendar
import configparser
import io
import random
import re
from datetime import date, datetime
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

# 验证码 OCR（自动识别）
try:
    from ddddocr import DdddOcr
    _ocr = DdddOcr(show_ad=False, beta=True)
    _ocr.set_ranges("0123456789+-x×÷")  # 只允许输出数字和运算符
    DDDD_OCR_AVAILABLE = True
except Exception:
    DDDD_OCR_AVAILABLE = False
    _ocr = None

# ============================================================
# 用户配置：全部在本目录的 config.ini 中填写（不进代码、不进 git）
# 代码里只保留仪器列表等固定信息
# ============================================================
_config_path = Path(__file__).with_name("config.ini")
_cfg = configparser.ConfigParser()
if not _cfg.read(_config_path, encoding="utf-8-sig"):
    raise SystemExit(
        "✗ 未找到 config.ini：请在本目录新建 config.ini（完整格式见 README 第二节），"
        "填好账号后重跑。"
    )


def _cfg_pair(section, key, default):
    """解析「起,止」两个整数，如 start_range = 8,9 -> (8, 9)"""
    raw = _cfg.get(section, key, fallback=",".join(map(str, default))).strip()
    try:
        parts = [int(x) for x in raw.replace("，", ",").split(",")]
        if len(parts) != 2 or parts[0] > parts[1]:
            raise ValueError
        return parts[0], parts[1]
    except ValueError:
        raise SystemExit(f"✗ config.ini 中 [{section}] {key} 应为「起,止」两个整数，当前为：{raw!r}")


def _cfg_day(section, key):
    """解析当月几号：留空返回 None（运行时询问）"""
    raw = _cfg.get(section, key, fallback="").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        raise SystemExit(f"✗ config.ini 中 [{section}] {key} 应为当月几号（整数）或留空。")


STUDENT_ID = _cfg.get("account", "student_id", fallback="").strip()
PASSWORD = _cfg.get("account", "password", fallback="").strip()
if not STUDENT_ID or not PASSWORD:
    raise SystemExit("✗ 请打开 config.ini 的 [account]，把 student_id 和 password 填写完整后再运行。")

INSTRUMENT_INDEX = _cfg.get("instrument", "index", fallback="1").strip()

START_RANGE = _cfg_pair("time", "start_range", (8, 9))     # 开始时间随机区间（小时）
END_RANGE = _cfg_pair("time", "end_range", (18, 23))       # 结束时间随机区间（小时）
try:
    MONTHLY_BUDGET = int(_cfg.get("time", "monthly_budget", fallback="200"))
except ValueError:
    raise SystemExit("✗ config.ini 中 [time] monthly_budget 应为整数（小时）。")

START_DAY = _cfg_day("date", "start_day")   # 开始日期（当月几号）；留空则运行时询问
END_DAY = _cfg_day("date", "end_day")       # 结束日期（当月几号）；留空则运行时询问

# 可选仪器列表（一般无需改动，config.ini 的 index 序号对应本列表顺序）
INSTRUMENTS = [
    {"name": "通信场景仿真攻防对抗实验教学设备", "click": None,    "tag": ""},
    {"name": "可重构高性能无损网络实验教学设备",   "click": "first", "tag": "S2507234"},
    {"name": "可重构高性能无损网络实验教学设备",   "click": "nth1",  "tag": "S2507235"},
    {"name": "可编程SDN网络技术实验教学设备",      "click": None,    "tag": ""},
    {"name": "5G-A通感一体化网络实验教学设备",     "click": None,    "tag": ""},
]
# ============================================================

# ---- 以下为自动计算，一般无需改动 ----
_today = date.today()
_last_day = calendar.monthrange(_today.year, _today.month)[1]

# 选择仪器：必须用配置里的 INSTRUMENT_INDEX，不允许运行时输入
try:
    _choice = int(INSTRUMENT_INDEX)
except (TypeError, ValueError):
    raise SystemExit("✗ 请在 config.ini 的 [instrument] index 设为 1-5 的整数。")
if not (1 <= _choice <= len(INSTRUMENTS)):
    raise SystemExit(f"✗ INSTRUMENT_INDEX 必须为 1 到 {len(INSTRUMENTS)} 的整数，当前为：{INSTRUMENT_INDEX!r}。")
_chosen = INSTRUMENTS[_choice - 1]
SEARCH_CATEGORY = _chosen["name"]
CLICK_STRATEGY = _chosen["click"]
print(f"已选择：{SEARCH_CATEGORY}{(' ' + _chosen['tag']) if _chosen['tag'] else ''}")

# 日期范围：配置里写了 START_DAY / END_DAY 就直接用，否则运行时询问
if START_DAY is None:
    _first_weekday = next(
        (d for d in range(_today.day, _last_day + 1)
         if date(_today.year, _today.month, d).weekday() < 5),
        _today.day,
    )
    print()
    _start_input = input(f"开始日期（默认从 {_first_weekday} 号起，直接回车确认 / 输入天数）：").strip()
    START_DAY = int(_start_input) if _start_input else _first_weekday

if END_DAY is None:
    print()
    _end_input = input(f"结束日期（默认到月底 {_last_day} 号，直接回车确认 / 输入天数，如 20）：").strip()
    END_DAY = int(_end_input) if _end_input else _last_day

DATES = [
    day for day in range(START_DAY, min(END_DAY, _last_day) + 1)
    if date(_today.year, _today.month, day).weekday() < 5  # 0=周一 ... 4=周五
]


def time_to_id(hour):
    """预约系统从 08:00 开始：08:00 -> 0，08:30 -> 1，09:00 -> 2 ..."""
    return int((hour - 8) * 2)


def fmt_time(hour):
    """将小时数格式化为 HH:MM 字符串（支持半小时）"""
    h = int(hour)
    m = "30" if hour % 1 else "00"
    return f"{h:02d}:{m}"


def recognize_captcha(image_bytes: bytes) -> str:
    """识别验证码图片（字节流），返回图片中的文字"""
    if not DDDD_OCR_AVAILABLE or _ocr is None:
        raise RuntimeError("ddddocr 未安装，无法自动识别验证码")
    # 格式固定为「a 运算 b = ?」：裁掉右侧的 "=?" 再放大 3 倍，
    # 让等号问号不参与识别；裁剪比例取宽松的 0.8，宁可残留 "=?"（解析时会忽略）
    # 也不能切到第二位数字
    img = Image.open(io.BytesIO(image_bytes)).convert("L")
    w, h = img.size
    img = img.crop((0, 0, int(w * 0.8), h))
    img = img.resize((int(w * 0.8) * 3, h * 3), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return str(_ocr.classification(buf.getvalue()))


def calc_captcha(text: str) -> int:
    """解析并计算简单算术表达式，如 '3+5=?', '12-7=?', '8×9=?'"""
    # 统一字符：去掉空格、等号、问号
    expr = re.sub(r"\s+|\?=|=?$", "", text.strip())
    expr = expr.replace("＝", "=").replace("×", "*").replace("÷", "/")
    expr = expr.replace("x", "*").replace("X", "*")

    # 提取数字和运算符
    tokens = re.findall(r"\d+|[+\-*/]", expr)
    if len(tokens) < 3:
        raise ValueError(f"无法解析验证码: {text}")

    a, op, b = tokens[0], tokens[1], tokens[2]
    a, b = int(a), int(b)
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if op == "/":
        return a // b
    raise ValueError(f"未知运算符: {op}")


def random_time_slots(min_start_half=None):
    """开始时间随机在 START_RANGE，结束时间随机在 END_RANGE（支持半小时）；
    min_start_half：起始格下限（半小时粒度），当天预约时用于跳过已过去的时间"""
    lo = START_RANGE[0] * 2
    if min_start_half is not None:
        lo = max(lo, min_start_half)
    start_half = random.randint(lo, START_RANGE[1] * 2)  # 半小时粒度
    end_half = random.randint(END_RANGE[0] * 2, END_RANGE[1] * 2)
    if end_half <= start_half:  # 兜底：结束至少比开始晚半小时
        end_half = min(start_half + 1, END_RANGE[1] * 2)
    return start_half / 2, end_half / 2


def safe_click(page, locator, action_desc):
    """点击某元素：先滚动进可视区再点；失败不崩溃，提示手动接管后回车继续"""
    try:
        locator.scroll_into_view_if_needed()
        page.wait_for_timeout(200)
        locator.click(timeout=8000)
        return True
    except Exception as exc:
        print(f"  ⚠ 自动点击「{action_desc}」失败：{exc}")
        input("  请在浏览器中手动完成该步，然后按回车继续...")
        return False


# 预生成整月预约计划
# 开始日期是今天时，已过去的时间格在页面上选不了：起始至少推到「下一个半点 + 1 小时缓冲」；
# 若连 START_RANGE 的上限都来不及了（如下午才跑、起始区间是早上），则直接跳过当天
_today_min_start_half = None
if _today.day in DATES:
    _now = datetime.now()
    _today_min_start_half = _now.hour * 2 + (1 if _now.minute >= 30 else 0) + 2

SCHEDULE = []
for _day in DATES:
    _min_half = _today_min_start_half if _day == _today.day else None
    if _min_half is not None and _min_half > START_RANGE[1] * 2:
        print(f"提示：今天（{_today.day} 号）的起始区间已过，无法再约当天，跳过。")
        continue
    _start, _end = random_time_slots(_min_half)
    SCHEDULE.append([_day, _start, _end])  # 用 list 以便后续调整

# 总时长超预算时，从最长的那天开始把结束时间往前收（最少收到 18:00）
def _total():
    return sum(e - s for _, s, e in SCHEDULE)

while _total() > MONTHLY_BUDGET and SCHEDULE:
    _idx = max(range(len(SCHEDULE)), key=lambda i: SCHEDULE[i][2] - SCHEDULE[i][1])
    _e = SCHEDULE[_idx][2]
    _new_e = _e - 0.5
    if _new_e < END_RANGE[0]:  # 已收到下限，移除该天
        SCHEDULE.pop(_idx)
        continue
    SCHEDULE[_idx][2] = _new_e

print()
print("========== 预约计划 ==========")
_total_hours = 0
for _day, _start, _end in SCHEDULE:
    _weekday = ["周一", "周二", "周三", "周四", "周五"][
        date(_today.year, _today.month, _day).weekday()
    ]
    _hours = _end - _start
    _total_hours += _hours
    print(f"  {(_today.month):>2}月{_day:>2}日 ({_weekday})  {fmt_time(_start)} ~ {fmt_time(_end)}  ({_hours:g}h)")
print(f"共 {len(SCHEDULE)} 天，预计 {_total_hours:g} 小时")
print("==============================")
print()

confirm = input("确认执行以上预约计划？（回车确认 / 输入 n 取消）：").strip().lower()
if confirm == "n":
    print("已取消。")
    exit()


URL = ("https://webvpn.uestc.edu.cn/https/77726476706e69737468656265737421f4ee0f8922237c53300d8db9d6562d/customer/index/index.html")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page()

    # 1. 打开首页（会自动跳转到 CAS 登录页）
    print("打开 WebVPN...")
    page.goto(URL)

    # 2. 登录
    print("登录 WebVPN...")
    page.get_by_role("textbox", name="请输入学号/工号").click()
    page.get_by_role("textbox", name="请输入学号/工号").fill(STUDENT_ID)
    page.get_by_role("textbox", name="请输入学号/工号").press("Tab")
    page.get_by_role("textbox", name="请输入密码").fill(PASSWORD)
    page.get_by_role("textbox", name="请输入密码").press("Enter")

    # 3. 用户登录 -> 统一身份认证
    print("点击用户登录...")
    page.get_by_role("button", name="用户登录").click()

    print("进入统一身份认证...")
    page.get_by_role("button", name="统一身份认证").click()

    # 4. 仪器预约
    print("进入仪器预约...")
    page.get_by_text("仪器预约").click()
    page.wait_for_timeout(2000)  # 给右侧 iframe 加载时间

    # 5. 右侧 iframe
    right_frame = page.locator("#right_container").content_frame

    # 6. 搜索仪器类别
    if SEARCH_CATEGORY:
        print(f"搜索：{SEARCH_CATEGORY}...")
        search_box = right_frame.get_by_role("textbox", name="请输入搜索关键字")
        search_box.click()
        search_box.fill(SEARCH_CATEGORY)
        search_box.press("Enter")
        page.wait_for_timeout(1000)

    # 7. 自主上机
    print("进入自主上机...")
    _self_btns = right_frame.get_by_role("button", name="自主上机")
    # 直接按所选设备的策略点击（配置已明确，无需运行时检测数量）：
    #   None    -> 不重名单台设备：直接点
    #   "first" -> 重名两台中的第一台（S2507234）
    #   "nth1"  -> 重名两台中的第二台（S2507235）
    if CLICK_STRATEGY == "nth1":
        _self_btns.nth(1).click()
    elif CLICK_STRATEGY == "first":
        _self_btns.first.click()
    else:
        _self_btns.click()
    page.wait_for_timeout(1000)

    # 8. 预约 iframe
    iframe1 = right_frame.locator("#Iframe1").content_frame

    page.wait_for_timeout(2000)

    # 9. 逐天预约：选择日期 + 随机时间 + 提交
    failed_days = []
    for day, start_hour, end_hour in SCHEDULE:
        start_slot = time_to_id(start_hour)
        end_slot = time_to_id(end_hour) - 1

        print()
        print(f"--- {day} 号 | {fmt_time(start_hour)} ~ {fmt_time(end_hour)} ---")

        try:
            # 选择日期（用 onclick 里的完整日期精确定位，避免 7 命中 17/27）
            _date_str = f"{_today.year}-{_today.month:02d}-{day:02d}"
            print(f"  选择日期：{day} 号")
            safe_click(page, iframe1.locator(f'td[onclick*="{_date_str}"]'), f"选择 {day} 号")
            page.wait_for_timeout(300)

            # 选择时间：只点起始格和结束格，中间自动连成区间
            print(f"  点击起始格 ID：{start_slot}")
            safe_click(page, iframe1.locator(f'[id="{start_slot}"]'), f"起始格 {start_slot}")
            page.wait_for_timeout(300)

            print(f"  点击结束格 ID：{end_slot}")
            safe_click(page, iframe1.locator(f'[id="{end_slot}"]'), f"结束格 {end_slot}")
            page.wait_for_timeout(300)

            # 勾选确认
            safe_click(page, iframe1.locator(".layui-icon"), "勾选确认")
            page.wait_for_timeout(200)

            # 立即提交
            print("  点击立即提交...")
            safe_click(page, iframe1.get_by_text("立即提交"), "立即提交")
            page.wait_for_timeout(1000)

            # 验证码（直接字节流，不落盘）
            captcha_bytes = page.locator("#img2").screenshot()
            answer = None
            if DDDD_OCR_AVAILABLE:
                try:
                    captcha_text = recognize_captcha(captcha_bytes)
                    print(f"  验证码识别：{captcha_text}")
                    answer = str(calc_captcha(captcha_text))
                    print(f"  自动计算结果：{answer}")
                except Exception as e:
                    print(f"  自动识别失败：{e}")

            if answer is None:
                answer = input("  请输入验证码答案：").strip()

            page.locator("#MathVerifyText").fill(answer)

            # 确定
            print("  提交验证码...")
            safe_click(page, page.get_by_text("确定"), "确定")
            page.wait_for_timeout(1000)

            confirm = page.get_by_text("确定")
            if confirm.is_visible():
                confirm.click()

            page.wait_for_timeout(3000)
        except Exception as exc:
            print(f"  ✗ {day} 号预约失败：{exc}")
            failed_days.append(day)
            page.wait_for_timeout(500)

    print()
    print("==============================")
    print(f"预约流程执行完成！成功 {len(SCHEDULE) - len(failed_days)} 天")
    if failed_days:
        print(f"失败日期：{failed_days}")
    print("==============================")
    print()

    input("按 Enter 键退出浏览器...")
    browser.close()
