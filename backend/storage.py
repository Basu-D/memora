"""
Storage abstraction layer.
Swap LocalStorage for an S3Storage implementation without touching callers.
"""

import shutil
from abc import ABC, abstractmethod
from pathlib import Path


class StorageBackend(ABC):
    """Abstract interface for file storage. Every concrete backend must implement these."""

    @abstractmethod
    def save(self, source_path: str, destination_key: str) -> str:
        """
        Persist a file from source_path to the storage backend.

        Args:
            source_path: Temporary path of the uploaded file.
            destination_key: Logical key / relative path under which to store the file.

        Returns:
            The storage key (or S3 object key) that can be passed back to retrieve().
        """
        ...

    @abstractmethod
    def retrieve(self, key: str) -> str:
        """
        Return a local filesystem path for the file identified by key.

        For local storage this is trivial. For S3 this would download to a temp file first.

        Args:
            key: The key returned by save().

        Returns:
            Absolute path to a readable local file.
        """
        ...

    @abstractmethod
    def delete(self, key: str) -> None:
        """
        Remove the stored file.

        Args:
            key: The key returned by save().
        """
        ...

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Return True if the key exists in the backend."""
        ...


class LocalStorage(StorageBackend):
    """
    Stores files on the local filesystem under a configurable root directory.
    Production replacement: swap this class for an S3Storage that uses boto3.
    """

    def __init__(self, root_dir: str = "uploads") -> None:
        self.root = Path(root_dir)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, source_path: str, destination_key: str) -> str:
        """Copy source file into the storage root under destination_key."""
        dest = self.root / destination_key
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, dest)
        return destination_key

    def retrieve(self, key: str) -> str:
        """Return the absolute path for a stored file."""
        path = self.root / key
        if not path.exists():
            raise FileNotFoundError(f"No file found for storage key: {key}")
        return str(path.resolve())

    def delete(self, key: str) -> None:
        """Delete the file at key, silently ignoring missing files."""
        path = self.root / key
        path.unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return (self.root / key).exists()


# TODO: Add S3Storage class here that accepts bucket_name and uses boto3.
# It should implement the same StorageBackend interface.
# Example signature:
#   class S3Storage(StorageBackend):
#       def __init__(self, bucket_name: str) -> None: ...

def get_storage() -> StorageBackend:
    """
    Factory that returns the active storage backend.
    Switch to S3Storage here based on an env variable when ready.
    """
    # TODO: read STORAGE_BACKEND env var; return S3Storage if "s3"
    from config import settings
    return LocalStorage(root_dir=settings.upload_dir)
