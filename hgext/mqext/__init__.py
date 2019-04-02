'''tweaks for the mq extension

Note that many if not all of these changes should really be made to the
upstream project. I just haven't gotten around to it.

Commands added:

  :qshow: Display a single patch (similar to 'export' or 'diff -c')
  :qtouched: See what patches modify which files

Commands not related to mq:

  :reviewers: Suggest potential reviewers for a patch
  :bugs: Display the bugs that have touched the same files as a patch
  :components: Suggest a potential component for a patch
  :urls: Guess revision urls for pushed changes

Autocommit:

If you would like to have any change to your patch repository committed to
revision control, mqext adds -Q and -M flags to all mq commands that modify the
patch repository. -Q commits the change to the patch repository, and -M sets
the log message used for that commit (but mqext provides reasonable default
messages, tailored to the specific patch repo-modifying command.)

The -M option argument may include a variety of substitution characters after '%' signs:

  :%a: The action being performed (DELETE,REFRESH,NEW,etc.)
  :%p: The name of the patch
  :%n: The new name of the patch (only for qrename/qmv)
  :%s: Diffstat output (only for qrefresh)
  :%P: A string eg "3 patches - change1, change2, change3" summarizing a set of patches
  :%Q: The qparent and qtip revisions after performing the operation

In addition, for commands that take a set of patches (currently only
qdelete/qrm), square brackets may be used to repeatedly '%p' in a substring
with each patch name in turn. So "foo\n[%a: %p\n]bar", for example, with 3
patches would produce::

  foo
  DELETE: change1
  DELETE: change2
  DELETE: change3
  bar

Pretty horrible, huh?

The following commands are modified:

  - qrefresh
  - qnew
  - qrename
  - qdelete
  - qimport
  - qfinish
  - qfold
  - qcrecord (from the crecord extension)

The expected usage is to add the 'mqcommit=auto' option to the 'mqext' section
of your ~/.hgrc so that all changes are autocommitted if you are using a
versioned patch queue, and to do nothing if not::

  [mqext]
  mqcommit = auto

You could also set it to 'yes' to force it to try to commit all changes, and
error out if you don't have (or have forgotten to create) a patch repository.

Alternatively, if you only want a subset of commands to autocommit, you may add
the -Q option to all relevant commands in your ~/.hgrc::

  [defaults]
  qnew = -Q
  qdelete = -Q
  qimport = -Q

The extension also installs a hook that disallows pushes and pulls to
repositories that have MQ patches applied. To disable this behavior, set the
following config option::

  [mqext]
  allowexchangewithapplied = true
'''

testedwith = '4.3 4.4 4.5 4.6 4.7 4.8'
minimumhgversion = '4.3'

import os
import re
import json
import urllib2

from mercurial.i18n import _
from mercurial.node import short
from mercurial import (
    commands,
    cmdutil,
    configitems,
    error,
    extensions,
    mdiff,
    patch,
    pathutil,
    registrar,
    scmutil,
    url,
    util,
)

from hgext import mq
from collections import Counter, defaultdict, namedtuple

OUR_DIR = os.path.dirname(__file__)
with open(os.path.join(OUR_DIR, '..', 'bootstrap.py')) as f:
    exec(f.read())

from mozautomation.commitparser import BUG_RE
from mozhg.util import import_module

logcmdutil = import_module('mercurial.logcmdutil')

configtable = {}
configitem = registrar.configitem(configtable)

# TRACKING hg44 generic argument added in 4.4.
try:
    configitem('reviewers', '.*',
               generic=True)
except TypeError:
    pass

configitem('bugzilla', 'jsonrpc-url',
           default=None)
configitem('bugzilla', 'url',
           default=None)
configitem('mqext', 'qcommit',
           default=configitems.dynamicdefault)
configitem('mqext', 'allowexchangewithapplied',
           default=False)

buglink = 'https://bugzilla.mozilla.org/enter_bug.cgi?product=Developer%20Services&component=General'

cmdtable = {}

# Mercurial 4.3 introduced registrar.command as a replacement for
# cmdutil.command.
if util.safehasattr(registrar, 'command'):
    command = registrar.command(cmdtable)
else:
    command = cmdutil.command(cmdtable)

# TRACKING hg46
if util.safehasattr(logcmdutil, 'diffordiffstat'):
    diffordiffstat = logcmdutil.diffordiffstat
else:
    diffordiffstat = cmdutil.diffordiffstat

# TRACKING hg47
if util.safehasattr(logcmdutil, 'getrevs'):
    getlogrevs = logcmdutil.getrevs
else:
    getlogrevs = cmdutil.getlogrevs

