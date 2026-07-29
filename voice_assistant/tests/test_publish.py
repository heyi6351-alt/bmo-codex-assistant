from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from bmo_voice.config import Settings
from bmo_voice.publish import (
    GitHubPublisher,
    PreparedPublish,
    PublishError,
)


class ScriptedRunner:
    def __init__(self, project: Path):
        self.project = project
        self.calls: list[tuple[list[str], Path]] = []
        self.status = ""
        self.branch = "main"
        self.head = "a" * 40
        self.after_commit = "b" * 40
        self.auth_ok = True
        self.pr_list = "[]"
        self.pr_url = "https://github.com/example/demo/pull/17"

    @staticmethod
    def _result(
        argv: list[str],
        code: int = 0,
        stdout: str = "",
        stderr: str = "",
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, code, stdout, stderr)

    def __call__(
        self, argv: list[str], cwd: Path
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(argv), cwd))
        command = tuple(argv[1:])
        if command == ("rev-parse", "--show-toplevel"):
            return self._result(argv, stdout=str(self.project) + "\n")
        if command == (
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ):
            return self._result(argv, stdout=self.status)
        if command == ("symbolic-ref", "--quiet", "--short", "HEAD"):
            return self._result(argv, stdout="main\n")
        if command == ("rev-parse", "HEAD"):
            return self._result(argv, stdout=self.head + "\n")
        if command[:2] == ("checkout", "-b"):
            self.branch = command[2]
            return self._result(argv)
        if command[:1] == ("checkout",) and len(command) == 2:
            self.branch = command[1]
            return self._result(argv)
        if command == ("branch", "--show-current"):
            return self._result(argv, stdout=self.branch + "\n")
        if command[:2] == ("merge-base", "--is-ancestor"):
            return self._result(argv)
        if command[:1] == ("add",):
            return self._result(argv)
        if "commit" in command:
            self.head = self.after_commit
            self.status = ""
            return self._result(argv)
        if command[:1] == ("push",):
            return self._result(argv)
        if argv[0] == "gh" and command == ("auth", "status"):
            return self._result(argv, 0 if self.auth_ok else 1)
        if argv[0] == "gh" and command[:2] == ("pr", "list"):
            return self._result(argv, stdout=self.pr_list)
        if argv[0] == "gh" and command[:2] == ("pr", "create"):
            return self._result(argv, stdout=self.pr_url + "\n")
        if command[:2] == ("diff", "--name-only"):
            return self._result(argv, stdout="")
        return self._result(argv, 1, stderr="unexpected command")


