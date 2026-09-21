"""Safe, responsive, self-contained HTML shell for offline graph pages."""

from __future__ import annotations

import html
import json
from typing import Any


def safe_embedded_json(payload: dict[str, Any]) -> str:
    """Serialize JSON safely inside a script element without changing its value."""
    return (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def render_graph_shell(
    *,
    title: str,
    mode: str,
    payload: dict[str, Any],
    body: str,
    styles: str,
    script: str,
) -> str:
    if mode not in {"panorama", "explorer"}:
        raise ValueError("知识图谱页面模式无效")
    safe_title = html.escape(title, quote=True)
    embedded = safe_embedded_json(payload)
    return f'''<!doctype html>
<html lang="zh-CN" data-mode="{mode}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light">
<title>{safe_title}</title>
<style>
:root{{--ink:#24324a;--muted:#66758e;--paper:#f7f7fc;--card:#fff;--line:#dce3ef;--blue:#6e91d8;--purple:#8c79c6;--shadow:0 12px 35px rgba(57,73,112,.10)}}
*{{box-sizing:border-box}}html,body{{height:100%}}body{{margin:0;background:linear-gradient(145deg,#f8f9ff 0%,#f7fbff 52%,#fbf8ff 100%);color:var(--ink);font:15px/1.55 "Microsoft YaHei","Segoe UI",sans-serif}}
a{{color:#426fb2}}button,select,input{{font:inherit}}button{{border:1px solid #cbd6e7;border-radius:10px;background:#fff;color:var(--ink);padding:8px 12px;cursor:pointer}}button:hover{{border-color:#7896cb;background:#f5f8ff}}button:focus-visible,select:focus-visible,input:focus-visible,[tabindex]:focus-visible{{outline:3px solid #a9c7ff;outline-offset:2px}}
.skip-link{{position:fixed;left:12px;top:-60px;z-index:100;background:#1f3150;color:#fff;padding:10px 14px;border-radius:8px}}.skip-link:focus{{top:12px}}
.page-header{{display:flex;align-items:flex-end;justify-content:space-between;gap:20px;padding:18px 24px 12px}}.page-header h1{{font-size:25px;margin:0}}.page-header p{{margin:4px 0 0;color:var(--muted)}}
.graph-layout{{display:grid;grid-template-columns:minmax(0,1fr) 280px;gap:14px;padding:0 20px 20px;min-height:calc(100vh - 86px)}}
.graph-stage{{min-width:0;background:rgba(255,255,255,.88);border:1px solid var(--line);border-radius:18px;box-shadow:var(--shadow);display:flex;flex-direction:column;overflow:hidden}}
#graph-toolbar{{position:relative;z-index:4;display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:12px;border-bottom:1px solid var(--line);background:rgba(255,255,255,.94)}}
#graph-main{{position:relative;flex:1;min-height:620px;overflow:hidden}}#graph-empty{{display:none;position:absolute;inset:0;place-items:center;color:var(--muted);background:rgba(255,255,255,.9);z-index:5}}
.graph-side{{display:flex;flex-direction:column;gap:12px}}#graph-legend,#graph-details{{background:rgba(255,255,255,.92);border:1px solid var(--line);border-radius:16px;padding:15px;box-shadow:var(--shadow)}}#graph-legend h2,#graph-details h2{{font-size:16px;margin:0 0 10px}}.legend-grid{{display:grid;grid-template-columns:1fr 1fr;gap:7px;font-size:13px}}.legend-item{{display:flex;align-items:center;gap:7px}}.legend-dot{{width:13px;height:13px;border-radius:50%;border:3px solid #9baac1;background:#fff}}.legend-dot.stable{{border-color:#45a071}}.legend-dot.basic{{border-color:#5d88cf}}.legend-dot.unstable{{border-color:#dc8c32}}.legend-dot.weak{{border-color:#cf5261}}.legend-dot.evidence_insufficient,.legend-dot.no_data{{border-style:dashed;border-color:#98a2b3}}
#graph-details{{flex:1;overflow:auto}}#graph-details .muted{{color:var(--muted)}}#graph-status{{position:fixed;left:-10000px;width:1px;height:1px;overflow:hidden}}
@media(max-width:980px){{.graph-layout{{grid-template-columns:1fr}}.graph-side{{display:grid;grid-template-columns:1fr 1fr}}#graph-main{{min-height:540px}}}}
@media(max-width:620px){{.page-header{{padding:14px 12px}}.page-header h1{{font-size:20px}}.graph-layout{{padding:0 8px 10px}}.graph-side{{display:block}}#graph-legend{{margin-bottom:10px}}#graph-main{{min-height:470px}}}}
@media(prefers-reduced-motion:reduce){{*,*::before,*::after{{scroll-behavior:auto!important;animation-duration:.001ms!important;transition-duration:.001ms!important}}}}
@media print{{body{{background:#fff}}.skip-link,#graph-toolbar{{display:none!important}}.page-header{{padding:0 0 8px}}.graph-layout{{display:block;padding:0}}.graph-stage,#graph-legend,#graph-details{{box-shadow:none;border-color:#999}}#graph-main{{min-height:0;height:175mm}}.graph-side{{display:block;page-break-before:always}}}}
{styles}
</style>
</head>
<body>
<a class="skip-link" href="#graph-main">跳到知识图</a>
<header class="page-header"><div><h1>{safe_title}</h1><p>同一知识底座 · 掌握状态来自已确认学习证据</p></div></header>
<div class="graph-layout">
  <section class="graph-stage" aria-label="知识图谱画布">
    <div id="graph-toolbar" role="toolbar" aria-label="知识图操作"></div>
    <main id="graph-main" tabindex="0">{body}<div id="graph-empty">当前筛选下没有可显示的知识点</div></main>
  </section>
  <aside class="graph-side">
    <section id="graph-legend" aria-label="图例"><h2>掌握状态</h2><div class="legend-grid">
      <span class="legend-item"><i class="legend-dot stable"></i>稳定掌握</span>
      <span class="legend-item"><i class="legend-dot basic"></i>基本掌握</span>
      <span class="legend-item"><i class="legend-dot unstable"></i>掌握不稳</span>
      <span class="legend-item"><i class="legend-dot weak"></i>明显短板</span>
      <span class="legend-item"><i class="legend-dot evidence_insufficient"></i>证据不足</span>
      <span class="legend-item"><i class="legend-dot no_data"></i>尚无数据</span>
    </div></section>
    <section id="graph-details" aria-label="知识点详情"><h2>知识点详情</h2><p class="muted">点击一个知识点，查看它与其他知识的联系、学习来源和掌握证据。</p></section>
  </aside>
</div>
<div id="graph-status" aria-live="polite"></div>
<script type="application/json" id="graph-data">{embedded}</script>
<script>"use strict";{script}</script>
</body></html>'''
