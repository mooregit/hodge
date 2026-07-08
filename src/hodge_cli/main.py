import argparse
import contextlib
import curses
import datetime as dt
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path


DEFAULT_CONFIG = {
    "default_agent": "codex",
    "agents": {
        "codex": {
            "command": ["codex", "exec"],
            "model_flag": "--model",
            "default_model": "",
            "models": ["native default", "gpt-5.5", "gpt-5"],
            "output_file_flag": "--output-last-message",
        },
        "claude": {
            "command": ["claude", "--print"],
            "model_flag": "--model",
            "default_model": "",
            "models": ["native default", "sonnet", "opus", "fable"],
        },
        "kiro": {
            "command": ["kiro-cli"],
            "model_flag": "--model",
            "default_model": "",
            "models": ["native default", "sonnet", "opus"],
        },
        "ollama": {
            "command": ["ollama", "run"],
            "model_arg": "positional",
            "default_model": "llama3.2",
            "models": ["llama3.2"],
            "local_models_command": ["ollama", "list"],
        },
    },
}


def hodge_home() -> Path:
    return Path(os.environ.get("HODGE_HOME", "~/.hodge")).expanduser()


def config_path() -> Path:
    return hodge_home() / "config.json"


def session_file(session: str) -> str:
    keep = "-_."
    name = "".join(c if c.isalnum() or c in keep else "_" for c in session).strip("._")
    return f"history-{name}.jsonl" if name and name != "default" else "history.jsonl"


def history_path(session: str = "default") -> Path:
    return hodge_home() / session_file(session)


def usage_path() -> Path:
    return hodge_home() / "usage.jsonl"


def specs_dir() -> Path:
    return hodge_home() / "specs"


def slug(text: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return value[:60] or "spec"


def load_config() -> dict:
    path = config_path()
    if not path.exists():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n")
        except OSError:
            pass
        return DEFAULT_CONFIG
    config = json.loads(path.read_text())
    config.setdefault("default_agent", DEFAULT_CONFIG["default_agent"])
    config.setdefault("agents", {})
    for name, agent in DEFAULT_CONFIG["agents"].items():
        config["agents"].setdefault(name, agent)
        for key, value in agent.items():
            config["agents"][name].setdefault(key, value)
    return config


def save_config(config: dict) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n")


def append_history(role: str, text: str, session: str = "default", agent: str | None = None) -> None:
    try:
        history_path(session).parent.mkdir(parents=True, exist_ok=True)
        row = {"role": role, "text": text}
        if agent:
            row["agent"] = agent
        with history_path(session).open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except OSError as exc:
        print(f"History not saved: {exc}", file=sys.stderr)


def read_history(session: str = "default", limit: int = 20) -> list[dict]:
    path = history_path(session)
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return rows[-limit:]


def last_message(session: str, role: str = "assistant") -> dict | None:
    for row in reversed(read_history(session, limit=10_000)):
        if row.get("role") == role:
            return row
    return None


def parse_tokens(output: str) -> int | None:
    match = re.search(r"tokens used\s*\n\s*([\d,]+)", output, re.IGNORECASE)
    return int(match.group(1).replace(",", "")) if match else None


def append_usage(agent: str, model: str, session: str, tokens: int | None) -> None:
    if tokens is None:
        return
    try:
        usage_path().parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": dt.datetime.now(dt.UTC).isoformat(),
            "agent": agent,
            "model": model or "native default",
            "session": session,
            "tokens": tokens,
        }
        with usage_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except OSError as exc:
        print(f"Usage not saved: {exc}", file=sys.stderr)


def read_usage(agent: str | None = None) -> list[dict]:
    path = usage_path()
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [row for row in rows if agent is None or row.get("agent") == agent]


def usage_summary(agent: str) -> dict:
    now = dt.datetime.now(dt.UTC)
    rows = read_usage(agent)
    for row in rows:
        row["_dt"] = dt.datetime.fromisoformat(row["ts"])
    return {
        "5h": sum(row["tokens"] for row in rows if now - row["_dt"] <= dt.timedelta(hours=5)),
        "7d": sum(row["tokens"] for row in rows if now - row["_dt"] <= dt.timedelta(days=7)),
        "last": rows[-1] if rows else None,
    }


def ascii_mode() -> bool:
    return os.environ.get("HODGE_ASCII", "").lower() in {"1", "true", "yes"}


def banner(agent: str, model: str) -> str:
    active_model = model or "native default"
    return "\n".join(
        [
            " _   _           _",
            "| | | | ___   __| | __ _  ___   .\\\\\\\\\\\\\\\\\\.",
            "| |_| |/ _ \\ / _` |/ _` |/ _ \\ " + "\\" * 13,
            "|  _  | (_) | (_| | (_| |  __/ " + "\\" * 11 + "' .\\",
            "|_| |_|\\___/ \\__,_|\\__, |\\___| `\\\\\\\\\\\\\\\\\\\\_,__o",
            "                   |___/",
            "",
            f"agent  {agent}:{active_model}",
            "spec   requirements -> design -> tasks",
            "run    shared sessions + local models",
        ]
    )


def shared_prompt(message: str, session: str = "default") -> str:
    lines = ["Shared Hodge conversation:"]
    for row in read_history(session):
        role = row["role"].upper()
        agent = f" ({row['agent']})" if row.get("agent") else ""
        lines.append(f"{role}{agent}: {row['text']}")
    lines.append(f"USER: {message}")
    return "\n\n".join(lines)