# TRACKING hg47
if util.safehasattr(cmdutil, 'exportfile'):
    exportfile = cmdutil.exportfile
else:
    exportfile = cmdutil.export

bugzilla_jsonrpc_url = "https://bugzilla.mozilla.org/jsonrpc.cgi"

def resolve_patchfile(ui, repo, patchspec):
    if patchspec is None:
        return None

    try:
        q = repo.mq
    except AttributeError:
        return None

    try:
        p = q.lookup(patchspec, strict=True)
        return q.opener(p, "r")
    except error.Abort, e:
        pass

    try:
        return file(patchspec, "r")
    except Exception, e:
        return None

@command('qshow', [
    ('', 'stat', None, 'output diffstat-style summary of changes')],
    ('hg qshow [patch]'))
def qshow(ui, repo, patchspec=None, **opts):
    '''display a patch

    If no patch is given, the top of the applied stack is shown.'''

    patchf = resolve_patchfile(ui, repo, patchspec)

    if patchf is None:
        # commands.diff has a bad error message
        if patchspec is None:
            patchspec = '.'

        # the built-in export command does not label the diff for color
        # output, and the patch header generation is not reusable
        # independently
        def empty_diff(*args, **kwargs):
            return []
        temp = patch.diff
        try:
            patch.diff = empty_diff
            exportfile(repo, repo.revs(patchspec), fp=ui)
        finally:
            patch.diff = temp

        return commands.diff(ui, repo, change=patchspec, date=None, **opts)

    if opts['stat']:
        del opts['stat']
        lines = patch.diffstatui(patchf, **opts)
    else:
        def singlefile(*a, **b):
            return patchf
        lines = patch.difflabel(singlefile, **opts)

    for chunk, label in lines:
        ui.write(chunk, label=label)

    patchf.close()

def fullpaths(ui, repo, paths):
    cwd = os.getcwd()
    return [pathutil.canonpath(repo.root, cwd, path) for path in paths]

def get_logrevs_for_files(repo, files, opts):
    limit = opts['limit'] or 1000000
    revs = getlogrevs(repo, files, {'follow': True, 'limit': limit})[0]
    for rev in revs:
        yield rev

def choose_changes(ui, repo, patchfile, opts):
    if opts.get('file'):
        changedFiles = fullpaths(ui, repo, opts['file'])
        return (changedFiles, 'file', opts['file'])

    if opts.get('dir'):
        changedFiles = opts['dir']  # For --debug printout only
        return (changedFiles, 'dir', opts['dir'])

    if opts.get('rev'):
        revs = scmutil.revrange(repo, opts['rev'])
        if not revs:
            raise error.Abort("no changes found")
        filesInRevs = set()
        for rev in revs:
            for f in repo[rev].files():
                filesInRevs.add(f)
        changedFiles = sorted(filesInRevs)
        return (changedFiles, 'rev', opts['rev'])

    diff = None
    changedFiles = None
    if patchfile is not None:
        source = None
        if hasattr(patchfile, 'getvalue'):
            diff = patchfile.getvalue()
            source = ('patchdata', None)
        else:
            try:
                diff = url.open(ui, patchfile).read()
                source = ('patch', patchfile)
            except IOError:
                if hasattr(repo, 'mq'):
                    q = repo.mq
                    if q:
                        diff = url.open(ui, q.lookup(patchfile)).read()
                        source = ('mqpatch', patchfile)
    else:
        # try using:
        #  1. current diff (if nonempty)
        #  2. top applied patch in mq patch queue (if mq enabled)
        #  3. parent of working directory
        ui.pushbuffer()
        commands.diff(ui, repo, git=True)
        diff = ui.popbuffer()
        changedFiles = fileRe.findall(diff)
        if len(changedFiles) > 0:
            source = ('current diff', None)
        else:
            changedFiles = None
            diff = None

        if hasattr(repo, 'mq') and repo.mq:
            ui.pushbuffer()
            try:
                commands.diff(ui, repo, change="qtip", git=True)
            except error.RepoLookupError:
                pass
            diff = ui.popbuffer()
            if diff == '':
                diff = None
            else:
                source = ('qtip', None)

        if diff is None:
            changedFiles = sorted(repo['.'].files())
            source = ('rev', '.')

    if changedFiles is None:
        changedFiles = fileRe.findall(diff)

    return (changedFiles, source[0], source[1])

