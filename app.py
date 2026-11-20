# -*- coding: utf-8 -*-
"""记录校验 Agent · 零依赖 Web 界面

用法：
    python app.py            # 打开 http://127.0.0.1:8000
    python app.py --port 9000

只依赖标准库 + python-docx。不装任何前端框架、不连 CDN。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from rg import docx_io                      # noqa: E402,F401
from rg.judge import LLMConfig              # noqa: E402
from rg.layout import FIELD_DEFS            # noqa: E402
from rg.pipeline import run as run_pipeline  # noqa: E402

OUTDIR = os.path.join(HERE, "out")
SAMPLES_DIR = os.path.join(HERE, "samples")
SAMPLES = [
    ("sample_clean.docx", "示例 1 · 规范记录",
     "20 条全合规。用来确认它不会瞎报问题。"),
    ("sample_errors.docx", "示例 2 · 埋了 8 处问题",
     "批间不一致、结论与结果矛盾、Ct 越界、必填缺失、编号重复、年龄越界、日期非法，"
     "以及一句正文断言与数据不符。"),
    ("sample_messy.docx", "示例 3 · 格式混乱",
     "表头是英文和缩写，还有一列「B值」认不出来。它不会硬猜，会停下来问你。"),
    ("sample_other_format.docx", "示例 4 · 另一种检测记录",
     "新冠核酸记录，字段体系完全不同（双靶标、两个 Ct 列）。它不靠模板匹配，"
     "靠猜列 + 让你确认。"),
    ("sample_notrecord.docx", "示例 5 · 不是检测记录",
     "一份费用明细表。它会明确拒绝并说明看到了什么，而不是硬套检测规则。"),
]

_LAST = {}            # token -> 结果字典（供下载用）
_LOCK = threading.Lock()


# ---------------------------------------------------------------- 页面

PAGE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>记录校验 Agent</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#fff;color:#111;
 font:15px/1.7 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:40px 24px 80px}
h1{font-size:24px;margin:0 0 6px;font-weight:600;letter-spacing:.5px}
.sub{color:#555;font-size:14px;margin-bottom:28px}
.note{background:#fafafa;border-left:3px solid #111;padding:12px 16px;
 font-size:13.5px;color:#333;margin:0 0 28px;line-height:1.75}
.note b{font-weight:600}
h2{font-size:16px;font-weight:600;margin:32px 0 14px;
 padding-bottom:8px;border-bottom:1px solid #e5e5e5}
.grid{display:grid;gap:10px}
.card{border:1px solid #ddd;border-radius:6px;padding:14px 16px;cursor:pointer;
 background:#fff;transition:.15s;text-align:left;width:100%;font:inherit;color:inherit}
.card:hover{border-color:#111;background:#fcfcfc}
.card b{font-weight:600;display:block;margin-bottom:3px}
.card span{color:#555;font-size:13px;display:block;line-height:1.6}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
input[type=file]{font:inherit;font-size:13.5px}
button.go{background:#111;color:#fff;border:0;border-radius:5px;
 padding:9px 20px;font:inherit;font-size:14px;cursor:pointer}
button.go:disabled{background:#999;cursor:default}
button.ghost{background:#fff;color:#111;border:1px solid #111;border-radius:5px;
 padding:8px 16px;font:inherit;font-size:13.5px;cursor:pointer}
#out{margin-top:8px}
a.ghost{display:inline-block;text-decoration:none;line-height:1.25;color:#111;
 background:#fff;border:1px solid #111;border-radius:5px;padding:8px 16px;font-size:13.5px}
a.ghost:hover{background:#111;color:#fff}
.meta{font-size:13px;color:#555;margin:10px 0 18px;line-height:1.9}
.meta code{background:#f4f4f4;padding:1px 6px;border-radius:3px;font-size:12.5px}
table{width:100%;border-collapse:collapse;font-size:13.5px;margin:10px 0 18px}
th,td{border:1px solid #ddd;padding:8px 10px;text-align:left;vertical-align:top}
th{background:#fafafa;font-weight:600;font-size:13px}
td.sev{white-space:nowrap;font-weight:600}
tr.high td:first-child{box-shadow:inset 3px 0 0 #111}
tr.medium td:first-child{box-shadow:inset 3px 0 0 #999}
tr.low td:first-child{box-shadow:inset 3px 0 0 #ddd}
.dl{display:inline-block;margin:6px 8px 6px 0;border:1px solid #111;
 border-radius:5px;padding:9px 18px;text-decoration:none;color:#111;font-size:14px}
.dl:hover{background:#111;color:#fff}
.confirm{border:1px solid #111;border-radius:6px;padding:16px 18px;margin:14px 0}
.confirm .hd{font-weight:600;margin-bottom:6px}
.confirm .why{color:#555;font-size:13px;margin-bottom:14px;line-height:1.7}
.maprow{display:flex;gap:10px;align-items:center;margin:8px 0;font-size:13.5px}
.maprow code{background:#f4f4f4;padding:2px 7px;border-radius:3px}
select{font:inherit;font-size:13.5px;padding:5px 8px;border:1px solid #ccc;border-radius:4px}
.err{border-left:3px solid #111;background:#fafafa;padding:12px 16px;
 font-size:13.5px;line-height:1.75}
.loading{color:#555;font-size:14px}
footer{margin-top:56px;padding-top:18px;border-top:1px solid #eee;
 color:#888;font-size:12.5px;line-height:1.9}
</style></head><body>
<div class="wrap">
<h1>记录校验 Agent</h1>
<div class="sub">上传一份检测记录，它找出数据里说不通的地方，并把批注写回原文档。</div>

<div class="note">
<b>它怎么工作：</b>先解析表格和正文，识别每一列是什么；然后用确定性规则查数值类问题
（批间不一致、结论与结果矛盾、Ct 越界、必填缺失等）；再用模型读正文里的文字断言，
看是否与数据自洽；最后把每条问题作为批注写回原 Word 文件。<br>
<b>认不出来的列不会硬猜</b>——它会停下来让你指定。因为在数据核对里，
一个错误的「通过」比一次明确的「失败」代价高得多。
</div>

<h2>选一个示例</h2>
<div class="grid" id="samples"></div>

<h2>或上传自己的文件</h2>
<div class="row">
  <input type="file" id="f" accept=".docx">
  <button class="go" id="up">开始校验</button>
  <span style="color:#777;font-size:13px">仅支持 .docx，第一张表作为数据表</span>
</div>

<div id="out"></div>

<footer>
记录校验 Agent · 规则层保证不漏，模型层负责读懂文字，不确定的结论显式标出而不是猜。<br>
所有示例数据均为虚构，仅用于演示。
</footer>
</div>

<script>
const SAMPLES = __SAMPLES__;
const FIELDS = __FIELDS__;

const el = id => document.getElementById(id);

function renderSamples(){
  const box = el('samples');
  SAMPLES.forEach(s => {
    const div = document.createElement('div');
    div.className = 'card';
    div.style.cursor = 'default';
    div.innerHTML =
      '<b>' + s.title + '</b><span>' + s.desc + '</span>' +
      '<div class="row" style="margin-top:10px">' +
        '<button class="go pick">用这份文件校验</button>' +
        '<a class="ghost" target="_blank" href="/api/source/' +
          encodeURIComponent(s.file) + '">查看原文件</a>' +
      '</div>';
    div.querySelector('.pick').onclick = () => start({sample: s.file});
    box.appendChild(div);
  });
}

function showLoading(msg){
  el('out').innerHTML = '<div class="loading">' + (msg||'正在校验…') + '</div>';
}

async function start(payload){
  showLoading('正在解析、校验，并让模型读一遍上下文…通常 3-5 秒');
  try{
    const r = await fetch('/api/run', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    });
    const j = await r.json();
    if(!j.ok && j.error){ renderError(j.error); return; }
    render(j);
    setTimeout(() => {
      const o = el('out');
      if(o) o.scrollIntoView({behavior:'smooth', block:'start'});
    }, 80);
  }catch(e){
    renderError(String(e));
  }
}

function renderError(msg){
  el('out').innerHTML = '<div class="err"><b>处理失败</b><br>' + escapeHtml(msg) +
    '<br><br>这不是崩溃——系统宁可明确报错，也不输出一个看起来正常但可能错的核对结果。</div>';
}

function escapeHtml(s){
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// 把「我读到了什么」摊开给用户看：不解耦这个，报告就是黑箱
function tablePreview(prev){
  if(!prev || !prev.length) return '';
  let h = '<div class="note"><b>这是我从文件里读到的内容</b>' +
          '<span style="color:#777">（每张表显示前 3 行，完整文件可点「查看原文件」下载）</span>';
  prev.forEach(t => {
    h += '<div style="margin-top:10px;overflow-x:auto"><table style="margin:0;font-size:12.5px">';
    h += '<thead><tr>';
    (t.headers || []).forEach(hd => { h += '<th>' + escapeHtml(hd) + '</th>'; });
    h += '</tr></thead><tbody>';
    (t.rows || []).forEach(r => {
      h += '<tr>';
      for(let i = 0; i < (t.headers || []).length; i++){
        const v = (r[i] === undefined || r[i] === null) ? '' : r[i];
        h += '<td>' + escapeHtml(v) + '</td>';
      }
      h += '</tr>';
    });
    h += '</tbody></table></div>';
  });
  h += '</div>';
  return h;
}

// 不是检测记录 —— 明确拒绝，并把自己看到了什么交代清楚
function renderDomain(j){
  const heads = (j.summary && j.summary['识别到的表头']) || [];
  let h = '<div class="confirm"><div class="hd">这份文件我不处理</div>';
  h += '<div class="why">' + escapeHtml(j.error || '') + '</div>';
  if(heads.length){
    h += '<div style="font-size:13px;color:#555;margin:4px 0 8px">我从这份文件里读到的表头是：</div>';
    h += '<div style="margin-bottom:12px">';
    heads.forEach(p => {
      h += '<code style="background:#f4f4f4;padding:2px 8px;border-radius:3px;' +
           'margin:0 5px 5px 0;display:inline-block;font-size:12.5px">' +
           escapeHtml(p[1]) + '</code>';
    });
    h += '</div>';
  }
  if(j.summary && j.summary['说明']){
    h += '<div class="why" style="margin-bottom:0">' + escapeHtml(j.summary['说明']) + '</div>';
  }
  h += '</div>';
  el('out').innerHTML = h;
}

const MODE_NAME = {
  'rule_only': '规则校验（语义层未启用）',
  'rule+heuristic': '规则 + 本地启发式语义判断',
  'rule+model': '规则 + 模型语义判断',
  'need_confirm': '等待人工确认列含义'
};

function render(j){
  if(j.mode === 'domain_mismatch'){ renderDomain(j); return; }
  if(j.needs_confirm && j.needs_confirm.length){
    renderConfirm(j); return;
  }
  let h = '';
  h += '<div class="meta">';
  h += '模式：<b>' + (MODE_NAME[j.mode] || j.mode) + '</b>　';
  h += '解析：<b>' + j.n_tables + '</b> 张表 / <b>' + j.n_rows + '</b> 行数据　';
  if(j.steps && j.steps.length){
    h += '<br>执行轨迹：' + j.steps.map(s =>
      escapeHtml(s.name) + ' <span style="color:#888">' + s.ms + 'ms</span>'
    ).join(' → ');
  }
  h += '</div>';
  h += tablePreview(j.preview);

  if(!j.issues.length){
    h += '<div style="border:1px solid #ddd;border-radius:6px;padding:16px 18px;font-size:14px">' +
         '<b>未发现问题。</b><br><span style="color:#555;font-size:13px">' +
         '规则层 8 类检查全部通过，文字断言与数据未发现矛盾。</span></div>';
    el('out').innerHTML = h; return;
  }

  h += '<h2>发现 ' + j.issues.length + ' 处问题' +
       '　<span style="font-weight:400;color:#555;font-size:13px">高 ' + j.counts.high +
       ' / 中 ' + j.counts.medium + ' / 低 ' + j.counts.low + '</span></h2>';
  h += '<table><thead><tr><th style="width:52px">级别</th><th style="width:150px">类型</th>' +
       '<th>说明</th><th style="width:132px">来源</th></tr></thead><tbody>';
  j.issues.forEach(it => {
    h += '<tr class="' + it.severity + '"><td class="sev">' +
         ({high:'高',medium:'中',low:'低'}[it.severity]||'') + '</td><td>' +
         escapeHtml(it.title) + (it.sample ? '<br><span style="color:#777;font-size:12.5px">' +
         escapeHtml(it.sample) + '</span>' : '') + '</td><td>' +
         escapeHtml(it.detail) +
         (it.suggestion ? '<br><span style="color:#555;font-size:12.5px">建议：' +
         escapeHtml(it.suggestion) + '</span>' : '') +
         (it.rationale ? '<br><span style="color:#111;font-size:12.5px">' +
         '<b>分析：</b>' + escapeHtml(it.rationale) + '</span>' : '') +
         '</td><td style="font-size:12.5px;color:#555">' +
         (it.source === 'model' ? '模型判断' : it.source === 'heuristic' ? '本地判定' : '规则') +
         '<br>置信度 ' + (it.confidence*100).toFixed(0) + '%</td></tr>';
  });
  h += '</tbody></table>';

  if(j.download){
    h += '<a class="dl" href="/api/download/' + j.token + '">下载带批注的 Word 文件</a>';
    if(j.comments_written){
      h += '<span style="color:#555;font-size:13px">已写入 ' + j.comments_written + ' 条批注</span>';
    }
  }
  el('out').innerHTML = h;
}

function renderConfirm(j){
  const hasGuess = j.needs_confirm.some(c => c.guess && c.guess.field);
  let h = '<div class="confirm"><div class="hd">有 ' + j.needs_confirm.length +
          ' 列无法确定含义，需要你确认</div><div class="why">' +
          (hasGuess
            ? '系统先自己看了一眼，给出了猜测和依据——但它不会替你决定。猜错一列，整份结论就废了。'
            : '系统没有猜——在记录核对里，猜错一列的含义会导致整份结果失效。请告诉它这几列分别是什么。') +
          '</div>';
  h += '<table style="margin:0 0 14px"><thead><tr><th style="width:130px">表头</th>' +
       '<th>模型的判断</th><th style="width:170px">指定为</th></tr></thead><tbody>';
  j.needs_confirm.forEach(c => {
    const g = c.guess || {};
    const gLabel = g.field ? ((FIELDS.find(f => f.key === g.field) || {}).label || g.field) : '';
    const judgeCell = g.field
      ? '<b>' + escapeHtml(gLabel) + '</b> <span style="color:#777;font-size:13px">（' +
        (g.confidence * 100).toFixed(0) + '%）</span><br>' +
        '<span style="color:#555;font-size:13px">依据：' + escapeHtml(g.reason || '—') + '</span>'
      : '<span style="color:#777;font-size:13px">' +
        (c.missing ? '这是必需字段，但表里没有' :
          ('没把握（置信度 ' + (c.confidence * 100).toFixed(0) + '%），你可以直接指定')) + '</span>';
    h += '<tr><td><code>' + escapeHtml(c.raw_header || '（空）') + '</code></td>' +
         '<td>' + judgeCell + '</td>' +
         '<td><select data-col="' + c.col + '" data-table="' + c.table + '" class="sel">' +
         '<option value="">不参与校验</option>' +
         FIELDS.map(f => '<option value="' + f.key + '"' +
            ((g.field === f.key) ? ' selected' : '') + '>' + f.label + '</option>').join('') +
         '</select></td></tr>';
  });
  h += '</tbody></table>';
  h += '<button class="go" id="confirmBtn">确认并继续校验</button></div>';
  el('out').innerHTML = h;
  el('confirmBtn').onclick = () => {
    const map = {};
    document.querySelectorAll('.sel').forEach(s => {
      if(s.value){
        const t = s.dataset.table;
        if(!map[t]) map[t] = {};
        map[t][s.dataset.col] = s.value;
      }
    });
    start({sample: j.sample, manual: map});
  };
}

el('up').onclick = async () => {
  const f = el('f').files[0];
  if(!f){ alert('请先选择一个 .docx 文件'); return; }
  if(!/\.docx$/i.test(f.name)){ alert('只支持 .docx 格式'); return; }
  const fd = new FormData();
  fd.append('file', f);
  showLoading('正在上传并校验…');
  try{
    const r = await fetch('/api/upload', {method:'POST', body: fd});
    const j = await r.json();
    if(!j.ok && j.error){ renderError(j.error); return; }
    render(j);
  }catch(e){ renderError(String(e)); }
};

renderSamples();
</script>
</body></html>
"""


