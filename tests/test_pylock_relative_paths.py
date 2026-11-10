from __future__ import annotations

import hashlib
import os
import shutil
import sys
from pathlib import Path

import pytest
import tomlkit

from pdm.compat import tomllib
from pdm.utils import url_to_path
from tests import FIXTURES


def _source(project, kind, lock_directory):
    if kind == "directory":
        target = project.root / "local package"
        target.mkdir()
        (target / "pyproject.toml").write_text('[project]\nname = "first"\nversion = "2.0.2"\n', encoding="utf-8")
        entry = {"path": os.path.relpath(target, lock_directory), "editable": True}
    else:
        target = project.root / "first-2.0.2-py2.py3-none-any.whl"
        shutil.copy2(FIXTURES / "artifacts" / target.name, target)
        entry = {
            "path": os.path.relpath(target, lock_directory),
            "hashes": {"sha256": hashlib.sha256(target.read_bytes()).hexdigest()},
        }
    document = {
        "lock-version": "1.0",
        "created-by": "test",
        "packages": [{"name": "first", "version": "2.0.2", kind: entry}],
    }
    lock_directory.mkdir(parents=True, exist_ok=True)
    lock_path = lock_directory / "pylock.toml"
    lock_path.write_text(tomlkit.dumps(document), encoding="utf-8")
    return lock_path, target


@pytest.mark.parametrize("kind", ["directory", "archive"])
@pytest.mark.parametrize("output_directory", ["same", "nested", "sibling"])
def test_export_local_paths_are_relative_to_output(project, pdm, kind, output_directory):
    source, target = _source(project, kind, project.root)
    output_root = {
        "same": project.root,
        "nested": project.root / "export files" / "locks",
        "sibling": project.root.parent / "export files",
    }[output_directory]
    output_root.mkdir(parents=True, exist_ok=True)
    destination = output_root / "pylock.exported.toml"
    original = source.read_bytes()

    pdm(["export", "-f", "pylock", "-L", str(source), "-o", str(destination)], obj=project, strict=True)

    package = tomllib.loads(destination.read_text(encoding="utf-8"))["packages"][0]
    assert (destination.parent / package[kind]["path"]).resolve() == target.resolve()
    assert source.read_bytes() == original
    project.set_lockfile(destination)
    candidate = next(iter(project.get_locked_repository().packages.values())).candidate
    assert candidate.req.absolute_path.resolve() == target.resolve()
    if kind == "directory":
        assert package[kind]["editable"] is True
    else:
        assert package[kind]["hashes"] == {"sha256": hashlib.sha256(target.read_bytes()).hexdigest()}


@pytest.mark.parametrize("kind", ["directory", "archive"])
def test_read_local_paths_are_relative_to_lockfile(project, pdm, kind):
    source, target = _source(project, kind, project.root / "locks")

    result = pdm(["export", "-f", "pylock", "-L", str(source)], obj=project, strict=True)

    package = tomllib.loads(result.stdout)["packages"][0]
    assert (project.root / package[kind]["path"]).resolve() == target.resolve()


@pytest.mark.parametrize("kind", ["directory", "archive"])
def test_write_native_pylock_paths_follow_lockfile_directory(project, kind):
    source, target = _source(project, kind, project.root)
    project.set_lockfile(source)
    repository = project.get_locked_repository()
    destination = project.root / "locks" / "pylock.toml"
    destination.parent.mkdir()
    project.project_config["lock.format"] = "pylock"
    project.set_lockfile(destination)

    project.lockfile.format_lockfile(repository, ["default"], project.lockfile.default_strategies)
    project.lockfile.write(show_message=False)

    package = tomllib.loads(destination.read_text(encoding="utf-8"))["packages"][0]
    assert (destination.parent / package[kind]["path"]).resolve() == target.resolve()
    candidate = next(iter(project.get_locked_repository().packages.values())).candidate
    assert candidate.req.absolute_path.resolve() == target.resolve()


def test_archive_local_path_takes_precedence_over_url(project, pdm):
    source, target = _source(project, "archive", project.root / "locks")
    document = tomllib.loads(source.read_text(encoding="utf-8"))
    document["packages"][0]["archive"]["url"] = "https://example.invalid/remote.whl"
    source.write_text(tomlkit.dumps(document), encoding="utf-8")

    result = pdm(["export", "-f", "pylock", "-L", str(source)], obj=project, strict=True)

    archive = tomllib.loads(result.stdout)["packages"][0]["archive"]
    assert Path(url_to_path(archive["url"])).resolve() == target.resolve()
    assert (project.root / archive["path"]).resolve() == target.resolve()
    assert archive["hashes"] == {"sha256": hashlib.sha256(target.read_bytes()).hexdigest()}


def test_remote_archive_url_is_unchanged(project, pdm):
    source = project.root / "pylock.toml"
    archive = {"url": "https://example.invalid/first-2.0.2.whl", "hashes": {"sha256": "abc"}}
    source.write_text(
        tomlkit.dumps(
            {
                "lock-version": "1.0",
                "created-by": "test",
                "packages": [{"name": "first", "version": "2.0.2", "archive": archive}],
            }
        ),
        encoding="utf-8",
    )
    destination = project.root / "locks" / "pylock.toml"
    destination.parent.mkdir()

    pdm(["export", "-f", "pylock", "-L", str(source), "-o", str(destination)], obj=project, strict=True)

    assert tomllib.loads(destination.read_text(encoding="utf-8"))["packages"][0]["archive"] == archive


@pytest.mark.parametrize("kind", ["directory", "archive"])
def test_export_through_directory_link_uses_physical_lockfile_directory(project, pdm, tmp_path, kind):
    source, target = _source(project, kind, project.root)
    actual_directory = tmp_path / "external" / "locks"
    actual_directory.mkdir(parents=True)
    alias = project.root / "export-link"
    try:
        if sys.platform == "win32":
            import _winapi

            _winapi.CreateJunction(str(actual_directory), str(alias))
        else:
            alias.symlink_to(actual_directory, target_is_directory=True)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"Directory links are unavailable: {error}")
    destination = alias / "pylock.exported.toml"

    pdm(["export", "-f", "pylock", "-L", str(source), "-o", str(destination)], obj=project, strict=True)

    actual_file = actual_directory / destination.name
    package = tomllib.loads(actual_file.read_text(encoding="utf-8"))["packages"][0]
    assert (actual_directory / package[kind]["path"]).resolve() == target.resolve()
    project.set_lockfile(destination)
    candidate = next(iter(project.get_locked_repository().packages.values())).candidate
    assert candidate.req.absolute_path.resolve() == target.resolve()


@pytest.mark.parametrize("self_option", ["--self", "--editable-self"])
@pytest.mark.parametrize("nested", [False, True])
def test_export_self_from_another_working_directory(project, pdm, monkeypatch, self_option, nested):
    source = project.root / "pylock.toml"
    source.write_text('lock-version = "1.0"\ncreated-by = "test"\npackages = []\n', encoding="utf-8")
    (project.root / "test_project.py").touch()
    destination = (project.root / "exports" if nested else project.root) / "pylock.exported.toml"
    destination.parent.mkdir(exist_ok=True)
    monkeypatch.chdir(project.root.parent)

    pdm(["export", "-f", "pylock", "-L", str(source), self_option, "-o", str(destination)], obj=project, strict=True)

    package = tomllib.loads(destination.read_text(encoding="utf-8"))["packages"][0]
    assert (destination.parent / package["directory"]["path"]).resolve() == project.root.resolve()
    assert package["directory"]["editable"] is (self_option == "--editable-self")
