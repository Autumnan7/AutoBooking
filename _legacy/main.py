from playwright.sync_api import sync_playwright

USERNAME = "202422011018"
PASSWORD = "wyn1308419467"
INS_NAME ="通信场景"

URL = "https://dy.uestc.edu.cn/customer/index/index.html"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)

    page = browser.new_page()

    page.goto(URL)
    page.get_by_role("button", name="  用户登录").click()
    page.get_by_role("button", name="统一身份认证").click()


    page.get_by_role("textbox", name="请输入学号/工号").fill(USERNAME)
    page.get_by_role("textbox", name="请输入密码").fill(PASSWORD)

    page.get_by_role("checkbox").check()
    page.get_by_role("link", name="登录", exact=True).click()

    # 搜索
    page.get_by_role("textbox", name="仪器关键字检索").click()
    page.get_by_role("textbox", name="仪器关键字检索").fill("通信场景")

    # 点击搜索结果
    page.locator("a").nth(1).click()

    # 点击自主上机
    page.locator("#right_container").content_frame.get_by_role(
        "button",
        name="  自主上机"
    ).click()

    booking_frame = (
        page.locator("#right_container")
        .content_frame
        .locator("#Iframe1")
        .content_frame
    )

    booking_frame.get_by_role(
        "cell",
        name="08  已约0小时  剩余16小时"
    ).click()

    booking_frame.locator('[id="0"]').click()
    booking_frame.locator('[id="19"]').click()
    booking_frame.locator(".layui-icon").click()
    booking_frame.get_by_text("立即提交").click()

    # solve_captcha(page)

    input("登录完成，按回车退出...")
