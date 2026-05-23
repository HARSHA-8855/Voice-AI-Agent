# 2Care Voice Agent
### Real-Time Multilingual Clinical Appointment Booking — Voice AI Agent

A production-grade voice AI agent for digital healthcare. Patients speak in English, Hindi, or Tamil — the agent understands, books/reschedules/cancels appointments, and responds in voice — all under 450 ms end-to-end.

---

## Quick Start

```bash
# 1. Clone and enter the project
git clone https://github.com/HARSHA-8855/Voice-AI-Agent
cd voice-ai-agent

# 2. Copy environment variables
cp .env.example .env
# Fill in: GROQ_API_KEY, SARVAM_API_KEY, POSTGRES_URL, REDIS_URL

# 3. Start all services
docker-compose up --build

# 4. Open the demo client
open demo/index.html   # or just open in your browser
# Point it to ws://localhost:3000

# 5. Verify everything is healthy
curl http://localhost:8000/health
```

**Services started by docker-compose:**

| Service | Port | Purpose |
|---|---|---|
| FastAPI backend | 8000 | Agent, tools, memory, APIs |
| Node.js WS gateway | 3000 | Real-time audio streaming |
| Redis | 6379 | Session memory + telemetry |
| PostgreSQL | 5432 | Patient history + appointments |
| Celery worker | — | Outbound campaign scheduler |

---

## Architecture

```
Browser (demo/index.html)
    │  binary audio chunks (WebSocket)
    ▼
TypeScript WebSocket Gateway  (gateway/ · port 3000)
    │  POST /voice/process (multipart audio)
    ▼
FastAPI Pipeline  (backend/main.py · port 8000)
    │
    ├─► STT          Sarvam Saaras V3 (streaming WebSocket)
    │                → transcript + detected language
    │
    ├─► Memory fetch  Redis session (30-min TTL)
    │                 + Postgres patient history (last 3 visits)
    │
    ├─► LLM Agent    AsyncGroq · llama-3.1-70b-versatile
    │                → intent + tool selection
    │
    ├─► Tool call    check_availability / book / cancel / reschedule
    │                → appointment result from Postgres
    │
    └─► TTS          Sarvam Bulbul V3 (streaming)
                     → base64 audio response

Gateway returns audio to browser → Web Audio API plays it
```

**Outbound campaign path (Celery Beat):**
```
Celery Beat (9 AM daily)
    → query Postgres for tomorrow's appointments
    → seed agent with reminder context
    → run full agent loop
    → log interaction + mark reminder_sent = true
```

---

## Memory Design

The system uses a two-tier memory architecture to balance speed and persistence.

### Tier 1 — Session memory (Redis)

- **Key:** `session:{session_id}`
- **TTL:** 30 minutes, sliding (resets on every turn)
- **Stores:** current intent, doctor type, pending date/time, turn count, detected language, last agent utterance
- **Purpose:** maintains multi-turn context within one conversation so the agent remembers "we were discussing a cardiologist for Tuesday" without re-asking

```json
{
  "intent": "booking",
  "doctor_type": "cardiologist",
  "pending_date": "2025-06-12",
  "language": "hi",
  "turn_count": 3
}
```

### Tier 2 — Persistent memory (PostgreSQL)

- **Tables:** `patients`, `appointments`, `doctor_schedule`, `interaction_log`
- **Retrieved at session start:** patient name, preferred language, last 3 appointments
- **Written after each session:** interaction summary, language preference update
- **Purpose:** returning patients are recognised — the agent knows their preferred language and prior doctors without asking

**Why this split:** Redis gives sub-millisecond reads for hot session state. Postgres gives durable, queryable history across sessions. Storing everything in one place would either be too slow (all Postgres) or non-durable (all Redis).

---

## Latency Breakdown

Target: **< 450 ms** from speech end to first audio byte.

| Stage | Component | Typical | Notes |
|---|---|---|---|
| Speech-to-Text | Sarvam Saaras V3 | ~100 ms | Streaming WebSocket; VAD detects speech end |
| Language detect | Embedded in STT | ~0 ms | Saaras returns language tag with transcript |
| Memory fetch | Redis + Postgres | ~15 ms | Session from Redis (~1 ms) + history from Postgres (~15 ms) |
| LLM reasoning | Groq llama-3.1-70b | ~180 ms | Includes tool-call round trip |
| Tool execution | Postgres query | ~10 ms | Indexed slot lookup |
| Text-to-Speech | Sarvam Bulbul V3 | ~100 ms | Streaming; first audio chunk before full response |
| **Total** | | **~405 ms** | Under 450 ms target |

