# @summary The plugin host: one supervised program that runs the team's plugins
#
# manage.py armada_plugin_host (zerver/lib/armada_plugin_host.py) keeps each
# installed plugin's checkout and process under [armada] plugin_dir, as the
# zulip user. A Python plugin's environment is made with the uv Zulip itself
# installs (scripts/lib/install-uv), so nothing more is installed here.
class armada::plugin_host {
  include zulip::supervisor

  $plugin_dir = zulipconf('armada', 'plugin_dir', '/srv/armada/plugins')
  file { $plugin_dir:
    ensure => directory,
    owner  => 'zulip',
    group  => 'zulip',
    mode   => '0750',
  }
  file { "${zulip::common::supervisor_conf_dir}/armada-plugin-host.conf":
    ensure  => file,
    require => [Package[supervisor], File[$plugin_dir]],
    owner   => 'root',
    group   => 'root',
    mode    => '0644',
    source  => 'puppet:///modules/armada/supervisor/armada-plugin-host.conf',
    notify  => Service[$zulip::common::supervisor_service],
  }
}
