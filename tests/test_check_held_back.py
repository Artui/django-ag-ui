"""The drift job fails when a direct dependency is held below its newest release.

``scripts/check_held_back.py`` runs only inside the scheduled upstream-drift
workflow, so nothing on a pull request would otherwise exercise it. What it
exists to catch went unseen for weeks: ``pydantic-ai-slim[ag-ui]`` from 2.47 caps
``ag-ui-protocol<1`` while this package requires ``>=1.0``, so every unpinned
resolve stopped at 2.46 and the job passed with 2.54 on PyPI.

End to end, against the real resolver and the real index, the check flags slim
on a tree that depends on ``[ag-ui]`` and nothing on one that depends on
``[ui]``. These cases pin the rules that decide it, each of which would
otherwise fail quietly in one direction or the other: a rule that admits too
much makes the job flap, and one that admits too little is the green run again.

Index files are shaped as PyPI's simple API serves them in its JSON form
(``api-version`` 1.4): ``yanked`` is ``false`` or the reason string, and
``upload-time`` carries microseconds and a ``Z``. ``_REAL_FILE`` is one entry
copied verbatim from ``https://pypi.org/simple/ag-ui-protocol/``.
"""

from __future__ import annotations

import datetime
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import packaging
import pytest
from packaging.specifiers import SpecifierSet
from packaging.version import Version

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO / "scripts" / "check_held_back.py"
_WORKFLOW = _REPO / ".github" / "workflows" / "upstream-drift.yml"

_NOW = datetime.datetime(2026, 10, 6, 12, tzinfo=datetime.timezone.utc)
_WEEK = datetime.timedelta(days=7)
_PY314 = Version("3.14.2")

_REAL_FILE: dict[str, Any] = {
    "core-metadata": False,
    "data-dist-info-metadata": False,
    "filename": "ag_ui_protocol-1.0.0.tar.gz",
    "hashes": {"sha256": "cfebecef2e7bc942cc8a52d908a4ea86a98f631d2ffdc6ec5812bb843eac74fb"},
    "provenance": None,
    "requires-python": ">=3.9",
    "size": 30759,
    "upload-time": "2026-09-17T18:31:19.448434Z",
    "url": "https://files.pythonhosted.org/packages/57/92/"
    "d88fdc7f4648dc38d3d54c595066bb3e63a4c091884ffb2f98272c33eef8/ag_ui_protocol-1.0.0.tar.gz",
    "yanked": False,
}


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_held_back", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before it runs: ``dataclass`` resolves string annotations
    # through ``sys.modules[cls.__module__]``.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _days_ago(days: float) -> str:
    when = _NOW - datetime.timedelta(days=days)
    return when.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _wheel(
    version: str,
    days: float,
    *,
    requires_python: str | None = ">=3.10",
    yanked: bool | str = False,
    name: str = "pydantic_ai_slim",
) -> dict[str, Any]:
    return {
        **_REAL_FILE,
        "filename": f"{name}-{version}-py3-none-any.whl",
        "requires-python": requires_python,
        "upload-time": _days_ago(days),
        "yanked": yanked,
    }


def _classify(installed: str, files: list[dict[str, Any]], declared: str = "") -> Any:
    script = _script()
    return script.classify(
        "pydantic-ai-slim",
        Version(installed),
        script.releases_from_index(files, _PY314),
        SpecifierSet(declared),
        _NOW,
        _WEEK,
    )


def test_a_release_past_the_window_that_did_not_resolve_is_held() -> None:
    finding = _classify("2.46.0", [_wheel("2.46.0", 30), _wheel("2.51.0", 10), _wheel("2.54.0", 1)])

    assert finding.held
    assert not finding.fresh
    assert finding.settled.version == Version("2.51.0")
    assert finding.newest.version == Version("2.54.0")


def test_a_release_inside_the_window_is_reported_and_does_not_fail() -> None:
    # Published a day ago: the index this read and the one the resolver read
    # may disagree about it, which is the flap the window exists to absorb.
    finding = _classify("2.46.0", [_wheel("2.46.0", 30), _wheel("2.54.0", 1)])

    assert not finding.held
    assert finding.fresh


