import logging
import os
import sys
import unittest

from PySubtrans.Helpers.Tests import create_logfile


def discover_tests_in_directory(
    loader: unittest.TestLoader, test_dir: str, base_dir: str
) -> unittest.TestSuite:
    """Discover tests in a specific directory."""
    if not os.path.exists(test_dir):
        return unittest.TestSuite()

    return loader.discover(test_dir, pattern="test_*.py", top_level_dir=base_dir)


def discover_tests(base_dir: str | None = None) -> unittest.TestSuite:
    """Automatically discover all test modules following naming conventions.

    Args:
        base_dir: Base directory to search from. If None, uses parent of this file.
    """
    if base_dir is None:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    loader = unittest.TestLoader()
    original_dir = os.getcwd()

    try:
        os.chdir(base_dir)
        pysubtrans_dir = os.path.join(base_dir, "tests", "PySubtransTests")
        return discover_tests_in_directory(loader, pysubtrans_dir, base_dir)

    finally:
        os.chdir(original_dir)


if __name__ == "__main__":
    scripts_directory = os.path.dirname(os.path.abspath(__file__))
    root_directory = os.path.dirname(scripts_directory)
    results_directory = os.path.join(root_directory, "test_results")

    if not os.path.exists(results_directory):
        os.makedirs(results_directory)

    logging.getLogger().setLevel(logging.INFO)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))

    create_logfile(results_directory, "unit_tests.log")

    # Run discovered tests
    runner = unittest.TextTestRunner(verbosity=1)
    test_suite = discover_tests()
    for test in test_suite:
        result = runner.run(test)
        if not result.wasSuccessful():
            print("Some tests failed or had errors.")
            sys.exit(1)
