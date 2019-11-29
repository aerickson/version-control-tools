# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# This script is used to manage Docker containers in the context of running
# Mercurial tests.

from __future__ import absolute_import

from collections import deque
import docker
import errno
import hashlib
import json
import os
import pickle
import re
import requests
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time
import urlparse
import uuid
import warnings

import backports.lzma as lzma

from docker.errors import (
    APIError as DockerAPIError,
    DockerException,
)
from contextlib import contextmanager
from io import BytesIO

import concurrent.futures as futures
from coverage.data import CoverageData

from .util import (
    limited_threadpoolexecutor,
    wait_for_http,
)
from .vctutil import (
    get_and_write_vct_node,
    hg_executable,
)


HERE = os.path.abspath(os.path.dirname(__file__))
DOCKER_DIR = os.path.normpath(os.path.join(HERE, '..', 'docker'))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..'))


def rsync(*args):
    prog = None
    for path in os.environ['PATH'].split(':'):
        candidate = os.path.join(path, 'rsync')
        if os.path.exists(candidate):
            prog = candidate
            break

    if not prog:
        raise Exception('Could not find rsync program')

    subprocess.check_call([prog] + list(args), cwd='/')


class DockerNotAvailable(Exception):
    """Error raised when Docker is not available."""


def params_from_env(env):
    """Obtain Docker connect parameters from the environment.

    This returns a tuple that should be used for base_url and tls arguments
    of Docker.__init__.
    """
    host = env.get('DOCKER_HOST', None)
    tls = False

    if env.get('DOCKER_TLS_VERIFY'):
        tls = True

    # This is likely encountered with boot2docker.
    cert_path = env.get('DOCKER_CERT_PATH')
    if cert_path:
        ca_path = os.path.join(cert_path, 'ca.pem')
        tls_cert_path = os.path.join(cert_path, 'cert.pem')
        tls_key_path = os.path.join(cert_path, 'key.pem')

        # Hostnames will attempt to be verified by default. We don't know what
        # the hostname should be, so don't attempt it.
        tls = docker.tls.TLSConfig(
            client_cert=(tls_cert_path, tls_key_path),
            ssl_version=ssl.PROTOCOL_TLSv1, ca_cert=ca_path, verify=True,
            assert_hostname=False)

    # docker-py expects the protocol to have something TLS in it. tcp:// won't
    # work. Hack around it until docker-py works as expected.
    if tls and host:
        if host.startswith('tcp://'):
            host = host.replace('tcp://', 'https://')

    return host, tls


@contextmanager
def docker_rollback_on_error(client):
    """Perform Docker operations as a transaction of sorts.

    Returns a modified Docker client instance. Creation events performed
    on the client while the context manager is active will be undone if
    an exception occurs. This allows complex operations such as the creation
    of multiple containers to be rolled back automatically if an error
    occurs.
    """
    created_containers = set()
    created_networks = set()

    class ProxiedDockerClient(client.__class__):
        def create_container(self, *args, **kwargs):
            res = super(ProxiedDockerClient, self).create_container(*args, **kwargs)
            created_containers.add(res['Id'])
            return res

        def create_network(self, *args, **kwargs):
            res = super(ProxiedDockerClient, self).create_network(*args, **kwargs)
            created_networks.add(res['Id'])
            return res

    old_class = client.__class__
    try:
        client.__class__ = ProxiedDockerClient
        yield client
    except Exception:
        for cid in created_containers:
            client.remove_container(cid, v=True, force=True)
        for nid in created_networks:
            client.remove_network(nid)
        raise
    finally:
        client.__class__ = old_class


