# -*- coding: utf-8 -*-
"""评测集与评测脚本

用途：给「记录校验 Agent」一个可复现的质量基线。
方法：每份样本都有标注答案（应该报出几条、分别是什么规则），
      跑完后对齐算三件事——召回、误报、定位准确。

跑法：
    python evals/run.py
    python evals/run.py --verbose
"""
from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from rg.judge import LLMConfig                      # noqa: E402
from rg.pipeline import run as run_pipeline          # noqa: E402


def load_cases():
    p = os.path.join(HERE, "cases.jsonl")
    cases = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def norm_manual(raw):
    """JSON 里的 key 是字符串，pipeline 要 {int: {int: field}}"""
    out = {}
    for t, cols in (raw or {}).items():
        out[int(t)] = {int(c): v for c, v in (cols or {}).items()}
    return out


def metrics(tp, fp, fn):
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return prec, rec, f1


# 语义层的问题编号随来源变化：本地启发式是 H01，走模型是 M01。
# 评测关心的是「这一处有没有被抓到」，不是它由谁抓到的，所以按同一类算。
_ALIAS = {"H01": "SEM", "M01": "SEM"}


def _alias(rule_id):
    return _ALIAS.get(rule_id, rule_id)


def main():
    verbose = "--verbose" in sys.argv
    cases = load_cases()
    cfg = LLMConfig.from_env()

    print("=" * 66)
    print("记录校验 Agent · 评测")
    print("=" * 66)
    print("样本数 : %d" % len(cases))
    print("语义层 : %s" % ("模型（已配置 API）" if cfg.enabled else "本地启发式（未配置 API key）"))
    print("=" * 66)

    TP = FP = FN = 0
    total_ms = 0
    rows_total = 0
    skipped = 0

    for c in cases:
        src = os.path.join(ROOT, "samples", c["file"])
        if not os.path.exists(src):
            print("! 缺样本 %s" % c["file"])
            skipped += 1
            continue
        t0 = time.time()
        res = run_pipeline(src, os.path.join(ROOT, "out"),
                           manual_map=norm_manual(c.get("manual")),
                           cfg=cfg, write_file=False)
        ms = int((time.time() - t0) * 1000)
        total_ms += ms
        rows_total += sum(int(m.get("rows") or 0) for m in (res.tables_meta or []))

        got = {}
        for i in res.issues:
            k = _alias(i.rule_id)
            got[k] = got.get(k, 0) + 1

        want = {}
        for e in c["expect"]:
            k = _alias(e["rule"])
            want[k] = want.get(k, 0) + 1

        case_tp = sum(min(got.get(k, 0), v) for k, v in want.items())
        case_fn = sum(want.values()) - case_tp
        case_fp = sum(got.values()) - case_tp
        TP += case_tp
        FN += case_fn
        FP += case_fp

        ok = (case_fn == 0 and case_fp == 0)
        if verbose or not ok:
            print("\n[%s] %s  (mode=%s, %dms)" % ("OK" if ok else "差异", c["name"], res.mode, ms))
            print("   期望 %s" % (", ".join("%s×%d" % kv for kv in sorted(want.items())) or "无"))
            print("   实得 %s" % (", ".join("%s×%d" % kv for kv in sorted(got.items())) or "无"))
            miss = {k: want[k] - got.get(k, 0) for k in want if want[k] - got.get(k, 0) > 0}
            extra = {k: got[k] - want.get(k, 0) for k in got if got[k] - want.get(k, 0) > 0}
            if miss:
                print("   漏报 %s" % miss)
            if extra:
                print("   误报 %s" % extra)

    n_run = len(cases) - skipped
    prec, rec, f1 = metrics(TP, FP, FN)
    print("\n" + "=" * 66)
    print("汇总（按问题条目计，%d 份样本）" % n_run)
    print("=" * 66)
    print("命中 TP         : %d" % TP)
    print("漏报 FN         : %d" % FN)
    print("误报 FP         : %d" % FP)
    print("准确率 Precision: %.1f%%" % (prec * 100))
    print("召回率 Recall   : %.1f%%" % (rec * 100))
    print("F1              : %.1f%%" % (f1 * 100))
    print("-" * 66)
    print("总耗时          : %d ms（平均 %d ms/份）" % (total_ms, total_ms // max(n_run, 1)))
    if rows_total:
        print("处理行数        : %d 行（%.0f 行/秒）"
              % (rows_total, rows_total / max(total_ms / 1000.0, 1e-6)))
    print("=" * 66)
    if cfg.enabled:
        print("语义层：已配置模型（%s），正文断言由模型判断，编号记 M01。" % cfg.model)
    else:
        print("语义层：未配置模型，走本地启发式，编号记 H01。")
        print("        它只能发现「强断言 + 存在明显问题」这一类，不构成完整语义判断。")
        print("        配置 RG_API_KEY 或 DEEPSEEK_API_KEY 后重跑，指标会变。")
    print("注意：这 %d 份样本里的缺陷都是按规则人为埋进去的（白盒），" % n_run)
    print("      100% 只说明规则覆盖到了自己埋的坑，不证明对真实文件的泛化能力。")


if __name__ == "__main__":
    main()
