"""Static contracts for the local Docker Compose infrastructure."""

import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_PATH = REPOSITORY_ROOT / "infra" / "compose" / "compose.yaml"
OBSERVABILITY_ROOT = REPOSITORY_ROOT / "infra" / "observability"


def _service_blocks(compose: str) -> dict[str, str]:
    """Extract service blocks without adding a YAML dependency to the backend."""
    services: dict[str, list[str]] = {}
    current_service: str | None = None
    in_services = False

    for line in compose.splitlines():
        if line == "services:":
            in_services = True
            continue
        if in_services and line and not line.startswith(" "):
            break

        match = re.fullmatch(r"  ([a-z][a-z0-9_-]*):", line)
        if in_services and match:
            current_service = match.group(1)
            services[current_service] = []
        elif current_service is not None:
            services[current_service].append(line)

    return {name: "\n".join(lines) for name, lines in services.items()}


def _published_ports(service: str) -> list[str]:
    """Return the Compose short-syntax port entries for one service."""
    lines = service.splitlines()
    try:
        start = lines.index("    ports:") + 1
    except ValueError:
        return []

    ports: list[str] = []
    for line in lines[start:]:
        if not line.startswith("      - "):
            break
        ports.append(line.removeprefix("      - ").strip("\"'"))
    return ports


def test_compose_profiles_are_pinned_local_and_healthy() -> None:
    compose = COMPOSE_PATH.read_text(encoding="utf-8")
    services = _service_blocks(compose)
    expected = {
        "etcd": ("quay.io/coreos/etcd:v3.5.25", "vector"),
        "milvus": ("milvusdb/milvus:v3.0.0", "vector"),
        "elasticsearch": (
            "docker.elastic.co/elasticsearch/elasticsearch:9.5.1",
            "search",
        ),
        "jaeger": ("jaegertracing/jaeger:2.20.0", "observability"),
        "prometheus": ("prom/prometheus:v3.13.2", "observability"),
        "grafana": ("grafana/grafana:13.1.3", "observability"),
    }
    health_probes = {
        "etcd": 'etcdctl", "endpoint", "health',
        "milvus": "http://127.0.0.1:9091/healthz",
        "elasticsearch": "/_cluster/health?wait_for_status=yellow",
        "jaeger": "http://127.0.0.1:13133/status",
        "prometheus": "http://127.0.0.1:9090/-/healthy",
        "grafana": "http://127.0.0.1:3000/api/health",
    }

    for name, (image, profile) in expected.items():
        block = services[name]
        assert f"image: {image}" in block
        assert f'profiles: ["{profile}"]' in block
        assert "healthcheck:" in block
        assert health_probes[name] in block
        assert "restart: unless-stopped" in block

    image_references = re.findall(r"^    image: (\S+)$", compose, flags=re.MULTILINE)
    assert image_references
    assert all(":" in image.rsplit("/", maxsplit=1)[-1] for image in image_references)
    assert all(not image.endswith(":latest") for image in image_references)


def test_host_ports_and_stateful_data_are_explicit() -> None:
    compose = COMPOSE_PATH.read_text(encoding="utf-8")
    services = _service_blocks(compose)

    for name, block in services.items():
        assert all(port.startswith("127.0.0.1:") for port in _published_ports(block)), name

    expected_ports = {
        "postgres": ["127.0.0.1:15432:5432"],
        "redis": ["127.0.0.1:16379:6379"],
        "minio": ["127.0.0.1:19000:9000", "127.0.0.1:19001:9001"],
        "pgadmin": ["127.0.0.1:15080:80"],
        "redisinsight": ["127.0.0.1:15540:5540"],
        "milvus": ["127.0.0.1:19530:19530", "127.0.0.1:19091:9091"],
        "elasticsearch": ["127.0.0.1:19200:9200"],
        "jaeger": [
            "127.0.0.1:16686:16686",
            "127.0.0.1:14317:4317",
            "127.0.0.1:14318:4318",
        ],
        "prometheus": ["127.0.0.1:19090:9090"],
        "grafana": ["127.0.0.1:13000:3000"],
    }
    for name, ports in expected_ports.items():
        assert _published_ports(services[name]) == ports

    expected_mounts = {
        "postgres": "postgres_data:/var/lib/postgresql/data",
        "redis": "redis_data:/data",
        "minio": "minio_data:/data",
        "etcd": "etcd_data:/etcd",
        "milvus": "milvus_data:/var/lib/milvus",
        "elasticsearch": "elasticsearch_data:/usr/share/elasticsearch/data",
        "jaeger": "jaeger_data:/tmp",
        "prometheus": "prometheus_data:/prometheus",
        "grafana": "grafana_data:/var/lib/grafana",
    }
    for name, mount in expected_mounts.items():
        assert mount in services[name]
        assert f"  {mount.split(':', maxsplit=1)[0]}:" in compose


def test_profiles_reuse_minio_and_have_operational_configuration() -> None:
    compose = COMPOSE_PATH.read_text(encoding="utf-8")
    services = _service_blocks(compose)

    assert '--appendonly\n      - "yes"' in services["redis"]
    assert "--appendfsync\n      - everysec" in services["redis"]

    milvus = services["milvus"]
    assert "MINIO_ADDRESS: minio:9000" in milvus
    assert "MINIO_ACCESS_KEY_ID: ${MINIO_ROOT_USER}" in milvus
    assert "MINIO_SECRET_ACCESS_KEY: ${MINIO_ROOT_PASSWORD}" in milvus
    assert "minio:\n        condition: service_healthy" in milvus
    assert len(re.findall(r"^  minio:$", compose, flags=re.MULTILINE)) == 1

    prometheus = (OBSERVABILITY_ROOT / "prometheus" / "prometheus.yml").read_text(encoding="utf-8")
    datasource = (
        OBSERVABILITY_ROOT / "grafana" / "provisioning" / "datasources" / "prometheus.yml"
    ).read_text(encoding="utf-8")
    jaeger = (OBSERVABILITY_ROOT / "jaeger.yaml").read_text(encoding="utf-8")

    assert 'targets: ["jaeger:8888"]' in prometheus
    assert "url: http://prometheus:9090" in datasource
    assert "prometheusVersion: 3.13.2" in datasource
    assert "badger:" in jaeger
    assert "ephemeral: false" in jaeger
    assert "healthcheckv2:" in jaeger
    assert "JAEGER_LISTEN_HOST: 0.0.0.0" in services["jaeger"]
    for port in (13133, 16686, 4317, 4318):
        assert f'endpoint: "${{env:JAEGER_LISTEN_HOST:-localhost}}:{port}"' in jaeger