class Docker(object):
    def __init__(self, state_path, url, tls=False):
        self._ddir = DOCKER_DIR
        self._state_path = state_path
        self.state = {
            'clobber-hgweb': None,
            'clobber-hgmaster': None,
            'clobber-hgrb': None,
            'clobber-rbweb': None,
            'images': {},
            'containers': {},
            'last-pulse-id': None,
            'last-rbweb-id': None,
            'last-rbweb-bootstrap-id': None,
            'last-hgrb-id': None,
            'last-hgmaster-id': None,
            'last-hgweb-id': None,
            'last-ldap-id': None,
            'last-vct-id': None,
            'last-treestatus-id': None,
            'vct-cid': None,
        }

        if os.path.exists(state_path):
            with open(state_path, 'rb') as fh:
                self.state = json.load(fh)

        keys = (
            'clobber-hgweb',
            'clobber-hgmaster',
            'clobber-hgrb',
            'clobber-rbweb',
            'last-pulse-id',
            'last-rbweb-id',
            'last-rbweb-bootstrap-id',
            'last-hgmaster-id',
            'last-hgrb-id',
            'last-hgweb-id',
            'last-ldap-id',
            'last-vct-id',
            'last-treestatus-id',
            'vct-cid',
        )
        for k in keys:
            self.state.setdefault(k, None)

        try:
            self.client = docker.DockerClient(base_url=url, tls=tls,
                                              version='auto')
            self.api_client = self.client.api
        except DockerException:
            self.client = None
            self.api_client = None
            return

        # We need API 1.22+ for some networking APIs.
        if docker.utils.compare_version('1.22',
                                        self.api_client.api_version) < 0:
            warnings.warn('Warning: unable to speak to Docker servers older '
                          'than Docker 1.10.x')
            self.client = None
            self.api_client = None
            return

        # Try to obtain a network hostname for the Docker server. We use this
        # for determining where to look for opened ports.
        # This is a bit complicated because Docker can be running from a local
        # socket or or another host via something like boot2docker.

        # This is wrong - the gateway returned is the _internal_ IP gateway for
        # running containers.  docker makes no guarantee it will be routable
        # from the host; and on MacOS this is indeed not routable.  Port mapping
        # and querying for the HostIP should be used instead (or use a sane
        # docker build system such as docker-compose).

        docker_url = urlparse.urlparse(self.api_client.base_url)
        self.docker_hostname = docker_url.hostname
        if docker_url.hostname in ('localunixsocket', 'localhost', '127.0.0.1'):
            networks = self.api_client.networks()
            for network in networks:
                if network['Name'] == 'bridge':
                    ipam = network['IPAM']
                    try:
                        addr = ipam['Config'][0]['Gateway']
                    except KeyError:
                        warnings.warn('Warning: Unable to determine ip '
                                      'address of the docker gateway. Please '
                                      'ensure docker is listening on a tcp '
                                      'socket by setting -H '
                                      'tcp://127.0.0.1:4243 in your docker '
                                      'configuration file.')
                        self.client = None
                        self.api_client = None
                        break

                    self.docker_hostname = addr
                    break

    def is_alive(self):
        """Whether the connection to Docker is alive."""
        if not self.client:
            return False

        # This is a layering violation with docker.client, but meh.
        try:
            self.api_client._get(self.api_client._url('/version'), timeout=5)
            return True
        except requests.exceptions.RequestException:
            return False

    def _get_vct_files(self):
        """Obtain all the files in the version-control-tools repo.

        Returns a dict of relpath to full path.
        """
        env = dict(os.environ)
        env['HGRCPATH'] = '/dev/null'
        args = [hg_executable(), '-R', '.', 'locate']
        with open(os.devnull, 'wb') as null:
            files = subprocess.check_output(
                args, env=env, cwd=ROOT, stderr=null).splitlines()

        # Add untracked files from extra-files directory. This can be used
        # as a means to add files that aren't tracked by version control for
        # whatever reason.
        extra_files_path = os.path.join(ROOT, 'extra-files')
        if os.path.exists(extra_files_path):
            for p in os.listdir(extra_files_path):
                files.append(os.path.join('extra-files', p))

        paths = {}
        for f in files:
            full = os.path.join(ROOT, f)
            # Filter out files that have been removed in the working
            # copy but haven't been committed.
            if os.path.exists(full):
                paths[f] = full

        return paths

    def clobber_needed(self, name):
        """Test whether a clobber file has been touched.

        We periodically need to force certain actions to occur. There is a
        "clobber" mechanism to facilitate this.

        There are various ``clobber.<name>`` files on the filesystem. When
        the files are touched, it signals a clobber is required.

        This function answers the question of whether a clobber is required
        for a given action. Returns True if yes, False otherwise.

        If a clobber file doesn't exist, a clobber is never needed.
        """
        path = os.path.join(ROOT, 'testing', 'clobber.%s' % name)
        key = 'clobber-%s' % name

        try:
            oldmtime = self.state[key]
        except KeyError:
            oldmtime = None

        try:
            newmtime = os.path.getmtime(path)
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise
            # No clobber file means no clobber needed.
            return False

        if oldmtime is None or newmtime > oldmtime:
            self.state[key] = int(time.time())
            return True

        return False

    def import_base_image(self, repository, tagprefix, url, digest):
        """Secure Docker base image importing.

        `docker pull` is not secure because it doesn't verify digests before
        processing data. Instead, it "tees" the image content to the image
        processing layer and the hasher and verifies the digest matches
        expected only after all image processing has occurred. While fast
        (images don't need to be buffered before being applied), it is insecure
        because a malicious image could exploit a bug in image processing
        and take control of the Docker daemon and your machine.

        This function takes a repository name, tag prefix, URL, and a SHA-256
        hex digest as arguments and returns the Docker image ID for the image.
        The contents of the image are, of course, verified to match the digest
        before being applied.

        The imported image is "tagged" in the repository specified. The tag of
        the created image is set to the specified prefix and the SHA-256 of a
        combination of the URL and digest. This serves as a deterministic cache
        key so subsequent requests for a (url, digest) can be returned nearly
        instantly. Of course, this assumes: a) the Docker daemon and its stored
        images can be trusted b) content of URLs is constant.
        """
        tag = '%s-%s' % (tagprefix,
                         hashlib.sha256('%s%s' % (url, digest)).hexdigest())
        for image in self._get_sorted_images():
            repotags = image['RepoTags'] or []
            for repotag in repotags:
                r, t = repotag.split(':')
                if r == repository and t == tag:
                    return image['Id']

        # We didn't get a cache hit. Download the URL.
        with tempfile.NamedTemporaryFile() as fh:
            digester = hashlib.sha256()
            res = requests.get(url, stream=True)
            for chunk in res.iter_content(8192):
                fh.write(chunk)
                digester.update(chunk)

            # Verify content before doing anything with it.
            # (This is the part Docker gets wrong.)
            if digester.hexdigest() != digest:
                raise Exception('downloaded Docker image does not match '
                                'digest:  %s; got %s expected %s'
                                % (url, digester.hexdigest(), digest))

            fh.flush()
            fh.seek(0)

            # Docker 1.10 no longer appears to allow import of .xz files
            # directly. Do the decompress locally.
            if url.endswith('.xz'):
                fh = lzma.decompress(fh.read())

            res = self.api_client.import_image_from_data(
                fh, repository=repository, tag=tag)
            # docker-py doesn't parse the JSON response in what is almost
            # certainly a bug. Do it ourselves.
            return json.loads(res.strip())['status']

    def ensure_built(self, name, verbose=False, use_last=False):
        """Ensure a Docker image from a builder directory is built and up to
        date.

        This function is docker build++. Under the hood, it talks to the same
        ``build`` Docker API. However, it does one important thing differently:
        it builds the context archive manually.

        We supplement all contexts with the content of the source in this
        repository related to building Docker containers. This is done by
        scanning the Dockerfile for references to extra files to include.

        If a line in the Dockerfile has the form ``# %include <path>``,
        the relative path specified on that line will be matched against
        files in the source repository and added to the context under the
        path ``extra/vct/``. If an entry ends in a ``/``, we add all files
        under that directory. Otherwise, we assume it is a literal match and
        only add a single file.

        This added content can be ``ADD``ed to the produced image inside the
        Dockerfile. If the content changes, the Docker image ID changes and the
        cache is invalidated. This effectively allows downstream consumers to
        call ``ensure_built()`` as there *is the image up to date* check.

        If ``use_last`` is true, the last built image will be returned, if
        available.
        """
        if use_last:
            for image in self._get_sorted_images():
                repotags = image['RepoTags'] or []
                for repotag in repotags:
                    repo, tag = repotag.split(':', 1)
                    if repo == name:
                        return image['Id']

        p = os.path.join(self._ddir, 'builder-%s' % name)
        if not os.path.isdir(p):
            raise Exception('Unknown Docker builder name: %s' % name)

        dockerfile_lines = []
        vct_paths = []
        with open(os.path.join(p, 'Dockerfile'), 'rb') as fh:
            for line in fh:
                line = line.rstrip()
                if line.startswith('# %include'):
                    vct_paths.append(line[len('# %include '):])

                # Detect our security optimized pull mode.
                if line.startswith('FROM secure:'):
                    parts = line[len('FROM secure:'):]
                    repository, tagprefix, digest, url = parts.split(':', 3)
                    if not digest.startswith('sha256 '):
                        raise Exception('FROM secure: requires sha256 digests')

                    digest = digest[len('sha256 '):]

                    base_image = self.import_base_image(repository, tagprefix,
                                                        url, digest)
                    line = b'FROM %s' % base_image.encode('ascii')

                dockerfile_lines.append(line)

        # We build the build context for the image manually because we need to
        # include things outside of the directory containing the Dockerfile.
        buf = BytesIO()
        tar = tarfile.open(mode='w', fileobj=buf)

        for root, dirs, files in os.walk(p):
            for f in files:
                if f == '.dockerignore':
                    raise Exception('.dockerignore not currently supported!')

                full = os.path.join(root, f)
                rel = full[len(p) + 1:]

                ti = tar.gettarinfo(full, arcname=rel)

                # Make files owned by root:root to prevent mismatch between
                # host and container. Without this, files can be owned by
                # undefined users.
                ti.uid = 0
                ti.gid = 0

                fh = None

                # We may modify the content of the Dockerfile. Grab it from
                # memory.
                if rel == 'Dockerfile':
                    df = b'\n'.join(dockerfile_lines)
                    ti.size = len(df)
                    fh = BytesIO(df)
                    fh.seek(0)
                else:
                    fh = open(full, 'rb')

                tar.addfile(ti, fileobj=fh)
                fh.close()

        if vct_paths:
            # We grab the set of tracked files in this repository.
            vct_files = sorted(self._get_vct_files().keys())
            added = set()
            for p in vct_paths:
                ap = os.path.join(ROOT, p)
                if not os.path.exists(ap):
                    raise Exception('specified path not under version '
                                    'control: %s' % p)
                if p.endswith('/'):
                    for f in vct_files:
                        if not f.startswith(p) and p != '/':
                            continue
                        full = os.path.join(ROOT, f)
                        rel = 'extra/vct/%s' % f
                        if full in added:
                            continue
                        tar.add(full, rel)
                else:
                    full = os.path.join(ROOT, p)
                    if full in added:
                        continue
                    rel = 'extra/vct/%s' % p
                    tar.add(full, rel)

        tar.close()

        # Need to seek to beginning so .read() inside docker.client will return
        # data.
        buf.seek(0)

        for s in self.api_client.build(fileobj=buf, custom_context=True,
                                       rm=True, decode=True):
            if 'stream' not in s:
                continue

            s = s['stream']

            if verbose:
                for l in s.strip().splitlines():
                    sys.stdout.write('%s> %s\n' % (name, l))

            match = re.match('^Successfully built ([a-f0-9]{12})$', s.rstrip())
            if match:
                image = match.group(1)
                # There is likely a trailing newline.
                full_image = self.get_full_image(image.rstrip())

                # We only tag the image once to avoid redundancy.
                have_tag = False
                for i in self.api_client.images():
                    if i['Id'] == full_image:
                        repotags = i['RepoTags'] or []
                        for repotag in repotags:
                            repo, tag = repotag.split(':')
                            if repo == name:
                                have_tag = True

                        break

                if not have_tag:
                    self.api_client.tag(full_image, name, str(uuid.uuid1()))

                return full_image

        raise Exception('Unable to confirm image was built: %s' % name)

    def ensure_images_built(self, names, ansibles=None, existing=None,
                            verbose=False, use_last=False, max_workers=None):
        """Ensure that multiple images are built.

        ``names`` is a list of Docker images to build.
        ``ansibles`` describes how to build ansible-based images. Keys
        are repositories. Values are tuples of (playbook, builder). If an
        image in the specified repositories is found, we'll use it as the
        start image. Otherwise, we'll use the configured builder.

        If ``use_last`` is true, we will use the last built image instead
        of building a new one.

        If ``max_workers`` is less than 1 or is None, use the default number
        of worker threads to perform I/O intensive tasks.  Otherwise use the
        specified number of threads.  Useful for debugging and reducing
        load on resource-constrained machines.
        """
        ansibles = ansibles or {}
        existing = existing or {}

        # Verify existing images actually exist.
        docker_images = self.all_docker_images()

        images = {k: v for k, v in existing.items() if v in docker_images}

        missing = (set(names) | set(ansibles.keys())) - set(images.keys())

        # Collect last images if wanted.
        # This is also done inside the building functions. But doing it here
        # as well prevents some overhead to create the vct container. The code
        # duplication is therefore warranted.
        if use_last:
            for image in self._get_sorted_images():
                repotags = image['RepoTags'] or []
                for repotag in repotags:
                    repo, tag = repotag.split(':', 1)
                    if repo in missing:
                        images[repo] = image['Id']
                        missing.remove(repo)

        if not missing:
            return images

        missing_ansibles = {k: ansibles[k] for k in missing if k in ansibles}
        start_images = {}
        for image in self._get_sorted_images():
            repotags = image['RepoTags'] or []
            for repotag in repotags:
                repo, tag = repotag.split(':', 1)
                if repo in missing_ansibles:
                    start_images[repo] = image['Id']

        def build(name, **kwargs):
            image = self.ensure_built(name, use_last=use_last, **kwargs)
            return name, image

        def build_ansible(f_builder, vct_cid, playbook, repository=None,
                          builder=None, start_image=None, verbose=False):

            if start_image and use_last:
                return repository, start_image

            # Wait for the builder image to be built.
            if f_builder:
                start_image = f_builder.result()
                builder = None

            image, repo, tag = self.run_ansible(playbook,
                                                repository=repository,
                                                builder=builder,
                                                start_image=start_image,
                                                vct_cid=vct_cid,
                                                verbose=verbose)
            return repository, image

        with self.vct_container(verbose=verbose) as vct_state, \
                limited_threadpoolexecutor(len(missing), max_workers) as e:
            vct_cid = vct_state['Id']
            fs = []
            builder_fs = {}
            for n in sorted(missing):
                if n in names:
                    fs.append(e.submit(build, n, verbose=verbose))
                else:
                    playbook, builder = ansibles[n]
                    start_image = start_images.get(n)
                    if start_image:
                        # If a clobber is needed, ignore the base image
                        # and always use the builder. If no clobber needed,
                        # always use the base image.
                        if self.clobber_needed(n):
                            start_image = None
                        else:
                            builder = None

                    # Builders may be shared across images. This code it to
                    # ensure we only build the builder image once.
                    if builder:
                        bf = builder_fs.get(builder)
                        if not bf:
                            bf = e.submit(self.ensure_built,
                                          'ansible-%s' % builder,
                                          verbose=verbose)
                            builder_fs[builder] = bf
                    else:
                        bf = None

                    fs.append(e.submit(build_ansible, bf, vct_cid, playbook,
                                       repository=n, builder=builder,
                                       start_image=start_image,
                                       verbose=verbose))

            for f in futures.as_completed(fs):
                name, image = f.result()
                images[name] = image

        return images

    def run_ansible(self, playbook, repository=None,
                    builder=None, start_image=None, vct_image=None,
                    vct_cid=None, verbose=False):
        """Create an image with the results of Ansible playbook execution.

        This function essentially does the following:

        1. Obtain a starting image.
        2. Create and start a container with the content of v-c-t mounted
           in that container.
        3. Run the ansible playbook specified.
        4. Tag the resulting image.

        You can think of this function as an alternative mechanism for building
        Docker images. Instead of Dockerfiles, we use Ansible to "provision"
        our containers.

        You can provision containers either from scratch or incrementally.

        To build from scratch, specify a ``builder``. This corresponds to a
        directory in v-c-t that contains a Dockerfile specifying how to install
        Ansible in an image. e.g. ``centos6`` will be expanded to
        ``builder-ansible-centos6``.

        To build incrementally, specify a ``start_image``. This is an existing
        Docker image.

        One of ``builder`` or ``start_image`` must be specified. Both cannot be
        specified.
        """
        if not builder and not start_image:
            raise ValueError('At least 1 of "builder" or "start_image" '
                             'must be defined')
        if builder and start_image:
            raise ValueError('Only 1 of "builder" and "start_image" may '
                             'be defined')

        repository = repository or playbook

        if builder:
            full_builder = 'ansible-%s' % builder
            start_image = self.ensure_built(full_builder, verbose=verbose)

        # Docker imposes a limit of 127 stacked images, at which point an
        # error will be raised creating a new container. Since Ansible
        # containers are incremental images, it's only a matter of time before
        # this limit gets hit.
        #
        # When we approach this limit, walk the stack of images and reset the
        # base image to the first image built with Ansible. This ensures
        # some cache hits and continuation and prevents us from brushing into
        # the limit.
        history = self.api_client.history(start_image)
        if len(history) > 120:
            # Newest to oldest.
            for base in history:
                if base['CreatedBy'].startswith('/sync-and-build'):
                    start_image = base['Id']

        with self.vct_container(image=vct_image, cid=vct_cid, verbose=verbose) \
                as vct_state:
            cmd = ['/sync-and-build', '%s.yml' % playbook]
            host_config = self.api_client.create_host_config(
                volumes_from=[vct_state['Name']])
            with self.create_container(start_image, command=cmd,
                                       host_config=host_config) as cid:
                output = deque(maxlen=20)
                self.api_client.start(cid)

                # attach() can return early if network timeout occurs. See
                # https://github.com/docker/docker-py/issues/2166. So poll
                # container state and automatically re-attach if the container
                # is still running.
                while True:
                    for s in self.api_client.attach(cid, stream=True,
                                                    logs=True):
                        for line in s.splitlines():
                            if line != '':
                                output.append(line)
                                if verbose:
                                    print('%s> %s' % (repository, line))

                    state = self.api_client.inspect_container(cid)

                    if not state['State']['Running']:
                        break

                    if verbose:
                        print('%s> (timeout waiting for output; re-attaching)' %
                              repository)

                if state['State']['ExitCode']:
                    # This should arguably be part of the exception.
                    for line in output:
                        print('ERROR %s> %s' % (repository, line))
                    raise Exception('Ansible did not run on %s successfully' %
                                    repository)

                tag = str(uuid.uuid1())

                iid = self.api_client.commit(cid['Id'], repository=repository,
                                             tag=tag)['Id']
                iid = self.get_full_image(iid)
                return iid, repository, tag

    def build_hgmo(self, images=None, verbose=False, use_last=False):
        """Ensure the images for a hg.mozilla.org service are built.

        hg-master runs the ssh service while hg-slave runs hgweb. The mirroring
        and other bits should be the same as in production with the caveat that
        LDAP integration is probably out of scope.
        """
        images = self.ensure_images_built([
            'ldap',
            'pulse',
        ], ansibles={
            'hgmaster': ('docker-hgmaster', 'centos7'),
            'hgweb': ('docker-hgweb', 'centos7'),
        }, existing=images, verbose=verbose, use_last=use_last)

        self.state['last-hgmaster-id'] = images['hgmaster']
        self.state['last-hgweb-id'] = images['hgweb']
        self.state['last-ldap-id'] = images['ldap']
        self.state['last-pulse-id'] = images['pulse']
        self.save_state()

        return images

    def network_config(self, network_name, alias):
        """Obtain a networking config object."""
        return self.api_client.create_networking_config(
            endpoints_config={
                network_name: self.api_client.create_endpoint_config(
                    aliases=[alias],
                )
            }
        )

    def build_all_images(self, verbose=False, use_last=False,
                         hgmo=True, max_workers=None):
        docker_images = set()
        ansible_images = {}

        if hgmo:
            docker_images |= {
                'ldap',
            }
            ansible_images['hgmaster'] = ('docker-hgmaster', 'centos7')
            ansible_images['hgweb'] = ('docker-hgweb', 'centos7')

        images = self.ensure_images_built(docker_images,
                                          ansibles=ansible_images,
                                          verbose=verbose,
                                          use_last=use_last)

        with limited_threadpoolexecutor(3, max_workers) as e:
            if hgmo:
                f_hgmo = e.submit(
                    self.build_hgmo,
                    images=images,
                    verbose=verbose,
                    use_last=use_last)

        hgmo_result = f_hgmo.result() if hgmo else None

        self.prune_images()

        return None, hgmo_result

    def get_full_image(self, image):
        for i in self.api_client.images():
            iid = i['Id']
            if iid.startswith('sha256:'):
                iid = iid[7:]

            if iid[0:12] == image:
                return i['Id']

        return image

    def prune_images(self):
        """Prune images that are old and likely unused."""
        running = set(self.get_full_image(c['Image'])
                      for c in self.api_client.containers())

        ignore_images = set([
            self.state['last-hgrb-id'],
            self.state['last-pulse-id'],
            self.state['last-rbweb-id'],
            self.state['last-hgmaster-id'],
            self.state['last-hgweb-id'],
            self.state['last-ldap-id'],
            self.state['last-vct-id'],
            self.state['last-treestatus-id'],
        ])

        relevant_repos = set([
            'pulse',
            'rbweb',
            'hgmaster',
            'hgrb',
            'hgweb',
            'ldap',
            'vct',
            'treestatus',
        ])

        to_delete = {}

        for i in self.api_client.images():
            iid = i['Id']

            # Don't do anything with images attached to running containers -
            # Docker won't allow it.
            if iid in running:
                continue

            # Don't do anything with our last used images.
            if iid in ignore_images:
                continue

            repotags = i['RepoTags'] or []
            for repotag in repotags:
                repo, tag = repotag.split(':')
                if repo in relevant_repos:
                    to_delete[iid] = repo
                    break

        retained = {}
        for key, image in sorted(self.state['images'].items()):
            if image not in to_delete:
                retained[key] = image

        with futures.ThreadPoolExecutor(8) as e:
            for image, repo in to_delete.items():
                print('Pruning old %s image %s' % (repo, image))
                e.submit(self.api_client.remove_image, image)

        self.state['images'] = retained
        self.save_state()

    def save_state(self):
        with open(self._state_path, 'wb') as fh:
            json.dump(self.state, fh, indent=4, sort_keys=True)

    def all_docker_images(self):
        """Obtain the set of all known Docker image IDs."""
        return {i['Id'] for i in self.api_client.images(all=True)}

    @contextmanager
    def start_container(self, cid, **kwargs):
        """Context manager for starting and stopping a Docker container.

        The container with id ``cid`` will be started when the context manager
        is entered and stopped when the context manager is execited.

        The context manager receives the inspected state of the container,
        immediately after it is started.
        """
        self.api_client.start(cid, **kwargs)
        try:
            state = self.api_client.inspect_container(cid)
            yield state
        finally:
            try:
                self.api_client.stop(cid, timeout=20)
            except DockerAPIError as e:
                # Silently ignore failures if the container doesn't exist, as
                # the container is obviously stopped.
                if e.response.status_code != 404:
                    raise

    @contextmanager
    def create_container(self, image, remove_volumes=False, **kwargs):
        """Context manager for creating a temporary container.

        A container will be created from an image. When the context manager
        exists, the container will be removed.

        This context manager is useful for temporary containers that shouldn't
        outlive the life of the process.
        """
        s = self.api_client.create_container(image, **kwargs)
        try:
            yield s
        finally:
            self.api_client.remove_container(s['Id'], force=True,
                                             v=remove_volumes)

    @contextmanager
    def vct_container(self, image=None, cid=None, verbose=False):
        """Obtain a container with content of v-c-t available inside.

        We employ some hacks to make this as fast as possible. Three run modes
        are possible:

        1) Client passes in a running container (``cid``)
        2) A previously executed container is available to start
        3) We create and start a temporary container.

        The multiple code paths make the logic a bit difficult. But it makes
        code in consumers slightly easier to follow.
        """
        existing_cid = self.state['vct-cid']

        # Force rebuild if a clobber is needed.
        if self.clobber_needed('vct'):
            existing_cid = None
            image = None

        # If we're going to use an existing container, verify it exists.
        if not cid and existing_cid:
            try:
                state = self.api_client.inspect_container(existing_cid)
            except DockerAPIError:
                existing_cid = None
                self.state['vct-cid'] = None

        # Build the image if we're in temporary container mode.
        if not image and not cid and not existing_cid:
            image = self.ensure_built('vct', verbose=verbose)

        start = False

        if cid:
            state = self.api_client.inspect_container(cid)
            if not state['State']['Running']:
                raise RuntimeError(
                    "Container '%s' should have been started by the calling "
                    "function, but is not running" % cid)
        elif existing_cid:
            cid = existing_cid
            start = True
        else:
            host_config = self.api_client.create_host_config(
                port_bindings={873: None})

            cid = self.api_client.create_container(image,
                                                   volumes=['/vct-mount'],
                                                   ports=[873],
                                                   host_config=host_config,
                                                   labels=['vct'])['Id']
            start = True

        try:
            if start:
                self.api_client.start(cid)
                state = self.api_client.inspect_container(cid)
                ports = state['NetworkSettings']['Ports']
                hostname = ports['873/tcp'][0]['HostIp']
                port = ports['873/tcp'][0]['HostPort']
                url = 'rsync://%s:%s/vct-mount/' % (hostname, port)

                get_and_write_vct_node()
                vct_paths = self._get_vct_files()
                with tempfile.NamedTemporaryFile() as fh:
                    for f in sorted(vct_paths.keys()):
                        fh.write('%s\n' % f)
                    fh.write('.vctnode\n')
                    fh.flush()

                    # We don't use -a (implies -tlptgoD) because with some
                    # filesystems used by Docker (namely overlay2), touching
                    # files only to update attributes has a ton of overhead.
                    # We shouldn't care about owner/group, so we don't sync
                    # these.
                    rsync('-rlpt', '--delete-before', '--files-from', fh.name,
                          ROOT, url)

                self.state['last-vct-id'] = image
                self.state['vct-cid'] = cid
                self.save_state()

            yield state
        finally:
            if start:
                self.api_client.stop(cid)

    @contextmanager
    def auto_clean_orphans(self):
        if not self.is_alive():
            yield
            return

        containers = {c['Id'] for c in self.api_client.containers(all=True)}
        images = {i['Id'] for i in self.api_client.images(all=True)}
        networks = {n['Id'] for n in self.api_client.networks()}
        try:
            yield
        finally:
            with futures.ThreadPoolExecutor(8) as e:
                for c in self.api_client.containers(all=True):
                    if c['Id'] not in containers:
                        e.submit(self.api_client.remove_container, c['Id'],
                                 force=True, v=True)

            with futures.ThreadPoolExecutor(8) as e:
                for i in self.api_client.images(all=True):
                    if i['Id'] not in images:
                        e.submit(self.api_client.remove_image, c['Id'])

            with futures.ThreadPoolExecutor(8) as e:
                for n in self.api_client.networks():
                    if n['Id'] not in networks:
                        e.submit(self.api_client.remove_network, n['Id'])

    def execute(self, cid, cmd, stdout=False, stderr=False, stream=False,
                detach=False):
        """Execute a command on a container.

        Returns the output of the command.

        This mimics the old docker.execute() API, which was removed in
        docker-py 1.3.0.
        """
        r = self.api_client.exec_create(cid, cmd, stdout=stdout, stderr=stderr)
        return self.api_client.exec_start(r['Id'], stream=stream, detach=detach)

    def get_file_content(self, cid, path):
        """Get the contents of a file from a container."""
        r, stat = self.api_client.get_archive(cid, path)
        buf = BytesIO()
        for chunk in r:
            buf.write(chunk)
        buf.seek(0)
        t = tarfile.open(mode='r', fileobj=buf)
        fp = t.extractfile(os.path.basename(path))
        return fp.read()

    def get_directory_contents(self, cid, path, tar='/bin/tar'):
        """Obtain the contents of all files in a directory in a container.

        This is done by invoking "tar" inside the container and piping the
        results to us.

        This returns an iterable of ``tarfile.TarInfo``, fileobj 2-tuples.
        """
        data = self.execute(cid, [tar, '-c', '-C', path, '-f', '-', '.'],
                            stdout=True, stderr=False)
        buf = BytesIO(data)
        t = tarfile.open(mode='r', fileobj=buf)
        for member in t:
            f = t.extractfile(member)
            member.name = member.name[2:]
            yield member, f

    def get_code_coverage(self, cid, filemap=None):
        """Obtain code coverage data from a container.

        Containers can be programmed to collect code coverage from executed
        programs automatically. Our convention is to place coverage files in
        ``/coverage``.

        This method will fetch coverage files and parse them into data
        structures, which it will emit.

        If a ``filemap`` dict is passed, it will be used to map filenames
        inside the container to local filesystem paths. When present,
        files not inside the map will be ignored.
        """
        filemap = filemap or {}

        for member, fh in self.get_directory_contents(cid, '/coverage'):
            if not member.name.startswith('coverage.'):
                continue

            data = pickle.load(fh)

            c = CoverageData(basename=member.name,
                             collector=data.get('collector'))

            lines = {}
            for f, linenos in data.get('lines', {}).items():
                newname = filemap.get(f)
                if not newname:
                    # Ignore entries missing from map.
                    if filemap:
                        continue

                    newname = f

                lines[newname] = dict.fromkeys(linenos, None)

            arcs = {}
            for f, arcpairs in data.get('arcs', {}).items():
                newname = filemap.get(f)
                if not newname:
                    if filemap:
                        continue

                    newname = f

                arcs[newname] = dict.fromkeys(arcpairs, None)

            if not lines and not arcs:
                continue

            c.lines = lines
            c.arcs = arcs

            yield c

    def _get_host_hostname_port(self, state, port):
        """Resolve the host hostname and port number for an exposed port."""
        host_port = state['NetworkSettings']['Ports'][port][0]
        host_ip = host_port['HostIp']
        host_port = int(host_port['HostPort'])

        if host_ip != '0.0.0.0':
            return host_ip, host_port

        if self.docker_hostname not in ('localhost', '127.0.0.1'):
            return self.docker_hostname, host_port

        for network in state['NetworkSettings']['Networks'].values():
            if network['Gateway']:
                return network['Gateway'], host_port

        # This works when Docker is running locally, which is common. But it
        # is far from robust.
        gateway = state['NetworkSettings']['Gateway']
        return gateway, host_port

    def _get_assert_container_running_fn(self, cid):
        """Obtain a function that raises during invocation if a container
        stops."""
        def assert_running():
            try:
                info = self.api_client.inspect_container(cid)
            except DockerAPIError as e:
                if e.response.status_code == 404:
                    raise Exception('Container does not exist '
                                    '(stopped running?): %s' % cid)

                raise

            if not info['State']['Running']:
                raise Exception('Container stopped running: %s' % cid)

        return assert_running

    def _get_sorted_images(self):
        return sorted(self.api_client.images(), key=lambda x: x['Created'],
                      reverse=True)
