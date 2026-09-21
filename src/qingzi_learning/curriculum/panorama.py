"""Child-facing panoramic association map for primary mathematics."""

from __future__ import annotations

import html
import math
from typing import Any

from qingzi_learning.curriculum.html_shell import render_graph_shell


_WIDTH = 1800
_HEIGHT = 1450
_DOMAIN_CENTERS = {
    "domain-number": (470, 360),
    "domain-geometry": (1330, 360),
    "domain-statistics": (470, 960),
    "domain-practice": (1330, 960),
}
_DOMAIN_COLORS = {
    "domain-number": "#E8F1FF",
    "domain-geometry": "#EEE9FF",
    "domain-statistics": "#E7F7F1",
    "domain-practice": "#FFF1E8",
    "extension": "#F1F3F7",
}
_STATE_SYMBOLS = {
    "stable": "✓", "basic": "●", "unstable": "!", "weak": "×",
    "evidence_insufficient": "?", "no_data": "—",
}


def render_panorama_page(model: dict[str, Any]) -> str:
    positions = _positions(model)
    edge_markup = _containment_edges(model, positions) + _relation_edges(model, positions)
    node_markup = "".join(_node(node, positions[node_id]) for node_id, node in model["nodes"].items())
    body = (
        f'<svg id="knowledge-map" viewBox="0 0 {_WIDTH} {_HEIGHT}" '
        'xmlns="http://www.w3.org/2000/svg" aria-label="小学数学全景关联脑图">'
        '<defs><filter id="node-shadow" x="-30%" y="-30%" width="160%" height="160%">'
        '<feDropShadow dx="0" dy="4" stdDeviation="5" flood-color="#536887" flood-opacity=".13"/>'
        '</filter><marker id="arrow" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto">'
        '<path d="M0,0 L9,4.5 L0,9 z" fill="#7c88a1"/></marker></defs>'
        '<g id="map-viewport">'
        '<text x="900" y="58" text-anchor="middle" class="map-title">小学数学知识全景</text>'
        '<text x="900" y="86" text-anchor="middle" class="map-subtitle">主枝告诉我属于哪里 · 横向连线告诉我知识怎样互相帮助</text>'
        + edge_markup
        + node_markup
        + '<text x="900" y="1418" text-anchor="middle" class="extension-caption">拓展数学：补习班与兴趣课程放在这里，并连接回课标知识</text>'
        '</g></svg>'
    )
    return render_graph_shell(
        title="数学知识全景脑图",
        mode="panorama",
        payload=model,
        body=body,
        styles=_STYLES,
        script=_SCRIPT,
    )


def _positions(model: dict[str, Any]) -> dict[str, tuple[float, float]]:
    nodes = model["nodes"]
    result: dict[str, tuple[float, float]] = {}
    for domain, center in _DOMAIN_CENTERS.items():
        members = [node for node in nodes.values() if node["domain"] == domain]
        root = next((node for node in members if node["id"] == domain), None)
        if root:
            result[root["id"]] = center
        themes = sorted((node for node in members if node["kind"] == "theme"), key=lambda item: item["id"])
        for index, node in enumerate(themes):
            angle = -math.pi / 2 + index * (2 * math.pi / max(1, len(themes)))
            result[node["id"]] = (center[0] + 145 * math.cos(angle), center[1] + 115 * math.sin(angle))
        concepts = sorted((node for node in members if node["kind"] == "concept"), key=lambda item: item["id"])
        for index, node in enumerate(concepts):
            angle = -math.pi / 2 + index * (2 * math.pi / max(1, len(concepts)))
            result[node["id"]] = (center[0] + 330 * math.cos(angle), center[1] + 245 * math.sin(angle))

    extension = sorted(
        (node for node in nodes.values() if node["domain"] == "extension"),
        key=lambda item: (0 if item["id"] == "extension-root" else 1, item["id"]),
    )
    if extension:
        result[extension[0]["id"]] = (900, 1278)
        others = extension[1:]
        start = 180
        step = 1440 / max(1, len(others) - 1)
        for index, node in enumerate(others):
            result[node["id"]] = (start + index * step, 1360)
    return result


