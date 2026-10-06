"""Fail when the unpinned resolve holds a direct dependency below its newest release.

``upstream-drift.yml`` resolves the newest versions ``pyproject.toml`` allows and
runs the suite. That answers "does the newest *resolvable* set still pass", and
it is silent on a second question: whether the newest resolvable set is the
newest *released* one. A resolver that cannot reach a release does not fail. It
backs off to an older version that does resolve, logs an ordinary ``Updated``
line, and the suite passes against something nobody chose.

That is not hypothetical. From 2.47, ``pydantic-ai-slim[ag-ui]`` declares
``ag-ui-protocol<1`` while this package requires ``ag-ui-protocol>=1.0``, so every
unpinned resolve stopped at slim 2.46 and the drift run went green with 2.54 on
PyPI. Nothing here declared that ceiling: it was inherited from another
distribution's extra, which is exactly the kind of ceiling the drift job exists
to measure, and the kind it could not see.

For every **direct** requirement -- ``[project]`` dependencies, every extra and
every dependency group, because the job installs all of them -- this compares the
version installed in the synced environment with the newest release that:

- is final: no pre-release, dev release or yanked file;
- admits the running Python. The job's single cell is the newest Python, and a
  release that has not reached it is not held back by anything of ours;
- satisfies every specifier this project declares for the name, so a ceiling we
  wrote down (``<3``) is honoured. Declaring one, with a comment saying why, is
  how a hold is *accepted* rather than silenced;
- is at least ``--min-age-days`` old.

The age window exists because this script and the resolver read the index at
different moments, and a release published minutes ago can be visible to one and
not the other -- the same CDN lag the floor gate trips over. The default of seven
days is the Dependabot cooldown: a release younger than that would not have been
adopted here anyway, and on a weekly schedule it delays a real report by at most
one run. A newer release still inside the window is reported as a notice and
never fails the job, so it is visible without flapping.

For each hold the resolver is asked why, by re-locking with that release
required (``uv lock --dry-run --upgrade-package 'name==X'``). uv's derivation
names the requirement that excludes it. ``uv tree --invert`` cannot: the
excluding constraint usually belongs to the *newer* version of some other
distribution, which by construction never appears in the lock.

Run it inside the synced environment, since the installed versions and the
interpreter are what it measures::

    uv run --no-sync python scripts/check_held_back.py --report held-back.md
"""

from __future__ import annotations

import argparse
import datetime
import importlib.metadata
import json
import os
import pathlib
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import (
    InvalidSdistFilename,
    InvalidWheelFilename,
    NormalizedName,
    canonicalize_name,
    parse_sdist_filename,
    parse_wheel_filename,
)
from packaging.version import InvalidVersion, Version

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10, where pytest brings ``tomli`` instead.
    import tomli as tomllib

# The simple API, in its JSON form, is what uv itself reads, so the two answer
# from the same source. PyPI's ``/pypi/<name>/json`` is a different cache whose
# ``info.version`` has been seen lagging the simple index by minutes.
INDEX = "https://pypi.org/simple"
ACCEPT = "application/vnd.pypi.simple.v1+json"


@dataclass(frozen=True)
class Release:
    version: Version
    published: datetime.datetime


@dataclass(frozen=True)
class Finding:
    name: NormalizedName
    installed: Version
    newest: Release | None
    settled: Release | None

    @property
    def held(self) -> bool:
        """Installed below a release that is outside the age window."""
        return self.settled is not None and self.installed < self.settled.version

    @property
    def fresh(self) -> bool:
        """Installed below a release that is still inside the age window, and nothing older."""
        return not self.held and self.newest is not None and self.installed < self.newest.version


def declared_requirements(pyproject: dict[str, Any]) -> Iterator[str]:
    """Every requirement string the drift job installs directly.

    That is the job's ``uv sync --all-groups --all-extras``: the project's own
    dependencies, every extra, and every dependency group. A dev-only group is
    in scope on purpose -- when it holds a runtime dependency back, the suite
    tests an older release than a consumer installs, which is the gap itself.
    """
    project = pyproject.get("project", {})
    yield from project.get("dependencies", [])
    for extra in project.get("optional-dependencies", {}).values():
        yield from extra
    for group in pyproject.get("dependency-groups", {}).values():
        # An ``{include-group = ...}`` entry is skipped rather than followed:
        # every group is walked here anyway, the included one with the rest.
        yield from (entry for entry in group if isinstance(entry, str))


