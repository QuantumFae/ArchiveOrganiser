"""
SQLite-backed scan index for large (1TB+) libraries.

Keeps file metadata on disk instead of one giant in-memory list during scan.
Hash results are cached by (path, size, mtime) so re-runs skip unchanged files.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from typing import Iterable, Iterator, Optional

from models import FileInfo


SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    size INTEGER NOT NULL,
    modified REAL NOT NULL,
    category TEXT NOT NULL,
    source_root TEXT NOT NULL DEFAULT '',
    archive_container TEXT,
    archive_member TEXT,
    zip_crc INTEGER,
    is_junk INTEGER NOT NULL DEFAULT 0,
    hash_value TEXT,
    visual_hash TEXT,
    content_fingerprint TEXT
);
CREATE INDEX IF NOT EXISTS idx_files_size ON files(size);
CREATE INDEX IF NOT EXISTS idx_files_hash ON files(hash_value);
CREATE INDEX IF NOT EXISTS idx_files_category ON files(category);

CREATE TABLE IF NOT EXISTS hash_cache (
    path TEXT NOT NULL,
    size INTEGER NOT NULL,
    modified REAL NOT NULL,
    hash_value TEXT NOT NULL,
    PRIMARY KEY (path, size, modified)
);
"""


class ScanStore:
    """Temporary SQLite database for one scan session."""

    def __init__(self, path: Optional[Path] = None) -> None:
        if path is None:
            fd, name = tempfile.mkstemp(prefix="archive_organiser_scan_", suffix=".sqlite")
            import os

            os.close(fd)
            path = Path(name)
            self._temp = True
        else:
            self._temp = False
        self.path = path
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self._insert_batch: list[tuple] = []
        self._batch_size = 400

    def close(self) -> None:
        self.flush()
        try:
            self.conn.close()
        except Exception:
            pass
        if self._temp:
            try:
                self.path.unlink(missing_ok=True)
                wal = Path(str(self.path) + "-wal")
                shm = Path(str(self.path) + "-shm")
                wal.unlink(missing_ok=True)
                shm.unlink(missing_ok=True)
            except OSError:
                pass

    def flush(self) -> None:
        if not self._insert_batch:
            return
        self.conn.executemany(
            """
            INSERT INTO files (
                path, size, modified, category, source_root,
                archive_container, archive_member, zip_crc, is_junk,
                hash_value, visual_hash, content_fingerprint
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            self._insert_batch,
        )
        self.conn.commit()
        self._insert_batch.clear()

    def insert_file(self, info: FileInfo) -> None:
        self._insert_batch.append(
            (
                str(info.path),
                info.size,
                info.modified,
                info.category,
                info.source_root,
                str(info.archive_container) if info.archive_container else None,
                info.archive_member,
                info.zip_crc,
                1 if info.is_junk_location else 0,
                info.hash_value,
                info.visual_hash,
                info.content_fingerprint,
            )
        )
        if len(self._insert_batch) >= self._batch_size:
            self.flush()

    def count(self) -> int:
        self.flush()
        row = self.conn.execute("SELECT COUNT(*) AS n FROM files").fetchone()
        return int(row["n"]) if row else 0

    def total_bytes(self) -> int:
        self.flush()
        row = self.conn.execute("SELECT COALESCE(SUM(size), 0) AS n FROM files").fetchone()
        return int(row["n"]) if row else 0

    def category_counts(self) -> dict[str, int]:
        self.flush()
        out: dict[str, int] = {}
        for row in self.conn.execute(
            "SELECT category, COUNT(*) AS n FROM files GROUP BY category"
        ):
            out[row["category"]] = int(row["n"])
        return out

    def disk_file_count(self) -> int:
        self.flush()
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM files WHERE archive_container IS NULL"
        ).fetchone()
        return int(row["n"]) if row else 0

    def sample_paths(self, limit: int = 40) -> list[FileInfo]:
        self.flush()
        rows = self.conn.execute(
            "SELECT * FROM files ORDER BY id LIMIT ?", (limit,)
        ).fetchall()
        return [self._row_to_info(r) for r in rows]

    def load_all_files(self) -> list[FileInfo]:
        self.flush()
        rows = self.conn.execute("SELECT * FROM files ORDER BY id").fetchall()
        return [self._row_to_info(r) for r in rows]

    def load_files_by_ids(self, ids: Iterable[int]) -> list[FileInfo]:
        self.flush()
        id_list = list(ids)
        if not id_list:
            return []
        out: list[FileInfo] = []
        # SQLite variable limit — chunk
        for i in range(0, len(id_list), 400):
            chunk = id_list[i : i + 400]
            placeholders = ",".join("?" * len(chunk))
            rows = self.conn.execute(
                f"SELECT * FROM files WHERE id IN ({placeholders}) ORDER BY id",
                chunk,
            ).fetchall()
            out.extend(self._row_to_info(r) for r in rows)
        return out

    def iter_same_size_groups(self) -> Iterator[tuple[int, list[int]]]:
        """Yield (size, [row ids]) for sizes that appear more than once."""
        self.flush()
        for row in self.conn.execute(
            """
            SELECT size, GROUP_CONCAT(id) AS ids, COUNT(*) AS n
            FROM files
            GROUP BY size
            HAVING n > 1
            """
        ):
            ids = [int(x) for x in str(row["ids"]).split(",") if x]
            yield int(row["size"]), ids

    def update_hash(self, file_id: int, hash_value: Optional[str]) -> None:
        self.conn.execute(
            "UPDATE files SET hash_value = ? WHERE id = ?",
            (hash_value, file_id),
        )

    def commit(self) -> None:
        self.conn.commit()

    def cached_hash(self, path: str, size: int, modified: float) -> Optional[str]:
        row = self.conn.execute(
            """
            SELECT hash_value FROM hash_cache
            WHERE path = ? AND size = ? AND ABS(modified - ?) < 0.0001
            """,
            (path, size, modified),
        ).fetchone()
        return row["hash_value"] if row else None

    def store_hash_cache(self, path: str, size: int, modified: float, hash_value: str) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO hash_cache (path, size, modified, hash_value)
            VALUES (?, ?, ?, ?)
            """,
            (path, size, modified, hash_value),
        )

    @staticmethod
    def _row_to_info(row: sqlite3.Row) -> FileInfo:
        container = Path(row["archive_container"]) if row["archive_container"] else None
        info = FileInfo(
            path=Path(row["path"]),
            size=int(row["size"]),
            modified=float(row["modified"]),
            category=row["category"],
            hash_value=row["hash_value"],
            source_root=row["source_root"] or "",
            archive_container=container,
            archive_member=row["archive_member"],
            zip_crc=row["zip_crc"],
            is_junk_location=bool(row["is_junk"]),
            visual_hash=row["visual_hash"],
            content_fingerprint=row["content_fingerprint"],
            store_id=int(row["id"]),
        )
        return info
