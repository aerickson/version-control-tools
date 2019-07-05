# Copyright 2017 Octobus <contact@octobus.net>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.
"""help dealing with code source reformatting

The extension provides a way to run code-formatting tools in a way that avoids
conflicts related to this formatting when merging/rebasing code across the
reformatting.

A new `format-source` command is provided to register a code formatting tool to
be applied to merges and rebases of files matching a given pattern. This
information is recorded into the repository and reused when merging or
rebasing. The client doing the merge needs the extension for this logic to kick
in.

Code formatting tools have to be registered in the global configuration file,
though they may be overridden in per-repository configuration files. The tool
"name" will be used to identify a specific command line that must output the
formatted content on its standard output.

Per-tool configuration files (eg .clang-format) may be specified with the
"configpaths" suboption, which is read and registered at "hg format-source"
time. Note that any change in those files will trigger reformatting.

File matching may be restricted to a set of file extensions with the "fileext"
subooption.

Example::

    [format-source]
    json = python -m json.tool
    clang-format = /usr/bin/clang-format -style=Mozilla
    clang-format:configpaths = .clang-format, .clang-format-ignore
    clang-format:fileext = .cpp, .c, .h

We do not support specifying the mapping of tool name to tool command in the
repository itself for security reasons.

The code formatting information is tracked in a .hg-format-source file at the
root of the repository.

Warning: There is no special logic handling renames so moving files to a
directory not covered by the patterns used for the initial formatting will
likely fail.
"""

from __future__ import absolute_import
import os
import json
import tempfile

from mercurial import (
    commands,
    cmdutil,
    encoding,
    error,
    extensions,
    filemerge,
    match,
    merge,
    registrar,
    scmutil,
    util,
    worker,
)

from mercurial.i18n import _

OUR_DIR = os.path.dirname(__file__)
with open(os.path.join(OUR_DIR, '..', 'bootstrap.py')) as f:
    exec(f.read())

from mozhg.util import (
    is_firefox_repo,
)

__version__ = '0.1.0.dev'
testedwith = '4.4 4.5 4.6 4.7 4.8 4.9 5.0'
minimumhgversion = '4.4'
buglink = 'https://bugzilla.mozilla.org/enter_bug.cgi?product=Firefox%20Build%20System&component=Lint%20and%20Formatting'

cmdtable = {}

if util.safehasattr(registrar, 'command'):
    command = registrar.command(cmdtable)
else: # compat with hg < 4.3
    command = cmdutil.command(cmdtable)

if util.safehasattr(registrar, 'configitem'):
    # where available, register our config items
    configtable = {}
    configitem = registrar.configitem(configtable)
    configitem('format-source', '.*', default=None, generic=True)

file_storage_path = '.hg-format-source'


# TRACKING hg50
# cmdutil.add function signature changed
def cmdutiladd(ui, repo, storage_matcher):
    if util.versiontuple(n=2) >= (5, 0):
        uipathfn = scmutil.getuipathfn(repo, forcerelativevalue=True)
        cmdutil.add(ui, repo, storage_matcher, "", uipathfn, True)
    else:
        cmdutil.add(ui, repo, storage_matcher, "", True)


def return_default_formatter(repo, tool):
    if tool == 'clang-format':
        return return_default_clang_format(repo)
    if tool == 'prettier-format':
        return return_default_prettier_format(repo)

    msg = _("unknown format tool: %s (no 'format-source.%s' config)")
    raise error.Abort(msg.format(tool, tool))

def return_default_clang_format(repo):
    arguments = ['clang-format', '--assume-filename', '$HG_FILENAME', '-p']

    # On windows we need this to call the command in a shell, see Bug 1511594
    if os.name == 'nt':
        clang_format_cmd = ' '.join(['sh', 'mach'] + arguments)
    else:
        clang_format_cmd = ' '.join([os.path.join(repo.root, "mach")] + arguments)

    clang_format_cfgpaths = ['.clang-format', '.clang-format-ignore']
    clang_format_fileext = ('.cpp', '.c', '.cc', '.h')
    return clang_format_cmd, clang_format_cfgpaths, clang_format_fileext


def return_default_prettier_format(repo):
    arguments = ['prettier-format', '--assume-filename', '$HG_FILENAME', '-p']

    # On windows we need this to call the command in a shell, see Bug 1511594
    if os.name == 'nt':
        prettier_format_cmd = ' '.join(['sh', 'mach'] + arguments)
    else:
        prettier_format_cmd = ' '.join([os.path.join(repo.root, "mach")] + arguments)

    prettier_format_cfgpaths = ['.prettierrc', '.prettierignore']
    prettier_format_fileext = ('.js', '.jsx', '.jsm')
    return prettier_format_cmd, prettier_format_cfgpaths, prettier_format_fileext


