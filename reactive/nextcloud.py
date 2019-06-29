
from charms.reactive import when_all, when, when_not, set_flag, when_none, when_any

from charmhelpers.core import templating
from charmhelpers.core.hookenv import open_port, status_set, config, unit_public_ip, log
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
    status_set('blocked', "Need Mysql or Postgres database relation to continue")

    return


def create_config_php(dbtype, extra_trusted_domains):

    ctx = {'_dbtype': dbtype ,
           '_extra_trusted_domains': extra_trusted_domains}

    templating.render(source="config.php.template",
                      target=NEXTCLOUD_CONFIG_PHP,
                      context=ctx,
                      perms=0o400)