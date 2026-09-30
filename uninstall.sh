#!/data/data/com.termux/files/usr/bin/sh
# termux-mcp-shell uninstaller. Idempotent: safe to rerun.
# Usage:
#   sh uninstall.sh            # stop, unhook PATH, remove files, drop the mcp package
#   sh uninstall.sh --dry-run  # print the plan, change nothing
#   sh uninstall.sh --yes      # do not prompt before deleting the install directory
#   MCP_DEST=/path sh uninstall.sh
set -eu

DRY_RUN=0
ASSUME_YES=0
PURGE_DEPS=0
DEST="${MCP_DEST:-}"

log() { printf '\033[1;36m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m warning:\033[0m %s\n' "$1" >&2; }

usage() {
    sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run|-n) DRY_RUN=1 ;;
        --yes|-y) ASSUME_YES=1 ;;
        --purge-deps) PURGE_DEPS=1 ;;
        --dest) DEST="${2:-}"; shift ;;
        --dest=*) DEST="${1#--dest=}" ;;
        -h|--help) usage 0 ;;
        *) printf 'unknown option: %s\n' "$1" >&2; usage 2 ;;
    esac
    shift
done

run() {
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '    would run: %s\n' "$*"
    else
        "$@" || warn "failed: $*"
    fi
}

# 1. locate the install directory
# An explicit MCP_DEST wins. Otherwise recover the destination from the PATH
# line the installer wrote, so a custom MCP_DEST install is still found.
if [ -z "$DEST" ]; then
    for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile" \
              "$HOME/.config/fish/config.fish"; do
        [ -f "$rc" ] || continue
        # The two installer formats differ: sh writes
        #   export PATH="<dest>/bin:$PATH"
        # and fish writes
        #   set -gx PATH "<dest>/bin" $PATH
        # so take the first quoted token, then drop the $PATH tail and /bin.
        found="$(awk '/^# termux-mcp-shell/ {
            if (getline > 0 && match($0, /"[^"]*"/)) {
                s = substr($0, RSTART + 1, RLENGTH - 2)
                sub(/:[[:space:]]*\$PATH$/, "", s)
                sub(/[[:space:]]+\$PATH$/, "", s)
                sub(/\/bin$/, "", s)
                print s
            }
            exit
        }' "$rc" 2>/dev/null)"
        if [ -n "$found" ]; then
            DEST="$found"
            break
        fi
    done
fi
[ -n "$DEST" ] || DEST="$HOME/termux-mcp-shell"

# Refuse to delete anything that is not an install directory. A mistyped
# MCP_DEST must never turn into an rm -rf of $HOME or a parent directory.
case "$DEST" in
    ""|"/"|"$HOME"|"$HOME/") echo "refusing to operate on '$DEST'" >&2; exit 1 ;;
esac
if [ ! -d "$DEST" ]; then
    log "No install directory at $DEST; only unhooking and package cleanup remain"
else
    if [ ! -f "$DEST/server.py" ] && [ ! -d "$DEST/.git" ]; then
        warn "$DEST does not look like a termux-mcp-shell install (no server.py, no .git)"
        printf 'Delete it anyway? [y/N] '
        # Grouped so the shell's own "cannot open /dev/tty" diagnostic is
        # swallowed when there is no terminal (piped, CI, background).
        { read -r reply </dev/tty; } 2>/dev/null || reply=""
        case "$reply" in [yY]*) ;; *) echo "aborted"; exit 1 ;; esac
    fi
fi

log "Uninstalling from $DEST"

# 2. stop the running server
PIDFILE="$HOME/.mcpsh.pid"
if [ -f "$PIDFILE" ]; then
    PID="$(cat "$PIDFILE" 2>/dev/null || true)"
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        log "Stopping server (PID $PID)"
        run kill "$PID"
        sleep 0.5
        kill -0 "$PID" 2>/dev/null && run kill -9 "$PID"
    else
        log "Removing stale pidfile"
    fi
    run rm -f "$PIDFILE"