def should_use_default(repo, tool):
    # Only enable formatting with prettier, to avoid unnecessary overhead.
    return tool == 'prettier-format' and not repo.ui.config(
        'format-source', tool) and is_firefox_repo(repo)


@command('format-source',
        [] + commands.walkopts + commands.commitopts + commands.commitopts2,
        _('TOOL FILES+'))
def cmd_format_source(ui, repo, tool, *pats, **opts):
    """register a tool to format source files during merges and rebases

    Record a mapping from the given file pattern FILES to a source formatting
    tool TOOL. Mappings are stored in the version-controlled file
    (automatically committed when format-source is used) .hg-format-source in
    the root of the checkout. The mapping causes TOOL to be run on FILES during
    future merge and rebase operations.

    The actual command run for TOOL needs to be registered in the config. See
    :hg:`help -e format-source` for details.

    """
    if repo.getcwd():
        msg = _("format-source must be run from repository root")
        hint = _("cd %s") % repo.root
        raise error.Abort(msg, hint=hint)

    if not pats:
        raise error.Abort(_('no files specified'))

    # XXX We support glob pattern only for now, the recursive behavior of various others is a bit wonky.
    for pattern in pats:
        if not pattern.startswith('glob:'):
            msg = _("format-source only supports explicit 'glob' patterns "
                    "for now ('%s')")
            msg %= pattern
            hint = _('maybe try with "glob:%s"') % pattern
            raise error.Abort(msg, hint=hint)

    # lock the repo to make sure no content is changed
    with repo.wlock():
        # formatting tool
        if ' ' in tool:
            raise error.Abort(_("tool name cannot contain space: '%s'") % tool)

        # if tool was not specified in the cfg maybe we can use our mozilla firefox in tree
        # clang-format and/or prettier tools
        if should_use_default(repo, tool):
            shell_tool, tool_config_files, file_ext = return_default_formatter(
                repo, tool)
        else:
            shell_tool = repo.ui.config('format-source', tool)
            tool_config_files = repo.ui.configlist('format-source',
                                                   '%s:configpaths' % tool)
            file_ext = tuple(
                repo.ui.configlist('format-source', '%s:fileext' % tool))

        if not shell_tool:
            msg = _("unknown format tool: %s (no 'format-source.%s' config)")
            raise error.Abort(msg.format(tool, tool))
        if not file_ext:
            msg = _("no {}:fileext present".format(tool))
            raise error.Abort(msg.format(tool, tool))
        cmdutil.bailifchanged(repo)
        cmdutil.checkunfinished(repo, commit=True)
        wctx = repo[None]
        # files to be formatted
        matcher = scmutil.match(wctx, pats, opts)
        files = list(wctx.matches(matcher))

        if util.versiontuple(n=2) >= (4, 7):
            # In 4.7 we have ui.makeprogress
            with ui.makeprogress(
                    _('formatting'), unit=_('files'),
                    total=len(files)) as progress:
                proc = worker.worker(ui, 0.1, batchformat,
                                     (repo, wctx, tool, shell_tool, file_ext),
                                     files)
                for filepath in proc:
                    progress.increment(item=filepath)
        else:
            proc = worker.worker(ui, 0.1, batchformat,
                                 (repo, wctx, tool, shell_tool, file_ext),
                                 files)
            # Wait for everything to finish
            for filepath in proc:
                pass

        # update the storage to mark formatted file as formatted
        with repo.wvfs(file_storage_path, mode='ab') as storage:
            for pattern in pats:
                # XXX if pattern was relative, we need to reroot it from the
                # repository root. For now we constrained the command to run
                # at the root of the repository.
                data = {'tool': encoding.unifromlocal(tool),
                        'pattern': encoding.unifromlocal(pattern)}
                if tool_config_files:
                    data['configpaths'] = [encoding.unifromlocal(path)
                                           for path in tool_config_files]
                entry = json.dumps(data, sort_keys=True)
                assert '\n' not in entry
                storage.write('%s\n' % entry)

        if file_storage_path not in wctx:
            storage_matcher = scmutil.match(wctx, ['path:' + file_storage_path])
            cmdutiladd(ui, repo, storage_matcher)

        # commit the whole
        with repo.lock():
            commit_patterns = ['path:' + file_storage_path]
            commit_patterns.extend(pats)
            return commands._docommit(ui, repo, *commit_patterns, **opts)

