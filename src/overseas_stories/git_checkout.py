"""Fetch a public Markdown repository using one shallow Git checkout per run."""

import subprocess

from .network import direct_subprocess_env
import tempfile
from pathlib import Path, PurePosixPath


class GitReadError(RuntimeError):
    pass


class GitCheckout:
    def __init__(self, repo: str, ref: str = "main"):
        self.repo = repo
        self.ref = ref
        self._tmp: tempfile.TemporaryDirectory | None = None
        self.root: Path | None = None
        self.revision = ""

    def __enter__(self) -> "GitCheckout":
        if self.repo != "indie-tm/indie.md":
            raise GitReadError("Only the configured indie.md repository is enabled")
        self._tmp = tempfile.TemporaryDirectory(prefix="overseas-stories-")
        self.root = Path(self._tmp.name) / "source"
        url = f"https://github.com/{self.repo}.git"
        try:
            subprocess.run(
                ["git", "clone", "--quiet", "--depth", "1", "--single-branch",
                 "--branch", self.ref, url, str(self.root)],
                check=True,
                capture_output=True,
                text=True,
                timeout=180,
                env=direct_subprocess_env(),
            )
            result = subprocess.run(
                ["git", "-C", str(self.root), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
                env=direct_subprocess_env(),
            )
            self.revision = result.stdout.strip()
            return self
        except (OSError, subprocess.SubprocessError) as exc:
            self.__exit__(None, None, None)
            raise GitReadError(f"Could not fetch indie.md: {type(exc).__name__}") from exc

    def __exit__(self, *_: object) -> None:
        if self._tmp is not None:
            self._tmp.cleanup()
        self._tmp = None
        self.root = None

    def list_files(self, repo: str, ref: str = "main") -> tuple[str, dict[str, str]]:
        if repo != self.repo or self.root is None or not self.revision:
            raise GitReadError("GitCheckout must be opened for the requested repository")
        paths = (
            list((self.root / "src/content/people").glob("*.md"))
            + list((self.root / "src/content/journeys").glob("*.md"))
        )
        files = {path.relative_to(self.root).as_posix(): path.relative_to(self.root).as_posix()
                 for path in paths if path.is_file()}
        return self.revision, files

    def read_blob(self, repo: str, sha: str) -> str:
        if repo != self.repo or self.root is None:
            raise GitReadError("GitCheckout is not open for the requested repository")
        relative = PurePosixPath(sha)
        if relative.is_absolute() or ".." in relative.parts:
            raise GitReadError("Unexpected relative path from source checkout")
        target = (self.root / str(relative)).resolve()
        if not target.is_relative_to(self.root.resolve()):
            raise GitReadError("Source file escapes the checkout directory")
        try:
            return target.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise GitReadError(f"Failed to read source Markdown: {sha}") from exc

