import os
from dotenv import load_dotenv
load_dotenv()
import time
import json
import base64
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from contextlib import asynccontextmanager
from pydantic import BaseModel

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession

# Imports from backend modules
from db.database import get_db, AsyncSessionLocal
from db.models import Patient, Appointment, DoctorSchedule, InteractionLog
from memory.session import SessionMemory
from memory.persistent import PersistentMemory
from agent.agent import ClinicalAgent
from agent.tools import (
    check_availability,
    book_appointment,
    cancel_appointment,
    reschedule_appointment,
    seed_demo_data,
    ClinicalClinicError,
    AppointmentConflictError
)
from services.stt import SarvamSTT
from services.tts import SarvamTTS
from services.lang_detect import detect_language

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Core global services
session_memory = SessionMemory()
stt_service = SarvamSTT()
tts_service = SarvamTTS()
agent = ClinicalAgent()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create all tables if they don't exist
    logger.info("Creating database tables if they do not exist...")
    from db.models import Base
    from db.database import engine
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables verified/created successfully.")
    except Exception as e:
        logger.error(f"Error creating database tables on startup: {e}")
        
    # Startup: seed the database for demo/test purposes
    logger.info("Initializing database and seeding demo data on startup...")
    async with AsyncSessionLocal() as session:
        try:
            await seed_demo_data(session)
            logger.info("Successfully seeded demo data.")
        except Exception as e:
            logger.error(f"Error seeding database on startup: {e}")
    yield
    # Shutdown: cleanly close open connections
    logger.info("Shutting down application...")
    await session_memory.close()
    try:
        await stt_service.close()
    except Exception as e:
        logger.error(f"Error closing stt_service client: {e}")
    try:
        await tts_service.close()
    except Exception as e:
        logger.error(f"Error closing tts_service client: {e}")

