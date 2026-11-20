# -*- coding: utf-8 -*-
"""生成三份样例记录（虚构数据，结构真实）

样例 1 clean  : 20 行，逻辑自洽，用于证明「不瞎报」
样例 2 errors : 25 行，埋 8 处问题（7 处规则可查 + 1 处需读文字）
样例 3 messy  : 表头不规范、日期格式混乱，用于演示「识别不了就让人确认」
"""
import os
from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH

HERE = os.path.dirname(os.path.abspath(__file__))

HEADERS = ["样本编号", "性别", "年龄", "检测项目", "批号", "检测结果", "Ct值", "结论", "检测日期"]

ITEMS = ["甲型流感病毒抗原", "乙型流感病毒抗原", "呼吸道合胞病毒抗原", "腺病毒抗原"]


def _doc(title, intro, rows, headers=None, tail="检测人：________　　复核人：________"):
    doc = Document()
    # 页面设置
    sec = doc.sections[0]
    sec.left_margin = sec.right_margin = sec.top_margin = sec.bottom_margin = None or sec.left_margin

    h = doc.add_paragraph()
    r = h.add_run(title)
    r.bold = True
    r.font.size = Pt(16)
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER

    p = doc.add_paragraph(intro)
    p.paragraph_format.space_after = Pt(6)

    headers = headers or HEADERS
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    for i, htxt in enumerate(headers):
        cell = table.rows[0].cells[i]
        cell.text = htxt
        for par in cell.paragraphs:
            for run in par.runs:
                run.bold = True

    for row in rows:
        cells = table.add_row().cells
        for i, val in enumerate(row):
            cells[i].text = "" if val is None else str(val)

    doc.add_paragraph("")
    doc.add_paragraph(tail)
    return doc


def clean_rows():
    """20 行，全部合规（同样本的三个批号用同一检测项目，符合真实记录结构）"""
    rows = []
    n = 0
    for si in range(1, 8):
        sid = "SX-%03d" % si
        sex = "男" if si % 2 else "女"
        age = 22 + si
        item = ITEMS[si % 4]
        for lot in ("LOT20261001", "LOT20261002", "LOT20261003"):
            n += 1
            # 阳性样本 Ct 较低，阴性无 Ct
            positive = si <= 4
            if positive:
                result, ct, concl = "阳性", "%d" % (24 + (n % 5)), "符合阳性"
            else:
                result, ct, concl = "阴性", "", "符合阴性"
            rows.append([sid, sex, age, item, lot, result, ct, concl, "2026-10-20"])
        if len(rows) >= 20:
            break
    return rows[:20]


