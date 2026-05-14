# src/orchestra/core/vault.py
"""Encrypted secret storage using Fernet (AES-128-CBC + HMAC-SHA256)."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet


class Vault:
    """Store and retrieve secrets encrypted on disk.

    Secrets are kept in a single Fernet-encrypted JSON blob at `vault_path`.
    The Fernet key lives at `key_path` (auto-generated, chmod 0600).
    """

    VAULT_REF_PREFIX = "vault:"

    def __init__(self, vault_path: Path, key_path: Path):
        self._vault_path = vault_path
        self._key_path = key_path
        self._fernet: Fernet | None = None
        self._secrets: dict[str, str] = {}
        self._load()

    def _ensure_fernet(self) -> Fernet:
        if self._fernet:
            return self._fernet
        if self._key_path.exists():
            key = self._key_path.read_bytes().strip()
        else:
            key = Fernet.generate_key()
            self._key_path.parent.mkdir(parents=True, exist_ok=True)
            self._key_path.write_bytes(key)
            os.chmod(self._key_path, stat.S_IRUSR | stat.S_IWUSR)
        self._fernet = Fernet(key)
        return self._fernet

    def _load(self) -> None:
        if not self._vault_path.exists():
            self._secrets = {}
            return
        f = self._ensure_fernet()
        encrypted = self._vault_path.read_bytes()
        decrypted = f.decrypt(encrypted)
        self._secrets = json.loads(decrypted)

    def _save(self) -> None:
        f = self._ensure_fernet()
        plaintext = json.dumps(self._secrets).encode()
        self._vault_path.parent.mkdir(parents=True, exist_ok=True)
        self._vault_path.write_bytes(f.encrypt(plaintext))

    def store(self, name: str, secret: str) -> None:
        self._secrets[name] = secret
        self._save()

    def delete(self, name: str) -> None:
        self._secrets.pop(name, None)
        self._save()

    def list_keys(self) -> list[str]:
        return list(self._secrets.keys())

    def resolve(self, value: str) -> str:
        if not value.startswith(self.VAULT_REF_PREFIX):
            return value
        name = value[len(self.VAULT_REF_PREFIX):]
        if name not in self._secrets:
            raise KeyError(f"Vault key not found: {name}")
        return self._secrets[name]
