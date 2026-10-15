# @summary An Armada server on one machine: Zulip, the agent and its care
#
# Set `puppet_classes = armada::profile::standalone` in the `[machine]`
# section of /etc/zulip/zulip.conf (in place of zulip::profile::standalone).
# What a fresh server still needs by hand is in docs/armada/operations.md:
# the secrets, the organization, and a person signing Codex in.
class armada::profile::standalone {
  include zulip::profile::standalone
  include armada::settings
  include armada::agent_runtime
  include armada::hooks
  include armada::nginx_brotli
}
