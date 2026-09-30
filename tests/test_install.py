import os
import pathlib
import shutil
import subprocess


ROOT = pathlib.Path(__file__).resolve().parents[1]


def _write_executable(path, content):
    path.write_text(content)
    path.chmod(0o755)


def _run_installer(tmp_path, *, rust_version="1.97.1", rust_std_version="1.97.1",
                   inherited_api="29", python_api="24", crypto_import_exit="0"):
    source = tmp_path / "source"
    source.mkdir()
    shutil.copy2(ROOT / "install.sh", source / "install.sh")
    (source / "server.py").write_text("# installer fixture\n")
    (source / "requirements.txt").write_text("# installer fixture\n")

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    pkg_log = tmp_path / "pkg.log"
    python_log = tmp_path / "python.log"

    _write_executable(fake_bin / "pkg", """#!/bin/sh
printf '%s\n' "$*" > "$FAKE_PKG_LOG"
""")
    _write_executable(fake_bin / "dpkg", """#!/bin/sh
[ "$1" = "--print-architecture" ] || exit 2
printf '%s\n' "${FAKE_ARCH:-aarch64}"
""")
    _write_executable(fake_bin / "dpkg-query", """#!/bin/sh
for package do :; done
case "$package" in
    rust) printf '%s' "$FAKE_RUST_VERSION" ;;
    rust-std-*) printf '%s' "$FAKE_RUST_STD_VERSION" ;;
    *) exit 1 ;;
esac
""")
    _write_executable(fake_bin / "python", """#!/bin/sh
if [ "${1:-}" = "-" ]; then
    payload=$(cat)
    printf '%s\n---\n' "$payload" >> "$FAKE_PYTHON_LOG"
    case "$payload" in
        *sysconfig*) printf '%s\n' "$FAKE_PYTHON_API" ;;
        *'import cffi'*) exit "${FAKE_CRYPTO_IMPORT_EXIT:-0}" ;;
        *) exit 2 ;;
    esac
    exit 0
fi
if [ "${1:-}" = "-m" ] && [ "${2:-}" = "pip" ]; then
    printf 'pip %s\n' "$*" >> "$FAKE_PYTHON_LOG"
    exit 0
fi
exit 2
""")

    env = os.environ.copy()
    env.update({
        "ANDROID_API_LEVEL": inherited_api,
        "FAKE_PKG_LOG": str(pkg_log),
        "FAKE_PYTHON_LOG": str(python_log),
        "FAKE_PYTHON_API": python_api,
        "FAKE_CRYPTO_IMPORT_EXIT": crypto_import_exit,
        "FAKE_RUST_VERSION": rust_version,
        "FAKE_RUST_STD_VERSION": rust_std_version,
        "HOME": str(tmp_path / "home"),
        "MCP_DEST": str(tmp_path / "dest"),
        "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
    })
    result = subprocess.run(
        ["sh", "install.sh"], cwd=source, env=env, text=True,
        capture_output=True)
    return result, pkg_log, python_log


