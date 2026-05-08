import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "build_registry.py"
SPEC = importlib.util.spec_from_file_location("build_registry", MODULE_PATH)
build_registry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_registry)


class ReleaseFilterTest(unittest.TestCase):
    def test_allows_stable_release(self):
        release = {"tag_name": "gongwen-v1.2.1", "draft": False, "prerelease": False}

        self.assertEqual(build_registry.release_skip_reason(release), "")

    def test_skips_prerelease_by_default(self):
        release = {"tag_name": "gongwen-v1.2.1-beta.2", "draft": False, "prerelease": True}

        self.assertEqual(build_registry.release_skip_reason(release), "prerelease")

    def test_can_allow_prerelease_for_non_production_flows(self):
        release = {"tag_name": "gongwen-v1.2.1-beta.2", "draft": False, "prerelease": True}

        self.assertEqual(
            build_registry.release_skip_reason(release, allow_prerelease=True),
            "",
        )

    def test_always_skips_drafts(self):
        release = {"tag_name": "gongwen-v1.2.1", "draft": True, "prerelease": False}

        self.assertEqual(
            build_registry.release_skip_reason(release, allow_prerelease=True),
            "draft",
        )


if __name__ == "__main__":
    unittest.main()
