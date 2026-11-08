# Virtual Embryo Challenge: tools from an agent-track team

Small, tested pieces we built while competing in the [Virtual Embryo Challenge](https://virtualembryo.ai) (NeurIPS 2026) on the agent track. Each folder has its own README with an example and a test.

| folder | what it is for |
|---|---|
| [`agent_sandbox/`](agent_sandbox) | Run Claude Code headless as an agent that can touch only its run folder: OS sandbox for Bash, matching rules for the file tools, no network, no shared memory or CLAUDE.md, hidden tokens. Comes with a probe that checks all of it and gives you a table to keep with your evidence. |
| [`t1_lite_score/`](t1_lite_score) | The four ranked Task 1 metrics, identical to veckit 0.1.2, in ~7 GB instead of ~40 GB. |

Requirements: Python 3.10+, `anndata`, `numpy`, `scipy`; `pip install pytest veckit==0.1.2` for the tests and the scoring tools; for `agent_sandbox`, Linux with `bubblewrap` and `socat` and the `claude` CLI.

```bash
python -m pytest -q      # offline tests, a few seconds (or run each test_*.py directly)
```

Nothing here uses challenge data beyond the public release, and no test needs it: the tests make synthetic data.

MIT licence.
