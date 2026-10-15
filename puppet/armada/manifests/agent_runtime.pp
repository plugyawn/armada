# @summary What the agent needs beyond Zulip: Codex, its sandbox, its directories
#
# The Codex login is a person's, by the device-code flow, and stays out of
# configuration (docs/armada/deploy.md, 6.3).
class armada::agent_runtime {
  $codex_version = zulipconf('armada', 'codex_version', '0.157.1')
  $worktree_root = zulipconf('armada', 'worktree_root', '/srv/armada/worktrees')

  zulip::safepackage { ['bubblewrap', 'git']: ensure => installed }

  # Ubuntu 24.04 keeps unprivileged user namespaces from Bubblewrap, which
  # runs every command an agent issues (docs/armada/deploy.md, 6.1).
  zulip::sysctl { 'armada-codex-userns':
    key     => 'kernel.apparmor_restrict_unprivileged_userns',
    value   => '0',
    order   => 90,
    comment => 'Armada: Codex sandboxes agent commands with Bubblewrap',
  }
  zulip::sysctl { 'armada-codex-userns-clone':
    key   => 'kernel.unprivileged_userns_clone',
    value => '1',
    order => 90,
  }

  # In its own prefix: Zulip replaces /srv/zulip-node when it upgrades Node.
  exec { 'armada-install-codex':
    command     => "/usr/local/bin/npm install -g --prefix /opt/codex @openai/codex@${codex_version}",
    unless      => "test \"$(/opt/codex/bin/codex --version 2>/dev/null)\" = \"codex-cli ${codex_version}\"",
    provider    => shell,
    # Codex's launcher and npm run the node Zulip installed there.
    path        => ['/usr/local/bin', '/usr/bin', '/bin'],
    environment => ['HOME=/root'],
    timeout     => 600,
  }

  file { ['/srv/armada', $worktree_root, "${worktree_root}/_scratch"]:
    ensure => directory,
    owner  => 'zulip',
    group  => 'zulip',
    mode   => '0750',
  }
}
