from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class UpdaterPinTests(unittest.TestCase):
    def test_image_profile_is_limited_to_config_and_not_final_activation(self) -> None:
        updater = Path(__file__).parents[1] / "update.sh"
        source = updater.read_text(encoding="utf-8")
        config_command = "compose --profile images config --format json"
        self.assertEqual(source.count(config_command), 2)
        self.assertIn("compose_pull --profile images pull", source)
        self.assertNotIn("COMPOSE_PROFILES", source)
        activation = [
            line.strip()
            for line in source.splitlines()
            if line.strip().startswith("timeout 180s docker compose")
        ]
        self.assertEqual(len(activation), 1)
        self.assertIn(" up -d ", activation[0])
        self.assertNotIn("--profile", activation[0])

    @unittest.skipUnless(
        os.name != "nt" and shutil.which("bash"),
        "a POSIX Bash runtime is required for updater contract test",
    )
    def test_retag_after_preflight_does_not_change_activated_pair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            bin_dir = root / "bin"
            project.mkdir()
            bin_dir.mkdir()
            (project / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
            log = root / "activation.json"
            original_refs = {
                "web:tag": "sha256:web-original",
                "proxy:tag": "sha256:proxy-original",
                "hub:tag": "sha256:hub-original",
                "lab:tag": "sha256:lab-original",
            }
            (root / "image-state.json").write_text(
                json.dumps(
                    {
                        "refs": original_refs,
                        "retagged": False,
                        "compose_config_profiles": [],
                        "activation_profiled": None,
                    }
                ),
                encoding="utf-8",
            )
            config = {
                "name": "test-project",
                "services": {
                    "web": {"image": "web:tag"},
                    "proxy": {"image": "proxy:tag"},
                    "hub": {
                        "image": "hub:tag",
                        "environment": {
                            "LAB_USER": "ubuntu",
                            "LAB_POOL_DIR": str(root / "pool"),
                            "LAB_IMAGE": "lab:tag",
                        },
                        "volumes": [
                            {"source": str(root / "pool"), "target": "/srv/pool"}
                        ],
                    },
                    "lab": {"image": "lab:tag"},
                },
            }
            state_path = root / "image-state.json"
            config_path = root / "compose-config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            cli = root / "fake_cli.py"
            cli.write_text(
                """
import json, os, pathlib, sys
args = sys.argv[1:]
root = pathlib.Path(os.environ['FAKE_ROOT'])
state_path = root / 'image-state.json'
state = json.loads(state_path.read_text())
config = json.loads((root / 'compose-config.json').read_text())
if args[0] == 'compose':
    tail = args[args.index('--project-directory') + 2:]
    if 'config' in tail:
        if tail[:4] != ['--profile', 'images', 'config', '--format'] or tail[4:] != ['json']:
            raise SystemExit(29)
        state['compose_config_profiles'].append(tail[:2])
        state_path.write_text(json.dumps(state))
        services = config['services']
        env = os.environ
        for service, key in [('web','WEB_IMAGE'),('proxy','PROXY_IMAGE'),('hub','HUB_IMAGE'),('lab','LAB_IMAGE')]:
            if key in env:
                services[service]['image'] = env[key]
        if 'LAB_IMAGE' in env:
            services['hub']['environment']['LAB_IMAGE'] = env['LAB_IMAGE']
        print(json.dumps(config))
        raise SystemExit(0)
    if 'pull' in tail:
        if tail[:2] != ['--profile', 'images']:
            raise SystemExit(29)
        raise SystemExit(0)
    if tail[:2] == ['ps', '-q']:
        print('hub-current')
        raise SystemExit(0)
    if 'up' in tail:
        state['activation_profiled'] = '--profile' in tail
        state_path.write_text(json.dumps(state))
        if state['activation_profiled']:
            raise SystemExit(30)
        payload = {key: os.environ[key] for key in ('WEB_IMAGE','PROXY_IMAGE','HUB_IMAGE','LAB_IMAGE')}
        (root / 'activation.json').write_text(json.dumps(payload))
        raise SystemExit(0)
    raise SystemExit(0)
if args[:2] == ['image', 'inspect']:
    fmt = args[args.index('--format') + 1]
    ref = args[-1]
    if os.environ.get('FAKE_FAIL_INSPECT') == ref:
        raise SystemExit(17)
    image_id = state['refs'].get(ref, ref)
    if fmt == '{{.Id}}':
        print(image_id)
    elif '{{.Id}}|' in fmt:
        label = os.environ.get('FAKE_CANDIDATE_VERSION','6.0.1') if image_id in {'sha256:hub-original','sha256:lab-original'} else ''
        print(image_id + '|' + label)
    else:
        print('{}')
    raise SystemExit(0)
if args and args[0] == 'inspect':
    if 'hub-current' in args:
        print(os.environ.get('FAKE_CURRENT_VERSION', '6.0.1'))
        if not state['retagged']:
            state['refs'] = {key: 'sha256:' + key.split(':')[0] + '-retagged' for key in state['refs']}
            state['retagged'] = True
            state_path.write_text(json.dumps(state))
        raise SystemExit(0)
if args[:2] == ['ps', '-aq']:
    raise SystemExit(0)
raise SystemExit(0)
""",
                encoding="utf-8",
            )
            python = shutil.which("python") or shutil.which("python3")
            if not python:
                self.skipTest("Python executable is not discoverable")
            docker_script = bin_dir / "docker"
            docker_script.write_text(
                f"#!/bin/sh\nexec '{python}' '{cli}' \"$@\"\n", encoding="utf-8"
            )
            python_script = bin_dir / "python3"
            python_script.write_text(
                f"#!/bin/sh\nexec '{python}' \"$@\"\n", encoding="utf-8"
            )
            timeout_script = bin_dir / "timeout"
            timeout_script.write_text('#!/bin/sh\nshift\nexec "$@"\n', encoding="utf-8")
            flock_script = bin_dir / "flock"
            flock_script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            for path in (docker_script, python_script, timeout_script, flock_script):
                path.chmod(0o755)
            updater = Path(__file__).parents[1] / "update.sh"
            env = os.environ.copy()
            for key in ("WEB_IMAGE", "PROXY_IMAGE", "HUB_IMAGE", "LAB_IMAGE"):
                env.pop(key, None)
            env.update(
                {
                    "PATH": str(bin_dir) + os.pathsep + env.get("PATH", ""),
                    "FAKE_ROOT": str(root),
                    "UPDATE_LOCK_FILE": str(root / "update.lock"),
                }
            )
            result = subprocess.run(  # ruff: ignore[S603] - executes only fixture-owned scripts
                [shutil.which("bash") or "bash", str(updater), str(project)],
                capture_output=True,
                text=True,
                timeout=60,
                env=env,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(
                json.loads(state_path.read_text(encoding="utf-8"))["retagged"]
            )
            self.assertEqual(
                json.loads(log.read_text(encoding="utf-8")),
                {
                    "WEB_IMAGE": "sha256:web-original",
                    "PROXY_IMAGE": "sha256:proxy-original",
                    "HUB_IMAGE": "sha256:hub-original",
                    "LAB_IMAGE": "sha256:lab-original",
                },
            )
            successful_state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                successful_state["compose_config_profiles"],
                [["--profile", "images"], ["--profile", "images"]],
            )
            self.assertFalse(successful_state["activation_profiled"])
            activated = log.read_bytes()

            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["refs"] = original_refs.copy()
            state["retagged"] = False
            state["compose_config_profiles"] = []
            state["activation_profiled"] = None
            state_path.write_text(json.dumps(state), encoding="utf-8")
            failure_env = env.copy()
            failure_env["FAKE_FAIL_INSPECT"] = "hub:tag"
            failed = subprocess.run(  # ruff: ignore[S603] - executes only fixture-owned scripts
                [shutil.which("bash") or "bash", str(updater), str(project)],
                capture_output=True,
                text=True,
                timeout=60,
                env=failure_env,
                check=False,
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertEqual(log.read_bytes(), activated)

            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["refs"] = original_refs.copy()
            state["retagged"] = False
            state["compose_config_profiles"] = []
            state["activation_profiled"] = None
            state_path.write_text(json.dumps(state), encoding="utf-8")
            log.unlink()
            old_stack_env = env.copy()
            old_stack_env["FAKE_CURRENT_VERSION"] = "5.0.0"
            refused = subprocess.run(  # ruff: ignore[S603] - executes only fixture-owned scripts
                [shutil.which("bash") or "bash", str(updater), str(project)],
                capture_output=True,
                text=True,
                timeout=60,
                env=old_stack_env,
                check=False,
            )
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn(
                "Hub version change 5.0.0 -> 6.0.1 may require a schema migration",
                refused.stderr,
            )
            self.assertFalse(log.exists(), "version refusal must not activate images")
            refusal_state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                refusal_state["compose_config_profiles"],
                [["--profile", "images"], ["--profile", "images"]],
            )
            self.assertIsNone(refusal_state["activation_profiled"])


if __name__ == "__main__":
    unittest.main()