def agent_names(config: dict) -> list[str]:
    return sorted(config["agents"])


def pick(prompt: str, choices: list[str], default: str = "") -> str:
    default = default if default in choices else choices[0]
    for i, choice in enumerate(choices, 1):
        suffix = " (default)" if choice == default else ""
        print(f"{i}. {choice}{suffix}")
    raw = input(f"{prompt} [{default}]: ").strip()
    if not raw:
        return default
    if raw.isdigit() and 1 <= int(raw) <= len(choices):
        return choices[int(raw) - 1]
    if raw in choices:
        return raw
    print(f"Unknown choice: {raw}")
    return pick(prompt, choices, default)


def pick_model(agent: dict, current: str = "") -> str:
    choices = local_models(agent) or agent.get("models") or ["native default"]
    picked = pick("Model", choices, current or agent.get("default_model", "") or "native default")
    return "" if picked == "native default" else picked


def model_label(agent_name: str, model: str, agent: dict) -> str:
    source = "local" if agent_name == "ollama" or agent.get("local_models_command") else "cloud"
    default = " default" if model == (agent.get("default_model") or "native default") else ""
    return f"{agent_name}: {model} [{source}{default}]"


def pick_any_model(config: dict, current_agent: str, current_model: str) -> tuple[str, str]:
    choices = []
    values = []
    for agent_name in agent_names(config):
        agent = config["agents"][agent_name]
        models = local_models(agent) or agent.get("models") or ["native default"]
        for model in models:
            choices.append(model_label(agent_name, model, agent))
            values.append((agent_name, "" if model == "native default" else model))
    default_model = current_model or config["agents"][current_agent].get("default_model", "") or "native default"
    default = model_label(current_agent, default_model, config["agents"][current_agent])
    picked = pick("Model", choices, default if default in choices else choices[0])
    return values[choices.index(picked)]