def declared_bounds(pyproject: dict[str, Any]) -> dict[NormalizedName, SpecifierSet]:
    """The intersection of everything this project declares, per direct dependency.

    The intersection, because ``--all-extras --all-groups`` installs every
    occurrence at once: ``pydantic-ai-slim>=2.37,<3`` in the dependencies and
    ``pydantic-ai-slim[anthropic]>=2.33,<3`` in an extra both bind the same
    install. A requirement whose marker excludes this interpreter binds nothing.

    uv's ``constraint-dependencies`` narrow that, and its
    ``override-dependencies`` replace it, because that is what each does to the
    resolve. A constraint is the place to accept a hold without publishing a
    ceiling to consumers. Neither adds a name: a constraint on a transitive
    dependency is not a direct requirement.
    """
    own = canonicalize_name(pyproject.get("project", {}).get("name", ""))
    bounds: dict[NormalizedName, SpecifierSet] = {}
    for line in declared_requirements(pyproject):
        requirement = _applicable(line)
        if requirement is None:
            continue
        name = canonicalize_name(requirement.name)
        # A group that installs the project's own extras names the project; it
        # is built from the working tree, so there is no release to compare.
        if name == own:
            continue
        bounds[name] = bounds.get(name, SpecifierSet()) & requirement.specifier
    uv = pyproject.get("tool", {}).get("uv", {})
    overrides: dict[NormalizedName, SpecifierSet] = {}
    for line in uv.get("override-dependencies", []):
        requirement = _applicable(line)
        if requirement is not None and canonicalize_name(requirement.name) in bounds:
            name = canonicalize_name(requirement.name)
            overrides[name] = overrides.get(name, SpecifierSet()) & requirement.specifier
    bounds.update(overrides)
    for line in uv.get("constraint-dependencies", []):
        requirement = _applicable(line)
        if requirement is not None and canonicalize_name(requirement.name) in bounds:
            bounds[canonicalize_name(requirement.name)] &= requirement.specifier
    return bounds


def _applicable(line: str) -> Requirement | None:
    try:
        requirement = Requirement(line)
    except InvalidRequirement as error:
        raise SystemExit(f"cannot parse requirement {line!r}: {error}") from error
    # ``extra`` is supplied because older ``packaging`` raises on a marker that
    # names it when the environment has none. pyproject declares extras as
    # tables rather than markers, so an empty one matches nothing it should.
    if requirement.marker is not None and not requirement.marker.evaluate({"extra": ""}):
        return None
    return requirement


def _version_of(filename: str) -> Version | None:
    try:
        if filename.endswith(".whl"):
            return parse_wheel_filename(filename)[1]
        if filename.endswith((".tar.gz", ".zip")):
            return parse_sdist_filename(filename)[1]
    except (InvalidWheelFilename, InvalidSdistFilename, InvalidVersion):
        pass
    # Eggs, installers and legacy names with no parseable version. Nothing
    # current publishes them, and uv does not install them either.
    return None


def _admits(requires_python: str | None, python: Version) -> bool:
    if not requires_python:
        return True
    try:
        return SpecifierSet(requires_python).contains(python, prereleases=True)
    except InvalidSpecifier:
        # Only ancient files carry one, and the resolver refuses them too.
        return False


def _published(raw: str | None) -> datetime.datetime:
    if raw is None:
        # PEP 700 times are served for every file PyPI holds; a mirror that
        # omits them is treated as old, which can only make a hold *more*
        # visible, never hide one.
        return datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
    # ``fromisoformat`` accepts the trailing ``Z`` only from Python 3.11.
    return datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))


