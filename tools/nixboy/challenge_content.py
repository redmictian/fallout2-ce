"""Trusted, versioned training content. YAML is data, never executable code."""

from dataclasses import dataclass
from pathlib import Path
import random
import re

import yaml

CONTENT_ROOT = Path(__file__).resolve().parent / "content"
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


@dataclass(frozen=True)
class Challenge:
    id: str
    skill: int
    level: int
    title: str
    lesson: str
    instructions: str
    kind: str
    parameters: dict
    version: int = 1


class Catalog:
    def __init__(self, root=None):
        self.root = Path(root or CONTENT_ROOT).resolve()
        raw = yaml.safe_load((self.root / "tracks.yaml").read_text(encoding="utf-8"))
        self.tracks = {}
        for track in raw["tracks"]:
            skill = track["skill"]
            if type(skill) is not int or skill not in (3, 12) or skill in self.tracks:
                raise ValueError("unsupported or duplicate training skill")
            self.tracks[skill] = {"id": track["id"], "title": track["title"], "levels": [1, 2]}
        self.challenges = {}
        for path in sorted((self.root / "challenges").glob("*/*/*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            required = {"id", "version", "skill", "level", "title", "lesson", "instructions", "kind", "parameters"}
            if not isinstance(data, dict) or set(data) != required:
                raise ValueError(f"invalid challenge fields: {path}")
            if not IDENTIFIER.fullmatch(data["id"]) or data["id"] in self.challenges:
                raise ValueError("invalid or duplicate challenge id")
            if any(type(data[key]) is not int for key in ("skill", "level", "version")) or data["skill"] not in self.tracks or data["level"] not in (1, 2) or data["version"] != 1:
                raise ValueError("invalid challenge track, level or version")
            for key in ("title", "lesson", "instructions", "kind"):
                if not isinstance(data[key], str) or not data[key] or len(data[key]) > 4096:
                    raise ValueError(f"invalid challenge {key}")
            if not IDENTIFIER.fullmatch(data["kind"]) or not isinstance(data["parameters"], dict):
                raise ValueError("invalid challenge parameters")
            for key, choices in data["parameters"].items():
                if not IDENTIFIER.fullmatch(key) or not isinstance(choices, list) or not 1 <= len(choices) <= 32:
                    raise ValueError("parameters must be bounded choice lists")
                if any(type(value) not in (str, int) or isinstance(value, str) and (len(value) > 1000 or "\0" in value) for value in choices):
                    raise ValueError("invalid parameter value")
            spec = Challenge(**data)
            self.challenges[spec.id] = spec
        if not self.challenges:
            raise ValueError("empty challenge catalog")

    def pool(self, skill, level):
        return [item.id for item in self.challenges.values() if item.skill == skill and item.level == level]

    def get(self, identifier):
        try:
            return self.challenges[identifier]
        except (KeyError, TypeError) as exc:
            raise ValueError("unknown challenge id") from exc

    def instantiate(self, identifier, seed):
        spec = self.get(identifier)
        if type(seed) is not int or not 0 <= seed <= 2**63 - 1:
            raise ValueError("seed must be a nonnegative signed 64-bit integer")
        rng = random.Random(seed)
        return {key: rng.choice(values) for key, values in sorted(spec.parameters.items())}
