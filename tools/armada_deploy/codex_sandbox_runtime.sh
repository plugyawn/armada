#!/usr/bin/env bash

set -euo pipefail

MODE="${1:---check}"
CONFIG_PATH="${ARMADA_CODEX_SANDBOX_SYSCTL_PATH:-/etc/sysctl.d/90-armada-codex-userns.conf}"
TEST_USER="${ARMADA_CODEX_SANDBOX_TEST_USER:-zulipdev}"

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

read_sysctl() {
    sysctl -n "$1" 2>/dev/null
}

check_runtime() {
    command -v bwrap >/dev/null || fail "Bubblewrap is not installed"
    command -v unshare >/dev/null || fail "unshare is not installed"
    command -v runuser >/dev/null || fail "runuser is not installed"
    id -u "$TEST_USER" >/dev/null 2>&1 || fail "sandbox test user does not exist: $TEST_USER"

    [[ "$(read_sysctl kernel.unprivileged_userns_clone)" == "1" ]] ||
        fail "kernel.unprivileged_userns_clone must be 1"
    if read_sysctl kernel.apparmor_restrict_unprivileged_userns >/dev/null; then
        [[ "$(read_sysctl kernel.apparmor_restrict_unprivileged_userns)" == "0" ]] ||
            fail "kernel.apparmor_restrict_unprivileged_userns must be 0 for Bubblewrap"
    fi

    [[ -f "$CONFIG_PATH" && ! -L "$CONFIG_PATH" ]] ||
        fail "Codex sandbox sysctl config is missing or unsafe: $CONFIG_PATH"
    [[ "$(stat -c '%U:%G %a %h %F' "$CONFIG_PATH")" == "root:root 644 1 regular file" ]] ||
        fail "Codex sandbox sysctl config has unsafe metadata: $CONFIG_PATH"

    runuser -u "$TEST_USER" -- unshare -Ur true ||
        fail "the Codex runtime user cannot create a user namespace"
    runuser -u "$TEST_USER" -- bwrap \
        --unshare-all \
        --ro-bind / / \
        --dev /dev \
        --proc /proc \
        /usr/bin/true || fail "Bubblewrap cannot create an isolated sandbox"

    bwrap --version
    echo "codex_sandbox_userns=ready"
}

install_runtime() {
    [[ "$(id -u)" == "0" ]] || fail "--install must run as root"
    read_sysctl kernel.unprivileged_userns_clone >/dev/null ||
        fail "this kernel does not expose unprivileged user namespaces"

    temporary="$(mktemp)"
    trap 'rm -f -- "$temporary"' EXIT
    printf '%s\n' 'kernel.unprivileged_userns_clone = 1' >"$temporary"
    if read_sysctl kernel.apparmor_restrict_unprivileged_userns >/dev/null; then
        printf '%s\n' 'kernel.apparmor_restrict_unprivileged_userns = 0' >>"$temporary"
    fi
    install -o root -g root -m 0644 "$temporary" "$CONFIG_PATH"

    sysctl -w kernel.unprivileged_userns_clone=1 >/dev/null
    if read_sysctl kernel.apparmor_restrict_unprivileged_userns >/dev/null; then
        sysctl -w kernel.apparmor_restrict_unprivileged_userns=0 >/dev/null
    fi
    check_runtime
}

case "$MODE" in
    --check)
        check_runtime
        ;;
    --install)
        install_runtime
        ;;
    *)
        echo "Usage: $0 [--check|--install]" >&2
        exit 2
        ;;
esac
