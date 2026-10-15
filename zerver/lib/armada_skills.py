"""Reconcile the small set of Codex skills managed by Armada.

The manifest records ownership so removing or renaming an Armada skill deletes its
provisioned copy without touching user- or plugin-managed skills in the same
CODEX_HOME.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path

ARMADA_MANAGED_SKILLS_MANIFEST = ".armada-managed-skills.json"
ARMADA_MANAGED_SKILL_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def _read_managed_skills_manifest(manifest_path: Path) -> set[str]:
    if manifest_path.is_symlink():
        raise OSError("Managed-skills manifest must not be a symbolic link.")
    try:
        raw_manifest = manifest_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return set()
    try:
        manifest = json.loads(raw_manifest)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise OSError("Managed-skills manifest is invalid.") from error
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise OSError("Managed-skills manifest has an unsupported format.")
    raw_skills = manifest.get("skills")
    if not isinstance(raw_skills, list) or any(
        not isinstance(name, str) or ARMADA_MANAGED_SKILL_NAME_PATTERN.fullmatch(name) is None
        for name in raw_skills
    ):
        raise OSError("Managed-skills manifest contains an invalid skill name.")
    return set(raw_skills)


def _remove_managed_skill_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path)


def _replace_managed_skill(source: Path, destination: Path) -> None:
    skills_home = destination.parent
    temporary = Path(tempfile.mkdtemp(prefix=f".armada-skill-{destination.name}-", dir=skills_home))
    backup: Path | None = None
    try:
        shutil.copytree(
            source,
            temporary,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        if destination.exists() or destination.is_symlink():
            backup = Path(
                tempfile.mkdtemp(prefix=f".armada-old-skill-{destination.name}-", dir=skills_home)
            )
            backup.rmdir()
            os.replace(destination, backup)
        try:
            os.replace(temporary, destination)
        except OSError:
            if backup is not None:
                os.replace(backup, destination)
                backup = None
            raise
        if backup is not None:
            _remove_managed_skill_path(backup)
            backup = None
    finally:
        _remove_managed_skill_path(temporary)
        if backup is not None:
            _remove_managed_skill_path(backup)


def _write_managed_skills_manifest(manifest_path: Path, skills: list[str]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".armada-managed-skills-",
        dir=manifest_path.parent,
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"version": 1, "skills": skills}, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, manifest_path)
    finally:
        temporary.unlink(missing_ok=True)


def reconcile_armada_codex_skills(source_dir: Path, codex_home: str | Path) -> list[str]:
    if not source_dir.is_dir():
        raise OSError("Skills source directory is unavailable.")

    skills_home = Path(codex_home).expanduser() / "skills"
    skills_home.mkdir(parents=True, exist_ok=True)
    manifest_path = skills_home / ARMADA_MANAGED_SKILLS_MANIFEST
    previously_managed = _read_managed_skills_manifest(manifest_path)
    source_skills: list[tuple[str, Path]] = []
    for source_skill_dir in sorted(source_dir.iterdir(), key=lambda path: path.name):
        if not source_skill_dir.is_dir() or not (source_skill_dir / "SKILL.md").is_file():
            continue
        if ARMADA_MANAGED_SKILL_NAME_PATTERN.fullmatch(source_skill_dir.name) is None:
            raise OSError("Skills source contains an invalid skill directory name.")
        source_skills.append((source_skill_dir.name, source_skill_dir))

    provisioned_skills = [name for name, _source in source_skills]
    for retired_skill in sorted(previously_managed - set(provisioned_skills)):
        _remove_managed_skill_path(skills_home / retired_skill)
    for name, source_skill_dir in source_skills:
        _replace_managed_skill(source_skill_dir, skills_home / name)
    _write_managed_skills_manifest(manifest_path, provisioned_skills)
    return provisioned_skills
