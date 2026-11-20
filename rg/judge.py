# -*- coding: utf-8 -*-
"""语义判断层：规则算不出来的部分交给模型

规则能算「符合率 87% 低于阈值」，算不出「这段文字说全部符合，可数据里明明有两例不符」。
后者要读懂中文、理解上下文、做跨段落比对——这正是模型的活。

无 API Key 时降级为本地启发式，并明确标注来源，绝不冒充模型结论。
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Optional

from .rules import Issue

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_dotenv():
    """零依赖读取项目根目录的 .env（不覆盖已有环境变量）"""
    for name in (".env", ".env.local"):
        p = os.path.join(_ROOT, name)
        if not os.path.exists(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k and k not in os.environ:
                        os.environ[k] = v
        except OSError:
            pass


_load_dotenv()

# ---------------------------------------------------------------- 配置

# 断言词分两级：强断言单独出现即算；弱断言必须与「结果类名词」共现，
# 否则「结构与常规记录一致」这种描述性句子会被误判为结论断言。
ASSERT_STRONG = ["全部符合", "均符合", "无异常", "符合预期", "未发现异常", "无问题",
                 "全部为阴性", "全部为阳性", "未检出", "全部正常"]
ASSERT_WEAK = ["全部", "均", "所有", "一致", "正常", "符合"]
RESULT_NOUNS = ["结果", "结论", "数据", "检测", "异常", "符合率", "记录表"]

# 允许模型输出的字段名，与 layout.FIELD_DEFS 的 key 保持一致
KNOWN_FIELDS = {"sample_id", "gender", "age", "item", "lot",
                "result", "ct", "conclusion", "date"}


def is_claim(sentence: str) -> bool:
    """判断一句话是不是「关于检测结果的文字断言」"""
    t = sentence or ""
    if len(t) < 8:
        return False
    if any(w in t for w in ASSERT_STRONG):
        return True
    if any(w in t for w in ASSERT_WEAK) and any(n in t for n in RESULT_NOUNS):
        return True
    return False


@dataclass
class LLMConfig:
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    timeout: int = 40

    @property
    def enabled(self) -> bool:
        return bool(self.api_key.strip())

    @staticmethod
    def from_env() -> "LLMConfig":
        key = (os.environ.get("RG_API_KEY") or os.environ.get("OPENAI_API_KEY")
               or os.environ.get("DEEPSEEK_API_KEY") or "")
        base = (os.environ.get("RG_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
                or "https://api.deepseek.com")
        model = (os.environ.get("RG_MODEL") or os.environ.get("OPENAI_MODEL")
                 or "deepseek-chat")
        return LLMConfig(api_key=key.strip(), base_url=base.strip(), model=model.strip())


# ---------------------------------------------------------------- 数据摘要

def summarize(tables, issues: List[Issue]) -> dict:
    total_rows = sum(td.n_data_rows for td in tables)
    samples = set()
    pos = neg = other = 0
    for td in tables:
        hdr = td.headers
        for row in td.data_rows():
            d = {hdr[i]: row[i] for i in range(min(len(hdr), len(row)))}
            sid = (d.get("样本编号") or d.get("编号") or "").strip()
            if sid:
                samples.add(sid)
            r = (d.get("检测结果") or d.get("结果") or d.get("结果值") or "").strip()
            if r in ("阳性", "阳", "POS", "pos", "+"):
                pos += 1
            elif r in ("阴性", "阴", "NEG", "neg", "-"):
                neg += 1
            elif r:
                other += 1
    return {
        "记录条数": total_rows,
        "样本份数": len(samples),
        "阳性条数": pos,
        "阴性条数": neg,
        "其他结果值": other,
        "规则发现问题数": len(issues),
        "问题类型": sorted({i.title for i in issues}),
    }


# ---------------------------------------------------------------- 模型调用

SYSTEM_PROMPT = """你是一名体外诊断（IVD）试验记录的复核员。
你的任务只有一件：判断文档里的**文字性结论/断言**，与表格中的**实际数据**是否一致。

要求：
1. 只依据给出的数据摘要判断，不要推测数据之外的信息。
2. 如果文字声称“全部符合/无异常/均一致”，但摘要显示存在规则问题或数据分布与之矛盾，判为不一致。
3. 如果文字只是中性描述（如“本次试验共检测 X 份样本”），判为一致。
4. 输出严格 JSON，不要输出任何其他文字。

