"""Parent-facing interactive mastery explorer for the shared math graph."""

from __future__ import annotations

import html
from typing import Any

from qingzi_learning.curriculum.html_shell import render_graph_shell


_COLORS = {
    "domain-number": "#E8F1FF",
    "domain-geometry": "#EEE9FF",
    "domain-statistics": "#E7F7F1",
    "domain-practice": "#FFF1E8",
    "extension": "#F1F3F7",
}


def render_explorer_page(model: dict[str, Any]) -> str:
    positions, height = _layered_positions(model)
    edges = _edges(model, positions)
    nodes = "".join(_node(node, positions[node_id]) for node_id, node in model["nodes"].items())
    audit = _audit_panel(model)
    appendix = _weak_appendix(model)
    overview = _overview(model)
    body = f'''
<div class="explorer-workspace">
  <section id="filter-rail" aria-label="知识图筛选">
    <div class="filter-grid">
      <label>学段<select id="filter-stage"><option value="">全部学段</option>{_options(model["filters"]["stages"])}</select></label>
      <label>年级<select id="filter-grade"><option value="">全部年级</option>{_options(model["filters"]["grades"])}</select></label>
      <label>学期<select id="filter-term"><option value="">全部学期</option><option value="upper">上学期</option><option value="lower">下学期</option></select></label>
      <label>教材与课程<select id="filter-source"><option value="">全部来源</option>{_source_options(model)}</select></label>
      <label>掌握状态<select id="filter-mastery"><option value="">全部状态</option>{_mastery_options()}</select></label>
      <label>核心素养<select id="filter-competency"><option value="">全部素养</option>{_options(model["filters"]["competencies"])}</select></label>
    </div>
    <fieldset><legend>训练来源</legend><label><input type="radio" name="source-type" value="" checked> 全部来源</label><label><input type="radio" name="source-type" value="textbook"> 学校教材</label><label><input type="radio" name="source-type" value="calculation_training"> 计算训练</label><label><input type="radio" name="source-type" value="tutoring"> 补习班</label></fieldset>
  </section>
  {overview}
  <div id="explorer-canvas-wrap">
    <svg id="mastery-graph" width="1500" height="{height}" viewBox="0 0 1500 {height}" xmlns="http://www.w3.org/2000/svg" aria-label="数学掌握知识图谱">
      <defs><marker id="explorer-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#79879d"/></marker></defs>
      <g id="explorer-viewport">{edges}{nodes}</g>
    </svg>
  </div>
</div>
{audit}
{appendix}
'''
    return render_graph_shell(
        title="数学掌握知识图谱",
        mode="explorer",
        payload=model,
        body=body,
        styles=_STYLES,
        script=_SCRIPT,
    )


def _layered_positions(model: dict[str, Any]) -> tuple[dict[str, tuple[float, float]], int]:
    nodes = model["nodes"]

    def depth(node_id: str) -> int:
        value, seen = 0, set()
        current = nodes[node_id]
        while current["parentId"] is not None and current["parentId"] not in seen:
            seen.add(current["parentId"])
            value += 1
            current = nodes[current["parentId"]]
        return value

    columns: dict[int, list[dict[str, Any]]] = {}
    for node in nodes.values():
        columns.setdefault(depth(node["id"]), []).append(node)
    max_items = max(len(items) for items in columns.values())
    height = max(820, 100 + max_items * 70)
    positions: dict[str, tuple[float, float]] = {}
    xs = {0: 170, 1: 500, 2: 850, 3: 1190, 4: 1410}
    for level, items in columns.items():
        ordered = sorted(items, key=lambda item: (item["domain"], item["label"]))
        span = height - 120
        for index, node in enumerate(ordered):
            y = 70 + (index + 0.5) * span / max(1, len(ordered))
            positions[node["id"]] = (xs.get(level, 1410), y)
    return positions, height


def _edges(model: dict[str, Any], positions: dict[str, tuple[float, float]]) -> str:
    result = []
    for node in model["nodes"].values():
        parent = node["parentId"]
        if parent is None:
            continue
        x1, y1 = positions[parent]
        x2, y2 = positions[node["id"]]
        result.append(
            f'<path class="explorer-edge contains" data-edge-kind="contains" data-source="{parent}" '
            f'data-target="{node["id"]}" d="M{x1+120:.1f},{y1:.1f} C{x1+210:.1f},{y1:.1f} {x2-210:.1f},{y2:.1f} {x2-120:.1f},{y2:.1f}"/>'
        )
    for edge in model["edges"]:
        x1, y1 = positions[edge["source"]]
        x2, y2 = positions[edge["target"]]
        result.append(
            f'<path class="explorer-edge relation kind-{edge["kind"]}" data-edge-kind="{edge["kind"]}" '
            f'data-source="{edge["source"]}" data-target="{edge["target"]}" '
            f'd="M{x1:.1f},{y1:.1f} Q{(x1+x2)/2:.1f},{min(y1,y2)-38:.1f} {x2:.1f},{y2:.1f}"/>'
        )
    return "".join(result)


