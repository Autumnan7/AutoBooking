import calendar
import configparser
import json
import random
import re
import traceback
from datetime import date, datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

# 验证码识别（v2：分割 + 运算符几何判别 + 数字 OCR，见 captcha_ocr.py）
try:
    import captcha_ocr
    CAPTCHA_OCR_AVAILABLE = True
except Exception:
    CAPTCHA_OCR_AVAILABLE = False

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
try:
    YEARLY_TARGET = float(_cfg.get("time", "yearly_target", fallback="1700"))
except ValueError:
    raise SystemExit("✗ config.ini 中 [time] yearly_target 应为数字（小时）。")

START_DAY = _cfg_day("date", "start_day")   # 开始日期（当月几号）；留空则运行时询问
END_DAY = _cfg_day("date", "end_day")       # 结束日期（当月几号）；留空则运行时询问

# 是否把每次验证码原图落盘到 captcha_samples/（图片 + labels.jsonl 记录），
# 用于积累数据集（后续做模板匹配或训练识别模型都靠它）；不用时关掉
SAVE_CAPTCHA_SAMPLES = _cfg.get("ocr", "save_samples", fallback="false").strip().lower() in ("1", "true", "yes", "on")
SAMPLES_DIR = Path(__file__).with_name("captcha_samples")
HISTORY_PATH = Path(__file__).with_name("booking_history.json")
LOG_PATH = Path(__file__).with_name("reserve.log")

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


def load_history():
    """读取 booking_history.json（{"2026-11": 小时数}）；不存在/损坏时按空记录处理。"""
    if not HISTORY_PATH.exists():
        return {}
    try:
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("顶层应为 {月份: 小时数}")
        # 兼容早期按日期记录的格式 {"bookings": {"2026-11-03": {"hours": ...}}}
        if isinstance(data.get("bookings"), dict):
            merged = {}
            for key, entry in data["bookings"].items():
                month = key[:7]
                merged[month] = merged.get(month, 0) + float(entry.get("hours", 0))
            return merged
        return {k: float(v) for k, v in data.items()
                if isinstance(v, (int, float))}
    except Exception as exc:
        print(f"  ⚠ 历史记录读取失败（本次按空记录处理）：{exc}")
        return {}


def save_history(history):
    """原子写入，避免中途崩溃把记录写坏。"""
    tmp = HISTORY_PATH.with_name(HISTORY_PATH.name + ".tmp")
    tmp.write_text(json.dumps(history, ensure_ascii=False, indent=2, sort_keys=True),
                   encoding="utf-8")
    tmp.replace(HISTORY_PATH)


def record_booking(history, hours):
    """把一次成功提交的时长累加到当前月份（每月只保留一条记录）。"""
    key = f"{_today.year}-{_today.month:02d}"
    history[key] = round(float(history.get(key, 0)) + hours, 2)
    try:
        save_history(history)
    except Exception as exc:
        print(f"  ⚠ 时长记录写入失败（不影响预约）：{exc}")


def history_totals(history, year=None):
    """返回 (该年累计小时, 该年当前月小时)。"""
    year = year or _today.year
    year_total = sum(float(v) for k, v in history.items()
                     if k.startswith(f"{year}-"))
    month_total = float(history.get(f"{year}-{_today.month:02d}", 0))
    return year_total, month_total


def print_history(history, title="预约时长记录", planned_hours=None):
    """打印本月/本年累计，以及与年度目标的差额；可附带本次计划后的预计值。"""
    year_total, month_total = history_totals(history)
    remaining = YEARLY_TARGET - year_total
    print()
    print(f"========== {title} ==========")
    print(f"  {_today.year} 年 {_today.month} 月已记录：{month_total:g} 小时")
    if remaining > 0:
        print(f"  {_today.year} 年累计：{year_total:g} / {YEARLY_TARGET:g} 小时"
              f"（还差 {remaining:g} 小时）")
    else:
        print(f"  {_today.year} 年累计：{year_total:g} / {YEARLY_TARGET:g} 小时（已达标）")
    if planned_hours is not None:
        print(f"  本次计划：{planned_hours:g} 小时"
              f"（执行后年度累计约 {year_total + planned_hours:g} 小时）")
    print("==============================")