def test_the_window_is_inclusive_at_exactly_its_length() -> None:
    finding = _classify("2.46.0", [_wheel("2.46.0", 30), _wheel("2.54.0", 7)])

    assert finding.held


def test_the_newest_release_installed_is_current() -> None:
    finding = _classify("2.54.0", [_wheel("2.46.0", 30), _wheel("2.54.0", 10)])

    assert not finding.held
    assert not finding.fresh


def test_a_declared_ceiling_is_honoured() -> None:
    # The way a hold is accepted on purpose: a ceiling this project wrote down.
    finding = _classify("2.54.0", [_wheel("2.54.0", 30), _wheel("3.0.0", 10)], "<3")

    assert not finding.held
    assert not finding.fresh


@pytest.mark.parametrize("declared", ["", ">=2.0.0rc1"])
def test_pre_and_dev_releases_are_not_newer(declared: str) -> None:
    # The second case is a floor that itself names a pre-release, which on
    # its own would make the specifier admit them.
    finding = _classify(
        "2.54.0",
        [_wheel("2.54.0", 30), _wheel("2.55.0b1", 10), _wheel("2.55.0.dev3", 10)],
        declared,
    )

    assert not finding.held
    assert not finding.fresh


@pytest.mark.parametrize("yanked", [True, "broken metadata"])
def test_a_yanked_release_is_not_newer(yanked: bool | str) -> None:
    finding = _classify("2.54.0", [_wheel("2.54.0", 30), _wheel("2.55.0", 10, yanked=yanked)])

    assert not finding.held


def test_a_release_that_has_not_reached_this_python_is_not_held() -> None:
    # The job's one cell is the newest Python, and nothing of ours holds back
    # a release that does not install on it.
    finding = _classify(
        "2.54.0",
        [_wheel("2.54.0", 30), _wheel("2.55.0", 10, requires_python=">=3.15")],
    )

    assert not finding.held


def test_a_release_counts_from_its_earliest_installable_file() -> None:
    # The sdist went up ten days ago and a wheel a day ago: a resolver could
    # have picked the release from the first of them.
    sdist = {**_wheel("2.55.0", 10), "filename": "pydantic_ai_slim-2.55.0.tar.gz"}
    finding = _classify("2.54.0", [_wheel("2.54.0", 30), _wheel("2.55.0", 1), sdist])

    assert finding.held
    assert finding.settled.version == Version("2.55.0")


def test_the_index_entry_pypi_serves_parses() -> None:
    (release,) = _script().releases_from_index([_REAL_FILE], _PY314)

    assert release.version == Version("1.0.0")
    assert release.published == datetime.datetime(
        2026, 9, 17, 18, 31, 19, 448434, tzinfo=datetime.timezone.utc
    )


def test_files_with_no_parseable_version_are_ignored() -> None:
    legacy = {**_REAL_FILE, "filename": "ag-ui-protocol-0.1.win32.exe"}

    assert _script().releases_from_index([legacy], _PY314) == []


def test_only_an_index_install_has_a_release_to_compare() -> None:
    # The project itself is installed editable, so it carries a PEP 610
    # ``direct_url.json`` and has no index release to fall behind.
    script = _script()

    assert script._installed("packaging") == Version(packaging.__version__)
    assert script._installed("django-ag-ui") is None
    assert script._installed("no-such-distribution-here") is None


