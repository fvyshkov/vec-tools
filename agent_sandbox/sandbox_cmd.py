"""Run Claude Code headless as an agent that can touch only its own run folder.

Builds the `claude -p` command line and environment for one agent turn:

* Bash runs inside Claude Code's OS sandbox (bubblewrap + socat on Linux): no network, no way to leave the sandbox,
  writes only to the folders you name, reads denied for the rest of $HOME and of the project;
* the file tools (Read / Edit / Write) get the same limits as permission deny rules: the sandbox covers Bash only;
* a fresh CLAUDE_CONFIG_DIR per run, so the agent does not inherit auto memory, settings, hooks, plugins or MCP
  servers from your own ~/.claude. Auto memory in a shared config dir is a channel between runs: what one agent
  wrote there is loaded into the next agent's context, bypassing the prompt and the evidence;
* CLAUDE.md files and auto memory are switched off (instructions come only from the prompt you log);
* tokens are hidden from the environment that Bash commands see; temp files and library caches go to private folders.

Layout it assumes (names are yours):

    project/            claude's working directory
      data/  .venv/     shared, read-only (pass with --readable)
      runs/r1/          this run: harness files are read-only ...
        methods/        ... except the folders passed with --writable
        scratch/
      runs/r2/  notes/  everything else: no read, no write

    python sandbox_cmd.py --project ~/proj --run ~/proj/runs/r1 --readable data .venv --print
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ALLOWED_TOOLS = "Read,Edit,Write,Glob,Grep,Bash"
# Tools that reach past the Bash sandbox or outlive the turn: web access, schedulers, sub-agent orchestration.
# Monitor asks for approval in headless mode, so it is useless there; it is removed to keep the tool surface fixed.
NO_TOOLS = ("Monitor", "WebFetch", "WebSearch", "CronCreate", "RemoteTrigger", "ScheduleWakeup", "Workflow")
AUTH_ENV = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")


@dataclass
class Layout:
    project: Path                                   # claude's working directory
    run: Path                                       # this run's folder
    writable: tuple = ("methods", "scratch")        # subfolders of `run` the agent may write
    readable: tuple = ()                            # more paths the agent may read: relative to project, or absolute
    config_dir: Path | None = None                  # fresh CLAUDE_CONFIG_DIR (default ~/.config/agent-sandbox/<run>)
    tmp: str | None = None                          # short private CLAUDE_CODE_TMPDIR (default /tmp/agent-<run>)
    secret_env: tuple = ()                          # your own secrets (e.g. a board API key) to hide from Bash
    home: Path = field(default_factory=Path.home)

    def __post_init__(self):
        self.project = Path(self.project).expanduser().absolute()
        self.run = Path(self.run).expanduser().absolute()
        self.home = Path(self.home).expanduser().absolute()
        if self.project not in (self.run, *self.run.parents):
            raise ValueError("run must be inside project")
        if self.config_dir is None:
            self.config_dir = self.home / ".config/agent-sandbox" / self.run.name
        self.config_dir = Path(self.config_dir).expanduser().absolute()
        self.tmp = self.tmp or f"/tmp/agent-{self.run.name}"

    @property
    def writable_paths(self):
        return [self.run / w for w in self.writable]

    @property
    def readable_paths(self):
        return [self.run, *[(p if Path(p).is_absolute() else self.project / p) for p in map(Path, self.readable)]]


def _outside(start: Path, keep: list[Path]) -> list[Path]:
    """Entries under `start` that are neither in `keep` nor on the way to something in `keep` (recursing into those)."""
    keep = [Path(k).absolute() for k in keep]
    out = []
    for e in sorted(start.iterdir()):
        if any(e == k or k in e.parents for k in keep):                 # e is kept, or sits inside a kept folder
            continue
        if any(e in k.parents for k in keep) and e.is_dir() and not e.is_symlink():
            out += _outside(e, keep)                                     # e leads to something kept: look inside
        else:
            out.append(e)
    return out


def read_denied(L: Layout) -> list[Path]:
    keep = L.readable_paths
    out = _outside(L.home, keep) if L.home.is_dir() else []
    if L.home not in (L.project, *L.project.parents):
        out += _outside(L.project, keep)
    if Path("/tmp").is_dir() and sys.platform.startswith("linux"):
        out.append(Path("/tmp"))         # the sandbox still gives commands their own temp dir (CLAUDE_CODE_TMPDIR)
    return out


def write_denied(L: Layout) -> list[Path]:
    """Everything in the project tree except the writable folders. The project root itself stays writable for Bash
    (it is claude's working directory); new files there are swept after the turn, see `sweep`."""
    return _outside(L.project, L.writable_paths)


def _rule(tool, p: Path):
    pat = f"/{p}/**" if p.is_dir() else f"/{p}"            # "//abs/path": absolute path in Claude Code rules
    return f"{tool}({pat})"


def deny_rules(L: Layout) -> list[str]:
    rules = []
    for p in read_denied(L):
        rules += [_rule("Read", p), _rule("Edit", p)]
    rules += [_rule("Edit", p) for p in write_denied(L)]
    return rules


def settings(L: Layout) -> dict:
    return {
        "sandbox": {
            "enabled": True,
            "failIfUnavailable": True,           # no sandbox -> no Bash, instead of running unsandboxed
            "allowUnsandboxedCommands": False,   # the model cannot ask to run a command outside the sandbox
            "autoAllowBashIfSandboxed": True,
            "network": {"allowedDomains": [], "strictAllowlist": True},
            "filesystem": {"denyRead": [str(p) for p in read_denied(L)],
                           "denyWrite": [str(p) for p in write_denied(L)],
                           "allowWrite": [str(p) for p in L.writable_paths]},
            "credentials": {"envVars": [{"name": n, "mode": "deny"} for n in (*AUTH_ENV, *L.secret_env)]},
        },
        "autoMemoryEnabled": False,
    }


def agent_env(L: Layout, token: str | None = None, base: dict | None = None) -> dict:
    """Environment for `claude`. The auth token stays (claude needs it: the fresh config dir has no login);
    the sandbox hides it from Bash. Your own secrets are dropped entirely."""
    base = dict(os.environ if base is None else base)
    env = {k: v for k, v in base.items() if k not in (*AUTH_ENV, *L.secret_env, "CLAUDE_CONFIG_DIR")}
    if token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    else:
        env.update({k: base[k] for k in AUTH_ENV if base.get(k)})
    L.config_dir.mkdir(parents=True, exist_ok=True)
    env["CLAUDE_CONFIG_DIR"] = str(L.config_dir)
    env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
    env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] = "1"
    # Without this the sandbox mounts the shared /tmp/claude-<uid>, with the temp files of every claude session on the
    # machine. Keep it short: for a long path claude falls back to the shared default.
    env["CLAUDE_CODE_TMPDIR"] = L.tmp
    if L.writable:
        cache = L.run / L.writable[-1] / ".cache"       # libraries that cache under $HOME would fail on a read-only home
        for k, sub in (("NUMBA_CACHE_DIR", "numba"), ("MPLCONFIGDIR", "mpl"), ("XDG_CACHE_HOME", "xdg")):
            (cache / sub).mkdir(parents=True, exist_ok=True)
            env[k] = str(cache / sub)
    return env


