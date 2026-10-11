"""Stand-in for a package-manager program. Each fake program in the test PATH runs:

    python3 dispatch.py <tool> <args...>

It prints the canned output for that command line (see responses.py) and logs the call,
so tests can check exactly what URPM ran.
"""

import fnmatch
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fakes.responses import RESPONSES  # noqa: E402

tool, args = sys.argv[1], sys.argv[2:]
joined = " ".join(args)
with open(os.environ["URPM_FAKE_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps([tool, *args]) + "\n")
for name, pattern, output, code in RESPONSES:
    if name == tool and fnmatch.fnmatchcase(joined, pattern):
        sys.stdout.write(output.replace("{root}", os.environ["URPM_FAKE_ROOT"]))
        sys.exit(code)
sys.stderr.write(f"fake {tool}: no canned response for {joined!r}\n")
sys.exit(97)
