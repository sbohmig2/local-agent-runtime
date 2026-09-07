from dataclasses import replace
from pathlib import Path

import pytest

from local_agent_runtime.configuration import load_configuration, validate_connection
from local_agent_runtime.contracts import ProcessingClass, ProviderConnection
from local_agent_runtime.errors import RuntimeFailure

ROOT = Path(__file__).resolve().parents[1]


def test_example_configuration_includes_independent_embeddings() -> None:
    config = load_configuration(ROOT / "config/runtime.example.yaml")
    assert len(config.profiles) == 5
    assert len(config.embedding_profiles) == 2
    assert (
        config.embedding_profiles["lm-studio-embeddings"].model
        != config.profiles["lm-studio-local"].model
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://example.com/v1",
        "http://192.168.1.2/v1",
        "http://127.0.0.1:bad/v1",
        "http://user:password@127.0.0.1/v1",
        "http://127.0.0.1/v1?key=secret",
        "http://127.0.0.1/other",
        "http://127.0.0.1:0/v1",
    ],
)
def test_python_adapters_cannot_bypass_loopback(endpoint: str) -> None:
    with pytest.raises(RuntimeFailure):
        validate_connection(
            ProviderConnection("local", "lmstudio", ProcessingClass.LOCAL, endpoint=endpoint)
        )


@pytest.mark.parametrize(
    "fragment", ["version: true", "version: 1\nversion: 1", "version: &v 1\nproviders: *v"]
)
def test_invalid_yaml_version_duplicates_and_aliases(tmp_path: Path, fragment: str) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(fragment)
    with pytest.raises(RuntimeFailure):
        load_configuration(path)


def test_config_cannot_mislabel_external_processing() -> None:
    config = load_configuration(ROOT / "config/runtime.example.yaml")
    with pytest.raises(RuntimeFailure):
        validate_connection(
            replace(config.providers["openrouter"], processing=ProcessingClass.LOCAL)
        )
