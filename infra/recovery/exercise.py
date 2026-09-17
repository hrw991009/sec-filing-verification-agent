"""Opt-in, project-scoped container recovery exercise. No shared-service mutations."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx2
import psycopg
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from psycopg.rows import dict_row
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[2]
REVISION = "dd8ca1672ea92e4111b0d261c379120bb170effd"
SERVICES = ("postgres", "redis", "minio", "minio-init", "etcd", "milvus", "elasticsearch")
PORTS = {"postgres": 25432, "redis": 26379, "minio": 29000, "milvus": 29530, "elasticsearch": 29200}
REQUIRED_CHECKS = (
    "fresh-migration-and-authenticated-ingestion",
    "worker-sigkill-checkpoint-resume",
    "redis-container-outage",
    "minio-container-outage",
    "elasticsearch-container-outage",
    "milvus-container-outage",
    "dead-letter-owner-replay",
    "whole-derived-index-loss-rebuild",
    "duplicate-real-broker-delivery",
    "populated-database-backup-restore",
    "live-model-research-worker-checkpoint-resume",
)


def verification_summary(report: dict[str, Any]) -> dict[str, Any]:
    """Keep failures visible while reporting every required check's latest verified attempt."""
    latest = {row["name"]: row for row in report["scenarios"]}
    checks = {name: latest.get(name) for name in REQUIRED_CHECKS}
    return {
        "checks_passed": all(row is not None and row["passed"] is True for row in checks.values()),
        "release_accepted": False,
        "logical_check_count": len(REQUIRED_CHECKS),
        "source_commit": report["source_commit"],
        "source_patch_sha256": report["source_patch_sha256"],
        "application_image_id": report["application_image_id"],
        "checks": checks,
        "historical_failed_attempts": [row for row in report["scenarios"] if not row["passed"]],
        "single_failure_free_batch": all(row["passed"] for row in report["scenarios"]),
    }


def build_image(directory: Path) -> None:
    """Freeze tracked source plus an explicit patch; never label a patch as a clean commit."""
    exercise = Exercise(directory)
    target = ROOT / ".data/recovery-images" / exercise.project
    target.mkdir(parents=True, exist_ok=False)
    context = target / "source"
    context.mkdir()
    paths = ("pyproject.toml", "uv.lock", "README.md", "apps/backend", "evals/fixtures/sec")
    revision = command("git", "rev-parse", "HEAD").decode().strip()
    archive = command("git", "archive", "--format=tar", revision, *paths)
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        source.extractall(context, filter="data")
    patch = command("git", "diff", "--binary", revision, "--", *paths)
    (target / "source.patch").write_bytes(patch)
    changed = command("git", "diff", "--name-only", "-z", revision, "--", *paths)
    untracked = command("git", "ls-files", "--others", "--exclude-standard", "-z", "--", *paths)
    additions = {}
    for name in untracked.decode().split("\0"):
        if name:
            additions[name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
    for name in (changed + untracked).decode().split("\0"):
        if not name:
            continue
        source_path, destination = ROOT / name, context / name
        if source_path.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination)
        elif destination.is_file():
            destination.unlink()
    patch_identity = {
        "tracked_diff_sha256": hashlib.sha256(patch).hexdigest(),
        "added_files": additions,
    }
    patch_hash = hashlib.sha256(json.dumps(patch_identity, sort_keys=True).encode()).hexdigest()
    write_json(target / "patch-identity.json", patch_identity)
    tag = f"sec-filing-verification-agent:recovery-{revision[:7]}-{patch_hash[:12]}"
    command(
        "docker",
        "build",
        "-f",
        str(ROOT / "infra/recovery/Dockerfile"),
        "--build-arg",
        f"SOURCE_REVISION={revision}",
        "--build-arg",
        f"SOURCE_PATCH_SHA256={patch_hash}",
        "-t",
        tag,
        str(context),
        timeout=1800,
    )
    inspected = json.loads(command("docker", "image", "inspect", tag))[0]
    identity = {
        "source_commit": revision,
        "source_patch_sha256": patch_hash,
        "source_is_clean_commit": not bool(patch or additions),
        "application_tag": tag,
        "application_image_id": inspected["Id"],
        "dockerfile_sha256": hashlib.sha256(
            (ROOT / "infra/recovery/Dockerfile").read_bytes()
        ).hexdigest(),
    }
    write_json(target / "build.json", identity)
    sys.stdout.write(json.dumps(identity, indent=2) + "\n")
    exercise.client.close()


def command(*argv: str, timeout: int = 600, input_bytes: bytes | None = None) -> bytes:
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell; mutation targets validated
        argv, cwd=ROOT, input=input_bytes, capture_output=True, timeout=timeout
    )
    if result.returncode:
        # Compose config/env may contain credentials; never print arbitrary stdout.
        raise RuntimeError(
            f"{argv[0]} {argv[1]} failed ({result.returncode}): "
            + result.stderr.decode(errors="replace")[-2000:]
        )
    return result.stdout


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")


def b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def index_identity_digest(records: list[dict[str, Any]]) -> str:
    """SQL row order is not part of persisted index identity."""
    return hashlib.sha256(
        json.dumps(
            sorted(records, key=lambda row: str(row["id"])), sort_keys=True, default=str
        ).encode()
    ).hexdigest()