def claude_command(prompt: str, model: str, L: Layout, claude_bin: str = "claude", extra=()) -> list[str]:
    for p in (*L.writable_paths, L.config_dir):         # create first, so the deny lists cover them
        p.mkdir(parents=True, exist_ok=True)
    return [claude_bin, "-p", prompt, "--model", model, "--permission-mode", "acceptEdits",
            "--settings", json.dumps(settings(L)), *extra,
            "--allowedTools", ALLOWED_TOOLS, "--disallowedTools", *deny_rules(L), *NO_TOOLS]


def fingerprint(L: Layout) -> dict:
    """sha256 of every file in the run folder outside the writable folders (harness, scorer, state ...)."""
    out = {}
    for f in sorted(L.run.rglob("*")):
        if f.is_file() and not any(w == f or w in f.parents for w in L.writable_paths):
            out[str(f.relative_to(L.run))] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def sweep(L: Layout, before: set[str], dest: Path) -> list[str]:
    """Move files that appeared in the project root during the turn to `dest` (outside the agent's reach)."""
    moved = []
    for e in sorted(L.project.iterdir()):
        if e.name in before:
            continue
        dest.mkdir(parents=True, exist_ok=True)
        shutil.move(str(e), str(dest / f"{int(time.time())}_{e.name}"))
        moved.append(e.name)
    return moved


def run_turn(L: Layout, prompt: str, model: str, claude_bin="claude", token=None, timeout=1800, extra=(),
             stray_dir: Path | None = None):
    """One agent turn. Returns (returncode, stdout, stderr, swept files, changed protected files)."""
    before_root = {e.name for e in L.project.iterdir()}
    before_fp = fingerprint(L)
    r = subprocess.run(claude_command(prompt, model, L, claude_bin, extra), cwd=str(L.project), capture_output=True,
                       text=True, timeout=timeout, env=agent_env(L, token),
                       stdin=subprocess.DEVNULL)    # claude -p also reads piped stdin into the prompt
    moved = sweep(L, before_root, stray_dir or (L.config_dir.parent / f"stray-{L.run.name}"))
    after_fp = fingerprint(L)
    changed = sorted(k for k in set(before_fp) | set(after_fp) if before_fp.get(k) != after_fp.get(k))
    return r.returncode, r.stdout, r.stderr, moved, changed


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--readable", nargs="*", default=[])
    ap.add_argument("--writable", nargs="*", default=["methods", "scratch"])
    ap.add_argument("--secret-env", nargs="*", default=[], help="your own secret env vars to hide, e.g. a board API key")
    ap.add_argument("--print", action="store_true", help="print the settings and the deny rules")
    ap.add_argument("--prompt-file", help="run one turn with this prompt")
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--claude-bin", default="claude")
    a = ap.parse_args(argv)
    L = Layout(a.project, a.run, tuple(a.writable), tuple(a.readable), secret_env=tuple(a.secret_env))
    if a.print or not a.prompt_file:
        claude_command("", a.model, L)                   # creates the writable folders and the config dir first
        print(json.dumps(settings(L), indent=2))
        print("\n--disallowedTools", *deny_rules(L), *NO_TOOLS, sep="\n  ")
    if a.prompt_file:
        rc, out, err, moved, changed = run_turn(L, Path(a.prompt_file).read_text(), a.model, a.claude_bin)
        print(out)
        if err:
            print("[stderr]", err, file=sys.stderr)
        if moved:
            print("swept from the project root:", moved, file=sys.stderr)
        if changed:
            print("PROTECTED FILES CHANGED:", changed, file=sys.stderr)
        return rc or (2 if changed else 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