# ---------------------------------------------------------------- 结果序列化

def _flat_confirm(payload):
    """pipeline 的 needs_confirm 是按表聚合的，前端按列展示更直观"""
    out = []
    for block in (payload or []):
        table = block.get("table", 0)
        missing = set(block.get("missing_required") or [])
        for c in (block.get("columns") or []):
            need = (c.get("confidence") or 0) < 0.7 or (c.get("field") in missing)
            if not need:
                continue
            out.append({
                "table": table,
                "col": c.get("col"),
                "raw_header": c.get("header") or "",
                "confidence": c.get("confidence") or 0.0,
                "field": c.get("field") or "",
                "missing": bool(c.get("field") in missing),
                "guess": c.get("guess") or {},
            })
    return out


def _normalize_manual(manual):
    """JSON 的 key 都是字符串，转成 {int: {int: field}}"""
    out = {}
    for tk, cols in (manual or {}).items():
        try:
            t = int(tk)
        except (TypeError, ValueError):
            continue
        m = {}
        for ck, fld in (cols or {}).items():
            try:
                c = int(ck)
            except (TypeError, ValueError):
                continue
            if fld:
                m[c] = fld
        if m:
            out[t] = m
    return out


def _result_to_json(res, sample_name=""):
    issues = [{
        "severity": i.severity,
        "title": i.title,
        "detail": i.detail,
        "suggestion": i.suggestion,
        "sample": i.sample or "",
        "rule_id": i.rule_id,
        "source": i.source or "rule",
        "confidence": i.confidence,
        "rationale": i.rationale or "",
    } for i in res.issues]

    counts = res.counts() if hasattr(res, "counts") else {"high": 0, "medium": 0, "low": 0}

    meta = getattr(res, "tables_meta", None) or []
    n_tables = len(meta)
    n_rows = sum(int(m.get("n_rows") or m.get("rows") or 0) for m in meta)

    preview = [{
        "index": m.get("index"),
        "headers": m.get("headers") or [],
        "rows": m.get("sample_rows") or [],
        "n_rows": int(m.get("n_rows") or m.get("rows") or 0),
    } for m in meta]

    token = None
    if res.output_path and os.path.exists(res.output_path):
        token = os.path.basename(res.output_path)
        with _LOCK:
            _LAST[token] = res.output_path

    return {
        "ok": True,
        "mode": res.mode,
        "error": res.error or "",
        "summary": res.summary or {},
        "preview": preview,
        "n_tables": n_tables,
        "n_rows": n_rows,
        "issues": issues,
        "counts": counts,
        "steps": [{"name": s.name, "ms": s.ms} for s in (res.trace or [])],
        "needs_confirm": _flat_confirm(res.needs_confirm),
        "comments_written": res.comments_written,
        "download": bool(token),
        "token": token or "",
        "sample": sample_name,
    }