输出格式：
{"consistent": true/false, "reason": "一句话说明依据", "quoted": "被判断的那句原文"}"""


def _post_openai(cfg: LLMConfig, messages: List[dict], max_tokens: int = 400) -> str:
    url = cfg.base_url.rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": cfg.model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": max_tokens,
        "stream": False,
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + cfg.api_key)
    with urllib.request.urlopen(req, timeout=cfg.timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    data = json.loads(raw)
    return data["choices"][0]["message"]["content"]


# ---------------------------------------------------------------- 列语义猜测

GUESS_PROMPT = """你是检测记录表的解析助手。给你一张表的表头和前几行的值，判断每一列是什么字段。

可选字段（只能从这里面选，或给 null）：
- sample_id：样本编号 / 病历号 / 唯一标识
- gender：性别
- age：年龄
- item：检测项目
- lot：试剂批号
- result：检测结果（阳性 / 阴性）
- ct：Ct 值 / 扩增阈值（数值）
- conclusion：判定 / 结论（如「符合阳性」「符合阴性」）
- date：日期

判断规则：
1. 同时看列名含义和值的形态。比如列名叫「病历号」、值都是 MR- 开头的编码，那就是样例编号。
2. 拿不准就给 null，不要硬猜——猜错了会让整份记录的校验结论失效。
3. reason 必须指出具体证据，不要写「根据语义判断」这种空话。

只输出 JSON，不要别的：
{"columns":[{"index":0,"field":"sample_id","reason":"列名是病历号，值都是 MR- 开头的唯一编码","confidence":0.9}]}"""


def guess_columns(headers: List[str], sample_rows: List[List[str]],
                  cfg: Optional[LLMConfig] = None) -> Dict[int, dict]:
    """让模型猜每一列是什么字段。返回 {col_index: {field, reason, confidence}}"""
    cfg = cfg or LLMConfig.from_env()
    if not cfg.enabled or not headers:
        return {}

    preview = []
    for row in sample_rows[:4]:
        preview.append([str(c)[:24] for c in row[:len(headers)]])

    user = ("【表头】\n%s\n\n【前几行的值】\n%s"
            % (json.dumps(headers, ensure_ascii=False),
               json.dumps(preview, ensure_ascii=False, indent=1)))
    try:
        content = _post_openai(cfg, [
            {"role": "system", "content": GUESS_PROMPT},
            {"role": "user", "content": user},
        ], max_tokens=900)
        obj = _extract_json(content)
        if not obj:
            return {}
        out: Dict[int, dict] = {}
        for item in obj.get("columns", []):
            try:
                idx = int(item.get("index"))
            except (TypeError, ValueError):
                continue
            field = (item.get("field") or "").strip()
            if field and field not in KNOWN_FIELDS:
                field = ""
            out[idx] = {
                "field": field,
                "reason": str(item.get("reason", ""))[:120],
                "confidence": float(item.get("confidence") or 0.0),
            }
        return out
    except Exception as exc:                                   # noqa: BLE001
        print("[WARN] 列语义猜测失败：%s" % exc)
        return {}


def _extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------- 问题解释

EXPLAIN_PROMPT = """你是体外诊断（IVD）试验记录的复核员。下面给你若干条规则引擎已经发现的问题，
以及出问题那行、以及同一批号的其他行数据。请对每一条写一段分析。

每段要说清三件事，合并成一段话，不超过 80 字：
1. 这个问题在业务上意味着什么
2. 判断依据——必须引用同一批或同一样本的其他具体数据做对比（比如「同批另外两支阳性 Ct 在 23-25」）
3. 建议怎么处理

铁律：
- 不要重复问题本身的描述，直接给分析
- 不要下最终结论，最终判断由人做
- 只依据给出的数据，不要编造数字
- 数据不足以判断时，直接写「数据不足以判断，需核对原始记录」

