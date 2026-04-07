from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

Base = declarative_base()

class Employer(Base):
    """H1B employer records table."""
    __tablename__ = "employers"
    
    id = Column(Integer, primary_key=True, index=True)
    employer_name = Column(String(255), unique=True, nullable=False, index=True)
    h1b_count = Column(Integer, default=0)
    last_updated = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Employer(name='{self.employer_name}', count={self.h1b_count})>"
