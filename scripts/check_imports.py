import importlib.util
import sys


def check_required_imports(modules: list[str], pip_extras: str | None = None) -> None:
    """Check if required modules are available and exit with helpful message if not"""
    missing_modules = []
    for module_name in modules:
        if importlib.util.find_spec(module_name) is None:
            missing_modules.append(module_name)

    if missing_modules:
        print("Error: Required modules not found")
        print(f"Missing: {', '.join(missing_modules)}")
        print()
        if pip_extras:
            print("Installation options:")
            print(f"  - uv tool install 'ikonsub[{pip_extras}]'")
            print(f"  - uv sync --extra {pip_extras}")
        else:
            print("Installation options:")
            print("  - uv tool install ikonsub")
            print("  - uv sync --all-extras")
        sys.exit(1)