def patch_changes(ui, repo, patchfile=None, **opts):
    '''Given a patch, look at what files it changes, and map a function over
    the changesets that touch overlapping files.

    Scan through the last LIMIT commits to find the relevant changesets

    The patch may be given as a file or a URL. If no patch is specified,
    the changes in the working directory will be used. If there are no
    changes, the topmost applied patch in your mq repository will be used.

    Alternatively, the -f option may be used to pass in one or more files
    that will be used directly.
    '''
    (changedFiles, source, source_info) = choose_changes(ui, repo, patchfile, opts)
    if ui.verbose:
        ui.write("Patch source: %s" % source)
        if source_info is not None:
            ui.write(" %r" % (source_info,))
        ui.write("\n")

    if len(changedFiles) == 0:
        ui.write("Warning: no modified files found in patch. Did you mean to use the -f option?\n")

    if ui.verbose:
        ui.write("Using files:\n")
        if len(changedFiles) == 0:
            ui.write("  (none)\n")
        else:
            for changedFile in changedFiles:
                ui.write("  %s\n" % changedFile)

    # Expand files out to their current full paths
    if opts.get('dir'):
        exactFiles = ['glob:' + opts['dir'] + '/**']
    else:
        paths = [p + '/**' if os.path.isdir(p) else p for p in changedFiles]
        matchfn = scmutil.match(repo['.'], paths, default='relglob')
        exactFiles = ['path:' + path for path in repo['.'].walk(matchfn)]
        if len(exactFiles) == 0:
            return

    for rev in get_logrevs_for_files(repo, exactFiles, opts):
        yield repo[rev]

fileRe = re.compile(r"^\+\+\+ (?:b/)?([^\s]*)", re.MULTILINE)
suckerRe = re.compile(r"[^s-]r=(\w+)")

class DropoffCounter(object):
    '''Maintain a mapping from values to counts and weights, where the weight
drops off exponentially as "time" passes. This is useful when more recent
contributions should be weighted higher than older ones.'''

    Item = namedtuple('Item', ['name', 'count', 'weight'])

    def __init__(self, factor):
        self.factor = factor
        self.counts = Counter()
        self.weights = defaultdict(float)
        self.age = 0

    def add(self, value):
        self.counts[value] += 1
        self.weights[value] += pow(self.factor, self.age)

    def advance(self):
        self.age += 1

    def most_weighted(self, n):
        top = sorted(self.weights, key=lambda k: self.weights[k], reverse=True)
        if len(top) > n:
            top = top[:n]
        return [self[key] for key in top]

    def countValues(self):
        '''Return number of distinct values stored.'''
        return len(self.counts)

    def weight(self, value):
        return self.weights[value]

    def __getitem__(self, key):
        if key in self.counts:
            return DropoffCounter.Item(key, self.counts[key], self.weights[key])

@command('reviewers', [
    ('f', 'file', [], 'see reviewers for FILE', 'FILE'),
    ('r', 'rev', [], 'see reviewers for revisions', 'REVS'),
    ('l', 'limit', 200, 'how many revisions back to scan', 'LIMIT'),
    ('', 'brief', False, 'shorter output')],
    _('hg reviewers [-f FILE1 -f FILE2...] [-r REVS] [-l LIMIT] [PATCH]'))
def reviewers(ui, repo, patchfile=None, **opts):
    '''Suggest a reviewer for a patch

    Scan through the last LIMIT commits to find candidate reviewers for a
    patch (or set of files).

    The patch may be given as a file or a URL. If no patch is specified,
    the changes in the working directory will be used. If there are no
    changes, the topmost applied patch in your mq repository will be used.

    Alternatively, the -f option may be used to pass in one or more files
    that will be used to infer the reviewers instead.

    The [reviewers] section of your .hgrc may be used to specify reviewer
    aliases in case reviewers are specified multiple ways.

    Written by Blake Winton http://weblog.latte.ca/blake/
    '''

    def canon(reviewer):
        reviewer = reviewer.lower()
        return ui.config('reviewers', reviewer, reviewer)

    suckers = DropoffCounter(0.95)
    totalSuckers = 0
    enoughSuckers = 100
    for change in patch_changes(ui, repo, patchfile, **opts):
        for raw in suckerRe.findall(change.description()):
            suckers.add(canon(raw))
        if suckers.countValues() >= enoughSuckers:
            break
        suckers.advance()

    if suckers.age == 0:
        ui.write("no matching files found\n")
        return

    if opts.get('brief'):
        if len(suckers) == 0:
            ui.write("no reviewers found in range\n")
        else:
            r = [ "%s x %d" % (s.name, s.count) for s in suckers.most_weighted(3) ]
            ui.write(", ".join(r) + "\n")
        return

    ui.write("Potential reviewers:\n")
    if (suckers.countValues() == 0):
        ui.write("  none found in range (try higher --limit?)\n")
    else:
        top_weight = 0
        for s in suckers.most_weighted(5):
            top_weight = top_weight or s.weight
            ui.write("  %s: %d (score = %d)\n" % (s.name, s.count, 10 * s.weight / top_weight))
    ui.write("\n")

