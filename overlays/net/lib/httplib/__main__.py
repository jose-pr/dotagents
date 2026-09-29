"""``python -m httplib [curl options] URL``: the curl-compatible fallback
(``httplib.cli``), over the standard library and duho."""
import sys

from httplib.cli import main

sys.exit(main())
