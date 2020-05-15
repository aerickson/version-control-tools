# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from __future__ import absolute_import, unicode_literals

import os
import socket
import string
import subprocess
import time

import concurrent.futures as futures
import requests


HERE = os.path.abspath(os.path.dirname(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..'))
HGCLUSTER_DOCKER_COMPOSE = os.path.join(ROOT, 'testing', 'hgcluster-docker-compose.yml')


def get_available_port():
    """Obtain a port number available for binding."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('', 0))
    host, port = s.getsockname()
    s.close()

    return port


def wait_for_http(host, port, path='', timeout=240, extra_check_fn=None):
    """Wait for an HTTP server to respond.

    If extra_check_fn is defined, it will be called as an extra check to see if
    we should still poll. If we should not (e.g. the underlying container
    stopped running), that function should raise an exception.
    """

    start = time.time()

    while True:
        try:
            res = requests.get('http://%s:%s/%s' % (host, port, path), timeout=1)
            if res.status_code == 200:
                return
        except requests.exceptions.RequestException:
            pass

        if extra_check_fn:
            extra_check_fn()

        if time.time() - start > timeout:
            raise Exception('Timeout reached waiting for HTTP')

        time.sleep(0.1)


def wait_for_amqp(hostname, port, userid, password, ssl=False, timeout=60,
                  extra_check_fn=None):
    # Delay import to facilitate module use in limited virtualenvs.
    import kombu
    c = kombu.Connection(hostname=hostname, port=port, userid=userid,
            password=password, ssl=ssl)

    start = time.time()

    while True:
        try:
            c.connection
            return
        except Exception:
            pass

        if extra_check_fn:
            extra_check_fn()

        if time.time() - start > timeout:
            raise Exception('Timeout reached waiting for AMQP')

        time.sleep(0.1)


def wait_for_ssh(hostname, port, timeout=60, extra_check_fn=None):
    """Wait for an SSH server to start on the specified host and port."""
    # Delay import to facilitate module use in limited virtualenvs.
    import paramiko

    class IgnoreHostKeyPolicy(paramiko.MissingHostKeyPolicy):
        def missing_host_key(self, client, hostname, key):
            return

    start = time.time()

    while True:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(IgnoreHostKeyPolicy())
        try:
            client.connect(hostname, port=port, timeout=0.1, allow_agent=False,
                           look_for_keys=False)
            client.close()
            return
        except socket.error:
            pass
        except paramiko.SSHException:
            # This is probably wrong. We should ideally attempt authentication
            # and wait for an explicit auth failed instead of a generic
            # error.
            return

        if extra_check_fn:
            extra_check_fn()

        if time.time() - start > timeout:
            raise Exception('Timeout reached waiting for SSH')

        time.sleep(0.1)


def wait_for_kafka(hostport, timeout=60):
    """Wait for Kafka to start responding on the specified host:port string."""
    # Delay import to facilitate module use in limited virtualenvs.
    from kafka import SimpleClient

    start = time.time()
    while True:
        try:
            SimpleClient(hostport, client_id=b'dummy', timeout=1)
            return
        except Exception:
            pass

        if time.time() - start > timeout:
            raise Exception('Timeout reached waiting for Kafka')

        time.sleep(0.1)


def wait_for_kafka_topic(hostport, topic, timeout=60):
    """Wait for a Kafka topic to become available."""
    # Delay import to facilitate module use in limited virtualenvs.
    from kafka import SimpleClient, TopicPartition

    start = time.time()
    client = SimpleClient(hostport, client_id=b'dummy', timeout=1)
    while not client.has_metadata_for_topic(topic):
        if time.time() - start > timeout:
            raise Exception('timeout reached waiting for topic')

        time.sleep(0.1)
        client.load_metadata_for_topics()

    # And wait for all partitions in that topic to have a leader.
    while True:
        tps = [TopicPartition(topic, p)
               for p in client.topic_partitions.get(topic, [])]

        if tps and all(client.topics_to_brokers.get(tp) for tp in tps):
            break

        if time.time() - start > timeout:
            raise Exception('timeout reached waiting for topic brokers')

        time.sleep(0.1)
        client.load_metadata_for_topics()


def limited_threadpoolexecutor(wanted_workers, max_workers=None):
    """Return a ThreadPoolExecutor with up to ``max_workers`` executors.

    Call with ``wanted_workers`` equal to None to ask for the default number
    of workers, which is the number of processors on the machine multiplied
    by 5.

    Call with ``max_workers`` less than 1 or ``max_workers=None`` to specify
    no limit on worker threads.

    See https://docs.python.org/3/library/concurrent.futures.html#threadpoolexecutor
    """
    # Are we trying to ask for default workers, which is the "number of
    # processors on the machine, multiplied by 5"?
    # See https://docs.python.org/3/library/concurrent.futures.html#threadpoolexecutor
    wants_unlimited = (wanted_workers is None) or (wanted_workers < 1)

    max_unlimited = (max_workers is None) or (max_workers < 1)

    if max_unlimited:
        workers = wanted_workers
    elif wants_unlimited:
        workers = max_workers
    else:
        workers = min(wanted_workers, max_workers)

    return futures.ThreadPoolExecutor(workers)


def docker_compose_down_background(project_name, show_output=False):
    '''Run `docker-compose down` for the given project name.

    Returns the `subprocess.Popen` object for use by the caller.
    '''
    docker_compose_down_command = [
        'docker-compose',
        '--file', HGCLUSTER_DOCKER_COMPOSE,
        '--project-name', project_name,
        'down',
    ]

    kwargs = {}
    if not show_output:
        # TRACKING py3 - once we have full Py3 support in the test environment
        # we can make use of `subprocess.DEVNULL`
        devnull = open(os.devnull, 'wb')
        kwargs['stderr'] = devnull
        kwargs['stdout'] = devnull
    return subprocess.Popen(docker_compose_down_command, **kwargs)


def normalize_testname(testname):
    '''Normalize test name for use with `docker-compose`.

    `docker-compose` normalizes project names by removing whitespace and other
    punctuation.

    >>> normalize_testname('test-push-basic.t')
    'testpushbasict'
    >>> normalize_testname('this is a test')
    'thisisatest'
    >>> normalize_testname('lolol!!!!!! haha')
    'lololhaha'
    >>> normalize_testname('hello/there/testname')
    testname
    >>> normalize_testname(None)
    None
    '''
    if not testname:
        return None

    # TODO names like "/" will break this
    testname = testname.split('/')[-1].lower()

    return ''.join(
        char for char in testname
        if char not in (set(string.punctuation) | set(string.whitespace))
    )