def save_captcha_sample(image_bytes: bytes, *, day=None, ocr_text=None,
                        auto_answer=None, final_answer=None, source=None):
    """把一张验证码原图落盘，并在 labels.jsonl 追加一条记录。
    source：auto=自动识别的答案；manual=人工输入。
    记录同时保留识别文本与最终提交答案，方便之后清洗数据集、核对标注"""
    if not SAVE_CAPTCHA_SAMPLES:
        return
    try:
        SAMPLES_DIR.mkdir(exist_ok=True)
        ts = datetime.now()
        stem = ts.strftime("%Y%m%d_%H%M%S")
        png = SAMPLES_DIR / f"{stem}.png"
        n = 1
        while png.exists():  # 同秒重跑时加序号，避免覆盖
            png = SAMPLES_DIR / f"{stem}_{n}.png"
            n += 1
        png.write_bytes(image_bytes)
        record = {
            "file": png.name,
            "time": ts.strftime("%Y-%m-%d %H:%M:%S"),
            "day": day,
            "ocr_text": ocr_text,
            "auto_answer": auto_answer,
            "final_answer": final_answer,
            "source": source,
        }
        with (SAMPLES_DIR / "labels.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"  样本已落盘：{png.name}")
    except Exception as e:
        print(f"  ⚠ 样本落盘失败（不影响预约）：{e}")


def recognize_captcha(image_bytes: bytes) -> str:
    """识别验证码图片（字节流），返回 "a<op>b" 形式的表达式"""
    if not CAPTCHA_OCR_AVAILABLE:
        raise RuntimeError("验证码识别模块不可用（ddddocr/opencv 未安装）")
    return captcha_ocr.recognize(image_bytes)


def calc_captcha(text: str) -> int:
    """解析并计算简单算术表达式，如 '3+5=?', '12-7=?', '8×9=?'"""
    # 统一字符：去掉空格、等号、问号
    expr = re.sub(r"\s+|\?=|=?$", "", text.strip())
    expr = expr.replace("＝", "=").replace("×", "*").replace("÷", "/")
    expr = expr.replace("x", "*").replace("X", "*")
    expr = re.sub(r"[+\-*/]+$", "", expr)  # 模型偶尔把尾部 "=" 读成运算符，剥掉

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


def log_event(message):
    """把带时间戳的明细追加到 reserve.log（控制台只打印简短信息）。"""
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n")
    except Exception:
        pass


def log_error(context, exc):
    """记录上下文 + 完整堆栈；控制台不打印这一大段。"""
    log_event(f"{context} | {type(exc).__name__}: {exc}")
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(traceback.format_exc() + "\n")
    except Exception:
        pass


def short_error(exc):
    """异常只取第一行给控制台，避免刷屏；完整内容在 reserve.log。"""
    text = str(exc).strip()
    first = text.splitlines()[0] if text else type(exc).__name__
    if "outside of the viewport" in text:
        return f"{first}（元素在 iframe 内无法滚入可视区）"
    return first


class StepSkipped(Exception):
    """某一步无法自动完成，跳过当前日期（整月批处理不做人工等待）。"""


def skip_step(reason):
    """记录原因并跳过当天；由每天的执行块统一捕获。"""
    print(f"  ⏭ {reason}，跳过当天。")
    raise StepSkipped(reason)


def safe_click(page, locator, action_desc):
    """点击某元素：先滚动进可视区再点。
    物理点击失败时（常见于 iframe 内元素报 outside of the viewport），
    回退到 DOM click；两者都失败才跳过当天。"""
    try:
        locator.scroll_into_view_if_needed()
        # 页面在嵌套 iframe 里，且时间格子有自己的滚动容器：
        # Playwright 自带滚动有时滚不到正确容器，再用 JS 强制滚到视口中间。
        try:
            locator.evaluate("el => el.scrollIntoView({block: 'center', inline: 'center'})")
        except Exception:
            pass
        page.wait_for_timeout(200)
        locator.click(timeout=8000)
        return True
    except Exception as exc:
        log_error(f"点击「{action_desc}」失败", exc)
        try:
            locator.evaluate("el => el.click()")
            print(f"  ↳ 物理点击失败，已改用 DOM click 完成「{action_desc}」")
            log_event(f"「{action_desc}」已用 DOM click 兜底成功")
            return True
        except Exception as fallback_exc:
            log_error(f"点击「{action_desc}」的 DOM click 兜底也失败", fallback_exc)
            print(f"  ⚠ 自动点击「{action_desc}」失败：{short_error(exc)}")
            print(f"  ↳ DOM click 兜底也失败：{short_error(fallback_exc)}"
                  f"（完整堆栈见 {LOG_PATH.name}）")
            skip_step(f"「{action_desc}」无法完成")


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

history = load_history()
print_history(history, planned_hours=_total_hours)
print()

confirm = input("确认执行以上预约计划？（回车确认 / 输入 n 取消）：").strip().lower()
if confirm == "n":
    print("已取消。")
    exit()

log_event("=" * 60)
log_event(f"开始预约：{_today.year}-{_today.month:02d}，{len(SCHEDULE)} 天，"
          f"共 {_total_hours:g} 小时")


URL = ("https://webvpn.uestc.edu.cn/https/77726476706e69737468656265737421f4ee0f8922237c53300d8db9d6562d/customer/index/index.html")

with sync_playwright() as p:
    # 最大化窗口：默认 1280×720 视口下，预约表格靠下的时间格会落在视口外，
    # Playwright 会拒绝点击（element is outside of the viewport）。
    browser = p.chromium.launch(headless=False, args=["--start-maximized"])
    context = browser.new_context(no_viewport=True)
    page = context.new_page()

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
    booked_days = []
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

            # 验证码（字节流；[ocr] save_samples=true 时同步落盘做数据集）
            captcha_bytes = page.locator("#img2").screenshot()
            ocr_text = None
            auto_answer = None
            if not CAPTCHA_OCR_AVAILABLE:
                skip_step("验证码识别模块不可用")
            for attempt in range(3):
                try:
                    ocr_text = recognize_captcha(captcha_bytes)
                    print(f"  验证码识别：{ocr_text}")
                    auto_answer = str(calc_captcha(ocr_text))
                    break
                except Exception as e:
                    log_error(f"{day} 号验证码识别失败（第 {attempt + 1}/3 次）", e)
                    print(f"  验证码识别失败（第 {attempt + 1}/3 次）：{short_error(e)}")
                    if attempt == 2:
                        # 失败样本也落盘，方便事后分析/补标注
                        save_captcha_sample(captcha_bytes, day=day, ocr_text=ocr_text,
                                            auto_answer=auto_answer, final_answer=None,
                                            source="failed")
                        skip_step("验证码识别失败")
                    # 验证码没加载出来/太糊：点图片刷新后重试
                    try:
                        page.locator("#img2").click(timeout=5000)
                        page.wait_for_timeout(1000)
                        captcha_bytes = page.locator("#img2").screenshot()
                    except Exception as refresh_exc:
                        print(f"  ⚠ 刷新验证码失败：{refresh_exc}")
                        skip_step("验证码刷新失败")

            print(f"  自动计算结果：{auto_answer}")
            answer = auto_answer

            save_captcha_sample(captcha_bytes, day=day, ocr_text=ocr_text,
                                auto_answer=auto_answer, final_answer=answer, source="auto")

            page.locator("#MathVerifyText").fill(answer)

            # 确定
            print("  提交验证码...")
            safe_click(page, page.get_by_text("确定"), "确定")
            page.wait_for_timeout(1000)

            confirm = page.get_by_text("确定")
            if confirm.is_visible():
                confirm.click()

            page.wait_for_timeout(3000)

            # 提交完成，把当天时长累加到本月台账
            record_booking(history, end_hour - start_hour)
            booked_days.append(day)
        except StepSkipped as exc:
            log_event(f"{day} 号跳过：{exc}")
            print(f"  ⏭ {day} 号已跳过：{exc}")
            failed_days.append(day)
            page.wait_for_timeout(500)
        except Exception as exc:
            log_error(f"{day} 号预约失败", exc)
            print(f"  ✗ {day} 号预约失败：{short_error(exc)}"
                  f"（完整堆栈见 {LOG_PATH.name}）")
            failed_days.append(day)
            page.wait_for_timeout(500)

    print()
    print("==============================")
    print(f"预约流程执行完成！成功 {len(booked_days)} 天，跳过/失败 {len(failed_days)} 天")
    if failed_days:
        print(f"失败日期：{failed_days}")
    print("==============================")

    _run_hours = sum(e - s for d, s, e in SCHEDULE if d in booked_days)
    if booked_days:
        print(f"本次入账：{len(booked_days)} 天 / {_run_hours:g} 小时")
    print_history(history, "预约时长记录（更新后）")
    print(f"详细日志：{LOG_PATH.name}")
    log_event(f"执行完成：成功 {len(booked_days)} 天，失败 {len(failed_days)} 天，"
              f"失败日期 {failed_days}")
    print()

    browser.close()