def _node(node: dict[str, Any], point: tuple[float, float]) -> str:
    x, y = point
    state = node["mastery"]["state"]
    fill = _COLORS.get(node["domain"], _COLORS["extension"])
    label = html.escape(node["label"])
    source_types = ",".join(sorted({source["source_type"] for source in node["sources"]}))
    source_ids = ",".join(source["source_id"] for source in node["sources"])
    return (
        f'<g class="explorer-node state-{state}" data-node-id="{node["id"]}" '
        f'data-state="{state}" data-domain="{node["domain"]}" data-stages="{",".join(node["stages"])}" '
        f'data-grades="{",".join(node["grades"])}" data-competencies="{",".join(node["competencies"])}" '
        f'data-source-types="{source_types}" data-source-ids="{source_ids}" role="button" tabindex="0" aria-expanded="true">'
        f'<rect x="{x-120:.1f}" y="{y-26:.1f}" width="240" height="52" rx="13" fill="{fill}"/>'
        f'<text x="{x:.1f}" y="{y-3:.1f}" text-anchor="middle">{label}</text>'
        f'<text class="node-meta" x="{x:.1f}" y="{y+16:.1f}" text-anchor="middle">{_state_label(state)} · 样本 {node["mastery"]["evidence_count"]}</text>'
        '</g>'
    )


def _audit_panel(model: dict[str, Any]) -> str:
    pending = [item for item in model["audit"] if item["status"] != "confirmed"]
    rows = "".join(
        f'<li><strong>{html.escape(item["label"])}</strong> · {_audit_label(item["status"])} · 样本 {item["evidence_count"]}</li>'
        for item in pending
    ) or '<li class="muted">当前没有待整理标签。</li>'
    source_rows = "".join(
        f'<li><strong>{html.escape(item["source_label"])}</strong> · 课程来源待确认 · '
        f'{html.escape(item["provider"])}</li>'
        for item in model["sourceAudit"]
    ) or '<li class="muted">当前没有待确认课程来源。</li>'
    return (
        '<section id="mapping-audit"><h2>待整理映射</h2>'
        '<p>这些标签和课程来源不会影响正式数学掌握图，也不会在孩子视图中显示为已经学过。</p>'
        f'<h3>学习记录标签</h3><ul>{rows}</ul><h3>课程来源</h3><ul>{source_rows}</ul></section>'
    )