def test_installer_prepares_native_dependencies_and_uses_python_api(tmp_path):
    result, pkg_log, python_log = _run_installer(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    pkg_args = pkg_log.read_text().split()
    assert "rust" in pkg_args
    assert "rust-std-aarch64-linux-android" in pkg_args
    assert "python-cryptography" in pkg_args
    assert "-y" in pkg_args
    assert "Ignoring inherited ANDROID_API_LEVEL=29" in result.stdout
    assert "Using ANDROID_API_LEVEL=24 from the active Python platform" in result.stdout

    python_calls = python_log.read_text()
    assert "import cffi" in python_calls
    assert "import cryptography" in python_calls
    assert "sysconfig.get_platform()" in python_calls
    assert 'get_config_var("ANDROID_API_LEVEL")' in python_calls
    assert "from packaging.tags import sys_tags" in python_calls
    assert "tag.platform == wheel_platform" in python_calls


def test_installer_fails_early_on_mismatched_rust_std(tmp_path):
    result, _, python_log = _run_installer(
        tmp_path, rust_version="1.97.1", rust_std_version="1.96.1")

    assert result.returncode != 0
    assert "Rust package mismatch" in result.stderr
    assert "rust=1.97.1" in result.stderr
    assert "rust-std-aarch64-linux-android=1.96.1" in result.stderr
    assert not python_log.exists()


def test_installer_fails_before_dependency_build_when_crypto_imports_fail(tmp_path):
    result, _, python_log = _run_installer(tmp_path, crypto_import_exit="1")

    assert result.returncode != 0
    assert "did not provide importable cryptography and cffi" in result.stderr
    assert "import cffi" in python_log.read_text()
    assert "pip install" not in python_log.read_text()


# --- uninstaller ---------------------------------------------------------
# Every case runs against a sandboxed HOME/MCP_DEST with a fake python and a
# fake pkg on PATH, so the suite can never uninstall a real package or touch
# real shell configs.


def _uninstall_env(tmp_path):
    home = tmp_path / "home"
    dest = tmp_path / "dest"
    (dest / "bin").mkdir(parents=True)
    (dest / ".git").mkdir()
    (dest / "server.py").write_text("# fixture\n")
    (dest / "bin" / "mcpsh").write_text("#!/bin/sh\n")
    (dest / "bin" / "mcpsh-stop").write_text("#!/bin/sh\n")
    home.mkdir()

    # Exactly the block the installer appends, with user lines on both sides.
    bashrc = home / ".bashrc"
    bashrc.write_text(
        "export FOO=1\n"
        "\n# termux-mcp-shell\n"
        f'export PATH="{dest}/bin:$PATH"\n'
        "echo after\n"
    )

    fake_bin = tmp_path / "uninstall-fake-bin"
    fake_bin.mkdir()
    python_log = tmp_path / "python.log"
    pkg_log = tmp_path / "pkg.log"
    _write_executable(fake_bin / "python", f"""#!/bin/sh
printf '%s\\n' "$*" >> "{python_log}"
exit 0
""")
    # The uninstaller must never call pkg: those packages are shared with the
    # rest of Termux. Log any call so the tests can assert on it.
    _write_executable(fake_bin / "pkg", """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_PKG_LOG"
exit 0
""")

    env = os.environ.copy()
    env.update({
        "HOME": str(home),
        "MCP_DEST": str(dest),
        "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
        "FAKE_PKG_LOG": str(pkg_log),
    })
    return env, bashrc, dest, python_log, pkg_log


def _run_uninstaller(env, *args):
    return subprocess.run(
        ["sh", str(ROOT / "uninstall.sh"), *args], env=env, text=True,
        capture_output=True)


def test_uninstaller_removes_install_and_keeps_surrounding_rc_lines(tmp_path):
    env, bashrc, dest, python_log, _ = _uninstall_env(tmp_path)

    result = _run_uninstaller(env, "--yes")

    assert result.returncode == 0, result.stdout + result.stderr
    assert not dest.exists()
    # The marker block goes; the user's own lines on either side stay.
    assert bashrc.read_text() == "export FOO=1\necho after\n"
    assert "pip uninstall -y mcp" in python_log.read_text()


def test_uninstaller_never_removes_shared_termux_packages(tmp_path):
    env, _, _, _, pkg_log = _uninstall_env(tmp_path)

    _run_uninstaller(env, "--yes")

    assert not pkg_log.exists(), "uninstaller must not call pkg"


def test_uninstaller_is_idempotent(tmp_path):
    env, bashrc, dest, _, _ = _uninstall_env(tmp_path)

    assert _run_uninstaller(env, "--yes").returncode == 0
    after_first = bashrc.read_text()

    second = _run_uninstaller(env, "--yes")
    assert second.returncode == 0, second.stdout + second.stderr
    assert bashrc.read_text() == after_first
    assert not dest.exists()


def test_uninstaller_dry_run_changes_nothing(tmp_path):
    env, bashrc, dest, python_log, _ = _uninstall_env(tmp_path)
    before = bashrc.read_text()

    result = _run_uninstaller(env, "--dry-run")

    assert result.returncode == 0, result.stdout + result.stderr
    assert dest.exists()
    assert bashrc.read_text() == before
    # The read-only `import mcp` probe still runs so the plan can name the
    # package, but nothing may actually be uninstalled.
    assert "pip uninstall" not in python_log.read_text()


def test_uninstaller_discovers_custom_dest_from_rc(tmp_path):
    env, bashrc, dest, _, _ = _uninstall_env(tmp_path)
    # No MCP_DEST: the destination has to come from the PATH line in the rc.
    del env["MCP_DEST"]

    result = _run_uninstaller(env, "--yes")

    assert result.returncode == 0, result.stdout + result.stderr
    assert not dest.exists()
    assert bashrc.read_text() == "export FOO=1\necho after\n"


def test_uninstaller_refuses_directory_that_is_not_an_install(tmp_path):
    env, _, dest, _, _ = _uninstall_env(tmp_path)
    shutil.rmtree(dest / ".git")
    (dest / "server.py").unlink()

    result = _run_uninstaller(env, "--yes")

    assert result.returncode != 0
    assert "does not look like a termux-mcp-shell install" in result.stderr
    assert dest.exists()


def test_uninstaller_refuses_dangerous_destinations(tmp_path):
    env, _, dest, _, _ = _uninstall_env(tmp_path)
    home = pathlib.Path(env["HOME"])

    for unsafe in ("/", str(home), str(home) + "/"):
        env["MCP_DEST"] = unsafe
        result = _run_uninstaller(env, "--yes")
        assert result.returncode != 0, unsafe
        assert "refusing to operate on" in result.stderr, unsafe

    assert dest.exists()
