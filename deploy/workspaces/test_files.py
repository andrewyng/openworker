import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import files


class FileToolsTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.project = self.folder / "project"
        self.project.mkdir()
        self.root = os.open(self.project, files.DIRECTORY)
        self.addCleanup(os.close, self.root)
        env = patch.dict(os.environ, {"WORKSPACE_ID":"ws-test"})
        env.start()
        self.addCleanup(env.stop)

    def op(self, name, **args):
        return files.run({"workspace":"ws-test","operation":{"name":name,"arguments":args}}, self.root)

    def test_relative_edit_and_conflict(self):
        self.op("write_file", path="src/home", content="Hello")
        self.op("edit_file", path="src/home", old_text="Hello", new_text="Welcome")
        with self.assertRaises(ValueError):
            self.op("edit_file", path="src/home", old_text="Hello", new_text="Oops")
        self.assertEqual(self.op("list_files"), {"files":["src/home"]})
        self.assertEqual(self.op("read_file", path="src/home")["text"], "Welcome")

    def test_scoped_read_opens_a_regular_file_under_the_exact_root(self):
        self.op("write_file", path="src/nested/page", content="visible")
        self.op("write_file", path="src-old/key", content="private")
        self.assertEqual(self.op("read_file_under", directory="src", path="src/nested/page")["text"], "visible")
        (self.project / "src/link").symlink_to("../src-old")
        for path in ["src-old/key", "src/../src-old/key", "src/link/key"]:
            with self.subTest(path=path), self.assertRaises((OSError, ValueError)):
                self.op("read_file_under", directory="src", path=path)

    def test_link_and_path_confinement(self):
        outside = self.folder / "outside"
        outside.mkdir()
        sentinel = outside / "sentinel"
        sentinel.write_text("original")
        (self.project / "file-link").symlink_to(sentinel)
        (self.project / "dir-link").symlink_to(outside)
        for path in ["file-link", "dir-link/sentinel", "../outside/sentinel", str(sentinel)]:
            with self.subTest(path=path):
                with self.assertRaises((OSError,ValueError)):
                    self.op("read_file", path=path)
                with self.assertRaises((OSError,ValueError)):
                    self.op("write_file", path=path, content="changed")
        self.assertEqual(sentinel.read_text(), "original")
        self.assertEqual(self.op("list_files")["files"], [])


if __name__ == "__main__":
    unittest.main()
