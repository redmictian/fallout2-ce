from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from challenge_content import Catalog, CONTENT_ROOT
from wiki_content import MAX_ARTICLE_BYTES, MAX_INDEX_BYTES, WikiCatalog


class WikiCatalogTests(unittest.TestCase):
    def fixture(self, directory):
        root = Path(directory) / "wiki"
        root.mkdir()
        (root / "articles").mkdir()
        (root / "assets").mkdir()
        (root / "assets/tree.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
        (root / "articles/paths.html").write_text(
            '<h2>Paths</h2><pre><code>pwd\nls -l</code></pre>'
            '<img src="../assets/tree.svg" alt="Directory tree">'
            '<a href="wiki:linux.permissions#mode">Permissions</a>'
            '<a href="challenge:files.copy">Copy a file</a>', encoding="utf-8")
        (root / "articles/permissions.html").write_text('<h2 id="mode">Permissions</h2>', encoding="utf-8")
        index = {"schema": 1, "subjects": [
            {"id": "filesystem", "title": "Filesystem", "articles": [
                {"id": "linux.paths", "title": "Paths", "file": "articles/paths.html"},
                {"id": "linux.permissions", "title": "Permissions", "file": "articles/permissions.html"}]}]}
        self.write_index(root, index)
        task = SimpleNamespace(id="files.copy", title="Copy a file", skill=3, level=1, wiki=("linux.paths",))
        challenges = SimpleNamespace(challenges={task.id: task})
        return root, index, challenges

    @staticmethod
    def write_index(root, index):
        (root / "index.yaml").write_text(yaml.safe_dump(index, sort_keys=False), encoding="utf-8")

    def test_ordered_subjects_articles_html_assets_and_derived_backlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root, _, challenges = self.fixture(directory)
            catalog = WikiCatalog(root, challenges)
            self.assertEqual(tuple(catalog.subjects), ("filesystem",))
            self.assertEqual(catalog.subject("filesystem").articles, ("linux.paths", "linux.permissions"))
            self.assertEqual(catalog.get("linux.paths").subject, "filesystem")
            self.assertIn("<pre><code>pwd\nls -l</code></pre>", catalog.get("linux.paths").html)
            self.assertEqual(catalog.asset_paths, ((root / "assets/tree.svg").resolve(),))
            self.assertEqual(catalog.challenges_for("linux.paths"), (challenges.challenges["files.copy"],))
            self.assertEqual(catalog.challenges_for("linux.permissions"), ())
            for lookup in (catalog.get, catalog.subject, catalog.challenges_for):
                with self.assertRaises(ValueError):
                    lookup("missing")

    def test_subject_and_article_ids_are_valid_and_globally_unique(self):
        with tempfile.TemporaryDirectory() as directory:
            root, index, challenges = self.fixture(directory)
            original_subject = index["subjects"][0]["id"]
            for invalid in ("Linux Paths", "UPPER", "", "a" * 65, True):
                index["subjects"][0]["id"] = invalid
                self.write_index(root, index)
                with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "subject"):
                    WikiCatalog(root, challenges)
            index["subjects"][0]["id"] = original_subject
            articles = index["subjects"][0]["articles"]
            for invalid in ("Bad ID", "linux.paths", "filesystem"):
                articles[1]["id"] = invalid
                self.write_index(root, index)
                with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "article"):
                    WikiCatalog(root, challenges)
            articles[1]["id"] = "linux.permissions"
            index["subjects"].append(dict(index["subjects"][0]))
            self.write_index(root, index)
            with self.assertRaisesRegex(ValueError, "subject"):
                WikiCatalog(root, challenges)

    def test_unresolved_links_and_challenge_references_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root, _, challenges = self.fixture(directory)
            article = root / "articles/paths.html"
            for reference in ("wiki:linux.missing", "challenge:files.missing", "wiki://linux.paths", "wiki:linux.paths?x=1"):
                article.write_text(f'<a href="{reference}">More</a>', encoding="utf-8")
                with self.subTest(reference=reference), self.assertRaisesRegex(ValueError, "unresolved"):
                    WikiCatalog(root, challenges)
            article.write_text('<a href="#local">Local anchor</a>', encoding="utf-8")
            challenges.challenges["files.copy"].wiki = ("linux.missing",)
            with self.assertRaisesRegex(ValueError, "challenge files.copy"):
                WikiCatalog(root, challenges)

    def test_article_and_asset_paths_cannot_escape_including_encoded_and_symlink_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root, index, challenges = self.fixture(directory)
            outside = Path(directory) / "outside.html"
            outside.write_text("Outside the wiki", encoding="utf-8")
            definition = index["subjects"][0]["articles"][0]
            for invalid in ("../outside.html", str(outside.resolve()), "C:/outside.html", "articles\\paths.html"):
                definition["file"] = invalid
                self.write_index(root, index)
                with self.subTest(path=invalid), self.assertRaises(ValueError):
                    WikiCatalog(root, challenges)
            definition["file"] = "articles/paths.html"
            self.write_index(root, index)
            article = root / definition["file"]
            for invalid in ("../../outside.html", "%2e%2e/%2e%2e/outside.html", "https://example.com/a.png", "/a.png", "missing.png"):
                article.write_text(f'<img src="{invalid}">', encoding="utf-8")
                with self.subTest(source=invalid), self.assertRaises(ValueError):
                    WikiCatalog(root, challenges)
            try:
                (root / "escape.html").symlink_to(outside)
            except (NotImplementedError, OSError):
                return  # Windows without symlink privilege still checks all ordinary traversal cases.
            definition["file"] = "escape.html"
            self.write_index(root, index)
            with self.assertRaisesRegex(ValueError, "escapes"):
                WikiCatalog(root, challenges)
            definition["file"] = "articles/paths.html"
            self.write_index(root, index)
            article.write_text('<img src="../escape.html">', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "escapes"):
                WikiCatalog(root, challenges)

    def test_missing_files_duplicate_keys_schema_and_bounded_utf8(self):
        with tempfile.TemporaryDirectory() as directory:
            root, index, challenges = self.fixture(directory)
            index_path = root / "index.yaml"
            self.write_index(root, dict(index, schema=True))
            with self.assertRaisesRegex(ValueError, "schema 1"):
                WikiCatalog(root, challenges)
            self.write_index(root, index)
            index_path.write_text(index_path.read_text(encoding="utf-8") + "schema: 1\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate YAML key"):
                WikiCatalog(root, challenges)
            index_path.write_bytes(b"x" * (MAX_INDEX_BYTES + 1))
            with self.assertRaisesRegex(ValueError, "exceeds"):
                WikiCatalog(root, challenges)
            self.write_index(root, index)
            article = root / "articles/paths.html"
            for content in (b"\xff", b"\0", b"x" * (MAX_ARTICLE_BYTES + 1)):
                article.write_bytes(content)
                with self.subTest(content_size=len(content)), self.assertRaises(ValueError):
                    WikiCatalog(root, challenges)
            article.unlink()
            with self.assertRaisesRegex(ValueError, "missing wiki file"):
                WikiCatalog(root, challenges)

    def test_moving_challenge_between_skills_and_levels_preserves_article_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root, _, _ = self.fixture(directory)
            (root / "articles/paths.html").write_text("<p>Paths</p>", encoding="utf-8")
            content = Path(directory) / "content"
            shutil.copytree(CONTENT_ROOT, content)
            for path in (content / "challenges").rglob("*.yaml"):
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
                data.pop("wiki", None)
                path.write_text(yaml.safe_dump(data), encoding="utf-8")
            challenge_path = content / "challenges/unarmed/level1/copy-file.yaml"
            data = yaml.safe_load(challenge_path.read_text(encoding="utf-8"))
            data["wiki"] = ["linux.paths"]
            challenge_path.write_text(yaml.safe_dump(data), encoding="utf-8")
            before = Catalog(content)
            wiki_before = WikiCatalog(root, before)
            # Other authored tasks retain the original pool, so this needs no progression redesign.
            data.update(skill=12, level=2)
            challenge_path.write_text(yaml.safe_dump(data), encoding="utf-8")
            after = Catalog(content)
            wiki_after = WikiCatalog(root, after)
            self.assertEqual(wiki_before.get("linux.paths"), wiki_after.get("linux.paths"))
            self.assertEqual(wiki_before.challenges_for("linux.paths")[0].id,
                             wiki_after.challenges_for("linux.paths")[0].id)
            self.assertEqual(wiki_after.challenges_for("linux.paths")[0].skill, 12)
            self.assertEqual(wiki_after.challenges_for("linux.paths")[0].level, 2)


if __name__ == "__main__":
    unittest.main()
