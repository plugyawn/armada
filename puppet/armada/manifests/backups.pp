# @summary Backups to S3-compatible storage (DigitalOcean Spaces)
#
# The database: Zulip's own wal-g support (zulip::postgresql_backups, which
# the s3_backups_* secrets turn on), pointed at Spaces by wal-g's own
# configuration file and archiving at least once a minute. The files:
# restic, hourly, and Zulip's backup tool nightly, into the same bucket.
# Everything here is idle unless [armada] backup_restic_repository is set
# in /etc/zulip/zulip.conf. docs/armada/operations.md has the restore.
class armada::backups {
  $restic_repository = zulipconf('armada', 'backup_restic_repository', '')
  $s3_endpoint = zulipconf('armada', 'backup_s3_endpoint', '')
  $walg_libsodium_key = zulipsecret('secrets', 'armada_backup_walg_libsodium_key', '')
  $postgresql_version = zulipconf('postgresql', 'version', undef)

  if $restic_repository != '' {
    # Without the key wal-g would archive the database unencrypted.
    if $walg_libsodium_key == '' or $s3_endpoint == '' {
      fail('armada::backups needs [armada] backup_s3_endpoint and the armada_backup_walg_libsodium_key secret')
    }
    include zulip::systemd_daemon_reload

    zulip::safepackage { ['restic', 'crudini']: ensure => installed }

    # wal-g reads $HOME/.walg.json; env-wal-g gives it the bucket and keys
    # from zulip-secrets.conf, and this the endpoint and the encryption key.
    file { '/var/lib/postgresql/.walg.json':
      ensure    => file,
      owner     => 'postgres',
      group     => 'postgres',
      mode      => '0600',
      content   => template('armada/walg.json.erb'),
      show_diff => false,
    }
    file { "/etc/postgresql/${postgresql_version}/main/conf.d/armada.conf":
      ensure => file,
      owner  => 'postgres',
      group  => 'postgres',
      mode   => '0644',
      source => 'puppet:///modules/armada/postgresql-armada.conf',
      notify => Exec['armada-reload-postgresql'],
    }
    exec { 'armada-reload-postgresql':
      command     => '/bin/systemctl reload postgresql',
      refreshonly => true,
    }

    file { '/usr/local/sbin/armada-backup':
      ensure => file,
      owner  => 'root',
      group  => 'root',
      mode   => '0755',
      source => 'puppet:///modules/armada/armada-backup',
    }
    file { '/usr/local/sbin/armada-restore-drill':
      ensure => file,
      owner  => 'root',
      group  => 'root',
      mode   => '0755',
      source => 'puppet:///modules/armada/armada-restore-drill',
    }

    $timers = ['armada-backup-files', 'armada-backup-nightly']
    $timers.each |String $timer| {
      ['service', 'timer'].each |String $kind| {
        file { "/etc/systemd/system/${timer}.${kind}":
          ensure => file,
          owner  => 'root',
          group  => 'root',
          mode   => '0644',
          source => "puppet:///modules/armada/systemd/${timer}.${kind}",
          notify => Exec['reload systemd'],
        }
      }
      service { "${timer}.timer":
        ensure  => running,
        enable  => true,
        require => [Exec['reload systemd'], File['/usr/local/sbin/armada-backup']],
      }
    }
  }
}
