"""The net overlay's environment: syshttp and curlike read every setting as
``<PREFIX><NAME>`` for each prefix of ``SYSHTTP_PREFIX``, and the agents'
prefix is ``AGENTS_`` alone (``AGENTS_SYSHTTP_PROXY``, ``AGENTS_HTTPS_PROXY``,
``AGENTS_SYSHTTP_HOOKS_<KEY>``, ``AGENTS_CURL``, ...): the machine's
unprefixed variables are not the agents'. A prefix the caller already chose
is left alone."""

import json
import os

if not (os.environ.get("SYSHTTP_PREFIX") or "").strip():
    print(json.dumps({"SYSHTTP_PREFIX": "AGENTS_"}))
