import importlib.util
import unittest
from unittest.mock import patch
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


class RegistryBaseUrlTest(unittest.TestCase):
    def test_default_registry_base_url(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(
                build_registry.registry_base_url(),
                "https://presto.c-1o.top/templates",
            )

    def test_registry_base_path_selects_prerelease_channel(self):
        with patch.dict("os.environ", {"REGISTRY_BASE_PATH": "templates-prerelease"}, clear=True):
            self.assertEqual(
                build_registry.registry_base_url(),
                "https://presto.c-1o.top/templates-prerelease",
            )

    def test_explicit_registry_base_url_wins(self):
        with patch.dict(
            "os.environ",
            {
                "REGISTRY_BASE_PATH": "templates-prerelease",
                "REGISTRY_BASE_URL": "https://example.test/custom/",
            },
            clear=True,
        ):
            self.assertEqual(
                build_registry.registry_base_url(),
                "https://example.test/custom",
            )


if __name__ == "__main__":
    unittest.main()
