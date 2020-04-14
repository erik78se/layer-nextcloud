import os
import shutil
import subprocess
import time

from charms.reactive import ( when_all, when, when_not, set_flag, set_state,
                              when_none, when_any, hook, clear_flag )
from charms import reactive, apt
from charmhelpers.core import ( hookenv, host, unitdata )
from charmhelpers.core.hookenv import ( storage_get, storage_list, status_set, config, log, DEBUG, WARNING )

data_mount_key = "nextcloud.storage.data.mount"

@hook("data-storage-attached")
def attach():
    # When the disk is available at data_mount_key.

    set_state("nextcloud.storage.data.attached")

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

    if os.path.exists('/var/www/nextcloud/data'):

        required_space = shutil.disk_usage('/var/www/nextcloud/data').used

        free_space = shutil.disk_usage(mount).free

        if required_space > free_space:

            hookenv.status_set("blocked", "Not enough free space in data storage: {}.".format(mount))

            return

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
    Attached storage, nextcloud initialized = ready to migrate.
    Copy the data-dir from /var/www/nextcloud/data to the
    new path and replace the original /var/www/nextcloud/data with a symlink.
    Note that the original may already be a symlink, either from
    the block storage broker or manual changes by admins.
    """
    log("Migrating data to {}".format(unitdata.kv().get(data_mount_key)), DEBUG)

    if reactive.is_state("nextcloud.serviceavailable"):
        # Attempting this while nextcloud is live would be bad. So, stop apache2
        # possibly put site in maintenance mode.
        # sudo -u  www-data occ maintenance:mode --on
        host.service_stop('apache2')

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

    # replace the /var/www/nextcloud/data with a symlink and fix perms
    os.replace(old_data_dir, backup_data_dir)

    os.symlink(new_data_dir, old_data_dir)

    host.chownr(new_data_dir, "www-data", "www-data", follow_links=False)

    os.chmod(new_data_dir, 0o700)

    # Bring apache2 back
    host.service_start('apache2')

    reactive.set_state("nextcloud.storage.data.migrated")