app = FastAPI(title="Clinical Voice AI Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Request & Response Models ---

class VoiceProcessRequest(BaseModel):
    audio_base64: str
    session_id: str
    patient_phone: str

class VoiceProcessResponse(BaseModel):
    audio_base64: str
    response_text: str
    latency_breakdown: Dict[str, int]
    detected_language: str = "en"
    trace: Optional[Dict[str, Any]] = None

# --- API Endpoints ---

@app.get("/")
async def root():
    return {"message": "Voice AI Backend is running"}

@app.post("/voice/process", response_model=VoiceProcessResponse)
async def voice_process(payload: VoiceProcessRequest, db: AsyncSession = Depends(get_db)):
    """
    Core real-time voice pipeline.
    Executes: Audio decode -> STT -> Language Detect -> Fetch Memory -> LLM Agent -> Tool Execution -> TTS -> Metric Logging.
    """
    total_start = time.perf_counter()
    persistent_memory = PersistentMemory(db)

    # 1. Speech-to-Text (STT) Stage
    stt_start = time.perf_counter()
    stt_text = ""
    detected_lang = "en"
    
    try:
        audio_bytes = base64.b64decode(payload.audio_base64)
        
        async def audio_chunk_generator():
            yield audio_bytes

        # Consume from Sarvam's streaming STT
        async for res in stt_service.stream_stt(audio_chunk_generator()):
            stt_text = res.get("text", "")
            detected_lang = res.get("language", "en")
            if res.get("is_final", False):
                break
    except Exception as e:
        logger.error(f"STT Stage failed: {e}")
        raise HTTPException(status_code=500, detail=f"Speech to text transcription failed: {str(e)}")
        
    stt_ms = int((time.perf_counter() - stt_start) * 1000)
    try:
        print(f"[STT] transcript='{stt_text}' language='{detected_lang}'")
    except Exception:
        safe_text = stt_text.encode('ascii', errors='replace').decode('ascii')
        print(f"[STT] transcript='{safe_text}' (safe ascii) language='{detected_lang}'")

    # 2. Language Detection Stage
    lang_start = time.perf_counter()
    stt_detected_lang = detected_lang  # Store the STT-detected language
    if stt_text.strip():
        try:
            # Refine detected language via langdetect
            refined_lang = detect_language(stt_text)
            if refined_lang in ["en", "hi", "ta", "te"]:
                detected_lang = refined_lang
        except Exception:
            pass # Retain fallback from STT if detection fails
    lang_ms = int((time.perf_counter() - lang_start) * 1000)
    
    # Ensure detected_lang is one of the supported codes, falling back to STT-detected or 'en'
    if detected_lang not in ["en", "hi", "ta", "te"]:
        detected_lang = stt_detected_lang if stt_detected_lang in ["en", "hi", "ta", "te"] else "en"

    # 3. Memory Fetch Stage (Redis session & PostgreSQL patient history)
    mem_start = time.perf_counter()
    try:
        # A. Redis Session Context
        session_state = await session_memory.get_session(payload.session_id)
        session_state["turn_count"] = session_state.get("turn_count", 0) + 1
        session_state["language"] = detected_lang
        
        # B. PostgreSQL Patient Context
        patient = await persistent_memory.get_patient(payload.patient_phone)
        patient_id = None
        patient_name = "New Patient"
        patient_history = []

        if patient:
            patient_id = patient.id
            patient_name = patient.name
            history_records = await persistent_memory.get_patient_history(patient_id)
            patient_history = [appt.to_dict() for appt in history_records]
        else:
            # Register patient automatically on first interaction
            patient = Patient(
                name=f"Patient {payload.patient_phone[-4:]}",
                phone=payload.patient_phone,
                preferred_language=detected_lang
            )
            db.add(patient)
            await db.commit()
            await db.refresh(patient)
            patient_id = patient.id
            patient_name = patient.name

        # Update Redis sliding TTL state
        await session_memory.update_session(payload.session_id, session_state)
    except Exception as e:
        logger.error(f"Memory Fetch Stage failed: {e}")
        raise HTTPException(status_code=500, detail=f"Memory operations failed: {str(e)}")
        
    memory_ms = int((time.perf_counter() - mem_start) * 1000)

    # 4. LLM Reasoning Agent Stage
    llm_start = time.perf_counter()
    try:
        agent_res = await agent.process_request(
            user_text=stt_text,
            session_context=session_state,
            patient_history={"id": patient_id, "name": patient_name, "history": patient_history},
            detected_language=detected_lang
        )
    except Exception as e:
        logger.error(f"LLM Agent Stage failed: {e}")
        raise HTTPException(status_code=500, detail=f"AI Agent reasoning failed: {str(e)}")
        
    llm_ms = int((time.perf_counter() - llm_start) * 1000)

    # 5. Appointment Tool Execution
    tool_called = agent_res.get("tool_called")
    tool_args = agent_res.get("tool_result", {}).get("args", {}) if agent_res.get("tool_result") else {}
    tool_exec_result = None
    response_text = agent_res.get("response_text", "")

    if tool_called:
        logger.info(f"Executing tool: {tool_called} with arguments: {tool_args}")
        try:
            if tool_called == "check_availability":
                tool_exec_result = await check_availability(
                    db,
                    doctor_type=tool_args.get("doctor_type"),
                    date_str=tool_args.get("date")
                )
            elif tool_called == "book_appointment":
                tool_exec_result = await book_appointment(
                    db,
                    patient_id=patient_id,
                    doctor_type=tool_args.get("doctor_type"),
                    date_str=tool_args.get("date"),
                    time_str=tool_args.get("time")
                )
            elif tool_called == "cancel_appointment":
                appt_id = int(tool_args.get("appointment_id"))
                tool_exec_result = await cancel_appointment(
                    db,
                    appointment_id=appt_id,
                    patient_id=patient_id
                )
            elif tool_called == "reschedule_appointment":
                appt_id = int(tool_args.get("appointment_id"))
                tool_exec_result = await reschedule_appointment(
                    db,
                    appointment_id=appt_id,
                    patient_id=patient_id,
                    new_date_str=tool_args.get("new_date"),
                    new_time_str=tool_args.get("new_time")
                )
        except AppointmentConflictError as e:
            tool_exec_result = {
                "status": "error",
                "error_type": "conflict",
                "message": str(e),
                "alternatives": e.alternatives
            }
        except ClinicalClinicError as e:
            tool_exec_result = {
                "status": "error",
                "message": str(e)
            }
        except Exception as e:
            tool_exec_result = {
                "status": "error",
                "message": f"An unexpected error occurred during scheduling: {str(e)}"
            }

        # Secondary quick reasoning pass to formulate final response based on tool results
        if tool_exec_result:
            try:
                messages = [{"role": "system", "content": agent._build_system_prompt(detected_lang, {"id": patient_id, "name": patient_name, "history": patient_history}, session_state)}]
                for msg in session_state.get("messages", []):
                    if msg.get("role") in ["user", "assistant"]:
                        messages.append({"role": msg["role"], "content": msg["content"]})
                messages.extend([
                    {"role": "user", "content": stt_text},
                    {"role": "assistant", "content": None, "tool_calls": [
                        {"id": "call_1", "type": "function", "function": {"name": tool_called, "arguments": json.dumps(tool_args)}}
                    ]},
                    {"role": "tool", "tool_call_id": "call_1", "name": tool_called, "content": json.dumps(tool_exec_result)}
                ])
                
                second_res = await agent.client.chat.completions.create(
                    model=agent.model,
                    messages=messages,
                    temperature=0.2
                )
                response_text = second_res.choices[0].message.content or ""
            except Exception as e:
                logger.error(f"Secondary LLM generation failed: {e}")
                # Fallback to predefined slot confirmations
                if tool_exec_result.get("status") == "success":
                    response_text = tool_exec_result.get("confirmation_message") or "Done."
                else:
                    response_text = tool_exec_result.get("message") or "Sorry, an error occurred."

    # Update and save the updated conversation history in Redis
    if stt_text.strip():
        if "messages" not in session_state:
            session_state["messages"] = []
        session_state["messages"].append({"role": "user", "content": stt_text})
        session_state["messages"].append({"role": "assistant", "content": response_text})
        
        # Also preserve Slot Tracking in session state for fallback reference
        if tool_called == "check_availability":
            session_state["doctor_type"] = tool_args.get("doctor_type") or session_state.get("doctor_type")
            session_state["date"] = tool_args.get("date") or session_state.get("date")
        elif tool_called == "book_appointment":
            session_state["doctor_type"] = tool_args.get("doctor_type") or session_state.get("doctor_type")
            session_state["date"] = tool_args.get("date") or session_state.get("date")
            session_state["time"] = tool_args.get("time") or session_state.get("time")
        
        try:
            await session_memory.update_session(payload.session_id, session_state)
        except Exception as se:
            logger.error(f"Failed to update session with history: {se}")

    intent = tool_called or "chit_chat"
    print(f"[AGENT] intent='{intent}' tool='{tool_called}'")

    # 6. Text-to-Speech (TTS) Stage
    tts_start = time.perf_counter()
    audio_base64_out = ""
    tts_success = False
    out_audio_bytes = b""
    try:
        audio_base64_out = await tts_service.generate_tts(response_text, language_code=detected_lang)
        if audio_base64_out:
            out_audio_bytes = base64.b64decode(audio_base64_out)
            tts_success = True
    except Exception as e:
        logger.error(f"TTS Stage failed: {e}")
        # Retain empty audio fallback instead of crashing pipeline
        
    tts_ms = int((time.perf_counter() - tts_start) * 1000)
    print(f"[TTS] success={tts_success} size={len(out_audio_bytes)}")
    total_ms = int((time.perf_counter() - total_start) * 1000)
    print(f"[PIPELINE] total={total_ms}ms")

    # 7. Metrics & Tracing Logging (Redis list with 1-Hour TTL)
    latency_breakdown = {
        "stt_ms": stt_ms,
        "lang_ms": lang_ms,
        "memory_ms": memory_ms,
        "llm_ms": llm_ms,
        "tts_ms": tts_ms,
        "total_ms": total_ms
    }

    try:
        timestamp_str = datetime.now().isoformat()
        
        # A. Latency Metric Log
        metric_data = {
            "timestamp": timestamp_str,
            "session_id": payload.session_id,
            "total_ms": total_ms,
            "breakdown": latency_breakdown,
            "tool_called": tool_called,
            "language": detected_lang
        }
        await session_memory.redis.lpush("metrics_list", json.dumps(metric_data))
        await session_memory.redis.ltrim("metrics_list", 0, 19)
        await session_memory.redis.expire("metrics_list", 3600)

        # B. Agent Reasoning Trace Log
        trace_data = {
            "timestamp": timestamp_str,
            "session_id": payload.session_id,
            "user_said": stt_text,
            "intent_detected": tool_called or "chit_chat",
            "tool_called": tool_called,
            "tool_returned": tool_exec_result,
            "agent_said": response_text
        }
        await session_memory.redis.lpush("traces_list", json.dumps(trace_data))
        await session_memory.redis.ltrim("traces_list", 0, 19)
        await session_memory.redis.expire("traces_list", 3600)
        
    except Exception as e:
        logger.error(f"Failed to record pipeline metrics/traces to Redis: {e}")

    return VoiceProcessResponse(
        audio_base64=audio_base64_out,
        response_text=response_text,
        latency_breakdown=latency_breakdown,
        detected_language=detected_lang,
        trace=trace_data
    )

@app.get("/metrics")
async def get_metrics():
    """
    Returns the last 20 calls with latency breakdown per stage from Redis.
    """
    try:
        metrics_json = await session_memory.redis.lrange("metrics_list", 0, 19)
        metrics = [json.loads(m) for m in metrics_json]
        return metrics
    except Exception as e:
        logger.error(f"Error fetching metrics from Redis: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch metrics: {str(e)}")

@app.get("/traces")
async def get_traces():
    """
    Returns the last 20 agent reasoning traces from Redis.
    """
    try:
        traces_json = await session_memory.redis.lrange("traces_list", 0, 19)
        traces = [json.loads(t) for t in traces_json]
        return traces
    except Exception as e:
        logger.error(f"Error fetching traces from Redis: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch traces: {str(e)}")

@app.get("/health")
async def health(db: AsyncSession = Depends(get_db)):
    """
    Performs standard health check diagnostics.
    """
    redis_status = "fail"
    postgres_status = "fail"
    
    # Check Redis Connectivity
    try:
        await session_memory.redis.ping()
        redis_status = "ok"
    except Exception as e:
        logger.error(f"Health check failed on Redis connection: {e}")
        
    # Check Postgres Connectivity
    try:
        from sqlalchemy import text
        await db.execute(text("SELECT 1"))
        postgres_status = "ok"
    except Exception as e:
        logger.error(f"Health check failed on Postgres connection: {e}")
        
    status = "healthy" if (redis_status == "ok" and postgres_status == "ok") else "unhealthy"
    
    return {
        "status": status,
        "redis": redis_status,
        "postgres": postgres_status
    }

class CampaignTriggerRequest(BaseModel):
    patient_phone: str
    campaign_type: str  # reminder or followup

@app.post("/campaigns/trigger")
async def trigger_campaign(payload: CampaignTriggerRequest, db: AsyncSession = Depends(get_db)):
    """
    Triggers an outbound reminder campaign.
    To make this demo highly interactive, if the patient doesn't have an appointment scheduled tomorrow,
    we seed one dynamically so the campaign has data to process.
    """
    from datetime import date, time, timedelta
    from db.models import Patient, Appointment
    from scheduler.campaigns import run_outbound_campaign
    from sqlalchemy import select

    phone = payload.patient_phone.strip()
    campaign_type = payload.campaign_type.strip()

    if not phone:
        raise HTTPException(status_code=400, detail="Patient phone is required")

    logger.info(f"Triggering manual {campaign_type} campaign for phone: {phone}")

    try:
        # 1. Fetch or create patient
        pat_stmt = select(Patient).where(Patient.phone == phone)
        pat_res = await db.execute(pat_stmt)
        patient = pat_res.scalar_one_or_none()

        if not patient:
            patient = Patient(
                name=f"Demo Patient ({phone[-4:]})",
                phone=phone,
                preferred_language="en"
            )
            db.add(patient)
            await db.commit()
            await db.refresh(patient)

        # 2. Check if an appointment exists for tomorrow, otherwise seed one
        tomorrow = date.today() + timedelta(days=1)
        appt_stmt = (
            select(Appointment)
            .where(Appointment.patient_id == patient.id)
            .where(Appointment.date == tomorrow)
            .where(Appointment.reminder_sent == False)
            .where(Appointment.status != "cancelled")
        )
        appt_res = await db.execute(appt_stmt)
        appt = appt_res.scalar_one_or_none()

        if not appt:
            # Seed dynamic dentist appointment for tomorrow
            appt = Appointment(
                patient_id=patient.id,
                doctor_type="General Physician",
                date=tomorrow,
                time=time(11, 30),
                status="scheduled",
                reminder_sent=False
            )
            db.add(appt)
            await db.commit()
            await db.refresh(appt)
            logger.info(f"Seeded tomorrow's appointment dynamically for {phone} to trigger campaign.")

        # 3. Run the campaign synchronously for instant user feedback in the demo UI
        await run_outbound_campaign(campaign_id="manual_demo")
        
        return {
            "status": "success",
            "message": f"Successfully processed campaign outbound call for {phone}.",
            "details": f"Dynamically scheduled appointment tomorrow for {patient.name} at 11:30 and ran campaign agent reminder."
        }

    except Exception as e:
        logger.error(f"Campaign trigger endpoint failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to trigger campaign: {str(e)}")
