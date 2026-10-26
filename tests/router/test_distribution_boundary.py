"""Installed artifact closure, independently of checkout import paths."""

import json
import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_core_wheel_and_sdist_are_generic(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    def run(args, cwd=tmp_path):
        result = subprocess.run(
            args, cwd=cwd, env=env, capture_output=True, text=True, timeout=60
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    direct = tmp_path / "direct"
    run(["uv", "build", "--offline", "--wheel", "--out-dir", str(direct)], ROOT)
    source = tmp_path / "source"
    run(["uv", "build", "--offline", "--sdist", "--out-dir", str(source)], ROOT)
    with tarfile.open(next(source.glob("*.tar.gz"))) as archive:
        names = archive.getnames()
        assert all(
            "/src/millrace/" not in n
            or "/src/millrace/router" in n
            or n.endswith(("/src/millrace/__init__.py", "/src/millrace/py.typed"))
            for n in names
        )
        archive.extractall(tmp_path / "unpacked", filter="data")
    unpacked = next((tmp_path / "unpacked").iterdir())
    rebuilt = tmp_path / "rebuilt"
    run(["uv", "build", "--offline", "--wheel", "--out-dir", str(rebuilt)], unpacked)
    for index, folder in enumerate((direct, rebuilt)):
        wheel = next(folder.glob("*.whl"))
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            runtime = [
                n for n in names if n.startswith("millrace/") and not n.endswith("/")
            ]
            assert all(
                n in {"millrace/__init__.py", "millrace/py.typed"}
                or n.startswith("millrace/router/")
                for n in runtime
            )
            assert not any("/pi.py" in n or "__pycache__" in n for n in names)
            entry = archive.read(
                next(n for n in names if n.endswith("entry_points.txt"))
            ).decode()
            assert "millrace.router.cli:cli" in entry
            metadata = archive.read(
                next(n for n in names if n.endswith("/METADATA"))
            ).decode()
            assert "Requires-Dist:" not in metadata
        venv = tmp_path / f"venv-{index}"
        run(["uv", "venv", "--offline", "--python", sys.executable, str(venv)])
        python = venv / "bin/python"
        run(
            [
                "uv",
                "pip",
                "install",
                "--offline",
                "--no-deps",
                "--python",
                str(python),
                str(wheel),
            ]
        )
        output = run(
            [
                str(python),
                "-I",
                "-c",
                "import importlib.util,importlib.metadata as m,sys,json; "
                "import millrace.router.execution as e; "
                "assert importlib.util.find_spec('millrace_plus') is None; "
                "assert importlib.util.find_spec('millrace_workflow_package') is None; "
                "assert not any(x.startswith('millrace_plus') for x in sys.modules); "
                "d=m.distribution('millrace-ai'); "
                "assert e.__file__.startswith(sys.prefix); "
                "assert d.locate_file('').as_posix().startswith(sys.prefix); "
                "print(json.dumps({'module':e.__file__,"
                "'direct_url':d.read_text('direct_url.json')}))",
            ]
        )
        proof = json.loads(output)
        assert wheel.name in proof["direct_url"]
        run([str(venv / "bin/millrace"), "router", "--help"])
        # Author the fixture in the test host, then execute only the installed wheel.
        from tests.router.test_execution_boundary import generic_flow

        for version in ("v3", "v4"):
            host = tmp_path / f"host-{index}-{version}"
            host.mkdir()
            file, project, home = generic_flow(host)
            graph = json.loads(file.read_text())
            graph["bindings"]["python"]["executable"] = "runtime"
            if version == "v4":
                graph["format"], graph["limits"] = "millrace-router/v4", {}
            file.write_text(json.dumps(graph))

            def call(*args):
                raw = run(
                    [str(venv / "bin/millrace"), "router", "--state-home",
                     str(home), *args]
                )
                return [json.loads(line) for line in raw.splitlines()]

            created = call(
                "instance",
                "create",
                "--workflow",
                str(file),
                "--project",
                str(project),
                "--host-paths",
                json.dumps({"runtime": str(python)}),
            )[-1]
            instance = created["instance_id"]
            validation = call("validate", "--instance", instance)[-1]
            started = call("start", "--instance", instance)
            final = call(
                "status", "--instance", instance, "--run", started[-1]["run_id"]
            )[-1]
            assert final["status"] == "succeeded" and final["count"] == 1
            launched = final["invocations"][0]["launch_settings"]
            advisory = validation["resolved_nodes"]["main"]["step"]
            assert (
                launched["common"]["producing_revision"]
                == final["invocations"][0]["revision_id"]
                == final["startup_revision_id"]
                == final["revision_id"]
            )
            assert advisory["common"]["producing_revision"] == validation["revision_id"]
            assert launched == {
                **advisory,
                "common": {
                    **advisory["common"],
                    "producing_revision": launched["common"]["producing_revision"],
                },
            }
            assert (
                call("replay", "--envelope",
                     json.dumps(started[0]["request_envelope"]))[-1]
                == started[-1]
            )
            database = home / "instances" / instance / "state.sqlite3"
            import sqlite3

            with sqlite3.connect(database) as db:
                assert dict(db.execute("SELECT key,value FROM meta"))["format"] == (
                    "millrace-router-store/v8"
                )
                assert db.execute("SELECT started_at FROM runs").fetchone()[0] == (
                    final["started_at"]
                )
            if version == "v4":
                for key in ("startup_cap", "startup_elapsed", "startup_deadline", "cap",
                            "max_elapsed", "deadline"):
                    assert key in final and final[key] is None
                invocation = final["invocations"][0]
                assert invocation["run_deadline"] is None
                assert invocation["deadline"] > invocation["created_at"]



def test_retained_reader_refuses_v4_v8_without_state_mutation(single_flow, tmp_path):
    import pytest
    from tests.router.support import create_instance, optional_graph_json, start_run

    workflow, project, home = single_flow
    old_python = os.environ.get("MILLRACE_OL_OLD_READER_PYTHON")
    if not old_python:
        pytest.skip("requires exact retained installed finite reader")
    workflow.write_text(optional_graph_json(json.loads(workflow.read_text()), {}))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    view = start_run(instance, state_home=home)
    before = {p.relative_to(home).as_posix(): p.read_bytes()
              for p in home.rglob("*") if p.is_file()}
    program = """
import json,sys
from pathlib import Path
from millrace.router import control
from millrace.router.graph import capture
from millrace.router.records import RouterError
assert Path(control.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
for expected, call in [
    ('unsupported_graph', lambda: capture(Path(sys.argv[1]), Path(sys.argv[2]))),
    ('unsupported_store_version', lambda: control.inspect_run(
        sys.argv[3],sys.argv[4],state_home=Path(sys.argv[5]))),
]:
    try:
        call()
    except RouterError as exc:
        assert exc.code == expected, exc.code
    else:
        raise AssertionError(expected)
print(json.dumps({'module':control.__file__,'guards':'v4/v8'}))
"""
    child = subprocess.run(
        [old_python, "-I", "-c", program, str(workflow), str(project),
         instance, str(view["id"]), str(home)],
        cwd=tmp_path, capture_output=True, text=True, timeout=15,
        env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert child.returncode == 0, child.stdout + child.stderr
    assert json.loads(child.stdout)["guards"] == "v4/v8"
    assert {p.relative_to(home).as_posix(): p.read_bytes()
            for p in home.rglob("*") if p.is_file()} == before