def fetch_bugs(url, ui, bugs):
    data = json.dumps({
            "method": "Bug.get",
            "id": 1,
            "permissive": True,
            "include_fields": ["id", "url", "summary", "component", "product" ],
            "params": [{ "ids": list(bugs) }]
    })

    req = urllib2.Request(url,
                          data,
                          { "Accept": "application/json",
                           "Content-Type": "application/json"})

    conn = urllib2.urlopen(req)
    ui.debug("fetched %s for bugs %s\n" % (conn.geturl(), ",".join(bugs)))
    try:
        buginfo = json.load(conn)
    except Exception, e:
        pass

    if buginfo.get('result', None) is None:
        # Error handling: bugzilla will report a single error if any of the
        # retrieved bugs was problematic, so drop that bug and retry with two
        # remaining halves (it'll still do one fetch per failure in the bug
        # range, but presumably fetching N/2 bugs is faster than fetching N so
        # the total time will be less)
        if 'error' in buginfo:
            badbug = None
            m = re.search(r'Bug #(\d+) does not exist', buginfo['error']['message'])
            if m:
                if ui.verbose:
                    ui.write("  dropping nonexistent bug %s\n" % m.group(1))
                badbug = m.group(1)
            m = re.search(r'You are not authorized to access bug (?:#?)(\d+)', buginfo['error']['message'])
            if m:
                if ui.verbose:
                    ui.write("  dropping inaccessible bug %s\n" % m.group(1))
                badbug = m.group(1)
            bugs.remove(badbug)
            nparts = 2
            if len(bugs) < nparts:
                parts = [ bugs ]
            else:
                bs = list(bugs)
                parts = [ bs[i:len(bugs):nparts] for i in range(0,nparts) ]
            return reduce(lambda bs,p: bs + fetch_bugs(url, ui, p), parts, [])

        raise error.Abort("Failed to retrieve bugs, last buginfo=%r" % (
            buginfo,))

    return buginfo['result']['bugs']

def guess_components(ui, repo, patchfile=None, **opts):
    bugs = set()
    minBugs = 20
    for change in patch_changes(ui, repo, patchfile, **opts):
        m = BUG_RE.search(change.description())
        if m:
            bugs.add(m.group(2))
        if len(bugs) >= minBugs:
            break
    if len(bugs) == 0:
        ui.write("No bugs found\n")
        return

    components = Counter()
    url = ui.config('bugzilla', 'jsonrpc-url')
    if url is None:
        url = ui.config('bugzilla', 'url')
        if url is None:
            url = bugzilla_jsonrpc_url
        else:
            url = "%s/jsonrpc.cgi" % url

    for b in fetch_bugs(url, ui, bugs):
        comp = "%s :: %s" % (b['product'], b['component'])
        ui.debug("bug %s: %s\n" % (b['id'], comp))
        components.update([comp])

    return components

@command('components', [
    ('f', 'file', [], 'see components for FILE', 'FILE'),
    ('r', 'rev', [], 'see reviewers for revisions', 'REVS'),
    ('l', 'limit', 25, 'how many revisions back to scan', 'LIMIT'),
    ('', 'brief', False, 'shorter output')],
    _('hg components [-f FILE1 -f FILE2...] [-r REVS] [-l LIMIT] [PATCH]'))
def bzcomponents(ui, repo, patchfile=None, **opts):
    '''Suggest a bugzilla product and component for a patch

    Scan through the last LIMIT commits to find bug product/components that
    touch the same files.

    The patch may be given as a file or a URL. If no patch is specified,
    the changes in the working directory will be used. If there are no
    changes, the topmost applied patch in your mq repository will be used.

    Alternatively, the -f option may be used to pass in one or more files
    that will be used to infer the component instead.
    '''
    components = guess_components(ui, repo, patchfile, **opts)

    if opts.get('brief'):
        if len(components) == 0:
            ui.write("no components found\n")
        else:
            r = [ "%s x %d" % (comp, count) for comp, count in components.most_common(3) ]
            ui.write(", ".join(r) + "\n")
        return

    ui.write("Potential components:\n")
    if len(components) == 0:
        ui.write("  none found in range (try higher --limit?)\n")
    else:
        for (comp, count) in components.most_common(5):
            ui.write("  %s: %d\n" % (comp, count))

