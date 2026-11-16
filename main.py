"""Application entry point for the terminal interface."""

from cli.menu import run_menu


def main() -> None:
    """Start the interactive terminal menu."""
    run_menu()


if __name__ == "__main__":
    main()
