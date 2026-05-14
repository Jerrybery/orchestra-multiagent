# tests/test_vault.py
"""Tests for the encrypted Vault."""

import pytest
from pathlib import Path
from orchestra.core.vault import Vault


@pytest.fixture
def vault(tmp_path):
    vault_path = tmp_path / "vault.enc"
    key_path = tmp_path / "vault.key"
    return Vault(vault_path, key_path)


class TestVault:
    def test_store_and_list(self, vault):
        vault.store("my-key", "my-secret")
        assert "my-key" in vault.list_keys()

    def test_resolve_vault_ref(self, vault):
        vault.store("api-key", "sk-ant-123")
        assert vault.resolve("vault:api-key") == "sk-ant-123"

    def test_resolve_plain_passthrough(self, vault):
        assert vault.resolve("plain-value") == "plain-value"

    def test_resolve_missing_key_raises(self, vault):
        with pytest.raises(KeyError):
            vault.resolve("vault:nonexistent")

    def test_delete(self, vault):
        vault.store("temp", "val")
        vault.delete("temp")
        assert "temp" not in vault.list_keys()

    def test_delete_nonexistent_is_noop(self, vault):
        vault.delete("nope")  # should not raise

    def test_persistence_across_instances(self, tmp_path):
        vault_path = tmp_path / "vault.enc"
        key_path = tmp_path / "vault.key"
        v1 = Vault(vault_path, key_path)
        v1.store("persistent", "secret-value")
        v2 = Vault(vault_path, key_path)
        assert v2.resolve("vault:persistent") == "secret-value"

    def test_key_file_permissions(self, vault):
        vault.store("x", "y")  # triggers key generation
        import os, stat
        mode = os.stat(vault._key_path).st_mode
        assert stat.S_IMODE(mode) == 0o600

    def test_empty_vault_list(self, vault):
        assert vault.list_keys() == []
