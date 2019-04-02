# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

"""Dummy Mercurial extension to facilitate testing of mozhg.auth.getbugzillaauth()."""

import getpass
import os

from mercurial import (
    cmdutil,
    registrar,
    util,
)

OUR_DIR = os.path.dirname(__file__)
with open(os.path.join(OUR_DIR, '..', '..', '..', '..', 'hgext', 'bootstrap.py')) as f:
    exec(f.read())

from mozhg.auth import (
    getbugzillaauth,
    register_config_items,
)

cmdtable = {}

# Mercurial 4.3 introduced registrar.command as a replacement for
# cmdutil.command.
if util.safehasattr(registrar, 'command'):
    command = registrar.command(cmdtable)
else:
    command = cmdutil.command(cmdtable)

# TRACKING hg43 Mercurial 4.3 introduced the config registrar. 4.4
# requires config items to be registered to avoid a devel warning.
if util.safehasattr(registrar, 'configitem'):
    configtable = {}
    configitem = registrar.configitem(configtable)

    register_config_items(configitem)


@command('bzauth', [
    ('', 'require', False, 'Require auth'),
    ('', 'fakegetpass', '', 'Provide a fake getpass.getpass answer'),
    ('', 'ffprofile', '', 'Firefox profile to use'),
    ], 'hg bzauth',
    norepo=True)
def bzauth(ui, require=False, fakegetpass=None, ffprofile=None):
    if fakegetpass:
        def newgetpass(arg):
            return fakegetpass
        getpass.getpass = newgetpass

    a = getbugzillaauth(ui, require=require, profile=ffprofile)
    if a:
        ui.write('userid: %s\n' % a.userid)
        ui.write('cookie: %s\n' % a.cookie)
        ui.write('username: %s\n' % a.username)
        ui.write('password: %s\n' % a.password)
        ui.write('apikey: %s\n' % a.apikey)
    else:
        ui.write('no auth\n')

@command('bzcreatecookie', [],
         'hg bzcreatecookie [profiledir] [url] [userid] [cookie]',
         norepo=True)
def bzcreatecookie(ui, profiledir, url, userid, cookie):
    from mozhg.tests.test_auth import create_login_cookie

    create_login_cookie(profiledir, url, userid, cookie)
