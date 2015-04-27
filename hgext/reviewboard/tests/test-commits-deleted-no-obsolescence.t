#require docker
  $ . $TESTDIR/hgext/reviewboard/tests/helpers.sh
  $ commonenv

  $ bugzilla create-bug TestProduct TestComponent summary

  $ cd client
  $ echo 'foo' > foo0
  $ hg commit -A -m 'root commit'
  adding foo0
  $ hg push --noreview
  pushing to ssh://*:$HGPORT6/test-repo (glob)
  searching for changes
  remote: adding changesets
  remote: adding manifests
  remote: adding file changes
  remote: added 1 changesets with 1 changes to 1 files
  remote: Trying to insert into pushlog.
  remote: Inserted into the pushlog db successfully.
  $ hg phase --public -r .

  $ echo 'foo1' > foo1
  $ hg commit -A -m 'Bug 1 - Foo 1'
  adding foo1
  $ echo 'foo2' > foo2
  $ hg commit -A -m 'Bug 1 - Foo 2'
  adding foo2
  $ echo 'foo3' > foo3
  $ hg commit -A -m 'Bug 1 - Foo 3'
  adding foo3
  $ echo 'foo4' > foo4
  $ hg commit -A -m 'Bug 1 - Foo 4'
  adding foo4
  $ echo 'foo5' > foo5
  $ hg commit -A -m 'Bug 1 - Foo 5'
  adding foo5

  $ hg push
  pushing to ssh://*:$HGPORT6/test-repo (glob)
  searching for changes
  remote: adding changesets
  remote: adding manifests
  remote: adding file changes
  remote: added 5 changesets with 5 changes to 5 files
  remote: Trying to insert into pushlog.
  remote: Inserted into the pushlog db successfully.
  submitting 5 changesets for review
  
  changeset:  1:a252038ad074
  summary:    Bug 1 - Foo 1
  review:     http://*:$HGPORT1/r/2 (draft) (glob)
  
  changeset:  2:c3d0947fefb7
  summary:    Bug 1 - Foo 2
  review:     http://*:$HGPORT1/r/3 (draft) (glob)
  
  changeset:  3:de473ef3c9d2
  summary:    Bug 1 - Foo 3
  review:     http://*:$HGPORT1/r/4 (draft) (glob)
  
  changeset:  4:f5691a90b4d0
  summary:    Bug 1 - Foo 4
  review:     http://*:$HGPORT1/r/5 (draft) (glob)
  
  changeset:  5:d86c61a23fc8
  summary:    Bug 1 - Foo 5
  review:     http://*:$HGPORT1/r/6 (draft) (glob)
  
  review id:  bz://1/mynick
  review url: http://*:$HGPORT1/r/1 (draft) (glob)
  (visit review url to publish this review request so others can see it)

  $ rbmanage publish 1

Popping the last commit truncates the review set

  $ hg strip -r 5 --no-backup
  0 files updated, 0 files merged, 1 files removed, 0 files unresolved
  $ hg push
  pushing to ssh://*:$HGPORT6/test-repo (glob)
  searching for changes
  no changes found
  submitting 4 changesets for review
  
  changeset:  1:a252038ad074
  summary:    Bug 1 - Foo 1
  review:     http://*:$HGPORT1/r/2 (glob)
  
  changeset:  2:c3d0947fefb7
  summary:    Bug 1 - Foo 2
  review:     http://*:$HGPORT1/r/3 (glob)
  
  changeset:  3:de473ef3c9d2
  summary:    Bug 1 - Foo 3
  review:     http://*:$HGPORT1/r/4 (glob)
  
  changeset:  4:f5691a90b4d0
  summary:    Bug 1 - Foo 4
  review:     http://*:$HGPORT1/r/5 (glob)
  
  review id:  bz://1/mynick
  review url: http://*:$HGPORT1/r/1 (glob)
  [1]

