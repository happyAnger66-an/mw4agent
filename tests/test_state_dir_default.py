"""State dir default: ~/.orbit first, then legacy ~/.mw4agent, then ~/orbit."""

from __future__ import annotations

from pathlib import Path

from orbit.config.paths import _default_state_dir_home


def test_default_state_dir_uses_mw4agent_when_dot_orbit_missing(tmp_path: Path) -> None:
    (tmp_path / ".mw4agent").mkdir()
    assert _default_state_dir_home(tmp_path).resolve() == (tmp_path / ".mw4agent").resolve()


def test_default_state_dir_prefers_dot_orbit_when_both_exist(tmp_path: Path) -> None:
    (tmp_path / ".orbit").mkdir()
    (tmp_path / ".mw4agent").mkdir()
    assert _default_state_dir_home(tmp_path).resolve() == (tmp_path / ".orbit").resolve()


def test_default_state_dir_prefers_mw4agent_over_visible_orbit_when_no_dot_orbit(tmp_path: Path) -> None:
    (tmp_path / "orbit").mkdir()
    (tmp_path / ".mw4agent").mkdir()
    assert _default_state_dir_home(tmp_path).resolve() == (tmp_path / ".mw4agent").resolve()


def test_default_state_dir_uses_visible_orbit_when_only_that_exists(tmp_path: Path) -> None:
    (tmp_path / "orbit").mkdir()
    assert _default_state_dir_home(tmp_path).resolve() == (tmp_path / "orbit").resolve()


def test_default_state_dir_fresh_home_defaults_to_dot_orbit(tmp_path: Path) -> None:
    assert _default_state_dir_home(tmp_path).resolve() == (tmp_path / ".orbit").resolve()
