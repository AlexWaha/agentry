# Code Retrieval

How agents find things. The code knowledge graph (codegraph) exists so code
exploration costs one tool call instead of a grep-and-read chain. Use it first;
fall back to file tools only where the graph cannot answer.

> The graph is a per-project SQLite index maintained by the codegraph CLI in
> `.codegraph/` at the PROJECT root (gitignored; codegraph does not allow
> relocating it under `.claude/` - CODEGRAPH_DIR accepts plain names only).
> It is synced at SessionStart and kept current by codegraph's own file
> watcher. The MCP server is wired per-project in `.mcp.json` - never in the
> user-global config.

---

## The three rules

1. **Query codegraph before grepping code.** For any question about the
   codebase - where is X implemented, who calls Y, what breaks if Z changes,
   how does a request reach the database - use ONE `codegraph_explore` call
   (MCP) or the CLI equivalent. Do not open files speculatively; the graph
   returns verbatim source, call paths, and blast radius in a single answer.

   CLI fallback when MCP is unavailable:
   ```
   codegraph explore "<question>"
   codegraph callers <symbol>
   codegraph callees <symbol>
   codegraph impact <symbol>
   codegraph affected <changed-files...>
   ```

2. **Read the .claude setup directly.** Rules, agents, skills, project overlay,
   and tasks are a small set of markdown files codegraph does not index. For
   N-of-N completeness over a category: `Glob` the directory, count the
   matches, then account for every file. Never reason from a partial set you
   happened to read.

3. **Grep/Glob remain for literals and non-code.** Exact string hunts (error
   messages, config keys, translations), markdown, templates, and anything
   outside the indexed languages go through `Grep`/`Glob` as before.

---

## Staleness

The graph syncs at SessionStart and via codegraph's own watcher. If a result
looks stale (a symbol you just added is missing), run:

```
codegraph sync
```

If codegraph is unavailable on this machine, say so once and fall back to
Grep/Glob - do not silently degrade exploration quality.
