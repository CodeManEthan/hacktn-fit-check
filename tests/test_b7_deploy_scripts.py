"""[B7] The deploy scripts parse, the live checker reads the seed's output, and
nothing under deploy/ carries an OCID or a password.

The fix-pass tests run pieces of the scripts locally: bootstrap's code step and
redeploy's remote stage and cutover scripts, with /opt rewritten to a temporary
directory, a local bare repository for the fork, and stubs for sudo, systemctl and
the other VM commands. Nothing here reaches the VM."""

from __future__ import annotations

import base64
import importlib.util
import os
import py_compile
import re
import shutil
import socket
import stat
import subprocess
import sys
import time
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


# `sudo -u caddy cmd` runs cmd as the test's user.
SUDO = 'if [ "${1:-}" = -u ]; then shift 2; fi; exec "$@"'


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
    body = body.replace("/etc/caddy", str(root / "etc" / "caddy"))
    if port is not None:
        assert "PORT=8001;" in body
        body = body.replace("PORT=8001;", f"PORT={port};")
    return body


VM_STUBS = {
    "sudo": SUDO,
    "systemctl": 'echo "systemctl $*" >> "$STUB_LOG"\n'
                 'if [ "$1" = is-active ]; then echo "${STUB_UNIT_STATE:-inactive}";'
                 ' [ "${STUB_UNIT_STATE:-inactive}" = active ]; exit; fi\n'
                 'if [ "$1" = reload-or-restart ] && [ -n "${STUB_RELOAD_FAIL:-}" ]; then exit 1; fi',
    # caddy validate: fails when STUB_CADDY_INVALID is set.
    "caddy": 'echo "caddy $*" >> "$STUB_LOG"\n[ -z "${STUB_CADDY_INVALID:-}" ]',
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


# Answers like the app on the port: 200 for a GET, one row for /offer/extract.
FAKE_APP = """
import http.server, json, sys
class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, body, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        self._send(b"ok", "text/plain")
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self._send(json.dumps({"rows": [{"name": "x"}], "message": ""}).encode(), "application/json")
    def log_message(self, *args):
        pass
http.server.HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
"""


@pytest.fixture
def vm(tmp_path):
    """A stand-in VM under tmp_path: /opt/fitcheck holds the .env, commands are stubbed,
    and systemd-run starts FAKE_APP on the port it is given."""
    (tmp_path / "opt" / "fitcheck").mkdir(parents=True, exist_ok=True)
    (tmp_path / "opt" / "fitcheck" / ".env").write_text("KEY=value\n")
    app = tmp_path / "fake_app.py"
    app.write_text(FAKE_APP)
    pidfile = tmp_path / "app.pid"
    systemd_run = f'''echo "systemd-run $*" >> "$STUB_LOG"
port="${{@: -1}}"
{sys.executable} {app} "$port" >/dev/null 2>&1 </dev/null &
echo $! > {pidfile}
for _ in $(seq 1 100); do
  (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null && exit 0
  command -p sleep 0.05
done
exit 1'''
    env = _stubs(tmp_path, **{**VM_STUBS, "systemd-run": systemd_run})
    env["STUB_LOG"] = str(tmp_path / "stub.log")
    Path(env["STUB_LOG"]).touch()
    yield env
    if pidfile.exists():
        try:
            os.kill(int(pidfile.read_text()), 15)
        except (ProcessLookupError, ValueError):
            pass


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


def test_bootstrap_points_at_the_fork():
    text = (DEPLOY / "bootstrap.sh").read_text()
    assert 'REPO="https://github.com/CodeManEthan/hacktn-fit-check.git"' in text


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
def test_l3_stage_refuses_a_head_that_is_not_on_the_forks_main(tmp_path, vm, which):
    fork = Fork(tmp_path)
    head = fork.side if which == "side" else "0" * 40
    env = vm
    result = _run(_remote("stage", tmp_path, _free_port()), env, fork.url, head, "1", "0")
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"!! {head} is not on the fork's main" in result.stdout
    assert _git(tmp_path / "opt" / "fitcheck-next", "rev-parse", "HEAD") == fork.main
    assert "systemd-run" not in Path(env["STUB_LOG"]).read_text()


@needs_git_bash
def test_l3_stage_takes_a_head_on_main(tmp_path, vm):
    fork = Fork(tmp_path)
    fork.advance("two")
    env = vm
    result = _run(_remote("stage", tmp_path, _free_port()), env, fork.url, fork.main, "1", "0")
    assert "is not on the fork's main" not in result.stdout
    assert f"checkout: {fork.main[:7]}" in result.stdout
    assert "systemd-run" in Path(env["STUB_LOG"]).read_text()


@needs_git_bash
@pytest.mark.skipif(shutil.which("ss") is None, reason="no ss")
def test_l4_stage_refuses_when_something_already_listens_on_the_port(tmp_path, vm):
    fork = Fork(tmp_path)
    with socket.socket() as foreign:
        foreign.bind(("127.0.0.1", 0))
        foreign.listen()
        port = foreign.getsockname()[1]
        result = _run(_remote("stage", tmp_path, port), vm, fork.url, fork.main, "1", "0")
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"!! something already listens on port {port}" in result.stdout
    assert "systemd-run" not in Path(vm["STUB_LOG"]).read_text()
    assert "stage OK" not in result.stdout


@needs_git_bash
@pytest.mark.parametrize("state", ["active", "failed"])
def test_l4_stage_says_ok_only_when_the_unit_is_active(tmp_path, vm, state):
    fork = Fork(tmp_path)
    vm["STUB_UNIT_STATE"] = state
    result = _run(_remote("stage", tmp_path, _free_port()), vm, fork.url, fork.main, "1", "0")
    assert f"unit fitcheck-stage: {state}" in result.stdout
    assert "-> 200" in result.stdout and "rows 1 |" in result.stdout
    if state == "active":
        assert result.returncode == 0, result.stdout + result.stderr
        assert f"stage OK at {fork.main}" in result.stdout
    else:
        assert result.returncode == 1
        assert "stage OK" not in result.stdout
        assert "(journal)" in result.stdout


def _caddy(root: Path, installed: str | None = None, new: str | None = None) -> list[str]:
    """etc/caddy under root holding `installed` (deploy/Caddyfile by default); returns the
    cutover remote's Caddyfile argument, base64 of `new` (deploy/Caddyfile by default)."""
    current = (DEPLOY / "Caddyfile").read_text()
    caddy_dir = root / "etc" / "caddy"
    caddy_dir.mkdir(parents=True, exist_ok=True)
    (caddy_dir / "Caddyfile").write_text(current if installed is None else installed)
    payload = (current if new is None else new).encode()
    return [base64.b64encode(payload).decode()]


def _checkout(path: Path, fork: Fork, commit: str) -> None:
    """A detached checkout of the fork at path, as stage and cutover leave one."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "fetch", "-q", fork.url, "+refs/heads/*:refs/remotes/origin/*")
    _git(path, "checkout", "-q", "--detach", commit)


@needs_git_bash
def test_l5_cutover_to_the_live_head_keeps_the_rollback(tmp_path, vm):
    fork = Fork(tmp_path)
    opt = tmp_path / "opt"
    _checkout(opt / "fitcheck", fork, fork.main)
    _checkout(opt / "fitcheck-next", fork, fork.main)
    (opt / "fitcheck-prev").mkdir()
    (opt / "fitcheck-prev" / "rollback-marker").write_text("the real previous head\n")
    vm["STUB_UNIT_STATE"] = "active"
    _stubs(tmp_path, curl="exit 0")  # joins the vm's stubs on its PATH
    result = _run(_remote("cutover", tmp_path), vm, fork.main, *_caddy(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "restart only" in result.stdout
    assert not (opt / "fitcheck-next").exists()
    assert (opt / "fitcheck-prev" / "rollback-marker").exists()
    assert _git(opt / "fitcheck", "rev-parse", "HEAD") == fork.main
    calls = Path(vm["STUB_LOG"]).read_text().splitlines()
    assert "systemctl stop fitcheck-stage" in calls
    assert "systemctl restart fitcheck" in calls
    assert "systemctl stop fitcheck" not in calls


@needs_git_bash
def test_l5_cutover_to_a_new_head_still_swaps(tmp_path, vm):
    fork = Fork(tmp_path)
    opt = tmp_path / "opt"
    _checkout(opt / "fitcheck", fork, fork.first)
    _checkout(opt / "fitcheck-next", fork, fork.main)
    (opt / "fitcheck-prev").mkdir()
    (opt / "fitcheck-prev" / "rollback-marker").write_text("older\n")
    vm["STUB_UNIT_STATE"] = "active"
    _stubs(tmp_path, curl="exit 0")  # joins the vm's stubs on its PATH
    result = _run(_remote("cutover", tmp_path), vm, fork.main, *_caddy(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert _git(opt / "fitcheck", "rev-parse", "HEAD") == fork.main
    assert _git(opt / "fitcheck-prev", "rev-parse", "HEAD") == fork.first
    assert (opt / "fitcheck" / ".env").read_text() == "KEY=value\n"
    assert not (opt / "fitcheck-next").exists()


def test_l6_every_curl_assignment_survives_set_e():
    """Under set -e, code=$(curl ...) exits on a refused or timed-out connection.
    [B11]: `msg=$(curl ...) || rc=$?` keeps curl's exit code and survives too."""
    for script in SHELL_SCRIPTS:
        for line in script.read_text().splitlines():
            if re.search(r"=\$\(curl ", line):
                assert re.search(r"\|\| (true|echo [^)]*)\)|\) \|\| \w+=\$\?$", line), \
                    f"{script.name}: {line.strip()}"


@needs_git_bash
def test_l6_stage_prints_the_journal_when_the_app_never_answers(tmp_path, vm):
    fork = Fork(tmp_path)
    _stubs(tmp_path, **{"systemd-run": 'echo "systemd-run $*" >> "$STUB_LOG"'})  # no app
    vm["STUB_UNIT_STATE"] = "failed"
    port = _free_port()
    result = _run(_remote("stage", tmp_path, port), vm, fork.url, fork.main, "1", "0")
    assert result.returncode == 1
    for path in ("/", "/offer", "/impact"):
        assert f"GET 127.0.0.1:{port}{path} -> 000" in result.stdout
    assert "!! stage checks failed; the live site is untouched. Journal:" in result.stdout
    assert "(journal)" in result.stdout


@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("curl") is None,
                    reason="no bash or curl")
