import argparse
import base64
from contextlib import closing
import html
from http.client import HTTPConnection
import json
import msvcrt
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import sys
import time
from urllib.parse import urljoin, urlsplit

import httpx


EXTRACT = """(() => {
    const note = window.__INITIAL_STATE__?.note?.noteDetailMap?.[NOTE_ID]?.note;
    return {
        title: note?.title || '',
        source_url: location.href,
        type: note?.type || '',
        images: note?.imageList?.slice(0, 12) || [],
        text: document.body?.innerText || ''
    };
})()"""


def resolve_url(text):
    match = re.search(r"https?://(?:[\w-]+\.)*(?:xiaohongshu\.com|rednote\.com|xhslink\.com|douyin\.com)/[^\s<>\"'，。]+", text)
    if not match:
        raise ValueError("请输入小红书、rednote 或抖音分享链接")
    url = html.unescape(match[0]).rstrip(".,，。!?！？;；)）]】")
    if urlsplit(url).hostname in {"xhslink.com", "www.xhslink.com", "v.douyin.com"}:
        response = httpx.get(url, follow_redirects=True, timeout=30)
        response.raise_for_status()
        url = str(response.url)
    parts = urlsplit(url)
    domain = ".".join(parts.hostname.split(".")[-2:])
    platform = {"xiaohongshu.com": "xiaohongshu", "rednote.com": "rednote", "douyin.com": "douyin"}.get(domain)
    if not platform:
        raise ValueError("短链未跳转到支持的平台笔记")
    if platform != "douyin":
        note = re.fullmatch(r"/(?:discovery/item|explore)/([^/]+)/?", parts.path)
        if not note:
            raise ValueError("链接不是小红书/rednote 笔记详情页")
        token = re.search(r"(?:^|&)xsec_token=([^&]*)", parts.query)
        query = f"xsec_token={token[1]}&" if token else ""
        url = f"https://www.{domain}/explore/{note[1]}?{query}xsec_source=pc_share"
    elif re.search(r"/(?:share/)?video/", parts.path):
        raise ValueError("这是抖音视频作品，不是图文笔记")
    return platform, url


def page_error(text):
    if re.search(r"验证码|安全验证|验证后继续|captcha", text, re.I):
        raise RuntimeError("平台要求验证码或安全验证，停止抓图")
    if re.search(r"扫码登录|登录后查看|登录后浏览|请先登录|登录查看|/login", text):
        raise RuntimeError("平台要求登录或分享 token 已过期，无法免登录抓图")
    raise RuntimeError("页面未返回图文原图，请核对笔记链接及 token 是否有效")


def send_cdp(stream, message):
    payload = json.dumps(message).encode()
    size = len(payload)
    length = bytes([size | 128]) if size < 126 else b"\xfe" + struct.pack("!H", size)
    mask = os.urandom(4)
    stream.write(b"\x81" + length + mask + bytes(value ^ mask[index % 4] for index, value in enumerate(payload)))
    stream.flush()


def receive_cdp(stream):
    payload = bytearray()
    while True:
        header = stream.read(2)
        if len(header) != 2 or header[0] & 15 == 8:
            raise RuntimeError("浏览器 CDP 连接已关闭")
        size = header[1] & 127
        if size == 126:
            size = struct.unpack("!H", stream.read(2))[0]
        elif size == 127:
            size = struct.unpack("!Q", stream.read(8))[0]
        payload.extend(stream.read(size))
        if header[0] & 128:
            return json.loads(payload)


def cdp(stream, request_id, method, event=None, **params):
    send_cdp(stream, {"id": request_id, "method": method, "params": params})
    result = None
    loaded = event is None
    while result is None or not loaded:
        reply = receive_cdp(stream)
        if reply.get("id") == request_id:
            if "error" in reply:
                raise RuntimeError(reply["error"]["message"])
            result = reply["result"]
            if result.get("errorText"):
                raise RuntimeError(f"页面加载失败：{result['errorText']}")
        if reply.get("method") == event:
            loaded = True
    return result