@command('bugs', [
    ('f', 'file', [], 'see bugs for FILE', 'FILE'),
    ('l', 'limit', 100, 'how many revisions back to scan', 'LIMIT')],
    _('hg bugs [-f FILE1 -f FILE2...] [-l LIMIT] [PATCH]'))
def bzbugs(ui, repo, patchfile=None, **opts):
    '''List the bugs that have modified the files in a patch

    Scan through the last LIMIT commits to find bugs that touch the same files.

    The patch may be given as a file or a URL. If no patch is specified,
    the changes in the working directory will be used. If there are no
    changes, the topmost applied patch in your mq repository will be used.

    Alternatively, the -f option may be used to pass in one or more files
    that will be used instead.
    '''

    bugs = set()
    minBugs = 20
    for change in patch_changes(ui, repo, patchfile, **opts):
        m = BUG_RE.search(change.description())
        if m:
            bugs.add(m.group(2))
        if len(bugs) >= minBugs:
            break

    if bugs:
        for bug in bugs:
            ui.write("bug %s\n" % bug)
    else:
        ui.write("No bugs found\n")

@command('qtouched', [
    ('a', 'applied', None, 'only consider applied patches'),
    ('p', 'patch', '', 'restrict to given patch')],
    _('hg touched [-a] [-p PATCH] [FILE]'))
def touched(ui, repo, sourcefile=None, **opts):
    '''Show what files are touched by what patches

    If no file is given, print out a series of lines containing a
    patch and a file changed by that patch, for all files changed by
    all patches. This is mainly intended for easy grepping.

    If a file is given, print out the list of patches that touch that file.'''

    q = repo.mq

    if opts['patch'] and opts['applied']:
        raise error.Abort(_('Cannot use both -a and -p options'))

    if opts['patch']:
        patches = [ q.lookup(opts['patch']) ]
    elif opts['applied']:
        patches = [ p.name for p in q.applied ]
    else:
        patches = q.series

    for patchname in [ q.lookup(p) for p in patches ]:
        lines = q.opener(patchname)
        for filename, adds, removes, isbinary in patch.diffstatdata(lines):
            if sourcefile is None:
                ui.write(patchname + "\t" + filename + "\n")
            elif sourcefile == filename:
                ui.write(patchname + "\n")

qparent_re = re.compile('^qparent: (\S+)$', re.M)
top_re = re.compile('^top: (\S+)$', re.M)

@command('qrevert', [], _('hg qrevert REV'))
def qrevert(ui, repo, rev, **opts):
    '''
    Revert to a past mq state. This updates both the main checkout as well as
    the patch directory, and leaves either or both at a non-head revision.
    '''
    q = repo.mq
    if not q or not q.qrepo():
        raise error.Abort(_("No revisioned patch queue found"))
    p = q.qrepo()[q.qrepo().lookup(rev)]

    desc = p.description()
    m = qparent_re.search(desc)
    if not m:
        raise error.Abort(_("mq commit is missing needed metadata in comment"))
    qparent = m.group(1)
    m = top_re.search(desc)
    if not m:
        raise error.Abort(_("mq commit is missing needed metadata in comment"))
    top = m.group(1)

    # Check the main checkout before updating the mq checkout
    if repo[None].dirty(merge=False, branch=False):
        raise error.Abort(_("uncommitted local changes"))

    # Pop everything first
    q.pop(repo, None, force=False, all=True, nobackup=True, keepchanges=False)

    # Update the mq checkout
    commands.update(ui, q.qrepo(), rev=rev, check=True)
    # Update the main checkout
    commands.update(ui, repo, rev=qparent, check=False)

    # Push until reaching the correct patch
    if top != "(none)":
        mq.goto(ui, repo, top)

    # Needed?
    q.savedirty()

def mqcommit_info(ui, repo, opts):
    mqcommit = opts.pop('mqcommit', None)

    try:
        auto = ui.configbool('mqext', 'qcommit', None)
        if auto is None:
            raise error.ConfigError()
    except error.ConfigError:
        auto = ui.config('mqext', 'qcommit', 'auto').lower()

    if mqcommit is None and auto:
        if auto == 'auto':
            if repo.mq and repo.mq.qrepo():
                mqcommit = True
        else:
            mqcommit = True

    if mqcommit is None:
        return (None, None, None)

    q = repo.mq
    if q is None:
        raise error.Abort("-Q option given but mq extension not installed")
    r = q.qrepo()
    if r is None:
        raise error.Abort("-Q option given but patch directory is not "
                          "versioned")

    return mqcommit, q, r

def mqmessage_rep(ch, repo, values):
    if ch in values:
        r = values[ch]
        return r(repo, values) if callable(r) else r
    else:
        raise error.Abort("Invalid substitution %%%s in mqmessage template" %
                          ch)

