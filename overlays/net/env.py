"""The net overlay's environment: httplib and curlike read their settings
under the prefix ``HTTPLIB_PREFIX`` names, and the agents' settings are
``AGENTS_*`` (``AGENTS_PROXY``, ``AGENTS_PROXY_AUTH``, ``AGENTS_HOME``,
``AGENTS_HOOKS_<KEY>``, ``AGENTS_CURL_COMPAT``, ...). A prefix the caller
already chose is left alone."""

import json
import os

if not (os.environ.get("HTTPLIB_PREFIX") or "").strip():
    print(json.dumps({"HTTPLIB_PREFIX": "AGENTS"}))
