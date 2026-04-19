"""
conftest.py — project-wide pytest configuration and custom markers.
"""


def pytest_configure(config: "pytest.Config") -> None:  # type: ignore[name-defined]
    config.addinivalue_line(
        "markers",
        "llm_quality: LLM-as-judge quality tests "
        "(require HUGGINGFACE_API_TOKEN; slow — run with -m llm_quality)",
    )