class Exercise:
    def __init__(self, directory: Path) -> None:
        self.directory = directory.resolve()
        if not self.directory.is_relative_to(ROOT / ".data" / "isolated-recovery"):
            raise ValueError("Evidence directory must be inside .data/isolated-recovery")
        self.project = self.directory.name
        if not re.fullmatch(r"iip-recovery-[0-9a-f-]+", self.project):
            raise ValueError("Invalid isolated project identity")
        self.compose_file = self.directory / "compose.json"
        self.report: dict[str, Any] = {
            "source_commit": REVISION,
            "project": self.project,
            "release_accepted": False,
            "scenarios": [],
        }
        self.client = httpx2.Client(base_url="http://127.0.0.1:28000", timeout=45, trust_env=False)

    def compose(self, *args: str, timeout: int = 300) -> bytes:
        return command(
            "docker",
            "compose",
            "-p",
            self.project,
            "-f",
            str(self.compose_file),
            *args,
            timeout=timeout,
        )

    def prepare(self, image: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=False)
        password = secrets.token_urlsafe(30)
        signing = secrets.token_bytes(32)
        public = (
            Ed25519PrivateKey.from_private_bytes(signing)
            .public_key()
            .public_bytes(Encoding.Raw, PublicFormat.Raw)
        )
        env = {
            "APP_ENVIRONMENT": "test",
            "POSTGRES_HOST": "postgres",
            "POSTGRES_PORT": "5432",
            "POSTGRES_DB": "iip_recovery",
            "POSTGRES_USER": "recovery",
            "POSTGRES_PASSWORD": password,
            "REDIS_HOST": "redis",
            "REDIS_PORT": "6379",
            "REDIS_PASSWORD": password,
            "MINIO_ROOT_USER": "recovery",
            "MINIO_ROOT_PASSWORD": password,
            "MINIO_ENDPOINT": "minio:9000",
            "MINIO_ACCESS_KEY": "recovery",
            "MINIO_SECRET_KEY": password,
            "MINIO_BUCKET": "recovery-private",
            "MINIO_REGION": "us-east-1",
            "MINIO_SECURE": "false",
            "MILVUS_ENDPOINT": "http://milvus:19530",
            "ELASTICSEARCH_ENDPOINT": "http://elasticsearch:9200",
            "BROWSER_TRUSTED_ORIGINS_JSON": '["https://localhost:28000"]',
            "ACCESS_TOKEN_CURRENT_KID": "recovery",
            "ACCESS_TOKEN_PRIVATE_KEY_B64": b64(signing),
            "ACCESS_TOKEN_PUBLIC_KEYS_JSON": json.dumps({"recovery": b64(public)}),
            "JOB_LEASE_SECONDS": "15",
            "JOB_HEARTBEAT_SECONDS": "3",
            "JOB_UNSTARTED_TIMEOUT_SECONDS": "60",
            "KNOWLEDGE_INDEX_TIMEOUT_SECONDS": "30",
            "PGADMIN_DEFAULT_EMAIL": "recovery@example.test",
            "PGADMIN_DEFAULT_PASSWORD": password,
        }
        for name in (
            "REFRESH_TOKEN_HMAC_KEY_B64",
            "CSRF_TOKEN_HMAC_KEY_B64",
            "DEVICE_TOKEN_HMAC_KEY_B64",
            "LOGIN_RATE_LIMIT_HMAC_KEY_B64",
            "REFRESH_RECOVERY_AEAD_KEY_B64",
        ):
            env[name] = b64(secrets.token_bytes(32))
        env_file = self.directory / "runtime.env"
        env_file.write_text(
            "".join(f"{key}={value}\n" for key, value in env.items()), encoding="utf-8"
        )
        config = json.loads(
            command(
                "docker",
                "compose",
                "--profile",
                "vector",
                "--profile",
                "search",
                "--env-file",
                str(env_file),
                "-f",
                "infra/compose/compose.yaml",
                "config",
                "--format",
                "json",
            )
        )
        selected = {name: config["services"][name] for name in SERVICES}
        images = {}
        for name, service in selected.items():
            inspected = json.loads(command("docker", "image", "inspect", service["image"]))[0]
            service["image"] = inspected["RepoDigests"][0]
            images[name] = {"digest": service["image"], "id": inspected["Id"]}
            service.pop("profiles", None)
            service["restart"] = "no"
            if "ports" in service:
                service["ports"] = [
                    {
                        "target": service["ports"][0]["target"],
                        "published": str(PORTS[name]),
                        "host_ip": "127.0.0.1",
                        "protocol": "tcp",
                    }
                ]
        app = json.loads(command("docker", "image", "inspect", image))[0]
        if app["Config"]["Labels"]["org.opencontainers.image.revision"] != REVISION:
            raise ValueError("Application revision mismatch")
        for name, module in {
            "api": None,
            "worker": "celery_app",
            "dispatcher": "dispatcher",
            "reconciler": "reconciler",
        }.items():
            selected[name] = {
                "image": app["Id"],
                "pull_policy": "never",
                "env_file": [str(env_file)],
                "networks": {"default": None},
                "restart": "no",
            }
            if module:
                selected[name]["command"] = ["python", "-m", f"industry_platform.workers.{module}"]
                if name == "worker":
                    selected[name]["command"] += ["--pool=solo", "--concurrency=1"]
            else:
                selected[name]["ports"] = ["127.0.0.1:28000:8000"]
        volume_names = {
            v["source"]
            for s in selected.values()
            for v in s.get("volumes", [])
            if v["type"] == "volume"
        }
        # Discard resolved development project names from `compose config`.
        write_json(
            self.compose_file,
            {
                "name": self.project,
                "services": selected,
                "volumes": {name: {} for name in volume_names},
                "networks": {"default": {}},
            },
        )
        self.report.update(
            {
                "images": images,
                "application_image_id": app["Id"],
                "application_tag": image,
                "source_patch_sha256": app["Config"]["Labels"].get(
                    "org.opencontainers.image.source-patch-sha256", "none"
                ),
                "dockerfile_sha256": hashlib.sha256(
                    (ROOT / "infra/recovery/Dockerfile").read_bytes()
                ).hexdigest(),
                "started_at": datetime.now(UTC).isoformat(),
            }
        )
        write_json(self.directory / "environment.json", self.report)
        # Credentials remain local under ignored .data, never in the evidence report.

    def load(self) -> None:
        self.report = json.loads((self.directory / "environment.json").read_text())
        self.env = dict(
            line.split("=", 1) for line in (self.directory / "runtime.env").read_text().splitlines()
        )

    def rows(self, query: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with psycopg.connect(
            host="127.0.0.1",
            port=25432,
            dbname="iip_recovery",
            user="recovery",
            password=self.env["POSTGRES_PASSWORD"],
            row_factory=dict_row,
        ) as db:
            db.execute("SET TRANSACTION READ ONLY")
            return list(db.execute(query, args))

    def wait(self, label: str, predicate: Any, timeout: float = 180) -> Any:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            time.sleep(0.5)
        raise TimeoutError(label)

    def container(self, service: str) -> str:
        identity = self.compose("ps", "-a", "-q", service).decode().strip()
        if not identity or "\n" in identity:
            raise ValueError("Expected exactly one scoped container")
        labels = json.loads(command("docker", "inspect", identity))[0]["Config"]["Labels"]
        if (
            labels["com.docker.compose.project"] != self.project
            or labels["com.docker.compose.service"] != service
        ):
            raise ValueError("Container is not owned by this exercise")
        return identity

    def start(self) -> None:
        self.compose("up", "-d", "--wait", *(s for s in SERVICES if s != "minio-init"), timeout=300)
        self.compose("run", "--rm", "--no-deps", "minio-init")
        migration = self.compose(
            "run",
            "--rm",
            "--no-deps",
            "api",
            "python",
            "-m",
            "alembic",
            "-c",
            "apps/backend/alembic.ini",
            "upgrade",
            "head",
        )
        (self.directory / "migration.log").write_bytes(migration)
        self.compose("up", "-d", "api", "worker", "dispatcher", "reconciler")
        self.wait_api()
        sys.stdout.write("isolated environment ready\n")
        sys.stdout.flush()

    def wait_api(self) -> None:
        def healthy() -> bool:
            try:
                return self.client.get("/health/live").status_code == 200
            except httpx2.RequestError:
                return False

        self.wait("API startup", healthy)

    def api(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.client.request(method, "/api/v1" + path, **kwargs)
        if not response.is_success:
            raise RuntimeError(f"API {method} {path}: {response.status_code} {response.text[:700]}")
        return response.json()

    def authenticate(self) -> None:
        account_file = self.directory / "account.json"
        if account_file.exists():
            account = json.loads(account_file.read_text())
        else:
            account = {
                "email": f"recovery-{uuid4().hex}@example.com",
                "password": secrets.token_urlsafe(24),
            }
            registered = self.api("POST", "/auth/register", json=account)
            account["workspace_id"] = registered["workspace"]["id"]
            account["user_id"] = registered["user"]["id"]
            write_json(account_file, account)
        login = self.api("POST", "/auth/login", json={k: account[k] for k in ("email", "password")})
        self.client.headers["Authorization"] = "Bearer " + login["access_token"]
        self.workspace = account["workspace_id"]
        self.path = f"/workspaces/{self.workspace}/knowledge-bases"
        if "knowledge_base_id" not in account:
            account["knowledge_base_id"] = self.api(
                "POST", self.path, json={"name": "Isolated recovery"}
            )["id"]
            write_json(account_file, account)
        self.kb = account["knowledge_base_id"]

    def submit(self, title: str, pages: int = 1) -> dict[str, Any]:
        pdf = io.BytesIO()
        document = canvas.Canvas(pdf)
        for page in range(pages):
            document.drawString(50, 780, f"Isolated recovery {title} page {page} {uuid4().hex}")
            document.drawString(50, 750, "Revenue 2023: 100 USD. Revenue 2024: 120 USD.")
            document.showPage()
        document.save()
        content = pdf.getvalue()
        upload = self.api(
            "POST",
            f"{self.path}/{self.kb}/uploads/presign",
            json={
                "original_name": "recovery.pdf",
                "declared_media_type": "application/pdf",
                "expected_size": len(content),
                "expected_sha256": hashlib.sha256(content).hexdigest(),
            },
        )
        # POST policies sign form fields, not the host; the bucket is only exposed on loopback.
        response = httpx2.post(
            "http://127.0.0.1:29000/" + self.env["MINIO_BUCKET"],
            data=upload["fields"],
            files={"file": ("recovery.pdf", content, "application/pdf")},
            trust_env=False,
            timeout=30,
        )
        response.raise_for_status()
        receipt = self.api(
            "POST",
            f"{self.path}/{self.kb}/uploads/{upload['file']['id']}/complete",
            headers={"Idempotency-Key": uuid4().hex},
            json={"title": title},
        )
        return {
            "job_id": receipt["job"]["id"],
            "version_id": receipt["version"]["id"],
            "document_id": receipt["document"]["id"],
            "source_sha256": hashlib.sha256(content).hexdigest(),
        }

    def job(self, job_id: str) -> dict[str, Any]:
        return self.rows(
            "SELECT id,status,attempt_count,max_attempts,dispatch_generation,"
            "fencing_token,stage_name,"
            "lease_expires_at,last_error_code FROM jobs WHERE id=%s",
            (job_id,),
        )[0]

    def wait_job(self, job_id: str, states: tuple[str, ...], timeout: int = 240) -> dict[str, Any]:
        def reached() -> Any:
            value = self.job(job_id)
            if value["status"] in states:
                return value
            if value["status"] in ("failed", "cancelled", "dead_letter"):
                raise RuntimeError(f"Unexpected terminal job: {value}")
            return None

        return self.wait(f"Job {job_id} -> {states}", reached, timeout)

    def checkpoint_ids(self, version_id: str) -> list[dict[str, Any]]:
        return self.rows(
            "SELECT id,stage,attempt_count,fencing_token FROM ingestion_checkpoints "
            "WHERE document_version_id=%s ORDER BY stage_sequence",
            (version_id,),
        )

    def verify_document(self, receipt: dict[str, Any]) -> dict[str, Any]:
        final = self.wait_job(receipt["job_id"], ("succeeded",))
        detail = self.api("GET", f"{self.path}/{self.kb}/documents/{receipt['document_id']}")
        version = self.rows(
            "SELECT status,file_object_id FROM document_versions WHERE id=%s",
            (receipt["version_id"],),
        )[0]
        if version["status"] != "ready":
            raise AssertionError("Both indexes must be ready")
        source = self.rows(
            "SELECT source_sha256,safe_sha256,object_key FROM file_objects WHERE id=%s",
            (version["file_object_id"],),
        )[0]
        if source["source_sha256"] != receipt["source_sha256"]:
            raise AssertionError("Original source digest changed")
        from minio import Minio

        store = Minio(
            "127.0.0.1:29000",
            access_key=self.env["MINIO_ACCESS_KEY"],
            secret_key=self.env["MINIO_SECRET_KEY"],
            secure=False,
        )
        stored = store.get_object(self.env["MINIO_BUCKET"], source["object_key"])
        try:
            if hashlib.sha256(stored.read()).hexdigest() != source["safe_sha256"]:
                raise AssertionError("Stored source object changed")
        finally:
            stored.close()
            stored.release_conn()
        indexes = self.rows(
            "SELECT id,chunk_id,external_id,kind FROM document_index_records "
            "WHERE document_version_id=%s",
            (receipt["version_id"],),
        )
        ids = sorted({row["external_id"] for row in indexes})
        if not ids or len(indexes) != len(ids) * 2:
            raise AssertionError("Missing or duplicate index records")
        for identity in ids:
            vector = httpx2.post(
                "http://127.0.0.1:29530/v2/vectordb/entities/get",
                trust_env=False,
                json={
                    "collectionName": "knowledge_chunks_v1",
                    "id": identity,
                    "consistencyLevel": "Strong",
                },
            ).json()
            lexical = httpx2.get(
                f"http://127.0.0.1:29200/knowledge_chunks_v1/_doc/{identity}", trust_env=False
            )
            if vector.get("code") != 0 or len(vector["data"]) != 1 or lexical.status_code != 200:
                raise AssertionError("Restored index identity is missing")
        return {
            "final_job": final,
            "checkpoints": self.checkpoint_ids(receipt["version_id"]),
            "index_identity_sha256": index_identity_digest(indexes),
            "api_detail_visible": bool(detail),
        }

    def fault(self, service: str, action: str) -> None:
        identity = self.container(service)
        if action not in ("stop", "kill", "pause", "unpause", "start"):
            raise ValueError("Unsupported fault")
        command("docker", action, identity)
        self.report.setdefault("faults", []).append(
            {
                "service": service,
                "container_id": identity,
                "action": action,
                "at": datetime.now(UTC).isoformat(),
            }
        )

    def recover_service(self, service: str) -> None:
        self.container(service)
        self.compose("up", "-d", "--wait", service, timeout=240)

    def scenario(self, name: str, operation: Any) -> None:
        start = time.monotonic()
        item: dict[str, Any] = {
            "name": name,
            "started_at": datetime.now(UTC).isoformat(),
            "passed": False,
        }
        self.report["scenarios"].append(item)
        self.report["checks_passed"] = False
        try:
            item["evidence"] = operation()
            item["passed"] = True
            sys.stdout.write(f"PASS {name}\n")
            sys.stdout.flush()
        except Exception as error:
            item["error"] = str(error)
            raise
        finally:
            item["duration_seconds"] = round(time.monotonic() - start, 3)
            self.report["checks_passed"] = all(row["passed"] for row in self.report["scenarios"])
            self.report["completed_at"] = datetime.now(UTC).isoformat()
            write_json(self.directory / "report.json", self.report)

    def outage(self, service: str) -> Any:
        self.fault("worker", "stop")
        if service == "redis":
            self.fault("dispatcher", "stop")
        receipt = self.submit(f"{service} outage")
        self.fault(service, "stop")
        try:
            if service == "redis":
                self.fault("dispatcher", "start")
            self.fault("worker", "start")
            if service == "redis":
                pending = self.wait(
                    "outbox retry",
                    lambda: self.rows(
                        "SELECT id,status,attempt_count FROM outbox_events WHERE source_job_id=%s "
                        "AND status='pending' AND attempt_count>0",
                        (receipt["job_id"],),
                    ),
                )
                during: Any = {"job": self.job(receipt["job_id"]), "pending_outbox": pending}
            else:
                during = self.wait_job(receipt["job_id"], ("retry_wait",))
                version = self.rows(
                    "SELECT status,ready_at FROM document_versions WHERE id=%s",
                    (receipt["version_id"],),
                )[0]
                if version["ready_at"] is not None:
                    raise AssertionError("Faulty ingestion became ready")
            checkpoints = self.checkpoint_ids(receipt["version_id"])
        finally:
            self.recover_service(service)
        verified = self.verify_document(receipt)
        retained = {str(row["id"]) for row in checkpoints}
        if not retained.issubset({str(row["id"]) for row in verified["checkpoints"]}):
            raise AssertionError("Successful checkpoints were rewritten")
        return {**receipt, "during_fault": during, "retained_checkpoints": checkpoints, **verified}

    def worker_kill(self) -> Any:
        self.fault("worker", "stop")
        receipt = self.submit("worker SIGKILL", pages=48)
        self.fault("worker", "start")
        before = self.wait(
            "durable checkpoint before SIGKILL", lambda: self.checkpoint_ids(receipt["version_id"])
        )
        lease = self.job(receipt["job_id"])
        if lease["status"] != "running":
            raise AssertionError("Missed the active Worker fault-injection window")
        self.fault("worker", "kill")
        # The real reconciler, not direct SQL, detects the expired lease.
        retry = self.wait_job(receipt["job_id"], ("retry_wait", "dispatched"), timeout=120)
        self.fault("worker", "start")
        final = self.verify_document(receipt)
        if final["final_job"]["fencing_token"] <= lease["fencing_token"]:
            raise AssertionError("Replacement worker did not advance its fence")
        if final["checkpoints"][: len(before)] != before:
            raise AssertionError("Committed stage identities did not survive")
        return {**receipt, "old_lease": lease, "reconciled_job": retry, **final}

    def dead_letter(self) -> Any:
        self.fault("worker", "stop")
        receipt = self.submit("authorized replay")
        self.fault("elasticsearch", "stop")
        try:
            self.fault("worker", "start")
            dead = self.wait_job(receipt["job_id"], ("dead_letter",), timeout=420)
        finally:
            self.recover_service("elasticsearch")
        path = f"/workspaces/{self.workspace}/jobs/{receipt['job_id']}/replay"
        headers = {"Idempotency-Key": uuid4().hex}
        payload = {
            "expected_dispatch_generation": dead["dispatch_generation"],
            "additional_attempts": 1,
        }
        accepted = self.api("POST", path, headers=headers, json=payload)
        repeated = self.api("POST", path, headers=headers, json=payload)
        if accepted["outbox_event_id"] != repeated["outbox_event_id"] or repeated["created"]:
            raise AssertionError("Replay generated duplicate delivery")
        verified = self.verify_document(receipt)
        terminal = self.rows(
            "SELECT event_type,dispatch_generation FROM job_events WHERE job_id=%s "
            "AND event_type IN ('succeeded','dead_letter') ORDER BY dispatch_generation",
            (receipt["job_id"],),
        )
        if [row["event_type"] for row in terminal] != ["dead_letter", "succeeded"]:
            raise AssertionError("Terminal history not preserved")
        return {
            **receipt,
            "dead_letter": dead,
            "replay": accepted,
            "terminal_history": terminal,
            **verified,
        }

    def index_loss(self) -> Any:
        receipt = self.submit("derived index loss")
        before = self.verify_document(receipt)
        for service in ("milvus", "elasticsearch"):
            self.container(service)
        response = httpx2.post(
            "http://127.0.0.1:29530/v2/vectordb/collections/drop",
            trust_env=False,
            json={"collectionName": "knowledge_chunks_v1"},
        )
        if response.json().get("code") != 0:
            raise AssertionError("Collection drop failed")
        httpx2.delete(
            "http://127.0.0.1:29200/knowledge_chunks_v1", trust_env=False
        ).raise_for_status()
        # This whole collection belongs to the disposable project; reconstruct every ready version.
        versions = self.rows(
            "SELECT id FROM document_versions WHERE status='ready' ORDER BY created_at"
        )
        jobs = []
        for version in versions:
            headers = {"Idempotency-Key": uuid4().hex}
            path = f"{self.path}/{self.kb}/versions/{version['id']}/rebuild-indexes"
            first = self.api("POST", path, headers=headers)
            repeated = self.api("POST", path, headers=headers)
            if first["job_id"] != repeated["job_id"] or repeated["created"]:
                raise AssertionError("Index submission not idempotent")
            self.wait_job(first["job_id"], ("succeeded",))
            jobs.append(first)
        after = self.verify_document(receipt)
        if before["index_identity_sha256"] != after["index_identity_sha256"]:
            raise AssertionError("Reconstruction changed original identities")
        return {
            **receipt,
            "rebuild_jobs": jobs,
            "all_ready_versions_rebuilt": len(versions),
            **after,
        }

    def backup_restore(self) -> Any:
        from industry_platform.core.config import Settings
        from industry_platform.modules.evaluation.release_recovery_exercise import _backup_restore

        actors = ("api", "worker", "dispatcher", "reconciler")
        for actor in actors:
            self.fault(actor, "stop")
        previous = os.environ.get("RECOVERY_POSTGRES_CONTAINER")
        try:
            os.environ["RECOVERY_POSTGRES_CONTAINER"] = self.container("postgres")
            settings = Settings(
                _env_file=self.directory / "runtime.env",
                postgres_host="127.0.0.1",
                postgres_port=25432,
            )
            result = _backup_restore(settings)
            result["alembic_revision"] = self.rows("SELECT version_num FROM alembic_version")[0][
                "version_num"
            ]
            return result
        finally:
            if previous is None:
                os.environ.pop("RECOVERY_POSTGRES_CONTAINER", None)
            else:
                os.environ["RECOVERY_POSTGRES_CONTAINER"] = previous
            for actor in actors:
                self.fault(actor, "start")

    def duplicate_delivery(self) -> Any:
        from industry_platform.core.config import Settings
        from industry_platform.modules.jobs.domain import CELERY_JOB_DISPATCH_TASK_NAME
        from industry_platform.workers.celery_app import create_celery_app

        receipt = self.submit("duplicate broker delivery")
        self.verify_document(receipt)
        original = self.job(receipt["job_id"])
        event_count = self.rows(
            "SELECT count(*) AS n FROM job_events WHERE job_id=%s", (receipt["job_id"],)
        )[0]["n"]
        dispatch = self.rows(
            "SELECT payload FROM outbox_events WHERE source_job_id=%s "
            "ORDER BY job_dispatch_generation LIMIT 1",
            (receipt["job_id"],),
        )[0]
        settings = Settings(
            _env_file=self.directory / "runtime.env", redis_host="127.0.0.1", redis_port=26379
        )
        app = create_celery_app(settings)
        try:
            for _ in range(2):
                app.send_task(
                    CELERY_JOB_DISPATCH_TASK_NAME,
                    kwargs=dispatch["payload"],
                    task_id=dispatch["payload"]["outbox_id"],
                    queue="ingestion",
                    routing_key="ingestion",
                    serializer="json",
                    ignore_result=True,
                    retry=False,
                )
            time.sleep(3)
            if not app.control.inspect(timeout=5).ping():
                raise AssertionError("Worker did not respond after duplicate delivery")
        finally:
            app.close()
        if self.job(receipt["job_id"]) != original:
            raise AssertionError("Duplicate broker message mutated a completed Job")
        if (
            self.rows("SELECT count(*) AS n FROM job_events WHERE job_id=%s", (receipt["job_id"],))[
                0
            ]["n"]
            != event_count
        ):
            raise AssertionError("Duplicate broker message produced new lifecycle events")
        return {
            **receipt,
            "published_duplicates": 2,
            "job_unchanged": True,
            "job_event_count": event_count,
            **self.verify_document(receipt),
        }

    def configure_model(self) -> None:
        from industry_platform.core.config import Settings

        configured = Settings(_env_file=ROOT / ".env")
        if configured.agent_model_route is None or configured.agent_model_provider_api_key is None:
            raise ValueError("A configured live model is required for the Research exercise")
        values = {
            "AGENT_MODEL_PROVIDER_BASE_URL": str(configured.agent_model_provider_base_url),
            "AGENT_MODEL_PROVIDER_API_KEY": (
                configured.agent_model_provider_api_key.get_secret_value()
            ),
            "AGENT_MODEL_ROUTE_JSON": configured.agent_model_route.model_dump_json(),
            "AGENT_MODEL_REQUEST_TIMEOUT_SECONDS": "120",
            "SEC_CONTROLLED_SOURCE_MANIFEST_PATH": (
                "evals/fixtures/sec/sec-browser-v1/manifest.json"
            ),
        }
        for key, value in {
            "SEC_USER_AGENT_APP": configured.sec_user_agent_app,
            "SEC_USER_AGENT_EMAIL": configured.sec_user_agent_email,
        }.items():
            if value:
                values[key] = value
        self.env.update(values)
        (self.directory / "runtime.env").write_text(
            "".join(f"{key}={value}\n" for key, value in self.env.items()), encoding="utf-8"
        )
        self.report["research_dependencies"] = {
            "model": configured.agent_model_route.model,
            "model_kind": "live_provider",
            "sec_source": "frozen_sec_browser_fixture_not_live_sec",
        }
        write_json(self.directory / "environment.json", self.report)
        self.compose("up", "-d", "--force-recreate", "api", "worker", "dispatcher", "reconciler")
        self.wait_api()

    def research_resume(self) -> Any:
        self.authenticate()
        accession = "0000320193-23-000106"
        cutoff = "2023-12-01T00:00:00Z"
        path = f"/workspaces/{self.workspace}/disclosures"
        self.api(
            "GET",
            path + "/filings",
            params={
                "cik": "0000320193",
                "forms": "10-K",
                "report_period_start": "2023-01-01",
                "report_period_end": "2023-12-31",
                "as_of": cutoff,
            },
        )
        imported = self.api(
            "POST",
            f"{path}/filings/{accession}/imports",
            json={"knowledge_base_id": self.kb, "as_of": cutoff},
        )
        self.wait_job(imported["ingestion_job_id"], ("succeeded",))
        self.fault("worker", "stop")
        accepted = self.api(
            "POST",
            f"/workspaces/{self.workspace}/research-runs",
            headers={"Idempotency-Key": uuid4().hex},
            json={
                "original_question": (
                    "核验 Apple 2023 财年净销售额, 给出金额和原文证据引用, 不创建监控或其他写入。"
                ),
                "confirmed_scope": ["仅限 Apple 2023 年 10-K 净销售额"],
                "completion_criteria": ["报告收入金额并保留证据引用"],
                "mode": "local",
                "knowledge_base_ids": [self.kb],
                "required_tool_names": ["sec.search_filing"],
                "financial_scope": {
                    "schema_version": 1,
                    "cik": "0000320193",
                    "accession": accession,
                    "form": "10-K",
                    "report_period": "2023-09-30",
                    "as_of": cutoff,
                    "unit": "USD",
                    "scale": 6,
                },
                "max_steps": 20,
                "max_total_tokens": 100000,
                "timeout_seconds": 600,
            },
        )
        self.fault("worker", "start")
        checkpoints = self.wait(
            "Research durable checkpoint",
            lambda: self.rows(
                "SELECT id,revision FROM agent_checkpoints WHERE run_id=%s ORDER BY revision",
                (accepted["agent_run_id"],),
            ),
            timeout=180,
        )
        interrupted_model = self.wait(
            "Research model request in flight",
            lambda: self.rows(
                "SELECT s.id,s.sequence FROM agent_steps s WHERE s.run_id=%s "
                "AND s.kind='model' AND s.status='running' AND EXISTS "
                "(SELECT 1 FROM agent_events e WHERE e.run_id=s.run_id "
                "AND e.event_type='agent.model.started' "
                "AND e.payload->>'step_id'=s.id::text)",
                (accepted["agent_run_id"],),
            ),
            timeout=180,
        )[0]
        old = self.job(accepted["job_id"])
        if old["status"] != "running":
            raise AssertionError("Research already ended before interruption")
        self.fault("worker", "kill")
        self.wait_job(accepted["job_id"], ("retry_wait", "dispatched"), timeout=120)
        self.fault("worker", "start")
        job = self.wait_job(accepted["job_id"], ("succeeded",), timeout=480)
        run = self.rows(
            "SELECT id,status,job_id FROM agent_runs WHERE id=%s", (accepted["agent_run_id"],)
        )[0]
        if run["status"] != "completed" or str(run["job_id"]) != accepted["job_id"]:
            raise AssertionError(f"Research did not complete the original Run: {run}")
        if job["fencing_token"] <= old["fencing_token"]:
            raise AssertionError("Research resumed without fencing the interrupted Worker")
        resumed = self.rows(
            "SELECT sequence,payload->>'resume_kind' AS kind FROM agent_events "
            "WHERE run_id=%s AND event_type='agent.run.resumed' ORDER BY sequence",
            (accepted["agent_run_id"],),
        )
        if len(resumed) != 1 or resumed[0]["kind"] != "recovery":
            raise AssertionError("Research must record exactly one checkpoint recovery event")
        settled_attempt = self.rows(
            "SELECT status,error_code FROM agent_steps WHERE id=%s",
            (interrupted_model["id"],),
        )[0]
        if settled_attempt != {"status": "failed", "error_code": "worker_interrupted"}:
            raise AssertionError("Interrupted model attempt was not durably settled")
        if self.rows(
            "SELECT id FROM agent_steps WHERE run_id=%s AND status='running'",
            (accepted["agent_run_id"],),
        ):
            raise AssertionError("Recovered Research left an orphan running Step")
        after = self.rows(
            "SELECT id,revision FROM agent_checkpoints WHERE run_id=%s ORDER BY revision",
            (accepted["agent_run_id"],),
        )
        if after[: len(checkpoints)] != checkpoints:
            raise AssertionError("Research checkpoint history was replaced")
        messages = self.rows(
            "SELECT id FROM conversation_messages WHERE agent_run_id=%s AND role='assistant'",
            (accepted["agent_run_id"],),
        )
        if len(messages) != 1:
            raise AssertionError("Research produced duplicate or missing final messages")
        result = self.api(
            "GET",
            f"/workspaces/{self.workspace}/research-runs/{accepted['research_run_id']}/result-view",
        )
        return {
            **accepted,
            "old_job": old,
            "final_job": job,
            "agent_run": run,
            "retained_checkpoints": checkpoints,
            "interrupted_model_step": interrupted_model,
            "settled_interrupted_attempt": settled_attempt,
            "final_checkpoint_count": len(after),
            "recovery_events": resumed,
            "assistant_message_count": len(messages),
            "result_sha256": hashlib.sha256(
                json.dumps(result, sort_keys=True).encode()
            ).hexdigest(),
        }

    def run(self) -> None:
        previous = self.directory / "report.json"
        if previous.exists():
            (self.directory / f"report-attempt-{time.time_ns()}.json").write_bytes(
                previous.read_bytes()
            )
        self.authenticate()
        self.scenario(
            "fresh-migration-and-authenticated-ingestion",
            lambda: self.verify_document(self.submit("baseline")),
        )
        self.scenario("worker-sigkill-checkpoint-resume", self.worker_kill)
        for service in ("redis", "minio", "elasticsearch", "milvus"):
            self.scenario(
                f"{service}-container-outage", lambda service=service: self.outage(service)
            )
        self.scenario("dead-letter-owner-replay", self.dead_letter)
        self.scenario("whole-derived-index-loss-rebuild", self.index_loss)
        self.scenario("duplicate-real-broker-delivery", self.duplicate_delivery)
        self.scenario("populated-database-backup-restore", self.backup_restore)
        self.report["completed_at"] = datetime.now(UTC).isoformat()
        self.report["checks_passed"] = all(row["passed"] for row in self.report["scenarios"])
        write_json(self.directory / "report.json", self.report)

    def collect(self) -> None:
        from industry_platform.modules.evaluation.release_recovery_executor import _redact

        facts = {
            "agent_runs": self.rows(
                "SELECT r.id,r.job_id,r.status,r.stop_reason,j.status AS job_status,"
                "j.last_error_code,j.dispatch_generation,j.fencing_token "
                "FROM agent_runs r JOIN jobs j ON j.id=r.job_id ORDER BY r.created_at"
            ),
            "checkpoints": self.rows(
                "SELECT id,run_id,revision,state->'payload'->>'node' AS node,"
                "state->'payload'->>'next_node' AS next_node FROM agent_checkpoints "
                "ORDER BY saved_at"
            ),
            "events": self.rows(
                "SELECT run_id,sequence,event_type,payload->>'error_code' AS error_code "
                "FROM agent_events ORDER BY occurred_at,sequence"
            ),
        }
        write_json(self.directory / "research-durable-facts.json", facts)
        for actor in ("api", "worker", "dispatcher", "reconciler"):
            container = self.container(actor)
            result = subprocess.run(  # noqa: S603 - validated container, fixed read-only command
                ("docker", "logs", "--tail", "2000", container),  # noqa: S607 - installed Docker CLI
                capture_output=True,
                timeout=30,
            )
            log = (result.stdout + result.stderr).decode(errors="replace")
            for name, value in self.env.items():
                if any(part in name for part in ("KEY", "PASSWORD", "SECRET")) and value:
                    log = log.replace(value, "[REDACTED]")
            (self.directory / f"{actor}.log").write_text(_redact(log), encoding="utf-8")
        manifest = {}
        for path in self.directory.iterdir():
            if path.name in {"runtime.env", "account.json", "compose.json", "artifacts.json"}:
                continue
            if path.is_file():
                manifest[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        write_json(self.directory / "artifacts.json", manifest)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
            "build",
            "prepare",
            "start",
            "exercise",
            "configure-model",
            "research",
            "backup",
            "summarize",
            "collect",
            "stop",
        ),
    )
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--image", default="sec-filing-verification-agent:recovery-dd8ca16")
    args = parser.parse_args()
    exercise = Exercise(args.directory)
    if args.action == "build":
        build_image(args.directory)
    elif args.action == "prepare":
        exercise.prepare(args.image)
    else:
        exercise.load()
        if args.action == "start":
            exercise.start()
        elif args.action == "configure-model":
            exercise.configure_model()
        elif args.action == "research":
            exercise.report = json.loads((exercise.directory / "report.json").read_text())
            exercise.scenario(
                "live-model-research-worker-checkpoint-resume", exercise.research_resume
            )
        elif args.action == "backup":
            exercise.report = json.loads((exercise.directory / "report.json").read_text())
            exercise.scenario("populated-database-backup-restore", exercise.backup_restore)
        elif args.action == "summarize":
            report_file = exercise.directory / "report.json"
            summary = verification_summary(json.loads(report_file.read_text()))
            summary["attempt_report_sha256"] = hashlib.sha256(report_file.read_bytes()).hexdigest()
            summary["environment_sha256"] = hashlib.sha256(
                (exercise.directory / "environment.json").read_bytes()
            ).hexdigest()
            write_json(exercise.directory / "verification-summary.json", summary)
            if not summary["checks_passed"]:
                raise AssertionError("One or more required recovery checks remain unverified")
        elif args.action == "collect":
            exercise.collect()
        elif args.action == "stop":
            for name in (*SERVICES, "api", "worker", "dispatcher", "reconciler"):
                if exercise.compose("ps", "-a", "-q", name).strip():
                    exercise.container(name)
            exercise.compose("stop")
        else:
            exercise.run()


if __name__ == "__main__":
    main()
