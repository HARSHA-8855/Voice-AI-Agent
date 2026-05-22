import logging
from typing import List, Dict, Any, Optional
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import Patient, Appointment, InteractionLog

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PersistentMemory:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session
        logger.info("Initialized PersistentMemory with SQLAlchemy AsyncSession")

    async def get_patient(self, phone_number: str) -> Optional[Patient]:
        """
        Retrieves a patient profile by phone number.
        Returns the Patient model or None.
        """
        try:
            stmt = select(Patient).where(Patient.phone == phone_number)
            result = await self.db.execute(stmt)
            patient = result.scalar_one_or_none()
            if patient:
                logger.info(f"Retrieved patient with phone: {phone_number} (ID: {patient.id})")
            else:
                logger.info(f"No patient found with phone: {phone_number}")
            return patient
        except Exception as e:
            logger.error(f"Error fetching patient by phone {phone_number}: {e}")
            return None

    async def get_patient_history(self, patient_id: int, limit: int = 3) -> List[Appointment]:
        """
        Retrieves the last N appointments for a patient.
        """
        try:
            stmt = (
                select(Appointment)
                .where(Appointment.patient_id == patient_id)
                .order_by(Appointment.created_at.desc(), Appointment.id.desc())
                .limit(limit)
            )
            result = await self.db.execute(stmt)
            appointments = result.scalars().all()
            logger.info(f"Retrieved {len(appointments)} historical appointments for patient ID: {patient_id}")
            return list(appointments)
        except Exception as e:
            logger.error(f"Error fetching appointment history for patient ID {patient_id}: {e}")
            return []

    async def save_interaction(self, patient_id: int, session_id: str, summary: str) -> Optional[InteractionLog]:
        """
        Logs a call interaction summary for cross-session continuity.
        """
        try:
            interaction = InteractionLog(
                patient_id=patient_id,
                session_id=session_id,
                summary=summary
            )
            self.db.add(interaction)
            await self.db.commit()
            await self.db.refresh(interaction)
            logger.info(f"Logged interaction for patient ID {patient_id} in session {session_id}")
            return interaction
        except Exception as e:
            logger.error(f"Error saving interaction for patient ID {patient_id}: {e}")
            await self.db.rollback()
            return None

    async def upsert_language_preference(self, patient_id: int, language: str) -> Optional[Patient]:
        """
        Updates the language preference for a patient.
        """
        try:
            stmt = select(Patient).where(Patient.id == patient_id)
            result = await self.db.execute(stmt)
            patient = result.scalar_one_or_none()
            if patient:
                patient.preferred_language = language
                await self.db.commit()
                await self.db.refresh(patient)
                logger.info(f"Updated language preference to '{language}' for patient ID: {patient_id}")
            else:
                logger.warning(f"Patient with ID {patient_id} not found to update language preference")
            return patient
        except Exception as e:
            logger.error(f"Error updating language preference for patient ID {patient_id}: {e}")
            await self.db.rollback()
            return None