def test_declared_bounds_intersect_every_occurrence_the_job_installs() -> None:
    pyproject = {
        "project": {
            "name": "django-ag-ui",
            "dependencies": [
                "pydantic-ai-slim[ag-ui]>=2.37,<3",
                # A marker no interpreter running this suite satisfies.
                "tomli>=2; python_version < '3'",
            ],
            "optional-dependencies": {"anthropic": ["pydantic-ai-slim[anthropic]>=2.33,<3"]},
        },
        "dependency-groups": {
            "dev": [{"include-group": "lint"}, "django-ag-ui[anthropic]"],
            "lint": ["Ruff>=0.9"],
        },
        "tool": {"uv": {"constraint-dependencies": ["ruff<1", "not-direct<2"]}},
    }

    bounds = _script().declared_bounds(pyproject)

    assert bounds == {
        "pydantic-ai-slim": SpecifierSet(">=2.37,<3") & SpecifierSet(">=2.33,<3"),
        # From a group another one includes, under its normalised name, with
        # the uv constraint folded in as this project's own ceiling.
        "ruff": SpecifierSet(">=0.9,<1"),
    }


def test_an_override_replaces_what_the_project_declares() -> None:
    # uv resolves an overridden name against the override alone, so a ceiling
    # declared beside it no longer binds the resolve, and must not bind this.
    pyproject = {
        "project": {"name": "demo", "dependencies": ["pydantic-ai-slim>=2.37,<3"]},
        "tool": {"uv": {"override-dependencies": ["pydantic-ai-slim>=2.37"]}},
    }

    assert _script().declared_bounds(pyproject) == {"pydantic-ai-slim": SpecifierSet(">=2.37")}


def test_main_fails_on_a_hold_and_writes_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = _script()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["pydantic-ai-slim[ag-ui]>=2.37,<3"]\n'
    )
    files = [_wheel("2.46.0", 30), _wheel("2.51.0", 10)]
    monkeypatch.setattr(script, "fetch_files", lambda name: files)
    monkeypatch.setattr(script, "_installed", lambda name: Version("2.46.0"))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    report = tmp_path / "held-back.md"

    status = script.main(
        ["--project", str(tmp_path), "--report", str(report), "--no-explain", "--min-age-days", "0"]
    )

    assert status == 1
    assert "| `pydantic-ai-slim` | 2.46.0 | 2.51.0" in report.read_text()
    assert "::error title=Held back%3A pydantic-ai-slim::" in capsys.readouterr().out


def test_an_annotation_title_cannot_split_into_another_property() -> None:
    # A property value ends at a comma: unescaped, this title would be cut at
    # "Newer" and the rest read as a malformed second property.
    line = _script()._annotation("notice", "Newer, inside the window: x", "a\nb")

    assert line == "::notice title=Newer%2C inside the window%3A x::a%0Ab"


def test_a_run_outside_the_synced_environment_does_not_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Nothing declared is installed, so nothing was compared, and a check that
    # could not look must not report that it found nothing.
    script = _script()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["pydantic-ai-slim>=2.37,<3"]\n'
    )
    monkeypatch.setattr(script, "_installed", lambda name: None)

    assert script.main(["--project", str(tmp_path), "--no-explain"]) == 2


def test_main_passes_when_everything_is_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _script()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["pydantic-ai-slim>=2.37,<3"]\n'
    )
    monkeypatch.setattr(script, "fetch_files", lambda name: [_wheel("2.54.0", 30)])
    monkeypatch.setattr(script, "_installed", lambda name: Version("2.54.0"))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    assert script.main(["--project", str(tmp_path), "--no-explain"]) == 0


def _steps(workflow: str) -> list[str]:
    # One block per step of the single job, split at the step list's own
    # indentation so a ``- `` inside a step's script cannot start a new one.
    return workflow.split("\n      - ")[1:]


def test_the_workflow_runs_the_check_and_still_runs_the_suite_after_it() -> None:
    # The issue the job files says which of the two failed, so the suite has
    # to run whatever the check decided, and the check only helps if it runs.
    steps = _steps(_WORKFLOW.read_text(encoding="utf-8"))
    check = next(i for i, step in enumerate(steps) if "id: held-back\n" in step)
    test = next(i for i, step in enumerate(steps) if "id: test\n" in step)

    assert "python scripts/check_held_back.py" in steps[check]
    assert check < test
    assert "if: ${{ !cancelled() && steps.install.outcome == 'success' }}" in steps[test]
