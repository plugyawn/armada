# @summary The deploy hook that links the sprites into each new deployment
#
# The sprites are licensed and never in git: they live in /srv/armada/sprites.
# Tagged `hooks`, so an upgrade installs the hook before it runs it.
class armada::hooks {
  include zulip::hooks::base

  file { '/etc/zulip/hooks/pre-deploy.d/50-armada-sprites.hook':
    ensure => file,
    owner  => 'zulip',
    group  => 'zulip',
    mode   => '0755',
    source => 'puppet:///modules/armada/hooks/50-armada-sprites.hook',
    tag    => ['hooks'],
  }
}
