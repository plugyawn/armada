# @summary Brotli for static assets, with Ubuntu's nginx modules
#
# A deploy hook writes a .br beside each new text asset, so nginx serves
# them without compressing on every request.
class armada::nginx_brotli {
  include zulip::hooks::base

  $packages = ['libnginx-mod-http-brotli-filter', 'libnginx-mod-http-brotli-static', 'brotli']
  package { $packages: ensure => installed }
  file { '/etc/nginx/conf.d/armada-brotli.conf':
    ensure  => file,
    owner   => 'root',
    group   => 'root',
    mode    => '0644',
    source  => 'puppet:///modules/armada/nginx-brotli.conf',
    require => Package[$packages],
    notify  => Exec['armada-reload-nginx'],
  }
  # A reload, not Service['nginx']'s restart, so no connection is cut.
  exec { 'armada-reload-nginx':
    command     => '/usr/sbin/nginx -t && /bin/systemctl reload nginx',
    provider    => shell,
    refreshonly => true,
  }
  file { '/etc/zulip/hooks/pre-deploy.d/60-armada-brotli.hook':
    ensure => file,
    owner  => 'zulip',
    group  => 'zulip',
    mode   => '0755',
    source => 'puppet:///modules/armada/hooks/60-armada-brotli.hook',
    tag    => ['hooks'],
  }
}
