
from charms.reactive import when_all, when, when_not, set_flag, when_none, when_any, hook, clear_flag
from charmhelpers.core import templating
from charmhelpers.core.hookenv import ( open_port,
                                        status_set,
                                        config,
                                        unit_public_ip,
                                        log,
                                        application_version_set )
from charmhelpers.core.host import chdir, service_restart
from charms.reactive.relations import endpoint_from_flag
from pathlib import Path
import subprocess


NEXTCLOUD_CONFIG_PHP = '/var/www/nextcloud/config/config.php'


@when_any('mysql.available', 'postgres.master.available')
@when_not('nextcloud.initdone')
def init_nextcloud():

    mysql = endpoint_from_flag('mysql.available')

    postgres = endpoint_from_flag('postgres.master.available')

    ctxt = {'dbname': None,
            'dbuser': None,
            'dbpass': None,
            'dbhost': None,
            'dbport': None,
            'dbtype': None,
            'admin_username': config().get('admin-username'),
            'admin_password': config().get('admin-password'),
            'data_dir': Path('/var/www/nextcloud/data'),
            }

    if mysql:
        ctxt['dbname'] = mysql.database()
        ctxt['dbuser'] = mysql.user()
        ctxt['dbpass'] = mysql.password()
        ctxt['dbhost'] = mysql.host()
        ctxt['dbport'] = mysql.port()
        ctxt['dbtype'] = 'mysql'
    elif postgres:
        # ConnectionString(host='1.2.3.4',
        # dbname='mydb',
        # port = 5432,
        # user = 'anon',
        # password = 'secret')
        ctxt['dbname'] = postgres.master.dbname
        ctxt['dbuser'] = postgres.master.user
        ctxt['dbpass'] = postgres.master.password
        ctxt['dbhost'] = postgres.master.host
        ctxt['dbport'] = postgres.master.port
        ctxt['dbtype'] = 'pgsql'
    else:
        log("Failed to determine supported database.")

    status_set('maintenance', "Initializing Nextcloud")

    # Comment below init to test installation manually

    nextcloud_init = ("sudo -u www-data /usr/bin/php occ  maintenance:install "
                      "--database {dbtype} --database-name {dbname} "
                      "--database-host {dbhost} --database-pass {dbpass} "
                      "--database-user {dbuser} --admin-user {admin_username} "
                      "--admin-pass {admin_password} "
                      "--data-dir {data_dir} ").format(**ctxt)

    log(nextcloud_init) #TODO: Remove this

    with chdir('/var/www/nextcloud'):

        subprocess.call(("sudo chown -R www-data:www-data .").split())

        subprocess.call(nextcloud_init.split())

    #TODO: This is wrong and will also replace other values in config.php
    #BUG - perhaps add a config here with trusted_domains.
    Path('/var/www/nextcloud/config/config.php').write_text(
        Path('/var/www/nextcloud/config/config.php').open().read().replace(
            "localhost", config().get('fqdn') or unit_public_ip()))

    set_flag('nextcloud.initdone')

    status_set('active', "Nextcloud init complete")


@when_all('nextcloud.initdone', 'apache.available')
@when_not('nextcloud.serviceavailable')
def server_config():

    for module in ['rewrite', 'headers', 'env', 'dir', 'mime']:

        subprocess.call(['a2enmod', module])

    open_port(port='80')

    set_flag('nextcloud.serviceavailable')

    set_flag('apache.start')

    status_set('active', "Ready")


@when_none('mysql.available', 'postgres.master.available')
def blocked_on_database():
    ''' Due for block when no database is available'''
    status_set('blocked', "Need Mysql or Postgres relation to continue")

    return

@hook('update-status')
def statusupdate():
    '''
    Calls occ status and sets version every now and then (update-status).
    :return:
    '''
    nextcloud_status = "sudo -u www-data /usr/bin/php occ status"

    with chdir('/var/www/nextcloud'):

        try:
            output = subprocess.run( nextcloud_status.split(), stdout=subprocess.PIPE ).stdout.split()

            version = output[5].decode('UTF-8')

            install_status = output[2].decode('UTF-8')

            if install_status == 'true':

                application_version_set(version)
                
                status_set('active', "Nextcloud is OK.")

            else:

                status_set('waiting', "Nextcloud install state not OK.")

                log("Nextcloud install state not OK")
                
        except:
            
            log('ERROR in update-status')
            
            status_set('waiting', "Nextcloud install state not OK.")

@when('apache.available')
@when_any('config.changed.php_max_file_uploads',
          'config.changed.php_upload_max_filesize',
          'config.changed.php_post_max_size',
          'config.changed.php_memory_limit')
def config_php_settings():
    '''
    Detects changes in configuration and renders the phpmodule for
    nextcloud (nextcloud.ini)
    This is instead of manipulating the system wide php.ini
    which might be overwitten or changed from elsewhere.
    '''
    phpmod_context = {
        'max_file_uploads': config('php_max_file_uploads'),
        'upload_max_filesize': config('php_upload_max_filesize'),
        'post_max_size': config('php_post_max_size'),
        'memory_limit': config('php_memory_limit')
    }

    templating.render(source="nextcloud.ini",
                      target='/etc/php/7.2/mods-available/nextcloud.ini',
                      context=phpmod_context)

    subprocess.check_call(['phpenmod', 'nextcloud'])

    flags=['config.changed.php_max_file_uploads',
           'config.changed.php_upload_max_filesize',
           'config.changed.php_memory_limit',
           'config.changed.php_post_max_size']

    for f in flags:
        clear_flag(f)
