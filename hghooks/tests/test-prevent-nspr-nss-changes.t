  $ hg init server
  $ cat > server/.hg/hgrc << EOF
  > [hooks]
  > pretxnchangegroup.prevent_nspr_nss = python:mozhghooks.prevent_nspr_nss_changes.hook
  > EOF

  $ hg -q clone server client
  $ cat >> client/.hg/hgrc << EOF
  > [extensions]
  > strip =
  > EOF

  $ cd client

Regular file changes work

  $ touch file0
  $ hg -q commit -A -m "initial"

  $ hg push
  pushing to $TESTTMP/server
  searching for changes
  adding changesets
  adding manifests
  adding file changes
  added 1 changesets with 1 changes to 1 files

Without magic word, can't change nsprpub/

  $ mkdir nsprpub
  $ echo "new file" > nsprpub/file
  $ hg -q commit -A -m 'add nsprpub/file'

  $ hg push
  pushing to $TESTTMP/server
  searching for changes
  adding changesets
  adding manifests
  adding file changes
  (1 changesets contain changes to protected nsprpub/ directory: 89dbe12f8b46)
  ************************************************************************
  You do not have permissions to modify files under nsprpub/ or security/nss/
  
  These directories are kept in sync with the canonical upstream repositories at
  https://hg.mozilla.org/projects/nspr and https://hg.mozilla.org/projects/nss
  
  Please contact the NSPR/NSS maintainers at nss-dev@mozilla.org or on IRC
  channel #nss to request that your changes are merged, released and uplifted.
  ************************************************************************
  transaction abort!
  rollback completed
  abort: pretxnchangegroup.prevent_nspr_nss hook failed
  [255]

With magic word, can change nsprpub/

  $ hg -q strip --no-backup --keep -r .

  $ hg -q commit -A -m 'add nsprpub/file, UPGRADE_NSPR_RELEASE'
  $ hg push
  pushing to $TESTTMP/server
  searching for changes
  adding changesets
  adding manifests
  adding file changes
  added 1 changesets with 1 changes to 1 files

Without magic word, can't change security/nss/

  $ mkdir security
  $ mkdir security/nss
  $ touch security/nss/file
  $ hg -q commit -A -m 'add security/nss/file'

  $ hg push
  pushing to $TESTTMP/server
  searching for changes
  adding changesets
  adding manifests
  adding file changes
  (1 changesets contain changes to protected security/nss/ directory: 0792e81e881c)
  ************************************************************************
  You do not have permissions to modify files under nsprpub/ or security/nss/
  
  These directories are kept in sync with the canonical upstream repositories at
  https://hg.mozilla.org/projects/nspr and https://hg.mozilla.org/projects/nss
  
  Please contact the NSPR/NSS maintainers at nss-dev@mozilla.org or on IRC
  channel #nss to request that your changes are merged, released and uplifted.
  ************************************************************************
  transaction abort!
  rollback completed
  abort: pretxnchangegroup.prevent_nspr_nss hook failed
  [255]

With magic word, can change security/nss/

  $ hg -q strip --no-backup --keep -r .

  $ hg -q commit -A -m 'add security/nss/file, UPGRADE_NSS_RELEASE'
  $ hg push
  pushing to $TESTTMP/server
  searching for changes
  adding changesets
  adding manifests
  adding file changes
  added 1 changesets with 1 changes to 1 files

Multiple changesets handled properly, each changeset touching protected
files must contain the correct corresponding keyword

  $ touch file1
  $ hg -q commit -A -m 'add file1'
  $ touch nsprpub/file2
  $ hg -q commit -A -m 'add nsprpub/file2, UPGRADE_NSPR_RELEASE'
  $ touch security/nss/file3
  $ hg -q commit -A -m 'add security/nss/file3'

  $ hg push
  pushing to $TESTTMP/server
  searching for changes
  adding changesets
  adding manifests
  adding file changes
  (1 changesets contain changes to protected security/nss/ directory: b0b7d814f51e)
  ************************************************************************
  You do not have permissions to modify files under nsprpub/ or security/nss/
  
  These directories are kept in sync with the canonical upstream repositories at
  https://hg.mozilla.org/projects/nspr and https://hg.mozilla.org/projects/nss
  
  Please contact the NSPR/NSS maintainers at nss-dev@mozilla.org or on IRC
  channel #nss to request that your changes are merged, released and uplifted.
  ************************************************************************
  transaction abort!
  rollback completed
  abort: pretxnchangegroup.prevent_nspr_nss hook failed
  [255]

  $ hg -q commit --amend -m 'add security/nss/file3, UPGRADE_NSS_RELEASE'
  $ hg push
  pushing to $TESTTMP/server
  searching for changes
  adding changesets
  adding manifests
  adding file changes
  added 3 changesets with 3 changes to 3 files