fi
# Fall back to a path-scoped match. A bare `pkill -f server.py` would also kill
# unrelated processes, so only the copy inside DEST is targeted.
if command -v pgrep >/dev/null 2>&1; then
    for pid in $(pgrep -f "$DEST/server.py" 2>/dev/null || true); do
        [ "$pid" = "$$" ] && continue
        log "Stopping server (PID $pid)"
        run kill "$pid"
    done
fi

# 3. remove the PATH line from shell configs
# The installer appends a blank line, the marker, and one PATH line. Drop
# exactly those three and keep everything else, so lines a user added after
# installing survive an uninstall.
strip_rc() {
    rc="$1"
    [ -f "$rc" ] || return 0
    grep -q "^# termux-mcp-shell" "$rc" 2>/dev/null || return 0
    log "Unhooking PATH from $rc"
    if [ "$DRY_RUN" -eq 1 ]; then
        return 0
    fi
    tmp="$rc.mcpsh-uninstall.$$"
    awk '
        BEGIN { pending = ""; skip = 0 }
        {
            if (skip > 0) { skip--; pending = ""; next }
            if ($0 ~ /^# termux-mcp-shell/) { skip = 1; pending = ""; next }
            if (pending != "") print pending
            pending = $0
        }
        END { if (pending != "") print pending }
    ' "$rc" > "$tmp" || { rm -f "$tmp"; warn "could not rewrite $rc"; return 0; }
    # Keep the original mode; a fresh file would otherwise drop the +x bit some
    # users set on their rc.
    chmod --reference="$rc" "$tmp" 2>/dev/null || chmod "$(stat -c %a "$rc" 2>/dev/null || echo 644)" "$tmp" 2>/dev/null || true
    mv "$tmp" "$rc"
}

for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile" \
          "$HOME/.config/fish/config.fish"; do
    strip_rc "$rc"
done

# 4. remove the install directory
if [ -d "$DEST" ]; then
    if [ "$DRY_RUN" -eq 1 ]; then
        log "Would remove $DEST"
    elif [ "$ASSUME_YES" -eq 1 ] || [ ! -t 0 ]; then
        rm -rf "$DEST"
        log "Removed $DEST"
    else
        printf 'Delete %s and everything in it? [y/N] ' "$DEST"
        read -r reply || reply=""
        case "$reply" in
            [yY]*) rm -rf "$DEST"; log "Removed $DEST" ;;
            *) warn "kept $DEST" ;;
        esac
    fi
fi

run rm -f "$HOME/.mcpsh.log"

# 5. Python packages
# mcp is this project's only direct dependency, so it is removed by default.
# Its transitive dependencies (pydantic, anyio, starlette, uvicorn, ...) are
# shared with other tooling on the device and are kept unless --purge-deps is
# given.
if command -v python >/dev/null 2>&1; then
    if python -c 'import mcp' >/dev/null 2>&1; then
        log "Removing Python package mcp"
        run python -m pip uninstall -y mcp
    fi
    if [ "$PURGE_DEPS" -eq 1 ]; then
        log "Removing transitive dependencies (--purge-deps)"
        run python -m pip uninstall -y \
            anyio httpx httpx-sse jsonschema pydantic pydantic-settings pyjwt \
            python-multipart sse-starlette starlette typing-extensions \
            typing-inspection uvicorn
    else
        warn "shared Python dependencies left in place; rerun with --purge-deps to drop them"
    fi
fi

# 6. system packages are deliberately kept
# python, git, rust, and the build toolchain are installed through pkg and are
# shared with the rest of Termux. Removing them can break unrelated tooling, so
# uninstall leaves them alone. List them for manual removal if wanted.
warn "Termux packages left installed (shared, remove manually if unused):"
warn "  python python-pip git rust rust-std-<arch> make pkg-config patchelf python-cryptography"

log "Uninstall complete. Open a new terminal tab, or run: source ~/.bashrc"
