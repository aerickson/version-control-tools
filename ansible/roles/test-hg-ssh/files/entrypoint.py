#!/usr/bin/python -u
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import os
import subprocess
import sys

os.environ['DOCKER_ENTRYPOINT'] = '1'

subprocess.check_call([
    '/usr/bin/python', '-u',
    '/usr/bin/ansible-playbook', 'test-hgmaster.yml', '-c', 'local',
    '-t', 'docker-startup'],
    cwd='/vct/ansible')

del os.environ['DOCKER_ENTRYPOINT']

# Generate host SSH keys for hg.
if not os.path.exists('/etc/mercurial/ssh/ssh_host_ed25519_key'):
    subprocess.check_call(['/usr/bin/ssh-keygen', '-t', 'ed25519',
                           '-f', '/etc/mercurial/ssh/ssh_host_ed25519_key', '-N', ''])

if not os.path.exists('/etc/mercurial/ssh/ssh_host_rsa_key'):
    subprocess.check_call(['/usr/bin/ssh-keygen', '-t', 'rsa', '-b', '4096',
                           '-f', '/etc/mercurial/ssh/ssh_host_rsa_key', '-N', ''])

subprocess.check_call(['/entrypoint-kafkabroker'])

kafka_state = open('/kafka-servers', 'rb').read().splitlines()

# Update the Kafka connect servers in the vcsreplicator config.
monitor_groups = kafka_state[2].split(',')
kafka_servers = kafka_state[3:]
kafka_servers = ['%s:9092' % s.split(':')[0] for s in kafka_servers]

hgrc_lines = open('/etc/mercurial/hgrc', 'rb').readlines()
with open('/etc/mercurial/hgrc', 'wb') as fh:
    for line in hgrc_lines:
        # This isn't the most robust ini parsing logic in the world, but it
        # gets the job done.
        if line.startswith('hosts = '):
            line = 'hosts = %s\n' % ', '.join(kafka_servers)

        fh.write(line)

pushdataaggregator_lines = open('/etc/mercurial/pushdataaggregator.ini', 'rb').readlines()
with open('/etc/mercurial/pushdataaggregator.ini', 'wb') as fh:
    for line in pushdataaggregator_lines:
        if line.startswith('hosts ='):
            line = 'hosts = %s\n' % ', '.join(kafka_servers)

        fh.write(line)

with open('/repo/hg/pushdataaggregator_groups', 'wb') as fh:
    fh.write('\n'.join(monitor_groups))

# Update the notification daemon settings.
notification_lines = open('/etc/mercurial/notifications.ini', 'rb').readlines()
with open('/etc/mercurial/notifications.ini', 'wb') as fh:
    section = None
    for line in notification_lines:
        if line.startswith('['):
            section = line.strip()[1:-1]

        if section == 'pulseconsumer':
            if line.startswith('hosts ='):
                line = 'hosts = %s\n' % ', '.join(kafka_servers)

        fh.write(line)

os.execl(sys.argv[1], *sys.argv[1:])
