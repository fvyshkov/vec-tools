"""Offline checks of the generated settings and rules (no claude needed, runs in a second).

    python -m pytest -q test_sandbox_cmd.py      (or just: python test_sandbox_cmd.py)
"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sandbox_cmd import NO_TOOLS, Layout, agent_env, claude_command, deny_rules, settings  # noqa: E402


def _tree(base: Path):
    if base.exists():
        shutil.rmtree(base)
    home = base / "home"
    proj = home / "work/proj"
    for d in ("data", "notes", ".venv", "runs/r1/methods", "runs/r2", "runs/r1/scratch"):
        (proj / d).mkdir(parents=True)
    (proj / "runs/r1/harness.py").write_text("#\n")
    (home / "work/other").mkdir()
    (home / ".claude/projects").mkdir(parents=True)
    (home / ".bashrc").write_text("#\n")
    return Layout(proj, proj / "runs/r1", readable=("data", ".venv"), secret_env=("BOARD_API_KEY",), home=home)


def test_layout(tmp=None):
    L = _tree(Path(tmp or Path(__file__).resolve().parent / "_test_tmp"))
    cmd = claude_command("hi", "haiku", L)                     # creates the writable folders and the config dir
    s = settings(L)["sandbox"]
    fs = s["filesystem"]
    deny_read, deny_write = set(fs["denyRead"]), set(fs["denyWrite"])
    P, H = L.project, L.home

    for p in (P / "notes", P / "runs/r2", H / "work/other", H / ".claude", H / ".bashrc"):
        assert str(p) in deny_read, p
    for p in (P, P / "data", P / ".venv", L.run, P / "runs", H / "work"):
        assert str(p) not in deny_read, p                      # kept, or on the way to something kept
    assert not any(d.startswith(str(L.run)) for d in deny_read)

    for p in (L.run / "harness.py", P / "data", P / ".venv", P / "notes", P / "runs/r2"):
        assert str(p) in deny_write, p
    assert fs["allowWrite"] == [str(L.run / "methods"), str(L.run / "scratch")]
    assert not any(d.startswith(str(L.run / "methods")) or d.startswith(str(L.run / "scratch")) for d in deny_write)

    assert s["network"] == {"allowedDomains": [], "strictAllowlist": True}
    assert s["failIfUnavailable"] is True and s["allowUnsandboxedCommands"] is False
    assert {"CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "BOARD_API_KEY"} <= {e["name"] for e in s["credentials"]["envVars"]}

    rules = deny_rules(L)
    assert f"Read(/{P / 'notes'}/**)" in rules and f"Edit(/{P / 'notes'}/**)" in rules
    assert f"Edit(/{L.run / 'harness.py'})" in rules
    assert not any(r.startswith("Read(/" + str(L.run)) for r in rules)

    i = cmd.index("--disallowedTools")
    assert cmd[i + 1:] == rules + list(NO_TOOLS)               # the variadic flag is last: nothing else gets swallowed
    assert json.loads(cmd[cmd.index("--settings") + 1]) == settings(L)

    env = agent_env(L, base={"PATH": "/usr/bin", "CLAUDE_CODE_OAUTH_TOKEN": "t", "BOARD_API_KEY": "k",
                             "CLAUDE_CONFIG_DIR": str(H / ".claude")})
    assert env["CLAUDE_CONFIG_DIR"] == str(L.config_dir) and L.config_dir.is_dir()
    assert str(L.config_dir) in deny_read or any(str(L.config_dir).startswith(d + "/") for d in deny_read)
    assert "BOARD_API_KEY" not in env and env["CLAUDE_CODE_OAUTH_TOKEN"] == "t"
    assert env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1" and env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] == "1"
    assert env["MPLCONFIGDIR"].startswith(str(L.run / "scratch"))
    print("ok:", len(deny_read), "read-denied,", len(deny_write), "write-denied,", len(rules), "rules")


if __name__ == "__main__":
    test_layout()
