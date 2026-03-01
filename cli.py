"""Full-screen CLI application for debugging the GraphRAG Service (Neo4j).

Usage:
    venv/bin/python cli.py [--api URL]

Commands (type in the search bar):
    <text>              Semantic search query
    /node <id>          Inspect a single node
    /nb <id> [hops]     Neighborhood subgraph
    /stats              Graph statistics
    /set <param> <val>  Adjust query parameters
    /filter <types>     Filter by node type (comma-separated)
    /build              Trigger graph (re)build
    /help               Show available commands
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    RichLog,
    Static,
)

# ── API base URL ──────────────────────────────────────────────────────────────

API_BASE = os.environ.get("GRAPHRAG_API", "http://localhost:8000")
for _i, _a in enumerate(sys.argv):
    if _a == "--api" and _i + 1 < len(sys.argv):
        API_BASE = sys.argv[_i + 1]


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _get(path: str) -> dict:
    with urllib.request.urlopen(f"{API_BASE}{path}", timeout=30) as r:
        return json.loads(r.read())


def _post(path: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


# ── Help text ─────────────────────────────────────────────────────────────────

HELP_TEXT = """\
[bold cyan]Commands[/]

  [bold]<query>[/]               Semantic search
  [bold]/node <id>[/]            Inspect a node
  [bold]/nb <id> [hops][/]       Neighborhood subgraph
  [bold]/stats[/]                Graph statistics
  [bold]/set <param> <val>[/]    Change query parameter
  [bold]/filter <types>[/]       Node type filter (comma-sep, empty=clear)
  [bold]/build[/]                Trigger graph (re)build
  [bold]/help[/]                 This help screen

[bold cyan]Query Parameters (/set)[/]

  [bold]top_k[/]        Seed nodes count       (default: 10)
  [bold]hops[/]         Expansion hops         (default: 2)
  [bold]threshold[/]    Min similarity score   (default: 0.3)
  [bold]max_ctx[/]      Max context chars      (default: 8000)

[bold cyan]Keyboard Shortcuts[/]

  [bold]/[/]             Focus search bar
  [bold]s[/]             Show stats
  [bold]b[/]             Go back (node navigation)
  [bold]Ctrl+L[/]        Clear screen
  [bold]q[/]             Quit
  [bold]Enter[/]         Inspect selected node / edge target
