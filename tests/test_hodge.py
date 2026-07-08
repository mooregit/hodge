import os
import sys
import tempfile
import unittest
import datetime as dt
import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hodge_cli.main import (
    DEFAULT_CONFIG,
    banner,
    box_chars,
    build_command,
    clean_text,
    click_tab,
    configure,
    create_spec,
    feedback_line,
    last_message,
    list_sessions,
    local_models,
    log_lines,
    mini_bar,
    mark_task_done,
    passthrough,
    parse_tokens,
    pick_any_model,
    pick_model,
    process_tui_command,
    read_tasks,
    read_history,
    read_usage,
    run_agent,
    save_spec,
    safe_addstr,
    session_file,
    shared_prompt,
    spec_prompt,
    sparkline,
    status_lines,
    tab_bar,
    tab_bar_parts,
    system_lines,
    task_lines,
    usage_lines,
    usage_path,
    usage_summary,
    wrap_panel_lines,
)


def subprocess_result(stdout: str, returncode: int = 0):
    return type("Result", (), {"stdout": stdout, "returncode": returncode})()


class FakeScreen:
    def __init__(self, height=2, width=5):
        self.height = height
        self.width = width
        self.calls = []

    def getmaxyx(self):
        return self.height, self.width

    def addstr(self, y, x, text):
        if y >= self.height or x >= self.width:
            raise AssertionError("out of bounds")
        self.calls.append((y, x, text))


