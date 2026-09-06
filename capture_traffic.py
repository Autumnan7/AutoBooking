import datetime
import pathlib

from playwright.sync_api import sync_playwright

# 保存到文件
OUT = pathlib.Path(__file__).with_name(
    "network_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + ".log"
)
_fh = open(OUT, "w", encoding="utf-8", errors="replace")


def log(*parts):
    line = " ".join(str(x) for x in parts)
    _fh.write(line + "\n")
    _fh.flush()  # 每行立即落盘，中断也不丢数据
    print(line)


with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)

    page = browser.new_page()

    def request_handler(request):
        log(">>", request.method, request.resource_type, request.url)
        if request.resource_type in ("xhr", "fetch") and request.post_data:
            log("   POST_DATA:", request.post_data[:2000])

    def response_handler(response):
        log("<<", response.status, response.request.resource_type, response.url)
        # 同步 API 的事件回调里不宜再调用 Playwright 阻塞方法，
        # 这里只登记对象，响应体等采集结束后统一读取。
        if response.request.resource_type in ("xhr", "fetch"):
            captured.append(response)

    captured = []  # 存放 XHR / Fetch 响应，稍后统一读取响应体

    page.on("request", request_handler)
    page.on("response", response_handler)

    URL = ("https://webvpn.uestc.edu.cn/https/77726476706e69737468656265737421"
       "f4ee0f8922237c53300d8db9d6562d/customer/index/index.html")
    page.goto(URL)

    log("开始采集 100 秒，请在浏览器窗口中手动完成操作...")
    second = 100
    page.wait_for_timeout(second * 1000)

    log(f"\n===== XHR / Fetch 响应体（共 {len(captured)} 条）=====")
    for i, resp in enumerate(captured):
        log(f"\n========== RESPONSE #{i} ==========")
        log("STATUS:", resp.status)
        log("URL:", resp.url)
        log("METHOD:", resp.request.method)
        log("POST_DATA:", (resp.request.post_data or "")[:2000])
        try:
            log("BODY:", resp.text()[:2000])
        except Exception as exc:
            log("BODY: <读取失败>", type(exc).__name__, exc)

    browser.close()
    _fh.close()
    print(f"\n日志已写入: {OUT}")