Review request 6 should be added to the list of discard on publish rids.

  $ rbmanage dumpreview 1
  id: 1
  status: pending
  public: true
  bugs:
  - '1'
  commit: bz://1/mynick
  submitter: default+5
  summary: bz://1/mynick
  description:
  - /r/2 - Bug 1 - Foo 1
  - /r/3 - Bug 1 - Foo 2
  - /r/4 - Bug 1 - Foo 3
  - /r/5 - Bug 1 - Foo 4
  - /r/6 - Bug 1 - Foo 5
  - ''
  - 'Pull down these commits:'
  - ''
  - hg pull -r d86c61a23fc8978f5d0c59a0ce608dc5d4312da5 http://*:$HGPORT/test-repo (glob)
  target_people: []
  extra_data:
    p2rb: true
    p2rb.commits: '[["a252038ad0749f90e448cb7384a47ca3642c6362", 2], ["c3d0947fefb784864eb53620d62c721bf58bbd27",
      3], ["de473ef3c9d292c8cf419958e4f3a3318a2d6a4d", 4], ["f5691a90b4d0ef04bbf08408d9f214356811db40",
      5], ["d86c61a23fc8978f5d0c59a0ce608dc5d4312da5", 6]]'
    p2rb.discard_on_publish_rids: '[6]'
    p2rb.identifier: bz://1/mynick
    p2rb.is_squashed: true
    p2rb.unpublished_rids: '[]'
  draft:
    bugs:
    - '1'
    commit: bz://1/mynick
    summary: bz://1/mynick
    description:
    - /r/2 - Bug 1 - Foo 1
    - /r/3 - Bug 1 - Foo 2
    - /r/4 - Bug 1 - Foo 3
    - /r/5 - Bug 1 - Foo 4
    - ''
    - 'Pull down these commits:'
    - ''
    - hg pull -r f5691a90b4d0ef04bbf08408d9f214356811db40 http://*:$HGPORT/test-repo (glob)
    target_people: []
    extra:
      p2rb: true
      p2rb.commits: '[["a252038ad0749f90e448cb7384a47ca3642c6362", 2], ["c3d0947fefb784864eb53620d62c721bf58bbd27",
        3], ["de473ef3c9d292c8cf419958e4f3a3318a2d6a4d", 4], ["f5691a90b4d0ef04bbf08408d9f214356811db40",
        5]]'
      p2rb.discard_on_publish_rids: '[]'
      p2rb.identifier: bz://1/mynick
      p2rb.is_squashed: true
      p2rb.unpublished_rids: '[]'
    diffs:
    - id: 7
      revision: 2
      base_commit_id: 93d9429b41ecf0d2ad8c62b6ea26686dd20330f4
      patch:
      - diff -r 93d9429b41ec -r f5691a90b4d0 foo1
      - "--- /dev/null\tThu Jan 01 00:00:00 1970 +0000"
      - "+++ b/foo1\tThu Jan 01 00:00:00 1970 +0000"
      - '@@ -0,0 +1,1 @@'
      - +foo1
      - diff -r 93d9429b41ec -r f5691a90b4d0 foo2
      - "--- /dev/null\tThu Jan 01 00:00:00 1970 +0000"
      - "+++ b/foo2\tThu Jan 01 00:00:00 1970 +0000"
      - '@@ -0,0 +1,1 @@'
      - +foo2
      - diff -r 93d9429b41ec -r f5691a90b4d0 foo3
      - "--- /dev/null\tThu Jan 01 00:00:00 1970 +0000"
      - "+++ b/foo3\tThu Jan 01 00:00:00 1970 +0000"
      - '@@ -0,0 +1,1 @@'
      - +foo3
      - diff -r 93d9429b41ec -r f5691a90b4d0 foo4
      - "--- /dev/null\tThu Jan 01 00:00:00 1970 +0000"
      - "+++ b/foo4\tThu Jan 01 00:00:00 1970 +0000"
      - '@@ -0,0 +1,1 @@'
      - +foo4

  $ rbmanage publish 1

