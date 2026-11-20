# -*- coding: utf-8 -*-
"""规则引擎：确定性校验，不调用模型

设计原则：能用代码算的绝不给模型。模型会算错、算错了也没人发现；
规则算错了能复现、能定位、能修。
"""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .layout import Layout, POS_WORDS, NEG_WORDS, norm

CT_MAX_POSITIVE = 40.0      # 阳性判定 Ct 上限，超过判可疑
CT_MIN_NEGATIVE = 35.0      # 阴性但 Ct 小于此值 → 可疑
CT_HARD_MAX = 45.0
AGE_MAX = 120

DATE_PAT = re.compile(r"^(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})日?$")


@dataclass
class Issue:
    rule_id: str
    severity: str            # high / medium / low
    title: str
    detail: str
    table: int
    row: int                 # 0-based，含表头
    col: int
    field: str
    sample: str = ""
    suggestion: str = ""
    source: str = "rule"
    confidence: float = 1.0
    rationale: str = ""      # 模型结合上下文给出的业务解释（是解释，不是结论）

    def as_dict(self) -> dict:
        return {
            "rule": self.rule_id, "severity": self.severity, "title": self.title,
            "detail": self.detail, "table": self.table, "row": self.row, "col": self.col,
            "field": self.field, "sample": self.sample, "suggestion": self.suggestion,
            "source": self.source, "confidence": self.confidence,
            "rationale": self.rationale,
        }

    def comment_text(self) -> str:
        tag = {"high": "【高】", "medium": "【中】", "low": "【低】"}.get(self.severity, "")
        s = "%s%s %s" % (tag, self.rule_id, self.title)
        if self.detail:
            s += "\n" + self.detail
        if self.suggestion:
            s += "\n建议：" + self.suggestion
        if self.rationale:
            s += "\n分析：" + self.rationale
        return s


def _parse_result(v: str) -> Optional[str]:
    n = norm(v)
    if not n:
        return None
    if n in POS_WORDS:
        return "pos"
    if n in NEG_WORDS:
        return "neg"
    return "other"


def _parse_ct(v: str) -> Optional[float]:
    n = (v or "").strip()
    if not n:
        return None
    try:
        return float(n)
    except ValueError:
        return None


def _two(x: float) -> str:
    return ("%.2f" % x).rstrip("0").rstrip(".")


