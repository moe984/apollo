import os
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


def _load() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


_cfg = _load()


class Settings:
    # Redis (env var overrides config.yaml for Docker)
    redis_url: str = os.environ.get("REDIS_URL", _cfg["redis"]["url"])
    redis_key_prefix: str = _cfg["redis"]["key_prefix"]
    redis_ttl_minutes: int = _cfg["redis"]["ttl_minutes"]

    # Logging
    log_max_bytes: int = _cfg["logging"]["max_bytes"]
    log_backup_count: int = _cfg["logging"]["backup_count"]

    # STIX parsing
    stix_extension_key: str = _cfg["stix"]["extension_key"]
    stix_hash_algorithms: list[str] = _cfg["stix"]["hash_algorithms"]
    notable_field_mappings: dict[str, list[str]] = _cfg["stix"]["notable_field_mappings"]
    ioc_field_map: dict[str, str] = _cfg["stix"]["ioc_field_map"]

    # Dedup
    window_minutes: int = _cfg["dedup"]["window_minutes"]

    # Layer 1
    layer1_slots: list[str] = _cfg["dedup"]["layer1"]["slots"]
    layer1_confidence_threshold: float = _cfg["dedup"]["layer1"]["confidence_threshold"]
    layer1_confidence_buckets: list[str] = _cfg["dedup"]["layer1"]["confidence_buckets"]
    stix_equivalence_enabled: bool = _cfg["dedup"]["layer1"]["stix_equivalence"]["enabled"]
    stix_equivalence_threshold: float = _cfg["dedup"]["layer1"]["stix_equivalence"]["threshold"]

    # Layer 2
    layer2_similarity_threshold: float = _cfg["dedup"]["layer2"]["similarity_threshold"]



settings = Settings()
