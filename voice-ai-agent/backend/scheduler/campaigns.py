import os
import json
import asyncio
import logging
from datetime import date, timedelta, datetime
from typing import Optional, Dict, Any

import redis
from celery import Celery
from celery.schedules import crontab
from sqlalchemy import select

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Parse REDIS_URL for connection
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Configure Celery
celery_app = Celery("campaigns", broker=REDIS_URL)

# Configure Celery Beat schedule for daily runs at 9:00 AM local time
celery_app.conf.beat_schedule = {
    "run-daily-outbound-campaign": {
        "task": "scheduler.campaigns.outbound_call_campaign",
        "schedule": crontab(hour=9, minute=0),
        "args": ("daily_campaign",)
    }
}
celery_app.conf.timezone = "Asia/Kolkata"  # Setting local timezone for Indian clinic context

# Sync Redis client for logging traces inside sync Celery task execution
redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)

async def run_outbound_campaign(campaign_id: str) -> None:
    """
    Asynchronously queries Postgres for tomorrow's appointments where reminder_sent = False,
    runs the conversational agent reasoning pass, updates the reminder status, and stores the
    interaction log and telemetry logs to Redis/Postgres.
    """
    # Import inside function to avoid premature initialization or circular imports
    from db.database import AsyncSessionLocal
    from db.models import Appointment, Patient, InteractionLog
    from agent.agent import run_agent
    from agent.tools import to_12hr

    tomorrow = date.today() + timedelta(days=1)
    logger.info(f"[Campaign {campaign_id}] Running outbound reminders for date: {tomorrow}")

    async with AsyncSessionLocal() as db:
        # 1. Query PostgreSQL for appointments tomorrow where reminder_sent is False
        stmt = (
            select(Appointment, Patient)
            .join(Patient, Appointment.patient_id == Patient.id)
            .where(Appointment.date == tomorrow)
            .where(Appointment.reminder_sent == False)
            .where(Appointment.status != "cancelled")
        )
        result = await db.execute(stmt)
        appointments_data = result.all()

        if not appointments_data:
            logger.info(f"[Campaign {campaign_id}] No pending reminders for {tomorrow}.")
            return

        logger.info(f"[Campaign {campaign_id}] Found {len(appointments_data)} appointments to process.")

        for appt, patient in appointments_data:
            time_str = to_12hr(appt.time) if appt.time else "10:00 AM"
            session_id = f"outbound_{campaign_id}_{appt.id}_{int(datetime.utcnow().timestamp())}"

            # 2. Build session context
            session_context = {
                "mode": "outbound",
                "intent": "reminder",
                "appointment_id": appt.id,
                "patient_name": patient.name,
                "doctor_type": appt.doctor_type,
                "time": time_str
            }

            # 3. Call agent directly with a seeded opening message
            seeded_msg = f"Hello {patient.name}, this is a reminder about your appointment with {appt.doctor_type} tomorrow at {time_str}."
            
            logger.info(f"[Campaign {campaign_id}] Seeding agent turn for patient {patient.name} (Phone: {patient.phone})")
            
            try:
                agent_res = await run_agent(
                    user_text=seeded_msg,
                    session_context=session_context,
                    patient_history={
                        "id": patient.id,
                        "name": patient.name,
                        "phone": patient.phone,
                        "preferred_language": patient.preferred_language
                    },
                    detected_language=patient.preferred_language or "en",
                    db=db
                )

                response_text = agent_res.get("response_text", "")
                tool_called = agent_res.get("tool_called")
                tool_result = agent_res.get("tool_result")

                # 4. Store the agent's response + any rescheduling/cancellation actions back to interaction_log table
                action_desc = f" [Tool Action: {tool_called}]" if tool_called else ""
                summary = f"Outbound Reminder. Seeded message: '{seeded_msg}'. Agent Response: '{response_text}'.{action_desc}"

                log_entry = InteractionLog(
                    patient_id=patient.id,
                    session_id=session_id,
                    summary=summary,
                    timestamp=datetime.utcnow()
                )
                db.add(log_entry)

                # 5. Mark reminder_sent = True on the appointment
                appt.reminder_sent = True
                db.add(appt)

                # Commit changes for this patient
                await db.commit()
                logger.info(f"[Campaign {campaign_id}] Successfully processed reminder for appointment ID {appt.id}")

                # 6. Log the full interaction to Redis traces so it shows up in GET /traces
                try:
                    trace_data = {
                        "timestamp": datetime.now().isoformat(),
                        "session_id": session_id,
                        "user_said": seeded_msg,
                        "intent_detected": tool_called or "reminder",
                        "tool_called": tool_called,
                        "tool_returned": tool_result,
                        "agent_said": response_text
                    }
                    redis_client.lpush("traces_list", json.dumps(trace_data))
                    redis_client.ltrim("traces_list", 0, 19)
                    redis_client.expire("traces_list", 3600)
                    logger.info(f"[Campaign {campaign_id}] Redis trace logged successfully for session {session_id}")
                except Exception as re:
                    logger.error(f"[Campaign {campaign_id}] Redis tracing failed for session {session_id}: {re}")

            except Exception as e:
                logger.error(f"[Campaign {campaign_id}] Failed to process reminder for appointment ID {appt.id}: {e}")
                await db.rollback()

@celery_app.task
def outbound_call_campaign(campaign_id: str) -> str:
    """
    Celery task that runs the outbound call campaign by executing the async pipeline synchronously
    inside the celery worker.
    """
    logger.info(f"[Celery] Triggering outbound_call_campaign task with campaign_id: {campaign_id}")
    asyncio.run(run_outbound_campaign(campaign_id))
    return f"Campaign {campaign_id} executed successfully."
