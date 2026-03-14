"""
Root test configuration for StorageHelperAIOrchestraService.

Registers shared pytest CLI options used across multiple test modules.
"""


def pytest_addoption(parser):
    parser.addoption(
        "--run-llm",
        action="store_true",
        default=False,
        help="Run tests that make real LLM API calls (llm_live / llm_judge markers).",
    )
