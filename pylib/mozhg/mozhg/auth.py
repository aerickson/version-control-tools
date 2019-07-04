# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

"""Shared Mercurial code related to authentication."""

import os
import platform
import shutil
import tempfile
import urlparse

from mercurial import config, error
from mercurial.i18n import _

from util import import_module


def register_config_items(configitem):
    """Registers config items with Mercurial's registrar.

    The argument is a ``registrar.configitem`` instance.
    """
    # TRACKING hg43
    configitems = import_module('mercurial.configitems')

    configitem(b'bugzilla', b'username',
               default=configitems.dynamicdefault)
    configitem(b'bugzilla', b'apikey',
               default=configitems.dynamicdefault)
    configitem(b'bugzilla', b'password',
               default=configitems.dynamicdefault)
    configitem(b'bugzilla', b'userid',
               default=configitems.dynamicdefault)
    configitem(b'bugzilla', b'cookie',
               default=configitems.dynamicdefault)
    configitem(b'bugzilla', b'firefoxprofile',
               default=configitems.dynamicdefault)
    configitem(b'bugzilla', b'url',
               default=configitems.dynamicdefault)
    configitem(b'mozilla', b'trustedbmoapikeyservices',
               default=configitems.dynamicdefault)


class BugzillaAuth(object):
    """Holds Bugzilla authentication credentials."""

    def __init__(self, userid=None, cookie=None, username=None, password=None,
                 apikey=None):
        if apikey:
            self._type = b'apikey'
        elif userid:
            self._type = b'cookie'
        else:
            self._type = b'explicit'

        self.userid = userid
        self.cookie = cookie
        self.username = username
        self.password = password
        self.apikey = apikey


def getbugzillaauth(ui, require=False, profile=None):
    """Obtain Bugzilla authentication credentials from any possible source.

    This returns a BugzillaAuth instance on success or None on failure.

    If ``require`` is True, we abort if Bugzilla credentials could not be
    found.

    If ``profile`` is defined, we will only consult the profile having this
    name. The default behavior is to examine all available profiles.

    The order of preference for Bugzilla credentials is as follows:

      1) bugzilla.username and bugzilla.apikey from hgrc
      2) bugzilla.userid and bugzilla.cookie from hgrc
      3) bugzilla.username and bugzilla.password from hgrc
      4) login cookies from Firefox profiles
      5) prompt the user

    The ``bugzilla.firefoxprofile`` option is interpreted as a list of Firefox
    profiles from which data should be read. This overrides the default sort
    order.
    """

    username = ui.config(b'bugzilla', b'username', None)
    apikey = ui.config(b'bugzilla', b'apikey', None)
    password = ui.config(b'bugzilla', b'password', None)
    userid = ui.config(b'bugzilla', b'userid', None)
    cookie = ui.config(b'bugzilla', b'cookie', None)
    profileorder = ui.configlist(b'bugzilla', b'firefoxprofile', [])

    if username and apikey:
        return BugzillaAuth(username=username, apikey=apikey)

    if userid and cookie:
        return BugzillaAuth(userid=userid, cookie=cookie)

    if username and password:
        return BugzillaAuth(username=username, password=password)

    ui.debug(b'searching for Bugzilla cookies in Firefox profile\n')
    url = ui.config(b'bugzilla', b'url', b'https://bugzilla.mozilla.org/')
    profilesdir = find_profiles_path()
    profiles = get_profiles(profilesdir)

    # If the list of profiles is explicitly defined, filter out unknown
    # profiles and sort by order.
    if profileorder:
        profiles = [p for p in profiles if p[b'name'] in profileorder]
        profiles = sorted(profiles, key=lambda p: profileorder.index(p[b'name']))

    for p in profiles:
        if profile and p[b'name'] != profile:
            continue

        try:
            userid, cookie = get_bugzilla_login_cookie_from_profile(p[b'path'], url)

            if userid and cookie:
                return BugzillaAuth(userid=userid, cookie=cookie)
        except NoSQLiteError:
            ui.warn(b'SQLite unavailable. Unable to look for Bugzilla cookie.\n')
            break

    if not username:
        username = ui.prompt(_(b'bugzilla username:'), b'')

    if not password:
        password = ui.getpass(_(b'bugzilla password: '), b'')

    if username and password:
        return BugzillaAuth(username=username, password=password)

    if require:
        raise error.Abort(_(b'unable to obtain Bugzilla authentication.'))

    return None

def find_profiles_path():
    """Find the path containing Firefox profiles.

    The location of Firefox profiles is OS dependent. This function handles the
    differences.
    """
    path = None
    if platform.system() == 'Darwin':
        from Carbon import Folder, Folders
        pathref = Folder.FSFindFolder(Folders.kUserDomain,
                                      Folders.kApplicationSupportFolderType,
                                      Folders.kDontCreateFolder)
        basepath = pathref.FSRefMakePath()
        path = os.path.join(basepath, 'Firefox')
    elif platform.system() == 'Windows':
        # From http://msdn.microsoft.com/en-us/library/windows/desktop/bb762494%28v=vs.85%29.aspx
        CSIDL_APPDATA = 26
        path = win_get_folder_path(CSIDL_APPDATA)
        if path:
            path = os.path.join(path, 'Mozilla', 'Firefox')
    else:
        # Assume POSIX
        # Pretty simple in comparison, eh?
        path = os.path.expanduser('~/.mozilla/firefox')

    # This is a backdoor to facilitate testing, since find_profiles_path()
    # doesn't need to be run-time configurable.
    path = os.environ.get('FIREFOX_PROFILES_DIR', path)

    return path

