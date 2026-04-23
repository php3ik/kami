"""SQLAlchemy models for FactStore — spec §2.2.

All tables use temporal versioning via valid_until_tick IS NULL for current rows.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


class Entity(Base):
    """Registry of everything that exists in the simulation."""

    __tablename__ = "entities"

    entity_id = Column(String, primary_key=True)
    kind = Column(
        String, nullable=False
    )  # agent, object, kami, animal, plant, vehicle, document, channel
    canonical_name = Column(String, nullable=False)
    archetype = Column(JSON, default=dict)
    created_at_tick = Column(Integer, nullable=False, default=0)
    created_by_event = Column(String, nullable=True)

    __table_args__ = (Index("ix_entities_kind", "kind"),)


class Location(Base):
    """Temporal location tracking — one current row per entity."""

    __tablename__ = "locations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    entity_id = Column(String, ForeignKey("entities.entity_id"), nullable=False)
    kami_id = Column(String, ForeignKey("entities.entity_id"), nullable=False)
    container_id = Column(String, ForeignKey("entities.entity_id"), nullable=True)
    since_tick = Column(Integer, nullable=False)
    valid_until_tick = Column(Integer, nullable=True)  # NULL = current

    __table_args__ = (
        Index("ix_locations_entity_current", "entity_id", "valid_until_tick"),
        Index("ix_locations_kami_current", "kami_id", "valid_until_tick"),
    )


class Ownership(Base):
    """Temporal ownership tracking."""

    __tablename__ = "ownership"

    id = Column(Integer, primary_key=True, autoincrement=True)
    entity_id = Column(String, ForeignKey("entities.entity_id"), nullable=False)
    owner_id = Column(String, ForeignKey("entities.entity_id"), nullable=False)
    since_tick = Column(Integer, nullable=False)
    valid_until_tick = Column(Integer, nullable=True)

    __table_args__ = (
        Index("ix_ownership_entity_current", "entity_id", "valid_until_tick"),
    )


class PhysicalState(Base):
    """Temporal attribute store for entities."""

    __tablename__ = "physical_state"

    id = Column(Integer, primary_key=True, autoincrement=True)
    entity_id = Column(String, ForeignKey("entities.entity_id"), nullable=False)
    attribute = Column(String, nullable=False)
    value = Column(JSON, nullable=False)
    since_tick = Column(Integer, nullable=False)
    valid_until_tick = Column(Integer, nullable=True)

    __table_args__ = (
        Index(
            "ix_physstate_entity_attr_current",
            "entity_id",
            "attribute",
            "valid_until_tick",
        ),
    )


VALID_ATTRIBUTES = {
    "integrity",
    "cleanliness",
    "temperature",
    "hp",
    "hunger",
    "fatigue",
    "locked",
    "mood",
    "open",
    "power",
    "capacity",
}


class Relation(Base):
    """Temporal relations between entities."""

    __tablename__ = "relations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    from_entity = Column(String, ForeignKey("entities.entity_id"), nullable=False)
    to_entity = Column(String, ForeignKey("entities.entity_id"), nullable=False)
    rel_type = Column(String, nullable=False)
    weight = Column(JSON, default=dict)
    since_tick = Column(Integer, nullable=False)
    valid_until_tick = Column(Integer, nullable=True)

    __table_args__ = (
        Index(
            "ix_relations_from_type_current",
            "from_entity",
            "rel_type",
            "valid_until_tick",
        ),
    )


VALID_REL_TYPES = {
    "knows",
    "trusts",
    "owes",
    "married_to",
    "employs",
    "fears",
    "has_contact_via",
    "lives_in",
    "works_at",
    "friends_with",
    "parent_of",
    "child_of",
    "sibling_of",
}


class Event(Base):
    """Append-only event log — source of truth for replay."""

    __tablename__ = "events"

    event_id = Column(String, primary_key=True)
    tick = Column(Integer, nullable=False)
    kami_id = Column(String, ForeignKey("entities.entity_id"), nullable=True)
    event_type = Column(String, nullable=False)
    participants = Column(JSON, default=list)
    payload = Column(JSON, default=dict)
    salience = Column(Float, default=0.5)
    narrative = Column(Text, default="")
    causes = Column(JSON, default=list)

    __table_args__ = (
        Index("ix_events_tick", "tick"),
        Index("ix_events_kami_tick", "kami_id", "tick"),
    )


class AgentBelief(Base):
    """Subjective belief store per agent — separate from canon."""

    __tablename__ = "agent_beliefs"

    belief_id = Column(String, primary_key=True)
    agent_id = Column(String, ForeignKey("entities.entity_id"), nullable=False)
    kind = Column(String, nullable=False)  # location, state, relation, fact
    target_entity = Column(String, nullable=True)
    attribute = Column(String, nullable=True)
    believed_value = Column(JSON, nullable=True)
    confidence = Column(Float, default=0.8)
    since_tick = Column(Integer, nullable=False)
    source_event_id = Column(String, nullable=True)

    __table_args__ = (
        Index("ix_beliefs_agent", "agent_id"),
    )


class Schedule(Base):
    """Pre-planned events."""

    __tablename__ = "schedules"

    schedule_id = Column(String, primary_key=True)
    fires_at_tick = Column(Integer, nullable=False)
    kami_id = Column(String, ForeignKey("entities.entity_id"), nullable=False)
    event_template = Column(JSON, nullable=False)

    __table_args__ = (
        Index("ix_schedules_tick", "fires_at_tick"),
    )


class Channel(Base):
    """Communication channels."""

    __tablename__ = "channels"

    channel_id = Column(String, primary_key=True)
    kind = Column(String, nullable=False)
    participants = Column(JSON, default=list)
    subscribers = Column(JSON, default=list)
    medium_properties = Column(JSON, default=dict)
    created_at_tick = Column(Integer, nullable=False, default=0)
    metadata_ = Column("metadata", JSON, default=dict)


class Message(Base):
    """Messages in channels."""

    __tablename__ = "messages"

    message_id = Column(String, primary_key=True)
    channel_id = Column(String, ForeignKey("channels.channel_id"), nullable=False)
    sender_id = Column(String, ForeignKey("entities.entity_id"), nullable=False)
    content = Column(Text, nullable=False)
    sent_at_tick = Column(Integer, nullable=False)
    salience = Column(Float, default=0.5)

    __table_args__ = (
        Index("ix_messages_channel_tick", "channel_id", "sent_at_tick"),
    )


class ReadReceipt(Base):
    """Tracks when agents read messages."""

    __tablename__ = "read_receipts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    message_id = Column(String, ForeignKey("messages.message_id"), nullable=False)
    agent_id = Column(String, ForeignKey("entities.entity_id"), nullable=False)
    read_at_tick = Column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("message_id", "agent_id", name="uq_read_receipt"),
    )


def get_engine(url: str = "sqlite:///./kami_sim.db"):
    return create_engine(url, echo=False)


def get_session_factory(engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(url: str = "sqlite:///./kami_sim.db") -> tuple:
    """Create all tables and return (engine, SessionFactory)."""
    engine = get_engine(url)
    Base.metadata.create_all(engine)
    factory = get_session_factory(engine)
    return engine, factory
