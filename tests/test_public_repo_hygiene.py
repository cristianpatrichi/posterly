from pathlib import Path
import re
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _tracked_files() -> set[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return {line for line in result.stdout.splitlines() if line}


class PublicRepoHygieneTest(unittest.TestCase):
    def test_private_runtime_paths_are_not_tracked(self):
        tracked = _tracked_files()
        forbidden_exact = {".env", ".DS_Store", "colaj-a4-print.png"}
        forbidden_prefixes = (
            ".venv/",
            "__pycache__/",
            "backend/app/static/",
            "data/projects/",
            "data/sessions/",
            "data/otp/",
            "poze/",
            "qa/",
        )

        offenders = sorted(
            path
            for path in tracked
            if path in forbidden_exact or path.startswith(forbidden_prefixes)
        )
        self.assertEqual(offenders, [], f"private paths are tracked: {offenders}")

    def test_only_the_allow_list_example_is_tracked_under_data(self):
        tracked_data = sorted(path for path in _tracked_files() if path.startswith("data/"))
        self.assertEqual(tracked_data, ["data/allowed_emails.txt.example"])

    def test_docker_context_excludes_environment_files(self):
        patterns = {
            line.strip()
            for line in (ROOT / ".dockerignore").read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertIn("**/.env*", patterns)

    def test_compose_forwards_documented_server_flags(self):
        compose = (ROOT / "docker-compose.yml").read_text()
        self.assertIn("ENABLE_DOCS: ${ENABLE_DOCS:-}", compose)

    def test_ci_actions_are_pinned_to_commit_shas(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        action_refs = re.findall(r"^\s*- uses:\s*([^\s]+)$", workflow, re.MULTILINE)
        self.assertTrue(action_refs)
        for action_ref in action_refs:
            self.assertRegex(action_ref, r"^[^@]+@[0-9a-f]{40}$")

    def test_ci_runs_dependency_audits_with_read_only_permissions(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("python -m pip_audit -r requirements.txt", workflow)
        self.assertIn("npm audit --audit-level=moderate", workflow)


if __name__ == "__main__":
    unittest.main()