Review 6 should be marked as discarded

  $ rbmanage dumpreview 6
  id: 6
  status: discarded
  public: true
  bugs:
  - '1'
  commit: null
  submitter: default+5
  summary: Bug 1 - Foo 5
  description: Bug 1 - Foo 5
  target_people: []
  extra_data:
    p2rb: true
    p2rb.commit_id: d86c61a23fc8978f5d0c59a0ce608dc5d4312da5
    p2rb.identifier: bz://1/mynick
    p2rb.is_squashed: false

Dropping the first commit should shuffle all the reviews down the line.
NOTE: If we ever employ heuristic matching on the server, this test
likely gets invalidated.

  $ hg -q rebase -s 2 -d 0
  $ hg strip -r 1 --no-backup
  $ hg push
  pushing to ssh://*:$HGPORT6/test-repo (glob)
  searching for changes
  remote: adding changesets
  remote: adding manifests
  remote: adding file changes
  remote: added 3 changesets with 0 changes to 3 files (+1 heads)
  remote: Trying to insert into pushlog.
  remote: Inserted into the pushlog db successfully.
  submitting 3 changesets for review
  
  changeset:  1:3299fd5f5fca
  summary:    Bug 1 - Foo 2
  review:     http://*:$HGPORT1/r/2 (glob)
  
  changeset:  2:4fcbb12a36e4
  summary:    Bug 1 - Foo 3
  review:     http://*:$HGPORT1/r/3 (glob)
  
  changeset:  3:d768dcb976de
  summary:    Bug 1 - Foo 4
  review:     http://*:$HGPORT1/r/4 (glob)
  
  review id:  bz://1/mynick
  review url: http://*:$HGPORT1/r/1 (glob)

The first commit was rewritten (we assume all subsequent were as well).

  $ rbmanage dumpreview 2
  id: 2
  status: pending
  public: true
  bugs:
  - '1'
  commit: null
  submitter: default+5
  summary: Bug 1 - Foo 1
  description: Bug 1 - Foo 1
  target_people: []
  extra_data:
    p2rb: true
    p2rb.commit_id: a252038ad0749f90e448cb7384a47ca3642c6362
    p2rb.identifier: bz://1/mynick
    p2rb.is_squashed: false
  draft:
    bugs:
    - '1'
    commit: null
    summary: Bug 1 - Foo 2
    description: Bug 1 - Foo 2
    target_people: []
    extra:
      p2rb: true
      p2rb.commit_id: 3299fd5f5fca4800c424e989c65615edb52a421b
      p2rb.identifier: bz://1/mynick
      p2rb.is_squashed: false
    diffs:
    - id: 9
      revision: 2
      base_commit_id: 93d9429b41ecf0d2ad8c62b6ea26686dd20330f4
      patch:
      - diff -r 93d9429b41ec -r 3299fd5f5fca foo2
      - "--- /dev/null\tThu Jan 01 00:00:00 1970 +0000"
      - "+++ b/foo2\tThu Jan 01 00:00:00 1970 +0000"
      - '@@ -0,0 +1,1 @@'
      - +foo2

