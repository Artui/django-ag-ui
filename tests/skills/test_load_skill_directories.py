from __future__ import annotations

from pathlib import Path

import pytest

from django_ag_ui.skills.load_skill_directories import load_skill_directories


def _write_skill(base: Path, name: str, frontmatter: str, body: str = "Do the thing.") -> Path:
    skill_dir = base / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(f"{frontmatter}\n{body}\n", encoding="utf-8")
    return skill_dir


def test_loads_skills_in_sorted_directory_order(tmp_path: Path) -> None:
    _write_skill(tmp_path, "zeta", "---\ndescription: Last alphabetically\n---")
    _write_skill(tmp_path, "alpha", "---\ndescription: First alphabetically\n---")
    skills = load_skill_directories([tmp_path])
    assert [skill.name for skill in skills] == ["alpha", "zeta"]
    assert skills[0].description == "First alphabetically"
    assert skills[0].instructions == "Do the thing."
    assert skills[0].resource_dir == tmp_path / "alpha"


def test_frontmatter_name_overrides_directory_name(tmp_path: Path) -> None:
    _write_skill(tmp_path, "some-dir", "---\nname: custom-name\ndescription: D\n---")
    (skill,) = load_skill_directories([tmp_path])
    assert skill.name == "custom-name"


def test_non_skill_directories_and_files_are_skipped(tmp_path: Path) -> None:
    _write_skill(tmp_path, "real", "---\ndescription: D\n---")
    (tmp_path / "assets").mkdir()  # no SKILL.md
    (tmp_path / "README.md").write_text("not a skill", encoding="utf-8")
    skills = load_skill_directories([tmp_path])
    assert [skill.name for skill in skills] == ["real"]


def test_missing_directory_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        load_skill_directories([tmp_path / "nope"])


def test_missing_description_raises(tmp_path: Path) -> None:
    _write_skill(tmp_path, "bad", "---\nname: bad\n---")
    with pytest.raises(ValueError, match="description"):
        load_skill_directories([tmp_path])


def test_empty_body_raises(tmp_path: Path) -> None:
    _write_skill(tmp_path, "bad", "---\ndescription: D\n---", body="   ")
    with pytest.raises(ValueError, match="body is empty"):
        load_skill_directories([tmp_path])


def test_missing_frontmatter_fence_means_no_description(tmp_path: Path) -> None:
    # No leading fence: the whole file is body, so the required description is
    # missing and the loader fails fast.
    skill_dir = tmp_path / "plain"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("Just instructions.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="description"):
        load_skill_directories([tmp_path])


def test_unclosed_frontmatter_fence_is_treated_as_body(tmp_path: Path) -> None:
    skill_dir = tmp_path / "broken"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\ndescription: D\nno closing fence")
    with pytest.raises(ValueError, match="description"):
        load_skill_directories([tmp_path])


def test_non_key_value_frontmatter_lines_are_ignored(tmp_path: Path) -> None:
    _write_skill(
        tmp_path,
        "listy",
        "---\ndescription: D\ntags:\n- one\n- two\n---",
    )
    (skill,) = load_skill_directories([tmp_path])
    assert skill.description == "D"