Latency is logged per-stage on every request. View live data at:
```
GET /metrics   → last 20 calls with stage breakdown
GET /traces    → last 20 agent reasoning traces
```

---

## API Reference

```
POST /voice/process     Main pipeline endpoint
GET  /health            Redis + Postgres connectivity check
GET  /metrics           Last 20 calls, latency per stage
GET  /traces            Last 20 agent reasoning traces
POST /campaigns/trigger Manual outbound campaign trigger
```

**`POST /voice/process` — request:**
```
multipart/form-data
  audio:         binary (webm/opus from browser)
  session_id:    string (UUID)
  patient_phone: string
```

**`POST /voice/process` — response:**
```json
{
  "audio_base64": "...",
  "response_text": "Your appointment with Dr. Sharma is confirmed for tomorrow at 10 AM.",
  "detected_language": "hi",
  "latency": {
    "stt_ms": 98,
    "llm_ms": 175,
    "tts_ms": 102,
    "total_ms": 412
  },
  "trace": {
    "intent": "book",
    "tool_called": "book_appointment",
    "tool_result": { "appointment_id": "appt_123", "confirmed": true },
    "response": "Aapki appointment confirm ho gayi hai..."
  }
}
```

---

## Multilingual Support

| Language | Code | STT | TTS | Detection |
|---|---|---|---|---|
| English | `en-IN` | Saaras V3 | Bulbul V3 | Auto |
| Hindi | `hi-IN` | Saaras V3 | Bulbul V3 | Auto |
| Tamil | `ta-IN` | Saaras V3 | Bulbul V3 | Auto |

Language is auto-detected by Sarvam Saaras V3 on every turn. The detected language is:
1. Stored in Redis session (used for current conversation)
2. Persisted to Postgres `patients.preferred_language` (used for future sessions)
3. Passed to the LLM system prompt (`"Respond only in {language}"`)
4. Passed to Bulbul V3 TTS for matching voice output

Language can change mid-conversation — if a patient switches from English to Hindi, the agent detects and switches on the next turn.

---

## Appointment & Conflict Logic

**Validation rules (enforced in `backend/agent/tools.py`):**

1. Slot in the past → rejected with message
2. Slot already booked → rejected, 3 nearest alternatives offered
3. Doctor not available on date → all available dates returned
4. Reschedule → atomic transaction (old slot freed + new slot booked together)

**Demo data seeded on startup:**
- 3 doctors: Cardiologist (Dr. Sharma), Dermatologist (Dr. Rao), Neurologist (Dr. Patel)
- Each has 5 available slots per day for the next 7 days
- 2 pre-booked slots per doctor to demonstrate conflict handling

---

## Outbound Campaign Mode

A Celery Beat scheduler runs daily at 9:00 AM:

1. Queries Postgres for appointments scheduled for the next day where `reminder_sent = false`
2. For each appointment, seeds the agent with: `"Hello {name}, this is a reminder about your appointment with {doctor} tomorrow at {time}."`
3. Runs the full agent reasoning loop — patient can respond to reschedule or cancel
4. Logs the full interaction to `interaction_log`
5. Sets `reminder_sent = true` to prevent duplicate reminders

**Trigger manually for testing:**
```bash
docker exec -it celery-worker python -c \
  "from scheduler.campaigns import outbound_call_campaign; \
   outbound_call_campaign.delay('test_campaign_001')"
```

---

## Running Tests

```bash
# All 15 tests (runs in ~21 seconds)
docker exec -it backend pytest

# With verbose output
docker exec -it backend pytest -v

# Single test file
docker exec -it backend pytest tests/test_tools.py
```

Tests use SQLite in-memory and mock Redis — no external services needed.

---

## Trade-offs & Design Decisions

**Sarvam AI for both STT and TTS (over Deepgram + ElevenLabs)**
Sarvam Saaras V3 and Bulbul V3 are trained natively on Indian languages including code-mixed speech (Hinglish, Tanglish). ElevenLabs and Deepgram are primarily English-first with Indian language support bolted on — this produces inconsistent prosody and misrecognition of common Indian names, clinic terms, and mixed-language sentences. Using one SDK for both also simplifies the integration surface under a tight deadline.