def local_models(agent: dict) -> list[str]:
    command = agent.get("local_models_command")
    if not command or not shutil.which(command[0]):
        return []
    try:
        proc = subprocess.run(command, text=True, capture_output=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode:
        return []
    lines = proc.stdout.splitlines()[1:]
    return [line.split()[0] for line in lines if line.split()]


def build_command(agent: dict, model: str, extra: list[str], prompt: str) -> list[str]:
    cmd = list(agent["command"])
    if model:
        if agent.get("model_arg") == "positional":
            cmd.append(model)
        else:
            cmd.extend([agent.get("model_flag", "--model"), model])
    cmd.extend(extra)
    cmd.append(prompt)
    return cmd


def passthrough(extra: list[str]) -> list[str]:
    return extra[1:] if extra[:1] == ["--"] else extra


NOISE_PREFIXES = (
    "--------",
    "workdir:",
    "model:",
    "provider:",
    "sandbox:",
    "reasoning ",
    "session id:",
    "user",
    "tokens used",
    "hook:",
    "warning: skill descriptions",
)


FEEDBACK_PREFIXES = (
    "thinking",
    "working",
    "reading",
    "searching",
    "checking",
    "planning",
    "running",
    "editing",
    "applying",
    "waiting",
    "requesting",
    "permission",
    "approval",
    "tool",
    "command",
    "exec",
    "patch",
    "error",
    "warning",
    "i'll ",
    "i’m ",
    "i'm ",
)


def noise_line(text: str, agent_name: str) -> bool:
    lower = text.lower()
    if not text or lower in {agent_name, "assistant"} or lower.startswith(NOISE_PREFIXES):
        return True
    if lower.startswith("approval:"):
        return lower.removeprefix("approval:").strip() in {"never", "on-request", "on-failure", "untrusted"}
    return False


def feedback_line(agent_name: str, line: str) -> str | None:
    text = clean_text(line).strip()
    lower = text.lower()
    if noise_line(text, agent_name):
        return None
    if lower.startswith(FEEDBACK_PREFIXES) or "permission" in lower or "approval" in lower:
        return f"[{agent_name}] {text}"
    return None


def latest_spec_path(session: str) -> Path:
    return specs_dir() / f"{session_file(session).removesuffix('.jsonl')}.md"


def create_spec(session: str, goal: str) -> Path:
    path = latest_spec_path(session)
    path.parent.mkdir(parents=True, exist_ok=True)
    title = goal.strip().rstrip(".")
    path.write_text(
        "\n".join(
            [
                f"# {title}",
                "",
                "## Requirements",
                f"- {title}",
                "",
                "## Design",
                "- Keep the smallest working change.",
                "- Reuse existing project patterns before adding new structure.",
                "",
                "## Tasks",
                "- [ ] Inspect the relevant files and current behavior.",
                "- [ ] Implement the smallest working change.",
                "- [ ] Run the focused verification command.",
                "",
            ]
        )
    )
    return path


def save_spec(session: str, goal: str, content: str) -> Path:
    path = latest_spec_path(session)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not content.lstrip().startswith("#"):
        content = f"# {goal.strip().rstrip('.')}\n\n{content.strip()}\n"
    path.write_text(content.rstrip() + "\n")
    return path


def spec_prompt(goal: str) -> str:
    return "\n".join(
        [
            "Create a concise implementation spec in Markdown for this goal:",
            goal,
            "",
            "Use exactly these sections:",
            "## Requirements",
            "## Design",
            "## Tasks",
            "",
            "The Tasks section must contain 3 to 7 markdown checkbox tasks like '- [ ] Inspect ...'.",
            "Keep tasks concrete and runnable by a coding agent.",
        ]
    )


def read_tasks(path: Path) -> list[dict]:
    if not path.exists():
        return []
    tasks = []
    for line_no, line in enumerate(path.read_text().splitlines(), 1):
        match = re.match(r"- \[([ xX])\] (.+)", line)
        if match:
            tasks.append({"line": line_no, "done": match.group(1).lower() == "x", "text": match.group(2)})
    return tasks


def print_tasks(path: Path) -> None:
    tasks = read_tasks(path)
    if not tasks:
        print("No tasks. Create one with /spec <goal>.")
        return
    print(path)
    for i, task in enumerate(tasks, 1):
        mark = "x" if task["done"] else " "
        print(f"{i}. [{mark}] {task['text']}")


def task_lines(session: str) -> list[str]:
    tasks = read_tasks(latest_spec_path(session))
    if not tasks:
        return ["No spec tasks. Use /spec <goal>."]
    return [f"{i}. [{'x' if task['done'] else ' '}] {task['text']}" for i, task in enumerate(tasks, 1)]


def status_lines(config: dict, agent: str, model: str, session: str, show_process: bool) -> list[str]:
    active_model = model or config["agents"][agent].get("default_model") or "native default"
    usage = usage_summary(agent)
    return [
        f"agent {agent}",
        f"model {active_model}",
        f"session {session}",
        f"history {len(read_history(session, limit=10_000))}",
        f"usage {usage['5h']:,}/5h {usage['7d']:,}/7d",
        f"process {'on' if show_process else 'off'}",
    ]


def system_lines(config: dict) -> list[str]:
    now = dt.datetime.now().strftime("%H:%M:%S")
    try:
        load_values = list(os.getloadavg())
        load = " ".join(f"{value:.2f}" for value in load_values)
    except OSError:
        load_values = []
        load = "n/a"
    disk_path = hodge_home() if hodge_home().exists() else hodge_home().parent
    disk = shutil.disk_usage(disk_path)
    used_pct = round((disk.used / disk.total) * 100)
    agents = ", ".join(name for name, agent in config["agents"].items() if shutil.which(agent["command"][0])) or "none"
    return [
        f"time {now}",
        f"cwd {Path.cwd()}",
        f"load {sparkline(load_values)} {load}",
        f"disk {mini_bar(used_pct / 100)} {used_pct}%",
        f"agents {agents}",
    ]


def usage_lines(agent: str) -> list[str]:
    usage = usage_summary(agent)
    lines = [
        f"5h {mini_bar(min(1, usage['5h'] / 100_000))} {usage['5h']:,}",
        f"7d {mini_bar(min(1, usage['7d'] / 500_000))} {usage['7d']:,}",
    ]
    if usage["last"]:
        lines.append(f"last {usage['last']['tokens']:,}")
        lines.append(usage["last"]["ts"])
    else:
        lines.append("last no capture")
    return lines


def controls_lines() -> list[str]:
    return [
        "Enter send",
        "/q exit",
        "/tab next",
        "/tab new [agent]",
        "/agent cycle",
        "/model cycle",
        "/spec <goal>",
        "/tasks",
        "/run <n>",
        "/verbose",
    ]


def mark_task_done(path: Path, task_number: int) -> None:
    lines = path.read_text().splitlines()
    tasks = read_tasks(path)
    if not 1 <= task_number <= len(tasks):
        return
    line_index = tasks[task_number - 1]["line"] - 1
    lines[line_index] = re.sub(r"^- \[[ xX]\]", "- [x]", lines[line_index])
    path.write_text("\n".join(lines) + "\n")


def run_agent(
    config: dict,
    agent_name: str,
    model: str,
    extra: list[str],
    message: str,
    session: str,
    show_process: bool = False,
) -> int:
    agent = config["agents"][agent_name]
    exe = agent["command"][0]
    if not shutil.which(exe):
        print(f"Hodge cannot find `{exe}` on PATH.", file=sys.stderr)
        return 127

    prompt = shared_prompt(message, session)
    append_history("user", message, session)
    extra = passthrough(extra)
    output_path = None
    if agent.get("output_file_flag"):
        fd, output_path = tempfile.mkstemp(prefix="hodge-", suffix=".txt")
        os.close(fd)
        extra = [agent["output_file_flag"], output_path, *extra]
    cmd = build_command(agent, model or agent.get("default_model", ""), extra, prompt)

    if output_path and not show_process:
        print(f"{agent_name} is running...", flush=True)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    output = []
    assert proc.stdout is not None
    for line in proc.stdout:
        output.append(line)
        if not output_path or show_process:
            print(line, end="")
        elif feedback := feedback_line(agent_name, line):
            print(feedback, flush=True)
    code = proc.wait()
    proc.stdout.close()
    text = Path(output_path).read_text().strip() if output_path else "".join(output).strip()
    raw_output = "".join(output)
    active_model = model or agent.get("default_model", "") or "native default"
    append_usage(agent_name, active_model, session, parse_tokens(raw_output))
    if output_path:
        Path(output_path).unlink(missing_ok=True)
        if code:
            print("".join(output), end="")
        elif text:
            print(text)
    if text:
        append_history("assistant", text, session, agent_name)
    return code


def chat(args: argparse.Namespace) -> int:
    config = load_config()
    session = args.session or "default"
    names = agent_names(config)
    if not names:
        print("No agents configured.", file=sys.stderr)
        return 1

    agent = args.agent or pick("Agent", names, config.get("default_agent", ""))
    if agent not in config["agents"]:
        print(f"Unknown agent: {agent}", file=sys.stderr)
        return 2
    model = args.model if args.model is not None else pick_model(config["agents"][agent])
    show_process = args.verbose

    print()
    print(banner(agent, model or config["agents"][agent].get("default_model", "")))
    print()
    print(
        f"Session: {session}. Type /status, /models, /spec, /tasks, /run, /agent, /model, /verbose, /exit."
    )
    while True:
        try:
            message = input(f"hodge:{agent}> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not message:
            continue
        if message in {"/exit", "/quit"}:
            return 0
        if message == "/agent":
            agent = pick("Agent", names, agent)
            model = pick_model(config["agents"][agent])
            continue
        if message == "/models":
            agent, model = pick_any_model(config, agent, model)
            continue
        if message == "/model":
            model = pick_model(config["agents"][agent], model)
            continue
        if message == "/verbose":
            show_process = not show_process
            print(f"Process output: {'on' if show_process else 'off'}")
            continue
        if message == "/status":
            active_model = model or config["agents"][agent].get("default_model") or "native default"
            usage = usage_summary(agent)
            print(f"agent: {agent}")
            print(f"model: {active_model}")
            print(f"session: {session}")
            print(f"history: {len(read_history(session, limit=10_000))} messages")
            print(f"process output: {'on' if show_process else 'off'}")
            print(f"command: {shlex.join(config['agents'][agent]['command'])}")
            print(f"usage captured: {usage['5h']:,} tokens / 5h, {usage['7d']:,} tokens / 7d")
            if usage["last"]:
                print(f"last run: {usage['last']['tokens']:,} tokens at {usage['last']['ts']}")
            else:
                print("last run: no token usage captured yet")
            print("limit: account quota/window remaining is not exposed by this CLI")
            continue
        if message == "/usage":
            usage = usage_summary(agent)
            print(f"{agent}: {usage['5h']:,} tokens / 5h, {usage['7d']:,} tokens / 7d")
            print("limit: account quota/window remaining is not exposed by this CLI")
            continue
        if message == "/history":
            for row in read_history(session):
                print(f"{row['role']}: {row['text'][:200]}")
            continue
        if message == "/last":
            row = last_message(session)
            print(row["text"] if row else "No assistant message yet.")
            continue
        if message.startswith("/retry"):
            row = last_message(session, "user")
            if not row:
                print("No user prompt to retry.")
                continue
            retry_agent = message.removeprefix("/retry").strip() or agent
            if retry_agent not in config["agents"]:
                print(f"Unknown agent: {retry_agent}")
                continue
            code = run_agent(config, retry_agent, model if retry_agent == agent else "", args.extra, row["text"], session, show_process)
            if code:
                print(f"\n{retry_agent} exited with {code}", file=sys.stderr)
            continue
        if message.startswith("/spec"):
            goal = message.removeprefix("/spec").strip()
            if not goal:
                print("Usage: /spec <goal>")
                continue
            before = last_message(session)
            code = run_agent(config, agent, model, args.extra, spec_prompt(goal), session, show_process)
            after = last_message(session)
            if code == 0 and after and after != before:
                candidate = save_spec(session, goal, after["text"])
                path = candidate if read_tasks(candidate) else create_spec(session, goal)
            else:
                path = create_spec(session, goal)
            print(f"Spec: {path}")
            print_tasks(path)
            continue
        if message == "/tasks":
            print_tasks(latest_spec_path(session))
            continue
        if message.startswith("/run"):
            raw = message.removeprefix("/run").strip()
            if not raw.isdigit():
                print("Usage: /run <task-number>")
                continue
            task_number = int(raw)
            spec_path = latest_spec_path(session)
            tasks = read_tasks(spec_path)
            if not 1 <= task_number <= len(tasks):
                print("Unknown task.")
                continue
            task = tasks[task_number - 1]["text"]
            code = run_agent(config, agent, model, args.extra, f"Run task {task_number}: {task}", session, show_process)
            if code == 0:
                mark_task_done(spec_path, task_number)
            else:
                print(f"\n{agent} exited with {code}", file=sys.stderr)
            continue
        if message == "/clear":
            try:
                history_path(session).unlink(missing_ok=True)
                print("History cleared.")
            except OSError as exc:
                print(f"History not cleared: {exc}", file=sys.stderr)
            continue

        code = run_agent(config, agent, model, args.extra, message, session, show_process)
        if code:
            print(f"\n{agent} exited with {code}", file=sys.stderr)


def wrapped_run_agent(
    config: dict,
    agent: str,
    model: str,
    extra: list[str],
    message: str,
    session: str,
    show_process: bool,
) -> tuple[int, str]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        code = run_agent(config, agent, model, extra, message, session, show_process)
    return code, output.getvalue().strip()


def cycle_model(agent: dict, current: str) -> str:
    models = local_models(agent) or agent.get("models") or ["native default"]
    current_label = current or "native default"
    try:
        index = models.index(current_label)
    except ValueError:
        index = -1
    picked = models[(index + 1) % len(models)]
    return "" if picked == "native default" else picked


ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def clean_text(text: str) -> str:
    text = ANSI_RE.sub("", text)
    if ascii_mode():
        return "".join(ch if ch == "\n" or ch == "\t" or 32 <= ord(ch) < 127 else " " for ch in text)
    return "".join(ch if ch == "\n" or ch == "\t" or ch == " " or (ch.isprintable() and not ch.isspace()) else " " for ch in text)


def box_chars() -> dict[str, str]:
    if ascii_mode():
        return {"tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "-", "v": "|"}
    return {"tl": "╭", "tr": "╮", "bl": "╰", "br": "╯", "h": "─", "v": "│"}


def mini_bar(value: float, width: int = 12) -> str:
    value = max(0, min(1, value))
    filled = round(value * width)
    if ascii_mode():
        return "[" + "#" * filled + "." * (width - filled) + "]"
    return "▕" + "█" * filled + "░" * (width - filled) + "▏"


def sparkline(values: list[float], width: int = 12) -> str:
    if not values:
        return ""
    values = values[-width:]
    ceiling = max(values) or 1
    chars = "._-^" if ascii_mode() else "▁▂▃▄▅▆▇█"
    return "".join(chars[min(len(chars) - 1, int((value / ceiling) * (len(chars) - 1)))] for value in values)


def safe_addstr(stdscr, y: int, x: int, text: str) -> None:
    max_y, max_x = stdscr.getmaxyx()
    if y < 0 or x < 0 or y >= max_y or x >= max_x:
        return
    try:
        stdscr.addstr(y, x, clean_text(text)[: max_x - x])
    except curses.error:
        pass


def wrap_panel_lines(lines: list[str], width: int) -> list[str]:
    wrapped = []
    indent = "  " if width > 10 else ""
    for line in lines:
        clean = clean_text(line).replace("\t", "  ")
        wrapped.extend(textwrap.wrap(clean, width=max(1, width), subsequent_indent=indent) or [""])
    return wrapped


def draw_box(stdscr, y: int, x: int, h: int, w: int, title: str, lines: list[str]) -> None:
    max_y, max_x = stdscr.getmaxyx()
    h = min(h, max_y - y)
    w = min(w, max_x - x)
    if h < 3 or w < 8 or y < 0 or x < 0:
        return
    chars = box_chars()
    safe_addstr(stdscr, y, x, chars["tl"] + chars["h"] * (w - 2) + chars["tr"])
    label = f" {title} "
    safe_addstr(stdscr, y, x + 2, label[: max(0, w - 4)])
    for row in range(1, h - 1):
        safe_addstr(stdscr, y + row, x, chars["v"] + " " * (w - 2) + chars["v"])
    safe_addstr(stdscr, y + h - 1, x, chars["bl"] + chars["h"] * (w - 2) + chars["br"])
    for row, line in enumerate(wrap_panel_lines(lines, w - 2)[: h - 2], 1):
        safe_addstr(stdscr, y + row, x + 1, line[: w - 2].ljust(w - 2))


def history_lines(session: str, width: int, limit: int = 8) -> list[str]:
    lines = []
    for row in read_history(session, limit=limit):
        prefix = f"{row['role']}: "
        text = clean_text(row.get("text", "")).replace("\n", " ")
        lines.extend(textwrap.wrap(prefix + text, width=max(20, width), subsequent_indent="  "))
    return lines[-limit * 2 :]


def log_lines(log: list[str], width: int, limit: int = 80) -> list[str]:
    lines = []
    for item in log:
        for raw_line in clean_text(item).splitlines() or [""]:
            lines.extend(textwrap.wrap(raw_line, width=max(20, width), subsequent_indent="  ") or [""])
    return lines[-limit:]


TAB_FIELDS = ("agent", "model", "session", "show_process", "log")


def ensure_tabs(state: dict) -> None:
    if "tabs" in state:
        return
    state["active_tab"] = 0
    state["tabs"] = [{field: state[field] for field in TAB_FIELDS}]


def save_active_tab(state: dict) -> None:
    ensure_tabs(state)
    tab = state["tabs"][state["active_tab"]]
    for field in TAB_FIELDS:
        tab[field] = state[field]


def load_active_tab(state: dict) -> None:
    tab = state["tabs"][state["active_tab"]]
    for field in TAB_FIELDS:
        state[field] = tab[field]


def switch_tab(state: dict, index: int) -> bool:
    ensure_tabs(state)
    if not 0 <= index < len(state["tabs"]):
        state["log"].append("Unknown tab.")
        return False
    save_active_tab(state)
    state["active_tab"] = index
    load_active_tab(state)
    return True


def new_tab(state: dict, agent: str | None = None) -> None:
    ensure_tabs(state)
    save_active_tab(state)
    agent = agent or state["agent"]
    model = state["config"]["agents"][agent].get("default_model", "")
    session = f"{state['session']}-{len(state['tabs']) + 1}"
    state["tabs"].append(
        {
            "agent": agent,
            "model": model,
            "session": session,
            "show_process": state["show_process"],
            "log": [banner(agent, model)],
        }
    )
    state["active_tab"] = len(state["tabs"]) - 1
    load_active_tab(state)


def tab_bar_parts(state: dict, width: int) -> tuple[str, list[tuple[int, int, int | str]]]:
    ensure_tabs(state)
    text = ""
    regions = []
    for i, tab in enumerate(state["tabs"], 1):
        label = f"{i}:{tab['agent']}"
        part = f"[{label}]" if i - 1 == state["active_tab"] else f" {label} "
        if text:
            text += " "
        start = len(text)
        text += part
        regions.append((start, len(text), i - 1))
    if text:
        text += " "
    start = len(text)
    text += "[+]"
    regions.append((start, len(text), "new"))
    return text[:width], [(start, min(end, width), value) for start, end, value in regions if start < width]


def tab_bar(state: dict, width: int) -> str:
    return tab_bar_parts(state, width)[0]


def click_tab(state: dict, x: int, y: int) -> None:
    if y != 1:
        return
    for start, end, value in state.get("tab_regions", []):
        if start <= x - 2 < end:
            if value == "new":
                new_tab(state)
            else:
                switch_tab(state, int(value))
            return


def process_tui_command(state: dict, command: str) -> bool:
    ensure_tabs(state)
    if command in {"/tab", "/tab next"}:
        switch_tab(state, (state["active_tab"] + 1) % len(state["tabs"]))
        return True
    if command == "/tabs":
        state["log"].append(tab_bar(state, 200))
        save_active_tab(state)
        return True
    if command.startswith("/tab new"):
        agent = command.removeprefix("/tab new").strip() or state["agent"]
        if agent not in state["config"]["agents"]:
            state["log"].append(f"Unknown agent: {agent}")
            save_active_tab(state)
            return True
        new_tab(state, agent)
        return True
    if command.startswith("/tab "):
        raw = command.removeprefix("/tab ").strip()
        if raw.isdigit():
            switch_tab(state, int(raw) - 1)
        else:
            state["log"].append("Usage: /tab [next|new [agent]|<number>]")
        save_active_tab(state)
        return True

    config = state["config"]
    session = state["session"]
    agent = state["agent"]
    model = state["model"]
    extra = state["extra"]
    show_process = state["show_process"]
    log = state["log"]

    if command in {"/exit", "/quit", "/q"}:
        return False
    if command == "/status":
        log.extend(status_lines(config, agent, model, session, show_process))
    elif command == "/tasks":
        log.extend(task_lines(session))
    elif command == "/last":
        row = last_message(session)
        log.append(row["text"] if row else "No assistant message yet.")
    elif command == "/clear":
        history_path(session).unlink(missing_ok=True)
        log.append("History cleared.")
    elif command == "/agent":
        names = agent_names(config)
        state["agent"] = names[(names.index(agent) + 1) % len(names)]
        state["model"] = config["agents"][state["agent"]].get("default_model", "")
        log.append(f"agent -> {state['agent']}")
    elif command.startswith("/agent "):
        name = command.split(maxsplit=1)[1]
        if name in config["agents"]:
            state["agent"] = name
            state["model"] = config["agents"][name].get("default_model", "")
            log.append(f"agent -> {name}")
        else:
            log.append(f"Unknown agent: {name}")
    elif command == "/model":
        state["model"] = cycle_model(config["agents"][agent], model)
        log.append(f"model -> {state['model'] or 'native default'}")
    elif command.startswith("/model "):
        raw = command.split(maxsplit=1)[1]
        state["model"] = "" if raw == "native default" else raw
        log.append(f"model -> {state['model'] or 'native default'}")
    elif command == "/verbose":
        state["show_process"] = not show_process
        log.append(f"process output -> {'on' if state['show_process'] else 'off'}")
    elif command.startswith("/spec "):
        goal = command.removeprefix("/spec ").strip()
        log.append("drafting spec...")
        before = last_message(session)
        code, output = wrapped_run_agent(config, agent, model, extra, spec_prompt(goal), session, show_process)
        after = last_message(session)
        if code == 0 and after and after != before:
            candidate = save_spec(session, goal, after["text"])
            path = candidate if read_tasks(candidate) else create_spec(session, goal)
        else:
            path = create_spec(session, goal)
        log.append(f"Spec: {path}")
        if output:
            log.append(output)
    elif command.startswith("/run "):
        raw = command.removeprefix("/run ").strip()
        if not raw.isdigit():
            log.append("Usage: /run <task-number>")
        else:
            task_number = int(raw)
            spec_path = latest_spec_path(session)
            tasks = read_tasks(spec_path)
            if not 1 <= task_number <= len(tasks):
                log.append("Unknown task.")
            else:
                task = tasks[task_number - 1]["text"]
                log.append(f"running task {task_number}...")
                code, output = wrapped_run_agent(
                    config, agent, model, extra, f"Run task {task_number}: {task}", session, show_process
                )
                if output:
                    log.append(output)
                if code == 0:
                    mark_task_done(spec_path, task_number)
                else:
                    log.append(f"{agent} exited with {code}")
    elif command.startswith("/retry"):
        row = last_message(session, "user")
        retry_agent = command.removeprefix("/retry").strip() or agent
        if not row:
            log.append("No user prompt to retry.")
        elif retry_agent not in config["agents"]:
            log.append(f"Unknown agent: {retry_agent}")
        else:
            log.append(f"retrying with {retry_agent}...")
            code, output = wrapped_run_agent(
                config, retry_agent, model if retry_agent == agent else "", extra, row["text"], session, show_process
            )
            if output:
                log.append(output)
            if code:
                log.append(f"{retry_agent} exited with {code}")
    else:
        log.append(f"Unknown command: {command}")
    state["log"] = log[-100:]
    save_active_tab(state)
    return True


def process_tui_input(state: dict, text: str) -> bool:
    ensure_tabs(state)
    if not text:
        return True
    if text.startswith("/"):
        return process_tui_command(state, text)
    state["log"].append(f"user: {text}")
    code, output = wrapped_run_agent(
        state["config"],
        state["agent"],
        state["model"],
        state["extra"],
        text,
        state["session"],
        state["show_process"],
    )
    if output:
        state["log"].append(output)
    if code:
        state["log"].append(f"{state['agent']} exited with {code}")
    state["log"] = state["log"][-100:]
    save_active_tab(state)
    return True


def tui_loop(stdscr, state: dict) -> None:
    ensure_tabs(state)
    curses.curs_set(1)
    with contextlib.suppress(curses.error):
        curses.mousemask(curses.BUTTON1_CLICKED)
    input_text = ""
    while True:
        ensure_tabs(state)
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        if height < 10 or width < 40:
            safe_addstr(stdscr, 0, 0, "Terminal too small for Hodge TUI. Resize or press q.")
            stdscr.refresh()
            ch = stdscr.get_wch()
            if ch in {"q", "Q", "\n", "\r"}:
                return
            continue
        top_bar_h = 3
        input_y = height - 3

        top_label = " HODGE "
        safe_addstr(stdscr, 0, 0, top_label + box_chars()["h"] * max(0, width - len(top_label)))
        tabs, regions = tab_bar_parts(state, max(10, width - 4))
        state["tab_regions"] = regions
        safe_addstr(stdscr, 1, 2, tabs)
        safe_addstr(stdscr, 2, 0, box_chars()["h"] * width)

        if width >= 120 and height >= 22:
            left_w = 30
            right_w = 32
            chat_x = left_w + 1
            chat_w = width - left_w - right_w - 2
            body_y = top_bar_h
            body_h = input_y - body_y
            left_top_h = min(9, max(6, body_h // 3))
            right_top_h = max(8, body_h // 2)

            draw_box(stdscr, body_y, 0, left_top_h, left_w, "System", system_lines(state["config"]))
            draw_box(stdscr, body_y + left_top_h, 0, body_h - left_top_h, left_w, "Status", status_lines(
                state["config"], state["agent"], state["model"], state["session"], state["show_process"]
            ))
            chat_lines = history_lines(state["session"], chat_w - 4, limit=10) + log_lines(state["log"], chat_w - 4)
            draw_box(stdscr, body_y, chat_x, body_h, chat_w, "Chat", chat_lines)
            draw_box(stdscr, body_y, chat_x + chat_w + 1, right_top_h, right_w, "Tasks", task_lines(state["session"]))
            draw_box(stdscr, body_y + right_top_h, chat_x + chat_w + 1, 6, right_w, "Usage", usage_lines(state["agent"]))
            draw_box(
                stdscr,
                body_y + right_top_h + 6,
                chat_x + chat_w + 1,
                body_h - right_top_h - 6,
                right_w,
                "Controls",
                controls_lines(),
            )
        else:
            left_w = max(24, min(32, width // 4))
            right_w = width - left_w - 1
            top_h = min(8, max(6, (input_y - top_bar_h) // 4))
            body_y = top_bar_h
            draw_box(stdscr, body_y, 0, top_h, left_w, "Status", status_lines(
                state["config"], state["agent"], state["model"], state["session"], state["show_process"]
            ))
            draw_box(stdscr, body_y + top_h, 0, max(5, input_y - body_y - top_h), left_w, "Tasks", task_lines(state["session"]))
            chat_lines = history_lines(state["session"], right_w - 4) + log_lines(state["log"], right_w - 4)
            draw_box(stdscr, body_y, left_w + 1, input_y - body_y, right_w, "Chat", chat_lines)

        draw_box(stdscr, input_y, 0, 3, width, "Input", [clean_text(input_text)])
        safe_addstr(stdscr, height - 1, 2, "Click tabs or [+]. Enter sends. /q exits. /agent /model /run <n>")
        with contextlib.suppress(curses.error):
            stdscr.move(input_y + 1, min(width - 2, 1 + len(input_text)))
        stdscr.refresh()

        ch = stdscr.get_wch()
        if ch == curses.KEY_MOUSE:
            with contextlib.suppress(curses.error):
                _, x, y, _, _ = curses.getmouse()
                click_tab(state, x, y)
        elif ch in ("\n", "\r"):
            if not process_tui_input(state, input_text.strip()):
                return
            input_text = ""
        elif ch in ("\b", "\x7f", curses.KEY_BACKSPACE):
            input_text = input_text[:-1]
        elif isinstance(ch, str) and ch.isprintable():
            input_text += ch


def tui(args: argparse.Namespace) -> int:
    config = load_config()
    agent = args.agent or config.get("default_agent", "codex")
    if agent not in config["agents"]:
        print(f"Unknown agent: {agent}", file=sys.stderr)
        return 2
    state = {
        "config": config,
        "session": args.session or "default",
        "agent": agent,
        "model": args.model or config["agents"][agent].get("default_model", ""),
        "show_process": args.verbose,
        "extra": args.extra,
        "log": [banner(agent, args.model or config["agents"][agent].get("default_model", ""))],
    }
    ensure_tabs(state)
    try:
        curses.wrapper(tui_loop, state)
        return 0
    except curses.error as exc:
        print(f"TUI unavailable: {exc}", file=sys.stderr)
        return 1


def list_agents(args: argparse.Namespace) -> int:
    config = load_config()
    for name, agent in config["agents"].items():
        available = "ok" if shutil.which(agent["command"][0]) else "missing"
        print(f"{name}: {shlex.join(agent['command'])} ({available})")
    return 0


def show_history(args: argparse.Namespace) -> int:
    for row in read_history(args.session or "default", limit=args.limit):
        agent = f" ({row['agent']})" if row.get("agent") else ""
        print(f"{row['role']}{agent}: {row['text']}")
    return 0


def clear_history(args: argparse.Namespace) -> int:
    try:
        history_path(args.session or "default").unlink(missing_ok=True)
        print("History cleared.")
        return 0
    except OSError as exc:
        print(f"History not cleared: {exc}", file=sys.stderr)
        return 1


def list_sessions(args: argparse.Namespace) -> int:
    home = hodge_home()
    if not home.exists():
        return 0
    for path in sorted(home.glob("history*.jsonl")):
        name = "default" if path.name == "history.jsonl" else path.stem.removeprefix("history-")
        print(name)
    return 0


def configure(args: argparse.Namespace) -> int:
    config = load_config()
    if not args.key:
        print(json.dumps(config, indent=2))
        return 0
    if len(args.value) != 1:
        print("Usage: hodge config set <key> <value>", file=sys.stderr)
        return 2
    value = args.value[0]
    if args.key == "default_agent":
        if value not in config["agents"]:
            print(f"Unknown agent: {value}", file=sys.stderr)
            return 2
        config["default_agent"] = value
    elif args.key.endswith(".default_model"):
        agent = args.key.removesuffix(".default_model")
        if agent not in config["agents"]:
            print(f"Unknown agent: {agent}", file=sys.stderr)
            return 2
        config["agents"][agent]["default_model"] = value
    elif args.key.endswith(".models"):
        agent = args.key.removesuffix(".models")
        if agent not in config["agents"]:
            print(f"Unknown agent: {agent}", file=sys.stderr)
            return 2
        config["agents"][agent]["models"] = [item.strip() for item in value.split(",") if item.strip()]
    else:
        print("Supported keys: default_agent, <agent>.default_model, <agent>.models", file=sys.stderr)
        return 2
    save_config(config)
    return 0


def open_agent(args: argparse.Namespace) -> int:
    config = load_config()
    if args.agent not in config["agents"]:
        print(f"Unknown agent: {args.agent}", file=sys.stderr)
        return 2
    agent = config["agents"][args.agent]
    exe = agent["command"][0]
    if not shutil.which(exe):
        print(f"Hodge cannot find `{exe}` on PATH.", file=sys.stderr)
        return 127
    cmd = [exe, *passthrough(args.extra)]
    return subprocess.call(cmd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hodge")
    parser.add_argument("--session", default="default")
    sub = parser.add_subparsers(dest="command")

    chat_parser = sub.add_parser("chat")
    chat_parser.add_argument("--agent")
    chat_parser.add_argument("--model")
    chat_parser.add_argument("--session")
    chat_parser.add_argument("--verbose", action="store_true")
    chat_parser.add_argument("extra", nargs=argparse.REMAINDER)
    chat_parser.set_defaults(func=chat)

    tui_parser = sub.add_parser("tui")
    tui_parser.add_argument("--agent")
    tui_parser.add_argument("--model")
    tui_parser.add_argument("--session")
    tui_parser.add_argument("--verbose", action="store_true")
    tui_parser.add_argument("extra", nargs=argparse.REMAINDER)
    tui_parser.set_defaults(func=tui)

    agents_parser = sub.add_parser("agents")
    agents_parser.set_defaults(func=list_agents)

    history_parser = sub.add_parser("history")
    history_parser.add_argument("--session")
    history_parser.add_argument("--limit", type=int, default=20)
    history_parser.set_defaults(func=show_history)

    clear_parser = sub.add_parser("clear")
    clear_parser.add_argument("--session")
    clear_parser.set_defaults(func=clear_history)

    sessions_parser = sub.add_parser("sessions")
    sessions_parser.set_defaults(func=list_sessions)

    config_parser = sub.add_parser("config")
    config_sub = config_parser.add_subparsers(dest="config_command")
    config_parser.set_defaults(func=configure, key=None, value=[])
    config_set = config_sub.add_parser("set")
    config_set.add_argument("key")
    config_set.add_argument("value", nargs=argparse.REMAINDER)
    config_set.set_defaults(func=configure)

    open_parser = sub.add_parser("open")
    open_parser.add_argument("agent")
    open_parser.add_argument("extra", nargs=argparse.REMAINDER)
    open_parser.set_defaults(func=open_agent)

    args = parser.parse_args(argv)
    if args.command is None:
        args.command = "chat"
        args.agent = None
        args.model = None
        args.verbose = False
        args.extra = []
        args.func = chat
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