def queue_info_string(repo, values):
    qparent_str = ''
    qtip_str = ''
    try:
        qparent_str = short(repo.lookup('qparent'))
        qtip_str = short(repo.lookup('qtip'))
    except error.RepoLookupError:
        qparent_str = short(repo.lookup('.'))
        qtip_str = qparent_str
    try:
        top_str = repo.mq.applied[-1].name
    except:
        top_str = '(none)'
    return '\nqparent: %s\nqtip: %s\ntop: %s' % (qparent_str, qtip_str, top_str)

def substitute_mqmessage(s, repo, values):
    newvalues = values.copy()
    newvalues['Q'] = queue_info_string
    s = re.sub(r'\[(.*)\]', lambda m: mqmessage_listrep(m.group(1), repo, newvalues), s, flags = re.S)
    s = re.sub(r'\%(\w)', lambda m: mqmessage_rep(m.group(1), repo, newvalues), s)
    return s

def mqmessage_listrep(message, repo, values):
    newvalues = values.copy()
    s = ''
    for patchname in values['[]']:
        newvalues['p'] = patchname
        s += substitute_mqmessage(message, repo, newvalues)
    return s

# Monkeypatch qrefresh in mq command table
#
# Note: The default value of the parameter is set here because it contains a
# newline, which messes up the formatting of the help message
def qrefresh_wrapper(orig, self, repo, *pats, **opts):
    mqmessage = opts.pop('mqmessage', '') or '%a: %p\n%s%Q'
    mqcommit, q, r = mqcommit_info(self, repo, opts)

    diffstat = ""
    if mqcommit and mqmessage:
        if mqmessage.find("%s") != -1:
            self.pushbuffer()
            m = cmdutil.matchmod.match(repo.root, repo.getcwd(), [],
                                       opts.get('include'), opts.get('exclude'),
                                       'relpath', auditor=repo.auditor)
            diffordiffstat(self, repo, mdiff.diffopts(),
                                   repo.dirstate.parents()[0], None, m,
                                   stat=True)
            diffstat = self.popbuffer()

    ret = orig(self, repo, *pats, **opts)
    if ret:
        return ret

    if mqcommit and len(q.applied) > 0:
        patch = q.applied[-1].name
        if r is None:
            raise error.Abort("no patch repository found when using -Q option")
        mqmessage = substitute_mqmessage(mqmessage, repo, { 'p': patch,
                                                            'a': 'UPDATE',
                                                            's': diffstat })
        commands.commit(r.ui, r, message=mqmessage)

# Monkeypatch qnew in mq command table
def qnew_wrapper(orig, self, repo, patchfn, *pats, **opts):
    mqmessage = opts.pop('mqmessage', None)
    mqcommit, q, r = mqcommit_info(self, repo, opts)

    ret = orig(self, repo, patchfn, *pats, **opts)
    if ret:
        return ret

    if mqcommit and mqmessage:
        mqmessage = substitute_mqmessage(mqmessage, repo, { 'p': patchfn,
                                                            'a': 'NEW' })
        commands.commit(r.ui, r, message=mqmessage)

# Monkeypatch qimport in mq command table
def qimport_wrapper(orig, self, repo, *filename, **opts):
    mqmessage = opts.pop('mqmessage', None)
    mqcommit, q, r = mqcommit_info(self, repo, opts)

    ret = orig(self, repo, *filename, **opts)
    if ret:
        return ret

    if mqcommit and mqmessage:
        # FIXME - can be multiple
        if len(filename) == 0:
            try:
                fname = q.fullseries[0]
            except:
                fname = q.full_series[0]
        else:
            fname = filename[0]
        mqmessage = substitute_mqmessage(mqmessage, repo, { 'p': fname,
                                                            'a': 'IMPORT' })
        commands.commit(r.ui, r, message=mqmessage)

# Monkeypatch qrename in mq command table
def qrename_wrapper(orig, self, repo, patch, name=None, **opts):
    mqmessage = opts.pop('mqmessage', None)
    mqcommit, q, r = mqcommit_info(self, repo, opts)

    ret = orig(self, repo, patch, name, **opts)
    if ret:
        return ret

    if mqcommit and mqmessage:
        if not name:
            name = patch
            patch = q.lookup('qtip')
        mqmessage = substitute_mqmessage(mqmessage, repo, { 'p': patch,
                                                            'n': name,
                                                            'a': 'RENAME' })
        commands.commit(r.ui, r, message=mqmessage)

