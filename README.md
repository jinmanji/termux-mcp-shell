# termux-mcp-shell

Streamable HTTP MCP server that gives an agent shell and file access inside Termux.

## Install

### One-liner

```sh
curl -fsSL https://raw.githubusercontent.com/jinmanji/termux-mcp-shell/master/install.sh | sh
```

The installer installs Python, Git, Termux's native Rust toolchain with its
matching architecture-specific standard library, and Termux's native
`python-cryptography` package. It clones or updates the repository at
`~/termux-mcp-shell`, installs Python dependencies, creates `mcpsh` and
`mcpsh-stop`, and adds the repository's `bin` directory to detected Bash, Zsh,
or Fish configuration. Rust is required because PyPI does not provide Android
wheels for `pydantic-core`; the installer prepares `maturin`, derives the wheel
API from the active Python build platform, verifies that tag against pip's
compatible tags, and disables build isolation so pip uses Termux's Rust instead
of the unsupported rustup Android target. It fails early if the Rust compiler
and standard-library package versions differ. It is safe to rerun.

Use another destination or repository with:

```sh
MCP_DEST=$HOME/mcp MCP_REPO_URL=https://github.com/example/fork \
  sh -c 'curl -fsSL https://raw.githubusercontent.com/jinmanji/termux-mcp-shell/master/install.sh | sh'
```

### Manual

```sh
case "$(dpkg --print-architecture)" in
  aarch64) rust_std=rust-std-aarch64-linux-android ;;
  arm) rust_std=rust-std-armv7-linux-androideabi ;;
  i686) rust_std=rust-std-i686-linux-android ;;
  x86_64) rust_std=rust-std-x86-64-linux-android ;;
  *) echo "unsupported Termux architecture" >&2; exit 1 ;;
esac
pkg install python python-pip git rust "$rust_std" make pkg-config patchelf \
  python-cryptography
export ANDROID_API_LEVEL="$(python -c \
  'import sysconfig; print(sysconfig.get_config_var("ANDROID_API_LEVEL"))')"
python -m pip install --upgrade "setuptools>=70.1" wheel "maturin>=1.10,<2"
python -m pip install --no-build-isolation -r requirements.txt
python server.py
```

Only the `mcp` SDK is a direct Python dependency. The server otherwise uses the
Python standard library.

### `mcp` SDK version

`requirements.txt` pins `mcp>=1.27.0,<2`, and both install paths above honour it.
The server targets the 1.x SDK. 2.x renamed `FastMCP` to `MCPServer`, moved
transport parameters such as `host` and `port` off the server constructor, and
replaced the `mcp.server.fastmcp` module with a shim that always raises, so an
environment resolving to 2.x fails at import before the server binds a port:

```text
ModuleNotFoundError: No module named 'mcp.server.fastmcp'. This is mcp 2.x,
where FastMCP was renamed to MCPServer (from mcp.server.mcpserver import
MCPServer) and other APIs changed; see the migration guide at
https://py.sdk.modelcontextprotocol.io/v2/migration/#fastmcp-renamed-to-mcpserver
or pin 'mcp<2' to keep running v1 code.
```

To repair an environment that already installed 2.x, downgrade and restart:

```sh
python -m pip install 'mcp<2'
python -c 'from mcp.server.fastmcp import FastMCP'   # verify the import
```

