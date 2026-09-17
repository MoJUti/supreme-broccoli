"""Safely register a local deployment package and a connection-information file."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import sys
import tarfile
import uuid
import zipfile
from contextlib import closing
from datetime import datetime, timezone

import yaml


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("DEPLOY_AGENT_DATA_DIR", ROOT / "deploy-agent-data")).resolve()
DATABASE = DATA_DIR / "state.sqlite"
MAX_ENTRIES = 10_000
MAX_UNCOMPRESSED_BYTES = 20 * 1024**3
MAX_MANIFEST_BYTES = 512 * 1024
COMPOSE_NAMES = {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}

ALIASES = {
    "project": "project",
    "project_name": "project",
    "项目": "project",
    "项目名称": "project",
    "host": "host",
    "server": "host",
    "ssh_host": "host",
    "服务器": "host",
    "服务器地址": "host",
    "目标服务器": "host",
    "ssh_user": "ssh_user",
    "server_user": "ssh_user",
    "服务器用户": "ssh_user",
    "ssh_port": "ssh_port",
    "vpn_type": "vpn_type",
    "vpn_client": "vpn_type",
    "vpn类型": "vpn_type",
    "vpn_profile": "vpn_profile",
    "vpn_config": "vpn_profile",
    "vpn配置": "vpn_profile",
    "vpn_portal": "vpn_portal",
    "vpn_address": "vpn_portal",
    "vpn地址": "vpn_portal",
    "vpn_username": "vpn_username",
    "vpn_user": "vpn_username",
    "vpn账号": "vpn_username",
    "vpn_password": "vpn_password",
    "vpn密码": "vpn_password",
    "ssh_password": "ssh_password",
    "服务器密码": "ssh_password",
    "ssh_key": "ssh_key",
    "服务器密钥": "ssh_key",
}
SECRET_KEYS = {"vpn_password", "ssh_password"}
MANIFEST_NAMES = {
    "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml",
    "dockerfile", "package.json", "requirements.txt", "pyproject.toml",
    "pom.xml", "build.gradle", "build.gradle.kts", "go.mod",
    "application.yml", "application.yaml", "application.properties",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_connection_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise ValueError("连接信息文件不存在或不是普通文件")
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("连接信息文件超过 1 MiB；请提供单个文本配置文件")
    raw = path.read_text(encoding="utf-8-sig")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None

    pairs: dict[str, object] = {}
    if isinstance(parsed, dict):
        for key, value in parsed.items():
            if isinstance(value, dict):
                for child_key, child_value in value.items():
                    pairs[f"{key}_{child_key}"] = child_value
                    pairs.setdefault(child_key, child_value)
            else:
                pairs[key] = value
    else:
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", ";", "[")):
                continue
            match = re.match(r"^([^:=：]+?)\s*[:=：]\s*(.*?)\s*$", stripped)
            if match:
                pairs[match.group(1).strip()] = match.group(2).strip()

    normalized: dict[str, str] = {}
    for key, value in pairs.items():
        canonical = ALIASES.get(str(key).strip().lower().replace(" ", "_"))
        if canonical and isinstance(value, (str, int)) and str(value).strip():
            normalized[canonical] = str(value).strip()
    if not normalized.get("host"):
        raise ValueError("连接信息文件中未识别到目标服务器地址；不会猜测目标")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", normalized["host"]):
        raise ValueError("目标服务器地址格式不安全或不明确")
    if normalized["host"].startswith("-"):
        raise ValueError("目标服务器地址不能以选项前缀开头")
    if normalized.get("ssh_user") and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", normalized["ssh_user"]):
        raise ValueError("服务器用户名称格式不安全")
    if normalized.get("ssh_port"):
        try:
            port = int(normalized["ssh_port"])
        except ValueError as error:
            raise ValueError("SSH 端口必须是数字") from error
        if not 1 <= port <= 65535:
            raise ValueError("SSH 端口超出范围")
    return normalized


def discover_vpn(connection: dict[str, str], file_path: Path) -> dict[str, object]:
    configured = connection.get("vpn_profile")
    if configured:
        profile = Path(configured)
        if not profile.is_absolute():
            profile = file_path.parent / profile
        profile = profile.resolve()
        return {"kind": connection.get("vpn_type", "unknown"), "profile_found": profile.is_file(),
                "profile_path": str(profile) if profile.is_file() else None,
                "portal_present": bool(connection.get("vpn_portal"))}

    candidates = sorted(p for p in file_path.parent.iterdir()
                        if p.is_file() and p.suffix.lower() in {".ovpn", ".vpn"})
    return {"kind": connection.get("vpn_type", "unknown"),
            "profile_found": len(candidates) == 1,
            "profile_path": str(candidates[0].resolve()) if len(candidates) == 1 else None,
            "profile_candidates": len(candidates),
            "portal_present": bool(connection.get("vpn_portal"))}


def inspect_package(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise ValueError("目前只支持单个 ZIP、TAR 或压缩 TAR 部署包")
    entries: list[dict[str, object]] = []
    total_uncompressed = 0
    compose_documents: list[tuple[str, bytes]] = []
    archive_type: str

    if zipfile.is_zipfile(path):
        archive_type = "zip"
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > MAX_ENTRIES:
                raise ValueError("部署包文件条目过多，停止分析")
            for member in members:
                if member.is_dir():
                    continue
                name, size = member.filename, member.file_size
                total_uncompressed += size
                entries.append({"path": name, "bytes": size})
                if PurePosixPath(name).name.lower() in COMPOSE_NAMES and size <= MAX_MANIFEST_BYTES:
                    compose_documents.append((name, archive.read(member)))
    elif tarfile.is_tarfile(path):
        archive_type = "tar"
        with tarfile.open(path, "r:*") as archive:
            for index, member in enumerate(archive):
                if index >= MAX_ENTRIES:
                    raise ValueError("部署包文件条目过多，停止分析")
                if not member.isfile():
                    continue
                total_uncompressed += member.size
                entries.append({"path": member.name, "bytes": member.size})
                if PurePosixPath(member.name).name.lower() in COMPOSE_NAMES and member.size <= MAX_MANIFEST_BYTES:
                    stream = archive.extractfile(member)
                    if stream is not None:
                        compose_documents.append((member.name, stream.read(MAX_MANIFEST_BYTES + 1)))
    else:
        raise ValueError("不支持的部署包格式；目前只接受 ZIP 或 TAR")

    if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
        raise ValueError("部署包展开体积超过 20 GiB，停止分析")
    manifest_paths = [entry["path"] for entry in entries
                      if PurePosixPath(str(entry["path"])).name.lower() in MANIFEST_NAMES]
    compose_services: list[dict[str, object]] = []
    compose_parse_failed: list[str] = []
    for name, content in compose_documents[:20]:
        try:
            document = yaml.safe_load(content.decode("utf-8-sig"))
        except (UnicodeError, yaml.YAMLError):
            compose_parse_failed.append(name)
            continue
        if not isinstance(document, dict) or not isinstance(document.get("services"), dict):
            compose_parse_failed.append(name)
            continue
        for service_name, config in list(document["services"].items())[:100]:
            if not isinstance(config, dict):
                continue
            dependencies = config.get("depends_on", [])
            if isinstance(dependencies, dict):
                dependencies = list(dependencies)
            if not isinstance(dependencies, list):
                dependencies = []
            environment = config.get("environment", {})
            if isinstance(environment, dict):
                environment_names = list(environment)
            elif isinstance(environment, list):
                environment_names = [str(item).split("=", 1)[0] for item in environment]
            else:
                environment_names = []
            ports = config.get("ports", [])
            if not isinstance(ports, list):
                ports = []
            compose_services.append({
                "compose_path": name,
                "service": str(service_name),
                "ports": [str(port)[:120] for port in ports[:30]],
                "depends_on": [str(dependency) for dependency in dependencies[:30]],
                "environment_names": [str(key) for key in environment_names[:100]],
            })
    return {"archive_type": archive_type, "entry_count": len(entries),
            "compressed_bytes": path.stat().st_size,
            "uncompressed_bytes": total_uncompressed,
            "manifest_paths": manifest_paths[:100],
            "compose_services": compose_services,
            "compose_parse_failed": compose_parse_failed,
            "entries": entries[:1000],
            "entries_truncated": len(entries) > 1000}


def register(package: Path, connection_file: Path) -> dict[str, object]:
    if not DATABASE.is_file():
        raise RuntimeError("任务数据库不存在；先运行 python storage/init_storage.py")
    connection = parse_connection_file(connection_file)
    vpn = discover_vpn(connection, connection_file)
    inventory = inspect_package(package)
    package_sha = digest_file(package)
    project_name = connection.get("project", package.stem)
    ssh_target = connection["host"]
    if connection.get("ssh_user"):
        ssh_target = f"{connection['ssh_user']}@{ssh_target}"
    project_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"deploy-agent:project:{project_name}"))
    host_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"deploy-agent:host:{ssh_target}"))
    artifact_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"deploy-agent:artifact:{package_sha}"))
    task_id = str(uuid.uuid4())
    timestamp = now()

    package_relative = f"packages/{package_sha}{''.join(package.suffixes).lower()}"
    target = DATA_DIR / package_relative
    if not target.exists():
        temporary = target.with_name(f".{target.name}.{task_id}.tmp")
        shutil.copyfile(package, temporary)
        if digest_file(temporary) != package_sha:
            temporary.unlink(missing_ok=True)
            raise RuntimeError("部署包复制后哈希不一致")
        os.replace(temporary, target)
    elif digest_file(target) != package_sha:
        raise RuntimeError("产物库中同名文件哈希不一致")

    safe_inventory = {"task_id": task_id, "project": project_name,
                      "target": ssh_target, "package_sha256": package_sha,
                      "package_name": package.name, "inventory": inventory,
                      "vpn_discovery": vpn,
                      "credentials_present": sorted(key for key in SECRET_KEYS if connection.get(key)),
                      "connection_file_sha256": digest_file(connection_file),
                      "generated_at": timestamp,
                      "remote_check": "not_started"}
    evidence_relative = f"evidence/{task_id}/package-inventory.json"
    evidence_path = DATA_DIR / evidence_relative
    evidence_path.parent.mkdir(parents=True, exist_ok=False)
    evidence_path.write_text(json.dumps(safe_inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    evidence_sha = digest_file(evidence_path)

    with closing(sqlite3.connect(DATABASE)) as db:
        db.execute("PRAGMA foreign_keys = ON")
        with db:
            db.execute("INSERT OR IGNORE INTO projects VALUES (?, ?, ?, ?)",
                       (project_id, project_name, None, timestamp))
            db.execute("INSERT OR IGNORE INTO hosts VALUES (?, ?, ?, ?, ?, ?)",
                       (host_id, ssh_target, ssh_target, vpn.get("profile_path"), None, timestamp))
            db.execute("INSERT OR IGNORE INTO artifacts VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (artifact_id, project_id, package.name, package_sha, package_relative,
                        package.stat().st_size, timestamp))
            db.execute("INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (task_id, project_id, host_id, artifact_id, "deploy", "prechecking",
                        None, None, timestamp, timestamp))
            db.execute("INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (str(uuid.uuid4()), task_id, evidence_relative, evidence_sha,
                        evidence_path.stat().st_size, 1, timestamp))
            db.execute("INSERT INTO task_events (task_id, step_id, event_type, payload_json, occurred_at) VALUES (?, ?, ?, ?, ?)",
                       (task_id, "local-package", "package_registered",
                        json.dumps({"sha256": package_sha, "evidence": evidence_relative,
                                    "remote_check": "not_started"}), timestamp))
    return {"task_id": task_id, "inventory_path": str(evidence_path),
            "package_sha256": package_sha, "target": ssh_target,
            "vpn_profile_found": vpn["profile_found"],
            "remote_check": "not_started"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Register package and connection file for read-only precheck")
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--connection", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = register(args.package.resolve(), args.connection.resolve())
    except (OSError, ValueError, RuntimeError, sqlite3.Error, tarfile.TarError, zipfile.BadZipFile) as error:
        # Do not print the source line: it may contain credentials.
        print(f"准备失败：{type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