**Groq over OpenAI/Anthropic for LLM**
Groq's inference speed (~500 tokens/second on llama-3.1-70b) is the primary reason. At 180 ms for a typical tool-calling response, it is 2–3× faster than equivalent OpenAI API calls. The free tier (14,400 req/day) covers demo and evaluation use entirely. Tool-calling support is native and stable.

**Redis for session + Celery broker**
Using Redis for both session memory and as the Celery task broker means one less service to run. The same Redis instance handles sub-millisecond session reads and durable task queuing. TTL-based session expiry (30 min) is a natural fit for Redis and requires zero application code.

**Async throughout (asyncpg, AsyncGroq, aioredis)**
All I/O is non-blocking. A synchronous Postgres query during an LLM call would stall the entire request. With async I/O, the FastAPI worker can handle multiple concurrent sessions without thread overhead — important for the outbound campaign scheduler which may fire many concurrent reminder sessions.

**No real telephony (Twilio)**
Outbound campaigns are simulated — no real phone dial. The assignment does not require real PSTN integration, and the agent reasoning loop is identical whether triggered by a browser WebSocket or a real phone call. Adding Twilio would be a config change (swap WebSocket transport for Twilio Media Streams), not an architectural one.

---

## Known Limitations

1. **No authentication** — endpoints are open. In production, JWT middleware and CORS guards are required before any patient data is exposed.

2. **Simulated outbound calls** — the Celery scheduler runs the agent loop but does not dial a real phone number. A Twilio or PSTN integration would be a transport-layer addition.

3. **Single-region deployment** — the docker-compose setup runs on one host. Horizontal scaling requires moving to Kubernetes or ECS with a shared Postgres/Redis layer.

4. **No audio streaming (bidirectional)** — the current pipeline collects a full audio turn before sending to STT. True sub-300 ms latency would require streaming audio packets to Saaras while the patient speaks and streaming TTS chunks back before the LLM finishes — a more complex gateway implementation.

5. **Tamil TTS naturalness** — Bulbul V3 Tamil voice quality is good but occasionally produces unnatural prosody on longer medical terms. English and Hindi voices are noticeably more natural.

6. **No barge-in handling** — if the agent is speaking and the patient interrupts, the current implementation finishes the TTS playback before processing the new input. Interrupt detection requires monitoring the audio stream while TTS is playing.

7. **LLM hallucination on edge cases** — the agent occasionally invents doctor names or slot times when given ambiguous input. Production would require stricter output validation and a fallback confirmation step before writing any booking to the database.

---

## Environment Variables

```env
# Required
GROQ_API_KEY=gsk_...
SARVAM_API_KEY=...
POSTGRES_URL=postgresql+asyncpg://user:pass@postgres:5432/voice_agent
REDIS_URL=redis://redis:6379/0

# Optional
LOG_LEVEL=INFO
CELERY_BROKER_URL=redis://redis:6379/1
TZ=Asia/Kolkata
```

---

## Project Structure

```
voice-ai-agent/
├── backend/
│   ├── main.py                  # FastAPI app, pipeline, /metrics, /traces, /health
│   ├── agent/
│   │   ├── agent.py             # AsyncGroq LLM agent with tool-calling loop
│   │   ├── tools.py             # Appointment tools with conflict logic
│   │   └── prompts.py           # System prompt builder (language-aware)
│   ├── memory/
│   │   ├── session.py           # Redis session memory (30-min TTL)
│   │   └── persistent.py        # Postgres patient history queries
│   ├── services/
│   │   ├── stt.py               # Sarvam Saaras V3 streaming STT
│   │   ├── tts.py               # Sarvam Bulbul V3 streaming TTS
│   │   └── lang_detect.py       # Language detection wrapper
│   ├── scheduler/
│   │   └── campaigns.py         # Celery Beat outbound reminder tasks
│   └── db/
│       ├── models.py            # SQLAlchemy models
│       └── database.py          # Async DB connection
├── gateway/
│   └── src/
│       ├── server.ts            # WebSocket server (port 3000)
│       └── audioStream.ts       # Audio buffer + silence VAD + FastAPI dispatch
├── demo/
│   └── index.html               # Single-file browser demo client
├── tests/                       # 15 async tests (pytest)
├── docs/
│   └── architecture.png         # System architecture diagram
├── docker-compose.yml
├── .env.example
└── README.md
```
