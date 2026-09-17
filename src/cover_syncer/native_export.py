"""Small JSON bridge used by the native recorder; no Qt or desktop automation."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from .runtime import configure_runtime

configure_runtime()

from .media import export_synced_video
from .workflow import analyze_media


def export_job(job: dict) -> dict:
    if job.get("version") != 1:
        raise ValueError("Unsupported export job version.")
    offset = float(job["offset_ms"])
    if job.get("auto_align"):
        try:
            alignment = analyze_media(job["video"], job["audio"])
        except Exception as exc:
            return {"success": False, "needs_manual": True, "offset_ms": offset,
                    "error": f"自动对齐失败，素材已保留，请手动设置偏移：{exc}"}
        offset = alignment.offset_ms
        if alignment.reliability != "high":
            return {"success": False, "needs_manual": True, "offset_ms": offset,
                    "error": "自动对齐可靠性不足，素材已保留；请确认或修改候选偏移后导出。"}
    result = export_synced_video(job["video"], job["audio"], job["output"], offset, overwrite=False)
    return {"success": True, "output": str(result.output_path), "offset_ms": offset}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        if len(argv) != 1:
            raise ValueError("Expected an export job JSON file.")
        configure_runtime()
        job = json.loads(Path(argv[0]).read_text(encoding="utf-8-sig"))
        result = export_job(job)
        code = 0
    except Exception as exc:
        result = {"success": False, "error": str(exc)[-2000:]}
        code = 1
    # ASCII JSON survives Windows code pages and JUCE UTF-8 decoding unchanged.
    print(json.dumps(result, ensure_ascii=True), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