# Monkeypatch qdelete in mq command table
#
# Note: The default value of the parameter is set here because it contains a
# newline, which messes up the formatting of the help message
def qdelete_wrapper(orig, self, repo, *patches, **opts):
    mqmessage = opts.pop('mqmessage', '') or '%a: %P\n\n[%a: %p\n]%Q'
    mqcommit, q, r = mqcommit_info(self, repo, opts)

    if mqcommit and mqmessage:
        patchnames = [ q.lookup(p) for p in patches ]

    ret = orig(self, repo, *patches, **opts)
    if ret:
        return ret

    if mqcommit and mqmessage:
        mqmessage = substitute_mqmessage(mqmessage, repo,
                                         { 'a': 'DELETE',
                                           'P': '%d patches - %s' % (len(patches), " ".join(patchnames)),
                                           '[]': patchnames })
        commands.commit(r.ui, r, message=mqmessage)

@command('urls', [
    ('d', 'date', '', _('show revisions matching date spec'), _('DATE')),
    ('r', 'rev', [], _('show the specified revision or range'), _('REV')),
    ('u', 'user', [], _('revisions committed by user'), _('USER')),
    ] + commands.logopts,
    _('hg urls [-l LIMIT] [NAME]'))
def urls(ui, repo, *paths, **opts):
    '''Display a list of urls for the last several commits.
    These are merely heuristic guesses and are intended for pasting into
    bugs after landing. If that makes no sense to you, then you are probably
    not the intended audience. It's mainly a Mozilla thing.

    Note that this will display the URL for your default repo, which may very
    well be something local. So you may need to give your outbound repo as
    an argument.
'''

    opts['template'] = '{node|short} {desc|firstline}\n'
    ui.pushbuffer()
    commands.log(ui, repo, **opts)
    lines = ui.popbuffer()
    if len(paths) == 0:
        paths = ['default']
    ui.pushbuffer()
    commands.paths(ui, repo, *paths)
    url = ui.popbuffer().rstrip()
    url = re.sub(r'^\w+', 'http', url)
    url = re.sub(r'(\w|\%|\.|-)+\@', '', url) # Remove usernames
    for line in lines.split('\n'):
        if len(line) > 0:
            rev, desc = line.split(' ', 1)
            ui.write(url + '/rev/' + rev + '\n  ' + desc + '\n\n')

# Monkeypatch qfinish in mq command table
def qfinish_wrapper(orig, self, repo, *revrange, **opts):
    mqmessage = opts.pop('mqmessage', None)
    mqcommit, q, r = mqcommit_info(self, repo, opts)

    ret = orig(self, repo, *revrange, **opts)
    if ret:
        return ret

    if mqcommit and mqmessage:
        mqmessage = substitute_mqmessage(mqmessage, repo, { })
        commands.commit(r.ui, r, message=mqmessage)

# Monkeypatch qfold in mq command table
def qfold_wrapper(orig, self, repo, *files, **opts):
    mqmessage = opts.pop('mqmessage', None)
    mqcommit, q, r = mqcommit_info(self, repo, opts)

    if mqcommit and mqmessage:
        patchnames = [ q.lookup(p) or p for p in files ]

    ret = orig(self, repo, *files, **opts)
    if ret:
        return ret

    if mqcommit and mqmessage:
        mqmessage = substitute_mqmessage(mqmessage, repo, { 'a': 'FOLD',
                                                            'n': ", ".join(patchnames),
                                                            'p': q.lookup('qtip') })
        commands.commit(r.ui, r, message=mqmessage)

# Monkeypatch qcrecord in mq command table (note that this comes from the crecord extension)
def qcrecord_wrapper(orig, self, repo, patchfn, *pats, **opts):
    mqmessage = opts.pop('mqmessage', None)
    mqcommit, q, r = mqcommit_info(self, repo, opts)

    ret = orig(self, repo, patchfn, *pats, **opts)
    if ret:
        return ret

    if mqcommit and mqmessage:
        mqmessage = substitute_mqmessage(mqmessage, repo, { 'p': patchfn,
                                                            'a': 'NEW' })
        commands.commit(r.ui, r, message=mqmessage)

def uisetup(ui):
    try:
        mq = extensions.find('mq')
        if mq is None:
            ui.debug("mqext extension is mostly disabled when mq is disabled\n")
            return
    except KeyError:
        ui.debug("mqext extension is mostly disabled when mq is not installed\n")
        return # mq not loaded at all

    # check whether mq is loaded before mqext. If not, do a nasty hack to set
    # it up first so that mqext can modify what it does and not have the
    # modifications get clobbered. Mercurial really needs to implement
    # inter-extension dependencies.

    aliases, entry = cmdutil.findcmd('init', commands.table)
    try:
        if not [ e for e in entry[1] if e[1] == 'mq' ]:
            orig = mq.uisetup
            mq.uisetup = lambda ui: deferred_uisetup(orig, ui, mq)
            return
    except AttributeError:
        # argh! Latest mq does not use uisetup anymore. Now it does its stuff
        # in extsetup (phase 2). Fortunately, it now installs its commands
        # early enough that the order no longer matters.
        pass

    uisetup_post_mq(ui, mq)