def batchformat(repo, wctx, tool, shell_tool, file_ext, files):
    for filepath in files:
        if not filepath.endswith(file_ext):
            repo.ui.debug("batchformat skip: {}\n".format(filepath))
            continue
        flags = wctx.flags(filepath)
        if 'l' in flags:
            # links should just be skipped
            repo.ui.warn(_('Skipping symlink, %s\n') % filepath)
            continue
        newcontent = run_tools(repo, tool, shell_tool, filepath, filepath)
        # if the formatting tool returned an empty string then do not write it
        if len(newcontent):
            # XXX we could do the whole commit in memory
            with repo.wvfs(filepath, 'wb') as formatted_file:
                formatted_file.write(newcontent)
            wctx.filectx(filepath).setflags(False, 'x' in flags)
        yield filepath

def run_tools(repo, tool, cmd, filepath, filename):
    """Run the a formatter tool on a specific file"""
    env = encoding.environ.copy()
    env['DISABLE_TELEMETRY'] = '1'
    ui = repo.ui
    if os.name == 'nt' and should_use_default(repo, tool):
        filename_to_use = filename.replace("/", "\\\\")
        filepath_to_use = filepath.replace("\\", "\\\\")
        # ENV doesn't work on windows as it does on POSIX
        cmd_to_use = cmd.replace('$HG_FILENAME', filename_to_use)
    else:
        filename_to_use = filename
        filepath_to_use = filepath
        cmd_to_use = cmd
        env['HG_FILENAME'] = filename_to_use

    # XXX escape special characters in filepath
    format_cmd = "%s %s" % (cmd_to_use, filepath_to_use)
    ui.debug('running %s\n' % format_cmd)
    ui.pushbuffer(subproc=True)
    try:
        ui.system(format_cmd,
                  environ=env,
                  cwd=repo.root,
                  onerr=error.Abort,
                  errprefix=tool)
    finally:
        newcontent = ui.popbuffer()
    return newcontent

def touched(repo, old_ctx, new_ctx, paths):
    matcher = rootedmatch(repo, new_ctx, paths)
    if any(path in new_ctx for path in paths):
        status = old_ctx.status(other=new_ctx, match=matcher)
        return bool(status.modified or status.added)
    return False

def formatted(repo, old_ctx, new_ctx):
    """retrieve the list of formatted patterns between <old> and <new>

    return a {'tool': [patterns]} mapping
    """
    new_formatting = {}
    if touched(repo, old_ctx, new_ctx, [file_storage_path]):
        # quick and dirty line diffing
        # (the file is append only by contract)

        new_lines = set(new_ctx[file_storage_path].data().splitlines())
        old_lines = set()
        if file_storage_path in old_ctx:
            old_lines = set(old_ctx[file_storage_path].data().splitlines())
        new_lines -= old_lines
        for line in new_lines:
            entry = json.loads(line)
            def getkey(key):
                return encoding.unitolocal(entry[key])
            new_formatting.setdefault(getkey('tool'), set()).add(getkey('pattern'))
    if file_storage_path in old_ctx:
        for line in old_ctx[file_storage_path].data().splitlines():
            entry = json.loads(line)
            if not entry.get('configpaths'):
                continue
            configpaths = [encoding.unitolocal(path) for path in entry['configpaths']]
            def getkey(key):
                return encoding.unitolocal(entry[key])
            if touched(repo, old_ctx, new_ctx, configpaths):
                new_formatting.setdefault(getkey('tool'), set()).add(getkey('pattern'))
    return new_formatting

def allformatted(repo, local, other, ancestor):
    """return a mapping of formatting needed for all involved changesets
    """

    cachekey = (local.node, other.node(), ancestor.node())
    cached = getattr(repo, '_formatting_cache', {}).get(cachekey)

    if cached is not None:
        return cached

    local_formatting = formatted(repo, ancestor, local)
    other_formatting = formatted(repo, ancestor, other)
    full_formatting = local_formatting.copy()
    for key, value in other_formatting.items():
        if key in local_formatting:
            value = value | local_formatting[key]
        full_formatting[key] = value

    all = [
        (local, local_formatting),
        (other, other_formatting),
        (ancestor, full_formatting)
    ]
    for ctx, formatting in all:
        for tool, patterns in formatting.items():
            formatting[tool] = rootedmatch(repo, ctx, patterns)

    final = tuple(formatting for __, formatting in all)
    getattr(repo, '_formatting_cache', {})[cachekey] = cached

    return final

