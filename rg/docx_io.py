# -*- coding: utf-8 -*-
"""docx 读写：提取表格、定位单元格、写入批注"""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from typing import List, Optional

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph


@dataclass
class CellRef:
    table_idx: int
    row_idx: int          # 0-based，含表头
    col_idx: int
    text: str


@dataclass
class TableData:
    index: int
    rows: List[List[str]]           # 原始文本
    header_row: int = 0             # 表头所在行（0-based）
    headers: List[str] = field(default_factory=list)
    n_data_rows: int = 0

    def data_rows(self):
        return self.rows[self.header_row + 1:]

    def cell(self, row_idx: int, col_idx: int) -> str:
        try:
            return self.rows[row_idx][col_idx]
        except IndexError:
            return ""


def _cell_text(cell) -> str:
    """取单元格纯文本，合并多段落，压缩空白"""
    parts = []
    for p in cell.paragraphs:
        parts.append(p.text)
    txt = " ".join(parts)
    txt = txt.replace("\u00a0", " ").replace("\u3000", " ")
    txt = re.sub(r"[ \t]+", " ", txt).strip()
    return txt


def read_tables(path: str) -> List[TableData]:
    doc = Document(path)
    out: List[TableData] = []
    for ti, table in enumerate(doc.tables):
        rows: List[List[str]] = []
        for row in table.rows:
            rows.append([_cell_text(c) for c in row.cells])
        if not rows:
            continue
        td = TableData(index=ti, rows=rows, header_row=0, headers=rows[0][:])
        normalized = []
        seen = {}
        for h in rows[0]:
            key = h.strip()
            if key in seen:
                seen[key] += 1
                normalized.append("%s#%d" % (key, seen[key]))
            else:
                seen[key] = 0
                normalized.append(key)
        td.headers = normalized
        td.n_data_rows = max(0, len(rows) - 1)
        out.append(td)
    return out


def read_doc_text(path: str) -> List[str]:
    """提取正文段落（不含表格内文字），用于「文字描述 vs 数据」比对"""
    doc = Document(path)
    texts = []
    for p in doc.paragraphs:
        t = (p.text or "").strip()
        if t:
            texts.append(t)
    return texts


def _find_cell_paragraph(table: Table, r: int, c: int) -> Optional[Paragraph]:
    try:
        cell = table.rows[r].cells[c]
    except IndexError:
        return None
    paras = cell.paragraphs
    for p in paras:
        if p.text.strip():
            return p
    return paras[0] if paras else None


def write_comments(src: str, dst: str, marks: List[dict], author="记录校验助手", initials="RG") -> int:
    """在 src 的指定单元格文字上添加批注，另存为 dst。

    marks: [{"table": 0, "row": 3, "col": 5, "text": "批注内容"}]
    返回成功写入的批注数。
    """
    doc = Document(src)
    written = 0
    for m in marks:
        ti = m.get("table", 0)
        if ti >= len(doc.tables):
            continue
        table = doc.tables[ti]
        r, c = m["row"], m["col"]
        # 单元格越界保护
        try:
            cell = table.rows[r].cells[c]
        except IndexError:
            continue
        para = _find_cell_paragraph(table, r, c)
        if para is None:
            continue
        if para.runs:
            runs = list(para.runs)
        else:
            runs = [para.add_run(cell.text or "")]
        try:
            doc.add_comment(runs=runs, text=m["text"], author=author, initials=initials)
            written += 1
        except Exception as exc:                       # noqa: BLE001
            print("[WARN] 批注写入失败 %s: %s" % (m, exc))
    doc.save(dst)
    return written


def out_path(src: str, outdir: str) -> str:
    os.makedirs(outdir, exist_ok=True)
    base = os.path.splitext(os.path.basename(src))[0]
    return os.path.join(outdir, "%s_已批注.docx" % base)
