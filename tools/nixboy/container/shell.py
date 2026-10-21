#!/usr/bin/env python3
"""Set the Docker PTY dimensions before Bash/Readline reads them."""

import os
import struct
import sys


def launch(rows, cols):
    if type(rows) is not int or type(cols) is not int or not (1 <= rows <= 200 and 1 <= cols <= 300):
        raise ValueError("terminal dimensions out of range")
    # Linux-only imports stay here so the dimension checks remain portable.
    import fcntl
    import termios

    fcntl.ioctl(sys.stdin.fileno(), termios.TIOCSWINSZ,
                struct.pack("HHHH", rows, cols, 0, 0))
    os.execv("/bin/bash", ["/bin/bash", "--noprofile", "--norc", "-i"])


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: shell.py ROWS COLS")
    launch(int(sys.argv[1]), int(sys.argv[2]))
