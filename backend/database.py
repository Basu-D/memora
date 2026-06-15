"""
SQLAlchemy database setup, models, and CRUD helpers.
Connection string is the only thing that needs to change to move from SQLite to PostgreSQL.

Schema migrations are managed by Alembic (see alembic/).
init_db() still calls Base.metadata.create_all for fresh databases, and keeps a
safe-ALTER list as a fallback for deployments that don't run Alembic.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    create_engine,
    Column, String, Text, DateTime, Boolean,
    Enum as SAEnum, ForeignKey, Index,
    text,
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session

from config import settings


engine = create_engine(
    settings.db_url,
    connect_args={"check_same_thread": False} if settings.db_url.startswith("sqlite") else {},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class OutputType(str, enum.Enum):
    DETAILED = "detailed"
    MOM = "mom"
    QUICK_SUMMARY = "quick_summary"
    ACTION_ITEMS = "action_items"


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


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id                        = Column(String(36),  primary_key=True, default=lambda: str(uuid.uuid4()))
    email                     = Column(String(256), nullable=False, unique=True)
    display_name              = Column(String(256), nullable=True)
    webex_host_id             = Column(String(256), nullable=True)
    confluence_space_key      = Column(String(64),  nullable=True)
    confluence_parent_page_id = Column(String(64),  nullable=True)
    created_at                = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at                = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    __table_args__ = (
        Index("ix_users_email",         "email",         unique=True),
        Index("ix_users_webex_host_id", "webex_host_id", unique=False),
    )


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    filename = Column(String(512), nullable=False)
    storage_path = Column(String(512), nullable=True)   # relative path inside uploads/
    source_url = Column(String(2048), nullable=True)    # set for URL-submitted jobs
    status = Column(SAEnum(JobStatus), nullable=False, default=JobStatus.UPLOADED)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)
    output_type = Column(String(32), nullable=False, default="detailed")
    publish_to_confluence = Column(Boolean, nullable=False, default=True)
    custom_instructions = Column(Text, nullable=True)
    # §4.5 — user-selected Confluence destination
    confluence_space_key = Column(String(64), nullable=True)
    confluence_parent_page_id = Column(String(64), nullable=True)
    confluence_page_title = Column(String(512), nullable=True)
    # §4.3 — optional meeting context
    context_text = Column(Text, nullable=True)
    confluence_reference_url = Column(String(2048), nullable=True)
    # §4.4 — screenshot capture (stub)
    screenshots_enabled = Column(Boolean, nullable=False, default=False)
    error_message = Column(Text, nullable=True)
    confluence_url = Column(String(2048), nullable=True)
    result_json = Column(Text, nullable=True)
    publish_failed = Column(Boolean, nullable=False, default=False)
    # User association
    user_id    = Column(String(36), ForeignKey("users.id"), nullable=True)
    host_email = Column(String(256), nullable=True)   # raw email from webhook for traceability

    __table_args__ = (
        Index("ix_jobs_user_id", "user_id", unique=False),
    )

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
            "user_id": self.user_id,
            "host_email": self.host_email,
        }


# ---------------------------------------------------------------------------
# CRUD — jobs
# ---------------------------------------------------------------------------

def create_job(
    db: Session,
    filename: str,
    storage_path: str | None = None,
    source_url: str | None = None,
    output_type: str = "detailed",
    publish_to_confluence: bool = True,
    custom_instructions: str | None = None,
    confluence_space_key: str | None = None,
    confluence_parent_page_id: str | None = None,
    confluence_page_title: str | None = None,
    context_text: str | None = None,
    confluence_reference_url: str | None = None,
    screenshots_enabled: bool = False,
    user_id: str | None = None,
    host_email: str | None = None,
) -> "Job":
    """Insert a new Job row with status UPLOADED and return it."""
    job = Job(
        filename=filename,
        storage_path=storage_path,
        source_url=source_url,
        output_type=output_type,
        publish_to_confluence=publish_to_confluence,
        custom_instructions=custom_instructions,
        confluence_space_key=confluence_space_key,
        confluence_parent_page_id=confluence_parent_page_id,
        confluence_page_title=confluence_page_title,
        context_text=context_text,
        confluence_reference_url=confluence_reference_url,
        screenshots_enabled=screenshots_enabled,
        user_id=user_id,
        host_email=host_email,
    )
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
    publish_failed: bool | None = None,
) -> "Job":
    """
    Transition a job to a new status and optionally set result fields.

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
    if publish_failed is not None:
        job.publish_failed = publish_failed

    db.commit()
    db.refresh(job)
    return job


