"""Replay historical display bytes without eliciting stale terminal replies."""
import re

REPORT_QUERY = re.compile(rb"\x1b\[[0-9;?><=]*[cn]")


def replay_bytes(history):
    # DSR/DA queries were issued to the earlier terminal/getty. Answering them
    # again at a new Bash prompt injects CPR/device replies as learner input.
    # Live serial output bypasses this filter; raw external logs stay intact.
    return REPORT_QUERY.sub(b"", history)
