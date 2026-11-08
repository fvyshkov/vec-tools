# Agent sandbox for Claude Code: an OS-level box for agent-track runs, and a probe that checks it

Needs: Linux, bubblewrap + socat, the `claude` CLI, `CLAUDE_CODE_OAUTH_TOKEN`. The probe reads `/proc` (Linux only) and spends one short Haiku session.

For the agent track the agent should change only its method, see only what you gave it, and reach nothing else. Hooks that match command text, as in the [Agent-Track Evidence Skeleton](https://github.com/xxx12e/vec-community-kit/tree/main/vec_agent_evidence), stop the commands they recognise, but a Python one-liner can still open a socket or read a file. Claude Code ships an OS sandbox for Bash (bubblewrap + socat on Linux). This kit sets it up for an agent loop, closes the gaps around it, and gives you a probe that shows what the agent can and cannot reach.

| | what the agent gets |
|---|---|
| read | its run folder, plus the paths you pass with `readable` (data, the venv) |
| write | only the subfolders you pass with `writable` (default `methods/`, `scratch/`). The harness, scorer and state in the run folder are read-only |
| everything else in `$HOME` and the project | no read, no write, for Bash and for the Read/Edit/Write tools alike |
| network | none, and no way to ask for a command outside the sandbox |
| context | no CLAUDE.md, no auto memory, no settings/hooks/plugins/MCP from your own `~/.claude`: only your prompt |
| secrets | auth token and any variables you name are hidden from Bash commands |

## Use

```python
from sandbox_cmd import Layout, run_turn

L = Layout(project="~/proj", run="~/proj/runs/r1", readable=("data", ".venv"),
           writable=("methods", "scratch"), secret_env=("MY_BOARD_API_KEY",))
rc, out, err, swept, changed = run_turn(L, prompt, model="sonnet")
# `changed` lists protected files in the run folder that differ after the turn (should be empty)
```

To see the generated settings and rules:

```bash
mkdir -p ~/proj/data ~/proj/.venv ~/proj/runs/r1
python sandbox_cmd.py --project ~/proj --run ~/proj/runs/r1 --readable data .venv --print
```

`claude_command()` and `agent_env()` give you the pieces if you have your own loop. The fresh config dir has no login, so claude needs `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`) in its environment. The sandbox hides it from the commands the agent runs.

## Check it

```bash
python probe.py              # one short Haiku session; ends with "0 problem(s)" when all checks pass
python probe.py --controls   # then two more no-tool calls, see below
```

The probe builds a throwaway project, asks the model to run each check once, and judges the results from the actual tool output in `stream-json` and from the disk afterwards, not from the model's summary. On our machine (Ubuntu 26.04, bubblewrap 0.11.1, Claude Code 2.1.319, 7 Nov 2026):

| check | result |
|---|---|
| read own harness, `data/`; run the venv's Python | ok |
| read `notes/`, another run, a file outside the project (with `cat` and from Python) | denied |
| list `~/.claude` | sees nothing |
| auth token in `env`, in any `/proc/*/environ` | 0 |
| write own harness, another run, `data/`, outside the project | nothing on disk |
| write `methods/`, `scratch/` | ok |
| new file in the project root | swept after the turn |
| network: `curl`, Python `urllib` | denied |
| Read tool on `notes/`, Write tool on the harness / on `methods/` | denied / denied / ok |
| CLAUDE.md in the working directory | not loaded |

`--controls` covers only the CLAUDE.md and auto-memory checks. It asks the same question twice, as configured and with the two switches off, and prints both answers as text after the "0 problem(s)" line. With the switches off both canary words should show up, which shows those two checks can fail. On our machine they did.

Keep the probe table, the settings JSON and the exact command line with your evidence. The Publish page asks agent teams to record "tools and permissions", and these three show exactly what the agent could reach. The settings JSON lists every top-level entry of your `$HOME`; redact it before sharing.

## Pitfalls

1. **The sandbox covers Bash only.** Read, Edit and Write follow permission rules. The same paths go into `--disallowedTools` as `Read(//abs/path/**)` and `Edit(...)` rules. Claude Code also copies Read deny rules into the sandbox.
2. **Auto memory can become a channel between runs.** With the default config dir, every session started in the same folder uses `~/.claude/projects/<folder>/memory/`, and what one agent writes there is loaded into the next session's context. The kit uses a fresh `CLAUDE_CONFIG_DIR` per run and sets `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`. Checked with a canary: we put a word in that memory folder and asked for it. It showed up with defaults and not with either switch.
3. **CLAUDE.md files in the working directory and its parents load even with a fresh config dir.** `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1` stops that (same canary check). Also check the project for `.claude/settings.json` and `.mcp.json`. Hooks and MCP servers run outside the sandbox.
4. **A read-denied folder is not "permission denied".** Inside the sandbox it shows up as an empty, writable folder. Reads say "No such file or directory", and a write "succeeds" but is gone when the command ends. Your agent may get confused by that, and a probe that trusts command output reports a leak that isn't one. The probe checks the disk.
5. **Claude's working directory stays writable for Bash**, and Claude Code creates `.claude/` there itself. A `denyWrite` on the root wins over `allowWrite` inside it, so you can't close the root and open the run. `run_turn` moves anything new in the project root out of reach after each turn. Give each run its own working directory: a sweep of a shared root also moves folders that other processes just created there.
6. **The shared temp dir.** By default the sandbox mounts `/tmp/claude-<uid>`, which holds the temp files of every claude session on the machine. Set `CLAUDE_CODE_TMPDIR` to a short private path. A long path was silently replaced by the shared one.
7. **Tools that bypass the Bash sandbox, or outlive the turn:** `WebFetch`, `WebSearch`, `CronCreate`, `RemoteTrigger`, `ScheduleWakeup`, `Workflow`. All disallowed. `Monitor` asks for approval, which headless mode can't give, so it is disallowed too. The tool list is fixed to `Read,Edit,Write,Glob,Grep,Bash`.
8. **Ubuntu's AppArmor blocks bubblewrap's user namespaces.** The sandbox failed with "nested userns is capability-restricted" until we disabled the `bwrap-userns-restrict` profile and added an unconfined profile for `/usr/bin/bwrap` (`userns,`). This loosens a system hardening for bwrap, so do it only on a machine you control, and keep the original profile so you can roll back.

## Notes

- Deny lists are fixed when the command is built: a folder created in `$HOME` or the project during a turn is not covered for that turn. Build the command right before each turn. The shortcut, `allowRead` on top of `denyRead: ~`, re-opened the whole working folder for us, `.env` included, so the kit denies entries one by one.
- Library caches (numba, matplotlib, XDG) are pointed at `scratch/.cache/`; they would otherwise try to write under `$HOME`.
- The interpreter has to be readable. A uv or pyenv venv points at `~/.local/share/uv/...` or `~/.pyenv/...`; add that to `readable` (the probe adds `sys.base_prefix` itself).
- Memory: we cap the whole loop with `systemd-run --user --scope -p MemoryMax=...`. `CLAUDE_CODE_TOOL_MEMORY_LIMIT` is documented for Bash commands; we have not tested it.
- macOS (Seatbelt instead of bubblewrap) and Windows were not tested.

MIT licence.