def run(tables, layouts: Dict[int, Layout]) -> List[Issue]:
    """tables: List[TableData]；layouts: {table_index: Layout}"""
    issues: List[Issue] = []
    for td in tables:
        lay = layouts.get(td.index)
        if not lay:
            continue
        m = lay.mapping()
        c_sid = m.get("sample_id")
        c_item = m.get("item")
        c_lot = m.get("lot")
        c_res = m.get("result")
        c_ct = m.get("ct")
        c_con = m.get("conclusion")
        c_age = m.get("age")
        c_date = m.get("date")

        rows = td.rows
        hdr = td.header_row

        def g(r, c):
            return td.cell(r, c) if c is not None else ""

        def sid(r):
            return g(r, c_sid).strip()

        # ---- R01 必填缺失 ----
        for r in range(hdr + 1, len(rows)):
            for name, c in (("样本编号", c_sid), ("检测项目", c_item), ("检测结果", c_res)):
                if c is None:
                    continue
                if not g(r, c).strip():
                    issues.append(Issue(
                        "R01", "high", "必填项缺失",
                        "第 %d 行「%s」为空。缺失字段无法参与后续判定。" % (r + 1, name),
                        td.index, r, c, name, sample=sid(r),
                        suggestion="补齐后重跑校验",
                    ))

        # ---- R02 样本编号重复（同一编号 + 同一批号才算真重复）----
        if c_sid is not None:
            occ: Dict[str, List[tuple]] = {}
            for r in range(hdr + 1, len(rows)):
                key = sid(r)
                if not key:
                    continue
                lot_v = g(r, c_lot).strip() if c_lot is not None else ""
                occ.setdefault(key, []).append((r, lot_v))
            for key, lst in occ.items():
                if len(lst) < 2:
                    continue
                # 同一批号出现多次 → 真重复；批号各不相同 → 正常的多样本记录
                by_lot: Dict[str, List[int]] = {}
                for r, lot_v in lst:
                    by_lot.setdefault(lot_v, []).append(r)
                for lot_v, rs in by_lot.items():
                    if len(rs) < 2:
                        continue
                    dup_rows = "、".join(str(x + 1) for x in rs)
                    issues.append(Issue(
                        "R02", "medium", "样本编号重复",
                        "「%s」%s在第 %s 行重复出现。同一编号 + 同一批号出现多次，"
                        "通常意味着同一份检测被重复录入。"
                        % (key, ("（批号 %s）" % lot_v) if lot_v else "（批号为空）", dup_rows),
                        td.index, rs[1], c_sid, "样本编号", sample=key,
                        suggestion="确认是否重复录入，保留一条",
                    ))

        # ---- R03 批间一致性 ----
        if c_sid is not None and c_res is not None and c_lot is not None:
            group: Dict[tuple, List[tuple]] = {}
            for r in range(hdr + 1, len(rows)):
                k = (sid(r), g(r, c_item).strip())
                if not k[0]:
                    continue
                group.setdefault(k, []).append((r, g(r, c_lot).strip(), _parse_result(g(r, c_res))))
            for (s, item), lst in group.items():
                results = {x[2] for x in lst if x[2] in ("pos", "neg")}
                if len(results) > 1:
                    detail = "；".join("批号 %s → %s" % (lot or "（空）", g(r, c_res))
                                      for r, lot, _ in lst)
                    issues.append(Issue(
                        "R03", "high", "批间结果不一致",
                        "样本 %s（%s）在不同批号下结果不同：%s。同一份样本在不同生产批号间应保持一致，"
                        "否则需怀疑批间差或操作误差。" % (s, item or "未标注项目", detail),
                        td.index, lst[0][0], c_res, "检测结果", sample=s,
                        suggestion="复核该样本的操作记录与批号，必要时重新检测",
                    ))

        # ---- R04 结论与检测结果矛盾（同样本合并为一条）----
        if c_res is not None and c_con is not None:
            bad: Dict[tuple, List[int]] = {}
            for r in range(hdr + 1, len(rows)):
                res = _parse_result(g(r, c_res))
                con = norm(g(r, c_con))
                if not res or not con:
                    continue
                contradicted = ((res == "pos" and "阴性" in con) or
                                (res == "neg" and "阳性" in con))
                if contradicted:
                    bad.setdefault((sid(r), g(r, c_res), g(r, c_con)), []).append(r)
            for (s, res_txt, con_txt), rs in bad.items():
                rows_txt = "、".join(str(x + 1) for x in rs)
                issues.append(Issue(
                    "R04", "high", "结论与检测结果矛盾",
                    "检测结果「%s」但结论写「%s」，涉及第 %s 行（共 %d 条）。"
                    % (res_txt, con_txt, rows_txt, len(rs)),
                    td.index, rs[0], c_con, "结论", sample=s,
                    suggestion="以检测结果为准修正结论，或说明判定依据",
                ))

        # ---- R05 Ct 值与结果矛盾 ----
        if c_ct is not None and c_res is not None:
            for r in range(hdr + 1, len(rows)):
                ct = _parse_ct(g(r, c_ct))
                res = _parse_result(g(r, c_res))
                if ct is None or res is None:
                    continue
                if res == "pos" and ct >= CT_MAX_POSITIVE:
                    issues.append(Issue(
                        "R05", "medium", "Ct 值与阳性判定不符",
                        "判为阳性但 Ct=%s，已超过常用阳性判定上限（%s）。可能是弱阳性、"
                        "也可能判读标准与本次试验方案不一致。" % (_two(ct), _two(CT_MAX_POSITIVE)),
                        td.index, r, c_ct, "Ct值", sample=sid(r),
                        suggestion="核对本次试验的判定阈值，或复测确认",
                    ))
                elif res == "neg" and ct and ct < CT_MIN_NEGATIVE:
                    issues.append(Issue(
                        "R05", "medium", "Ct 值与阴性判定不符",
                        "判为阴性但 Ct=%s，低于常用阴性判定下限（%s）。" % (_two(ct), _two(CT_MIN_NEGATIVE)),
                        td.index, r, c_ct, "Ct值", sample=sid(r),
                        suggestion="复核该样本是否存在扩增曲线异常",
                    ))

        # ---- R06 数值越界 ----
        if c_age is not None:
            for r in range(hdr + 1, len(rows)):
                raw = g(r, c_age).strip()
                if not raw:
                    continue
                try:
                    age = float(raw)
                except ValueError:
                    issues.append(Issue(
                        "R06", "low", "年龄不是数值",
                        "年龄列出现「%s」，无法参与计算。" % raw,
                        td.index, r, c_age, "年龄", sample=sid(r),
                        suggestion="更正为数字",
                    ))
                    continue
                if age < 0 or age > AGE_MAX:
                    issues.append(Issue(
                        "R06", "medium", "年龄超出合理范围",
                        "年龄为 %s，超出 0-%d 的合理范围。" % (_two(age), AGE_MAX),
                        td.index, r, c_age, "年龄", sample=sid(r),
                        suggestion="确认是否录入错误",
                    ))
        if c_ct is not None:
            for r in range(hdr + 1, len(rows)):
                ct = _parse_ct(g(r, c_ct))
                if ct is not None and (ct < 0 or ct > CT_HARD_MAX):
                    issues.append(Issue(
                        "R06", "medium", "Ct 值超出物理范围",
                        "Ct=%s，超出 0-%s 的合理范围。" % (_two(ct), _two(CT_HARD_MAX)),
                        td.index, r, c_ct, "Ct值", sample=sid(r),
                        suggestion="确认是否录入错误",
                    ))

        # ---- R07 日期格式异常 ----
        if c_date is not None:
            for r in range(hdr + 1, len(rows)):
                raw = g(r, c_date).strip()
                if not raw:
                    continue
                mm = DATE_PAT.match(raw.replace("\u3000", "").replace(" ", ""))
                bad = False
                if not mm:
                    bad = True
                else:
                    y, mo, d = int(mm.group(1)), int(mm.group(2)), int(mm.group(3))
                    # 用真实日历校验：2026/02/30 这种「格式对但日子不存在」也要报
                    try:
                        datetime.date(y, mo, d)
                    except ValueError:
                        bad = True
                if bad:
                    issues.append(Issue(
                        "R07", "low", "日期格式异常",
                        "日期「%s」格式不规范或不存在此日期。" % raw,
                        td.index, r, c_date, "检测日期", sample=sid(r),
                        suggestion="统一为 YYYY-MM-DD",
                    ))

        # ---- R08 结果值不在值域内 ----
        if c_res is not None:
            for r in range(hdr + 1, len(rows)):
                raw = g(r, c_res).strip()
                if not raw:
                    continue
                if _parse_result(raw) == "other":
                    issues.append(Issue(
                        "R08", "low", "结果值不在允许取值内",
                        "结果列出现「%s」，既不是阳性也不是阴性。" % raw,
                        td.index, r, c_res, "检测结果", sample=sid(r),
                        suggestion="统一为「阳性/阴性」或方案规定的表述",
                    ))

    return issues
