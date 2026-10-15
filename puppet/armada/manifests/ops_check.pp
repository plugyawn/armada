# @summary The ops check: every minute, posts what changed to #ops
#
# The bot and the channel are made once, after the organization exists,
# with armada-ops-bot-setup (docs/armada/operations.md, "Monitoring").
class armada::ops_check {
  include zulip::systemd_daemon_reload

  ['armada-ops-check', 'armada-ops-bot-setup'].each |String $script| {
    file { "/usr/local/sbin/${script}":
      ensure => file,
      owner  => 'root',
      group  => 'root',
      mode   => '0755',
      source => "puppet:///modules/armada/${script}",
    }
  }
  ['service', 'timer'].each |String $kind| {
    file { "/etc/systemd/system/armada-ops-check.${kind}":
      ensure => file,
      owner  => 'root',
      group  => 'root',
      mode   => '0644',
      source => "puppet:///modules/armada/systemd/armada-ops-check.${kind}",
      notify => Exec['reload systemd'],
    }
  }
  service { 'armada-ops-check.timer':
    ensure  => running,
    enable  => true,
    require => [Exec['reload systemd'], File['/usr/local/sbin/armada-ops-check']],
  }
}
