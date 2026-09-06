import re
from playwright.sync_api import Playwright, sync_playwright, expect


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://webvpn.uestc.edu.cn/https/77726476706e69737468656265737421f9f3408f69256d436a0bc7a99c406d3652/authserver/login?service=https%3A%2F%2Fwebvpn.uestc.edu.cn%2Flogin%3Fcas_login%3Dtrue")
    page.get_by_role("textbox", name="请输入学号/工号").click()
    page.get_by_role("textbox", name="请输入学号/工号").fill("202422011018")
    page.get_by_role("textbox", name="请输入学号/工号").press("Tab")
    page.get_by_role("textbox", name="请输入密码").fill("wyn1308419467")
    page.get_by_role("textbox", name="请输入密码").press("Enter")
    page.get_by_role("button", name="  用户登录").click()
    page.get_by_role("button", name="统一身份认证").click()
    page.get_by_text("仪器预约").click()
    page.locator("#right_container").content_frame.get_by_role("textbox", name="请输入搜索关键字").click()
    page.locator("#right_container").content_frame.get_by_role("textbox", name="请输入搜索关键字").fill("通信场景")
    page.locator("#right_container").content_frame.get_by_role("textbox", name="请输入搜索关键字").press("Enter")
    page.locator("#right_container").content_frame.get_by_role("button", name="  自主上机").click()
    page.locator("#right_container").content_frame.locator("#Iframe1").content_frame.get_by_role("cell", name=" 已约3.5小时  剩余12.5小时").click()
    page.locator("#right_container").content_frame.locator("#Iframe1").content_frame.locator("[id=\"7\"]").click()
    page.locator("#right_container").content_frame.locator("#Iframe1").content_frame.locator("[id=\"9\"]").click()
    page.locator("#right_container").content_frame.locator("#Iframe1").content_frame.locator(".layui-icon").click()
    page.locator("#right_container").content_frame.locator("#Iframe1").content_frame.get_by_text("立即提交").click()
    page.locator("#MathVerifyText").click()
    page.locator("#MathVerifyText").fill("26")
    page.get_by_text("确定").click()
    page.get_by_text("确定").click()
    page.locator("#right_container").content_frame.locator("#Iframe1").content_frame.get_by_role("cell", name=" 已约5小时  剩余11小时").click()
    page.locator("#right_container").content_frame.locator("#Iframe1").content_frame.locator("[id=\"10\"]").click()
    page.locator("#right_container").content_frame.locator("#Iframe1").content_frame.locator("[id=\"12\"]").click()
    page.locator("#right_container").content_frame.locator("#Iframe1").content_frame.locator(".layui-icon").click()
    page.locator("#right_container").content_frame.locator("#Iframe1").content_frame.get_by_text("立即提交").click()

    # 验证码
    page.locator("#img2").screenshot(path="captcha.png")
    
    page.locator("#MathVerifyText").click()
    page.locator("#MathVerifyText").fill("17")
    page.get_by_text("确定").click()
    page.get_by_text("确定").click()

    # ---------------------
    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
