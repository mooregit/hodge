# Hodge

Hodge is a tiny router over installed agent CLIs.

Current status: usable local prototype. It provides a shared REPL over Codex,
Claude, and Ollama, plus sessions, local usage capture, model picking, and a
lightweight spec/task workflow.

## Install

Requirements:

- Python 3.10+
- At least one supported agent CLI on `PATH`: `codex`, `claude`, or `ollama`
- Optional: a running Ollama server for local model discovery

From a checkout:

```bash
cd hodge
python3 -m venv .venv
.venv/bin/pip install -e .
```

Make the command available from your shell:

```bash
ln -sf "$PWD/.venv/bin/hodge" ~/.local/bin/hodge
```

Confirm it works:

```bash
hodge --help
hodge agents
```

If `~/.local/bin` is not on your `PATH`, add it in your shell profile.

## Quick Start

```bash
hodge
hodge tui
hodge --session paper-shield
hodge chat --agent codex
hodge tui --agent codex
hodge chat --agent claude --model sonnet
hodge chat --agent ollama --model llama3.2
hodge agents
hodge sessions
hodge history --session paper-shield
hodge clear --session paper-shield
hodge config
hodge config set default_agent claude
hodge config set codex.default_model gpt-5.5
hodge config set codex.models "native default,gpt-5.5,gpt-5"
hodge open codex
```

Use `hodge` for the simple shared REPL. Use `hodge tui` for the dashboard.

It keeps a shared conversation transcript in `~/.hodge/history.jsonl` and sends
that transcript to the selected CLI on each turn.

Named sessions use `~/.hodge/history-<session>.jsonl`.

Specs live in `~/.hodge/specs/`. Token usage captured from CLI output lives in
`~/.hodge/usage.jsonl`.

Chat commands:

- `/status`: show agent, model, session, history count, and process-output mode
- `/usage`: show locally captured token usage for the current agent
- `/agent`: pick another configured agent
- `/model`: pick from that agent's configured model list
- `/models`: pick across all configured agents and models
- `/spec <goal>`: ask the selected agent for a requirements/design/tasks spec, with local fallback
- `/tasks`: show the current session's task checklist
- `/run <n>`: send task `n` to the selected agent and mark it done on success
- `/verbose`: toggle wrapped CLI process output
- `/history`: show recent shared history
- `/last`: show the last assistant response
- `/retry [agent]`: rerun the last user prompt with the current agent or named agent
- `/clear`: clear the current session
- `/exit`: quit

TUI mode:

- `hodge tui` opens a curses dashboard with a top bar, system/status panels, chat, task/usage panels, and input.
- TUI input accepts normal prompts and the same core slash commands: `/status`, `/tasks`, `/spec <goal>`, `/run <n>`, `/last`, `/retry [agent]`, `/agent`, `/model`, `/verbose`, `/clear`, and `/q`.
- `/agent` and `/model` cycle in TUI mode. Use `/agent claude` or `/model sonnet` to set explicit values.
- The dashboard uses Unicode box drawing, bars, and sparklines by default. Use `HODGE_ASCII=1 hodge tui` for plain ASCII fallback.

Use `hodge open <agent>` when you need the native CLI's full interactive
permission flow.

Hodge tracks token usage printed by wrapped CLIs in `~/.hodge/usage.jsonl`.
It can show local 5-hour and 7-day token totals, but account quota remaining is
not exposed by the installed CLIs.

Defaults:

- `codex`: `codex exec`
- `claude`: `claude --print`
- `ollama`: `ollama run`

Edit `~/.hodge/config.json` to add another agent.

For Ollama, Hodge tries `ollama list` for local model options and falls back to
the configured `ollama.models` list when the Ollama server is not reachable.

## Development

Run the test suite:

```bash
.venv/bin/python -m unittest
python3 -m compileall src tests
```

The package entry point is defined in `pyproject.toml`:

```bash
hodge = "hodge_cli.main:main"
```

## Known gaps

- Hodge calls each agent once per prompt. It does not yet keep native Codex or Claude sessions alive behind the shared REPL.
- Permission prompts and rich tool events are not unified. Use `hodge open <agent>` for the native interactive flow.
- Usage totals are local estimates from visible CLI output, not provider account limits.
- Specs are markdown files with checkbox parsing. The TUI is a curses dashboard, not a full Codex/Kiro-style event stream.
- Adding a brand-new agent still means editing `~/.hodge/config.json`.