def test_l6_check_reports_every_path_when_the_site_is_unreachable(tmp_path):
    port = _free_port()
    env = _stubs(tmp_path, ssh="exit 255")
    env["FITCHECK_HOST"] = f"opc@127.0.0.1:{port}"
    result = subprocess.run(["bash", str(DEPLOY / "redeploy.sh"), "check"], env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 1
    for path in ("/", "/offer", "/impact"):
        assert f"GET http://127.0.0.1:{port}{path} -> 000" in result.stdout


# --- [B11] HTTPS on fitcheck.care (design 9.1) ---------------------------------------

def _caddyfile_sites() -> dict[str, str]:
    """deploy/Caddyfile's site blocks, address -> body, comments dropped."""
    lines = [line for line in (DEPLOY / "Caddyfile").read_text().splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    sites, address, body = {}, None, []
    for line in lines:
        if address is None and line.endswith("{"):
            address, body = line[:-1].strip(), []
        elif address is not None and line.strip() == "}":
            sites[address] = "\n".join(body)
            address = None
        else:
            body.append(line.strip())
    return sites


def test_b11_caddyfile_has_the_name_and_the_ip_door_both_proxying_to_the_app():
    sites = _caddyfile_sites()
    assert set(sites) == {"fitcheck.care", "http://147.224.201.81"}
    for body in sites.values():
        assert "reverse_proxy 127.0.0.1:8000" in body
        assert "redir" not in body
    text = (DEPLOY / "Caddyfile").read_text()
    assert text.count("fitcheck.care") == 1, "the public name is written once"
    assert "{$" not in text and "{env." not in text, "no environment substitution"
    assert "the one place" in text


def test_b11_the_scripts_check_the_name_the_caddyfile_serves():
    name = next(a for a in _caddyfile_sites() if not a.startswith("http://"))
    assert f'PUBLIC_NAME="{name}"' in (DEPLOY / "redeploy.sh").read_text()
    assert _load("check_live").DEFAULT_BASE == f"https://{name}"


@pytest.mark.skipif(shutil.which("caddy") is None, reason="no caddy binary here")
def test_b11_caddyfile_validates(tmp_path):
    env = {**os.environ, "HOME": str(tmp_path), "XDG_DATA_HOME": str(tmp_path),
           "XDG_CONFIG_HOME": str(tmp_path)}
    result = subprocess.run(["caddy", "validate", "--config", str(DEPLOY / "Caddyfile"),
                             "--adapter", "caddyfile"], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-2000:]


def _fitcheck_unit(text: str) -> str:
    start = text.index("/etc/systemd/system/${SERVICE}.service")
    return text[start:text.index("\nUNIT\n", start)]


def test_b11_bootstrap_unit_binds_localhost_with_proxy_headers_and_no_capability():
    text = (DEPLOY / "bootstrap.sh").read_text()
    unit = _fitcheck_unit(text)
    assert 'BIND_HOST="127.0.0.1"' in text and 'PORT="8000"' in text
    assert ("ExecStart=${APP_DIR}/.venv/bin/python -m uvicorn app.main:app --host ${BIND_HOST}"
            " --port ${PORT} --proxy-headers --forwarded-allow-ips 127.0.0.1") in unit
    assert "AmbientCapabilities" not in unit
    assert "0.0.0.0" not in text
    assert "--add-service=http " in text and "--add-service=https " in text


def test_b11_bootstrap_installs_caddy_and_the_caddyfile():
    text = (DEPLOY / "bootstrap.sh").read_text()
    assert re.search(r'CADDY_VERSION="\d+\.\d+\.\d+"', text)
    assert "sha512sum -c" in text, "the release tarball is checked against its checksum file"
    assert 'sudo install -m 755 -o root -g root "$CADDY_TMP/caddy" /usr/local/bin/caddy' in text
    assert 'sudo install -m 644 "$APP_DIR/deploy/Caddyfile" /etc/caddy/Caddyfile' in text
    assert "caddy validate --config /etc/caddy/Caddyfile" in text
    assert "User=caddy" in text and "ExecStart=/usr/local/bin/caddy run" in text
    assert "sudo systemctl enable caddy" in text
    # Caddy takes 80 only after the app has left it.
    assert text.index("sudo systemctl restart ${SERVICE}") < text.index("reload-or-restart caddy")


def test_b11_redeploy_waits_on_8000_and_says_public_checks():
    text = (DEPLOY / "redeploy.sh").read_text()
    assert text.count("curl -fs -o /dev/null http://127.0.0.1:8000/ && break") == 2
    assert "127.0.0.1:80/" not in text
    assert "port-80" not in text and "port 80" not in text
    assert "public checks" in text
    # check_live.py is always given its base, once per base.
    assert re.findall(r'check_live\.py" (\S+)', text) == ['"$base"']


# Cutover's Caddyfile step, run locally with /opt and /etc/caddy under tmp_path.

def _swap_setup(tmp_path: Path, vm: dict) -> tuple[Fork, Path]:
    fork = Fork(tmp_path)
    opt = tmp_path / "opt"
    _checkout(opt / "fitcheck", fork, fork.first)
    _checkout(opt / "fitcheck-next", fork, fork.main)
    vm["STUB_UNIT_STATE"] = "active"
    _stubs(tmp_path, curl="exit 0")
    return fork, opt


@needs_git_bash
def test_b11_cutover_installs_a_changed_caddyfile_before_stopping_the_app(tmp_path, vm):
    fork, opt = _swap_setup(tmp_path, vm)
    arg = _caddy(tmp_path, installed="old config\n")
    result = _run(_remote("cutover", tmp_path), vm, fork.main, *arg)
    assert result.returncode == 0, result.stdout + result.stderr
    caddy_dir = tmp_path / "etc" / "caddy"
    assert (caddy_dir / "Caddyfile").read_text() == (DEPLOY / "Caddyfile").read_text()
    assert (caddy_dir / "Caddyfile.prev").read_text() == "old config\n"
    assert not (caddy_dir / "Caddyfile.new").exists()
    calls = Path(vm["STUB_LOG"]).read_text().splitlines()
    validate = next(i for i, c in enumerate(calls) if c.startswith("caddy validate --config "))
    reload = calls.index("systemctl reload-or-restart caddy")
    assert validate < reload < calls.index("systemctl stop fitcheck")
    assert _git(opt / "fitcheck", "rev-parse", "HEAD") == fork.main


@needs_git_bash
def test_b11_cutover_leaves_an_unchanged_caddyfile_and_drops_a_stale_prev(tmp_path, vm):
    fork, opt = _swap_setup(tmp_path, vm)
    arg = _caddy(tmp_path)
    (tmp_path / "etc" / "caddy" / "Caddyfile.prev").write_text("from an older cutover\n")
    result = _run(_remote("cutover", tmp_path), vm, fork.main, *arg)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Caddyfile unchanged; removed a stale" in result.stdout
    assert not (tmp_path / "etc" / "caddy" / "Caddyfile.prev").exists()
    calls = Path(vm["STUB_LOG"]).read_text().splitlines()
    assert not [c for c in calls if c.startswith("caddy ") or c.endswith(" caddy")]
    assert _git(opt / "fitcheck", "rev-parse", "HEAD") == fork.main


@needs_git_bash
def test_b11_cutover_without_caddy_installed_says_so_and_keeps_the_old_head_live(tmp_path, vm):
    fork, opt = _swap_setup(tmp_path, vm)
    payload = base64.b64encode((DEPLOY / "Caddyfile").read_bytes()).decode()
    assert not (tmp_path / "etc" / "caddy").exists()
    result = _run(_remote("cutover", tmp_path), vm, fork.main, payload)
    assert result.returncode == 1
    assert "!! Caddy is not installed on the VM" in result.stdout
    assert "run deploy/bootstrap.sh first" in result.stdout
    assert not (tmp_path / "etc" / "caddy").exists()
    calls = Path(vm["STUB_LOG"]).read_text().splitlines()
    assert "systemctl stop fitcheck" not in calls
    assert _git(opt / "fitcheck", "rev-parse", "HEAD") == fork.first


@needs_git_bash
def test_b11_cutover_with_an_invalid_caddyfile_keeps_the_old_head_live(tmp_path, vm):
    fork, opt = _swap_setup(tmp_path, vm)
    arg = _caddy(tmp_path, installed="old config\n")
    vm["STUB_CADDY_INVALID"] = "1"
    result = _run(_remote("cutover", tmp_path), vm, fork.main, *arg)
    assert result.returncode == 1
    assert "!! the new Caddyfile does not validate" in result.stdout
    caddy_dir = tmp_path / "etc" / "caddy"
    assert (caddy_dir / "Caddyfile").read_text() == "old config\n"
    assert not (caddy_dir / "Caddyfile.prev").exists()
    assert not (caddy_dir / "Caddyfile.new").exists()
    calls = Path(vm["STUB_LOG"]).read_text().splitlines()
    assert "systemctl stop fitcheck" not in calls
    assert "systemctl reload-or-restart caddy" not in calls
    assert _git(opt / "fitcheck", "rev-parse", "HEAD") == fork.first


@needs_git_bash
def test_b11_cutover_puts_the_old_caddyfile_back_when_caddy_refuses_the_new(tmp_path, vm):
    fork, opt = _swap_setup(tmp_path, vm)
    arg = _caddy(tmp_path, installed="old config\n")
    vm["STUB_RELOAD_FAIL"] = "1"
    result = _run(_remote("cutover", tmp_path), vm, fork.main, *arg)
    assert result.returncode == 1
    assert "!! Caddy would not take the new Caddyfile" in result.stdout
    caddy_dir = tmp_path / "etc" / "caddy"
    assert (caddy_dir / "Caddyfile").read_text() == "old config\n"
    assert not (caddy_dir / "Caddyfile.prev").exists()
    assert "systemctl stop fitcheck" not in Path(vm["STUB_LOG"]).read_text().splitlines()
    assert _git(opt / "fitcheck", "rev-parse", "HEAD") == fork.first


@needs_git_bash
@pytest.mark.parametrize("with_prev", [True, False])
def test_b11_rollback_restores_the_caddyfile_only_when_cutover_replaced_it(tmp_path, vm, with_prev):
    opt = tmp_path / "opt"
    (opt / "fitcheck-prev").mkdir(parents=True)
    _caddy(tmp_path, installed="new config\n")
    caddy_dir = tmp_path / "etc" / "caddy"
    if with_prev:
        (caddy_dir / "Caddyfile.prev").write_text("old config\n")
    _stubs(tmp_path, curl="exit 0", git="echo abc1234 head")
    result = _run(_remote("rollback", tmp_path), vm)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = Path(vm["STUB_LOG"]).read_text().splitlines()
    if with_prev:
        assert (caddy_dir / "Caddyfile").read_text() == "old config\n"
        assert not (caddy_dir / "Caddyfile.prev").exists()
        assert "systemctl reload-or-restart caddy" in calls
    else:
        assert (caddy_dir / "Caddyfile").read_text() == "new config\n"
        assert "systemctl reload-or-restart caddy" not in calls


# The public checks, run through redeploy.sh itself against fake sites on localhost.

SEED_OUT = (
    "  offer 8 added: DeVilbiss Healthcare 525DS x1 from Nashville\n"
    "  offer 9 added: DeVilbiss Healthcare 525KS x1 from Leeds\n"
    "  offer 10 added: this device x2 from Houston\n"
    "  request 1 added: Kayunga, 1 oxygen concentrator\n"
)

# A site that passes check_live.py ("good"), shows a wrong matches page ("bad"), or
# answers 404 everywhere ("404").
FAKE_SITE = """
import http.server, sys
MATCHES = ('<a href="/request/1/offer/9">B</a> Ship with '
           "<h2>Won't work at Kayunga District Hospital</h2>"
           '<a href="/request/1/offer/8">A</a> Buy locally instead? '
           '<a href="/request/1/offer/10">C</a> Needs info').encode()
MODE = sys.argv[2]
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        status, body = 200, b"ok"
        if MODE == "404":
            status = 404
        elif self.path.startswith("/request/1/matches"):
            body = b"no verdicts here" if MODE == "bad" else MATCHES
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass
http.server.HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
"""

# Logs each call; answers the seed and demo_ids commands with SEED_OUT.
SSH_STUB = '''cat > /dev/null
echo "ssh $*" >> "$STUB_LOG"
case "$*" in
  *seed_demo*|*"python -") printf '%s' "$STUB_SEED" ;;
esac'''


@pytest.fixture
def sites(tmp_path):
    """Starts fake sites on demand: sites("good") -> its base URL."""
    script = tmp_path / "fake_site.py"
    script.write_text(FAKE_SITE)
    procs = []

    def start(mode: str) -> str:
        port = _free_port()
        procs.append(subprocess.Popen([sys.executable, str(script), str(port), mode],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        for _ in range(200):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
                return f"http://127.0.0.1:{port}"
            except OSError:
                time.sleep(0.025)
        raise RuntimeError("fake site did not start")

    yield start
    for proc in procs:
        proc.terminate()
        proc.wait(timeout=10)


def _redeploy(tmp_path: Path, ip_site: str, *args: str, public_url: str | None = None,
              getent: str = "exit 2", **stubs: str) -> subprocess.CompletedProcess:
    env = _stubs(tmp_path, ssh=SSH_STUB, getent=getent, **stubs)
    env.pop("FITCHECK_PUBLIC_URL", None)
    env.update(FITCHECK_HOST="opc@" + ip_site.removeprefix("http://"), STUB_SEED=SEED_OUT,
               STUB_LOG=str(tmp_path / "stub.log"))
    if public_url is not None:
        env["FITCHECK_PUBLIC_URL"] = public_url
    return subprocess.run(["bash", str(DEPLOY / "redeploy.sh"), *args], env=env,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)


needs_curl = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("curl") is None, reason="no bash or curl")


# getent ahostsv4 prints one line per socket type for each address.
TWO_OTHERS = ("printf '%s\\n' '147.224.201.81 STREAM fitcheck.care' '147.224.201.81 DGRAM '"
              " '147.224.201.81 RAW ' '10.9.9.9 STREAM ' '10.9.9.9 DGRAM ' '10.9.9.9 RAW '")


@needs_curl
@pytest.mark.parametrize("getent", ["exit 2", "echo '147.224.201.81  fitcheck.care'", TWO_OTHERS],
                         ids=["no-record", "other-address", "two-other-addresses"])
def test_b11_check_skips_the_hostname_when_it_does_not_resolve_to_the_vm(tmp_path, sites, getent):
    ip_site = sites("good")
    result = _redeploy(tmp_path, ip_site, "check", getent=getent)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"GET {ip_site}/request/1/matches -> 200" in result.stdout
    assert "  ok: B ships (Ship with / Clear to ship)" in result.stdout
    skipped = [line for line in result.stdout.splitlines() if "hostname check skipped" in line]
    assert len(skipped) == 1
    assert skipped[0].startswith(
        "-- hostname check skipped: fitcheck.care does not resolve to 127.0.0.1")
    assert skipped[0].endswith(")")  # one line, the addresses joined with spaces
    assert "https://fitcheck.care" not in result.stdout


@needs_curl
def test_b11_check_resolves_when_the_vm_ip_is_not_the_first_address(tmp_path, sites):
    """Several addresses, the VM's second, each repeated per socket type as ahostsv4 prints."""
    ip_site = sites("good")
    real_curl = shutil.which("curl")
    curl = ('for a in "$@"; do case "$a" in https://fitcheck.care*) '
            'echo "curl: (7) refused" >&2; exit 7;; esac; done\n'
            f'exec {real_curl} "$@"')
    getent = ("printf '%s\\n' '10.9.9.9 STREAM fitcheck.care' '10.9.9.9 DGRAM ' "
              "'127.0.0.1 STREAM ' '127.0.0.1 DGRAM '")
    result = _redeploy(tmp_path, ip_site, "check", curl=curl, getent=getent)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "-- hostname check against https://fitcheck.care" in result.stdout
    assert "hostname check skipped" not in result.stdout
    assert "not rolling back" in result.stdout


@needs_curl
def test_b11_check_tries_the_name_when_it_resolves_and_warns_on_a_tls_failure(tmp_path, sites):
    ip_site = sites("good")
    real_curl = shutil.which("curl")
    curl = ('for a in "$@"; do case "$a" in https://fitcheck.care*) '
            'echo "curl $*" >> "$STUB_LOG"; echo "curl: (35) TLS alert" >&2; exit 35;; esac; done\n'
            f'exec {real_curl} "$@"')
    result = _redeploy(tmp_path, ip_site, "check", curl=curl,
                       getent="echo '127.0.0.1  fitcheck.care'")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "-- hostname check against https://fitcheck.care" in result.stdout
    assert ("!! warning: https://fitcheck.care not checked: the TLS handshake failed"
            in result.stdout)
    assert "curl exit 35" in result.stdout and "not rolling back" in result.stdout
    log = (tmp_path / "stub.log").read_text()
    assert "curl -sS -o /dev/null -m 20 https://fitcheck.care/" in log
    assert "GET https://fitcheck.care" not in result.stdout


@needs_curl
def test_b11_check_runs_both_halves_against_the_public_url(tmp_path, sites):
    ip_site, name_site = sites("good"), sites("good")
    result = _redeploy(tmp_path, ip_site, "check", public_url=name_site)
    assert result.returncode == 0, result.stdout + result.stderr
    for base in (ip_site, name_site):
        for path in ("/", "/offer", "/impact", "/request/1/matches",
                     "/packet/9?facility=uganda-district"):
            assert f"GET {base}{path} -> 200" in result.stdout
    assert result.stdout.count("  ok: C needs info") == 2
    assert "hostname check skipped" not in result.stdout


@needs_curl
def test_b11_check_warns_without_failing_when_the_public_url_refuses(tmp_path, sites):
    ip_site = sites("good")
    closed = f"http://127.0.0.1:{_free_port()}"
    result = _redeploy(tmp_path, ip_site, "check", public_url=closed)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"!! warning: {closed} not checked: nothing accepted the connection" in result.stdout
    assert f"GET {closed}" not in result.stdout


@needs_curl
@pytest.mark.parametrize("mode", ["bad", "404"])
def test_b11_check_fails_on_a_bad_page_or_status_at_the_public_url(tmp_path, sites, mode):
    ip_site, name_site = sites("good"), sites(mode)
    result = _redeploy(tmp_path, ip_site, "check", public_url=name_site)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "warning" not in result.stdout
    assert f"GET {name_site}/request/1/matches -> " in result.stdout


@needs_curl
def test_b11_cutover_does_not_roll_back_for_a_certificate_not_yet_issued(tmp_path, sites):
    ip_site = sites("good")
    closed = f"http://127.0.0.1:{_free_port()}"
    result = _redeploy(tmp_path, ip_site, "cutover", "a" * 40, public_url=closed)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "!! warning:" in result.stdout and "cutover OK" in result.stdout
    assert "==> rollback" not in result.stdout


@needs_curl
def test_b11_cutover_rolls_back_for_a_bad_page_at_the_public_url(tmp_path, sites):
    ip_site, name_site = sites("good"), sites("bad")
    result = _redeploy(tmp_path, ip_site, "cutover", "a" * 40, public_url=name_site)
    assert result.returncode == 1
    assert "!! public checks failed; rolling back" in result.stdout
    assert "==> rollback" in result.stdout
    # The Caddyfile went to the VM as the remote script's second argument.
    log = (tmp_path / "stub.log").read_text()
    assert base64.b64encode((DEPLOY / "Caddyfile").read_bytes()).decode() in log
