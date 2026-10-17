---
name: dashboard
description: Start or stop the Agentry dashboard, a read-only local web view of this project's pipeline, memory and harness. Use when the User types /agentry:dashboard, asks to open or close the dashboard, or wants to see the pipeline in a browser. With the argument stop it ends the running dashboard.
disable-model-invocation: true
allowed-tools: Bash
argument-hint: [stop]
---

# /agentry:dashboard

Start the dashboard server for the current project, or stop it. The server is read-only
(it never writes to the project), listens on `127.0.0.1` only and opens the browser on a
URL that carries a per-launch token. Details: `docs/dashboard.md`.

## Steps

### No argument: start

1. If a dashboard task you started earlier in this session is still running, give the User
   its URL again and stop here. Do not start a second server.
2. Run this with the Bash tool and `run_in_background: true`. Never run it in the
   foreground, it does not return until it stops:

   ```
   python "${CLAUDE_PLUGIN_ROOT}/tools/dashboard/server.py"
   ```

   It reads the project from `CLAUDE_PROJECT_DIR` and opens the default browser itself.
3. Read the background task's output until its first line appears (about 1 second).
   That line, and only that line, is the launch URL:
   `http://127.0.0.1:<port>/#token=<token>`. The server prints nothing else on stdout.
4. Tell the User in one or two lines: the URL (they may need it if the browser did not
   open; the token in it is a secret for this launch, so say it only to the User), and that
   `/agentry:dashboard stop` ends it. The server also exits by itself after 2 hours with
   no request and no open dashboard tab.
5. If the task ended instead of printing a URL, read its stderr and report the one-line
   message. Exit code 2 means the environment is wrong: `CLAUDE_PROJECT_DIR` unset, no
   `.agentry/` folder in the project, or the dashboard build failed its integrity check.

### Argument `stop`

1. End the background task started by the launch step (stop the task by its id, which
   `/tasks` also lists).
2. Confirm it is gone: `curl -s -m 2 -w "%{http_code}" http://127.0.0.1:<port>/api/v1/status`
   with the port from the launch URL. `000` means nothing answers, so tell the User it
   stopped. Any other code means the server survived the task stop: say so and point to
   `docs/dashboard.md` for stopping it by hand. Do not search for or kill processes.
3. If no dashboard task is running in this session, say so. A server left over from an
   earlier session ends on its own after the idle timeout.

## Output checklist

- [ ] The server ran in the background, never in the foreground
- [ ] The User got the launch URL (start) or a confirmation that it stopped (stop)