def releases_from_index(files: Iterable[dict[str, Any]], python: Version) -> list[Release]:
    """Group a simple-API file list into releases this interpreter can install.

    A release counts from its **earliest** installable file: that is when a
    resolver could first have picked it.
    """
    earliest: dict[Version, datetime.datetime] = {}
    for file in files:
        # ``yanked`` is either ``false`` or the reason string.
        if file.get("yanked"):
            continue
        if not _admits(file.get("requires-python"), python):
            continue
        version = _version_of(file["filename"])
        if version is None:
            continue
        published = _published(file.get("upload-time"))
        if version not in earliest or published < earliest[version]:
            earliest[version] = published
    return [Release(version, published) for version, published in earliest.items()]


def classify(
    name: NormalizedName,
    installed: Version,
    releases: Iterable[Release],
    declared: SpecifierSet,
    now: datetime.datetime,
    min_age: datetime.timedelta,
) -> Finding:
    """Compare what was installed with what the declared window admits."""
    allowed = [
        release
        for release in releases
        # ``prereleases=False`` drops pre- and dev releases even where the
        # declared specifier names one (``>=1.0rc1``). Left at its default, a
        # specifier like that would switch them back on, and the job would
        # start failing on a beta nobody here asked for.
        if declared.contains(release.version, prereleases=False)
    ]
    settled = [release for release in allowed if now - release.published >= min_age]
    return Finding(
        name=name,
        installed=installed,
        newest=max(allowed, key=lambda release: release.version, default=None),
        settled=max(settled, key=lambda release: release.version, default=None),
    )


def fetch_files(name: NormalizedName) -> list[dict[str, Any]]:
    request = urllib.request.Request(f"{INDEX}/{name}/", headers={"Accept": ACCEPT})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)["files"]
        except (urllib.error.URLError, TimeoutError) as error:
            # The resolve this runs after has just read the same index, so a
            # failure here is transient. It is retried and then fatal: a check
            # that passes when it could not look is the failure it exists for.
            if attempt == 2:
                raise SystemExit(f"cannot read {INDEX}/{name}/: {error}") from error
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def _installed(name: NormalizedName) -> Version | None:
    try:
        distribution = importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        return None
    # A path, URL or VCS install (PEP 610) has no index release to fall behind.
    if distribution.read_text("direct_url.json") is not None:
        return None
    return Version(distribution.version)


def ask_the_resolver(root: pathlib.Path, name: str, version: Version) -> str:
    """Re-lock with the newer release required, and return what uv says about it.

    ``==`` rather than ``>=``: the question is why *this* release cannot be
    reached, and a range makes uv walk every newer one too, which multiplies the
    derivation without adding a reason.
    """
    command = ["uv", "lock", "--dry-run", "--upgrade-package", f"{name}=={version}"]
    result = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        env={**os.environ, "NO_COLOR": "1"},
        check=False,
    )
    output = "\n".join(part.strip() for part in (result.stderr, result.stdout) if part.strip())
    if result.returncode == 0:
        # Nothing excludes it, so the resolver preferred something else, and
        # what the dry run would move down is what it was preferred to.
        return (
            f"`{' '.join(command)}` succeeds, so no requirement excludes it and the "
            f"resolver preferred another package's newer release. Requiring it would "
            f"change:\n\n{output}"
        )
    return output


def _age(now: datetime.datetime, published: datetime.datetime) -> str:
    days = (now - published).days
    return f"{published:%Y-%m-%d} ({days} day{'' if days == 1 else 's'} ago)"


def _annotation(level: str, title: str, message: str) -> str:
    # Escaped as the runner documents. A workflow command ends at a newline, so
    # an unescaped message is cut at its first line; and a property value ends
    # at a comma, so an unescaped title is cut there and the rest misread as
    # another property.
    message = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    title = title.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    title = title.replace(":", "%3A").replace(",", "%2C")
    return f"::{level} title={title}::{message}"


