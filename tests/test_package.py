import local_agent_runtime as lar
from local_agent_runtime.api_contract import API_VERSION


def test_package_version_is_available() -> None:
    assert lar.__version__ == "0.1.2"
    assert lar.RuntimeService.__module__ == "local_agent_runtime.service"
    assert lar.EmbeddingService.__module__ == "local_agent_runtime.embeddings"
    assert callable(lar.build_runtime)
    assert callable(lar.create_app)


def test_released_api_version_is_stable() -> None:
    assert API_VERSION == "1.0.0"
