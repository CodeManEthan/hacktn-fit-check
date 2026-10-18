"""[B7] The deploy scripts parse, the live checker reads the seed's output, and
nothing under deploy/ carries an OCID or a password."""

from __future__ import annotations

import importlib.util
import py_compile
import re
import shutil
import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"
SHELL_SCRIPTS = sorted(DEPLOY.glob("*.sh"))
PY_SCRIPTS = sorted(DEPLOY.glob("*.py"))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"deploy_{name}", DEPLOY / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
