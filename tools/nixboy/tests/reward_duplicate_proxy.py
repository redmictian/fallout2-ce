"""Opt-in loopback acceptance relay: duplicate one genuine earned reward frame.

Does not manufacture a reward or log authentication. Use a disposable game and
private output; the real bridge remains the completion authority. Actual game
skill/save observations must establish idempotency independently of these logs.
"""
import argparse
import json
from pathlib import Path
import socket
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from protocol import TRAIN_OPEN, TRAIN_ACK, TRAIN_REWARD, decode_training_open, training_context, frame, read_frame


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', action='store_true')
    p.add_argument('--listen', type=int, default=47833)
    p.add_argument('--upstream', type=int, default=47831)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if not a.run or a.listen == a.upstream or not all(1024 <= n <= 65535 for n in (a.listen, a.upstream)):
        p.error('Explicit --run and distinct local ports 1024..65535 required')
    a.output.mkdir(parents=True, exist_ok=True)
    stop = a.output/'stop'
    if stop.exists():
        p.error('Use a new output directory')
    lock = threading.Lock()
    duplicated = False
    def log(value):
        with lock, (a.output/'events.jsonl').open('a') as output:
            output.write(json.dumps(dict(unix=time.time(), **value))+'\n')
    def handle(game):
        upstream = socket.create_connection(('127.0.0.1', a.upstream), timeout=5)
        upstream.settimeout(None)
        def forward(source, target, to_game):
            nonlocal duplicated
            try:
                while True:
                    kind, payload = read_frame(source)
                    copies = 1
                    if to_game and kind == TRAIN_REWARD:
                        with lock:
                            if not duplicated:
                                duplicated = True
                                copies = 2
                        log({'kind': 'earned_reward_delivery', 'copies': copies,
                             'skill': payload[0], 'receipt': payload[5:].hex()})
                    elif not to_game and kind == TRAIN_ACK:
                        log({'kind': 'game_ack', 'accepted': payload[-1], 'receipt': payload[5:21].hex()})
                    elif not to_game and kind == TRAIN_OPEN:
                        _, _, context, claims = decode_training_open(payload)
                        identity, skills = training_context(context)
                        log({'kind': 'game_open', 'profile': identity, 'science': skills[12], 'claims': claims})
                    target.sendall(frame(kind, payload)*copies)
            except (OSError, EOFError):
                pass
            finally:
                for s in (game, upstream):
                    try: s.shutdown(socket.SHUT_RDWR)
                    except OSError: pass
        worker = threading.Thread(target=forward, args=(game, upstream, False), daemon=True)
        worker.start()
        forward(upstream, game, True)
        worker.join(5)
        upstream.close()
        game.close()
    with socket.socket() as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(('127.0.0.1', a.listen))
        server.listen(2)
        server.settimeout(.5)
        log({'kind': 'ready', 'listen': a.listen, 'upstream': a.upstream})
        while not stop.exists():
            try: game, _ = server.accept()
            except socket.timeout: continue
            threading.Thread(target=handle, args=(game,), daemon=True).start()


if __name__ == '__main__':
    main()
