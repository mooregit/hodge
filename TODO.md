# Hodge TODO

## Near Term

- Add `hodge config add <agent> <command...>` so new CLI agents do not require manual JSON edits.
- Add `hodge config remove <agent>` and basic validation for broken agent configs.
- Add `/specs` to list saved specs and `/spec open` or `/spec show` for the current spec.
- Add `/done <n>` and `/undone <n>` for manual task checklist control.
- Add `/retry --model <model>` or a retry picker so reruns can switch model without switching agents first.
- Capture more usage formats, especially Claude and Ollama verbose output, when available.

## Workflow

- Improve `/spec <goal>` so the agent drafts better requirements/design/tasks and preserves failed drafts for inspection.
- Include spec context automatically when running `/run <n>`, not just the single task text.
- Add a compact `/summary` command for session history and current spec state.
- Add optional project-local storage, such as `.hodge/`, for repo-specific specs and sessions.

## Native Agent Integration

- Improve native session handoff for `hodge open codex|claude|ollama`, preserving Hodge session metadata and making the boundary clear.
- Investigate whether Codex/Claude expose streamable events or JSON output that can surface tool calls, permission requests, and richer process state inside Hodge.
- Keep Hodge's shared REPL and native agent histories aligned where the underlying CLIs support it.

## Interface

- Expand `hodge tui` with scrollback, focused panes, and better keyboard navigation.
- Add a richer status panel that shows current spec path and pending task count.
- Add a model picker view with cloud/local labels, default markers, and configurable cost hints.
- Improve fallback messaging for terminals that cannot run curses or are too small.

## Later

- Add hook support for common checks, such as tests, lint, or format commands.
- Add import/export for sessions and specs.
- Add packaging/install notes beyond the current editable virtualenv setup.
