# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

"""Mercurial extension to run hooks on repositories."""

from __future__ import absolute_import

from mercurial import (
    configitems,
    registrar,
    util,
)
from mozhg.util import (
    identify_repo,
    timers,
)

testedwith = '4.8 4.9'
minimumhgversion = '4.8'
buglink = 'https://bugzilla.mozilla.org/enter_bug.cgi?product=Developer%20Services&component=Mercurial%3A%20hg.mozilla.org'

configtable = {}
configitem = registrar.configitem(configtable)

configitem('allowedroots', '.*',
           generic=True)
configitem('mozilla', 'check.*',
           generic=True)
configitem('mozilla', 'repo_root',
           default=configitems.dynamicdefault)
configitem('mozilla', 'treeherder_repo',
           default=None)


def get_check_classes(hook):
    # TODO come up with a mechanism for automatically discovering checks
    # so we don't have to enumerate them all.
    from mozhghooks.check import (
        advertise_upgrade,
        merge_day,
        prevent_cross_channel_messages,
        prevent_ftl_changes,
        prevent_subrepos,
        prevent_symlinks,
        prevent_sync_ipc_changes,
        prevent_webidl_changes,
        prevent_wptsync_changes,
        single_root,
        try_task_config_file,
    )

    # TODO check to hook mapping should also be automatically discovered.
    if hook == 'pretxnchangegroup':
        return (
            merge_day.MergeDayCheck,
            prevent_cross_channel_messages.XChannelMessageCheck,
            prevent_ftl_changes.FTLCheck,
            prevent_subrepos.PreventSubReposCheck,
            prevent_symlinks.PreventSymlinksCheck,
            prevent_sync_ipc_changes.SyncIPCCheck,
            prevent_webidl_changes.WebIDLCheck,
            prevent_wptsync_changes.WPTSyncCheck,
            single_root.SingleRootCheck,
            try_task_config_file.TryConfigCheck,
        )

    elif hook == 'changegroup':
        return (
            advertise_upgrade.AdvertiseUpgradeCheck,
        )


def get_checks(ui, repo, source, classes):
    """Loads checks from classes.

    Returns a list of check instances that are active for the given repo.
    """

    # Never apply hooks at pull time or when re-applying from strips.
    if source in ('pull', 'strip'):
        return []

    info = identify_repo(repo)

    # Don't apply to non-hosted repos.
    if not info['hosted']:
        ui.write('(not running mozilla hooks on non-hosted repo)\n')
        return []

    checks = []

    for cls in classes:
        check = cls(ui, repo, info)
        name = check.name

        force_enable = False
        force_disable = False
        override = ui.config('mozilla', 'check.%s' % name)
        if override in ('enable', 'true'):
            force_enable = True
        elif override in ('disable', 'false'):
            force_disable = True

        enabled = check.relevant()
        if not isinstance(enabled, bool):
            raise Exception('relevant() must return a bool; got %s' % enabled)

        if enabled and force_disable:
            ui.warn('(%s check disabled per config override)\n' %
                    name)
            continue
        elif not enabled and force_enable:
            ui.warn('(%s check enabled per config override)\n' %
                    name)
            enabled = True

        if enabled:
            checks.append(check)

    return checks


def pretxnchangegroup(ui, repo, node, source=None, **kwargs):
    checks = get_checks(ui, repo, source,
                        get_check_classes('pretxnchangegroup'))

    with timers(ui, 'mozhooks', 'mozhooks.pretxnchangegroup.') as times:
        for check in checks:
            with times.timeit(check.name):
                check.pre(node)

        for rev in repo.changelog.revs(repo[node].rev()):
            ctx = repo[rev]

            for check in checks:
                with times.timeit(check.name):
                    if not check.check(ctx):
                        return 1

        for check in checks:
            with times.timeit(check.name):
                if not check.post_check():
                    return 1

        return 0


def changegroup(ui, repo, source=None, **kwargs):
    checks = get_checks(ui, repo, source, get_check_classes('changegroup'))

    with timers(ui, 'mozhooks', 'mozhooks.changegroup.') as times:
        for check in checks:
            with times.timeit(check.name):
                if not check.check(**kwargs):
                    return 1

        return 0


def reposetup(ui, repo):
    ui.setconfig('hooks', 'pretxnchangegroup.mozhooks', pretxnchangegroup)
    ui.setconfig('hooks', 'changegroup.mozhooks', changegroup)