def rootedmatch(repo, ctx, patterns):
    """match patterns against the root of a repository"""
    # rework of basectx.match to ignore current working directory

    # Only a case insensitive filesystem needs magic to translate user input
    # to actual case in the filesystem.
    icasefs = not util.fscasesensitive(repo.root)
    if util.safehasattr(match, 'icasefsmatcher'): #< hg 4.3
        if icasefs:
            return match.icasefsmatcher(repo.root, repo.root, patterns,
                                        default='glob', auditor=repo.auditor,
                                        ctx=ctx)
        else:
            return match.match(repo.root, repo.root, patterns, default='glob',
                               auditor=repo.auditor, ctx=ctx)
    else:
        return match.match(repo.root, repo.root, patterns, default='glob',
                           auditor=repo.auditor, ctx=ctx, icasefs=icasefs)

def apply_formatting(repo, formatting, fctx):
    """apply formatting to a file context (if applicable)"""
    data = None
    for tool, matcher in sorted(formatting.items()):
        # matches?
        if matcher(fctx.path()):
            if should_use_default(repo, tool):
                shell_tool, _, supported_file_ext = return_default_formatter(
                    repo, tool)
            else:
                shell_tool = repo.ui.config('format-source', tool)
                supported_file_ext = tuple(
                    repo.ui.configlist('format-source', '%s:fileext' % tool))

            if data is None:
                data = fctx.data()

            if not fctx.path().endswith(supported_file_ext):
                repo.ui.debug('Apply formatting skipping: {}\n'.format(fctx.path()))
                continue

            if not shell_tool:
                msg = _("format-source, no command defined for '%s',"
                        " skipping formatting: '%s'\n")
                msg %= (tool, fctx.path())
                repo.ui.warn(msg)
                continue
            # Determine the extension of the file to pass it to the temp file
            _, fileext = os.path.splitext(fctx.path())
            tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=fileext, mode='wb')
            tmp_file.write(data)
            tmp_file.flush()
            tmp_file.close()
            formatted_data = run_tools(repo, tool, shell_tool, tmp_file.name, fctx.path())
            # delete the tmp file
            os.remove(tmp_file.name)

            if len(formatted_data) > 0:
                data = formatted_data

    if data is not None:
        fctx.data = lambda: data


def wrap_filemerge44(origfunc, premerge, repo, wctx, mynode, orig, fcd, fco, fca,
                   *args, **kwargs):
    """wrap the file merge logic to apply formatting to files that need it"""
    _update_filemerge_content(repo, fcd, fco, fca)
    return origfunc(premerge, repo, wctx, mynode, orig, fcd, fco, fca,
                    *args, **kwargs)

def wrap_filemerge43(origfunc, premerge, repo, mynode, orig, fcd, fco, fca,
                   *args, **kwargs):
    """wrap the file merge logic to apply formatting to files that needs it"""
    _update_filemerge_content(repo, fcd, fco, fca)
    return origfunc(premerge, repo, mynode, orig, fcd, fco, fca,
                    *args, **kwargs)

def _update_filemerge_content(repo, fcd, fco, fca):
    if fcd.isabsent() or fco.isabsent() or fca.isabsent():
        return
    local = fcd._changectx
    other = fco._changectx
    ances = fca._changectx
    all = allformatted(repo, local, other, ances)
    local_formatting, other_formatting, full_formatting = all

    repo.ui.debug('Files to be: {} {} {}\n'.format(fcd.path(), fco.path(), fca.path()))
    apply_formatting(repo, local_formatting, fco)
    apply_formatting(repo, other_formatting, fcd)
    apply_formatting(repo, full_formatting, fca)

    if 'data' in vars(fcd): # XXX hacky way to check if data overwritten
        file_path = repo.wvfs.join(fcd.path())
        with open(file_path, 'wb') as local_file:
            local_file.write(fcd.data())

def wrap_update(orig, repo, *args, **kwargs):
    """install the formatting cache"""
    repo._formatting_cache = {}
    try:
        return orig(repo, *args, **kwargs)
    finally:
        del repo._formatting_cache

def uisetup(self):
    pre44hg = filemerge._filemerge.__code__.co_argcount < 9
    if pre44hg:
        extensions.wrapfunction(filemerge, '_filemerge', wrap_filemerge43)
    else:
        extensions.wrapfunction(filemerge, '_filemerge', wrap_filemerge44)
    extensions.wrapfunction(merge, 'update', wrap_update)