class PublishTests(unittest.TestCase):
    def _settings(self, root: Path) -> Settings:
        return Settings(
            vosk_model=root,
            whisper_model=root / "model.bin",
            codex_project_root=root / "projects",
            git_bin="git",
            gh_bin="gh",
        )

    def _publisher(self, root: Path):
        project = root / "projects" / "demo"
        project.mkdir(parents=True)
        runner = ScriptedRunner(project)
        return project, runner, GitHubPublisher(
            self._settings(root), runner=runner
        )

    def test_prepare_requires_project_under_allowlisted_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "projects").mkdir()
            outside = root / "outside"
            outside.mkdir()
            publisher = GitHubPublisher(
                self._settings(root),
                runner=lambda *_args: self.fail("runner should not be called"),
            )
            with self.assertRaisesRegex(PublishError, "允许"):
                publisher.prepare(outside, "job-safe")

    def test_prepare_rejects_non_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, runner, publisher = self._publisher(root)

            def non_repo(argv, cwd):
                runner.calls.append((list(argv), cwd))
                return subprocess.CompletedProcess(argv, 1, "", "not a repo")

            publisher = GitHubPublisher(
                self._settings(root), runner=non_repo
            )
            with self.assertRaisesRegex(PublishError, "Git 仓库"):
                publisher.prepare(project, "job-safe")

    def test_prepare_rejects_dirty_repository_without_external_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, runner, publisher = self._publisher(root)
            runner.status = " M app.py\0"
            with self.assertRaisesRegex(PublishError, "未提交改动"):
                publisher.prepare(project, "job-safe")
            flattened = [call[0] for call in runner.calls]
            self.assertFalse(any(args[0] == "gh" for args in flattened))
            self.assertFalse(
                any("push" in args or "commit" in args for args in flattened)
            )

    def test_prepare_creates_only_local_bmo_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, runner, publisher = self._publisher(root)
            prepared = publisher.prepare(project, "job-1234")
            self.assertEqual(prepared.branch, "bmo/job-1234")
            self.assertEqual(prepared.base_branch, "main")
            checkout = next(
                argv for argv, _cwd in runner.calls if "checkout" in argv
            )
            self.assertEqual(
                checkout,
                ["git", "checkout", "-b", "bmo/job-1234"],
            )
            self.assertFalse(any(argv[0] == "gh" for argv, _ in runner.calls))

    def test_prepare_rejects_metacharacters_in_branch_identifier(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, runner, publisher = self._publisher(root)
            with self.assertRaisesRegex(PublishError, "任务号"):
                publisher.prepare(project, "job;touch-pwned")
            self.assertEqual(runner.calls, [])

    def test_prepare_rejects_chaining_from_an_old_bmo_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, runner, _publisher = self._publisher(root)

            def bmo_base(argv, cwd):
                command = tuple(argv[1:])
                if command == (
                    "symbolic-ref",
                    "--quiet",
                    "--short",
                    "HEAD",
                ):
                    runner.calls.append((list(argv), cwd))
                    return subprocess.CompletedProcess(
                        argv, 0, "bmo/job-old\n", ""
                    )
                return runner(argv, cwd)

            publisher = GitHubPublisher(
                self._settings(root), runner=bmo_base
            )
            with self.assertRaisesRegex(PublishError, "旧的 BMO 分支"):
                publisher.prepare(project, "job-new")
            self.assertFalse(
                any("checkout" in argv for argv, _cwd in runner.calls)
            )

    def test_publish_uses_safe_argv_and_never_pushes_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, runner, publisher = self._publisher(root)
            prepared = publisher.prepare(project, "job-1234")
            dangerous = "src/name;$(touch PWNED).py"
            runner.status = f"?? {dangerous}\0"
            prepared = publisher.collect(prepared)
            result = publisher.publish(
                prepared,
                title="Fix the screen",
                body="Automated test body",
            )
            self.assertEqual(result.pr_number, 17)
            add = next(argv for argv, _ in runner.calls if "add" in argv)
            self.assertIn(dangerous, add)
            self.assertEqual(add[-1], dangerous)
            push = next(argv for argv, _ in runner.calls if "push" in argv)
            self.assertIn(
                "refs/heads/bmo/job-1234:refs/heads/bmo/job-1234",
                push,
            )
            self.assertNotIn("main", push)
            self.assertFalse(any("merge" in argv for argv, _ in runner.calls))
            self.assertEqual(runner.branch, "main")

    def test_publish_rejects_same_path_when_contents_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, runner, publisher = self._publisher(root)
            prepared = publisher.prepare(project, "job-1234")
            changed = project / "app.py"
            changed.write_text("first version\n", encoding="utf-8")
            runner.status = " M app.py\0"
            prepared = publisher.collect(prepared)
            changed.write_text("different version\n", encoding="utf-8")
            with self.assertRaisesRegex(PublishError, "内容变化"):
                publisher.publish(prepared, title="Fix", body="Body")
            commands = [argv for argv, _cwd in runner.calls]
            self.assertFalse(any("commit" in argv for argv in commands))
            self.assertFalse(any("push" in argv for argv in commands))

    def test_auth_failure_stops_before_commit_push_and_pr(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, runner, publisher = self._publisher(root)
            prepared = publisher.prepare(project, "job-1234")
            runner.status = " M app.py\0"
            prepared = publisher.collect(prepared)
            runner.auth_ok = False
            with self.assertRaisesRegex(PublishError, "尚未登录"):
                publisher.publish(prepared, title="Fix", body="Body")
            commands = [argv for argv, _ in runner.calls]
            self.assertFalse(any("commit" in argv for argv in commands))
            self.assertFalse(any("push" in argv for argv in commands))
            self.assertFalse(
                any(argv[0] == "gh" and "create" in argv for argv in commands)
            )

    def test_publish_reuses_existing_open_pr_on_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, runner, publisher = self._publisher(root)
            prepared = PreparedPublish(
                project=project,
                job_id="job-1234",
                branch="bmo/job-1234",
                base_branch="main",
                base_commit="a" * 40,
                changed_paths=("app.py",),
            )
            runner.branch = prepared.branch
            runner.head = "b" * 40
            runner.status = ""
            runner.pr_list = (
                '[{"number":17,"url":'
                '"https://github.com/example/demo/pull/17"}]'
            )

            def retry_runner(argv, cwd):
                command = tuple(argv[1:])
                if command[:2] == ("diff", "--name-only"):
                    runner.calls.append((list(argv), cwd))
                    return subprocess.CompletedProcess(
                        argv, 0, "app.py\0", ""
                    )
                return runner(argv, cwd)

            publisher = GitHubPublisher(
                self._settings(root), runner=retry_runner
            )
            result = publisher.publish(
                prepared, title="Fix", body="Body"
            )
            self.assertEqual(result.pr_url, runner.pr_url)
            self.assertFalse(
                any(
                    argv[0] == "gh" and "create" in argv
                    for argv, _cwd in runner.calls
                )
            )


if __name__ == "__main__":
    unittest.main()
