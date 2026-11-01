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
from protocol import (
    TRAIN_OPEN, TRAIN_ACK, TRAIN_REWARD, TRAIN_PERK_ACK, TRAIN_PERK_REWARD, CONTEXT,
    decode_training_open, training_context, decode_context, decode_training_ack,
    decode_training_perk_reward, decode_training_perk_ack, frame, read_frame,
)


def observed_context(context):
    """Only explicit noncredential acceptance fields are retained."""
    return {"training_version": context.get("training.version"),
            "toughness": context.get("perk.toughness"),
            "toughness_max": context.get("perk.toughness.max"),
            "perk_training_enabled": context.get("perk.training.enabled"),
            "damage_resistance": context.get("stat.damage_resistance")}


class ReceiptRelay:
    """Forward exact received frames; only repeat or suppress nominated frames."""
    def __init__(self, log, reward_kind="skill", drop_acks=False, copies=2):
        if reward_kind not in ("skill", "perk") or not 1 <= copies <= 4 or (drop_acks and reward_kind != "perk"):
            raise ValueError("Invalid bounded acceptance relay options")
        self.log, self.reward_kind, self.drop_acks, self.copies = log, reward_kind, drop_acks, copies
        self.duplicated = False
        self.lock = threading.Lock()

    def transfer(self, kind, payload, to_game):
        copies, drop = 1, False
        nominated = TRAIN_REWARD if self.reward_kind == "skill" else TRAIN_PERK_REWARD
        if to_game and kind == nominated:
            with self.lock:
                if not self.duplicated:
                    self.duplicated = True
                    copies = self.copies
            if kind == TRAIN_PERK_REWARD:
                profile, perk, rank, receipt = decode_training_perk_reward(payload)
                self.log({"kind": "earned_reward_delivery", "reward_kind": "perk", "copies": copies,
                          "profile": profile, "perk": perk, "target_rank": rank, "receipt": receipt})
            else:
                skill, level, points, receipt, _ = decode_training_ack(payload + b"\x01")
                self.log({"kind": "earned_reward_delivery", "reward_kind": "skill", "copies": copies,
                          "skill": skill, "level": level, "points": points, "receipt": receipt})
        elif not to_game and kind == TRAIN_PERK_ACK:
            profile, perk, rank, receipt, accepted = decode_training_perk_ack(payload)
            drop = self.drop_acks and self.reward_kind == "perk"
            self.log({"kind": "game_ack", "reward_kind": "perk", "profile": profile,
                      "perk": perk, "target_rank": rank, "receipt": receipt,
                      "accepted": int(accepted), "dropped": drop})
        elif not to_game and kind == TRAIN_ACK:
            skill, level, points, receipt, accepted = decode_training_ack(payload)
            self.log({"kind": "game_ack", "reward_kind": "skill", "accepted": int(accepted),
                      "skill": skill, "level": level, "points": points, "receipt": receipt, "dropped": False})
        elif not to_game and kind == TRAIN_OPEN:
            _, _, context, claims, perks = decode_training_open(payload, include_perks=True)
            identity, skills = training_context(context)
            self.log({"kind": "game_open", "profile": identity, "science": skills[12],
                      "claims": claims, "perk_claims": perks, **observed_context(context)})
        elif not to_game and kind == CONTEXT:
            context = decode_context(payload)
            identity, skills = training_context(context)
            self.log({"kind": "game_context", "profile": identity, "science": skills[12],
                      **observed_context(context)})
        # AUTH/other traffic is forwarded without decoding or logging payloads.
        return b"" if drop else frame(kind, payload) * copies

    def forward(self, source, target, to_game):
        while True:
            kind, payload = read_frame(source)
            forwarded = self.transfer(kind, payload, to_game)
            if forwarded:
                target.sendall(forwarded)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', action='store_true')
    p.add_argument('--listen', type=int, default=47833)
    p.add_argument('--upstream', type=int, default=47831)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--reward-kind', choices=('skill', 'perk'), default='skill')
    p.add_argument('--drop-acks', action='store_true', help='Drop only real perk ACKs in perk mode; log their arrival')
    p.add_argument('--copies', type=int, default=2, help='Copies of first received nominated genuine reward, 1..4')
    a = p.parse_args()
    if not a.run or a.listen == a.upstream or not all(1024 <= n <= 65535 for n in (a.listen, a.upstream)):
        p.error('Explicit --run and distinct local ports 1024..65535 required')
    if not 1 <= a.copies <= 4 or (a.drop_acks and a.reward_kind != 'perk'):
        p.error('--copies must be1..4; --drop-acks requires --reward-kind perk')
    a.output.mkdir(parents=True, exist_ok=True)
    stop = a.output/'stop'
    if stop.exists():
        p.error('Use a new output directory')
    lock = threading.Lock()
    def log(value):
        with lock, (a.output/'events.jsonl').open('a') as output:
            output.write(json.dumps(dict(unix=time.time(), **value))+'\n')
    relay = ReceiptRelay(log, a.reward_kind, a.drop_acks, a.copies)
    def handle(game):
        upstream = socket.create_connection(('127.0.0.1', a.upstream), timeout=5)
        upstream.settimeout(None)
        def forward(source, target, to_game):
            try:
                relay.forward(source, target, to_game)
            except (OSError, EOFError, ValueError):
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
        log({'kind': 'ready', 'listen': a.listen, 'upstream': a.upstream,
             'reward_kind': a.reward_kind, 'drop_acks': a.drop_acks, 'copies': a.copies})
        while not stop.exists():
            try: game, _ = server.accept()
            except socket.timeout: continue
            threading.Thread(target=handle, args=(game,), daemon=True).start()


if __name__ == '__main__':
    main()
