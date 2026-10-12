# Subagent identity in the PreToolUse payload - Claude Code 2.1.295

Measured 2026-10-12 for task-0060, stage S1. Host: Windows 11, git-bash,
`claude --version` = `2.1.295 (Claude Code)`. Headless `claude -p` run in a
scratch project (`src/tmp/probe-0060/`, since deleted) whose only hook was a
`PreToolUse` matcher `*` that dumped stdin JSON and probed the transcript at hook
time. One dispatch of one test agent (`tools: Bash`), fixed prompt:

```
Swarm zone: probe
Task: run your single echo command.
End.
```

Headless dispatch worked non-interactively: `subagent_stats` in the result showed
`spawned:1, completed:1`. Not measured: an interactive (`entrypoint: cli`)
session, a background (`run_in_background`) dispatch, and two concurrent
subagents. The conclusions below are for the headless foreground case.

## 1. Payload: main thread versus subagent

Main thread, tool `Agent` (the dispatch itself):

```
session_id, transcript_path, cwd, prompt_id, permission_mode, effort,
hook_event_name, tool_name, tool_input, tool_use_id
```

Subagent, tool `Bash` (its first and only tool call). Two keys appear that the
main thread does not have:

```json
{
  "session_id": "932b93d4-360c-4529-87ee-75d3a6d89557",
  "transcript_path": "C:\\Users\\AlexWaha\\.claude\\projects\\E--Personal-Agenty-src-tmp-probe-0060\\932b93d4-360c-4529-87ee-75d3a6d89557.jsonl",
  "cwd": "E:\\Personal\\Agenty\\src\\tmp\\probe-0060",
  "prompt_id": "fc33eb3d-342e-4c31-a7f8-9ace853e28c4",
  "permission_mode": "acceptEdits",
  "agent_id": "a3d9c94df3116b37f",
  "agent_type": "probe",
  "effort": {"level": "medium"},
  "hook_event_name": "PreToolUse",
  "tool_name": "Bash",
  "tool_input": {"command": "echo probe-ran", "description": "Print probe marker"},
  "tool_use_id": "toolu_01W27MXQsChka9k5BUWJTkLy"
}
```

- `agent_id` (17 hex chars, `a` + 16) and `agent_type` (the agent definition
  name) are present ONLY for a subagent call. Their absence is how the main
  thread is recognised.
- `prompt_id` is identical for the dispatching call and the subagent's call (it
  ties to the parent prompt), so it does not identify the subagent.
- `transcript_path` is the PARENT session transcript, not the subagent's. It is
  the same string in both payloads.
- No other identity key exists (no zone, task, or dispatch-prompt key).

## 2. Transcript file names

| What | Path |
|---|---|
| Parent (payload `transcript_path`) | `<home>/.claude/projects/<cwd with : \ / replaced by ->/<session_id>.jsonl` |
| Subagent | `<dir of transcript_path>/<session_id>/subagents/agent-<agent_id>.jsonl` |

Concrete subagent file seen on disk:
`...\E--Personal-Agenty-src-tmp-probe-0060\932b93d4-360c-4529-87ee-75d3a6d89557\subagents\agent-a3d9c94df3116b37f.jsonl`

So the subagent file is derivable from the payload alone:
`Path(transcript_path).parent / session_id / "subagents" / f"agent-{agent_id}.jsonl"`.

## 3. Is the dispatch prompt readable before the first tool call

Yes. At the subagent's first PreToolUse the file already existed with 11 lines.
Line 1 is the dispatch prompt as the first user message (quoted from the file):

```
{"parentUuid":null,"isSidechain":true,"promptId":"fc33eb3d-...","agentId":"a3d9c94df3116b37f","type":"user","message":{"role":"user","content":"Swarm zone: probe\nTask: run your single echo command.\nEnd."},"uuid":"be46e1e6-...","timestamp":"2026-10-12T12:32:35.519Z","userType":"external","entrypoint":"sdk-cli","cwd":"E:\\Personal\\Agenty\\src\\tmp\\probe-0060","sessionId":"932b93d4-...","version":"2.1.295","gitBranch":"feature/task-0120"}
```

`message.content` was a plain string here. It matched `tool_input.prompt` on the
parent's `Agent` call exactly.

## 4. Pitfall: the parent transcript also contains the zone line

The parent file contains `Swarm zone: probe` too (in the user's message and in
the `Agent` tool_use input). A gate that searches `transcript_path` for the zone
line would read the orchestrator's text, and with several agents dispatched the
parent holds EVERY agent's zone line. The agent's own file (section 2) is the
only per-agent source. The parent is also large (1.29 MB after one turn), the
subagent file small.

## 5. Answer: can the gate know which subagent it is and read its zone line

Yes, with the plan's primary design. The fallback (payload `agent_id` plus a
`zones.py --bind` step) is not needed for the headless foreground case.

1. `agent_id` present in the payload = subagent; absent = main thread.
2. Open `Path(transcript_path).parent / session_id / "subagents" / f"agent-{agent_id}.jsonl"`.
3. Read line 1, take `message.content` (a string here; handle a list of text
   blocks defensively), match a line `^Swarm zone: (\S+)`.
4. No match, missing file, or unreadable file = fail closed (deny), per the plan.

Caveats to carry into S3:

- The zone is whatever the orchestrator wrote in the dispatch prompt, so it is
  orchestrator-asserted. Whether an agent could tamper with its own transcript
  file was not measured; the gate should deny agent writes under
  `~/.claude/projects/`.
- Re-measure the file layout and content shape on a Claude Code upgrade, in an
  interactive session, for background dispatch, and with concurrent subagents
  (distinct `agent_id` and file per agent is expected but unmeasured).
