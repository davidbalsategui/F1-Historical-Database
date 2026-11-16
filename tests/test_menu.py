"""Tests for the terminal menu without database or API dependencies."""

import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest

from cli.menu import render_menu, run_menu
import main


EXPECTED_MENU = """=========================================
         F1 HISTORICAL DATABASE
=========================================

1. Buscar piloto
2. Consultar temporada
3. Consultar Gran Premio
4. Ranking histórico de pilotos
5. Estadísticas de constructores
6. Comparar dos pilotos
7. Actualizar base de datos
8. Exportar estadísticas

0. Salir"""

VALID_SELECTIONS = (
    ("1", "Buscar piloto"),
    ("2", "Consultar temporada"),
    ("3", "Consultar Gran Premio"),
    ("4", "Ranking histórico de pilotos"),
    ("5", "Estadísticas de constructores"),
    ("6", "Comparar dos pilotos"),
    ("7", "Actualizar base de datos"),
    ("8", "Exportar estadísticas"),
)


def test_render_menu_matches_requested_text():
    assert render_menu() == EXPECTED_MENU


@pytest.mark.parametrize(("selection", "label"), VALID_SELECTIONS)
def test_valid_selection_displays_placeholder_and_returns_to_menu(
    monkeypatch, capsys, selection, label
):
    answers = iter((selection, "0"))
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    run_menu()

    output = capsys.readouterr().out
    assert f"{label}: funcionalidad pendiente de implementación." in output
    assert "Opción no válida" not in output
    assert output.count(EXPECTED_MENU) == 2
    assert output.rstrip().endswith("Saliendo de F1 Historical Database.")


@pytest.mark.parametrize(
    "selection", ("", " ", "-1", "9", "abc", "1.0", "01", "1 2", "١")
)
def test_invalid_selection_recovers_and_accepts_a_later_valid_selection(
    monkeypatch, capsys, selection
):
    answers = iter((selection, "1", "0"))
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    run_menu()

    output = capsys.readouterr().out
    assert output.count("Opción no válida. Introduce un número del 0 al 8.") == 1
    assert "Buscar piloto: funcionalidad pendiente de implementación." in output
    assert output.count(EXPECTED_MENU) == 3


def test_surrounding_whitespace_is_accepted(monkeypatch, capsys):
    answers = iter((" \t1\t ", " 0 "))
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    run_menu()

    output = capsys.readouterr().out
    assert "Buscar piloto: funcionalidad pendiente de implementación." in output
    assert "Opción no válida" not in output
    assert output.count(EXPECTED_MENU) == 2


def test_all_features_can_be_selected_before_exiting(monkeypatch, capsys):
    answers = iter(("1", "2", "3", "4", "5", "6", "7", "8", "0"))
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    run_menu()

    output = capsys.readouterr().out
    for _, label in VALID_SELECTIONS:
        assert output.count(f"{label}: funcionalidad pendiente de implementación.") == 1
    assert output.count(EXPECTED_MENU) == 9
    assert "Opción no válida" not in output


def test_zero_exits_without_requesting_another_selection(monkeypatch, capsys):
    read_input = Mock(return_value="0")
    monkeypatch.setattr("builtins.input", read_input)

    run_menu()

    read_input.assert_called_once_with("\nSelecciona una opción: ")
    output = capsys.readouterr().out
    assert output.count(EXPECTED_MENU) == 1
    assert output.rstrip().endswith("Saliendo de F1 Historical Database.")
    assert "funcionalidad pendiente" not in output


@pytest.mark.parametrize("interruption", (EOFError, KeyboardInterrupt))
def test_interrupted_input_exits_cleanly(monkeypatch, capsys, interruption):
    read_input = Mock(side_effect=interruption)
    monkeypatch.setattr("builtins.input", read_input)

    run_menu()

    assert read_input.call_count == 1
    assert capsys.readouterr().out.rstrip().endswith(
        "Saliendo de F1 Historical Database."
    )


def test_main_starts_the_menu(monkeypatch):
    start_menu = Mock()
    monkeypatch.setattr(main, "run_menu", start_menu)

    main.main()

    start_menu.assert_called_once_with()


def test_application_runs_as_a_script_and_exits():
    project_root = Path(__file__).resolve().parents[1]

    result = subprocess.run(
        [sys.executable, str(project_root / "main.py")],
        cwd=project_root,
        input="0\n",
        text=True,
        encoding="utf-8",
        capture_output=True,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert EXPECTED_MENU in result.stdout
    assert "Selecciona una opción:" in result.stdout
    assert result.stdout.rstrip().endswith("Saliendo de F1 Historical Database.")
