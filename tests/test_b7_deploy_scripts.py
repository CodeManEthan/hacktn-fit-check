"""[B7] The deploy scripts parse, the live checker reads the seed's output, and
nothing under deploy/ carries an OCID or a password.

The fix-pass tests run pieces of the scripts locally: bootstrap's code step and
redeploy's remote stage and cutover scripts, with /opt rewritten to a temporary
directory, a local bare repository for the fork, and stubs for sudo, systemctl and
the other VM commands. Nothing here reaches the VM."""

from __future__ import annotations

import importlib.util
import os
import py_compile
import re
import shutil
import socket
import stat
import subprocess
import sys
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"
SHELL_SCRIPTS = sorted(DEPLOY.glob("*.sh"))
PY_SCRIPTS = sorted(DEPLOY.glob("*.py"))

needs_git_bash = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None, reason="no bash or git")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"deploy_{name}", DEPLOY / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- local harness -------------------------------------------------------------------

def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         *args], check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _commit(work: Path, name: str) -> str:
    (work / name).write_text(name + "\n")
    _git(work, "add", name)
    _git(work, "commit", "-q", "-m", name)
    return _git(work, "rev-parse", "HEAD")


class Fork:
    """A bare repository standing in for the fork: main, plus a side branch."""

    def __init__(self, root: Path):
        self.bare = root / "fork.git"
        self.work = root / "fork-work"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.bare)], check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.work)], check=True)
        _git(self.work, "remote", "add", "origin", str(self.bare))
        self.first = _commit(self.work, "requirements.txt")
        self.main = _commit(self.work, "one")
        _git(self.work, "push", "-q", "origin", "main")
        _git(self.work, "checkout", "-q", "-b", "side")
        self.side = _commit(self.work, "side")
        _git(self.work, "push", "-q", "origin", "side")
        _git(self.work, "checkout", "-q", "main")

    @property
    def url(self) -> str:
        return str(self.bare)

    def advance(self, name: str) -> str:
        head = _commit(self.work, name)
        _git(self.work, "push", "-q", "origin", "main")
        return head


def _stubs(tmp_path: Path, **scripts: str) -> dict[str, str]:
    """Executables on PATH ahead of the real ones; returns the environment to run with."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for name, body in scripts.items():
        path = bindir / name
        path.write_text("#!/usr/bin/env bash\n" + body + "\n")
        path.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(PATH=f"{bindir}{os.pathsep}{env['PATH']}", HOME=str(home),
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return env


SUDO = 'exec "$@"'


def _run(script: str, env: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-s", "--", *args], input=script, env=env,
                          capture_output=True, text=True, timeout=120)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _remote(function: str, root: Path, port: int | None = None) -> str:
    """The heredoc that redeploy.sh's function sends to the VM, with /opt under root."""
    text = (DEPLOY / "redeploy.sh").read_text()
    body = text[text.index(f"\n{function}() {{"):]
    body = body[body.index("<<'REMOTE'\n") + len("<<'REMOTE'\n"):]
    body = body[:body.index("\nREMOTE\n") + 1]
    body = body.replace("/opt/fitcheck", str(root / "opt" / "fitcheck"))
    if port is not None:
        assert "PORT=8001;" in body
        body = body.replace("PORT=8001;", f"PORT={port};")
    return body


VM_STUBS = {
    "sudo": SUDO,
    "systemctl": 'echo "systemctl $*" >> "$STUB_LOG"\n'
                 'if [ "$1" = is-active ]; then echo "${STUB_UNIT_STATE:-inactive}";'
                 ' [ "${STUB_UNIT_STATE:-inactive}" = active ]; exit; fi',
    "systemd-run": 'echo "systemd-run $*" >> "$STUB_LOG"',
    "journalctl": 'echo "(journal)"',
    "sleep": "exit 0",
    "python3.12": f'''if [ "$1 $2" = "-m venv" ]; then
  mkdir -p "$3/bin"
  printf '#!/bin/sh\\nexec {sys.executable} "$@"\\n' > "$3/bin/python"
  printf '#!/bin/sh\\nexit 0\\n' > "$3/bin/pip"
  chmod +x "$3/bin/python" "$3/bin/pip"
fi''',
}


def _vm(tmp_path: Path, **overrides: str) -> dict[str, str]:
    """A stand-in VM under tmp_path: /opt/fitcheck holds the .env, commands are stubbed."""
    (tmp_path / "opt" / "fitcheck").mkdir(parents=True, exist_ok=True)
    (tmp_path / "opt" / "fitcheck" / ".env").write_text("KEY=value\n")
    env = _stubs(tmp_path, **{**VM_STUBS, **overrides})
    env["STUB_LOG"] = str(tmp_path / "stub.log")
    return env


def _bootstrap_code_step(repo: str, app_dir: Path) -> str:
    text = (DEPLOY / "bootstrap.sh").read_text()
    block = text[text.index('echo "==> code"'):text.index('echo "==> virtualenv')]
    return (f'set -euo pipefail\nREPO="{repo}"\nAPP_DIR="{app_dir}"\n'
            f'RUN_USER="$(id -un)"\n' + block)


def test_the_deploy_scripts_exist():
    names = {p.name for p in SHELL_SCRIPTS + PY_SCRIPTS}
    assert {"bootstrap.sh", "redeploy.sh", "check_live.py", "demo_ids.py",
            "instance_principals.py"} <= names


@pytest.mark.skipif(shutil.which("bash") is None, reason="no bash")
@pytest.mark.parametrize("script", SHELL_SCRIPTS, ids=lambda p: p.name)
def test_shell_scripts_parse(script):
    result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("script", PY_SCRIPTS, ids=lambda p: p.name)
