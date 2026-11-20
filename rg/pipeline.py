# -*- coding: utf-8 -*-
"""流程编排：一个任务型 Agent 的执行链

解析 → 列语义识别 → 规则校验 → 语义判断 → 生成批注 → 写回文件
每一步记录耗时与产出，便于回放和定位错误发生在哪一层。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import docx_io, judge as judge_mod, layout as layout_mod, rules as rules_mod


@dataclass
class Step:
    name: str
    tool: str
    ms: int
    detail: str

    def as_dict(self):
        return {"name": self.name, "tool": self.tool, "ms": self.ms, "detail": self.detail}


@dataclass
class Result:
    ok: bool
    mode: str                                  # rule+model / rule+heuristic / rule_only
    issues: List[rules_mod.Issue] = field(default_factory=list)
    trace: List[Step] = field(default_factory=list)
    layouts: Dict[int, layout_mod.Layout] = field(default_factory=dict)
    tables_meta: List[dict] = field(default_factory=list)
    needs_confirm: List[dict] = field(default_factory=list)
    output_path: str = ""
    comments_written: int = 0
    summary: dict = field(default_factory=dict)
    error: str = ""

    def counts(self) -> dict:
        c = {"high": 0, "medium": 0, "low": 0}
        for i in self.issues:
            c[i.severity] = c.get(i.severity, 0) + 1
        return c

    def as_dict(self):
        return {
            "ok": self.ok, "mode": self.mode, "error": self.error,
            "issues": [i.as_dict() for i in self.issues],
            "counts": self.counts(),
            "trace": [s.as_dict() for s in self.trace],
            "tables": self.tables_meta,
            "needs_confirm": self.needs_confirm,
            "output": self.output_path,
            "comments_written": self.comments_written,
            "summary": self.summary,
        }


SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _dedupe(issues: List[rules_mod.Issue]) -> List[rules_mod.Issue]:
    seen = set()
    out = []
    for i in issues:
        k = (i.table, i.row, i.col, i.rule_id)
        if k in seen:
            continue
        seen.add(k)
        out.append(i)
    out.sort(key=lambda x: (SEVERITY_ORDER.get(x.severity, 9), x.table, x.row, x.col))
    return out


class _Timer:
    def __init__(self):
        self.t0 = time.perf_counter()

    def ms(self) -> int:
        return int((time.perf_counter() - self.t0) * 1000)


def run(src_path: str, outdir: str, manual_map: Optional[Dict[int, Dict[int, str]]] = None,
        cfg: Optional[judge_mod.LLMConfig] = None, write_file: bool = True) -> Result:
    """
    manual_map: {table_index: {col_index: field_key or ""}}
    """
    manual_map = manual_map or {}
    cfg = cfg or judge_mod.LLMConfig.from_env()
    res = Result(ok=False, mode="rule_only")

    # 1) 解析
    t = _Timer()
    try:
        tables = docx_io.read_tables(src_path)
        doc_texts = docx_io.read_doc_text(src_path)
    except Exception as exc:                                   # noqa: BLE001
        res.error = "无法读取文件：%s" % exc
        res.trace.append(Step("解析文档", "docx_io.read_tables", t.ms(), "失败"))
        return res
    if not tables:
        res.error = "这份文档里没有找到表格。本工具只处理含表格的记录文档。"
        res.trace.append(Step("解析文档", "docx_io.read_tables", t.ms(), "0 张表"))
        return res
    res.tables_meta = [{"index": td.index, "rows": td.n_data_rows,
                        "headers": td.headers,
                        "sample_rows": td.data_rows()[:3]} for td in tables]
    res.trace.append(Step("解析文档", "docx_io.read_tables", t.ms(),
                          "%d 张表 / %d 行数据 / 正文 %d 段"
                          % (len(tables), sum(td.n_data_rows for td in tables), len(doc_texts))))

    # 2) 列语义识别
    t = _Timer()
    need_confirm_payload = []
    for td in tables:
        lay = layout_mod.analyze(td.headers, td.data_rows())
        if manual_map.get(td.index):
            lay = layout_mod.apply_manual(lay, manual_map[td.index])
        res.layouts[td.index] = lay
        missing = lay.required_missing()
        if lay.needs_confirm or missing:
            # 认不出的列，先让模型看一眼：它猜是什么、凭什么
            guesses = {}
            if cfg.enabled:
                unknown = [c.col for c in lay.columns if c.confidence < 0.7 or not c.field]
                if unknown:
                    g_timer = _Timer()
                    guesses = judge_mod.guess_columns(td.headers, td.data_rows()[:4], cfg)
                    res.trace.append(Step("猜测列含义", "judge.guess_columns", g_timer.ms(),
                                          "模型对 %d 列给出判断" % len(guesses)))
            need_confirm_payload.append({
                "table": td.index,
                "headers": td.headers,
                "columns": [{"col": c.col, "header": c.raw_header, "field": c.field,
                             "label": c.label, "confidence": c.confidence, "how": c.how,
                             "guess": guesses.get(c.col, {})}
                            for c in lay.columns],
                "missing_required": missing,
            })
    res.needs_confirm = need_confirm_payload
    res.trace.append(Step("识别列含义", "layout.analyze", t.ms(),
                          "；".join("表%d 识别 %d/%d 列" % (td.index, len(res.layouts[td.index].mapping()),
                                                          len(td.headers)) for td in tables)))

    # 2.5) 领域门槛：先确认这到底是不是一份检测记录
    #      没有「结果 / 结论 / Ct」中的任何一项，就不是本工具该处理的文件。
    #      把不相干的表当检测记录来判，比明确不判更糟。
    if not manual_map:
        key_fields = {"result", "conclusion", "ct"}
        hit = set()
        for _lay in res.layouts.values():
            for _c in _lay.columns:
                if _c.field in key_fields and _c.confidence >= 0.7:
                    hit.add(_c.field)
        if not hit:
            res.mode = "domain_mismatch"
            res.ok = False
            res.needs_confirm = []
            res.error = ("这份文件看起来不是检测记录：没有识别到「检测结果」「结论」「Ct 值」"
                         "中的任何一项。本工具只校验检测记录，不做通用表格校验——"
                         "因为把不相干的表当成检测记录来判，比不判更糟。")
            res.summary = {
                "识别到的表头": [[td.index, h] for td in tables for h in td.headers],
                "说明": ("如果这份文件确实是检测记录，请确认表头里是否有结果 / 结论 / Ct 值，"
                         "或换一份带这些字段的文件。"),
            }
            res.trace.append(Step("领域判断", "pipeline.domain_gate", 0,
                                  "未识别到结果/结论/Ct 字段，拒绝校验"))
            return res

    if need_confirm_payload and not manual_map:
        res.ok = True
        res.mode = "need_confirm"
        res.summary = {"说明": "有列无法确定含义，需要人工确认后继续"}
        return res

    # 3) 规则校验
    t = _Timer()
    rule_issues = rules_mod.run(tables, res.layouts)
    res.trace.append(Step("规则校验", "rules.run", t.ms(),
                          "执行 8 类规则，发现 %d 处问题" % len(rule_issues)))

    # 4) 语义判断
    t = _Timer()
    try:
        model_issues = judge_mod.judge(doc_texts, tables, rule_issues, cfg,
                                      table_index=tables[0].index)
    except Exception as exc:                                   # noqa: BLE001
        print("[WARN] 语义层异常：%s" % exc)
        model_issues = []
    if model_issues:
        src = model_issues[0].source
        res.mode = "rule+model" if src == "model" else "rule+heuristic"
    else:
        res.mode = "rule+model" if cfg.enabled else "rule_only"
    res.trace.append(Step("语义判断", "judge.judge", t.ms(),
                          "%d 处（来源：%s）"
                          % (len(model_issues),
                             (model_issues[0].source if model_issues else "无发现"))))

    res.issues = _dedupe(rule_issues + model_issues)
    res.summary = judge_mod.summarize(tables, res.issues)

    # 4.5) 给每条问题补业务解释（模型看得懂上下文，规则只看单点）
    if cfg.enabled and res.issues:
        t = _Timer()
        judge_mod.explain_issues(res.issues, tables, res.layouts, cfg)
        n = sum(1 for i in res.issues if i.rationale)
        res.trace.append(Step("生成分析", "judge.explain_issues", t.ms(),
                              "%d 条问题附上业务解释" % n))

    # 5) 写批注
    if write_file:
        t = _Timer()
        dst = docx_io.out_path(src_path, outdir)
        marks = []
        for i in res.issues:
            if i.row <= 0 and i.col == 0:
                continue
            marks.append({"table": i.table, "row": i.row, "col": i.col,
                          "text": i.comment_text()})
        try:
            n = docx_io.write_comments(src_path, dst, marks)
            res.output_path = dst
            res.comments_written = n
            res.trace.append(Step("写入批注", "docx_io.write_comments", t.ms(),
                                  "%d/%d 条批注写入成功" % (n, len(marks))))
        except Exception as exc:                               # noqa: BLE001
            res.trace.append(Step("写入批注", "docx_io.write_comments", t.ms(), "失败：%s" % exc))

    res.ok = True
    return res
