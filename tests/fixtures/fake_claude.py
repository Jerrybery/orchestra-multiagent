#!/usr/bin/env python3
"""
Drop-in replacement for `claude` CLI in tests.
Reads a script from FAKE_CLAUDE_SCRIPT env var (a JSONL file with stream-json events).
Each line is emitted to stdout in order, with optional sleeps via {"_sleep": 0.5}.
Final exit code: from FAKE_CLAUDE_EXIT (default 0).
If `--resume <sid>` arg present and FAKE_CLAUDE_RESUME_ID env != sid, exit 1 with stderr.
"""
import json
import os
import sys
import time


def main() -> int:
    args = sys.argv[1:]
    # Detect --resume
    resume_id = None
    for i, a in enumerate(args):
        if a == "--resume" and i + 1 < len(args):
            resume_id = args[i + 1]
    expected_resume = os.environ.get("FAKE_CLAUDE_RESUME_ID")
    if resume_id and expected_resume and resume_id != expected_resume:
        sys.stderr.write(f"Session {resume_id} not found\n")
        return 1

    script_path = os.environ.get("FAKE_CLAUDE_SCRIPT")
    if script_path and os.path.exists(script_path):
        with open(script_path) as f:
            for line in f:
                event = json.loads(line)
                if "_sleep" in event:
                    time.sleep(event["_sleep"])
                    continue
                print(json.dumps(event), flush=True)

    return int(os.environ.get("FAKE_CLAUDE_EXIT", "0"))


if __name__ == "__main__":
    sys.exit(main())
