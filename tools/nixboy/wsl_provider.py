#!/usr/bin/env python3
"""Foreground WSL provider owned by one hidden Windows wsl.exe client.

The Windows launcher keeps this process in the foreground for the bridge's
lifetime. No global WSL setting or unrelated terminal is required. A private
record with Linux process birth identity makes stop/status narrowly scoped.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time


def birth(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def owned(record):
    value = json.loads(record.read_text())
    pid = value.get('provider_pid')
    if value.get('format') != 'nixboy-wsl-provider-v1' or type(pid) is not int or pid <= 1:
        raise ValueError('Invalid provider ownership record')
    if birth(pid) != value.get('provider_birth'):
        return None
    command = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
    if os.fsencode(str(Path(__file__).resolve())) not in command:
        raise ValueError('Provider identity does not match this launcher')
    return value


def run(record, log, arguments):
    record.parent.mkdir(parents=True, exist_ok=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    with record.with_suffix(record.suffix+'.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise RuntimeError('This provider record is already in use') from e
        stopped = threading.Event()
        def stop(signum, frame):
            stopped.set()
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        bridge = Path(__file__).with_name('bridge.py')
        with log.open('ab', buffering=0) as output:
            child = subprocess.Popen([sys.executable, str(bridge), *arguments],
                                     stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                     start_new_session=True)
            value = {'format': 'nixboy-wsl-provider-v1', 'provider_pid': os.getpid(),
                     'provider_birth': birth(os.getpid()), 'bridge_pid': child.pid,
                     'bridge_birth': birth(child.pid), 'started_unix': time.time(),
                     'bridge': str(bridge), 'log': str(log)}
            temporary = record.with_suffix(record.suffix+'.tmp')
            temporary.write_text(json.dumps(value, indent=2)+'\n')
            temporary.chmod(0o600)
            temporary.replace(record)
            try:
                while child.poll() is None and not stopped.wait(.25):
                    pass
                if child.poll() is None:
                    # Bridge catches KeyboardInterrupt and closes the manager,
                    # including any cancellable guest preparation in flight.
                    child.send_signal(signal.SIGINT)
                    try:
                        child.wait(timeout=115)
                    except subprocess.TimeoutExpired:
                        output.write(b'Provider: graceful bridge cleanup exceeded 115 seconds; startup reconciliation is required.\n')
                        child.terminate()
                        try:
                            child.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            child.kill()
                            child.wait(timeout=5)
                return child.returncode
            finally:
                if record.exists() and json.loads(record.read_text()).get('provider_pid') == os.getpid():
                    record.unlink()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('operation', choices=('run', 'status', 'stop'))
    p.add_argument('--record', type=Path, required=True)
    p.add_argument('--log', type=Path)
    args, arguments = p.parse_known_args()
    record = args.record.resolve()
    if args.operation == 'run':
        if not args.log:
            p.error('--log is required for run')
        return run(record, args.log.resolve(), arguments[1:] if arguments[:1] == ['--'] else arguments)
    if not record.exists():
        print(json.dumps({'running': False, 'reason': 'no provider record'}))
        return 0
    value = owned(record)
    if args.operation == 'status':
        print(json.dumps({'running': value is not None, 'provider': value}))
        return 0
    if value is None:
        print(json.dumps({'stopped': True, 'reason': 'stale provider record; no process signaled'}))
        return 0
    os.kill(value['provider_pid'], signal.SIGTERM)
    deadline = time.monotonic()+135
    while birth(value['provider_pid']) == value['provider_birth']:
        if time.monotonic() >= deadline:
            raise TimeoutError('Provider did not exit; inspect its log before further action')
        time.sleep(.1)
    print(json.dumps({'stopped': True}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