def _overview(model: dict[str, Any]) -> str:
    """Render a readable fallback that remains useful without SVG or JavaScript."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for node in model["nodes"].values():
        groups.setdefault(node["domain"], []).append(node)
    sections = []
    for domain_id, nodes in groups.items():
        domain = model["nodes"].get(domain_id)
        title = domain["label"] if domain is not None else "拓展数学"
        buttons = "".join(
            f'<button type="button" data-overview-node-id="{html.escape(node["id"], quote=True)}" '
            f'class="state-{node["mastery"]["state"]}">{html.escape(node["label"])}</button>'
            for node in sorted(nodes, key=lambda item: item["label"])
        )
        sections.append(f'<section><h3>{html.escape(title)}</h3><div>{buttons}</div></section>')
    return (
        '<details id="graph-overview"><summary>全部知识点文字总览（图谱未显示时也可使用）</summary>'
        + "".join(sections)
        + "</details>"
    )


def _weak_appendix(model: dict[str, Any]) -> str:
    rows = []
    for node in model["nodes"].values():
        if node["mastery"]["state"] not in {"weak", "unstable"}:
            continue
        rate = node["mastery"]["weighted_rate"]
        rate_text = "暂无" if rate is None else f"{round(rate * 100)}%"
        rows.append(
            f'<tr data-concept-id="{node["id"]}"><td>{html.escape(node["label"])}</td>'
            f'<td>{_state_label(node["mastery"]["state"])}</td><td>{rate_text}</td>'
            f'<td>{node["mastery"]["evidence_count"]}</td></tr>'
        )
    body = "".join(rows) or '<tr><td colspan="4">当前没有已确认的薄弱知识点。</td></tr>'
    return (
        '<section id="weak-appendix"><h2>当前筛选的薄弱知识点</h2>'
        '<table><thead><tr><th>知识点</th><th>状态</th><th>加权正确率</th><th>证据数</th></tr></thead>'
        f'<tbody>{body}</tbody></table></section>'
    )


def _options(values: list[str]) -> str:
    return "".join(f'<option value="{html.escape(value, quote=True)}">{html.escape(value)}</option>' for value in values)


def _source_options(model: dict[str, Any]) -> str:
    return "".join(
        f'<option value="{html.escape(item["source_id"], quote=True)}">{html.escape(item["source_label"])}</option>'
        for item in model["sources"]
    )


def _mastery_options() -> str:
    values = ["stable", "basic", "unstable", "weak", "evidence_insufficient", "no_data"]
    return "".join(f'<option value="{value}">{_state_label(value)}</option>' for value in values)


def _state_label(state: str) -> str:
    return {
        "stable": "稳定掌握", "basic": "基本掌握", "unstable": "掌握不稳",
        "weak": "明显短板", "evidence_insufficient": "证据不足", "no_data": "尚无数据",
    }.get(state, "尚无数据")


def _audit_label(status: str) -> str:
    return {"pending": "待确认", "unmapped": "未匹配", "cross_subject": "疑似跨学科"}.get(status, status)


_STYLES = r'''
#graph-toolbar{position:sticky;top:0}.explorer-workspace{position:relative;min-height:560px;height:100%;display:flex;flex-direction:column}.filter-grid{display:grid;grid-template-columns:repeat(3,minmax(140px,1fr));gap:9px}.filter-grid label{font-size:12px;color:#5f6d82}.filter-grid select{display:block;width:100%;margin-top:3px;padding:7px;border:1px solid #cbd6e7;border-radius:8px;background:#fff}#filter-rail{padding:12px;border-bottom:1px solid #dce3ef;background:#f9fbff}#filter-rail fieldset{display:flex;gap:16px;flex-wrap:wrap;margin:10px 0 0;border:0;padding:0;color:#5f6d82}#filter-rail legend{float:left;margin-right:12px;font-size:12px;font-weight:700}
#graph-overview{margin:10px 12px;border:1px solid #d5deeb;border-radius:12px;background:#fff}#graph-overview summary{cursor:pointer;padding:11px 13px;font-weight:700;color:#304f91}#graph-overview section{padding:0 13px 10px}#graph-overview h3{margin:7px 0;font-size:14px}#graph-overview section div{display:flex;flex-wrap:wrap;gap:6px}#graph-overview button{border:1px solid #bcc9da;border-radius:999px;background:#f6f8fc;color:#34445d;padding:5px 9px;cursor:pointer}#graph-overview button.state-weak{border-color:#cf5261;background:#fff0f2}#graph-overview button.state-unstable{border-color:#dc8c32;background:#fff7e9}
#explorer-canvas-wrap{flex:1;height:clamp(600px,72vh,980px);min-height:600px;overflow:auto;background:linear-gradient(90deg,rgba(226,233,246,.32) 1px,transparent 1px),linear-gradient(rgba(226,233,246,.32) 1px,transparent 1px);background-size:28px 28px}#mastery-graph{display:block;width:1500px;height:auto;min-width:1500px;touch-action:none}.explorer-edge{fill:none;stroke:#cad4e3;stroke-width:2}.explorer-edge.relation{stroke:#7d8da8;stroke-dasharray:7 5;marker-end:url(#explorer-arrow);opacity:.7}.explorer-edge.kind-applies_to{stroke:#8c79c6}.explorer-edge.kind-prerequisite{stroke:#527fb6}.explorer-edge.kind-deepens_to{stroke:#469778}.explorer-edge.kind-confusable{stroke:#d38a47}
.is-hidden{display:none!important}.explorer-node{cursor:pointer}.explorer-node rect{stroke:#9aa8bd;stroke-width:2.5}.explorer-node text{font-size:14px;font-weight:650;fill:#293952;pointer-events:none}.explorer-node .node-meta{font-size:10px;font-weight:400;fill:#65728a}.explorer-node.state-stable rect{stroke:#45a071}.explorer-node.state-basic rect{stroke:#5d88cf}.explorer-node.state-unstable rect{stroke:#dc8c32;stroke-width:4}.explorer-node.state-weak rect{stroke:#cf5261;stroke-width:5}.explorer-node.state-evidence_insufficient rect,.explorer-node.state-no_data rect{stroke-dasharray:6 4}.explorer-node.is-selected rect{stroke:#304f91;stroke-width:6}.explorer-node.is-dimmed,.explorer-edge.is-dimmed{opacity:.12}
#mapping-audit,#weak-appendix{margin:14px;padding:16px;background:#fff;border:1px solid #dce3ef;border-radius:14px}#mapping-audit h2,#weak-appendix h2{font-size:18px;margin:0 0 6px}#mapping-audit p{color:#66758e}#weak-appendix table{width:100%;border-collapse:collapse}#weak-appendix th,#weak-appendix td{border-bottom:1px solid #e3e8f0;padding:8px;text-align:left}.shortcoming-active .explorer-node:not(.state-weak):not(.state-unstable){opacity:.16}
@media(max-width:960px){.filter-grid{grid-template-columns:repeat(2,minmax(130px,1fr))}#filter-rail.is-collapsed .filter-grid,#filter-rail.is-collapsed fieldset{display:none}.graph-layout{display:block}.graph-side{display:grid;margin-top:10px}}
@media(max-width:620px){.filter-grid{grid-template-columns:1fr}}
@media print{#filter-rail,#mapping-audit,#graph-overview{display:none!important}#explorer-canvas-wrap{height:auto;min-height:150mm;background:none;overflow:hidden}#mastery-graph{width:100%;min-width:0;height:auto}#weak-appendix{page-break-before:always;margin:0;border:0}}
'''


_SCRIPT = r'''
const data=JSON.parse(document.getElementById("graph-data").textContent),toolbar=document.getElementById("graph-toolbar"),details=document.getElementById("graph-details"),graph=document.getElementById("mastery-graph"),statusBox=document.getElementById("graph-status"),rail=document.getElementById("filter-rail");
toolbar.innerHTML=`<button id="toggle-filters">筛选条件</button><button id="shortcoming" aria-pressed="false">短板模式</button><button id="expand-all">全部展开</button><button id="fit-explorer">恢复全貌</button><button id="print-explorer">打印</button>`;
let shortcoming=false,collapsed=new Set();
function announce(text){statusBox.textContent="";requestAnimationFrame(()=>statusBox.textContent=text)}
function ancestors(id,set){let current=data.nodes[id];while(current&&current.parentId){set.add(current.parentId);current=data.nodes[current.parentId]}}
function descendantOfCollapsed(id){let current=data.nodes[id];while(current&&current.parentId){if(collapsed.has(current.parentId))return true;current=data.nodes[current.parentId]}return false}
function applyFilters(){const stage=document.getElementById("filter-stage").value,grade=document.getElementById("filter-grade").value;const term=document.getElementById("filter-term").value;const source=document.getElementById("filter-source").value,mastery=document.getElementById("filter-mastery").value,competency=document.getElementById("filter-competency").value,sourceType=document.querySelector('input[name="source-type"]:checked').value;const keep=new Set();Object.values(data.nodes).forEach(node=>{const sourceIds=node.sources.map(item=>item.source_id),sourceTypes=node.sources.map(item=>item.source_type);let match=(!stage||node.stages.includes(stage))&&(!grade||node.grades.includes(grade))&&(!term||node.sources.some(item=>item.terms.includes(term)))&&(!source||sourceIds.includes(source))&&(!mastery||node.mastery.state===mastery)&&(!competency||node.competencies.includes(competency))&&(!sourceType||sourceTypes.includes(sourceType));if(shortcoming)match=match&&["weak","unstable"].includes(node.mastery.state);if(match){keep.add(node.id);ancestors(node.id,keep);if(shortcoming)data.edges.filter(edge=>edge.kind==="prerequisite"&&edge.target===node.id).forEach(edge=>{keep.add(edge.source);ancestors(edge.source,keep)})}});const visibleIds=new Set();document.querySelectorAll(".explorer-node").forEach(element=>{const visible=keep.has(element.dataset.nodeId)&&!descendantOfCollapsed(element.dataset.nodeId);element.classList.toggle("is-hidden",!visible);if(visible)visibleIds.add(element.dataset.nodeId);element.classList.toggle("is-dimmed",shortcoming&&!["weak","unstable"].includes(element.dataset.state)&&!data.rootIds.includes(element.dataset.nodeId))});document.querySelectorAll(".explorer-edge").forEach(edge=>{const a=document.querySelector(`[data-node-id="${edge.dataset.source}"]`),b=document.querySelector(`[data-node-id="${edge.dataset.target}"]`);edge.classList.toggle("is-hidden",!a||!b||a.classList.contains("is-hidden")||b.classList.contains("is-hidden"))});document.querySelectorAll("#weak-appendix tbody tr[data-concept-id]").forEach(row=>row.classList.toggle("is-hidden",!visibleIds.has(row.dataset.conceptId)));document.getElementById("graph-empty").style.display=visibleIds.size?"none":"grid";announce(`当前显示 ${visibleIds.size} 个知识点`)}
document.querySelectorAll("#filter-rail select,#filter-rail input").forEach(control=>control.addEventListener("change",applyFilters));document.getElementById("toggle-filters").onclick=()=>rail.classList.toggle("is-collapsed");document.getElementById("shortcoming").onclick=event=>{shortcoming=!shortcoming;event.currentTarget.setAttribute("aria-pressed",String(shortcoming));document.body.classList.toggle("shortcoming-active",shortcoming);applyFilters()};document.getElementById("expand-all").onclick=()=>{collapsed.clear();document.querySelectorAll(".explorer-node").forEach(item=>item.setAttribute("aria-expanded","true"));applyFilters()};document.getElementById("print-explorer").onclick=()=>window.print();document.getElementById("fit-explorer").onclick=()=>{graph.setAttribute("viewBox",graph.getAttribute("viewBox").replace(/^.*? .*? /,"0 0 "));graph.scrollIntoView({block:"nearest"})};
function escapeText(value){return String(value).replace(/[&<>"']/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char])}
function trendLabel(value){return ({improving:"上升",steady:"稳定",stable:"稳定",declining:"下降",unknown:"样本不足",no_data:"暂无"})[value]||"样本不足"}
function showNode(id){const node=data.nodes[id];document.querySelectorAll(".explorer-node").forEach(item=>item.classList.toggle("is-selected",item.dataset.nodeId===id));const incoming=data.edges.filter(edge=>edge.target===id),outgoing=data.edges.filter(edge=>edge.source===id);const prerequisites=incoming.filter(edge=>edge.kind==="prerequisite").map(edge=>data.nodes[edge.source].label);const downstream=outgoing.map(edge=>data.nodes[edge.target].label);const sources=node.sources.map(item=>`${item.source_label}（已确认 · ${item.provider}）`).join("、")||"暂无已确认课程映射";const rate=node.mastery.weighted_rate==null?"暂无":`${Math.round(node.mastery.weighted_rate*100)}%`;const evidence=node.mastery.evidence||[];const evidenceHtml=evidence.length?`<ul>${evidence.map(item=>`<li><a href="${escapeText(item.href)}">${escapeText(item.label)}</a></li>`).join("")}</ul>`:"<p>暂无已确认题目。</p>";details.innerHTML=`<h2>${escapeText(node.label)}</h2><p class="muted">${escapeText(node.stages.join("、"))} · ${escapeText(node.grades.join("、")||"拓展")}</p><h3>前置知识路径</h3><p>${escapeText(prerequisites.join("、")||"暂无已核实前置关系")}</p><h3>后续与应用</h3><p>${escapeText(downstream.join("、")||"暂无已核实下游关系")}</p><h3>教材与训练来源</h3><p>${escapeText(sources)}</p><h3>掌握证据</h3><p>加权正确率 ${rate} · 确认证据 ${node.mastery.evidence_count} 题 · 累计走势 ${trendLabel(node.mastery.trend)} · 最近记录 ${escapeText(node.mastery.last_seen_at||"暂无")}</p><h3>确认题目</h3>${evidenceHtml}`;announce(`已选择${node.label}`)}
function setCollapsed(element,value){value?collapsed.add(element.dataset.nodeId):collapsed.delete(element.dataset.nodeId);element.setAttribute("aria-expanded",String(!value));applyFilters()}
document.querySelectorAll(".explorer-node").forEach(element=>{element.addEventListener("click",()=>showNode(element.dataset.nodeId));element.addEventListener("dblclick",()=>setCollapsed(element,!collapsed.has(element.dataset.nodeId)));element.addEventListener("keydown",event=>{if(event.key==="Enter"||event.key===" "){event.preventDefault();showNode(element.dataset.nodeId)}else if(event.key==="ArrowLeft"){event.preventDefault();setCollapsed(element,true)}else if(event.key==="ArrowRight"){event.preventDefault();setCollapsed(element,false)}})});
document.querySelectorAll("[data-overview-node-id]").forEach(button=>button.addEventListener("click",()=>{const id=button.dataset.overviewNodeId;showNode(id);const node=document.querySelector(`[data-node-id="${id}"]`);if(node)node.scrollIntoView({behavior:"smooth",block:"center",inline:"center"})}));applyFilters();
'''