def deferred_uisetup(orig, ui, mq):
    orig(ui)
    uisetup_post_mq(ui, mq)

def uisetup_post_mq(ui, mq):
    entry = extensions.wrapcommand(mq.cmdtable, 'qrefresh', qrefresh_wrapper)
    entry[1].extend([('Q', 'mqcommit', None, 'commit change to patch queue'),
                     ('M', 'mqmessage', '', 'commit message for patch update')])

    entry = extensions.wrapcommand(mq.cmdtable, 'qnew', qnew_wrapper)
    entry[1].extend([('Q', 'mqcommit', None, 'commit change to patch queue'),
                     ('M', 'mqmessage', '%a: %p%Q', 'commit message for patch creation')])

    entry = extensions.wrapcommand(mq.cmdtable, 'qimport', qimport_wrapper)
    entry[1].extend([('Q', 'mqcommit', None, 'commit change to patch queue'),
                     ('M', 'mqmessage', 'IMPORT: %p%Q', 'commit message for patch import')])

    entry = extensions.wrapcommand(mq.cmdtable, 'qdelete', qdelete_wrapper)
    entry[1].extend([('Q', 'mqcommit', None, 'commit change to patch queue'),
                     ('M', 'mqmessage', '', 'commit message for patch deletion')])

    entry = extensions.wrapcommand(mq.cmdtable, 'qrename', qrename_wrapper)
    entry[1].extend([('Q', 'mqcommit', None, 'commit change to patch queue'),
                     ('M', 'mqmessage', '%a: %p -> %n%Q', 'commit message for patch rename')])

    entry = extensions.wrapcommand(mq.cmdtable, 'qfinish', qfinish_wrapper)
    entry[1].extend([('Q', 'mqcommit', None, 'commit change to patch queue'),
                     ('M', 'mqmessage', 'FINISHED%Q', 'commit message for patch finishing')])

    entry = extensions.wrapcommand(mq.cmdtable, 'qfold', qfold_wrapper)
    entry[1].extend([('Q', 'mqcommit', None, 'commit change to patch queue'),
                     ('M', 'mqmessage', '%a: %p <- %n%Q', 'commit message for patch folding')])

def find_extension(ext):
    try:
        return extensions.find(ext)
    except KeyError:
        return

def extsetup():
    crecord_ext = find_extension('crecord')
    if not crecord_ext:
        return

    # check whether qcrecord is loaded before mqext. If not, do a nasty hack to
    # set it up first so that mqext can modify what it does and not have the
    # modifications get clobbered. Mercurial really needs to implement
    # inter-extension dependencies.

    if not hasattr(crecord_ext, 'cmdtable'):
        raise Exception("mercurial version is too old")

    if 'qcrecord' not in crecord_ext.cmdtable:
        orig = crecord_ext.extsetup
        crecord_ext.extsetup = lambda: deferred_extsetup(orig)
        return

    extsetup_post_crecord()

def deferred_extsetup(orig):
    orig()
    extsetup_post_crecord()

def extsetup_post_crecord():
    crecord_ext = find_extension('crecord')
    mq_ext = find_extension('mq')
    if crecord_ext and mq_ext:
        entry = extensions.wrapcommand(crecord_ext.cmdtable, 'qcrecord', qcrecord_wrapper)
        entry[1].extend([('Q', 'mqcommit', None, 'commit change to patch queue'),
                         ('M', 'mqmessage', '%a: %p%Q', 'commit message for patch creation')])


def prechangegroup_hook(ui, repo, source=None, **kwargs):
    # No MQ patches applied. Nothing to do.
    if not repo.mq.applied:
        return

    if source not in ('push', 'pull'):
        return

    if ui.configbool('mqext', 'allowexchangewithapplied'):
        return

    ui.warn(_('cannot %s with MQ patches applied\n') % source)
    ui.warn(_('(allow this behavior by setting '
              'mqext.allowexchangewithapplied=true)\n'))
    return True


def reposetup(ui, repo):
    if not util.safehasattr(repo, 'mq'):
        return

    ui.setconfig('hooks', 'prechangegroup.mqpreventpull', prechangegroup_hook,
                 'mqext')