只输出 JSON：{"items":[{"ref":"1","note":"……"}]}"""


def _row_dict(td, r: int) -> dict:
    hdr = td.headers
    row = td.rows[r] if 0 <= r < len(td.rows) else []
    return {hdr[i]: row[i] for i in range(min(len(hdr), len(row)))}


def _lot_key(d: dict) -> str:
    for k, v in d.items():
        if "批" in k:
            return v
    return ""


def explain_issues(issues: List[Issue], tables, layouts=None,
                   cfg: Optional[LLMConfig] = None, limit: int = 8) -> None:
    """给每条问题补一段结合上下文的业务解释（就地修改 issues 的 rationale）"""
    cfg = cfg or LLMConfig.from_env()
    if not cfg.enabled or not issues:
        return
    by_idx = {td.index: td for td in (tables or [])}

    mats = []
    for n, isu in enumerate(issues[:limit], 1):
        td = by_idx.get(isu.table)
        if td is None:
            continue
        cur = _row_dict(td, isu.row) if isu.row > 0 else {}
        lot = _lot_key(cur)
        peers = []
        if lot:
            for r in range(td.header_row + 1, len(td.rows)):
                d = _row_dict(td, r)
                if _lot_key(d) == lot and d != cur:
                    peers.append(d)
                if len(peers) >= 3:
                    break
        mats.append({
            "ref": str(n),
            "规则": isu.rule_id,
            "问题": isu.title,
            "系统判定": isu.detail,
            "当前行数据": cur,
            "同批其他行": peers,
        })

    if not mats:
        return
    try:
        content = _post_openai(cfg, [
            {"role": "system", "content": EXPLAIN_PROMPT},
            {"role": "user", "content": json.dumps(mats, ensure_ascii=False, indent=1)},
        ], max_tokens=1600)
        obj = _extract_json(content) or {}
        for item in obj.get("items", []):
            try:
                n = int(item.get("ref"))
            except (TypeError, ValueError):
                continue
            if 1 <= n <= len(issues):
                note = str(item.get("note", "")).strip()
                if note:
                    issues[n - 1].rationale = note[:200]
    except Exception as exc:                                   # noqa: BLE001
        print("[WARN] 问题解释生成失败（不影响校验结果）：%s" % exc)


# ---------------------------------------------------------------- 主入口

def judge(doc_texts: List[str], tables, issues: List[Issue],
          cfg: Optional[LLMConfig] = None, table_index: int = 0) -> List[Issue]:
    """返回由语义层发现的额外问题"""
    cfg = cfg or LLMConfig.from_env()
    summary = summarize(tables, issues)
    claims = [t for t in doc_texts if is_claim(t)]
    if not claims:
        return []

    found: List[Issue] = []
    if cfg.enabled:
        for claim in claims[:3]:
            msgs = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content":
                    "【文档中的文字断言】\n%s\n\n【表格数据摘要】\n%s\n\n"
                    "【已被规则引擎发现的问题】\n%s"
                    % (claim, json.dumps(summary, ensure_ascii=False, indent=2),
                       "\n".join("- %s" % i.detail for i in issues[:10]) or "（无）")},
            ]
            try:
                content = _post_openai(cfg, msgs)
                obj = _extract_json(content)
                if obj is None:
                    continue
                if obj.get("consistent") is False:
                    found.append(Issue(
                        "M01", "high", "文字断言与数据不符",
                        "文档中写「%s」，但数据不支持这一说法：%s"
                        % (claim, obj.get("reason", "模型未给出理由")),
                        table_index, 0, 0, "文字描述",
                        suggestion="修正结论表述，或说明差异原因",
                        source="model", confidence=0.85,
                    ))
            except (urllib.error.URLError, urllib.error.HTTPError, KeyError,
                    json.JSONDecodeError, TimeoutError, OSError) as exc:
                print("[WARN] 模型调用失败，降级为本地启发式：%s" % exc)
                return _heuristic(claims, summary, issues, table_index)
            except Exception as exc:                      # noqa: BLE001
                print("[WARN] 模型层异常，降级：%s" % exc)
                return _heuristic(claims, summary, issues, table_index)
        return found

    return _heuristic(claims, summary, issues, table_index)


def _heuristic(claims: List[str], summary: dict, issues: List[Issue],
               table_index: int) -> List[Issue]:
    """无模型时的兜底：强断言 + 存在实质问题 → 判为矛盾"""
    strong = ("全部", "均", "无异常", "未发现", "一致", "无问题")
    serious = [i for i in issues if i.severity in ("high", "medium")]
    found: List[Issue] = []
    if not serious:
        return found
    for claim in claims:
        if any(w in claim for w in strong):
            kinds = "、".join(sorted({i.title for i in serious})[:4])
            found.append(Issue(
                "H01", "high", "文字断言与数据不符（本地判定）",
                "文档称「%s」，但校验发现 %d 处问题（%s）。"
                % (claim, len(serious), kinds),
                table_index, 0, 0, "文字描述",
                suggestion="修正结论表述，或说明差异原因",
                source="heuristic", confidence=0.7,
            ))
            break
    return found