# ---------------------------------------------------------------- multipart

def parse_multipart(body: bytes, boundary: bytes):
    """极简 multipart/form-data 解析，只取第一个文件字段"""
    name, data = None, None
    for part in body.split(b"--" + boundary):
        if b"Content-Disposition" not in part:
            continue
        head, _, payload = part.partition(b"\r\n\r\n")
        m = re.search(rb'filename="([^"]*)"', head)
        if not m:
            continue
        name = m.group(1).decode("utf-8", "replace")
        data = payload.rstrip(b"\r\n").rstrip(b"--")
    return name, data


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "RecordGuard/1.0"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            samples = [{"file": f, "title": t, "desc": d} for f, t, d in SAMPLES]
            fields = [{"key": k, "label": v["label"]} for k, v in FIELD_DEFS.items()]
            page = (PAGE
                    .replace("__SAMPLES__", json.dumps(samples, ensure_ascii=False))
                    .replace("__FIELDS__", json.dumps(fields, ensure_ascii=False)))
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            return

        m = re.match(r"^/api/source/(.+)$", path)
        if m:
            name = urllib.parse.unquote(m.group(1))
            if name not in [s[0] for s in SAMPLES]:
                self._send(404, json.dumps({"error": "未知示例"}).encode())
                return
            p = os.path.join(SAMPLES_DIR, name)
            if os.path.exists(p):
                with open(p, "rb") as f:
                    body = f.read()
                fn = urllib.parse.quote(name)
                self._send(200, body,
                           "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                           {"Content-Disposition": "attachment; filename*=UTF-8''" + fn})
                return
            self._send(404, json.dumps({"error": "文件不存在"}).encode())
            return

        m = re.match(r"^/api/download/(.+)$", path)
        if m:
            token = urllib.parse.unquote(m.group(1))
            with _LOCK:
                p = _LAST.get(token)
            if p and os.path.exists(p):
                with open(p, "rb") as f:
                    body = f.read()
                fn = urllib.parse.quote(os.path.basename(p))
                self._send(200, body,
                           "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                           {"Content-Disposition": "attachment; filename*=UTF-8''" + fn})
            else:
                self._send(404, json.dumps({"error": "文件已失效，请重新运行"}).encode())
            return

        self._send(404, json.dumps({"error": "not found"}).encode())

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""

        if path == "/api/run":
            try:
                req = json.loads(body.decode("utf-8") or "{}")
            except Exception:
                self._send(200, json.dumps({"ok": False, "error": "请求格式错误"}).encode())
                return
            sample = req.get("sample") or ""
            manual = _normalize_manual(req.get("manual"))
            if sample not in [s[0] for s in SAMPLES]:
                self._send(200, json.dumps({"ok": False, "error": "未知示例"}).encode())
                return
            src = os.path.join(HERE, "samples", sample)
            self._run_and_reply(src, manual, sample)
            return

        if path == "/api/upload":
            ctype = self.headers.get("Content-Type") or ""
            m = re.search(r"boundary=(.+)$", ctype)
            if "multipart/form-data" not in ctype or not m:
                self._send(200, json.dumps({"ok": False, "error": "上传格式不支持"}).encode())
                return
            name, data = parse_multipart(body, m.group(1).strip().strip('"').encode())
            if not data:
                self._send(200, json.dumps({"ok": False, "error": "没有收到文件内容"}).encode())
                return
            os.makedirs(OUTDIR, exist_ok=True)
            safe = re.sub(r"[^\w\u4e00-\u9fa5.\-]", "_", os.path.basename(name or "upload.docx"))
            tmp = os.path.join(OUTDIR, "_upload_" + safe)
            with open(tmp, "wb") as f:
                f.write(data)
            self._run_and_reply(tmp, {}, safe)
            return

        self._send(404, json.dumps({"error": "not found"}).encode())

    def _run_and_reply(self, src, manual, label):
        try:
            cfg = LLMConfig.from_env()
            res = run_pipeline(src, OUTDIR, manual_map=manual, cfg=cfg, write_file=True)
            payload = _result_to_json(res, label)
            self._send(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        except Exception as e:                       # 兜底：任何异常都变成明确错误
            msg = "%s: %s" % (type(e).__name__, e)
            if os.environ.get("RG_DEBUG"):
                msg += "\n" + traceback.format_exc()
            self._send(200, json.dumps({"ok": False, "error": msg}, ensure_ascii=False).encode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    a = ap.parse_args()

    os.makedirs(OUTDIR, exist_ok=True)
    cfg = LLMConfig.from_env()
    url = "http://%s:%d" % ("127.0.0.1" if a.host in ("0.0.0.0", "") else a.host, a.port)
    print("=" * 62)
    print("记录校验 Agent")
    print("=" * 62)
    print("界面   : %s" % url)
    print("模式   : %s" % ("规则 + 模型（已配置 API）" if cfg.enabled
                          else "规则 + 本地启发式（未配置 API key）"))
    print("依赖   : 标准库 + python-docx，无前端框架")
    print("停止   : Ctrl+C")
    print("=" * 62)

    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    if not a.no_browser:
        try:
            import webbrowser
            threading.Timer(0.7, lambda: webbrowser.open(url)).start()
        except Exception:                                      # noqa: BLE001
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