def _containment_edges(model: dict[str, Any], positions: dict[str, tuple[float, float]]) -> str:
    paths = []
    for node in model["nodes"].values():
        parent_id = node["parentId"]
        if parent_id is None or parent_id not in positions:
            continue
        x1, y1 = positions[parent_id]
        x2, y2 = positions[node["id"]]
        paths.append(
            f'<path class="edge contains" data-edge-kind="contains" data-source="{parent_id}" '
            f'data-target="{node["id"]}" d="M{x1:.1f},{y1:.1f} Q{(x1+x2)/2:.1f},{(y1+y2)/2-18:.1f} {x2:.1f},{y2:.1f}"/>'
        )
    return "".join(paths)


def _relation_edges(model: dict[str, Any], positions: dict[str, tuple[float, float]]) -> str:
    paths = []
    for edge in model["edges"]:
        if edge["source"] not in positions or edge["target"] not in positions:
            continue
        x1, y1 = positions[edge["source"]]
        x2, y2 = positions[edge["target"]]
        importance = int(edge["importance"])
        paths.append(
            f'<path class="edge relation kind-{edge["kind"]} importance-{importance}" '
            f'data-edge-kind="{edge["kind"]}" data-importance="{importance}" '
            f'data-source="{edge["source"]}" data-target="{edge["target"]}" '
            f'd="M{x1:.1f},{y1:.1f} C{x1:.1f},{(y1+y2)/2:.1f} {x2:.1f},{(y1+y2)/2:.1f} {x2:.1f},{y2:.1f}"/>'
        )
    return "".join(paths)


def _node(node: dict[str, Any], point: tuple[float, float]) -> str:
    x, y = point
    width = 190 if node["kind"] in {"domain", "extension_group"} else 168
    height = 68 if node["kind"] == "domain" else 58
    fill = _DOMAIN_COLORS.get(node["domain"], _DOMAIN_COLORS["extension"])
    mastery = node["mastery"]
    state = mastery["state"]
    symbol = _STATE_SYMBOLS.get(state, "—")
    label = html.escape(node["label"])
    grades = ",".join(node["grades"])
    official = "true" if node["official"] else "false"
    evidence = mastery["evidence_count"]
    return (
        f'<g class="map-node state-{state} kind-{node["kind"]}" id="node-{node["id"]}" '
        f'data-node-id="{node["id"]}" data-domain="{node["domain"]}" data-grades="{grades}" '
        f'data-official="{official}" role="button" tabindex="0" aria-label="{label}，{symbol}，样本{evidence}">'
        f'<rect x="{x-width/2:.1f}" y="{y-height/2:.1f}" width="{width}" height="{height}" rx="16" '
        f'fill="{fill}" filter="url(#node-shadow)"/>'
        f'<foreignObject x="{x-width/2+8:.1f}" y="{y-height/2+6:.1f}" width="{width-16}" height="{height-12}">'
        f'<div xmlns="http://www.w3.org/1999/xhtml" class="node-label"><strong>{label}</strong>'
        f'<small><span>{symbol}</span> {"样本 " + str(evidence) if evidence else "尚无学习证据"}</small></div>'
        '</foreignObject></g>'
    )


