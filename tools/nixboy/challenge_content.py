"""Trusted external challenge catalog. YAML defines data, never executable code."""

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import random
import re
from string import Formatter

import yaml

from protocol import MAX_LEVEL, SKILL_NAMES

CONTENT_ROOT = Path(__file__).resolve().parent / "content"
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
KINDS = {"make-directory", "copy-file", "move-file", "rename-directory", "remove-empty",
         "filter-lines", "sort-unique", "extract-field", "count-matches", "replace-text",
         "ssh-upload", "ssh-download", "ssh-remote-move", "ssh-key-login", "ssh-alias",
         "private-file", "public-read", "executable", "shared-directory", "group-readable"}


class ContentLoader(yaml.SafeLoader):
    """Reject ambiguous duplicate keys while retaining ordinary YAML aliases."""


def _mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            if key in result:
                raise ValueError(f"duplicate YAML key: {key}")
            result[key] = loader.construct_object(value_node, deep=deep)
        except TypeError as exc:
            raise ValueError("invalid YAML mapping key") from exc
    return result


ContentLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


@dataclass(frozen=True)
class Challenge:
    id: str
    skill: int
    level: int
    title: str
    instructions: str
    kind: str
    parameters: dict
    version: int = 1


def _text(value, maximum=4096):
    return isinstance(value, str) and 0 < len(value) <= maximum and "\0" not in value


