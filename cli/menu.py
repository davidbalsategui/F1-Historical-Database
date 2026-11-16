"""Interactive menu with placeholders for future application features."""

MENU_OPTIONS = {
    "1": "Buscar piloto",
    "2": "Consultar temporada",
    "3": "Consultar Gran Premio",
    "4": "Ranking histórico de pilotos",
    "5": "Estadísticas de constructores",
    "6": "Comparar dos pilotos",
    "7": "Actualizar base de datos",
    "8": "Exportar estadísticas",
}


def render_menu() -> str:
    """Return the menu text without reading input or accessing external services."""
    return "\n".join(
        [
            "=" * 41,
            "         F1 HISTORICAL DATABASE",
            "=" * 41,
            "",
            *(f"{option}. {label}" for option, label in MENU_OPTIONS.items()),
            "",
            "0. Salir",
        ]
    )


def run_menu() -> None:
    """Repeat the menu until exit, end of input, or keyboard interruption."""
    while True:
        print(render_menu())
        try:
            selection = input("\nSelecciona una opción: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSaliendo de F1 Historical Database.")
            return

        if selection == "0":
            print("Saliendo de F1 Historical Database.")
            return

        if selection in MENU_OPTIONS:
            print(
                f"\n{MENU_OPTIONS[selection]}: "
                "funcionalidad pendiente de implementación.\n"
            )
        else:
            print("\nOpción no válida. Introduce un número del 0 al 8.\n")