That import is the quickest check: it succeeds on 1.x and raises the error above
on 2.x. Confirm the installed version with `python -m pip show mcp`, then use
`mcpsh-stop` and `mcpsh` to restart the server against the corrected environment.
See the
[v1 to v2 migration guide](https://py.sdk.modelcontextprotocol.io/v2/migration/)
before removing the cap; the 2.x port is a server rewrite, not a version bump.

## Run and stop

Foreground:

```sh
python server.py
```

Background, surviving terminal-tab closure:

```sh
mcpsh
mcpsh-stop
```

`mcpsh` writes the PID to `~/.mcpsh.pid`, logs to `~/.mcpsh.log`, and prints the
active endpoint and exposure status. The default MCP endpoint is:

```text
http://127.0.0.1:8088/mcp
```

The server binds loopback-only `127.0.0.1:8088` by default. LAN access requires
explicit `MCP_HOST=0.0.0.0`; set `MCP_AUTH_TOKEN` whenever using a non-loopback
bind address.

## Uninstall

```sh
sh ~/termux-mcp-shell/uninstall.sh
```

The uninstaller stops the running server, removes the `PATH` line the
installer added to `~/.bashrc`, `~/.zshrc`, `~/.profile`, and
`~/.config/fish/config.fish`, deletes the install directory, and removes the
`mcp` Python package. It is idempotent, so rerunning it is harmless, and it
only deletes the marker block it recognises, leaving any shell lines you added
afterwards in place. A custom `MCP_DEST` is discovered from that `PATH` line, so
the script finds a relocated install without extra arguments.

| Option | Effect |
|---|---|
| `--dry-run` | Print the plan and change nothing |
| `--yes` | Skip the confirmation before deleting the install directory |
| `--purge-deps` | Also remove the shared Python dependencies `mcp` pulled in |
| `--dest PATH` | Target a specific install directory |

Two things are deliberately left alone. The Termux packages the installer added
(`python`, `git`, `rust`, the `rust-std` for your architecture, `make`,
`pkg-config`, `patchelf`, and `python-cryptography`) are shared with the rest of
Termux, and the uninstaller prints their names instead of removing them. The
transitive Python dependencies (`pydantic`, `anyio`, `starlette`, `uvicorn`, and
the rest) are shared with other Python tooling on the device, so they need
`--purge-deps` to be removed.

## Configuration

| Environment variable | Default | Purpose |
|---|---:|---|
| `MCP_HOST` | `127.0.0.1` | Bind address; set `0.0.0.0` explicitly for LAN |
| `MCP_PORT` | `8088` | HTTP port |
| `MCP_TRUNC_LIMIT` | `8192` | Initial command-output bytes returned |
| `MCP_MAX_SESSIONS` | `50` | In-memory command-output buffers |
| `MCP_READ_MAX_LINES` | `2000` | Maximum lines per text read |
| `MCP_READ_MAX_BYTES` | `51200` | Approximate byte cap per text read |
| `MCP_AUTH_TOKEN` | unset | Optional shared Bearer/X-API-Key token |

## Tools

### Shell and output

`run_command(command, timeout?, cwd?)` runs `/bin/sh -c` asynchronously. Timeout
or cancellation kills the command's complete process group. Omitted `cwd` defaults
to `$HOME`; relative `cwd` values resolve from `$HOME`. Large stdout/stderr responses
include a `session_id` and continuation offsets for
`read_output(session_id, stream, offset, length)`.

### Reading files

`read_file(path, offset=1, limit=null, line_numbers=true)` returns paginated
UTF-8 text plus the exact file SHA-256. Line-number prefixes are display-only and
must not be copied into `match_text`; set `line_numbers=false` when copying exact
source. `read_files(reads)` batches up to 20 objects shaped as `{path, offset,
limit, line_numbers}` with the same semantics.
`read_file_bytes(path, offset=0, length=4096)` returns Base64 for binary or
minified data.

Filesystem work runs in worker threads, so slow storage does not block unrelated
MCP requests.

Relative file-tool paths resolve from `$HOME`, independent of server launch cwd,
and `~` and absolute paths remain supported. Android does not provide `/tmp`.
File-tool paths under `/tmp` and a `run_command` `cwd` under `/tmp` are mapped to
Termux's writable `$TMPDIR`.
Responses return the actual mapped path so later shell commands can reuse it.
Literal `/tmp/...` text inside `run_command.command` is deliberately not
rewritten; use the returned path or `$TMPDIR/...` there.

### Writing files

`write_file(path, content, expected_sha256=null, create_only=false)` atomically
creates or replaces one UTF-8 file and its parent directories. `expected_sha256`
requires an existing file with that exact current hash; `create_only=true` requires
a missing target. The two guards cannot be combined. `append_file(path, content,
expected_sha256=null)` atomically appends and can reject a stale current file. All
writes return the resulting SHA-256.

### Editing files

`edit_file(path, edits, dry_run=false, expected_sha256=null)` edits one existing
UTF-8 file. `edit_files(files, dry_run=false)` applies the same operation atomically
across multiple existing UTF-8 files; each file item is `{path, edits,
expected_sha256?}`. These tools never create files; use `write_file` to create or
replace one. Inputs are native arrays. Each edit has one canonical shape:

```json
{
  "mode": "replace_match | insert_before | insert_after",
  "match_text": "unique text or anchor",
  "write_text": "literal replacement or insertion"
}
```

Example transaction:

```json
{
  "files": [
    {
      "path": "src/A.kt",
      "expected_sha256": "hash-from-read_file",
      "edits": [
        {
          "mode": "replace_match",
          "match_text": "val enabled = false",
          "write_text": "val enabled = true"
        }
      ]
    },
    {
      "path": "src/B.kt",
      "edits": [
        {
          "mode": "insert_after",
          "match_text": "fun stop() {}",
          "write_text": "\nfun reset() {}"
        }
      ]
    }
  ],
  "dry_run": true
}
```

Compatibility input also accepts `matchText`/`writeText` and mode aliases
`insert_before_match`/`insert_after_match`. Schemas, documentation examples, and
results remain canonical snake_case. `old_text`/`new_text` and camel-case variants
remain unsupported. Insertions are literal and never add a newline automatically.

Every match must resolve uniquely. Matching supports normalized Unicode,
trailing-whitespace tolerance, and indentation-insensitive blocks. Fuzzy matching
only locates the original source span; unmatched text is never normalized or
rewritten. Overlapping edits and multiple operations at the same source position
are rejected before writing. The server validates every file before writing
anything, preserves UTF-8 BOM, line endings, and permission modes, always returns
diffs, and attempts rollback if publishing one file fails. `dry_run` previews
without writes. The recommended guarded flow is `read_file(line_numbers=false)`
then use its SHA-256 for `dry_run`, then apply the same payload and hash. Re-read and
rebuild the payload after any stale-source error.

## Authentication

The unauthenticated default is reachable only over loopback. Anyone who can reach
a non-loopback bind can execute commands and read or modify files. LAN exposure is
explicit and should always use a strong token:

```sh
MCP_HOST=0.0.0.0 MCP_AUTH_TOKEN="<strong-random-token>" mcpsh
```

The server does not generate or persist secrets.

Clients may send either:

```text
Authorization: Bearer <token>
```

or:

```text
X-API-Key: <token>
```

Authentication uses one shared token. There is no TLS, per-client identity, or
rate limiting. For exposure outside loopback or a trusted private network, place
the server behind TLS and stronger access controls.