def error_rows():
    """25 行，埋 8 处问题"""
    rows = []
    idx = 0
    # 样本 1-6：三批号一致（干净基线）
    for si in range(1, 7):
        sid = "SX-%03d" % si
        positive = si <= 3
        for lot in ("LOT20261001", "LOT20261002", "LOT20261003"):
            if positive:
                r, ct, c = "阳性", "%d" % (23 + idx % 6), "符合阳性"
            else:
                r, ct, c = "阴性", "", "符合阴性"
            rows.append([sid, "男" if si % 2 else "女", 22 + si, ITEMS[si % 4], lot, r, ct, c, "2026-10-20"])
            idx += 1

    # 【埋错 1】SX-007 批间不一致：lot1 阳性 / lot2 阴性 / lot3 阳性
    rows.append(["SX-007", "女", 25, ITEMS[2], "LOT20261001", "阳性", "26", "符合阳性", "2026-10-20"])
    rows.append(["SX-007", "女", 25, ITEMS[2], "LOT20261002", "阴性", "", "符合阴性", "2026-10-20"])
    rows.append(["SX-007", "女", 25, ITEMS[2], "LOT20261003", "阳性", "27", "符合阳性", "2026-10-20"])

    # 【埋错 2】SX-008 结论与结果矛盾：结果阳性但结论写「符合阴性」
    rows.append(["SX-008", "男", 31, ITEMS[1], "LOT20261001", "阳性", "25", "符合阴性", "2026-10-20"])
    rows.append(["SX-008", "男", 31, ITEMS[1], "LOT20261002", "阳性", "26", "符合阴性", "2026-10-20"])
    rows.append(["SX-008", "男", 31, ITEMS[1], "LOT20261003", "阳性", "24", "符合阴性", "2026-10-20"])

    # 【埋错 3】SX-009 Ct 值与结果矛盾：阳性但 Ct=41（≥40 通常判阴性）
    rows.append(["SX-009", "女", 28, ITEMS[0], "LOT20261001", "阳性", "41", "符合阳性", "2026-10-20"])
    rows.append(["SX-009", "女", 28, ITEMS[0], "LOT20261002", "阳性", "38", "符合阳性", "2026-10-20"])
    rows.append(["SX-009", "女", 28, ITEMS[0], "LOT20261003", "阳性", "37", "符合阳性", "2026-10-20"])

    # 【埋错 4】SX-010 检测结果缺失
    rows.append(["SX-010", "男", 34, ITEMS[3], "LOT20261001", "", "", "符合阴性", "2026-10-20"])
    rows.append(["SX-010", "男", 34, ITEMS[3], "LOT20261002", "阴性", "", "符合阴性", "2026-10-20"])
    rows.append(["SX-010", "男", 34, ITEMS[3], "LOT20261003", "阴性", "", "符合阴性", "2026-10-20"])

    # 【埋错 5】SX-012 真重复：同一编号 + 同一批号出现两次
    for lot in ("LOT20261001", "LOT20261002", "LOT20261003"):
        rows.append(["SX-012", "女", 26, ITEMS[2], lot, "阴性", "", "符合阴性", "2026-10-20"])
    # 重复录入一行（同一批号再录一次）
    rows.append(["SX-012", "女", 26, ITEMS[2], "LOT20261002", "阴性", "", "符合阴性", "2026-10-20"])

    # 【埋错 6】SX-014 年龄越界 999
    rows.append(["SX-014", "男", 999, ITEMS[0], "LOT20261001", "阴性", "", "符合阴性", "2026-10-20"])

    # 【埋错 7】SX-015 日期格式错误
    rows.append(["SX-015", "女", 23, ITEMS[1], "LOT20261001", "阴性", "", "符合阴性", "2026/13/45"])

    # 再补 2 行正常数据，凑到 25 行左右
    rows.append(["SX-016", "男", 29, ITEMS[3], "LOT20261002", "阴性", "", "符合阴性", "2026-10-20"])
    rows.append(["SX-017", "女", 30, ITEMS[0], "LOT20261003", "阴性", "", "符合阴性", "2026-10-20"])
    return rows


def messy_rows():
    """格式混乱：列名非标准（含一个无法识别的列）、日期写法不一、值带空格、掺入错值"""
    rows = []
    for si in range(1, 8):
        sid = "SX‐%03d" % si if si == 3 else "SX-%03d" % si   # 第 3 行用了全角连字符
        for lot in ("LOT1", "LOT2"):
            age = " 2%d" % si
            date = "2026/10/20" if si % 2 else "2026年10月20日"
            res = "POS" if si <= 4 else "NEG"
            # 掺入 3 处真实问题（在格式混乱的掩护下）
            if si == 5 and lot == "LOT1":
                age = "2X"                        # 年龄不是数字
            if si == 6 and lot == "LOT2":
                date = "2026/10/32"               # 不存在的日期
            if si == 7 and lot == "LOT1":
                res = "待定"                       # 结果值不在允许范围
            rows.append([
                " %s " % sid,
                "M" if si % 2 else "F",
                age,
                "Flu A" if si % 2 else "Flu B",
                lot,
                res,
                "",
                "",
                date,
            ])
    return rows


