# -*- coding: utf-8 -*-
"""列语义识别：不硬编码列名，先识别每列是什么

识别顺序：表头精确匹配 → 表头包含匹配 → 值特征推断
置信度低于阈值时，把决定权交回给人（而不是猜）
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# 字段定义：key -> (中文名, 同义词, 是否必需)
FIELD_DEFS: Dict[str, dict] = {
    "sample_id": {
        "label": "样本编号",
        # 注意：「序号」不在此列——它是行号，不是样本标识。
        # 之前把序号当样本编号，会导致「编号重复」规则失效（序号天然唯一）。
        "syn": ["样本编号", "样本号", "标本编号", "标本号", "编号", "样本id", "sampleid",
                "sample", "sx号", "id", "病历号", "住院号", "就诊号"],
        "required": True,
    },
    "gender": {"label": "性别", "syn": ["性别", "sex", "gender"], "required": False},
    "age": {"label": "年龄", "syn": ["年龄", "年龄(岁)", "age", "岁数"], "required": False},
    "item": {"label": "检测项目", "syn": ["检测项目", "项目", "项目名称", "assay", "item",
                                        "检测指标", "病原体"], "required": True},
    "lot": {"label": "批号", "syn": ["批号", "生产批号", "批号/生产批号", "lot", "lotno",
                                    "lot no", "lotnumber", "批次"], "required": False},
    "result": {"label": "检测结果", "syn": ["检测结果", "结果", "结果值", "result", "读数",
                                          "检出结果"], "required": True},
    "ct": {"label": "Ct值", "syn": ["ct值", "ct", "ctvalue", "循环阈值", "cq值"], "required": False},
    "conclusion": {"label": "结论", "syn": ["结论", "判定", "判定结果", "结论/判定", "conclusion",
                                          "结论值", "符合性"], "required": False},
    "date": {"label": "检测日期", "syn": ["检测日期", "日期", "date", "采样日期", "试验日期"],
             "required": False},
}

POS_WORDS = {"阳性", "阳", "pos", "positive", "+", "阳性(+)", "检出"}
NEG_WORDS = {"阴性", "阴", "neg", "negative", "-", "阴性(-)", "未检出"}
MALE_WORDS = {"男", "m", "male", "1"}
FEMALE_WORDS = {"女", "f", "female", "2"}

DATE_PAT = re.compile(r"^\d{4}[-/年.]\d{1,2}[-/月.]\d{1,2}日?$")
NUM_PAT = re.compile(r"^-?\d+(\.\d+)?$")


def norm(s: str) -> str:
    if not s:
        return ""
    s = s.replace("\u3000", " ").replace("\u00a0", " ")
    s = s.strip().lower()
    s = s.replace("（", "(").replace("）", ")")
    s = re.sub(r"\s+", "", s)
    return s


@dataclass
class ColumnGuess:
    col: int
    raw_header: str
    field: Optional[str]
    confidence: float
    how: str            # header_exact / header_contains / value_infer / none

    @property
    def label(self) -> str:
        if self.field:
            return FIELD_DEFS[self.field]["label"]
        return "（未识别）"


@dataclass
class Layout:
    separator_style: str
    columns: List[ColumnGuess] = field(default_factory=list)
    needs_confirm: List[ColumnGuess] = field(default_factory=list)

    def index_of(self, fld: str) -> Optional[int]:
        for c in self.columns:
            if c.field == fld:
                return c.col
        return None

    def mapping(self) -> Dict[str, int]:
        return {c.field: c.col for c in self.columns if c.field}

    def required_missing(self) -> List[str]:
        got = set(self.mapping().keys())
        return [FIELD_DEFS[k]["label"] for k, v in FIELD_DEFS.items()
                if v["required"] and k not in got]


def _value_profile(values: List[str]) -> dict:
    vals = [v.strip() for v in values if v and v.strip()]
    if not vals:
        return {}
    low = [norm(v) for v in vals]
    n = len(low)
    pos = sum(1 for v in low if v in POS_WORDS) / n
    neg = sum(1 for v in low if v in NEG_WORDS) / n
    male = sum(1 for v in low if v in MALE_WORDS) / n
    female = sum(1 for v in low if v in FEMALE_WORDS) / n
    date = sum(1 for v in low if DATE_PAT.match(v)) / n
    num = sum(1 for v in low if NUM_PAT.match(v)) / n
    return {"pos": pos, "neg": neg, "male": male, "female": female,
            "date": date, "num": num, "n": n}


def infer_by_values(values: List[str]) -> Tuple[Optional[str], float]:
    p = _value_profile(values)
    if not p:
        return None, 0.0
    if p["pos"] + p["neg"] >= 0.8:
        return "result", 0.7
    if p["male"] + p["female"] >= 0.8:
        return "gender", 0.7
    if p["date"] >= 0.8:
        return "date", 0.7
    if p["num"] >= 0.9:
        return "ct", 0.5          # 纯数字列歧义大：可能是 Ct、年龄、序号
    return None, 0.0


def analyze(headers: List[str], rows: List[List[str]], sep: str = "") -> Layout:
    """headers: 表头文本；rows: 数据行（不含表头）"""
    used: Dict[str, int] = {}
    guesses: List[ColumnGuess] = []

    for ci, h in enumerate(headers):
        nh = norm(h)
        values = [r[ci] if ci < len(r) else "" for r in rows]
        best: Optional[Tuple[str, float, str]] = None

        # 1) 表头精确匹配
        if nh:
            for fld, meta in FIELD_DEFS.items():
                for syn in meta["syn"]:
                    if nh == norm(syn):
                        best = (fld, 0.95, "header_exact")
                        break
                if best:
                    break

        # 2) 表头包含匹配（取最长同义词，避免 "样本编号" 被 "编号" 抢走）
        if not best and nh:
            cands = []
            for fld, meta in FIELD_DEFS.items():
                for syn in meta["syn"]:
                    ns = norm(syn)
                    if ns and ns in nh:
                        cands.append((len(ns), fld))
            if cands:
                cands.sort(reverse=True)
                best = (cands[0][1], 0.80, "header_contains")

        # 3) 值特征推断
        if not best:
            fld, conf = infer_by_values(values)
            if fld:
                best = (fld, conf, "value_infer")

        if best:
            fld, conf, how = best
            if fld in used:                       # 同一字段重复出现，后一个降级
                conf -= 0.25
                how += "_dup"
            used[fld] = used.get(fld, 0) + 1
            guesses.append(ColumnGuess(ci, h, fld, round(conf, 2), how))
        else:
            guesses.append(ColumnGuess(ci, h, None, 0.0, "none"))

    lay = Layout(separator_style=sep, columns=guesses)
    lay.needs_confirm = [g for g in guesses if g.confidence < 0.7]
    return lay


def apply_manual(lay: Layout, manual: Dict[int, str]) -> Layout:
    """人确认/指定：{col: field}；field 为 "" 表示忽略该列"""
    for g in lay.columns:
        if g.col in manual:
            fld = manual[g.col] or None
            g.field = fld
            g.confidence = 1.0
            g.how = "manual"
    lay.needs_confirm = [g for g in lay.columns if g.confidence < 0.7 and g.field]
    return lay
