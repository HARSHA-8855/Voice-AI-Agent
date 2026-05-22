from sqlalchemy import Column, Integer, String, Date, Time, DateTime, ForeignKey, JSON, Boolean
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime

Base = declarative_base()

class Patient(Base):
    __tablename__ = "patients"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    phone = Column(String(20), nullable=False, unique=True, index=True)
    preferred_language = Column(String(10), default="en")
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    appointments = relationship("Appointment", back_populates="patient", cascade="all, delete-orphan")
    interactions = relationship("InteractionLog", back_populates="patient", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "phone": self.phone,
            "preferred_language": self.preferred_language,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }

class Appointment(Base):
    __tablename__ = "appointments"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    doctor_type = Column(String(50), nullable=False)
    doctor_id = Column(String(50), nullable=True) # Optional specific doctor reference
    date = Column(Date, nullable=False, index=True)
    time = Column(Time, nullable=False)
    status = Column(String(20), default="scheduled") # scheduled, completed, cancelled, rescheduled
    reminder_sent = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    patient = relationship("Patient", back_populates="appointments")

    def to_dict(self):
        return {
            "id": self.id,
            "patient_id": self.patient_id,
            "doctor_type": self.doctor_type,
            "doctor_id": self.doctor_id,
            "date": self.date.isoformat() if self.date else None,
            "time": self.time.strftime("%H:%M") if self.time else None,
            "status": self.status,
            "reminder_sent": self.reminder_sent,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }

class DoctorSchedule(Base):
    __tablename__ = "doctor_schedule"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    doctor_type = Column(String(50), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    available_slots = Column(JSON, nullable=False) # e.g. ["09:00", "09:30", "10:00"]
    booked_slots = Column(JSON, default=list) # e.g. ["09:30"]

    def to_dict(self):
        return {
            "id": self.id,
            "doctor_type": self.doctor_type,
            "date": self.date.isoformat() if self.date else None,
            "available_slots": self.available_slots,
            "booked_slots": self.booked_slots
        }

class InteractionLog(Base):
    __tablename__ = "interaction_logs"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    session_id = Column(String(100), nullable=False, index=True)
    summary = Column(String(500), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    patient = relationship("Patient", back_populates="interactions")

    def to_dict(self):
        return {
            "id": self.id,
            "patient_id": self.patient_id,
            "session_id": self.session_id,
            "summary": self.summary,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None
        }