def variant_rows(kind):
    """从 clean 出发，按 kind 注入已知缺陷，用于评测集逐条对齐"""
    rows = [list(r) for r in clean_rows()]
    if kind == "v01":            # R01 结果缺失
        rows[3][5] = ""
    elif kind == "v02":          # R02 同编号同批号重复
        rows[4][4] = rows[3][4]
    elif kind == "v03":          # R03 批间不一致
        rows[4][5] = "阴性"
        rows[4][6] = ""
        rows[4][7] = "符合阴性"
    elif kind == "v04":          # R04 结论与结果矛盾
        rows[3][7] = "符合阴性"
    elif kind == "v05":          # R05 阳性 Ct 超上限
        rows[3][6] = "41"
    elif kind == "v06":          # R06 年龄越界
        rows[3][2] = 999
    elif kind == "v07":          # R07 日期不存在
        rows[3][8] = "2026/13/45"
    elif kind == "v08":          # R08 结果值不在值域
        rows[3][5] = "弱阳性"
    elif kind == "v09":          # R01 + R06
        rows[3][5] = ""
        rows[3][2] = 999
    elif kind == "v10":          # R04 + R05
        rows[3][7] = "符合阴性"
        rows[3][6] = "41"
    elif kind == "v11":          # R02 + R07
        rows[4][4] = rows[3][4]
        rows[6][8] = "2026/10/32"
    elif kind == "v12":          # R03 + R06(低) + R08
        rows[4][5] = "阴性"
        rows[4][6] = ""
        rows[4][7] = "符合阴性"
        rows[6][2] = "NA"
        rows[9][5] = "待定"
    elif kind == "v13":          # R05 阴性但 Ct 偏低
        rows[12][6] = "30"
    elif kind == "v14":          # R01 + R02 + R08
        rows[3][5] = ""
        rows[7][4] = rows[6][4]
        rows[6][5] = "待定"
    elif kind == "v15":          # R05 + R06（Ct 99 同时超阳性上限与物理上限）
        rows[3][6] = "99"
    return rows


VARIANTS = ["v01", "v02", "v03", "v04", "v05", "v06", "v07", "v08",
            "v09", "v10", "v11", "v12", "v13", "v14", "v15"]


def main():
    # 1 clean
    d = _doc("呼吸道病原体抗原检测记录表（示例一·规范数据）",
             "本次试验共检测样本 7 份，每份平行检测 3 个批号，共计 20 条记录。",
             clean_rows())
    d.save(os.path.join(HERE, "sample_clean.docx"))

    # 2 errors
    d = _doc("呼吸道病原体抗原检测记录表（示例二·含问题数据）",
             "本次试验共检测样本 17 份，每份检测 1-3 个批号，共计 26 条记录，全部结果符合预期，无异常情况。",
             error_rows())
    d.save(os.path.join(HERE, "sample_errors.docx"))

    # 3 messy
    d = _doc("检测记录（示例三·格式不规范）",
             "原始记录，字段命名与格式未经统一。",
             messy_rows(),
             headers=["编号", "Sex", "年龄(岁)", "Assay", "Lot No.", "结果值", "B值", "判定", "日期"])
    d.save(os.path.join(HERE, "sample_messy.docx"))

    # 4 评测变体
    for v in VARIANTS:
        d = _doc("检测记录（评测变体 %s）" % v,
                 "本表为评测用合成样本，每份样本平行检测三个批号。",
                 variant_rows(v))
        d.save(os.path.join(HERE, "variant_%s.docx" % v))

    names = ["sample_clean.docx", "sample_errors.docx", "sample_messy.docx"] + \
            ["variant_%s.docx" % v for v in VARIANTS]
    for f in names:
        p = os.path.join(HERE, f)
        print("%8d  %s" % (os.path.getsize(p), f))


if __name__ == "__main__":
    main()
