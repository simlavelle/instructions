#!/bin/sh
# Start Label Verification on macOS or Linux:  ./start.sh
# The first run installs what the app needs; later runs start in seconds.
cd "$(dirname "$0")" || exit 1

# Prefer well-supported Python versions, then whatever "python3" is.
for py in python3.13 python3.12 python3.11 python3 python3.10 python; do
  if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    exec "$py" run.py "$@"
  fi
done

echo "Python 3.10 or newer is needed. Download it from https://www.python.org/downloads/" >&2
echo "Then run ./start.sh again." >&2
exit 1
