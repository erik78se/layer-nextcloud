import os
import shutil
import subprocess
import time


from charms.reactive import ( when_all, when, when_not, set_flag, set_state,
                              when_none, when_any, hook, clear_flag )
from charms import reactive, apt
from charmhelpers.core import ( hookenv, host, unitdata )
from charmhelpers.core.hookenv import ( storage_get, storage_list, status_set, config, log, DEBUG, WARNING )
from charmhelpers.core.host import chdir

data_mount_key = "nextcloud.storage.data.mount"

@hook("data-storage-attached")
def attach():
    # This happens either with a non existing nextcloud installation
    # -OR-
    # After a nextcloud installation has been performed
    # and the operator has decided to attach storage post installation.
    # in which case the /var/www/nextcloud directory is present.

    storageids = storage_list("data")

    if not storageids:

        status_set("blocked", "Cannot locate attached storage")

        return

    storageid = storageids[0]

    mount = storage_get("location", storageid)

    if not mount:

        hookenv.status_set("blocked", "Cannot locate attached storage mount directory for data")

        return

    unitdata.kv().set(data_mount_key, mount)

    log("data storage attached at {}".format(mount))

    # In case storage is attached post deploy, we might have accumulated
    # some data so we need to make sure the attached storage meets our requirements on available disk.
    if os.path.exists('/var/www/nextcloud'):

        required_space = shutil.disk_usage('/var/www/nextcloud/data').used

        free_space = shutil.disk_usage(mount).free

        if required_space > free_space:

            hookenv.status_set("blocked", "attached storage to small.")

            return

    apt.queue_install(["rsync"])

    reactive.set_state("nextcloud.storage.data.attached")


@hook("data-storage-detaching")
def detaching():

    if reactive.is_state("nextcloud.storage.data.migrated"):
        # We don't attempt to migrate data back to local storage as there
        # is probably not enough of it. And we are most likely destroying
        # the unit, so it would be a waste of time even if there is enough
        # space.
        hookenv.status_set("blocked", "Storage detached. No way to store files.")

        host.service_stop('apache2')

    else:

        unitdata.kv().unset(data_mount_key)

        reactive.remove_state("nextcloud.storage.data.attached")


@when("nextcloud.storage.data.attached")
@when_not("nextcloud.storage.data.migrated")
@when("apt.installed.rsync")
@when('nextcloud.initdone')
def migrate_data():
    """
    We have got some attached storage and nextcloud initialized. This means that we migrate data
    following the following strategy:

    0. Stop apache2 to avoid getting out of sync AND place nextcloud in maintenance mode.
    1. rsync from the original /var/www/nextcloud/data to the new storage path.
    2. replace the original /var/www/nextcloud/data with a symlink.
    3. Fix permissions.
    4. Start apache2 and get out of maintenance mode.

    Note that the original may already be a symlink, either from
    the block storage broker or manual changes by admins.
    """
    log("Initializing migration of data to {}".format(unitdata.kv().get(data_mount_key)), DEBUG)

    # Attempting this while nextcloud is live would be bad. So, place in maintenance mode
    maintenance_mode(True)

    # clear_flag('apache.start') # layer:apache-php

    host.service_stop('apache2') # don't wait for the layer to catch the flag

    old_data_dir = '/var/www/nextcloud/data'

    new_data_dir = unitdata.kv().get(data_mount_key)

    backup_data_dir = "{}-{}".format(old_data_dir, int(time.time()))

    status_set("maintenance","Migrating data from {} to {}".format(old_data_dir, new_data_dir),)

    try:

        rsync_cmd = ["rsync", "-av", old_data_dir + "/", new_data_dir + "/"]

        log("Running {}".format(" ".join(rsync_cmd)), DEBUG)

        subprocess.check_call(rsync_cmd, universal_newlines=True)

    except subprocess.CalledProcessError:

        status_set(
            "blocked",
            "Failed to sync data from {} to {}"
            "".format(old_data_dir, new_data_dir),
        )

        return

    os.replace(old_data_dir, backup_data_dir)

    status_set("maintenance", "Relocated data-directory to {}".format(backup_data_dir))

    os.symlink(new_data_dir, old_data_dir) # /mnt/ncdata0 <- /var/www/nextcloud/data

    status_set("maintenance", "Created symlink to new data directory")

    host.chownr(new_data_dir, "www-data", "www-data", follow_links=False, chowntopdir=True)

    status_set("maintenance", "Ensured proper permissions on new data directory")

    os.chmod(new_data_dir, 0o700)

    status_set("maintenance", "Migration completed.")

    # Bring back from maintenance mode.
    maintenance_mode(False)

    # set_flag('apache.start') # layer:apache-php

    host.service_start('apache2') # don't wait for the layer to catch the flag

    status_set("active", "Nextcloud is OK.")

    reactive.set_state("nextcloud.storage.data.migrated")


def maintenance_mode(on_off):

    on = "sudo -u www-data /usr/bin/php occ maintenance:mode --on"

    off = "sudo -u www-data /usr/bin/php occ maintenance:mode --off"

    with chdir('/var/www/nextcloud'):
        if on_off:
            subprocess.call(on.split())
        else:
            subprocess.call(off.split())