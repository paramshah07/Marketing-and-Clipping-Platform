"""Worker entrypoint: fail fast unless ffmpeg and ffprobe run, then exec the Procrastinate worker
(same PID, so SIGTERM from compose reaches it). Extra args go to `procrastinate worker`."""

import os
import subprocess
import sys

for tool in ("ffmpeg", "ffprobe"):
    version = subprocess.run([tool, "-version"], capture_output=True, text=True, check=True, timeout=30).stdout
    print(f"startup check ok: {version.splitlines()[0]}", flush=True)

os.execvp("procrastinate", ["procrastinate", "worker", *sys.argv[1:]])
