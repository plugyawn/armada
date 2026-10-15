# @summary Security updates daily; services restarted only by people
class armada::patching {
  zulip::safepackage { ['unattended-upgrades', 'needrestart']: ensure => installed }
  file { '/etc/apt/apt.conf.d/52armada-unattended-upgrades':
    ensure  => file,
    owner   => 'root',
    group   => 'root',
    mode    => '0644',
    source  => 'puppet:///modules/armada/unattended-upgrades.conf',
    require => Package['unattended-upgrades'],
  }
  file { '/etc/needrestart/conf.d/armada.conf':
    ensure  => file,
    owner   => 'root',
    group   => 'root',
    mode    => '0644',
    source  => 'puppet:///modules/armada/needrestart.conf',
    require => Package['needrestart'],
  }
}
