#!/usr/bin/env bash

set -euo pipefail

MANIM_VERSION=0.20.1
MANIM_TOOL_DIR=/opt/pear-visual-tools
MANIM_BIN_DIR=/usr/local/bin
PACKAGES=(
    build-essential
    dvisvgm
    ffmpeg
    libcairo2-dev
    libpango1.0-dev
    pkg-config
    python3-dev
    texlive-fonts-recommended
    texlive-latex-base
    texlive-latex-extra
)

check_runtime() {
    local reported_version
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
    echo "visual_runtime=ready"
    echo "manim_version=$reported_version"
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

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends "${PACKAGES[@]}"
uv tool install \
    --force \
    --python /usr/bin/python3 \
    --tool-dir "$MANIM_TOOL_DIR" \
    --bin-dir "$MANIM_BIN_DIR" \
    "manim==$MANIM_VERSION"
check_runtime