_STYLES = r'''
@page{size:landscape;margin:8mm}
#knowledge-map{width:100%;height:100%;display:block;touch-action:none;user-select:none;background:radial-gradient(circle at 50% 45%,#fff 0%,#fbfcff 58%,#f5f6fb 100%)}
.map-title{font-size:29px;font-weight:750;fill:#354867}.map-subtitle,.extension-caption{font-size:14px;fill:#7a879b}.extension-caption{font-size:13px}
.edge{fill:none;pointer-events:none}.edge.contains{stroke:#cbd4e2;stroke-width:2}.edge.relation{stroke:#7c88a1;stroke-width:2.6;stroke-dasharray:8 6;marker-end:url(#arrow);opacity:.75}.edge.kind-applies_to{stroke:#8b78c5}.edge.kind-prerequisite{stroke:#4e83bd}.edge.kind-deepens_to{stroke:#43a07b}.edge.kind-confusable{stroke:#d38745}
.is-hidden{display:none!important}.map-node{cursor:pointer}.map-node rect{stroke:#9aa8bd;stroke-width:2.5;transition:stroke-width .16s,filter .16s}.map-node:hover rect,.map-node.is-focused rect{stroke:#355f9e;stroke-width:5}.map-node.is-dimmed,.edge.is-dimmed{opacity:.12}.map-node.is-match rect{stroke:#6e4fc5;stroke-width:6}
.map-node.state-stable rect{stroke:#45a071}.map-node.state-basic rect{stroke:#5d88cf}.map-node.state-unstable rect{stroke:#dc8c32;stroke-width:4}.map-node.state-weak rect{stroke:#cf5261;stroke-width:5}.map-node.state-evidence_insufficient rect,.map-node.state-no_data rect{stroke-dasharray:6 4}
.map-node.kind-domain rect{stroke-width:4}.map-node.kind-domain .node-label strong{font-size:18px}.node-label{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;color:#263650;line-height:1.16}.node-label strong{font-size:14px}.node-label small{font-size:11px;color:#68758b;margin-top:4px}
.scope-group{display:flex;border:1px solid #d6deeb;border-radius:10px;overflow:hidden}.scope-group button{border:0;border-radius:0;border-right:1px solid #d6deeb}.scope-group button:last-child{border-right:0}.scope-group button[aria-pressed="true"]{background:#e9efff;color:#3c5795;font-weight:700}
#map-search{width:min(230px,40vw);padding:8px 10px;border:1px solid #cbd6e7;border-radius:10px}.toolbar-spacer{flex:1}.relation-key{font-size:12px;color:#69768c}.relation-key b{font-weight:650}
@media print{#knowledge-map{height:170mm}.graph-side{page-break-before:auto;display:grid;grid-template-columns:1fr 1fr;margin-top:5mm}.edge.relation.importance-1,.edge.relation.importance-2{display:none}.map-node rect{filter:none}}
'''


