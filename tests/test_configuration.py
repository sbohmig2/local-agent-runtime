from dataclasses import replace
from pathlib import Path

import pytest

from local_agent_runtime.configuration import load_configuration, validate_connection
from local_agent_runtime.contracts import ProcessingClass, ProviderConnection, ReasoningEffort
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


def test_profile_reasoning_efforts_are_validated(tmp_path: Path) -> None:
    def written(fragment: str) -> Path:
        path = tmp_path / "runtime.yaml"
        path.write_text(
            "version: 1\n"
            "providers:\n  local:\n    driver: lmstudio\n"
            "profiles:\n  reason:\n    provider: local\n    model: m\n"
            "    allow_external_processing: false\n" + fragment + "default_profile: reason\n"
            "task_routes: {}\n",
            encoding="utf-8",
        )
        return path

    configured = load_configuration(
        written("    reasoning_efforts: [high, low]\n    default_reasoning_effort: low\n")
    )
    profile = configured.profiles["reason"]
    assert profile.reasoning_efforts == (ReasoningEffort.HIGH, ReasoningEffort.LOW)
    assert profile.default_reasoning_effort is ReasoningEffort.LOW
    assert load_configuration(written("")).profiles["reason"].reasoning_efforts == ()

    for fragment in (
        "    reasoning_efforts: [enormous]\n",
        "    reasoning_efforts: [low, low]\n",
        "    reasoning_efforts: []\n",
        "    reasoning_efforts: high\n",
        "    default_reasoning_effort: enormous\n",
        "    reasoning_efforts: [low]\n    default_reasoning_effort: high\n",
    ):
        with pytest.raises(RuntimeFailure) as failure:
            load_configuration(written(fragment))
        assert failure.value.code == "invalid_configuration"


def test_model_options_require_exact_models_tasks_and_reasoning_policy(tmp_path: Path) -> None:
    path = tmp_path / "runtime.yaml"
    path.write_text(
        "version: 1\n"
        "providers:\n  local:\n    driver: lmstudio\n"
        "profiles:\n  reason:\n    provider: local\n    model: first\n"
        "    allow_external_processing: false\n"
        "    model_options:\n"
        "      second-choice:\n"
        "        model: second\n"
        "        qualified_tasks: [answer]\n"
        "        reasoning_efforts: [low, high]\n"
        "        default_reasoning_effort: high\n"
        "default_profile: reason\n"
        "task_routes:\n  answer: reason\n",
        encoding="utf-8",
    )
    profile = load_configuration(path).profiles["reason"]
    option = profile.model_options["second-choice"]
    assert option.model == "second"
    assert option.qualified_tasks == ("answer",)
    assert option.reasoning_efforts == (ReasoningEffort.LOW, ReasoningEffort.HIGH)
    assert option.default_reasoning_effort is ReasoningEffort.HIGH

    for fragment in (
        "        qualified_tasks: []\n",
        "        qualified_tasks: [bad task]\n",
        "        model: -unsafe\n        qualified_tasks: [answer]\n",
        "        qualified_tasks: [answer]\n        reasoning_efforts: [low]\n"
        "        default_reasoning_effort: high\n",
    ):
        invalid = path.read_text(encoding="utf-8")
        start = invalid.index("        model: second\n")
        end = invalid.index("default_profile:")
        if fragment.startswith("        model:"):
            replacement = fragment
        else:
            replacement = "        model: second\n" + fragment
        path.write_text(invalid[:start] + replacement + invalid[end:], encoding="utf-8")
        with pytest.raises(RuntimeFailure) as failure:
            load_configuration(path)
        assert failure.value.code == "invalid_configuration"
        path.write_text(invalid, encoding="utf-8")


def test_catalog_model_qualification_does_not_require_consumer_model_ids(tmp_path: Path) -> None:
    path = tmp_path / "runtime.yaml"
    base = (
        "version: 1\n"
        "providers:\n  claude:\n    driver: claude_cli\n"
        "profiles:\n  reason:\n    provider: claude\n    model: default\n"
        "    allow_external_processing: true\n"
        "    catalog_model_tasks: [answer]\n"
        "default_profile: reason\n"
        "task_routes:\n  answer: reason\n"
    )
    path.write_text(base, encoding="utf-8")
    profile = load_configuration(path).profiles["reason"]
    assert profile.catalog_model_tasks == ("answer",)
    assert profile.model_options == {}

    path.write_text(
        base.replace(
            "default_profile:",
            "    model_options:\n"
            "      exact:\n"
            "        model: claude-opus-5\n"
            "        qualified_tasks: [answer]\n"
            "default_profile:",
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeFailure) as failure:
        load_configuration(path)
    assert failure.value.code == "invalid_configuration"
