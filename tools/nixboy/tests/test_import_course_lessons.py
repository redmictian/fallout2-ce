from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from challenge_content import CONTENT_ROOT, Catalog
from import_course_lessons import LESSONS, adapt_document, import_lessons, scoped_css
from wiki_content import WikiCatalog
from wiki_pages import export_wiki


IMPORTED_WIKI = Path(__file__).resolve().parents[1] / "content-delivery-test/wiki"


class LessonImportTests(unittest.TestCase):
    def test_full_document_adapter_preserves_teaching_and_drops_authority_surfaces(self):
        styles = {}
        result = adapt_document('''<!doctype html><html><head><style>body{color:red}</style></head>
<body><header>old navigation</header><div class="hero"><h1>Learn</h1></div><main id="main">
<svg style="width:100%"><title>Diagram</title><text>One page</text></svg>
<table><tr><td>Evidence</td></tr></table><details><summary>Predict</summary><p>Formative explanation</p></details>
<a href="pr062.html#samples">Continue</a><a href="assessments-pr062-assessor.html">Private reference</a>
<a href="https://example.test/manual">Manual</a><input id="wait-time"><script>SECRET_SCRIPT</script>
</main><footer>PRIVATE_FOOTER</footer></body></html>''', styles)
        for preserved in ('<svg', '<table>', '<details>', 'Formative explanation', 'wiki:course.pr062#samples'):
            self.assertIn(preserved, result)
        for excluded in ('<head', '<style', 'style=', '<script', 'SECRET_SCRIPT', 'PRIVATE_FOOTER',
                         'old navigation', 'assessments-pr062-assessor.html', 'href="https:'):
            self.assertNotIn(excluded, result)
        self.assertIn('disabled', result)
        self.assertIn('https://example.test/manual', result)
        self.assertEqual(list(styles.values()), ['width:100%'])

    def test_scoped_styles_do_not_change_reader_chrome(self):
        css = scoped_css(':root{--paper:white}body{color:black}h2, main>p{margin:1rem}@media(max-width:500px){body{font-size:16px}}')
        self.assertIn('.course-lesson{--paper:white}', css)
        self.assertIn('.course-lesson h2,.course-lesson .lesson-main>p', css)
        self.assertIn('@media(max-width:500px){.course-lesson{font-size:16px}', css)
        self.assertNotIn('body{', css)

    def test_pinned_allowlist_import_is_reproducible_and_never_includes_assessor_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / 'input.zip'
            with zipfile.ZipFile(package, 'w') as archive:
                for name in LESSONS:
                    archive.writestr(name, '<html><body><main><p>Public lesson</p></main></body></html>')
                archive.writestr('pilot.css', 'body{color:black}')
                archive.writestr('assessments-pr062-assessor.html', 'PRIVATE_KEY_2342')
            with mock.patch('import_course_lessons.PACKAGE_SHA256', hashlib.sha256(package.read_bytes()).hexdigest()):
                import_lessons(package, root / 'one')
                import_lessons(package, root / 'two')
                files = lambda folder: {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob('*') if p.is_file()}
                self.assertEqual(files(root / 'one'), files(root / 'two'))
                self.assertFalse(any('assessor' in p.name for p in (root / 'one').rglob('*')))
                self.assertFalse(any(b'PRIVATE_KEY_2342' in data for data in files(root / 'one').values()))
                with self.assertRaisesRegex(ValueError, 'new or empty'):
                    import_lessons(package, root / 'one')
            with self.assertRaisesRegex(ValueError, 'identity'):
                import_lessons(package, root / 'three')

    def test_committed_supplied_lessons_export_offline_with_original_static_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            content = root / 'content'
            shutil.copytree(CONTENT_ROOT, content)
            for path in (content / 'challenges').rglob('*.yaml'):
                data = yaml.safe_load(path.read_text())
                data.pop('wiki', None)
                path.write_text(yaml.safe_dump(data))
            shutil.rmtree(content / 'wiki')
            shutil.copytree(IMPORTED_WIKI, content / 'wiki')
            # A backend-only secret in the content tree is hashed for freshness,
            # never copied or interpolated into learner pages.
            (content / 'assessor-private.txt').write_text('PRIVATE_GRADING_KEY_99881')
            output = root / 'export'
            result = export_wiki(content, output)
            self.assertEqual((result['subjects'], result['articles'], result['assets']), (2, 5, 1))
            memory = (output / 'article-course.pr061.html').read_text()
            profile = (output / 'article-course.pr062.html').read_text()
            self.assertIn('<svg', memory)
            self.assertIn('<table>', memory)
            self.assertIn('<details>', memory)
            self.assertIn('href="article-course.practice-pr061.html"', memory)
            self.assertIn('wiki-assets/assets/course-lessons.css', memory)
            self.assertIn('style-src \'self\'', memory)
            self.assertNotIn('unsafe-inline', memory)
            self.assertIn('Static model: use the explicit cases below.', profile)
            self.assertIn('Without JavaScript:', profile)
            self.assertFalse((output / 'models.js').exists())
            for path in output.rglob('*'):
                if path.is_file():
                    self.assertNotIn('PRIVATE_GRADING_KEY_99881', path.read_text())
                    if path.suffix == '.html':
                        self.assertNotIn('assessments-pr062-assessor.html', path.read_text())
            self.assertFalse(any('assessor' in path.name for path in output.rglob('*')))

    def test_local_stylesheet_import_and_image_graph_is_copied_with_relative_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            content = root / 'content'
            shutil.copytree(CONTENT_ROOT, content)
            for path in (content / 'challenges').rglob('*.yaml'):
                data = yaml.safe_load(path.read_text())
                data.pop('wiki', None)
                path.write_text(yaml.safe_dump(data))
            wiki = content / 'wiki'
            shutil.rmtree(wiki)
            (wiki / 'articles').mkdir(parents=True)
            (wiki / 'assets/styles/nested').mkdir(parents=True)
            (wiki / 'articles/one.html').write_text('<p>Local resources</p>')
            (wiki / 'assets/styles/main.css').write_text('@import "nested/theme.css"; p{background:url("../tree.svg#root")}')
            (wiki / 'assets/styles/nested/theme.css').write_text('p{background:url("../../icon.svg")}')
            for name in ('tree.svg', 'icon.svg'):
                (wiki / 'assets' / name).write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
            (wiki / 'index.yaml').write_text(yaml.safe_dump({'schema': 1, 'subjects': [
                {'id': 'local', 'title': 'Local', 'articles': [
                    {'id': 'local.one', 'title': 'One', 'file': 'articles/one.html',
                     'stylesheets': ['assets/styles/main.css']}]}]}))
            output = root / 'export'
            result = export_wiki(content, output)
            self.assertEqual(result['assets'], 4)
            for name in ('styles/main.css', 'styles/nested/theme.css', 'tree.svg', 'icon.svg'):
                self.assertEqual((output / 'wiki-assets/assets' / name).read_bytes(), (wiki / 'assets' / name).read_bytes())
            self.assertIn('href="wiki-assets/assets/styles/main.css"', (output / 'article-local.one.html').read_text())
            for invalid in ('p{background:url("https://example.test/image.png")}',
                            'p{background:url("../../../../outside.svg")}',
                            '@import "main.css";'):
                (wiki / 'assets/styles/main.css').write_text(invalid)
                with self.subTest(css=invalid), self.assertRaises(ValueError):
                    WikiCatalog(wiki, Catalog(content))


if __name__ == '__main__':
    unittest.main()