class HodgeTests(unittest.TestCase):
    def test_banner_includes_hodge_hedgehog_and_agent(self):
        text = banner("codex", "gpt-5.5")
        self.assertIn(r".\\\\\\\\\.", text)
        self.assertIn(r"`\\\\\\\\\\_,__o", text)
        self.assertIn("agent  codex:gpt-5.5", text)

    def test_build_command_adds_model_and_prompt(self):
        agent = DEFAULT_CONFIG["agents"]["codex"]
        self.assertEqual(
            build_command(agent, "gpt-5", ["--search"], "hi"),
            ["codex", "exec", "--model", "gpt-5", "--search", "hi"],
        )

    def test_build_command_supports_kiro_cli(self):
        agent = DEFAULT_CONFIG["agents"]["kiro"]
        self.assertEqual(
            build_command(agent, "sonnet", [], "hi"),
            ["kiro-cli", "--model", "sonnet", "hi"],
        )

    def test_build_command_supports_positional_model(self):
        agent = DEFAULT_CONFIG["agents"]["ollama"]
        self.assertEqual(build_command(agent, "llama3.2", [], "hi"), ["ollama", "run", "llama3.2", "hi"])

    def test_passthrough_strips_separator(self):
        self.assertEqual(passthrough(["--", "--search"]), ["--search"])

    def test_feedback_line_filters_noise(self):
        self.assertIsNone(feedback_line("codex", "model: gpt-5.5"))
        self.assertIsNone(feedback_line("codex", "approval: never"))
        self.assertEqual(feedback_line("codex", "approval: run command?"), "[codex] approval: run command?")
        self.assertEqual(feedback_line("codex", "checking files"), "[codex] checking files")
        self.assertEqual(feedback_line("codex", "permission required"), "[codex] permission required")

    def test_parse_codex_tokens(self):
        self.assertEqual(parse_tokens("tokens used\n13,870\n"), 13870)
        self.assertIsNone(parse_tokens("no usage"))

    def test_pick_model_maps_native_default_to_empty(self):
        with patch("builtins.input", return_value=""), redirect_stdout(StringIO()):
            self.assertEqual(pick_model({"models": ["native default"], "default_model": ""}), "")

    def test_pick_any_model_can_switch_agents(self):
        config = {
            "agents": {
                "codex": {"models": ["native default"], "default_model": ""},
                "ollama": {"models": ["llama3.2"], "default_model": "llama3.2"},
            }
        }
        with patch("builtins.input", return_value="2"), redirect_stdout(StringIO()):
            self.assertEqual(pick_any_model(config, "codex", ""), ("ollama", "llama3.2"))

    def test_local_models_parses_ollama_list(self):
        proc = subprocess_result("NAME ID SIZE MODIFIED\nllama3.2:latest abc 2 GB now\n")
        with patch("shutil.which", return_value="/usr/local/bin/ollama"), patch(
            "subprocess.run", return_value=proc
        ):
            self.assertEqual(local_models({"local_models_command": ["ollama", "list"]}), ["llama3.2:latest"])

    def test_session_file_is_safe(self):
        self.assertEqual(session_file("default"), "history.jsonl")
        self.assertEqual(session_file("paper shield"), "history-paper_shield.jsonl")

    def test_shared_prompt_includes_history(self):
        old_home = os.environ.get("HODGE_HOME")
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["HODGE_HOME"] = tmp
            from hodge_cli.main import append_history

            append_history("user", "old question")
            prompt = shared_prompt("new question")
            self.assertIn("old question", prompt)
            self.assertIn("new question", prompt)
        if old_home is None:
            os.environ.pop("HODGE_HOME", None)
        else:
            os.environ["HODGE_HOME"] = old_home

    def test_last_message_finds_latest_role(self):
        old_home = os.environ.get("HODGE_HOME")
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["HODGE_HOME"] = tmp
            from hodge_cli.main import append_history

            append_history("assistant", "old")
            append_history("user", "question")
            append_history("assistant", "new")
            self.assertEqual(last_message("default")["text"], "new")
            self.assertEqual(last_message("default", "user")["text"], "question")
        if old_home is None:
            os.environ.pop("HODGE_HOME", None)
        else:
            os.environ["HODGE_HOME"] = old_home

    def test_run_agent_saves_output_file_text_only(self):
        old_home = os.environ.get("HODGE_HOME")
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["HODGE_HOME"] = tmp
            script = Path(tmp) / "fake_agent.py"
            script.write_text(
                "import sys\n"
                "path = sys.argv[sys.argv.index('--out') + 1]\n"
                "print('noise')\n"
                "print('tokens used')\n"
                "print('123')\n"
                "with open(path, 'w') as f:\n"
                "    f.write('final answer')\n"
            )
            config = {
                "agents": {
                    "fake": {
                        "command": [sys.executable, str(script)],
                        "output_file_flag": "--out",
                    }
                }
            }
            with redirect_stdout(StringIO()):
                code = run_agent(config, "fake", "", [], "hello", "quiet")
            self.assertEqual(code, 0)
            rows = read_history("quiet")
            self.assertEqual(rows[-1]["text"], "final answer")
            usage = read_usage("fake")
            self.assertEqual(usage[-1]["tokens"], 123)
        if old_home is None:
            os.environ.pop("HODGE_HOME", None)
        else:
            os.environ["HODGE_HOME"] = old_home

    def test_usage_summary_windows(self):
        old_home = os.environ.get("HODGE_HOME")
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["HODGE_HOME"] = tmp
            now = dt.datetime.now(dt.timezone.utc)
            rows = [
                {"ts": (now - dt.timedelta(hours=1)).isoformat(), "agent": "codex", "tokens": 10},
                {"ts": (now - dt.timedelta(days=2)).isoformat(), "agent": "codex", "tokens": 20},
                {"ts": (now - dt.timedelta(days=8)).isoformat(), "agent": "codex", "tokens": 30},
            ]
            usage_path().write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            summary = usage_summary("codex")
            self.assertEqual(summary["5h"], 10)
            self.assertEqual(summary["7d"], 30)
        if old_home is None:
            os.environ.pop("HODGE_HOME", None)
        else:
            os.environ["HODGE_HOME"] = old_home

    def test_spec_tasks_can_be_created_and_completed(self):
        old_home = os.environ.get("HODGE_HOME")
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["HODGE_HOME"] = tmp
            path = create_spec("default", "Build a thing")
            tasks = read_tasks(path)
            self.assertEqual(len(tasks), 3)
            self.assertFalse(tasks[0]["done"])
            mark_task_done(path, 1)
            self.assertTrue(read_tasks(path)[0]["done"])
        if old_home is None:
            os.environ.pop("HODGE_HOME", None)
        else:
            os.environ["HODGE_HOME"] = old_home

    def test_panel_helpers_show_status_and_tasks(self):
        old_home = os.environ.get("HODGE_HOME")
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["HODGE_HOME"] = tmp
            create_spec("default", "Build a thing")
            lines = status_lines(DEFAULT_CONFIG, "codex", "", "default", False)
            self.assertIn("agent codex", lines)
            self.assertIn("1. [ ] Inspect the relevant files and current behavior.", task_lines("default"))
            self.assertTrue(any(line.startswith("time ") for line in system_lines(DEFAULT_CONFIG)))
            self.assertTrue(any(line.startswith("5h ") for line in usage_lines("codex")))
        if old_home is None:
            os.environ.pop("HODGE_HOME", None)
        else:
            os.environ["HODGE_HOME"] = old_home

    def test_tui_command_cycles_agent_and_model(self):
        state = {
            "config": DEFAULT_CONFIG,
            "session": "default",
            "agent": "codex",
            "model": "",
            "show_process": False,
            "extra": [],
            "log": [],
        }
        self.assertTrue(process_tui_command(state, "/agent claude"))
        self.assertEqual(state["agent"], "claude")
        self.assertTrue(process_tui_command(state, "/model"))
        self.assertEqual(state["model"], "sonnet")

    def test_tui_tabs_switch_agents(self):
        state = {
            "config": DEFAULT_CONFIG,
            "session": "default",
            "agent": "codex",
            "model": "",
            "show_process": False,
            "extra": [],
            "log": [],
        }
        self.assertTrue(process_tui_command(state, "/tab new claude"))
        self.assertEqual(state["agent"], "claude")
        self.assertEqual(state["session"], "default-2")
        self.assertIn("[2:claude]", tab_bar(state, 80))
        self.assertTrue(process_tui_command(state, "/tab 1"))
        self.assertEqual(state["agent"], "codex")

    def test_tui_tab_click_regions(self):
        state = {
            "config": DEFAULT_CONFIG,
            "session": "default",
            "agent": "codex",
            "model": "",
            "show_process": False,
            "extra": [],
            "log": [],
        }
        process_tui_command(state, "/tab new claude")
        _, regions = tab_bar_parts(state, 80)
        state["tab_regions"] = regions
        click_tab(state, regions[0][0] + 2, 1)
        self.assertEqual(state["agent"], "codex")
        click_tab(state, regions[-1][0] + 2, 1)
        self.assertEqual(len(state["tabs"]), 3)

    def test_safe_addstr_clips_to_screen(self):
        screen = FakeScreen()
        safe_addstr(screen, 0, 3, "abcdef")
        safe_addstr(screen, 3, 0, "ignored")
        self.assertEqual(screen.calls, [(0, 3, "ab")])

    def test_clean_text_removes_ansi_and_controls(self):
        self.assertEqual(clean_text("\x1b[?25lhello\x1b[0m\x07"), "hello ")
        self.assertEqual(clean_text("╭─╮"), "╭─╮")

    def test_log_lines_wraps_clean_text(self):
        self.assertEqual(log_lines(["\x1b[31mabcdef"], 20), ["abcdef"])

    def test_wrap_panel_lines_wraps_long_text(self):
        self.assertEqual(wrap_panel_lines(["abcdef"], 3), ["abc", "def"])

    def test_dashboard_graphics_have_ascii_fallback(self):
        self.assertEqual(box_chars()["tl"], "╭")
        self.assertTrue(mini_bar(0.5).startswith("▕"))
        self.assertTrue(sparkline([1, 2, 3]))
        with patch.dict(os.environ, {"HODGE_ASCII": "1"}):
            self.assertEqual(box_chars()["tl"], "+")
            self.assertEqual(mini_bar(0.5, 4), "[##..]")
            self.assertTrue(set(sparkline([1, 2, 3])).issubset(set("._-^")))

    def test_save_agent_spec_and_prompt_shape(self):
        old_home = os.environ.get("HODGE_HOME")
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["HODGE_HOME"] = tmp
            prompt = spec_prompt("Add retry")
            self.assertIn("## Tasks", prompt)
            path = save_spec("default", "Add retry", "## Tasks\n- [ ] Do it\n")
            self.assertEqual(read_tasks(path)[0]["text"], "Do it")
        if old_home is None:
            os.environ.pop("HODGE_HOME", None)
        else:
            os.environ["HODGE_HOME"] = old_home

    def test_config_set_default_agent(self):
        old_home = os.environ.get("HODGE_HOME")
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["HODGE_HOME"] = tmp
            args = type("Args", (), {"key": "default_agent", "value": ["claude"]})()
            self.assertEqual(configure(args), 0)
            self.assertIn('"default_agent": "claude"', (Path(tmp) / "config.json").read_text())
        if old_home is None:
            os.environ.pop("HODGE_HOME", None)
        else:
            os.environ["HODGE_HOME"] = old_home

    def test_config_set_agent_models(self):
        old_home = os.environ.get("HODGE_HOME")
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["HODGE_HOME"] = tmp
            args = type("Args", (), {"key": "codex.models", "value": ["native default,o3"]})()
            self.assertEqual(configure(args), 0)
            self.assertIn('"o3"', (Path(tmp) / "config.json").read_text())
        if old_home is None:
            os.environ.pop("HODGE_HOME", None)
        else:
            os.environ["HODGE_HOME"] = old_home

    def test_list_sessions_prints_history_files(self):
        old_home = os.environ.get("HODGE_HOME")
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["HODGE_HOME"] = tmp
            Path(tmp, "history.jsonl").write_text("")
            Path(tmp, "history-paper.jsonl").write_text("")
            with redirect_stdout(StringIO()) as out:
                self.assertEqual(list_sessions(type("Args", (), {})()), 0)
            self.assertEqual(out.getvalue().splitlines(), ["paper", "default"])
        if old_home is None:
            os.environ.pop("HODGE_HOME", None)
        else:
            os.environ["HODGE_HOME"] = old_home


if __name__ == "__main__":
    unittest.main()
