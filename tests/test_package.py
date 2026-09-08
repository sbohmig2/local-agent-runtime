import local_agent_runtime as lar
from local_agent_runtime.api_contract import API_VERSION, OPERATIONS, SCHEMAS
from local_agent_runtime.version import PACKAGE_VERSION

RELEASED_1_0_0_OPERATIONS = {
    "health",
    "profiles",
    "selectProfile",
    "createSession",
    "session",
    "events",
    "continueSession",
    "submitToolResults",
    "cancelSession",
    "embeddingProfiles",
    "embed",
}


def test_package_version_is_available() -> None:
    assert lar.__version__ == PACKAGE_VERSION
    assert lar.RuntimeService.__module__ == "local_agent_runtime.service"
    assert lar.EmbeddingService.__module__ == "local_agent_runtime.embeddings"
    assert callable(lar.build_runtime)
    assert callable(lar.create_app)


def test_api_version_stays_in_the_released_compatible_line() -> None:
    major, minor, patch = API_VERSION.split(".")
    assert major == "1"
    assert (int(minor), int(patch)) >= (1, 0)


def test_released_operations_are_never_removed() -> None:
    assert {name for name, *_ in OPERATIONS} >= RELEASED_1_0_0_OPERATIONS


def test_new_request_fields_stay_optional_for_existing_clients() -> None:
    for schema_name in ("SessionRequest", "InputRequest"):
        assert "reasoning_effort" in SCHEMAS[schema_name]["properties"]
        assert "reasoning_effort" not in SCHEMAS[schema_name]["required"]
