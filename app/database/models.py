from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    telegram_id = Column(BigInteger, primary_key=True, index=True)
    username = Column(String(255), nullable=True)
    first_name = Column(String(255), nullable=True)
    last_name = Column(String(255), nullable=True)
    email = Column(String(255), nullable=True)
    phone = Column(String(255), nullable=True)
    is_admin = Column(Boolean, default=False)
    is_moderator = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    is_eternal = Column(Boolean, default=False)

    subscriptions = relationship("Subscription", back_populates="user", cascade="all, delete-orphan")
    payments = relationship("Payment", back_populates="user", cascade="all, delete-orphan")


class Tariff(Base):
    __tablename__ = "tariffs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    price = Column(Float, nullable=False)
    duration_days = Column(Integer, nullable=False)
    is_trial = Column(Boolean, default=False)
    is_hidden = Column(Boolean, default=False)
    require_email = Column(Boolean, default=False)
    require_phone = Column(Boolean, default=False)

    subscriptions = relationship("Subscription", back_populates="tariff")
    payments = relationship("Payment", back_populates="tariff")
    resource_links = relationship("TariffResource", back_populates="tariff", cascade="all, delete-orphan")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False)
    tariff_id = Column(Integer, ForeignKey("tariffs.id", ondelete="CASCADE"), nullable=False)
    start_date = Column(DateTime(timezone=True), server_default=func.now())
    end_date = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, default=True)
    in_grace_period = Column(Boolean, default=False)
    grace_end_date = Column(DateTime(timezone=True), nullable=True)
    auto_renew_enabled = Column(Boolean, default=False)
    renewal_provider = Column(String(50), nullable=True)
    recurring_token = Column(String(255), nullable=True)
    renewal_failed_at = Column(DateTime(timezone=True), nullable=True)
    notified_3d_at = Column(DateTime(timezone=True), nullable=True)
    notified_1d_at = Column(DateTime(timezone=True), nullable=True)
    notified_0d_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="subscriptions")
    tariff = relationship("Tariff", back_populates="subscriptions")


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False)
    tariff_id = Column(Integer, ForeignKey("tariffs.id", ondelete="SET NULL"), nullable=True)
    amount = Column(Float, nullable=False)
    provider = Column(String(50), nullable=False)
    status = Column(String(50), nullable=False, default="pending")
    transaction_id = Column(String(255), nullable=True, unique=True)
    contact_email = Column(String(255), nullable=True)
    contact_phone = Column(String(255), nullable=True)
    refund_id = Column(String(255), nullable=True)
    refunded_at = Column(DateTime(timezone=True), nullable=True)
    recurring_token = Column(String(255), nullable=True)
    raw_payload = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="payments")
    tariff = relationship("Tariff", back_populates="payments")


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key = Column(String(255), primary_key=True)
    value = Column(Text, nullable=True)
    description = Column(String(255), nullable=True)


class ManagedChat(Base):
    __tablename__ = "managed_chats"

    chat_id = Column(BigInteger, primary_key=True)
    title = Column(String(255), nullable=False)
    invite_link = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True)
    permissions_ok = Column(Boolean, default=True)
    missing_permissions = Column(Text, nullable=True)
    protect_content_enabled = Column(Boolean, default=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    tariff_links = relationship("TariffResource", back_populates="chat", cascade="all, delete-orphan")


class TariffResource(Base):
    __tablename__ = "tariff_resources"
    __table_args__ = (UniqueConstraint("tariff_id", "chat_id", name="uq_tariff_resources_tariff_chat"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    tariff_id = Column(Integer, ForeignKey("tariffs.id", ondelete="CASCADE"), nullable=False)
    chat_id = Column(BigInteger, ForeignKey("managed_chats.chat_id", ondelete="CASCADE"), nullable=False)

    tariff = relationship("Tariff", back_populates="resource_links")
    chat = relationship("ManagedChat", back_populates="tariff_links")
