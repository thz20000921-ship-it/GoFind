"""Call the existing local image engine; no provider keys or image generation SDK."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import mimetypes
import os
from pathlib import Path
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import uuid
import zipfile


class EngineError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".tmp")
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    pending.replace(path)


class EngineClient:
    def __init__(self, base_url: str, token: str, timeout: float = 180):
        parts = urlsplit(base_url)
        if (parts.scheme != "http" or parts.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parts.username or parts.password or parts.query or parts.fragment
                or parts.path not in {"", "/"}):
            raise ValueError("base_url必须是本机HTTP地址，例如http://127.0.0.1:8787。")
        if not token.strip():
            raise ValueError("本机Agent Token为空。")
        self.base_url = base_url.rstrip("/")
        self._token = token.strip()
        self.timeout = timeout
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    @classmethod
    def from_config(cls, config: dict):
        data_dir = Path(config.get("data_dir") or os.environ.get("ILAB_CONJURE_DATA_DIR")
                        or Path.home() / "复刻图文-Data").expanduser()
        token = os.environ.get("IMAGE_ENGINE_TOKEN")
        if not token:
            token_path = data_dir / "webui-outputs/source-data/company-workflow/agent-token.txt"
            token = token_path.read_text(encoding="utf-8").strip()
        return cls(config.get("base_url") or os.environ.get("IMAGE_ENGINE_URL")
                   or "http://127.0.0.1:8787", token, config.get("timeout_seconds", 180))

    def request(self, method: str, path: str, *, payload=None, data=None, content_type=None):
        parts = urlsplit(path)
        if parts.scheme or parts.netloc or not path.startswith("/api/"):
            raise ValueError("只接受引擎返回的本机/api/路径。")
        headers = {"Authorization": "Bearer " + self._token}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            content_type = "application/json"
        if content_type:
            headers["Content-Type"] = content_type
        request = Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                body = response.read()
                if "application/json" in response.headers.get("Content-Type", ""):
                    return json.loads(body)
                return body
        except HTTPError as exc:
            message = exc.read().decode("utf-8", "replace").replace(self._token, "[token]")
            raise EngineError(f"HTTP {exc.code}: {message}", exc.code) from None
        except (URLError, TimeoutError, socket.timeout, ConnectionError) as exc:
            message = str(exc).replace(self._token, "[token]")
            raise EngineError(f"接口未取得确定结果：{message}；提交后请沿用任务ID查询，不自动重发。") from None

    def status(self):
        return self.request("GET", "/api/engine/status")

    def upload(self, paths: list[Path], output: Path):
        boundary = "gofind-" + uuid.uuid4().hex
        parts = []
        for number, path in enumerate(paths, 1):
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            filename = f"asset-{number}{path.suffix.lower()}"
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="images"; filename="{filename}"'
                f"\r\nContent-Type: {mime}\r\n\r\n".encode("ascii") + path.read_bytes() + b"\r\n"
            )
        body = b"".join(parts) + f"--{boundary}--\r\n".encode("ascii")
        result = self.request("POST", "/api/engine/assets", data=body,
                              content_type=f"multipart/form-data; boundary={boundary}")
        save_json(output / "assets-response.json", result)
        assets = result["assets"]
        if len(assets) != len(paths):
            raise EngineError("上传返回的素材数量不一致；保留原始返回，不猜测ID映射。")
        mapping = [{"path": str(path.resolve()), "asset_id": row["id"]}
                   for path, row in zip(paths, assets)]
        save_json(output / "assets.json", mapping)
        return mapping

    def import_link(self, url: str, output: Path):
        result = self.request("POST", "/api/company-workflow/link", payload={"text": url})
        save_json(output / "import-response.json", result)
        paths = []
        for number, row in enumerate(result["images"], 1):
            name = Path(row["name"].replace("\\", "/")).name
            path = output / "images" / f"{number:02d}-{name}"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(self.request("GET", row["url"]))
            paths.append(path)
        if not paths:
            raise EngineError("链接导入没有返回图片；已保留实际返回。")
        mapping = self.upload(paths, output)
        return {"source": result["source_url"], "title": result["title"],
                "total": result["total"], "truncated": result["truncated"], "assets": mapping}

    def prepare(self, job: dict, output: Path):
        # The exact reviewed payload remains local; these two endpoints never enqueue.
        payload = {**job, "authorization": {"approved": False, "max_image_requests": 0}}
        save_json(output / "job.json", payload)
        status = self.status()
        save_json(output / "status.json", status)
        plan = self.request("POST", "/api/engine/plan", payload=payload)
        save_json(output / "plan.json", plan)
        estimate = self.request("POST", "/api/engine/estimate", payload=payload)
        save_json(output / "estimate.json", estimate)
        return {"task_id": job["task_id"], "estimate": estimate,
                "planner": plan["planner"], "paid_calls": plan["paid_calls"]}

    def analyze(self, job: dict, output: Path, analysis_id: str, approved_requests: int):
        if approved_requests != 1:
            raise ValueError("单独视觉分析需获准1次分析请求，图片额度不代替分析授权。")
        payload = {"analysis_id": analysis_id, "job": job,
                   "approved": True, "max_analysis_requests": 1}
        save_json(output / "analysis-request.json", payload)
        result = self.request("POST", "/api/engine/analyze", payload=payload)
        save_json(output / "analysis-response.json", result)
        # Only visual directions return from the engine. Caller text, facts and mapping stay intact.
        analyzed = result["analysis"]
        visuals = {p["page_number"]: p.get("visual") for p in analyzed.get("pages", [])}
        prepared = {**job, "pages": [
            {**page, **({"visual": visuals[page["page_number"]]}
                       if isinstance(visuals.get(page["page_number"]), str)
                       and visuals[page["page_number"]].strip() else {})}
            for page in job["pages"]
        ], "authorization": {"approved": False, "max_image_requests": 0}}
        save_json(output / "analyzed-job.json", prepared)
        return result

    def submit(self, job: dict, output: Path, approved_requests: int):
        if approved_requests <= 0:
            raise ValueError("付费提交需明确给出获准的--approve-image-requests，不能从Job文件推定授权。")
        payload = {**job, "authorization": {
            **job.get("authorization", {}), "approved": True,
            "max_image_requests": approved_requests,
        }}
        estimate = self.request("POST", "/api/engine/estimate", payload=payload)
        if approved_requests < estimate["initial_image_requests"]:
            raise ValueError("获准请求数少于输出页数，未提交。")
        # Save before POST; timeout/interruption must be recoverable with this same ID.
        save_json(output / "submitted-job.json", payload)
        save_json(output / "submission.json", {"task_id": job["task_id"], "status": "submitting"})
        try:
            result = self.request("POST", "/api/engine/jobs", payload=payload)
        except EngineError as exc:
            save_json(output / "submission.json", {
                "task_id": job["task_id"],
                "status": "rejected" if exc.status and 400 <= exc.status < 500 else "unknown",
                "http_status": exc.status, "error": str(exc),
            })
            raise
        save_json(output / "submission.json", result)
        return result

    def revise(self, task_id: str, revision: dict, output: Path, approved_requests: int):
        if approved_requests <= 0 or approved_requests < len(revision["pages"]):
            raise ValueError("指定页修订需明确获准足够的图片请求次数。")
        payload = {**revision, "authorization": {
            **revision.get("authorization", {}), "approved": True,
            "max_image_requests": approved_requests,
        }}
        save_json(output / "revision-request.json", payload)
        save_json(output / "revision-state.json", {
            "task_id": task_id, "revision_id": revision["revision_id"], "status": "submitting"})
        try:
            result = self.request("POST", f"/api/engine/jobs/{quote(task_id, safe='')}/revisions",
                                  payload=payload)
        except EngineError as exc:
            save_json(output / "revision-state.json", {
                "task_id": task_id, "revision_id": revision["revision_id"],
                "status": "rejected" if exc.status and 400 <= exc.status < 500 else "unknown",
                "http_status": exc.status, "error": str(exc),
            })
            raise
        save_json(output / "revision-state.json", result)
        return result

    def query(self, task_id: str, output: Path, wait_seconds: float = 0):
        deadline = time.monotonic() + wait_seconds
        while True:
            job = self.request("GET", f"/api/engine/jobs/{quote(task_id, safe='')}")
            save_json(output / "progress.json", job)
            if job["status"] in {"completed", "partial_failed", "failed", "cancelled"}:
                return job
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return job
            time.sleep(min(2, remaining))

    def download(self, task_id: str, output: Path):
        job = self.query(task_id, output)
        downloaded = []
        for row in job["pages"]:
            if not row.get("download_url"):
                continue
            data = self.request("GET", row["download_url"])
            if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                raise EngineError("下载返回不是PNG；未保存为成品。")
            digest = hashlib.sha256(data).hexdigest()
            if row.get("sha256") and digest != row["sha256"]:
                raise EngineError("图片哈希与任务返回不一致；请重新查询当前版本。")
            filename = f"{row['page_number']:02d}-v{row['version']}.png"
            (output / filename).write_bytes(data)
            downloaded.append(filename)
        complete = job["completed_pages"] == job["total_pages"] and job["total_pages"] > 0
        if complete:
            data = self.request("GET", job["download_url"])
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if archive.testzip():
                    raise EngineError("引擎ZIP校验失败。")
            (output / "results.zip").write_bytes(data)
        result = {"task_id": task_id, "status": job["status"], "images": downloaded,
                  "archive_downloaded": complete, "content_review": "pending"}
        save_json(output / "downloads.json", result)
        return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="本机私有JSON；只需base_url/data_dir，不保存Key")
    parser.add_argument("--output", type=Path, help="本次任务的私有输出目录")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    link = commands.add_parser("import-link")
    link.add_argument("url")
    upload = commands.add_parser("upload")
    upload.add_argument("files", type=Path, nargs="+")
    for name in ("prepare", "submit"):
        command = commands.add_parser(name)
        command.add_argument("--job", type=Path, required=True)
        if name == "submit":
            command.add_argument("--approve-image-requests", type=int, required=True)
    analyze = commands.add_parser("analyze")
    analyze.add_argument("--job", type=Path, required=True)
    analyze.add_argument("--analysis-id", required=True)
    analyze.add_argument("--approve-analysis-requests", type=int, required=True)
    revise = commands.add_parser("revise")
    revise.add_argument("task_id")
    revise.add_argument("--revision", type=Path, required=True)
    revise.add_argument("--approve-image-requests", type=int, required=True)
    query = commands.add_parser("query")
    query.add_argument("task_id")
    query.add_argument("--wait-seconds", type=float, default=0)
    download = commands.add_parser("download")
    download.add_argument("task_id")
    args = parser.parse_args(argv)
    if args.command != "status" and args.output is None:
        parser.error("请用--output指定当前私有任务目录。")
    try:
        client = EngineClient.from_config(read_json(args.config) if args.config else {})
        if args.command == "status":
            result = client.status()
        elif args.command == "import-link":
            result = client.import_link(args.url, args.output)
        elif args.command == "upload":
            result = client.upload(args.files, args.output)
        elif args.command == "prepare":
            result = client.prepare(read_json(args.job), args.output)
        elif args.command == "submit":
            result = client.submit(read_json(args.job), args.output, args.approve_image_requests)
        elif args.command == "analyze":
            result = client.analyze(read_json(args.job), args.output, args.analysis_id,
                                    args.approve_analysis_requests)
        elif args.command == "revise":
            result = client.revise(args.task_id, read_json(args.revision),
                                   args.output, args.approve_image_requests)
        elif args.command == "query":
            result = client.query(args.task_id, args.output, args.wait_seconds)
        else:
            result = client.download(args.task_id, args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (EngineError, OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        error = {"command": args.command, "http_status": getattr(exc, "status", None), "error": str(exc)}
        if args.output:
            save_json(args.output / f"{args.command}-error.json", error)
        print(json.dumps(error, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