def read_xhs(browser, profile, url):
    with socket.socket() as address:
        address.bind(("127.0.0.1", 0))
        port = address.getsockname()[1]
    process = subprocess.Popen([str(browser), "--window-position=-32000,-32000", "--window-size=1280,900",
                                f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
                                "--no-first-run", "about:blank"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 15
        target = None
        while time.monotonic() < deadline and process.poll() is None:
            try:
                with closing(HTTPConnection("127.0.0.1", port, timeout=1)) as connection:
                    connection.request("GET", "/json/list")
                    tabs = json.load(connection.getresponse())
                target = next((tab for tab in tabs if tab["type"] == "page"), None)
                if target:
                    break
            except ConnectionRefusedError:
                pass
            time.sleep(0.1)
        if target is None:
            raise RuntimeError("独立浏览器未能启动 CDP")
        endpoint = urlsplit(target["webSocketDebuggerUrl"])
        with socket.create_connection((endpoint.hostname, endpoint.port), timeout=40) as connection, connection.makefile("rwb") as stream:
            key = base64.b64encode(os.urandom(16)).decode()
            stream.write((f"GET {endpoint.path} HTTP/1.1\r\nHost: {endpoint.netloc}\r\nUpgrade: websocket\r\n"
                          f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
            stream.flush()
            if stream.readline().split()[1] != b"101":
                raise RuntimeError("浏览器拒绝 CDP WebSocket 握手")
            while stream.readline().strip():
                pass
            try:
                cdp(stream, 1, "Page.enable")
                cdp(stream, 2, "Page.navigate", event="Page.loadEventFired", url=url)
                expression = EXTRACT.replace("NOTE_ID", json.dumps(urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]))
                result = cdp(stream, 3, "Runtime.evaluate", expression=expression, returnByValue=True)
                if "exceptionDetails" in result:
                    raise RuntimeError("页面未能读取指定笔记的 __INITIAL_STATE__")
                note = result["result"]["value"]
            finally:
                send_cdp(stream, {"id": 4, "method": "Browser.close"})
                process.wait(timeout=10)
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
    if note.pop("type") == "video":
        raise RuntimeError("这是视频笔记，不是图文笔记")
    text = note.pop("text")
    if not note["images"]:
        page_error(text + note["source_url"])
    note["source_url"] = url
    return note


def read_douyin(browser, profile, url):
    result = subprocess.run([str(browser), "--headless=new", "--disable-gpu", f"--user-data-dir={profile}",
                             "--no-first-run", "--dump-dom", url], capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError(f"抖音浏览器抓图失败，返回码 {result.returncode}")
    dom = html.unescape(result.stdout.decode("utf-8")).replace(r"\/", "/").replace(r"\u002F", "/").replace(r"\u0026", "&")
    images, seen = [], set()
    for source in re.findall(r"""https?://[^\s"'<>\\]*tplv-dy-aweme-images[^\s"'<>\\]*""", dom):
        filename = urlsplit(source).path.rsplit("/", 1)[-1].split("~", 1)[0]
        if filename not in seen:
            seen.add(filename)
            images.append({"urlDefault": source})
        if len(images) == 12:
            break
    if not images:
        if re.search(r"<video\b", dom, re.I):
            raise RuntimeError("这是抖音视频作品，不是图文笔记")
        text = re.sub(r"<script\b[^>]*>.*?</script>|<style\b[^>]*>.*?</style>", "", dom, flags=re.S | re.I)
        page_error(re.sub(r"<[^>]+>", " ", text))
    title = re.search(r"<title[^>]*>(.*?)</title>", dom, re.S | re.I)
    return {"title": title[1].strip() if title else "", "source_url": url, "images": images}


def main():
    parser = argparse.ArgumentParser(description="免登录下载小红书/rednote/抖音图文原图，最多12张；单次独占独立浏览器")
    parser.add_argument("url", help="分享链接或包含链接的分享文字")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    platform, url = resolve_url(args.url)
    roots = [os.environ.get(name) for name in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]
    paths = ("Microsoft/Edge/Application/msedge.exe", "Google/Chrome/Application/chrome.exe")
    browser = next((Path(root) / path for root in roots if root for path in paths if (Path(root) / path).is_file()), None)
    if browser is None:
        raise RuntimeError("未找到本机 Edge 或 Chrome")
    profile = Path(__file__).resolve().parents[1] / "private/browser-profile"
    profile.mkdir(parents=True, exist_ok=True)
    with (profile / "fetch.lock").open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"\0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError("private/browser-profile 正被另一抓图任务占用，请等其结束") from exc
        note = read_douyin(browser, profile, url) if platform == "douyin" else read_xhs(browser, profile, url)
        args.output.mkdir(parents=True, exist_ok=True)
        files = []
        with httpx.Client(follow_redirects=True, timeout=30, headers={"Referer": note["source_url"]}) as client:
            for number, image in enumerate(note.pop("images"), 1):
                best = max(image.get("infoList", []), key=lambda row: (row.get("width", 0) * row.get("height", 0), row.get("imageScene") == "WB_DFT"), default={})
                source = image.get("urlDefault") or best.get("url")
                if not source:
                    raise RuntimeError(f"第{number}张缺少原图地址")
                response = client.get(urljoin(url, source))
                response.raise_for_status()
                mime = response.headers.get("content-type", "").split(";")[0].strip().lower()
                suffix = {"image/png": ".png", "image/webp": ".webp", "image/jpeg": ".jpg", "image/gif": ".gif", "image/avif": ".avif"}.get(mime)
                if not suffix:
                    raise RuntimeError(f"第{number}张返回非图片或未知图片格式：{mime}")
                path = args.output / f"{number:02d}{suffix}"
                path.write_bytes(response.content)
                files.append(str(path))
    print(json.dumps({"platform": platform, **note, "count": len(files), "files": files}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired, httpx.HTTPError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
