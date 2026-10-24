from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import unquote, urlsplit

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from challenge_content import CONTENT_ROOT
from wiki_pages import export_wiki


class Links(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.urls = []
        self.back = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        for name in ("href", "src"):
            if name in values:
                self.urls.append(values[name])
        if values.get("id") == "wiki-back":
            self.back = values["href"]


class WikiPageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.content = self.root / "content"
        shutil.copytree(CONTENT_ROOT, self.content)
        wiki = self.content / "wiki"
        if wiki.exists():
            shutil.rmtree(wiki)
        (wiki / "articles").mkdir(parents=True)
        (wiki / "assets").mkdir()
        index = {"schema": 1, "subjects": [
            {"id": "filesystem", "title": "Files & <folders>", "articles": [
                {"id": "linux.paths", "title": "Paths & <places>", "file": "articles/paths.html"},
                {"id": "linux.permissions", "title": "Permissions", "file": "articles/permissions.html"}]},
            {"id": "shell", "title": "Shell", "articles": [
                {"id": "linux.pipes", "title": "Pipes", "file": "articles/pipes.html"}]}]}
        (wiki / "index.yaml").write_text(yaml.safe_dump(index), encoding="utf-8")
        (wiki / "articles/paths.html").write_text(
            '<h2>Start here</h2><p>Paths &amp; names. <code>cd ~/lab</code></p>'
            '<a href="wiki:linux.permissions#read-write">Permissions</a>'
            '<a href="challenge:unarmed.files.copy-file">Copy practice</a>'
            '<img src="../assets/path%20tree.svg?theme=light#root" alt="Path tree">', encoding="utf-8")
        (wiki / "articles/permissions.html").write_text(
            '<h2 id="read-write">Read and write</h2><a href="wiki:linux.paths">Paths</a>', encoding="utf-8")
        (wiki / "articles/pipes.html").write_text('<p>Send output between programs.</p>', encoding="utf-8")
        self.asset = b'<svg xmlns="http://www.w3.org/2000/svg"><text>lab</text></svg>'
        (wiki / "assets/path tree.svg").write_bytes(self.asset)
        for path in (self.content / "challenges").rglob("*.yaml"):
            challenge = yaml.safe_load(path.read_text(encoding="utf-8"))
            challenge.pop("wiki", None)
            if challenge["id"] == "unarmed.files.copy-file":
                challenge["wiki"] = ["linux.paths", "linux.permissions"]
                challenge["title"] = "Copy & <keep>"
                challenge["instructions"] += " Keep <script> as plain text."
            path.write_text(yaml.safe_dump(challenge), encoding="utf-8")
        self.output = self.root / "preview"

    def export(self):
        return export_wiki(self.content, self.output)

    def read(self, name):
        return (self.output / name).read_text(encoding="utf-8")

    def test_all_generated_navigation_and_asset_targets_exist(self):
        result = self.export()
        self.assertEqual((result["subjects"], result["articles"], result["challenges"], result["assets"]),
                         (2, 3, 20, 1))
        for path in self.output.glob("*.html"):
            links = Links(path.read_text(encoding="utf-8"))
            with self.subTest(page=path.name):
                self.assertTrue(links.back)
                for url in links.urls:
                    parsed = urlsplit(url)
                    self.assertFalse(parsed.scheme)
                    self.assertTrue((self.output / unquote(parsed.path)).is_file(), url)
        self.assertIn('href="subject-filesystem.html"', self.read("index.html"))
        self.assertIn('href="article-linux.paths.html"', self.read("subject-filesystem.html"))
        self.assertIn('href="article-linux.permissions.html#read-write"', self.read("article-linux.paths.html"))
        self.assertIn('href="challenge-unarmed.files.copy-file.html"', self.read("article-linux.paths.html"))
        challenge = self.read("challenge-unarmed.files.copy-file.html")
        self.assertIn('href="article-linux.paths.html"', challenge)
        self.assertIn('href="article-linux.permissions.html"', challenge)

    def test_images_preserve_paths_bytes_query_and_fragment(self):
        self.export()
        self.assertIn('src="wiki-assets/assets/path%20tree.svg?theme=light#root"',
                      self.read("article-linux.paths.html"))
        self.assertEqual((self.output / "wiki-assets/assets/path tree.svg").read_bytes(), self.asset)

    def test_export_manifest_records_current_sources_and_exact_generated_files(self):
        self.export()
        manifest = json.loads(self.read("wiki-export.json"))
        self.assertEqual(manifest["schema"], 1)
        self.assertIn("content/wiki/index.yaml", manifest["source_sha256"])
        self.assertIn("wiki_pages.py", manifest["source_sha256"])
        expected = {path.relative_to(self.output).as_posix() for path in self.output.rglob("*")
                    if path.is_file() and path.name != "wiki-export.json"}
        self.assertEqual(set(manifest["files_sha256"]), expected)
        for name, fingerprint in manifest["files_sha256"].items():
            self.assertEqual(hashlib.sha256((self.output / name).read_bytes()).hexdigest(), fingerprint)
        source = self.content / "wiki/index.yaml"
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),
                         manifest["source_sha256"]["content/wiki/index.yaml"])
        source.write_text(source.read_text(encoding="utf-8") + "\n# Authored revision\n", encoding="utf-8")
        self.assertNotEqual(hashlib.sha256(source.read_bytes()).hexdigest(),
                            manifest["source_sha256"]["content/wiki/index.yaml"])
        (self.output / "index.html").write_text("Changed output", encoding="utf-8")
        self.assertNotEqual(hashlib.sha256((self.output / "index.html").read_bytes()).hexdigest(),
                            manifest["files_sha256"]["index.html"])

    def test_catalog_labels_and_plain_challenge_text_are_escaped(self):
        self.export()
        self.assertIn("Files &amp; &lt;folders&gt;", self.read("index.html"))
        article = self.read("article-linux.paths.html")
        self.assertIn("Paths &amp; &lt;places&gt;", article)
        self.assertIn('<code>cd ~/lab</code>', article)
        self.assertIn("Copy &amp; &lt;keep&gt;", article)
        self.assertIn("&lt;script&gt; as plain text.", self.read("challenge-unarmed.files.copy-file.html"))

    def test_every_generated_page_enforces_local_resources_and_blocks_active_connections(self):
        self.export()
        policy = "default-src 'self'; connect-src 'none'; frame-src 'none'; worker-src 'none'; " \
                 "object-src 'none'; base-uri 'none'; form-action 'none'; script-src 'self'; style-src 'self'"
        for path in self.output.glob("*.html"):
            with self.subTest(page=path.name):
                html = path.read_text(encoding="utf-8")
                self.assertIn(f'<meta http-equiv="Content-Security-Policy" content="{policy}">', html)
                self.assertLess(html.index('http-equiv="Content-Security-Policy"'), html.index('href="wiki.css"'))
                self.assertIn('<script src="navigation.js" defer></script>', html)

    def test_each_view_has_a_deterministic_back_fallback(self):
        self.export()
        expected = {"index.html": "index.html", "subject-filesystem.html": "index.html",
                    "article-linux.paths.html": "subject-filesystem.html",
                    "challenge-unarmed.files.copy-file.html": "article-linux.paths.html",
                    "challenge-science.text.filter-lines.html": "index.html"}
        for page, fallback in expected.items():
            with self.subTest(page=page):
                self.assertEqual(Links(self.read(page)).back, fallback)
        script = self.read("navigation.js")
        self.assertIn("nixboyWikiPrevious", script)
        self.assertIn("localPage(previous) || localPage(document.referrer)", script)
        self.assertIn("history.back()", script)

    def test_nonempty_or_source_output_is_rejected_without_overwriting(self):
        self.output.mkdir()
        sentinel = self.output / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "new or empty"):
            self.export()
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
        self.assertEqual(list(self.output.iterdir()), [sentinel])
        with self.assertRaisesRegex(ValueError, "separate from"):
            export_wiki(self.content, self.content / "preview")

    def test_invalid_references_fail_before_creating_output(self):
        path = self.content / "wiki/articles/paths.html"
        path.write_text('<a href="wiki:missing">Missing</a>', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unresolved"):
            self.export()
        self.assertFalse(self.output.exists())

    def test_cli_creates_preview_without_docker_or_bridge(self):
        result = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / "wiki_pages.py"),
                                 "--content-dir", str(self.content), "--output", str(self.output)],
                                check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(result.stdout)["status"], "exported")
        self.assertTrue((self.output / "index.html").is_file())


if __name__ == "__main__":
    unittest.main()
