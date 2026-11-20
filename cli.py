# -*- coding: utf-8 -*-
"""命令行入口

用法：
    python cli.py samples/sample_errors.docx
    python cli.py samples/sample_errors.docx --json
    python cli.py samples/sample_errors.docx --outdir out
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rg import pipeline                                    # noqa: E402
from rg.judge import LLMConfig                             # noqa: E402


SEV_LABEL = {"high": "高", "medium": "中", "low": "低"}


def main():
    ap = argparse.ArgumentParser(description="记录校验 Agent（命令行）")
    ap.add_argument("file", help="待校验的 .docx 文件")
    ap.add_argument("--outdir", default="out", help="输出目录（默认 out/）")
    ap.add_argument("--json", action="store_true", help="输出完整 JSON")
    ap.add_argument("--no-write", action="store_true", help="只校验，不生成批注文件")
    args = ap.parse_args()

    cfg = LLMConfig.from_env()
    res = pipeline.run(args.file, args.outdir, cfg=cfg, write_file=not args.no_write)

    if args.json:
        print(json.dumps(res.as_dict(), ensure_ascii=False, indent=2))
        return 0 if res.ok else 1

    print("=" * 62)
    print("记录校验 Agent")
    print("=" * 62)
    if not res.ok:
        print("失败：%s" % res.error)
        return 1

    print("模式：%s%s" % (res.mode, "" if cfg.enabled else "（未配置模型，语义层使用本地启发式）"))
    print()
    for s in res.trace:
        print("  · %-6s %5dms  %s" % (s.name, s.ms, s.detail))
    print()

    if res.needs_confirm:
        print("⚠ 有列无法确定含义，需要人工确认：")
        for t in res.needs_confirm:
            print("  表 %d 表头：%s" % (t["table"], " | ".join(t["headers"])))
            for c in t["columns"]:
                if c["confidence"] < 0.7:
                    print("    第 %d 列「%s」→ 未识别（置信度 %.2f）"
                          % (c["col"] + 1, c["header"], c["confidence"]))
        print()
        return 0

    counts = res.counts()
    print("发现问题 %d 处：高 %d / 中 %d / 低 %d"
          % (len(res.issues), counts.get("high", 0), counts.get("medium", 0), counts.get("low", 0)))
    print("-" * 62)
    for i, iss in enumerate(res.issues, 1):
        print("%2d. [%s] %s %s  样本 %s（第 %d 行）"
              % (i, SEV_LABEL.get(iss.severity, "?"), iss.rule_id, iss.title,
                 iss.sample or "-", iss.row + 1))
        print("    %s" % iss.detail.replace("\n", " "))
        if iss.suggestion:
            print("    建议：%s" % iss.suggestion)
        print("    来源：%s  置信度 %.2f" % (iss.source, iss.confidence))
    print("-" * 62)
    if res.output_path:
        print("已生成带批注文件：%s" % res.output_path)
        print("批注写入 %d 条" % res.comments_written)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
