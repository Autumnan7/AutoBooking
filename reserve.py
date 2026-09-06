from playwright.sync_api import sync_playwright

# ============================================================
# 预约配置
# ============================================================

# ---------- 日期 ----------
# 第几行
DATE_ROW = 4

# 从第几列开始
START_COL = 2

# 选择到第几列
END_COL = 3


# ---------- 时间 ----------
# 8  = 08:00
# 22 = 22:00
#
# 每 30 分钟一个时间段
START_TIME = 8
END_TIME = 22


# ============================================================
# 登录信息
# ============================================================

STUDENT_ID = "202422011018"
PASSWORD = "wyn1308419467"


# ============================================================
# 时间转换
# ============================================================

def time_to_id(hour):
    """
    预约系统从 08:00 开始：
    
    08:00 -> ID 0
    08:30 -> ID 1
    09:00 -> ID 2
    ...
    """

    return (hour - 8) * 2


# 检查时间
if not (8 <= START_TIME < END_TIME <= 24):
    raise ValueError(
        "时间必须满足：8 <= START_TIME < END_TIME <= 24"
    )


# 计算开始和结束 ID
start_id = time_to_id(START_TIME)
end_id = time_to_id(END_TIME)


# END_TIME 是结束时间，所以不包含 end_id
TIME_IDS = [
    str(i)
    for i in range(start_id, end_id)
]


# ============================================================
# 显示预约配置
# ============================================================

print()
print("========== 预约配置 ==========")

print(
    f"日期：第 {DATE_ROW} 行，"
    f"第 {START_COL} 列 ~ 第 {END_COL} 列"
)

print(
    f"时间：{START_TIME:02d}:00 ~ "
    f"{END_TIME:02d}:00"
)

print(
    f"时间 ID：{TIME_IDS}"
)

print("==============================")
print()


# ============================================================
# 启动 Playwright
# ============================================================

with sync_playwright() as p:

    browser = p.chromium.launch(
        headless=False
    )

    page = browser.new_page()


    # ========================================================
    # 1. 打开 WebVPN
    # ========================================================

    print("打开 WebVPN...")

    # 与 test.py（Codegen 录制）一致：直连 CAS 登录页
    page.goto(
        "https://webvpn.uestc.edu.cn/https/77726476706e69737468656265737421f9f3408f69256d436a0bc7a99c406d3652/authserver/login?service=https%3A%2F%2Fwebvpn.uestc.edu.cn%2Flogin%3Fcas_login%3Dtrue"
    )

    page.wait_for_load_state(
        "networkidle"
    )


    # ========================================================
    # 2. 登录
    # ========================================================

    print("登录 WebVPN...")

    # 与 test.py（Codegen 录制）一致：
    # 按 name 定位，Tab 切换到密码框，回车提交

    page.get_by_role(
        "textbox",
        name="请输入学号/工号"
    ).click()

    page.get_by_role(
        "textbox",
        name="请输入学号/工号"
    ).fill(STUDENT_ID)

    page.get_by_role(
        "textbox",
        name="请输入学号/工号"
    ).press("Tab")

    page.get_by_role(
        "textbox",
        name="请输入密码"
    ).fill(PASSWORD)

    page.get_by_role(
        "textbox",
        name="请输入密码"
    ).press("Enter")

    page.wait_for_load_state(
        "networkidle"
    )


    # ========================================================
    # 3. 用户登录 -> 统一身份认证
    # ========================================================

    print("点击用户登录...")

    page.get_by_role(
        "button",
        name="用户登录"
    ).click()

    page.wait_for_load_state(
        "networkidle"
    )

    print("进入统一身份认证...")

    page.get_by_role(
        "button",
        name="统一身份认证"
    ).click()

    page.wait_for_load_state(
        "networkidle"
    )


    # ========================================================
    # 4. 仪器预约
    # ========================================================

    print("进入仪器预约...")

    page.get_by_text(
        "仪器预约"
    ).click()

    page.wait_for_load_state(
        "networkidle"
    )


    # ========================================================
    # 5. 获取 right_container iframe
    # ========================================================

    right_frame = page.locator(
        "#right_container"
    ).content_frame


    # ========================================================
    # 6. 搜索「通信场景」
    # ========================================================

    print("搜索：通信场景...")

    search_box = right_frame.get_by_role(
        "textbox",
        name="请输入搜索关键字"
    )

    search_box.click()

    search_box.fill(
        "通信场景"
    )

    search_box.press(
        "Enter"
    )

    page.wait_for_timeout(
        1000
    )


    # ========================================================
    # 7. 点击「自主上机」
    # ========================================================

    print("进入自主上机...")

    right_frame.get_by_role(
        "button",
        name="  自主上机"
    ).click()

    page.wait_for_timeout(
        1000
    )


    # ========================================================
    # 8. 获取 Iframe1
    # ========================================================

    iframe1 = right_frame.locator(
        "#Iframe1"
    ).content_frame


    # ========================================================
    # 9. 选择日期
    # ========================================================
    #
    # 例如：
    #
    # DATE_ROW = 2
    # START_COL = 3
    # END_COL = 4
    #
    # 就是：
    #
    # 第 2 行
    # 第 3 列
    # 第 4 列
    #
    # 注意：
    # 这里假设日历使用 tr / td 结构。
    #

    print()
    print("选择日期...")

    rows = iframe1.locator(
        "tr"
    )

    row = rows.nth(
        DATE_ROW - 1
    )

    cells = row.locator(
        "td"
    )

    for col in range(
        START_COL,
        END_COL + 1
    ):

        print(
            f"选择第 {DATE_ROW} 行 "
            f"第 {col} 列"
        )

        cell = cells.nth(
            col - 1
        )

        cell.click()

        page.wait_for_timeout(
            200
        )


    # ========================================================
    # 10. 选择时间
    # ========================================================

    print()
    print(
        f"选择时间："
        f"{START_TIME:02d}:00 ~ "
        f"{END_TIME:02d}:00"
    )

    print(
        f"对应 ID：{TIME_IDS}"
    )

    for time_id in TIME_IDS:

        print(
            f"选择时间 ID：{time_id}"
        )

        iframe1.locator(
            f'[id="{time_id}"]'
        ).click()

        page.wait_for_timeout(
            100
        )


    # ========================================================
    # 11. 点击立即提交
    # ========================================================

    print()
    print("点击立即提交...")

    iframe1.get_by_text(
        "立即提交"
    ).click()

    page.wait_for_timeout(
        1000
    )


    # ========================================================
    # 12. 保存验证码
    # ========================================================

    print()
    print("保存验证码...")

    captcha = page.locator(
        "#img2"
    )

    captcha.screenshot(
        path="captcha.png"
    )

    print(
        "验证码已保存：captcha.png"
    )


    # ========================================================
    # 13. 手动输入验证码
    # ========================================================

    answer = input(
        "请输入验证码答案："
    ).strip()

    page.locator(
        "#MathVerifyText"
    ).fill(
        answer
    )


    # ========================================================
    # 14. 点击确定
    # ========================================================

    print(
        "提交验证码..."
    )

    page.get_by_text(
        "确定"
    ).click()

    page.wait_for_timeout(
        1000
    )


    # ========================================================
    # 15. 如果还有第二个确定按钮
    # ========================================================

    try:

        confirm = page.get_by_text(
            "确定"
        )

        if confirm.is_visible():

            confirm.click()

    except Exception:

        pass


    # ========================================================
    # 16. 完成
    # ========================================================

    print()
    print("==============================")
    print("预约流程执行完成！")
    print("==============================")
    print()

    input(
        "按 Enter 键退出浏览器..."
    )

    browser.close()