def get_profiles(profilesdir):
    """Obtain information about available Firefox profiles.

    The Firefox profiles from the specified path will be loaded. A list of
    dicts describing each profile will be returned. The list is sorted
    according to profile preference. The default profile is always first.
    """
    profileini = os.path.join(profilesdir, 'profiles.ini')
    if not os.path.exists(profileini):
        return []

    c = config.config()
    c.read(profileini)

    profiles = []
    for s in c.sections():
        if not c.get(s, 'Path') or not c.get(s, 'Name'):
            continue

        name = c.get(s, 'Name')
        path = c.get(s, 'Path')

        if c.get(s, 'IsRelative') == '1':
            path = os.path.join(profilesdir, path)

        newest = -1
        if os.path.exists(path):
            mtimes = []
            for p in os.listdir(path):
                p = os.path.join(path, p)
                if os.path.isfile(p):
                    mtimes.append(os.path.getmtime(p))

            # If there are no files, ignore the profile completely.
            if not mtimes:
                continue

            newest = max(mtimes)

        p = {
            'name': name,
            'path': path,
            'default': c.get(s, 'Default', False) and True,
            'mtime': newest,
        }

        profiles.append(p)

    def compare(a, b):
        """Sort profile by default first, file mtime second."""
        if a['default']:
            return -1

        if a['mtime'] > b['mtime']:
            return -1
        elif a['mtime'] < b['mtime']:
            return 1

        return 0

    return sorted(profiles, cmp=compare)

def win_get_folder_path(folder):
    import ctypes
    SHGetFolderPath = ctypes.windll.shell32.SHGetFolderPathW
    SHGetFolderPath.argtypes = [ctypes.c_void_p,
                                ctypes.c_int,
                                ctypes.c_void_p,
                                ctypes.c_int32,
                                ctypes.c_wchar_p]
    path_buf = ctypes.create_unicode_buffer(1024)
    if SHGetFolderPath(0, folder, 0, 0, path_buf) != 0:
        return None

    return path_buf.value


# Choose the cookie to use based on how much of its path matches the URL.
# Useful if you happen to have cookies for both
# https://landfill.bugzilla.org/bzapi_sandbox/ and
# https://landfill.bugzilla.org/bugzilla-3.6-branch/, for example.
def matching_path_len(cookie_path, url_path):
    return len(cookie_path) if url_path.startswith(cookie_path) else 0


class NoSQLiteError(Exception):
    """Raised when SQLite3 is not available."""

def get_bugzilla_login_cookie_from_profile(profile, url):
    """Given a Firefox profile path, try to find the login cookies for the given bugzilla URL."""
    try:
        import sqlite3
    except:
        raise NoSQLiteError()

    cookies = os.path.join(profile, 'cookies.sqlite')
    if not os.path.exists(cookies):
        return None, None

    host = urlparse.urlparse(url).hostname
    path = urlparse.urlparse(url).path

    # Firefox locks this file, so if we can't open it (browser is running)
    # then copy it somewhere else and try to open it.
    tempdir = None
    try:
        tempdir = tempfile.mkdtemp()
        tempcookies = os.path.join(tempdir, 'cookies.sqlite')
        shutil.copyfile(cookies, tempcookies)
        # Firefox uses sqlite's WAL feature, which bumps the sqlite
        # version number. Older sqlites will refuse to open the db,
        # but the actual format is the same (just the journalling is different).
        # Patch the file to give it an older version number so we can open it.
        with open(tempcookies, 'r+b') as f:
            f.seek(18, 0)
            f.write('\x01\x01')
        conn = sqlite3.connect(tempcookies)
        logins = conn.execute("select value, path from moz_cookies "
                              "where name = 'Bugzilla_login' and (host = ? or host = ?)",
                              (host, "." + host)).fetchall()
        row = sorted(logins, key=lambda row: -matching_path_len(row[1], path))[0]
        login = row[0]
        cookie = conn.execute("select value from moz_cookies "
                              "where name = 'Bugzilla_logincookie' "
                              " and (host = ? or host= ?) "
                              " and path = ?",
                              (host, "." + host, row[1])).fetchone()[0]
        conn.close()
        if isinstance(login, unicode):
            login = login.encode('utf-8')
            cookie = cookie.encode('utf-8')
        return login, cookie

    except IndexError:
        return None, None

    finally:
        if tempdir:
            shutil.rmtree(tempdir)

TRUSTEDAPIKEYSERVICES = {
    'https://reviewboard-hg.mozilla.org',
}

def configureautobmoapikeyauth(ui):
    """Automatically use Bugzilla API Key auth over HTTP for known services.

    Bugzilla credentials are stored in the [bugzilla] section. Mercurial has
    its own [auth] section for declaring credentials for remotes. This function
    carries over the [bugzilla] entries to [auth] entries for trusted services.
    """
    services = ui.configlist(b'mozilla', b'trustedbmoapikeyservices',
                             TRUSTEDAPIKEYSERVICES)
    if not services:
        ui.debug(b'no trusted services to auto define credentials on\n')
        return

    username = ui.config(b'bugzilla', b'username', None)
    apikey = ui.config(b'bugzilla', b'apikey', None)
    if not username:
        ui.debug(b'bugzilla username not defined; cannot define credentials\n')
        return

    for i, service in enumerate(services):
        ui.debug(b'automatically setting Bugzilla API Key auth %s\n' % service)
        key = b'autobmoapikey%d' % i
        ui.setconfig(b'auth', b'%s.prefix' % key, service)
        ui.setconfig(b'auth', b'%s.username' % key, username)
        if apikey:
            ui.setconfig(b'auth', b'%s.password' % key, apikey)