def report(
    findings: list[Finding],
    reasons: dict[NormalizedName, str],
    python: Version,
    min_age_days: int,
    now: datetime.datetime,
) -> str:
    held = [finding for finding in findings if finding.held]
    fresh = [finding for finding in findings if finding.fresh]
    lines = [
        f"Checked {len(findings)} direct dependencies against PyPI on Python {python}.",
        "",
    ]
    if held:
        lines += [
            "### Held back",
            "",
            "The unpinned resolve installed an older release than `pyproject.toml` allows,",
            "because a requirement elsewhere excludes the newer one. The suite passing says",
            "nothing about the newer release. Remove the conflict, or declare the ceiling",
            "with a comment saying why: a declared ceiling is what this check honours, and",
            "`[tool.uv] constraint-dependencies` declares one without publishing it.",
            "",
            f"| Package | Resolved | Newest allowed past the {min_age_days}-day window "
            "| Newest allowed |",
            "| --- | --- | --- | --- |",
        ]
        for finding in held:
            assert finding.settled is not None and finding.newest is not None
            lines.append(
                f"| `{finding.name}` | {finding.installed} | {finding.settled.version}, "
                f"{_age(now, finding.settled.published)} | {finding.newest.version} |"
            )
        lines.append("")
        for finding in held:
            assert finding.settled is not None
            lines += [
                f"<details><summary>Why <code>{finding.name}</code> stops at "
                f"{finding.installed}</summary>",
                "",
                "```text",
                reasons[finding.name],
                "```",
                "",
                "</details>",
                "",
            ]
    if fresh:
        lines += [
            f"### Newer, but inside the {min_age_days}-day window",
            "",
            "Not a failure: the index may not have propagated it yet. Reported again",
            "on the next run if it is still not resolved.",
            "",
        ]
        for finding in fresh:
            assert finding.newest is not None
            lines.append(
                f"- `{finding.name}` {finding.installed}: {finding.newest.version} published "
                f"{_age(now, finding.newest.published)}"
            )
        lines.append("")
    if not held and not fresh:
        lines.append("Every direct dependency resolved to its newest allowed release.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", type=pathlib.Path, default=pathlib.Path.cwd())
    parser.add_argument("--min-age-days", type=int, default=7)
    parser.add_argument("--report", type=pathlib.Path, help="also write the report here")
    parser.add_argument(
        "--no-explain",
        action="store_true",
        help="skip asking the resolver why each hold happens",
    )
    args = parser.parse_args(argv)

    pyproject = tomllib.loads((args.project / "pyproject.toml").read_text())
    python = Version(platform.python_version())
    now = datetime.datetime.now(datetime.timezone.utc)
    min_age = datetime.timedelta(days=args.min_age_days)

    findings: list[Finding] = []
    for name, declared in sorted(declared_bounds(pyproject).items()):
        installed = _installed(name)
        if installed is None:
            continue
        releases = releases_from_index(fetch_files(name), python)
        findings.append(classify(name, installed, releases, declared, now, min_age))
    if not findings:
        # Nothing declared is installed, so this is not running in the synced
        # environment, and passing would be a check that did not look.
        print(
            f"none of the direct dependencies is installed for {sys.executable}; "
            "run this inside the synced environment",
            file=sys.stderr,
        )
        return 2

    reasons: dict[NormalizedName, str] = {}
    for finding in findings:
        if finding.held:
            assert finding.settled is not None
            reasons[finding.name] = (
                "(not asked: --no-explain)"
                if args.no_explain
                else ask_the_resolver(args.project, finding.name, finding.settled.version)
            )
            print(
                _annotation(
                    "error",
                    f"Held back: {finding.name}",
                    f"{finding.name} resolved to {finding.installed}; pyproject.toml allows "
                    f"{finding.settled.version}, published "
                    f"{_age(now, finding.settled.published)}.\n\n"
                    f"{reasons[finding.name]}",
                )
            )
        elif finding.fresh:
            assert finding.newest is not None
            print(
                _annotation(
                    "notice",
                    f"Newer, inside the window: {finding.name}",
                    f"{finding.name} resolved to {finding.installed}; "
                    f"{finding.newest.version} was published "
                    f"{_age(now, finding.newest.published)}.",
                )
            )

    text = report(findings, reasons, python, args.min_age_days, now)
    print(text)
    if args.report is not None:
        args.report.write_text(text + "\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as handle:
            handle.write(text + "\n")
    return 1 if any(finding.held for finding in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
