# -*- coding: utf-8 -*-
"""自测：在进程内起服务，跑三个示例，验证接口与下载"""
import json
import os
import sys
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import app as webapp
from http.server import ThreadingHTTPServer

PORT = 8123
srv = ThreadingHTTPServer(("127.0.0.1", PORT), webapp.Handler)
th = threading.Thread(target=srv.serve_forever, daemon=True)
th.start()
time.sleep(0.6)

BASE = "http://127.0.0.1:%d" % PORT


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return r.status, r.read()


def post_json(path, obj):
    data = json.dumps(obj).encode()
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, json.loads(r.read().decode())


try:
    st, body = get("/")
    print("[1] GET /           -> %d, %d bytes, 含 title=%s" %
          (st, len(body), "记录校验 Agent" in body.decode()))

    for smp in ("sample_clean.docx", "sample_errors.docx", "sample_messy.docx"):
        st, o = post_json("/api/run", {"sample": smp})
        n = len(o.get("issues") or [])
        nc = len(o.get("needs_confirm") or [])
        print("[2] run %-20s -> mode=%-14s issues=%d needs_confirm=%d tables=%d rows=%d"
              % (smp, o.get("mode"), n, nc, o.get("n_tables", 0), o.get("n_rows", 0)))
        if o.get("download") and o.get("token"):
            st2, blob = get("/api/download/" + urllib.parse.quote(o["token"]))
            print("      下载 -> %d, %d bytes, zip=%s" %
                  (st2, len(blob), blob[:2] == b"PK"))

    # 人工指定列后重跑 messy
    st, o = post_json("/api/run", {"sample": "sample_messy.docx",
                                   "manual": {"0": {"6": "ct"}}})
    print("[3] messy + 人工指定第7列=Ct值 -> mode=%s issues=%d"
          % (o.get("mode"), len(o.get("issues") or [])))

    # 异常路径
    st, o = post_json("/api/run", {"sample": "不存在.docx"})
    print("[4] 未知示例 -> ok=%s error=%s" % (o.get("ok"), o.get("error")))

finally:
    srv.shutdown()
    print("done")
