"""Offline course map plus ordinary Markdown links usable in Obsidian."""

from __future__ import annotations

import html
from pathlib import Path
from urllib.parse import quote

from qingzi_learning.curriculum.catalog import load_catalog, validate_catalog
from qingzi_learning.export.publication import write_output
from qingzi_learning.storage.paths import KnowledgePaths


_TYPES = {"unit": "教材单元", "practice": "综合实践", "math_play": "数学好玩", "review": "总复习"}
_LINKS = {"related": "相关", "application": "应用", "prerequisite": "先修"}


def _md(value: str) -> str:
    """Keep catalog text literal in Markdown, including Obsidian renderers."""
    return html.escape(value, quote=False).replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


class CurriculumExporter:
    def __init__(self, paths: KnowledgePaths, catalog: dict | None = None,
                 *, graph_backlink: bool = False) -> None:
        self.paths = paths
        self.catalog = validate_catalog(catalog) if catalog is not None else load_catalog()
        self.graph_backlink = graph_backlink
        if self.catalog["subject"] not in paths.config.subjects:
            raise ValueError("课程科目不在知识库内")

    def _file(self, name: str) -> Path:
        return self.paths.curriculum_file(self.catalog["subject"], self.catalog["catalog_id"], name)

    def _node_path(self, node: dict) -> Path:
        return self._file(f"系统-{node['node_id']}.md")

    def _user_note_path(self, node: dict) -> Path:
        return self._file(f"我的-{node['node_id']}.md")

    def _index_path(self) -> Path:
        return self._file("系统课程总览.md")

    def entry_path(self) -> Path:
        return self._file("课程知识图.html")

    def expected_paths(self) -> set[Path]:
        return {self.entry_path(), self._index_path(),
                *(self._node_path(node) for node in self.catalog["nodes"])}

    def parent_note_paths(self) -> set[Path]:
        """Link targets owned by parents: check existence, never manifest/hash content."""
        return {self._file("我的课程笔记.md"),
                *(self._user_note_path(node) for node in self.catalog["nodes"])}

    def export(self) -> Path:
        nodes = self.catalog["nodes"]
        self._create_user_note(self._file("我的课程笔记.md"), "# 我的课程笔记\n\n在这里记录对本课程的观察和想法。\n")
        for node in nodes:
            self._create_user_note(self._user_note_path(node),
                                   f"# 我的笔记：{_md(node['title'])}\n\n在这里记录孩子的理解、练习心得和待解决的问题。\n")
        index = self._index_path()
        index_text = [f"# {_md(self.catalog['title'])}", "", "系统生成的教材课程结构；请在[我的课程笔记](我的课程笔记.md)中添加家长笔记，系统不会改写该文件。", "来源：教材封面、扉页及目录实拍；目录标题和起始页为教材目录确认。", "学习来源：" + self._track(), "", "## 教材目录确认", ""]
        for node in nodes:
            index_text.append(f"- [{_md(node['title'])}](系统-{node['node_id']}.md) · 第 {node['page']} 页 · {_TYPES[node['type']]}")
        index_text += ["", "## 教学整理建议（非目录原文）", ""]
        for edge in self.catalog["edges"]:
            index_text.append(f"- [{_md(self._by_id(edge['from'])['title'])}](系统-{edge['from']}.md) → [{_md(self._by_id(edge['to'])['title'])}](系统-{edge['to']}.md)：{_LINKS[edge['type']]}")
        if self._file("课程总览.md").is_file():
            index_text += ["", "[查看旧版课程总览（原样保留）](课程总览.md)"]
        index_text += ["", "作业题目与本课程节点尚未逐题核实关联；统计和错题库维持原样。", ""]
        write_output(index, "\n".join(index_text), self.paths.knowledge_root)
        for node in nodes:
            related = []
            for edge in self.catalog["edges"]:
                if node["node_id"] in (edge["from"], edge["to"]):
                    other = edge["to"] if edge["from"] == node["node_id"] else edge["from"]
                    related.append(f"- [{_md(self._by_id(other)['title'])}](系统-{other}.md)：{_LINKS[edge['type']]}（教学整理建议）")
            body = [f"# {_md(node['title'])}", "", f"教材目录确认：{_TYPES[node['type']]}，第 {node['page']} 页。", "", f"[返回课程总览](系统课程总览.md) · [我的笔记](我的-{node['node_id']}.md)", "", "## 关联", "", *(related or ["暂无已核实关联。"]), "", "目前没有将历史题目自动归入本节点。"]
            if self._file(f"{node['node_id']}.md").is_file():
                body += ["", f"[旧版节点笔记（原样保留）]({node['node_id']}.md)"]
            body += [""]
            write_output(self._node_path(node), "\n".join(body), self.paths.knowledge_root)
        entry = self.entry_path()
        write_output(entry, self._html(), self.paths.knowledge_root)
        return entry

    @staticmethod
    def _create_user_note(path: Path, content: str) -> None:
        """Create parent-owned notes once; no subsequent export opens them for writing."""
        try:
            with path.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
        except FileExistsError:
            pass

    def _by_id(self, node_id: str) -> dict:
        return next(node for node in self.catalog["nodes"] if node["node_id"] == node_id)

    def _track(self) -> str:
        return "学校课程" if self.catalog["track"] == "school" else "兴趣班"

    def _html(self) -> str:
        nodes = self.catalog["nodes"]
        count = len(nodes)
        width, height = 1120, 120 * ((count + 1) // 2) + 90
        point = {node["node_id"]: (55 + (node["order"] - 1) % 2 * 550,
                                       70 + (node["order"] - 1) // 2 * 120) for node in nodes}
        lines = []
        for first, second in zip(nodes, nodes[1:]):
            x1, y1 = point[first["node_id"]]
            x2, y2 = point[second["node_id"]]
            lines.append(f'<path d="M{x1+220} {y1+35} L{x2+220} {y2+35}" stroke="#c7d2df" stroke-width="2" fill="none"/>')
        for edge in self.catalog["edges"]:
            x1, y1 = point[edge["from"]]
            x2, y2 = point[edge["to"]]
            lines.append(f'<path d="M{x1+230} {y1+45} L{x2+230} {y2+45}" stroke="#e18a57" stroke-width="3" stroke-dasharray="7 6" fill="none"/>')
        cards = []
        for node in nodes:
            x, y = point[node["node_id"]]
            title = html.escape(node["title"])
            href = quote("系统-" + node["node_id"] + ".md")
            cards.append(f'<a href="{href}"><rect x="{x}" y="{y}" width="460" height="78" rx="12" fill="#fff" stroke="#b8ccda"/><text x="{x+18}" y="{y+32}" font-size="18" font-weight="600" fill="#193c54">{title}</text><text x="{x+18}" y="{y+57}" font-size="13" fill="#587185">{_TYPES[node["type"]]} · 第 {node["page"]} 页</text></a>')
        relations = "".join(
            f'<li>{html.escape(self._by_id(edge["from"])["title"])} → {html.escape(self._by_id(edge["to"])["title"])}：{_LINKS[edge["type"]]} <small>教学整理建议</small></li>'
            for edge in self.catalog["edges"])
        c = self.catalog
        edition_note = "（封面审定信息，不代表印次）" if c["catalog_id"] == "bnu-math-g5-upper-2024-review" else ""
        graph_link = (
            f'<p><a href="{quote("../primary-math-v1/系统知识图谱总览.md", safe="/.")}">打开双视图数学知识图谱</a></p>'
            if self.graph_backlink else ""
        )
        return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(c["title"])}</title><style>
body{{background:#f5f8fb;color:#193c54;font:16px/1.6 "Microsoft YaHei",sans-serif;margin:0}}main{{max-width:1200px;margin:auto;padding:26px}}h1{{font-size:27px;margin-bottom:6px}}p,small{{color:#587185}}.panel{{background:white;border:1px solid #d8e3eb;border-radius:14px;padding:20px;margin:18px 0;overflow-x:auto}}svg{{max-width:100%;height:auto;min-width:800px}}svg a{{text-decoration:none}}svg a:hover rect{{stroke:#207da6;stroke-width:2}}.key{{display:flex;gap:20px;flex-wrap:wrap}}.dash{{color:#d47743}}a{{color:#176b9a}}li{{margin:8px 0}}@media(max-width:850px){{main{{padding:12px}}}}
</style></head><body><main><h1>{html.escape(c["title"])}</h1><p>{html.escape(self._track())} · {html.escape(c["stage"])} {html.escape(c["grade"])} 年级 · {html.escape(c["publisher"])} · {html.escape(c["edition"])}{edition_note}</p><p>教材目录确认：标题、顺序和起始页。灰线表示目录顺序；<span class="dash">橙色虚线表示教学整理建议</span>，并非教材原文或孩子的掌握情况。</p>{graph_link}<div class="panel"><svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="教材目录与建议关系图">{''.join(lines)}{''.join(cards)}</svg></div><div class="panel"><h2>关系说明</h2><ul>{relations}</ul></div><div class="panel"><h2>继续扩展</h2><p>学校教材、兴趣班、六年级和初中应作为不同课程目录保存，明确版本后再建立跨课程连接。现有错题和知识统计没有因名称相似而被自动归类。</p><p><a href="{quote('系统课程总览.md')}">打开 Obsidian 兼容笔记索引</a></p></div></main></body></html>'''
