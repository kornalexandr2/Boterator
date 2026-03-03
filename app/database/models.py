from sqlalchemy import Boolean, Column, Integer, String, DateTime, ForeignKey, Float, Text
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func

Base = declarative_base()

class User(Base):
    __tablename__ = "users"

    telegram_id = Column(Integer, primary_key=True, index=True)
    username = Column(String(255), nullable=True)
    first_name = Column(String(255), nullable=True)
    last_name = Column(String(255), nullable=True)
    email = Column(String(255), nullable=True)
    phone = Column(String(255), nullable=True)
    is_admin = Column(Boolean, default=False)
    is_moderator = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # "Вечный" статус для юзеров, которых бот видит впервые, но они уже в канале
    is_eternal = Column(Boolean, default=False)

    subscriptions = relationship("Subscription", back_populates="user", cascade="all, delete-orphan")
    payments = relationship("Payment", back_populates="user", cascade="all, delete-orphan")


class Tariff(Base):
    __tablename__ = "tariffs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    price = Column(Float, nullable=False)
    duration_days = Column(Integer, nullable=False) # 0 for lifetime
    is_trial = Column(Boolean, default=False)
    is_hidden = Column(Boolean, default=False)
    require_email = Column(Boolean, default=False)
    require_phone = Column(Boolean, default=False)

    subscriptions = relationship("Subscription", back_populates="tariff")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False)
    tariff_id = Column(Integer, ForeignKey("tariffs.id", ondelete="CASCADE"), nullable=False)
    start_date = Column(DateTime(timezone=True), server_default=func.now())
    end_date = Column(DateTime(timezone=True), nullable=True) # Null if lifetime
    is_active = Column(Boolean, default=True)
    
    # Grace period logic
    in_grace_period = Column(Boolean, default=False)
    grace_end_date = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="subscriptions")
    tariff = relationship("Tariff", back_populates="subscriptions")


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False)
    amount = Column(Float, nullable=False)
    provider = Column(String(50), nullable=False) # 'yookassa', 'sberbank', 'yoomoney', 'mock'
    status = Column(String(50), nullable=False, default="pending") # 'pending', 'success', 'failed', 'refunded'
    transaction_id = Column(String(255), nullable=True, unique=True) # External ID
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="payments")
