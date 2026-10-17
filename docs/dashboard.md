# Dashboard

The dashboard is a local web page that shows what Agentry is doing in your project: what waits for you, the task board, the journal of gates and approvals, memory, the harness inventory and the handoff documents. It only reads. It cannot approve, reject, start or reorder anything, so you keep doing that in Claude Code.

## Table of contents

- [Start the dashboard](#start-the-dashboard)
- [The token URL](#the-token-url)
- [Pages](#pages)
- [Security model](#security-model)
- [Stop the dashboard](#stop-the-dashboard)
- [What is measured and what is not](#what-is-measured-and-what-is-not)
- [Reach it over an SSH tunnel](#reach-it-over-an-ssh-tunnel)
- [Troubleshooting](#troubleshooting)

## Start the dashboard

Type this in Claude Code, in the project you want to look at:

```text
/agentry:dashboard
```

Claude starts the server as a background task, reads its first output line and gives you the URL. The server also opens your default browser on that URL. You need Python 3.12 or newer, which Agentry already requires. You do not need Node or npm, because the built web page ships inside the plugin.

You can also run the server yourself. `AGENTRY_ROOT` and `CLAUDE_PROJECT_DIR` are set in every Bash call Claude Code makes (see [Commands](commands.md#command-line-tools)), so ask Claude to run it, or type it with the `!` prefix:

```bash
python "$AGENTRY_ROOT/tools/dashboard/server.py"
```

| Option | Effect |
|---|---|
| `--no-open` | Do not open the browser. Use it on a remote machine, and on a shared multi-user machine (see [The token URL](#the-token-url)) |
| `--idle-timeout SECONDS` | Exit after this many seconds with no request and no open dashboard tab. Default 7200 (2 hours) |
| `--help` | Print the options |

Each project gets its own port, computed from the project path, in the range 41000 to 41999. Starting the dashboard again for the same project uses the same port when it is free. If the port is taken, the server tries the next nine, then asks the system for one.

The server exits with code 2 and one line on stderr when `CLAUDE_PROJECT_DIR` is not set, when the project has no `.agentry/` folder, or when the built web page fails its integrity check. It binds no port in these cases.

## The token URL

The server prints one line on stdout and nothing else:

```text
http://127.0.0.1:<port>/#token=<token>
```

The token is a random secret made for this launch. The browser keeps it in local storage and removes it from the address bar after the first load, so a reload or a new tab on the same address keeps working. A new launch makes a new token. If a page shows the expired-link message, run `/agentry:dashboard` again and open the new URL. Treat the URL as a password: do not paste it into a chat or an issue.

The URL travels further than the server. To open your browser, the server passes the full URL, token included, to the operating system's browser launcher. On Linux that URL appears in the command line of the launcher (`xdg-open` or the browser), which other users on the same machine can read through `/proc`. The URL also lands in the browser history before the page removes the token from the address bar, in the Claude Code session transcript because `/agentry:dashboard` prints it, and the copy in local storage stays there after the server exits. On a shared multi-user machine, start the server with `--no-open` and copy the URL into your browser by hand.

## Pages

Open the page you need from the sidebar. Each page has a "Learn more" link to the part of the documentation that explains it. Some of these links open this page, and others open [Pipeline](pipeline.md) or [Agents](agents.md). The sidebar has a "Docs" link to the whole docs folder.

| Page | What it shows |
|---|---|
| Overview | Counts of backlog, active and done tasks and of runs that need you, the current runs with their stage, the five newest journal events, and the handoff and memory debt |
| Needs you | Every run that waits for you (a plan, commit or push approval, or a blocked task), oldest first, with the reason and how long it has waited |
| Board | Backlog, Active and Done columns of task cards, with an epic filter. A column shows its first 12 cards and a "View all" link to the Tasks page |
| Tasks | A searchable, paged list of all tasks with filters for folder, type and epic |
| Task detail | One task: header, description, acceptance criteria with checkbox counts, dependencies in both directions, the stage stepper and the task's own timeline of gates, approvals and rejections |
| Activity | The journal of transitions, gate results, approvals and merges, newest first, with filters for task, event type and result. New events appear while the page is open |
| Metrics | Gate duration and failures per stage, the slowest gates, time spent in each stage, per-day activity, recent rejections with their reasons, and lead time, over 7, 14, 30 or 90 days |
| Memory | Lessons, patterns and modules from the project memory store with search and filters, and a Stats tab with store size, growth and what each subagent profile would be given now |
| Graphs | A live, zoomable graph of memory rows and the tasks, agents and areas they point to, and a module-level graph of the code index when one exists. Scroll to zoom, drag the background to pan, drag a node to move it, hover or click to highlight a node and its neighbours. Keyboard users tab into the graph and use the arrow keys, and every graph has a "Show as table" view |
| Harness tabs | Agents, Rules, Skills, Hooks and Plugin. The Plugin tab ends with the runtime block: dials per lane, worktrees, supervisor and debt per task |
| Handoffs | The handoff documents, newest first, with a valid or invalid chip. Open one to read it and see why it is invalid |

The pages update by themselves while the browser is connected. The top bar shows `Live` when they do, `Reconnecting` while the page retries, and `Offline` when the server cannot be reached.

## Security model

The dashboard runs only on your machine and exposes little. The table lists each control.

| Control | What it does |
|---|---|
| Loopback only | The server binds `127.0.0.1`, never `0.0.0.0` or `::`. Nothing on your network can connect. A second server on the same port fails to bind |
| Per-launch token | Every data request needs `Authorization: Bearer <token>`, and the live connection needs the token in its address. A missing or wrong token gets 401. The server keeps the token in memory only: it does not write it to a file, an environment variable or a log. The browser side is different, see [The token URL](#the-token-url): the launch URL reaches the browser launcher and the browser history, and the browser keeps the token in local storage |
| Host check | The `Host` header must be `127.0.0.1:<port>`. Anything else, such as a DNS-rebinding page that points its own name at your machine, gets 403 |
| Origin check | When the browser sends `Origin` (and always for the live connection) it must be the server's own address. Cross-site requests get 403. The server sends no CORS headers |
| Read-only stores | Every SQLite database (`run*.db`, `events.db`, `memory.db`, the code index) is opened with `mode=ro` and closed after each request. Every route accepts GET only; any other method gets 405. The live connection refuses every command |
| Safe rendering | Task and handoff text is rendered by a Markdown renderer that builds elements, never raw HTML. Scripts, event handlers and `javascript:` links come out as plain text |
| Strict headers | A Content Security Policy that allows only the server's own origin, `nosniff`, no referrer and no framing. The web page loads nothing from the network |
| Bounded use | At most 8 live connections, and every list is paged by the server |

Two limits apply. The token protects against other websites and other machines, not against another program running as you on the same computer, which can read your files anyway. And the dashboard shows your task titles, memory rows and handoff text, so do not share a screenshot of it as if it were public.

## Stop the dashboard

| How | What happens |
|---|---|
| `/agentry:dashboard stop` | Claude ends the background task, then checks that nothing answers on the port. If something still does, it tells you instead of searching for processes |
| Ctrl+C or a terminate signal | The server closes its live connections and exits with code 0. This applies when you run it in a terminal yourself |
| Do nothing | The server exits with code 0 after 2 hours with no request and no open dashboard tab. An open tab counts as use, so it keeps the server up. Close the tab and the idle clock starts |

To stop a server by hand, find the process that listens on the port from the URL and end it. On Windows run `netstat -ano` and look for the port, then `taskkill /PID <pid>`. On Linux or macOS run `lsof -i :<port>`, then `kill <pid>`. These are standard operating system commands and were not tested with the dashboard. Stopping it loses nothing: the server holds no state and never wrote to your project.

## What is measured and what is not

These results come from a live check on Windows 11 with Git Bash, 2026-10-16, with the built dashboard.

| Case | Result |
|---|---|
| Start from `/agentry:dashboard` as a background task | The first output line, the URL, appeared in about 1 second and was the only line. The browser was asked to open that exact URL once |
| Terminate signal (Ctrl+Break to the server's process group, the Windows form of a terminate request) | Exit code 0 in 0.2 seconds, nothing printed |
| Hard kill of the server process | Exit code 1, no cleanup, and no data touched. Starting it again used the same port and worked |
| Idle exit with no traffic | Exit code 0 after the timeout (tested with 2 seconds). As a Claude Code background task it was reported as completed with exit code 0 and the port was free |
| The shell that started the server is killed | The server keeps running and exits only at the idle timeout, 2 hours by default. Killing the parent shell does not stop it |
| Ctrl+C in a terminal | Not measured live. The handler is the same one the terminate signal uses |
| Claude Code background-task stop and session end | Not measured. Whether Claude Code ends the Python process, or only its shell, was not observed. If it only ends the shell, the previous row applies and the server stays up until the idle timeout |

The safe habit is `/agentry:dashboard stop`, then a check that the page no longer answers. The idle exit is bounded and the server only reads, so a server left running costs you one idle process.

## Reach it over an SSH tunnel

The server listens on the loopback address of the machine it runs on, so another computer cannot reach it directly. To use the dashboard of a remote or virtual machine, forward the port and open the URL in a browser on your own computer. This was not tested.

1. On the remote machine, start the server without opening a browser, from a terminal in the project: `python "$AGENTRY_ROOT/tools/dashboard/server.py" --no-open`. Note the port in the URL it prints.
2. On your computer, forward the same port number on both sides: `ssh -L <port>:127.0.0.1:<port> user@remote`.
3. Open the URL it printed, unchanged, in your local browser.

Use the same port on both sides. The server accepts only `Host: 127.0.0.1:<port>`, so a tunnel that maps a different local port is refused with 403.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| The browser did not open | No default browser, or a remote machine | Open the URL that Claude printed. Start with `--no-open` on a remote machine |
| "Run /agentry:dashboard again" | The token is old or missing: the server restarted, or you opened the address without the `#token=` part | Run `/agentry:dashboard` and open the new URL |
| The server exits at once with code 2 | `CLAUDE_PROJECT_DIR` is unset, the project has no `.agentry/` folder, or the built page failed its integrity check | Start it from Claude Code inside an onboarded project. For the integrity check, reinstall or update the plugin |
| 403 on every request | The page was opened through another host name or a tunnel on a different port | Open `http://127.0.0.1:<port>/` exactly as printed |
| `Offline` in the top bar | The server stopped, for example at the idle timeout | Run `/agentry:dashboard` again |
| A list is empty | The store does not exist yet in this project, for example no memory rows recorded | Empty is correct for a new project. The page says why |
| A page shows a short "try again" notice | A database was locked for more than a second by a writer | Wait a moment. The page retries three times by itself |
| The page shows old data | The live connection dropped | Reload. The top bar shows `Reconnecting` while it retries |
| `/agentry:dashboard` is not found | The session started before the plugin was updated | Restart Claude Code. The skill list is read at session start |
| `/agentry:dashboard stop` says the server survived | Claude Code ended the shell and not the Python process | End it by hand as in [Stop the dashboard](#stop-the-dashboard), or wait for the idle exit |

For problems outside the dashboard, see [Troubleshooting](troubleshooting.md).
