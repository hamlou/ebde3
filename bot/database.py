import os
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, Text
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()

# DATABASE_URL lets us seamlessly migrate to Postgres (Supabase/Neon)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./apex.db")

class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True, index=True)
    whop_id = Column(String, unique=True, index=True)
    telegram_id = Column(String, nullable=True)
    is_active = Column(Boolean, default=False)

class Trade(Base):
    __tablename__ = 'active_trades'
    
    id = Column(Integer, primary_key=True, index=True)
    asset = Column(String, index=True)
    direction = Column(String)  # BUY or SELL
    entry_price = Column(String) # Stored as string to prevent precision loss if needed, or float
    tp_price = Column(String)
    sl_price = Column(String)
    risk_pct = Column(String, default="1.0") # E.g., "1.5" for 1.5% risk
    conviction = Column(Integer, nullable=True)
    status = Column(String, default="OPEN") # OPEN, WON, LOST
    mt5_status = Column(String, default="N/A") # PENDING, EXECUTED, FAILED, N/A
    opened_at = Column(String)
    closed_at = Column(String, nullable=True)

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(String, index=True)
    description = Column(Text)
    timestamp = Column(String)

engine = create_engine(
    DATABASE_URL, 
    # check_same_thread is only needed for SQLite
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