_SCRIPT = r'''
const data=JSON.parse(document.getElementById("graph-data").textContent);
const svg=document.getElementById("knowledge-map"),main=document.getElementById("graph-main"),toolbar=document.getElementById("graph-toolbar"),details=document.getElementById("graph-details"),status=document.getElementById("graph-status");
toolbar.innerHTML=`<div class="scope-group" aria-label="观看范围"><button data-scope="all_primary" aria-pressed="true">全部小学</button><button data-scope="current_grade" aria-pressed="false">当前年级</button><button data-scope="current_term" aria-pressed="false">本学期</button></div><label>找知识点 <input id="map-search" type="search" placeholder="例如：分数、面积、可能性"></label><button id="important-links" aria-pressed="true">显示重要联系</button><button id="all-links" aria-pressed="false">显示全部联系</button><span class="toolbar-spacer"></span><button id="fit-map">恢复全貌</button><button id="full-map">全屏</button><button id="print-map">打印</button>`;
const original={x:0,y:0,w:1800,h:1450};let view={...original},drag=null,currentScope="all_primary";
function applyView(){svg.setAttribute("viewBox",`${view.x} ${view.y} ${view.w} ${view.h}`)}applyView();
function announce(text){status.textContent="";requestAnimationFrame(()=>status.textContent=text)}
function visibleForScope(node){if(currentScope==="all_primary")return node.dataset.official==="true"||node.dataset.domain==="extension";if(currentScope==="current_grade")return node.dataset.grades.split(",").includes("5")||node.classList.contains("kind-domain")||node.classList.contains("kind-theme");const item=data.nodes[node.dataset.nodeId];return item.sources.length>0||node.classList.contains("kind-domain")||node.classList.contains("kind-theme")}
function applyScope(){document.querySelectorAll(".map-node").forEach(node=>node.classList.toggle("is-hidden",!visibleForScope(node)));document.querySelectorAll(".edge").forEach(edge=>{const a=document.querySelector(`[data-node-id="${edge.dataset.source}"]`),b=document.querySelector(`[data-node-id="${edge.dataset.target}"]`);edge.classList.toggle("is-hidden",!a||!b||a.classList.contains("is-hidden")||b.classList.contains("is-hidden"))});announce(`已切换观看范围`)}
toolbar.querySelectorAll("[data-scope]").forEach(button=>button.addEventListener("click",()=>{currentScope=button.dataset.scope;toolbar.querySelectorAll("[data-scope]").forEach(item=>item.setAttribute("aria-pressed",String(item===button)));applyScope()}));
function showImportant(important){document.querySelectorAll(".edge.relation").forEach(edge=>edge.style.display=important&&Number(edge.dataset.importance)<3?"none":"");document.getElementById("important-links").setAttribute("aria-pressed",String(important));document.getElementById("all-links").setAttribute("aria-pressed",String(!important))}showImportant(true);
document.getElementById("important-links").onclick=()=>showImportant(true);document.getElementById("all-links").onclick=()=>showImportant(false);
document.getElementById("fit-map").onclick=()=>{view={...original};applyView();announce("已恢复全貌")};document.getElementById("print-map").onclick=()=>window.print();document.getElementById("full-map").onclick=()=>{if(!document.fullscreenElement)main.requestFullscreen?.();else document.exitFullscreen?.()};
document.getElementById("map-search").addEventListener("input",event=>{const term=event.target.value.trim().toLocaleLowerCase();document.querySelectorAll(".map-node").forEach(node=>node.classList.toggle("is-match",Boolean(term)&&data.nodes[node.dataset.nodeId].label.toLocaleLowerCase().includes(term)));});
function showNode(id){const item=data.nodes[id];document.querySelectorAll(".map-node,.edge").forEach(element=>element.classList.remove("is-focused","is-dimmed"));const connected=new Set([id]);data.edges.forEach(edge=>{if(edge.source===id)connected.add(edge.target);if(edge.target===id)connected.add(edge.source)});document.querySelectorAll(".map-node").forEach(node=>{node.classList.toggle("is-focused",node.dataset.nodeId===id);node.classList.toggle("is-dimmed",!connected.has(node.dataset.nodeId))});document.querySelectorAll(".edge").forEach(edge=>edge.classList.toggle("is-dimmed",edge.dataset.source!==id&&edge.dataset.target!==id));const related=[...connected].filter(value=>value!==id).map(value=>data.nodes[value]?.label).filter(Boolean);const sources=item.sources.map(source=>source.source_label).join("、")||"还没有挂接教材或课程";const mastery=item.mastery;const rate=mastery.weighted_rate==null?"暂无":`${Math.round(mastery.weighted_rate*100)}%`;details.innerHTML=`<h2>${escapeText(item.label)}</h2><h3>我在哪里学过</h3><p>${escapeText(sources)}</p><h3>和什么有关</h3><p>${escapeText(related.join("、")||"暂无已核实横向联系")}</p><h3>最近掌握情况</h3><p>加权正确率 ${rate} · 确认证据 ${mastery.evidence_count} 题</p>`;announce(`已选择${item.label}`)}
function escapeText(value){return String(value).replace(/[&<>"']/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char])}
document.querySelectorAll(".map-node").forEach(node=>{node.addEventListener("click",()=>showNode(node.dataset.nodeId));node.addEventListener("keydown",event=>{if(event.key==="Enter"||event.key===" "){event.preventDefault();showNode(node.dataset.nodeId)}})});
svg.addEventListener("wheel",event=>{event.preventDefault();const factor=event.deltaY>0?1.12:.88;const rect=svg.getBoundingClientRect(),px=view.x+(event.clientX-rect.left)/rect.width*view.w,py=view.y+(event.clientY-rect.top)/rect.height*view.h;const nw=Math.min(3000,Math.max(420,view.w*factor)),nh=nw*1450/1800;view={x:px-(px-view.x)*nw/view.w,y:py-(py-view.y)*nh/view.h,w:nw,h:nh};applyView()},{passive:false});
svg.addEventListener("pointerdown",event=>{if(event.target.closest(".map-node"))return;drag={x:event.clientX,y:event.clientY,view:{...view}};svg.setPointerCapture(event.pointerId)});svg.addEventListener("pointermove",event=>{if(!drag)return;const rect=svg.getBoundingClientRect();view.x=drag.view.x-(event.clientX-drag.x)/rect.width*view.w;view.y=drag.view.y-(event.clientY-drag.y)/rect.height*view.h;applyView()});svg.addEventListener("pointerup",()=>drag=null);
main.addEventListener("keydown",event=>{if(event.key!=="+"&&event.key!=="-")return;event.preventDefault();const factor=event.key==="+"?.85:1.15;view.w*=factor;view.h*=factor;applyView()});
'''
