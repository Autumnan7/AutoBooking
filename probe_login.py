"""临时探针：诊断登录页为什么没有填入学号密码。
用法：python login_probe.py
"""
from playwright.sync_api import sync_playwright

URL = ("https://webvpn.uestc.edu.cn/https/77726476706e69737468656265737421"
       "f9f3408f69256d436a0bc7a99c406d3652/authserver/login"
       "?service=https%3A%2F%2Fwebvpn.uestc.edu.cn%2Flogin%3Fcas_login%3Dtrue")

JS_INFO = """e => ({
    tag: e.tagName,
    type: e.type || null,
    id: e.id || null,
    name: e.getAttribute('name'),
    ph: e.getAttribute('placeholder'),
    aria: e.getAttribute('aria-label'),
    cls: (e.className || '').toString().slice(0, 50),
    visible: !!(e.offsetWidth || e.offsetHeight),
    disabled: e.disabled
})"""

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page()
    page.set_default_timeout(10000)

    # 注意：这里用 domcontentloaded，不用 networkidle
    page.goto(URL, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)

    print(f"\n=== 落地 URL ===\n{page.url}")
    print(f"\n=== 标题 ===\n{page.title()}")

    page.screenshot(path="login_page.png", full_page=True)
    print("\n[已保存截图] login_page.png")

    print("\n=== 所有 input（主 frame）===")
    for i, el in enumerate(page.locator("input").all()):
        try:
            print(f"  #{i} {el.evaluate(JS_INFO)}")
        except Exception as exc:
            print(f"  #{i} <读取失败: {type(exc).__name__}>")

    print("\n=== 子 frame ===")
    for i, fr in enumerate(page.main_frame.child_frames):
        print(f"  [{i}] name={fr.name!r} url={fr.url[:100]}")

    print("\n=== 按 placeholder 找学号框 ===")
    for ph in ["请输入学号/工号", "学号", "工号", "用户名"]:
        loc = page.locator(f'input[placeholder*="{ph}"]')
        print(f"  placeholder 含 {ph!r}: 数量={loc.count()}")

    print("\n=== get_by_role('textbox', name=...) 匹配情况 ===")
    for nm in ["请输入学号/工号", "学号", "请输入密码", "密码"]:
        loc = page.get_by_role("textbox", name=nm)
        print(f"  name={nm!r}: 数量={loc.count()}")

    print("\n=== 页面可见文本（前 500 字）===")
    try:
        print(page.inner_text("body")[:500])
    except Exception as exc:
        print(f"<获取失败: {exc}>")

    input("\n按回车关闭浏览器...")
    browser.close()
