#!/usr/bin/env python
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

import os

OUR_DIR = os.path.normpath(os.path.dirname(__file__))
with open(os.path.join(OUR_DIR, '..', '..', '..', 'bootstrap.py')) as f:
    exec(f.read())

application = make_application(OUR_DIR)
