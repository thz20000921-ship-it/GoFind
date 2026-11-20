"""Check that job.json matches the handoff.md contract. No network, no paid calls."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PAGE_KEYS = ("page_number", "role", "generate", "exact_text", "reference")
GEN_ROLES = {"封面", "首屏", "内容", "cover", "content"}


def read_pages(text: str) -> list[dict]:
    """Collect per-page blocks: '## 页 N · ..' sections with '- key: value' lines."""
    pages, current = [], None
    for raw in text.splitlines():
        line = raw.rstrip()
        match = re.match(r"^##\s*页\s*(\d+)\s*[·・.]\s*(.+)$", line)
        if match:
            current = {"page_number": int(match.group(1)), "heading": match.group(2).strip(), "fields": {}}
            pages.append(current)
            continue
        if current is None:
            continue
        if line.startswith("## "):
            current = None
            continue
        field = re.match(r"^-\s*([a-z_]+)(?:\s*[（(][^）)]*[）)])?\s*[:：]\s*(.*)$", line)
        if field:
            current["fields"][field.group(1)] = field.group(2).strip()
    return pages


def fenced_text(text: str, after: int) -> tuple[str, int]:
    """First fenced block after a character offset."""
    start = text.find("```", after)
    if start < 0:
        return "", after
    body_start = text.find("\n", start)
    end = text.find("```", body_start + 1)
    if body_start < 0 or end < 0:
        return "", after
    return text[body_start + 1:end].strip(), end


def exact_text_of(handoff: str, page_number: int) -> str:
    pattern = re.compile(r"^##\s*页\s*" + str(page_number) + r"\s*[·・.]", re.MULTILINE)
    match = pattern.search(handoff)
    if not match:
        return ""
    return fenced_text(handoff, match.end())[0]


def norm(value: str) -> str:
    return re.sub(r"\s+", "", value)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff", type=Path, required=True)
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--assets", type=Path, help="assets.json from upload (optional)")
    args = parser.parse_args(argv)

    errors: list[str] = []
    handoff = args.handoff.read_text(encoding="utf-8-sig")
    job = json.loads(args.job.read_text(encoding="utf-8-sig"))
    pages = read_pages(handoff)
    if not pages:
        errors.append("handoff.md 里没有解析到任何 '## 页 N · 角色' 段落")

    for key in ("业务", "note_id", "主需求", "笔记标题"):
        if f"| {key} |" not in handoff:
            errors.append(f"handoff.md 缺基本信息字段：{key}")

    generated, screenshots = [], []
    for page in pages:
        missing = [k for k in PAGE_KEYS if k not in page["fields"]]
        if missing:
            errors.append(f"handoff 第 {page['page_number']} 页缺字段：{', '.join(missing)}")
        if not exact_text_of(handoff, page["page_number"]):
            errors.append(f"handoff 第 {page['page_number']} 页缺 exact_text 围栏内容")
        flag = page["fields"].get("generate", "").strip().lower()
        role = page["fields"].get("role", "")
        if flag in {"no", "false", "否"} or (not flag and "截图" in role):
            screenshots.append(page)
        else:
            generated.append(page)

    job_pages = {p["page_number"]: p for p in job.get("pages", [])}
    if len(job_pages) != job.get("page_count"):
        errors.append(f"job.page_count={job.get('page_count')} 与 pages 数量 {len(job_pages)} 不符")

    for page in screenshots:
        if page["page_number"] in job_pages:
            errors.append(f"真实截图页 {page['page_number']} 被写进了 job.pages，不得交给生成模型")

    gen_numbers = sorted(p["page_number"] for p in generated)
    if gen_numbers != sorted(job_pages):
        errors.append(f"job.pages 页码 {sorted(job_pages)} 与 handoff 生成页 {gen_numbers} 不一致")

    if job.get("mode") != "inspired":
        errors.append(f"job.mode 应为 inspired，实际 {job.get('mode')!r}")
    references = {r.get("asset_id") for r in job.get("references", []) if r.get("purpose") == "reference"}
    if job.get("reference_policy") == "layout" and not references:
        errors.append("reference_policy=layout 但没有 purpose=reference 的参考图，属于伪造 layout")
    if references and job.get("reference_policy") != "layout":
        errors.append("有参考图但 reference_policy 不是 layout")
    if not references:
        errors.append("没有参考图，不应提交")

    uploaded = None
    if args.assets:
        uploaded = {row["asset_id"] for row in json.loads(args.assets.read_text(encoding="utf-8-sig"))}

    for number, page in sorted(job_pages.items()):
        ids = page.get("asset_ids") or []
        if not ids:
            errors.append(f"job 第 {number} 页 asset_ids 为空")
        for asset_id in ids:
            if not re.fullmatch(r"[0-9a-f]{64}", asset_id):
                errors.append(f"job 第 {number} 页 asset_id 不是真实哈希：{asset_id!r}")
            elif uploaded is not None and asset_id not in uploaded:
                errors.append(f"job 第 {number} 页 asset_id 不在上传回执里：{asset_id[:12]}…")
            elif asset_id not in references:
                errors.append(f"job 第 {number} 页 asset_id 没有对应的 reference 条目：{asset_id[:12]}…")
        expected = norm(exact_text_of(handoff, number))
        actual = norm((page.get("title") or "") + (page.get("copy") or ""))
        if expected and actual != expected:
            errors.append(f"job 第 {number} 页 title+copy 与 handoff exact_text 不一致")

    if errors:
        for item in errors:
            print(f"FAIL {item}")
        return 1
    print(f"OK {args.handoff.name} ↔ {args.job.name}：{len(generated)} 个生成页、{len(screenshots)} 个截图页、"
          f"{len(references)} 张参考、reference_policy=layout")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