class Catalog:
    def __init__(self, root=None):
        self.root = Path(root if root is not None else CONTENT_ROOT).resolve()
        raw = self._read(self.root / "tracks.yaml")
        if not isinstance(raw, dict) or set(raw) != {"schema", "tracks"} or raw["schema"] != 2 \
                or not isinstance(raw["tracks"], list) or not 1 <= len(raw["tracks"]) <= len(SKILL_NAMES):
            raise ValueError("catalog needs schema 2 and between one and 18 tracks")
        self.tracks = {}
        identifiers = set()
        for track in raw["tracks"]:
            if not isinstance(track, dict) or set(track) != {"id", "title", "knowledge_title", "skill", "levels"}:
                raise ValueError("invalid training track fields")
            skill = track["skill"]
            if type(skill) is not int or not 0 <= skill < len(SKILL_NAMES) or skill in self.tracks:
                raise ValueError("unsupported or duplicate training skill")
            if not isinstance(track["id"], str) or not IDENTIFIER.fullmatch(track["id"]) \
                    or track["id"] in identifiers or not _text(track["title"], 200) \
                    or not _text(track["knowledge_title"], 200):
                raise ValueError("invalid or duplicate track identity/title")
            levels = track["levels"]
            if not isinstance(levels, dict) or not 1 <= len(levels) <= 2048:
                raise ValueError("training track needs authored skill bands")
            ranges = []
            for level, band in levels.items():
                if type(level) is not int or not 1 <= level <= MAX_LEVEL:
                    raise ValueError("level ID must be in 1..65535")
                if not isinstance(band, dict) or not {"title", "min_skill", "max_skill"} <= set(band) \
                        or set(band) - {"title", "min_skill", "max_skill", "reward_points"} \
                        or not _text(band["title"], 200) \
                        or any(type(band[key]) is not int for key in ("min_skill", "max_skill")) \
                        or not 0 <= band["min_skill"] <= band["max_skill"] <= 300:
                    raise ValueError("invalid training skill band")
                reward = band.get("reward_points", 5)
                if type(reward) is not int or not 1 <= reward <= 300:
                    raise ValueError("reward_points must be an integer in 1..300")
                levels[level] = dict(band, reward_points=reward)
                ranges.append((band["min_skill"], band["max_skill"]))
            previous_max = -1
            for minimum, maximum in sorted(ranges):
                if minimum <= previous_max:
                    raise ValueError("overlapping training skill bands")
                previous_max = maximum
            identifiers.add(track["id"])
            self.tracks[skill] = {key: value for key, value in track.items() if key != "skill"}
        if sum(len(track["levels"]) for track in self.tracks.values()) > 2048:
            raise ValueError("catalog exceeds 2048 authored levels")
        self.challenges = {}
        self._pools = {}
        paths = sorted((self.root / "challenges").rglob("*.yaml"))
        if not 1 <= len(paths) <= 8192:
            raise ValueError("catalog needs between one and 8192 challenge definitions")
        for path in paths:
            data = self._read(path)
            required = {"id", "version", "skill", "level", "title", "instructions", "kind", "parameters"}
            if not isinstance(data, dict) or set(data) != required:
                raise ValueError(f"invalid challenge fields: {path}")
            if not isinstance(data["id"], str) or not IDENTIFIER.fullmatch(data["id"]) or data["id"] in self.challenges:
                raise ValueError("invalid or duplicate challenge id")
            if any(type(data[key]) is not int for key in ("skill", "level", "version")) \
                    or data["skill"] not in self.tracks \
                    or data["level"] not in self.tracks[data["skill"]]["levels"] \
                    or not 1 <= data["version"] <= 2**31 - 1:
                raise ValueError("invalid challenge track, level or version")
            for key in ("title", "instructions", "kind"):
                if not _text(data[key], 200 if key == "title" else 4096):
                    raise ValueError(f"invalid challenge {key}")
            if data["kind"] not in KINDS or not isinstance(data["parameters"], dict) \
                    or not 1 <= len(data["parameters"]) <= 32 or "name" not in data["parameters"]:
                raise ValueError("unsupported challenge kind or parameters")
            for key, choices in data["parameters"].items():
                if not isinstance(key, str) or not IDENTIFIER.fullmatch(key) \
                        or not isinstance(choices, list) or not 1 <= len(choices) <= 32:
                    raise ValueError("parameters must be bounded choice lists")
                if any(type(value) not in (str, int) or isinstance(value, str) and (len(value) > 1000 or "\0" in value)
                       or type(value) is int and not -(2**31) <= value < 2**31 for value in choices):
                    raise ValueError("invalid parameter value")
            if any(not _text(value, 1000) for value in data["parameters"]["name"]):
                raise ValueError("name choices must be nonempty strings")
            if "subject" in data["parameters"] and any(not _text(value, 1000) for value in data["parameters"]["subject"]):
                raise ValueError("subject choices must be nonempty strings")
            if data["kind"] == "extract-field" and ("channel" not in data["parameters"]
                    or any(type(value) is not int or not -(2**31) <= value <= 2**31 - 3
                           for value in data["parameters"]["channel"])):
                raise ValueError("extract-field requires bounded integer channel choices")
            try:
                for _, field, format_spec, conversion in Formatter().parse(data["instructions"]):
                    if field is not None and (field not in data["parameters"] or format_spec or conversion):
                        raise ValueError("instructions must use declared, plain parameter placeholders")
                example = data["instructions"].format(**{key: max(choices, key=lambda value: len(str(value)))
                                                        for key, choices in data["parameters"].items()})
                if len(example) > 8192:
                    raise ValueError("formatted instructions exceed limit")
            except (KeyError, IndexError, ValueError) as exc:
                raise ValueError("invalid challenge instruction placeholders") from exc
            spec = Challenge(**data)
            self.challenges[spec.id] = spec
            self._pools.setdefault((spec.skill, spec.level), []).append(spec.id)
        for skill, track in self.tracks.items():
            for level in track["levels"]:
                if not self.pool(skill, level):
                    raise ValueError(f"authored skill {skill} level {level} has no challenges")
        for topology in {"network" if item.kind.startswith("ssh-") else "single" for item in self.challenges.values()}:
            path = self.root / "compose" / (topology + ".yaml")
            if not path.is_file():
                raise ValueError(f"missing content topology: {path}")

    @staticmethod
    def _read(path):
        if path.stat().st_size > 65536:
            raise ValueError(f"content definition exceeds 64 KiB: {path}")
        return yaml.load(path.read_text(encoding="utf-8"), Loader=ContentLoader)

    def pool(self, skill, level):
        return list(self._pools.get((skill, level), ()))

    def level_for_value(self, skill, value):
        if type(value) is not int:
            return None
        for level, band in self.tracks.get(skill, {}).get("levels", {}).items():
            if band["min_skill"] <= value <= band["max_skill"]:
                return level
        return None

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

    def digest(self, identifier):
        spec = self.get(identifier)
        track = self.tracks[spec.skill]
        data = {"challenge": asdict(spec), "track": {key: value for key, value in track.items() if key != "levels"},
                "band": track["levels"][spec.level]}
        topology = "network" if spec.kind.startswith("ssh-") else "single"
        digest = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        digest.update((self.root / "compose" / (topology + ".yaml")).read_bytes())
        return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description="Validate a nix-boy content directory without Docker or the game")
    parser.add_argument("--content-dir", type=Path, default=CONTENT_ROOT)
    args = parser.parse_args()
    try:
        catalog = Catalog(args.content_dir)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        parser.exit(1, f"Invalid content: {exc}\n")
    print(json.dumps({"status": "valid", "root": str(catalog.root), "tracks": len(catalog.tracks),
                      "levels": sum(len(track["levels"]) for track in catalog.tracks.values()),
                      "challenges": len(catalog.challenges)}))


if __name__ == "__main__":
    main()
