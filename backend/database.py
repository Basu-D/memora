"""
SQLAlchemy database setup and Job model.
Connection string is the only thing that needs to change to move from SQLite to PostgreSQL.
"""

import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, Column, String, Text, DateTime, Enum as SAEnum, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session

from config import settings


engine = create_engine(
    settings.database_url,
    # SQLite requires this; remove when switching to PostgreSQL
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class JobStatus(str, enum.Enum):
    UPLOADED = "uploaded"
    DOWNLOADING = "downloading"
    EXTRACTING_AUDIO = "extracting_audio"
    TRANSCRIBING = "transcribing"
    PROCESSING = "processing"
    PUBLISHING = "publishing"
    DONE = "done"
    FAILED = "failed"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    filename = Column(String(512), nullable=False)
    storage_path = Column(String(512), nullable=True)   # relative path inside uploads/
    source_url = Column(String(2048), nullable=True)    # set for URL-submitted jobs
    status = Column(SAEnum(JobStatus), nullable=False, default=JobStatus.UPLOADED)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)
    error_message = Column(Text, nullable=True)
    confluence_url = Column(String(2048), nullable=True)
    result_json = Column(Text, nullable=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "filename": self.filename,
            "status": self.status.value,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "error_message": self.error_message,
            "confluence_url": self.confluence_url,
            "result_json": self.result_json,
            "storage_path": self.storage_path,
        }


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def create_job(db: Session, filename: str, storage_path: str | None = None, source_url: str | None = None) -> Job:
    """
    Insert a new Job row with status UPLOADED and return it.

    Args:
        db: Active SQLAlchemy session.
        filename: Original name of the uploaded recording file.
        storage_path: Relative path of the saved file inside the uploads directory.

    Returns:
        The newly created and committed Job instance.
    """
    job = Job(filename=filename, storage_path=storage_path, source_url=source_url)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def update_job_status(
    db: Session,
    job_id: str,
    status: JobStatus,
    *,
    error_message: str | None = None,
    confluence_url: str | None = None,
    result_json: str | None = None,
) -> Job:
    """
    Transition a job to a new status and optionally set result fields.

    Args:
        db: Active SQLAlchemy session.
        job_id: UUID string of the job to update.
        status: The new JobStatus value.
        error_message: Set when transitioning to FAILED.
        confluence_url: Set when transitioning to DONE.
        result_json: JSON string of extracted meeting data; set when transitioning to DONE.

    Returns:
        The updated Job instance.

    Raises:
        ValueError: If no job with the given ID exists.
    """
    job = get_job(db, job_id)

    job.status = status
    job.updated_at = _now()

    if error_message is not None:
        job.error_message = error_message
    if confluence_url is not None:
        job.confluence_url = confluence_url
    if result_json is not None:
        job.result_json = result_json

    db.commit()
    db.refresh(job)
    return job


def get_job(db: Session, job_id: str) -> Job:
    """
    Fetch a job by its UUID.

    Args:
        db: Active SQLAlchemy session.
        job_id: UUID string to look up.

    Returns:
        The matching Job instance.

    Raises:
        ValueError: If no job with the given ID exists.
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if job is None:
        raise ValueError(f"Job not found: {job_id}")
    return job


# ---------------------------------------------------------------------------
# Session dependency + schema init
# ---------------------------------------------------------------------------

def get_db():
    """FastAPI dependency — yields a DB session and ensures it is closed."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all tables. Called once at application startup."""
    Base.metadata.create_all(bind=engine)
    # Safe migration: add source_url column if it doesn't exist yet (SQLite).
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE jobs ADD COLUMN source_url VARCHAR(2048)"))
            conn.commit()
        except Exception:
            pass  # column already exists
