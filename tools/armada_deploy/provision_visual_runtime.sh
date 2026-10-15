#!/usr/bin/env bash

set -euo pipefail

MANIM_VERSION=0.20.1
MANIM_TOOL_DIR=/opt/pear-visual-tools
MANIM_BIN_DIR=/usr/local/bin
PENROSE_VERSION=3.3.0
PENROSE_COMMIT=fdece0c60e591f90f8c2282b1eb2dc8b93322bf2
PENROSE_TAG=v3.3.0
PENROSE_REPOSITORY=https://github.com/penrose/penrose.git
NODE_TOOL_DIR="$MANIM_TOOL_DIR/node"
PENROSE_CATALOG_DIR="$MANIM_TOOL_DIR/penrose-catalog/$PENROSE_COMMIT"
PACKAGES=(
    build-essential
    dvisvgm
    ffmpeg
    git
    libcairo2-dev
    libpango1.0-dev
    pkg-config
    python3-dev
    texlive-fonts-recommended
    texlive-latex-base
    texlive-latex-extra
)

check_runtime() {
    local catalog_commit inaccessible reported_version writable
    for command in ffmpeg ffprobe latex dvisvgm manim; do
        command -v "$command" >/dev/null || {
            echo "missing visual runtime command: $command" >&2
            return 1
        }
    done
    reported_version="$(manim --version 2>/dev/null | sed -n 's/^Manim Community v//p')"
    if [[ "$reported_version" != "$MANIM_VERSION" ]]; then
        echo "unexpected Manim version: ${reported_version:-unknown}" >&2
        return 1
    fi
    [[ -x "$NODE_TOOL_DIR/node_modules/.bin/roger" ]] || {
        echo "missing pinned Penrose renderer" >&2
        return 1
    }
    [[ "$(node -p "require('$NODE_TOOL_DIR/node_modules/@penrose/roger/package.json').version")" == "$PENROSE_VERSION" ]] || {
        echo "unexpected Penrose renderer version" >&2
        return 1
    }
    [[ -f "$PENROSE_CATALOG_DIR/.pear-commit" ]] || {
        echo "missing pinned Penrose catalog" >&2
        return 1
    }
    catalog_commit="$(<"$PENROSE_CATALOG_DIR/.pear-commit")"
    [[ "$catalog_commit" == "$PENROSE_COMMIT" ]] || {
        echo "unexpected Penrose catalog commit" >&2
        return 1
    }
    [[ -f "$PENROSE_CATALOG_DIR/packages/examples/src/registry.json" ]] || {
        echo "missing Penrose example registry" >&2
        return 1
    }
    inaccessible="$(
        find "$NODE_TOOL_DIR" "$PENROSE_CATALOG_DIR" \
            -type d ! -perm -0005 -print -quit
    )"
    if [[ -n "$inaccessible" ]]; then
        echo "visual runtime directory is not isolation-user accessible: $inaccessible" >&2
        return 1
    fi
    inaccessible="$(
        find "$NODE_TOOL_DIR" "$PENROSE_CATALOG_DIR" \
            -type f ! -perm -0004 -print -quit
    )"
    if [[ -n "$inaccessible" ]]; then
        echo "visual runtime file is not isolation-user readable: $inaccessible" >&2
        return 1
    fi
    writable="$(
        find "$NODE_TOOL_DIR" "$PENROSE_CATALOG_DIR" \
            \( -type d -o -type f \) -perm /022 -print -quit
    )"
    if [[ -n "$writable" ]]; then
        echo "visual runtime is writable outside root ownership: $writable" >&2
        return 1
    fi
    echo "visual_runtime=ready"
    echo "manim_version=$reported_version"
    echo "penrose_version=$PENROSE_VERSION"
    echo "penrose_catalog_commit=$catalog_commit"
}

if [[ "${1:-}" == "--check" ]]; then
    check_runtime
    exit
fi
if [[ "${1:-}" != "--yes" || $# -ne 1 ]]; then
    echo "Usage: $0 --check | --yes" >&2
    exit 2
fi
if [[ "$(id -u)" -ne 0 ]]; then
    echo "visual runtime provisioning requires root" >&2
    exit 1
fi
if ! command -v uv >/dev/null; then
    echo "uv is required to provision the pinned Manim tool" >&2
    exit 1
fi
for command in git node npm; do
    command -v "$command" >/dev/null || {
        echo "$command is required to provision the scientific visual runtime" >&2
        exit 1
    }
done

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends "${PACKAGES[@]}"
UV_TOOL_DIR="$MANIM_TOOL_DIR" \
UV_TOOL_BIN_DIR="$MANIM_BIN_DIR" \
uv tool install \
    --force \
    --python /usr/bin/python3 \
    "manim==$MANIM_VERSION"
npm uninstall \
    --prefix "$NODE_TOOL_DIR" \
    --no-audit \
    --no-fund \
    "@resvg/resvg-js-cli"
npm install \
    --prefix "$NODE_TOOL_DIR" \
    --no-audit \
    --no-fund \
    --save-exact \
    "@penrose/roger@$PENROSE_VERSION"

existing_catalog_commit=""
if [[ -f "$PENROSE_CATALOG_DIR/.pear-commit" ]]; then
    existing_catalog_commit="$(<"$PENROSE_CATALOG_DIR/.pear-commit")"
fi
if [[ "$existing_catalog_commit" != "$PENROSE_COMMIT" ]]; then
    catalog_parent="$(dirname "$PENROSE_CATALOG_DIR")"
    install -d -m 0755 "$catalog_parent"
    temporary_catalog="$(mktemp -d "$catalog_parent/.catalog.XXXXXX")"
    trap 'rm -rf "${temporary_catalog:-}"' EXIT
    git clone \
        --depth 1 \
        --branch "$PENROSE_TAG" \
        --single-branch \
        "$PENROSE_REPOSITORY" \
        "$temporary_catalog"
    [[ "$(git -C "$temporary_catalog" rev-parse HEAD)" == "$PENROSE_COMMIT" ]] || {
        echo "Penrose tag resolved to an unexpected commit" >&2
        exit 1
    }
    printf '%s\n' "$PENROSE_COMMIT" >"$temporary_catalog/.pear-commit"
    rm -rf "$PENROSE_CATALOG_DIR"
    mv "$temporary_catalog" "$PENROSE_CATALOG_DIR"
    temporary_catalog=""
    trap - EXIT
fi
chown -R root:root "$NODE_TOOL_DIR" "$(dirname "$PENROSE_CATALOG_DIR")"
find "$NODE_TOOL_DIR" "$(dirname "$PENROSE_CATALOG_DIR")" -type d \
    -exec chmod a+rx,go-w {} +
find "$NODE_TOOL_DIR" "$(dirname "$PENROSE_CATALOG_DIR")" -type f \
    -exec chmod a+r,go-w {} +
check_runtime
