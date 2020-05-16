  $ cat >> $HGRCPATH << EOF
  > [ui]
  > ssh = python "$TESTDIR/pylib/mercurial-support/dummyssh"
  > 
  > [extensions]
  > pushlog = $TESTDIR/hgext/pushlog
  > EOF

  $ export USER=hguser
  $ hg init server
  $ cd server
  $ hg serve -d -p $HGPORT --pid-file server.pid -E error.log -A access.log
  $ cat server.pid >> $DAEMON_PIDS
  $ cd ..

Lack of permissions to create pushlog file should not impact read-only operations

  $ chmod u-w server/.hg
  $ chmod g-w server/.hg

  $ hg clone ssh://user@dummy/$TESTTMP/server clone
  no changes found
  added 0 pushes
  updating to branch default
  0 files updated, 0 files merged, 0 files removed, 0 files unresolved

Seed the pushlog for our next test

  $ chmod u+w server/.hg
  $ chmod g+w server/.hg

  $ cd clone
  $ touch foo
  $ hg -q commit -A -m initial
  $ hg push
  pushing to ssh://user@dummy/$TESTTMP/server
  searching for changes
  remote: adding changesets
  remote: adding manifests
  remote: adding file changes
  remote: recorded push in pushlog
  remote: added 1 changesets with 1 changes to 1 files

Lack of permissions on pushlog should prevent pushes from completing

  $ chmod 444 ../server/.hg/pushlog2.db
  $ echo perms > foo
  $ hg commit -m 'bad permissions'
  $ hg push
  pushing to ssh://user@dummy/$TESTTMP/server
  searching for changes
  remote: adding changesets
  remote: adding manifests
  remote: adding file changes
  remote: error recording into pushlog (attempt to write a readonly database); please retry your push
  remote: transaction abort!
  remote: rolling back pushlog
  remote: rollback completed
  remote: pretxnchangegroup.pushlog hook failed
  abort: push failed on remote
  [255]

Confirm no errors in log

  $ cat ../server/error.log