def assign_job_to_user(db: Session, job_id: str, user_id: str) -> "Job":
    """Link an existing job to a user. No-ops gracefully if already linked."""
    job = get_job(db, job_id)
    job.user_id = user_id
    job.updated_at = _now()
    db.commit()
    db.refresh(job)
    return job


def list_jobs(db: Session) -> list["Job"]:
    """Return all jobs ordered by created_at descending."""
    return db.query(Job).order_by(Job.created_at.desc()).all()


def get_job(db: Session, job_id: str) -> "Job":
    """
    Fetch a job by its UUID.

    Raises:
        ValueError: If no job with the given ID exists.
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if job is None:
        raise ValueError(f"Job not found: {job_id}")
    return job


# ---------------------------------------------------------------------------
# CRUD — users
# ---------------------------------------------------------------------------

def get_or_create_user_by_email(db: Session, email: str) -> User:
    """
    Look up a User by email; insert one if it doesn't exist yet.
    Safe to call on every inbound webhook — only creates on first occurrence.
    """
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        user = User(email=email)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def get_user_by_email(db: Session, email: str) -> User | None:
    """Return the User with the given email, or None."""
    return db.query(User).filter(User.email == email).first()


def get_user_by_webex_host_id(db: Session, host_id: str) -> User | None:
    """Return the User whose webex_host_id matches, or None."""
    return db.query(User).filter(User.webex_host_id == host_id).first()


def list_jobs_by_user(db: Session, user_id: str) -> list[Job]:
    """Return all jobs for a given user ordered by created_at descending."""
    return db.query(Job).filter(Job.user_id == user_id).order_by(Job.created_at.desc()).all()


def update_user_preferences(
    db: Session,
    user_id: str,
    space_key: str | None,
    parent_page_id: str | None,
) -> User:
    """
    Persist a user's preferred Confluence destination.

    Raises:
        ValueError: If no user with the given ID exists.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise ValueError(f"User not found: {user_id}")
    user.confluence_space_key = space_key
    user.confluence_parent_page_id = parent_page_id
    user.updated_at = _now()
    db.commit()
    db.refresh(user)
    return user


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
    """
    Create all tables for a fresh database, then apply safe ALTER TABLE
    fallbacks for existing deployments that are not managed by Alembic.

    For proper schema versioning, run: alembic upgrade head
    """
    Base.metadata.create_all(bind=engine)
    _migrations = [
        # Original columns added after initial deploy
        "ALTER TABLE jobs ADD COLUMN source_url VARCHAR(2048)",
        "ALTER TABLE jobs ADD COLUMN output_type VARCHAR(32) NOT NULL DEFAULT 'detailed'",
        "ALTER TABLE jobs ADD COLUMN publish_to_confluence BOOLEAN NOT NULL DEFAULT 1",
        "ALTER TABLE jobs ADD COLUMN custom_instructions TEXT",
        # §4.5 — Confluence destination
        "ALTER TABLE jobs ADD COLUMN confluence_space_key VARCHAR(64)",
        "ALTER TABLE jobs ADD COLUMN confluence_parent_page_id VARCHAR(64)",
        "ALTER TABLE jobs ADD COLUMN confluence_page_title VARCHAR(512)",
        # §4.3 — context input
        "ALTER TABLE jobs ADD COLUMN context_text TEXT",
        "ALTER TABLE jobs ADD COLUMN confluence_reference_url VARCHAR(2048)",
        # §4.4 — screenshot stub
        "ALTER TABLE jobs ADD COLUMN screenshots_enabled BOOLEAN NOT NULL DEFAULT 0",
        # publish_failed — set when Confluence publish fails but extraction succeeded
        "ALTER TABLE jobs ADD COLUMN publish_failed BOOLEAN NOT NULL DEFAULT 0",
        # User association (Alembic migration: 0001_add_users_job_user_fields)
        "ALTER TABLE jobs ADD COLUMN user_id VARCHAR(36)",
        "ALTER TABLE jobs ADD COLUMN host_email VARCHAR(256)",
    ]
    with engine.connect() as conn:
        for sql in _migrations:
            try:
                conn.execute(text(sql))
                conn.commit()
            except Exception:
                pass  # column already exists
