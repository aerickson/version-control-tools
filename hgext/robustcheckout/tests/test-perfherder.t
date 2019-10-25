  $ . $TESTDIR/hgext/robustcheckout/tests/helpers.sh
  $ export TASKCLUSTER_INSTANCE_TYPE=c5.4xlarge

  $ hg robustcheckout http://localhost:$HGPORT/repo0 dest --revision 5d6cdc75a09b
  (using Mercurial *.*) (glob)
  ensuring http://$LOCALHOST:$HGPORT/repo0@5d6cdc75a09b is available at dest
  (sharing from new pooled repository b8b78f0253d822e33ba652fd3d80a5c0837cfdf3)
  requesting all changes
  adding changesets
  adding manifests
  adding file changes
  added 3 changesets with 3 changes to 1 files (+1 heads)
  new changesets b8b78f0253d8:aada1b3e573f (hg44 !)
  searching for changes
  no changes found
  1 files updated, 0 files merged, 0 files removed, 0 files unresolved
  updated to 5d6cdc75a09bcccf76f9339a28e1d89360c59dce
  PERFHERDER_DATA: {"framework": {"name": "vcs"}, "suites": \[{"extraOptions": \["c5\.4xlarge"\], "lowerIsBetter": true, "name": "clone", "serverUrl": "\$LOCALHOST:\$HGPORT", "shouldAlert": false, "subtests": \[\], "value": \d+\.\d+}, {"extraOptions": \["c5\.4xlarge"\], "lowerIsBetter": true, "name": "update", "serverUrl": "\$LOCALHOST:\$HGPORT", "shouldAlert": false, "subtests": \[\], "value": \d+\.\d+}, {"extraOptions": \["c5\.4xlarge"\], "lowerIsBetter": true, "name": "overall", "serverUrl": "\$LOCALHOST:\$HGPORT", "shouldAlert": false, "subtests": \[\], "value": \d+\.\d+}, {"extraOptions": \["c5\.4xlarge"\], "lowerIsBetter": true, "name": "overall_clone", "serverUrl": "\$LOCALHOST:\$HGPORT", "shouldAlert": false, "subtests": \[\], "value": \d+\.\d+}, {"extraOptions": \["c5\.4xlarge"\], "lowerIsBetter": true, "name": "overall_clone_fullcheckout", "serverUrl": "\$LOCALHOST:\$HGPORT", "shouldAlert": false, "subtests": \[\], "value": \d+\.\d+}]} (re)

No pull reports properly

  $ hg robustcheckout http://localhost:$HGPORT/repo0 dest --revision 5d6cdc75a09b
  (using Mercurial *.*) (glob)
  ensuring http://$LOCALHOST:$HGPORT/repo0@5d6cdc75a09b is available at dest
  (existing repository shared store: $TESTTMP/share/b8b78f0253d822e33ba652fd3d80a5c0837cfdf3/.hg)
  0 files updated, 0 files merged, 0 files removed, 0 files unresolved
  updated to 5d6cdc75a09bcccf76f9339a28e1d89360c59dce
  PERFHERDER_DATA: {"framework": {"name": "vcs"}, "suites": \[{"extraOptions": \["c5.4xlarge"\], "lowerIsBetter": true, "name": "update", "serverUrl": "\$LOCALHOST:\$HGPORT", "shouldAlert": false, "subtests": \[]\, "value": \d+\.\d+}, {"extraOptions": \["c5.4xlarge"\], "lowerIsBetter": true, "name": "overall", "serverUrl": "\$LOCALHOST:\$HGPORT", "shouldAlert": false, "subtests": \[\], "value": \d+\.\d+}, {"extraOptions": \["c5.4xlarge"\], "lowerIsBetter": true, "name": "overall_nopull", "serverUrl": "\$LOCALHOST:\$HGPORT", "shouldAlert": false, "subtests": \[\], "value": \d+\.\d+}, {"extraOptions": \["c5.4xlarge"\], "lowerIsBetter": true, "name": "overall_nopull_fullcheckout", "serverUrl": "\$LOCALHOST:\$HGPORT", "shouldAlert": false, "subtests": \[\], "value": \d+\.\d+}, {"extraOptions": \["c5.4xlarge"\], "lowerIsBetter": true, "name": "overall_nopull_populatedwdir", "serverUrl": "\$LOCALHOST:\$HGPORT", "shouldAlert": false, "subtests": \[\], "value": \d+\.\d+}\]} (re)

Existing share reports properly

  $ hg robustcheckout http://localhost:$HGPORT/repo0 dest2 --revision 5d6cdc75a09b
  (using Mercurial *) (glob)
  ensuring http://$LOCALHOST:$HGPORT/repo0@5d6cdc75a09b is available at dest2
  (sharing from existing pooled repository b8b78f0253d822e33ba652fd3d80a5c0837cfdf3)
  searching for changes
  no changes found
  1 files updated, 0 files merged, 0 files removed, 0 files unresolved
  updated to 5d6cdc75a09bcccf76f9339a28e1d89360c59dce
  PERFHERDER_DATA: {"framework": {"name": "vcs"}, "suites": \[{"extraOptions": \["c5.4xlarge"\], "lowerIsBetter": true, "name": "clone", "serverUrl": "\$LOCALHOST:\$HGPORT", "shouldAlert": false, "subtests": \[\], "value": \d+\.\d+}, {"extraOptions": \["c5.4xlarge"\], "lowerIsBetter": true, "name": "update", "serverUrl": "\$LOCALHOST:\$HGPORT", "shouldAlert": false, "subtests": \[\], "value": \d+\.\d+}, {"extraOptions": \["c5.4xlarge"\], "lowerIsBetter": true, "name": "overall", "serverUrl": "\$LOCALHOST:\$HGPORT", "shouldAlert": false, "subtests": \[\], "value": \d+\.\d+}, {"extraOptions": \["c5.4xlarge"\], "lowerIsBetter": true, "name": "overall_pull", "serverUrl": "\$LOCALHOST:\$HGPORT", "shouldAlert": false, "subtests": \[\], "value": \d+\.\d+}, {"extraOptions": \["c5.4xlarge"\], "lowerIsBetter": true, "name": "overall_pull_fullcheckout", "serverUrl": "\$LOCALHOST:\$HGPORT", "shouldAlert": false, "subtests": \[\], "value": \d+\.\d+}, {"extraOptions": \["c5.4xlarge"\], "lowerIsBetter": true, "name": "overall_pull_emptywdir", "serverUrl": "\$LOCALHOST:\$HGPORT", "shouldAlert": false, "subtests": \[\], "value": \d+\.\d+}\]} (re)

Confirm no errors in log

  $ cat ./server/error.log