The last review request that got invalidated in the shuffle should
be in the list of review requests to discard when the squashed review
request is published.

  $ rbmanage dumpreview 1
  id: 1
  status: pending
  public: true
  bugs:
  - '1'
  commit: bz://1/mynick
  submitter: default+5
  summary: bz://1/mynick
  description:
  - /r/2 - Bug 1 - Foo 1
  - /r/3 - Bug 1 - Foo 2
  - /r/4 - Bug 1 - Foo 3
  - /r/5 - Bug 1 - Foo 4
  - ''
  - 'Pull down these commits:'
  - ''
  - hg pull -r f5691a90b4d0ef04bbf08408d9f214356811db40 http://*:$HGPORT/test-repo (glob)
  target_people: []
  extra_data:
    p2rb: true
    p2rb.commits: '[["a252038ad0749f90e448cb7384a47ca3642c6362", 2], ["c3d0947fefb784864eb53620d62c721bf58bbd27",
      3], ["de473ef3c9d292c8cf419958e4f3a3318a2d6a4d", 4], ["f5691a90b4d0ef04bbf08408d9f214356811db40",
      5]]'
    p2rb.discard_on_publish_rids: '[5]'
    p2rb.identifier: bz://1/mynick
    p2rb.is_squashed: true
    p2rb.unpublished_rids: '[]'
  draft:
    bugs:
    - '1'
    commit: bz://1/mynick
    summary: bz://1/mynick
    description:
    - /r/2 - Bug 1 - Foo 2
    - /r/3 - Bug 1 - Foo 3
    - /r/4 - Bug 1 - Foo 4
    - ''
    - 'Pull down these commits:'
    - ''
    - hg pull -r d768dcb976decf31b8ac1431701fefdacd31a390 http://*:$HGPORT/test-repo (glob)
    target_people: []
    extra:
      p2rb: true
      p2rb.commits: '[["3299fd5f5fca4800c424e989c65615edb52a421b", 2], ["4fcbb12a36e4f7a606c8ad86636e232d2133cfe1",
        3], ["d768dcb976decf31b8ac1431701fefdacd31a390", 4]]'
      p2rb.discard_on_publish_rids: '[]'
      p2rb.identifier: bz://1/mynick
      p2rb.is_squashed: true
      p2rb.unpublished_rids: '[]'
    diffs:
    - id: 8
      revision: 3
      base_commit_id: 93d9429b41ecf0d2ad8c62b6ea26686dd20330f4
      patch:
      - diff -r 93d9429b41ec -r d768dcb976de foo2
      - "--- /dev/null\tThu Jan 01 00:00:00 1970 +0000"
      - "+++ b/foo2\tThu Jan 01 00:00:00 1970 +0000"
      - '@@ -0,0 +1,1 @@'
      - +foo2
      - diff -r 93d9429b41ec -r d768dcb976de foo3
      - "--- /dev/null\tThu Jan 01 00:00:00 1970 +0000"
      - "+++ b/foo3\tThu Jan 01 00:00:00 1970 +0000"
      - '@@ -0,0 +1,1 @@'
      - +foo3
      - diff -r 93d9429b41ec -r d768dcb976de foo4
      - "--- /dev/null\tThu Jan 01 00:00:00 1970 +0000"
      - "+++ b/foo4\tThu Jan 01 00:00:00 1970 +0000"
      - '@@ -0,0 +1,1 @@'
      - +foo4

Publish to get us up to date, but we're not going to test the publishing
behaviour here. We'll save that for other tests.

  $ rbmanage publish 1

Try removing a commit in the middle.

  $ hg -q rebase -s 3 -d 1
  $ hg strip -r 2 --no-backup

  $ hg push
  pushing to ssh://*:$HGPORT6/test-repo (glob)
  searching for changes
  remote: adding changesets
  remote: adding manifests
  remote: adding file changes
  remote: added 1 changesets with 0 changes to 1 files (+1 heads)
  remote: Trying to insert into pushlog.
  remote: Inserted into the pushlog db successfully.
  submitting 2 changesets for review
  
  changeset:  1:3299fd5f5fca
  summary:    Bug 1 - Foo 2
  review:     http://*:$HGPORT1/r/2 (glob)
  
  changeset:  2:7f4c8af7c6c4
  summary:    Bug 1 - Foo 4
  review:     http://*:$HGPORT1/r/3 (glob)
  
  review id:  bz://1/mynick
  review url: http://*:$HGPORT1/r/1 (glob)

  $ mozreview stop
  stopped 8 containers