"""


# ── App ───────────────────────────────────────────────────────────────────────

class GraphRAGCLI(App):
    """Full-screen GraphRAG debug CLI — Neo4j Service."""

    TITLE = "GraphRAG Service CLI"
    SUB_TITLE = API_BASE

    CSS = """
    Screen {
        layout: vertical;
    }

    #search-bar {
        dock: top;
        height: 3;
        padding: 0 1;
        background: $surface;
    }

    #search-input {
        width: 100%;
    }

    #main-area {
        height: 1fr;
    }

    #left-panel {
        width: 2fr;
        min-width: 30;
        border-right: thick $primary 50%;
    }

    #right-panel {
        width: 3fr;
        min-width: 50;
    }

    #seeds-table {
        height: 1fr;
    }

    #detail-log {
        height: 1fr;
        scrollbar-gutter: stable;
    }

    #summary-bar {
        dock: bottom;
        height: 1;
        padding: 0 1;
        background: $primary-background;
        color: $text;
    }

    .section-label {
        background: $primary;
        color: $text;
        padding: 0 1;
        text-style: bold;
        width: 100%;
        height: 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", priority=False),
        Binding("ctrl+l", "clear_screen", "Clear"),
        Binding("s", "show_stats", "Stats", priority=False),
        Binding("slash", "focus_search", "/Search", key_display="/"),
        Binding("b", "go_back", "Back", priority=False),
    ]

    # Adjustable query parameters
    top_k: reactive[int] = reactive(10)
    expand_hops: reactive[int] = reactive(2)
    score_threshold: reactive[float] = reactive(0.3)
    max_context_chars: reactive[int] = reactive(8000)

    # Node type filter (None = no filter)
    _node_type_filter: list[str] | None = None

    # Navigation stack for back-navigation (node IDs)
    _nav_stack: list[str] = []

    # ── Layout ────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="search-bar"):
            yield Input(
                placeholder="Search query or /help for commands…",
                id="search-input",
            )
        with Horizontal(id="main-area"):
            with Vertical(id="left-panel"):
                yield Static("RESULTS", classes="section-label")
                yield DataTable(id="seeds-table")
            with Vertical(id="right-panel"):
                yield Static("DETAIL", classes="section-label")
                yield RichLog(
                    id="detail-log", wrap=True, markup=True, highlight=True,
                )
        yield Static("Connecting…", id="summary-bar")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#seeds-table", DataTable)
        table.add_columns("Score", "ID", "Type")
        table.cursor_type = "row"
        self._nav_stack = []

    def _set_left_label(self, text: str) -> None:
        """Update the left panel section label."""
        label = self.query_one("#left-panel > .section-label", Static)
        label.update(text)
        self.query_one("#search-input", Input).focus()
        self._check_health()

    # ── Helpers ───────────────────────────────────────────────────────────

    def _set_summary(self, text: str) -> None:
        self.query_one("#summary-bar", Static).update(text)

    def _set_detail_title(self, text: str) -> None:
        labels = self.query(".section-label")
        if len(labels) > 1:
            labels[1].update(text)

    def _show_welcome(self) -> None:
        log = self.query_one("#detail-log", RichLog)
        log.write(Panel(
            HELP_TEXT,
            title="[bold]GraphRAG Debug CLI[/]",
            border_style="cyan",
            expand=True,
        ))

    # ── Health check ──────────────────────────────────────────────────────

    @work(thread=True)
    def _check_health(self) -> None:
        try:
            h = _get("/health")
            if h["graph_loaded"]:
                msg = (
                    f"[green]●[/] {h['nodes']} nodes, {h['edges']} edges "
                    f"— Built: {str(h.get('built_at', '?'))[:19]}"
                )
            else:
                msg = "[yellow]●[/] Connected — no graph loaded (POST /build)"
            self.call_from_thread(self._set_summary, msg)
            self.call_from_thread(self._show_welcome)
        except Exception:
            self.call_from_thread(
                self._set_summary,
                f"[red]●[/] Cannot reach {API_BASE}",
            )

    # ── Input dispatcher ──────────────────────────────────────────────────

    @on(Input.Submitted, "#search-input")
    def on_search_submit(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return

        if text == "/help":
            log = self.query_one("#detail-log", RichLog)
            log.clear()
            self._show_welcome()
        elif text == "/stats":
            self._do_stats()
        elif text.startswith("/node "):
            self._do_node(text[6:].strip())
        elif text.startswith("/nb "):
            parts = text.split()
            nid = parts[1] if len(parts) > 1 else ""
            hops = int(parts[2]) if len(parts) > 2 else 1
            self._do_neighborhood(nid, hops)
        elif text.startswith("/set "):
            self._handle_set(text[5:].strip())
        elif text.startswith("/filter"):
            self._handle_filter(text[7:].strip())
        elif text == "/build":
            self._do_build()
        else:
            self._do_query(text)

    def _handle_set(self, args: str) -> None:
        parts = args.split(None, 1)
        if len(parts) != 2:
            self._set_summary("[red]Usage:[/] /set <param> <value>")
            return
        param, val = parts
        try:
            if param == "top_k":
                self.top_k = int(val)
            elif param == "hops":
                self.expand_hops = int(val)
            elif param == "threshold":
                self.score_threshold = float(val)
            elif param == "max_ctx":
                self.max_context_chars = int(val)
            else:
                self._set_summary(
                    f"[red]Unknown param:[/] {param}  "
                    "(top_k, hops, threshold, max_ctx)"
                )
                return
            self._set_summary(f"[green]✓[/] {param} = {val}")
        except ValueError:
            self._set_summary(f"[red]Invalid value:[/] {val}")

    def _handle_filter(self, args: str) -> None:
        """Set or clear the node_type_filter."""
        if not args:
            self._node_type_filter = None
            self._set_summary("[green]✓[/] Node type filter cleared")
        else:
            types = [t.strip() for t in args.split(",") if t.strip()]
            self._node_type_filter = types or None
            self._set_summary(
                f"[green]✓[/] Filter: {', '.join(types)}" if types
                else "[green]✓[/] Node type filter cleared"
            )

    # ── Build ─────────────────────────────────────────────────────────────

    @work(thread=True)
    def _do_build(self) -> None:
        self.call_from_thread(
            self._set_summary, "[yellow]⟳[/] Building graph … (this may take a while)",
        )
        try:
            result = _post("/build", {"clean": True})
            msg = result.get("message", "done")
            self.call_from_thread(
                self._set_summary, f"[green]✓[/] Build: {msg}",
            )
        except Exception as e:
            self.call_from_thread(self._set_summary, f"[red]✗[/] Build failed: {e}")

    # ── Query ─────────────────────────────────────────────────────────────

    @work(thread=True)
    def _do_query(self, query: str) -> None:
        self.call_from_thread(
            self._set_summary, f"[yellow]⟳[/] Searching: {query}",
        )
        try:
            body: dict = {
                "query": query,
                "top_k": self.top_k,
                "expand_hops": self.expand_hops,
                "score_threshold": self.score_threshold,
                "max_context_chars": self.max_context_chars,
                "include_context": True,
            }
            if self._node_type_filter:
                body["node_type_filter"] = self._node_type_filter
            result = _post("/query", body)
            # Fetch full node data for every seed
            node_details: dict[str, dict] = {}
            for s in result.get("seed_nodes", []):
                nid = s["node_id"]
                try:
                    safe = urllib.parse.quote(nid, safe="")
                    node_details[nid] = _get(f"/node/{safe}")
                except Exception:
                    pass
            self.call_from_thread(self._show_query_result, result, node_details)
        except Exception as e:
            self.call_from_thread(self._set_summary, f"[red]✗[/] {e}")

    def _show_query_result(self, r: dict, node_details: dict[str, dict] | None = None) -> None:
        node_details = node_details or {}
        self._nav_stack.clear()

        # ── Seeds table ───────────────────────────────────────────────
        self._set_left_label("RESULTS")
        table = self.query_one("#seeds-table", DataTable)
        table.clear()
        for s in r["seed_nodes"]:
            table.add_row(
                f"{s['score']:.3f}",
                s["node_id"],
                s.get("node_type", ""),
                key=s["node_id"],
            )

        # ── Detail panel ──────────────────────────────────────────────
        log = self.query_one("#detail-log", RichLog)
        log.clear()
        self._set_detail_title("QUERY RESULT")

        # Summary box
        et = r.get("edge_types", {})
        et_str = "  ".join(f"[bold]{k}[/]:{v}" for k, v in et.items())
        log.write(Panel(
            f"[bold]Query:[/] {escape(r['query'])}\n"
            f"[bold]Seeds:[/] {len(r['seed_nodes'])}  "
            f"[bold]Expanded:[/] {r['expanded_count']}  "
            f"[bold]Subgraph:[/] {r['subgraph_nodes']}n / "
            f"{r['subgraph_edges']}e\n"
            f"[bold]Edge types:[/] {et_str}\n"
            f"[bold]Latency:[/] [green]{r['latency_ms']:.0f}ms[/]",
            title="Summary",
            border_style="blue",
        ))

        # ── Seed Nodes — full data ────────────────────────────────────
        for s in r["seed_nodes"]:
            nid = s["node_id"]
            nd = node_details.get(nid)
            score = s["score"]
            ntype = s.get("node_type", "")

            # Colour by type
            if ntype == "karar_gerekce":
                style = "magenta"
            elif ntype == "karar":
                style = "yellow"
            elif ntype == "fikra":
                style = "cyan"
            elif ntype == "madde":
                style = "green"
            else:
                style = "white"

            if nd:
                # Full text
                text = nd.get("embed_text", "")
                parts = [f"[bold]Score:[/] {score:.4f}  [bold]Type:[/] [{style}]{escape(ntype)}[/{style}]\n"]
                parts.append(escape(text))

                # Metadata
                meta = nd.get("metadata", {})
                if meta:
                    parts.append("\n")
                    for k, v in meta.items():
                        if isinstance(v, list):
                            vs = ", ".join(str(x) for x in v)
                        else:
                            vs = str(v)
                        parts.append(f"  [dim]{escape(str(k))}:[/] {escape(vs)}")

                # Edge summary
                out_e = nd.get("out_edges", [])
                in_e = nd.get("in_edges", [])
                if out_e or in_e:
                    edge_types: dict[str, int] = {}
                    for e in out_e + in_e:
                        et2 = e.get("edge_type", "?")
                        edge_types[et2] = edge_types.get(et2, 0) + 1
                    edge_summary = "  ".join(f"{k}:{v}" for k, v in edge_types.items())
                    parts.append(f"\n  [dim]Edges ({len(out_e)} out, {len(in_e)} in):[/] {edge_summary}")

                log.write(Panel(
                    "\n".join(parts),
                    title=f"[{style}]{escape(nid)}[/{style}]",
                    border_style=style,
                    subtitle=f"[dim]/node {escape(nid)}[/]",
                ))
            else:
                # Fallback: just preview
                log.write(Panel(
                    f"[bold]Score:[/] {score:.4f}  [bold]Type:[/] [{style}]{escape(ntype)}[/{style}]\n"
                    f"{escape(s.get('text_preview', ''))}",
                    title=f"[{style}]{escape(nid)}[/{style}]",
                    border_style=style,
                ))

        # ── Expanded context (non-seed nodes) ─────────────────────────
        ctx = r.get("context", "")
        if ctx:
            # Collect seed IDs to skip them (already shown above)
            seed_ids = {s["node_id"] for s in r["seed_nodes"]}
            expanded_sections = []
            for sec in ctx.split("\n---\n"):
                sec = sec.strip()
                if not sec:
                    continue
                lines = sec.split("\n", 1)
                hdr = lines[0]
                # Extract node id from header like "[TYPE] NODE_ID"
                hdr_parts = hdr.split("]", 1)
                ctx_nid = hdr_parts[1].strip() if len(hdr_parts) > 1 else ""
                if ctx_nid in seed_ids:
                    continue
                expanded_sections.append((hdr, lines[1] if len(lines) > 1 else ""))

            if expanded_sections:
                log.write(Panel(
                    f"[bold]{len(expanded_sections)} expanded nodes[/] (via graph traversal)",
                    border_style="dim",
                ))
                for hdr, body in expanded_sections:
                    if "[KARAR_GEREKCE]" in hdr:
                        style = "magenta"
                    elif "[KARAR]" in hdr:
                        style = "yellow"
                    elif "[FIKRA]" in hdr:
                        style = "cyan"
                    elif "[MADDE]" in hdr:
                        style = "green"
                    else:
                        style = "white"
                    log.write(Panel(
                        escape(body[:600]) if body else escape(hdr),
                        title=f"[{style}]{escape(hdr)}[/{style}]",
                        border_style=f"dim {style}",
                    ))

        self._set_summary(
            f"[green]✓[/] {len(r['seed_nodes'])} seeds, "
            f"{r['subgraph_nodes']}n/{r['subgraph_edges']}e, "
            f"{r['latency_ms']:.0f}ms — "
            "Select a row to inspect full node"
        )

    # ── Node detail ───────────────────────────────────────────────────────

    @on(DataTable.RowSelected, "#seeds-table")
    def on_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key and event.row_key.value:
            node_id = str(event.row_key.value)
            # Push current node (from detail title) onto stack for back-nav
            detail_label = self.query_one("#right-panel > .section-label", Static)
            cur = detail_label.content
            if isinstance(cur, str) and cur.startswith("NODE: "):
                self._nav_stack.append(cur[6:])
            self._do_node(node_id)

    @work(thread=True)
    def _do_node(self, node_id: str) -> None:
        self.call_from_thread(
            self._set_summary, f"[yellow]⟳[/] Loading: {node_id}",
        )
        try:
            safe_id = urllib.parse.quote(node_id, safe="")
            node = _get(f"/node/{safe_id}")
            self.call_from_thread(self._show_node, node)
        except urllib.error.HTTPError:
            self.call_from_thread(
                self._set_summary, f"[red]✗[/] Node not found: {node_id}",
            )
        except Exception as e:
            self.call_from_thread(self._set_summary, f"[red]✗[/] {e}")

    def _show_node(self, n: dict) -> None:
        log = self.query_one("#detail-log", RichLog)
        log.clear()
        self._set_detail_title(f"NODE: {n['node_id']}")

        # Basic info
        log.write(Panel(
            f"[bold]ID:[/] {escape(n['node_id'])}\n"
            f"[bold]Type:[/] [cyan]{escape(n['node_type'])}[/]\n\n"
            f"{escape(n.get('embed_text', ''))}",
            title="Node Info",
            border_style="blue",
        ))

        # Metadata
        meta = n.get("metadata", {})
        if meta:
            lines = []
            for k, v in meta.items():
                if isinstance(v, list):
                    vs = ", ".join(str(x) for x in v)
                else:
                    vs = str(v)
                lines.append(f"[bold]{escape(str(k))}:[/] {escape(vs)}")
            log.write(Panel(
                "\n".join(lines), title="Metadata", border_style="green",
            ))

        # Out-edges (still shown in detail panel)
        out_edges = n.get("out_edges", [])
        if out_edges:
            t = Table(title="Out-edges", expand=True, show_lines=False)
            t.add_column("Target", style="cyan", ratio=3)
            t.add_column("Type", style="yellow", ratio=2)
            t.add_column("Weight", justify="right", ratio=1)
            for e in out_edges:
                t.add_row(
                    e["target"],
                    e.get("edge_type", ""),
                    f"{e.get('weight', 1.0):.1f}",
                )
            log.write(t)

        # In-edges (still shown in detail panel)
        in_edges = n.get("in_edges", [])
        if in_edges:
            t = Table(title="In-edges", expand=True, show_lines=False)
            t.add_column("Source", style="cyan", ratio=3)
            t.add_column("Type", style="yellow", ratio=2)
            t.add_column("Weight", justify="right", ratio=1)
            for e in in_edges:
                t.add_row(
                    e["source"],
                    e.get("edge_type", ""),
                    f"{e.get('weight', 1.0):.1f}",
                )
            log.write(t)

        # ── Populate left panel with connected nodes (clickable) ──────
        self._set_left_label(
            f"EDGES ({len(out_edges)} out, {len(in_edges)} in) — select to navigate"
        )
        table = self.query_one("#seeds-table", DataTable)
        table.clear()
        seen: set[str] = set()
        for e in out_edges:
            tid = e["target"]
            if tid not in seen:
                seen.add(tid)
                table.add_row(
                    f"→ {e.get('edge_type', '')}",
                    tid,
                    f"{e.get('weight', 1.0):.1f}",
                    key=tid,
                )
        for e in in_edges:
            sid = e["source"]
            if sid not in seen:
                seen.add(sid)
                table.add_row(
                    f"← {e.get('edge_type', '')}",
                    sid,
                    f"{e.get('weight', 1.0):.1f}",
                    key=sid,
                )

        total = len(in_edges) + len(out_edges)
        back_hint = "  |  [b] back" if self._nav_stack else ""
        self._set_summary(
            f"[green]✓[/] {n['node_id']} ({n['node_type']}) — "
            f"{total} edges, {len(seen)} unique targets  |  "
            f"Select edge to navigate{back_hint}"
        )

    # ── Neighborhood ──────────────────────────────────────────────────────

    @work(thread=True)
    def _do_neighborhood(self, node_id: str, hops: int = 1) -> None:
        self.call_from_thread(
            self._set_summary,
            f"[yellow]⟳[/] Neighborhood: {node_id} ({hops}h)",
        )
        try:
            safe_id = urllib.parse.quote(node_id, safe="")
            nb = _get(f"/node/{safe_id}/neighborhood?hops={hops}")
            self.call_from_thread(self._show_neighborhood, nb)
        except Exception as e:
            self.call_from_thread(self._set_summary, f"[red]✗[/] {e}")

    def _show_neighborhood(self, nb: dict) -> None:
        log = self.query_one("#detail-log", RichLog)
        log.clear()
        self._set_detail_title(f"NEIGHBORHOOD: {nb['center']}")

        nodes = nb.get("nodes", [])
        edges = nb.get("edges", [])

        # Type counts
        tc: dict[str, int] = {}
        for nd in nodes:
            t = nd.get("node_type", "?")
            tc[t] = tc.get(t, 0) + 1

        log.write(Panel(
            f"[bold]Center:[/] {escape(nb['center'])}\n"
            f"[bold]Hops:[/] {nb['hops']}  "
            f"[bold]Nodes:[/] {len(nodes)}  "
            f"[bold]Edges:[/] {len(edges)}\n"
            f"[bold]Types:[/] "
            + ", ".join(f"{k}:{v}" for k, v in tc.items()),
            title="Summary",
            border_style="blue",
        ))

        # Nodes table
        t = Table(title="Nodes", expand=True)
        t.add_column("ID", style="cyan", ratio=2)
        t.add_column("Type", style="yellow", ratio=1)
        t.add_column("Preview", ratio=4)
        for nd in nodes:
            t.add_row(
                nd["node_id"],
                nd.get("node_type", ""),
                nd.get("text_preview", "")[:80],
            )
        log.write(t)

        # Edges table
        t = Table(title="Edges", expand=True)
        t.add_column("Source", style="cyan", ratio=2)
        t.add_column("", ratio=0)
        t.add_column("Target", style="green", ratio=2)
        t.add_column("Type", style="yellow", ratio=1)
        for e in edges:
            t.add_row(
                e["source"], "→", e["target"], e.get("edge_type", ""),
            )
        log.write(t)

        self._set_summary(
            f"[green]✓[/] {nb['center']} — "
            f"{len(nodes)}n, {len(edges)}e, {nb['hops']}h"
        )

    # ── Stats ─────────────────────────────────────────────────────────────

    @work(thread=True)
    def _do_stats(self) -> None:
        self.call_from_thread(self._set_summary, "[yellow]⟳[/] Loading stats…")
        try:
            stats = _get("/stats")
            self.call_from_thread(self._show_stats, stats)
        except Exception as e:
            self.call_from_thread(self._set_summary, f"[red]✗[/] {e}")

    def _show_stats(self, stats: dict) -> None:
        log = self.query_one("#detail-log", RichLog)
        log.clear()
        self._set_detail_title("GRAPH STATISTICS")

        m = stats.get("manifest", {})
        g = stats.get("graph", {})

        # Build manifest (if present)
        if m:
            log.write(Panel(
                f"[bold]Version:[/] {m.get('version', '?')}  "
                f"[bold]Built:[/] {str(m.get('built_at', '?'))[:19]}  "
                f"[bold]Duration:[/] {m.get('build_duration_sec', '?')}s\n"
                f"[bold]Embedding:[/] {m.get('embedding_model', '?')} "
                f"(dim={m.get('embedding_dimension', '?')})",
                title="Build Manifest",
                border_style="blue",
            ))

        # Topology
        log.write(Panel(
            f"[bold]Nodes:[/] {g.get('nodes', '?')}  "
            f"[bold]Edges:[/] {g.get('edges', '?')}  "
            f"[bold]Avg degree:[/] {g.get('avg_degree', '?')}  "
            f"[bold]Max degree:[/] {g.get('max_degree', '?')}\n"
            f"[bold]Isolated:[/] {g.get('isolated_nodes', '?')}",
            title="Topology",
            border_style="green",
        ))

        # Node + edge type tables with bar charts
        # Service format: top-level node_types / edge_types dicts
        # Legacy format:  nested inside manifest
        node_types = stats.get("node_types") or m.get("node_types", {})
        edge_types = stats.get("edge_types") or m.get("edge_types", {})

        for label, data, col_style in [
            ("Node Types", node_types, "cyan"),
            ("Edge Types", edge_types, "yellow"),
        ]:
            if not data:
                continue
            t = Table(title=label, expand=True)
            t.add_column("Type", style=col_style)
            t.add_column("Count", justify="right")
            t.add_column("Distribution")
            items = sorted(data.items(), key=lambda x: -x[1])
            mx = max((v for _, v in items), default=1)
            for k, v in items:
                bar_len = int(v / mx * 30)
                t.add_row(k, str(v), "█" * bar_len)
            log.write(t)

        # Data files (if present in manifest)
        files = m.get("data_files", [])
        if files:
            log.write(Panel(
                "\n".join(f"  • {f}" for f in files),
                title=f"Data Files ({len(files)})",
                border_style="magenta",
            ))

        self._set_summary(
            f"[green]✓[/] {g.get('nodes', '?')}n / "
            f"{g.get('edges', '?')}e / "
            f"isolated: {g.get('isolated_nodes', '?')}"
        )

    # ── Actions ───────────────────────────────────────────────────────────

    def action_clear_screen(self) -> None:
        self.query_one("#detail-log", RichLog).clear()
        self.query_one("#seeds-table", DataTable).clear()
        self._set_detail_title("DETAIL")
        self._set_summary("Cleared")

    def action_focus_search(self) -> None:
        self.query_one("#search-input", Input).focus()

    def action_go_back(self) -> None:
        """Navigate back to the previous node."""
        if self._nav_stack:
            prev = self._nav_stack.pop()
            self._do_node(prev)
        else:
            self._set_summary("[dim]No navigation history[/]")

    def action_show_stats(self) -> None:
        self._do_stats()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = GraphRAGCLI()
    app.run()
