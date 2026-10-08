"""Opaque credential digests and authenticated encryption."""
import hashlib
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Vault:
    def __init__(self, directory):
        path = Path(directory) / 'secret.key'
        supplied = os.environ.get('HBASK_ADMIN_SECRET_KEY')
        if supplied:
            key = supplied.encode()
        elif path.exists():
            key = path.read_bytes()
        else:
            key = Fernet.generate_key()
            # Exclusive creation, never overwrite an existing encryption key.
            try:
                with path.open('xb') as file:
                    file.write(key)
                path.chmod(0o600)
            except FileExistsError:
                key = path.read_bytes()
        self.cipher = Fernet(key)

    def seal(self, value):
        return self.cipher.encrypt(value.encode()).decode() if value else ''

    def open(self, value):
        try:
            return self.cipher.decrypt(value.encode()).decode() if value else ''
        except InvalidToken as exc:
            raise ValueError('管理数据无法解密，请恢复原加密密钥，不要删除 secret.key') from exc
