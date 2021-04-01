.. _hgmozilla_extensions:

=====================================
Installing and Configuring Extensions
=====================================

A vanilla install of Mercurial does not have many features. This is an
intentional choice by Mercurial to keep the base install simple and free
of foot guns.

Guided Installation of Extensions
=================================

If you have a copy of the Firefox source repository, you can run
`./mach vcs-setup` to run an interactive wizard that will
guide you through the process of configuring Mercurial.

.. important::

   `mach vcs-setup` is the recommended way to configure Mercurial
   for use at Mozilla. The sections below effectively duplicate the
   work that `mach vcs-setup` does for you.

Installing Extensions
=====================

To install an extension, you'll need to add a line to your Mercurial
configuration file.

As a user, you care about the following configuration files:

1. Your global hgrc
2. A per-repository hgrc

Your user-global configuration file is ``~/.hgrc``. Settings in this
file apply to all ``hg`` commands you perform everywhere on your system
(for your current user).

Each repository that you clone or initialize has an optional
``.hg/hgrc`` file that provides repository-specific configurations.

Mercurial starts by loading and applying settings from global
configuration files and then overlays configurations from each
lesser-scoped files.

.. tip::

   To learn more about Mercurial configuration files, run ``hg help
   config``.

To install an extension, you add a line under the ``[extensions]``
section of a config file like the following::

  [extensions]
  foo=path/to/extension

This is saying *activate the **foo** extension whose code is present at
**path/to/extension***.

Core Extensions That Almost Everyone Wants
==========================================

Mercurial ships with a number of built-in extensions. Of these, every
user will almost always want to install the following extensions:

fsmonitor
   Monitor the filesystem for changes using so operations querying
   filesystem state complete faster.

.. important::

   fsmonitor is highly recommended when interacting with the Firefox
   repository. It will make Mercurial commands faster.

Since core extensions are bundled with Mercurial, they have a special
syntax that makes them easier to install::

  [extensions]
  fsmonitor=

Core Extensions to Perform History Rewriting
============================================

Out of the box, Mercurial only allows commits operations to be additive.
If you make a mistake, the solution is to create a new commit that fixes
it. You can't rewrite old commits. You can't change the order of
existing commits. You can't change the shape of the DAG of the commits.

These operations all have something in common: they rewrite history.

.. note::

   Mercurial doesn't allow history rewriting by default because it is a
   major foot gun for people new to version control. A potential
   side-effect of history rewriting is data loss or confusion due to
   loss of state. Mercurial believes that these risks should be opt-in
   and has thus shipped without any history rewriting features enabled
   by default.

Mercurial ships with a number of built-in extensions that enable history
rewriting:

histedit
   Enables the ``hg histedit`` command, which brings up a text editor
   listing commits, allowing you to change order and specify actions to
   perform.

   The functionality is roughly equivalent to ``git rebase -i``.
rebase
   Enables the ``hg rebase`` command, which allows you to splice commits
   across different chains in the DAG.
strip
   Enables you to delete changesets completely.

Core Extensions to Enable Different Workflows
=============================================

Mercurial ships with some extensions that enable alternate workflows.
These include:

shelve
   Enables uncommitted work to be saved to a standalone file without
   being committed to the repository.

chg
---

`chg <https://www.mercurial-scm.org/repo/hg/file/tip/contrib/chg/README>`_
is a C wrapper for the ``hg`` command. Typically, when you type ``hg``,
a new Python process is created, Mercurial is loaded, and your requested
command runs and the process exits.

With ``chg``, a Mercurial *command server* background process is created
that runs Mercurial. When you type ``chg``, a C program connects to that
background process and executes Mercurial commands.

**chg can drastically speed up Mercurial.** This is because the overhead
for launching a new Python process is high (often over 50ms) and the
overhead for loading Mercurial state into that process can also be high.
With ``chg``, you pay this cost once and all subsequent commands
effectively eliminate the Python and Mercurial startup overhead. For
example::

   $ time hg --version
   real    0m0.118s
   user    0m0.100s
   sys     0m0.015s

   $ time chg --version
   real    0m0.012s
   user    0m0.000s
   sys     0m0.004s

   $ time hg export
   real    0m0.137s
   user    0m0.093s
   sys     0m0.042s

   $ time chg export
   real    0m0.034s
   user    0m0.000s
   sys     0m0.004s

Here, we see ~100ms wall time improvement with chg activated. That may not
sound like a lot, but you will notice.

If you have installed Mercurial through a package manager (such as Homebrew
or APT) you may already have ``chg`` installed. For more information, see 
`here <https://www.mercurial-scm.org/wiki/CHg>`_.

3rd Party Extensions You Should Highly Consider
===============================================

evolve
------

The `evolve extension <https://www.mercurial-scm.org/wiki/EvolveExtension>`_
opens up new workflows that harness Mercurial's ability to record how
changesets *evolve* over time.

Typically, when history is rewritten, new commits are created and the old
ones are discarded. With the ``evolve`` extension enabled, Mercurial instead
hides the old commits and writes metadata holding the relationship between
old and new commits. This metadata can be transferred between clients,
allowing clients to make intelligent decisions about how to recover from
rewritten history. For example, if a force push is performed, a client
will know exactly what rebase to perform to mimic what was done elsewhere.

The ``evolve`` extension also enables useful Mercurial commands such as
``hg previous``, ``hg next``, and ``hg amend`` (which is a shortcut for
``hg commit --amend``).

