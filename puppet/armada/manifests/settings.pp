# @summary Armada's settings, kept in a marked block of /etc/zulip/settings.py
#
# The block is rendered from templates/settings.py.erb, and each value can be
# changed for one server in the `[armada]` section of /etc/zulip/zulip.conf.
# The rest of settings.py stays the operator's. A change takes effect at the
# next restart of the server (every upgrade restarts it).
class armada::settings {
  $settings = {
    'worker_agent_runtime'      => zulipconf('armada', 'worker_agent_runtime', 'codex-app-server'),
    'codex_bin'                 => zulipconf('armada', 'codex_bin', '/opt/codex/bin/codex'),
    'worktree_root'             => zulipconf('armada', 'worktree_root', '/srv/armada/worktrees'),
    'worker_sandbox'            => zulipconf('armada', 'worker_sandbox', 'workspace-write'),
    'trusted_users_only'        => zulipconf('armada', 'trusted_users_only', 'False'),
    'max_active_runs_per_realm' => zulipconf('armada', 'max_active_runs_per_realm', '4'),
    'worker_model'              => zulipconf('armada', 'worker_model', 'gpt-6-astra'),
    'worker_model_choices'      => zulipconf('armada', 'worker_model_choices', 'gpt-6-astra, gpt-6-sol, gpt-5.6-sol'),
    'worker_service_tier'       => zulipconf('armada', 'worker_service_tier', 'ultrafast'),
    'worker_reasoning_effort'   => zulipconf('armada', 'worker_reasoning_effort', 'medium'),
  }

  file { '/usr/local/sbin/armada-settings-apply':
    ensure => file,
    owner  => 'root',
    group  => 'root',
    mode   => '0755',
    source => 'puppet:///modules/armada/armada-settings-apply',
  }
  file { '/etc/zulip/armada-settings.block':
    ensure  => file,
    owner   => 'zulip',
    group   => 'zulip',
    mode    => '0644',
    content => template('armada/settings.py.erb'),
  }
  # The check first asks whether the script is installed, so that a run where
  # it is not yet (the upgrade's --noop pre-check of a new server) reads the
  # block as a change to make rather than failing to find the command.
  exec { 'armada-settings-apply':
    command  => '/usr/local/sbin/armada-settings-apply /etc/zulip/armada-settings.block /etc/zulip/settings.py',
    unless   => 'test -x /usr/local/sbin/armada-settings-apply && /usr/local/sbin/armada-settings-apply --check /etc/zulip/armada-settings.block /etc/zulip/settings.py',
    provider => shell,
    require  => [
      File['/usr/local/sbin/armada-settings-apply'],
      File['/etc/zulip/armada-settings.block'],
    ],
  }
}