def test_python_helpers_compile(script, tmp_path):
    py_compile.compile(str(script), cfile=str(tmp_path / "out.pyc"), doraise=True)


def test_bootstrap_points_at_the_fork_on_port_80_with_the_capability():
    text = (DEPLOY / "bootstrap.sh").read_text()
    assert 'REPO="https://github.com/CodeManEthan/hacktn-fit-check.git"' in text
    assert 'PORT="80"' in text
    assert "AmbientCapabilities=CAP_NET_BIND_SERVICE" in text


def test_nothing_under_deploy_carries_an_ocid_or_a_password():
    for path in (p for p in DEPLOY.iterdir() if p.is_file() and p.suffix in (".sh", ".py", ".md")):
        text = path.read_text()
        assert "ocid1." not in text, path.name
        assert not re.search(r"mysql\+pymysql://[^<\s:]+:[^<\s@]+@", text), path.name


def test_check_live_reads_the_seed_output():
    parse = _load("check_live").parse
    out = (
        "facilities: 3\n"
        "  offer 8 added: DeVilbiss Healthcare 525DS x1 from Nashville\n"
        "  offer 9 replaced: DeVilbiss Healthcare 525KS x1 from Leeds\n"
        "  offer 10 added: this device x2 from Houston\n"
        "  offer 1 replaced: Puritan Bennett 840 x3 from Nashville\n"
        "  answer 1: oxygen concentrator 525KS in Uganda\n"
        "  request 1 kept: Kayunga, 1 oxygen concentrator\n"
    )
    request_id, hero = parse(out)
    assert request_id == 1
    assert [offer_id for offer_id, _ in hero] == [8, 9, 10]
    assert "525DS" in hero[0][1] and "525KS" in hero[1][1]


def test_check_live_fails_without_ids(monkeypatch, capsys):
    module = _load("check_live")
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(""))
    assert module.main(["check_live.py", "http://127.0.0.1:1"]) == 1
    assert "could not read" in capsys.readouterr().out


# --- fix pass 1 ----------------------------------------------------------------------

@needs_git_bash
def test_l1_bootstrap_clones_when_the_parent_of_the_app_dir_is_not_writable(tmp_path):
    """/opt is root's on the VM: the clone must not need to create a sibling of /opt/fitcheck."""
    fork = Fork(tmp_path)
    opt = tmp_path / "opt"
    app_dir = opt / "fitcheck"
    app_dir.mkdir(parents=True)
    (app_dir / ".env").write_text("KEY=kept\n")
    env = _stubs(tmp_path, sudo=SUDO)
    opt.chmod(0o555)
    try:
        result = _run(_bootstrap_code_step(fork.url, app_dir), env)
    finally:
        opt.chmod(0o755)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _git(app_dir, "rev-parse", "HEAD") == fork.main
    assert (app_dir / ".env").read_text() == "KEY=kept\n"
    assert not list((tmp_path / "home").glob("fitcheck-clone.*"))


@needs_git_bash
def test_l1_bootstrap_rerun_moves_a_detached_checkout_to_main(tmp_path):
    """redeploy.sh leaves /opt/fitcheck detached; a rerun of bootstrap must still update it."""
    fork = Fork(tmp_path)
    app_dir = tmp_path / "opt" / "fitcheck"
    subprocess.run(["git", "clone", "-q", fork.url, str(app_dir)], check=True)
    _git(app_dir, "checkout", "-q", "--detach", fork.first)
    newer = fork.advance("two")
    result = _run(_bootstrap_code_step(fork.url, app_dir), _stubs(tmp_path, sudo=SUDO))
    assert result.returncode == 0, result.stdout + result.stderr
    assert _git(app_dir, "rev-parse", "HEAD") == newer
    assert "pull --ff-only" not in (DEPLOY / "bootstrap.sh").read_text()


@pytest.mark.skipif(shutil.which("git") is None, reason="no git")
@pytest.mark.parametrize("script", SHELL_SCRIPTS, ids=lambda p: p.name)
def test_l2_shell_scripts_are_executable_in_the_index(script):
    """The README runs them as deploy/redeploy.sh, so git must carry mode 100755."""
    listed = subprocess.run(["git", "-C", str(DEPLOY.parent), "ls-files", "-s", "--",
                             f"deploy/{script.name}"], capture_output=True, text=True)
    if listed.returncode != 0 or not listed.stdout:
        pytest.skip("not a git checkout")
    assert listed.stdout.split()[0] == "100755", listed.stdout


@needs_git_bash
@pytest.mark.parametrize("which", ["side", "unknown"])
def test_l3_stage_refuses_a_head_that_is_not_on_the_forks_main(tmp_path, which):
    fork = Fork(tmp_path)
    head = fork.side if which == "side" else "0" * 40
    env = _vm(tmp_path)
    result = _run(_remote("stage", tmp_path, _free_port()), env, fork.url, head, "1", "0")
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"!! {head} is not on the fork's main" in result.stdout
    assert _git(tmp_path / "opt" / "fitcheck-next", "rev-parse", "HEAD") == fork.main
    assert "systemd-run" not in Path(env["STUB_LOG"]).read_text()


@needs_git_bash
def test_l3_stage_takes_a_head_on_main(tmp_path):
    fork = Fork(tmp_path)
    fork.advance("two")
    env = _vm(tmp_path)
    result = _run(_remote("stage", tmp_path, _free_port()), env, fork.url, fork.main, "1", "0")
    assert "is not on the fork's main" not in result.stdout
    assert f"checkout: {fork.main[:7]}" in result.stdout
    assert "systemd-run" in Path(env["STUB_LOG"]).read_text()
