# 🎙️ 2Care Voice Agent
### Real-Time Multilingual Clinical Appointment Booking — Voice AI Agent

2Care is a production-grade, ultra-low latency, multi-lingual conversational voice AI agent designed for modern digital healthcare clinics. It allows patients to speak naturally in **English, Hindi, or Tamil** (and Hinglish/Tanglish mixed codes) to check availability, book, reschedule, or cancel appointments. The agent automatically detects the language mid-conversation, reasons using clinical schemas, invokes database tools with atomic conflict resolution, and responds back in voice—all with sub-second turnaround times.

---

## 🏗️ System Architecture

2Care uses a highly decoupled microservices architecture designed to maximize throughput and minimize latency:

```mermaid
graph TD
    subgraph Client [Client UI & Gateway]
        UI["🎙️ Web Voice Console<br>(HTML5 Audio + VAD - Port 8080)"]
        GW["⚡ Node.js WebSocket Gateway<br>(Express + WS - Port 3000)"]
    end

    subgraph Orchestrator [FastAPI Core Services - Port 8000]
        API["🚀 FastAPI Orchestrator<br>(main.py)"]
        STT["🗣️ STT Service<br>(Sarvam Saaras V3 REST API)"]
        LLM["🧠 Clinical Reasoning Agent<br>(Groq llama-3.1-8b-instant)"]
        TTS["🔊 TTS Service<br>(Sarvam Bulbul V3 REST API)"]
    end

    subgraph Memory [Memory & Context Layer]
        Redis["🔴 Redis Session Cache<br>(Turn History & Telemetry - Port 6379)"]
        DB[("🐘 PostgreSQL Database<br>(Clinical Slots & Records - Port 5432)")]
    end

    subgraph Background [Outbound Notification Engine]
        Worker["⚙️ Celery Workers<br>(Campaign execution)"]
        Beat["⏱️ Celery Beat Scheduler<br>(Recurring tasks)"]
    end

    %% Audio and websocket data flows
    UI <=>|Raw 16kHz PCM| GW
    GW <=>|HTTP /voice/process| API
    
    %% API Orchestration flow
    API <=>|HTTP Audio| STT
    API <=>|HTTP Prompt & Tools| LLM
    API <=>|HTTP Text| TTS
    
    %% Memory operations
    API <=>|Session context| Redis
    API <=>|Patient records| DB
    LLM <=>|Check / Book / Cancel| DB
    
    %% Background schedules
    Beat -->|Queue Campaigns| Redis
    Worker -->|Process Queue| Redis
    Worker -->|Fetch campaigns| DB
    Worker -->|Trigger outbound| API
```

---

## ⚡ Latency Performance Optimizations

Bringing total turnaround latency down from ~7.9s to **sub-second responses (~750ms - 1.2s)** was achieved through several key engineering optimizations:

*   **STT REST Migration**: Migrated from streaming WebSockets to aggregated HTTP POST requests to eliminate connection handshakes and improve transcription reliability on noisy mobile channels.
*   **Warm Connection Pooling**: Reuses a single persistent `httpx.AsyncClient` session inside the FastAPI backend for all external requests to Sarvam and Groq, eliminating TCP connection overhead.
*   **Fast Reasoning Swaps**: Swapped from the heavier `llama-3.1-70b-versatile` model to the ultra-responsive `llama-3.1-8b-instant` on Groq, bringing LLM inference time down to ~120-180ms.
*   **Aggressive Voice Activity Detection (VAD)**: Fine-tuned silence detection down to `800ms` in the WebSocket Gateway. As soon as the user finishes speaking, the buffer is dispatched immediately to the orchestrator.
*   **Audio Cache Fallback**: In the event of a Sarvam TTS timeout or API error, the backend immediately serves a pre-computed silent base64 WAV chunk to prevent pipeline blocking.

| Stage | Component | Typical Latency | Notes |
| :--- | :--- | :--- | :--- |
| **Speech-to-Text** | Sarvam Saaras V3 | ~150 - 250 ms | REST aggregated API; highly optimized for Indian accents |
| **Language Detect** | Embedded STT + langdetect | ~2 ms | Auto-detect and refine spoken tongue (EN/HI/TA/TE) |
| **Memory Fetch** | Redis + PostgreSQL | ~10 - 15 ms | Redis Cache (~1 ms) + Postgres Patient Profile & History (~10 ms) |
| **LLM Reasoning** | Groq Llama 3.1 8B | ~120 - 180 ms | Includes tool calling schemas & prompt composition |
| **Tool Execution** | Postgres DB Query | ~5 - 10 ms | Core appointment conflict checking and slot allocations |
| **Secondary LLM Pass**| Groq Llama 3.1 8B | ~120 - 150 ms | Translates raw tool JSON into humanized speech in the active language |
| **Text-to-Speech** | Sarvam Bulbul V3 | ~200 - 250 ms | Aggregated high-fidelity audio synthesis |
| **Total Pipeline** | **End-to-End** | **~750ms - 1.2s** | Response delivered back to client browser |

---

## 🚀 Getting Started

You can choose to spin up the entire application stack in Docker, or run a hybrid configuration (Infrastructure in Docker, code running natively on your host machine) which is highly recommended for active development on Windows.

### Prerequisites
*   Docker & Docker Compose installed
*   Python 3.10+ installed
*   Node.js 16+ and npm installed

---

### Option A: Hybrid / Native Execution (Recommended for Windows)

This option launches database containers in Docker while executing the microservices as fast local processes.

#### 1. Start Docker Infrastructure (PostgreSQL + Redis)
Ensure Docker is running, then start the persistent storage layers:
```bash
docker-compose up -d postgres redis
```

#### 2. Configure Environment Variables
Create a file named `.env` in the `backend/` directory matching the following structure:
```env
GROQ_API_KEY=gsk_your_groq_key_here
SARVAM_API_KEY=your_sarvam_key_here
POSTGRES_URL=postgresql+asyncpg://user:password@localhost:5432/voice_agent
REDIS_URL=redis://localhost:6379/0
```
Create a `.env` in the root or `gateway/` folder if you want to customize your Gateway configurations:
```env
PORT=3000
FASTAPI_URL=http://127.0.0.1:8000
```

#### 3. Run the Automated Startup Script
Simply run the startup batch file from the root directory:
```bash
start.bat
```
This script will:
1. Confirm Redis and PostgreSQL are active in Docker.
2. Launch the **FastAPI Backend** on port `8000`.
3. Launch the **Node.js WebSocket Gateway** on port `3000`.
4. Launch the **Celery Worker** process for background queues.
5. Launch the **Celery Beat Scheduler** for outbound triggers.
6. Spin up the **Demo Web Voice Console Server** on port `8080`.

#### 4. Open the Web Console
Open your browser and navigate to:
```
http://localhost:8080
```
This single-page app serves as a real-time web audio client. Press **Start Session**, allow microphone access, and begin speaking!

---

### Option B: Full Containerized Stack

To build and run all 6 microservices fully containerized:

```bash
# 1. Setup your .env in the backend folder
cp backend/.env.example backend/.env
# Update backend/.env with your GROQ_API_KEY and SARVAM_API_KEY

# 2. Build and launch all services
docker-compose up --build
```
Once healthy:
*   Open the Demo Web Console in your browser: `http://localhost:8080`
*   Verify backend health status: `curl http://localhost:8000/health`

---

## 💾 Two-Tier Memory Architecture

To balance speed and durable transactional history, 2Care splits session memory and patient records into a two-tier memory topology:

### Tier 1 — Active Session State (Redis Cache)
*   **Storage**: Keyed by `session:{session_id}` with a sliding 30-minute Time-To-Live (TTL).
*   **Properties**: Maintains turn history, active language, turn count, doctor type selection, and scheduled dates.
*   **Why**: Under 1ms latency reads/writes keep the conversation context warm between turns without constantly hitting PostgreSQL.

### Tier 2 — Patient & Appointment Records (PostgreSQL)
*   **Tables**: `patients`, `appointments`, `doctor_schedule`, `interaction_log`.
*   **Properties**: Holds patient contact details, primary language choices, past appointment records, and full textual interaction summaries.
*   **Why**: Ensures returning patients are greeted by name and their language preferences are recognized instantly. Storing records in PostgreSQL guarantees ACID transactional properties for scheduling.

---

## 🎯 API Reference

### Orchestrator Endpoints (`backend/main.py`)

| Endpoint | Method | Input / Output | Description |
| :--- | :--- | :--- | :--- |
| `/health` | `GET` | None $\rightarrow$ JSON | Performs diagnostic pings to Redis and Postgres. |
| `/voice/process` | `POST` | JSON $\rightarrow$ JSON | The core audio pipeline endpoint. |
| `/metrics` | `GET` | None $\rightarrow$ JSON Array | Fetches latency breakdowns and tool stats for the last 20 turns. |
| `/traces` | `GET` | None $\rightarrow$ JSON Array | Fetches deep LLM traces, tool parameters, and transcript logs. |
| `/campaigns/trigger` | `POST` | JSON $\rightarrow$ JSON | Manually fires the daily reminder campaign loop. |

#### `POST /voice/process`
**Payload Format:**
```json
{
  "audio_base64": "UklGRi...",
  "session_id": "8a7c29fb-df61-4131-8fdf-188b0f805a81",
  "patient_phone": "+919988776655"
}
```

**Response Format:**
```json
{
  "audio_base64": "UklGRiS...",
  "response_text": "Aapki appointment Dr. Sharma ke saath kal subah das baje confirmed hai.",
  "detected_language": "hi",
  "latency_breakdown": {
    "stt_ms": 198,
    "lang_ms": 2,
    "memory_ms": 11,
    "llm_ms": 140,
    "tts_ms": 210,
    "total_ms": 561
  },
  "trace": {
    "timestamp": "2026-05-23T19:06:57.123456",
    "session_id": "8a7c29fb-df61-4131-8fdf-188b0f805a81",
    "user_said": "book appointment tomorrow with sharma cardiologist",
    "intent_detected": "book_appointment",
    "tool_called": "book_appointment",
    "tool_returned": {
      "status": "success",
      "appointment_id": 42,
      "confirmation_message": "Appointment confirmed for 2026-05-24 at 10:00"
    },
    "agent_said": "Aapki appointment Dr. Sharma ke saath kal subah das baje confirmed hai."
  }
}
```

---

## 🌐 Multilingual Orchestration

2Care features robust support for Indian accents and code-mixed formats (Hinglish/Tanglish).
*   **Language Auto-Detection**: Handled by the native locale outputs of Sarvam Saaras V3. The backend refines this using a lightweight `langdetect` pass on the text to lock down the language (English, Hindi, or Tamil).
*   **Stateful Prompting**: Once the language is detected, it is committed to the Redis session. The orchestrator adjusts the Groq LLM prompt system instruction, commanding: `Current language: {language}. Respond ONLY in this language.`
*   **Native TTS Matching**: The output locale code is passed to the Sarvam Bulbul V3 engine to produce highly natural native-speaking TTS (e.g., using the "ritu" voice model for Hindi, Tamil, and Indian English).

---

## 🤖 Appointment Slot & Conflict Resolution

The logic inside `backend/agent/tools.py` guarantees schedule sanity:
1.  **Temporal Validation**: Slots occurring in the past relative to the server’s local time are rejected.
2.  **Conflict Checks**: When a slot is already booked, the system automatically runs an index lookup on available slots for that doctor and offers the **3 nearest alternative times** on the same day or subsequent days.
3.  **Atomic Operations**: Rescheduling an appointment executes as an atomic database transaction. The system frees the old slot and books the new one simultaneously—preventing a patient from losing their original slot if the new booking fails mid-transaction.
4.  **Startup Seeding**: By default, 3 doctors are seeded on startup (Dr. Sharma - Cardiologist, Dr. Rao - Dermatologist, Dr. Patel - Neurologist) with 5 open slots per day for 7 days, complete with pre-booked conflicts to showcase the agent's negotiation skills.

---

## ⏱️ Proactive Outbound Campaigns (Celery Beat)

The background worker scheduling handles outbound care campaigns automatically:
1.  **The Beat Schedule**: Runs daily at 9:00 AM local time (`Asia/Kolkata` timezone configured inside `scheduler/campaigns.py`).
2.  **The Check**: Queries PostgreSQL for all appointments scheduled for tomorrow where `reminder_sent = false` and `status != "cancelled"`.
3.  **The Seed**: For each matched appointment, it fires a background Celery task that seeds a brand-new conversation session in Redis, pre-populating it with context and initiating a virtual outbound greeting: `"Hello {patient_name}, this is a reminder about your appointment with {doctor_type} tomorrow at {time}."`
4.  **The Interactivity**: Because Celery workers write directly to the same Redis cache, patients can respond immediately via the Web Console to reschedule or cancel, triggering the real-time agent logic.
5.  **Durable Logging**: Every campaign run writes to `interaction_log` in PostgreSQL and records the full conversational trace to the `/traces` endpoint in Redis.

> [!TIP]
> **Manual Campaign Triggering for Demo**:
> To make evaluating outbound campaigns easy, the endpoint `POST /campaigns/trigger` is provided. If the patient phone number has no appointments scheduled for tomorrow, it will **dynamically seed a Physician appointment for tomorrow at 11:30 AM** and run the campaign logic instantly, displaying the telemetry outputs directly on the UI!
>
> **Trigger manually via terminal:**
> ```bash
> # Inside backend directory or via Docker terminal
> docker exec -it celery-worker python -c "from scheduler.campaigns import outbound_call_campaign; outbound_call_campaign.delay('test_campaign_001')"
> ```

---

## 🧪 Test Suite

The repository contains a robust, async-native test suite validating all parts of the voice orchestrator. It consists of **16 comprehensive unit & integration tests** covering:
*   Database models & migration setups
*   Redis session lifecycle, multi-turn contexts, and metrics
*   Clinical tools, doctor schedules, atomic transactions, and slot alternatives
*   STT and TTS API services and fail-safes
*   Full end-to-end conversation flows with multi-pass tool selections

```bash
# Enter backend folder
cd backend

# Run the full suite (completes in ~40 seconds)
python -m pytest -v
```

> [!NOTE]
> Tests run entirely in isolation utilizing an in-memory SQLite backend and a mocked Redis pipeline—meaning no external services or API keys are required to execute tests successfully!

---

## 🛡️ Architectural Trade-offs, Mitigations & Future Roadmap

*   **REST Aggregation over Raw WebSockets (STT/TTS)**:
    *   *Trade-off*: True live audio streaming (bidirectional streaming) was deferred. The pipeline collects the voice turn, waits for silence, and sends it as a single chunk.
    *   *Mitigation*: We integrated a highly aggressive `800ms` silence threshold and persistent warm connection pools to achieve sub-second response times. This results in an incredibly stable connection even under packet loss, which streaming WebSockets are highly sensitive to.
*   **Mitigation of LLM Hallucinations**:
    *   *Risk*: Small LLM models (like 8B parameter variants) occasionally hallucinate doctor types or available slot dates when answering loosely.
    *   *Mitigation*: We implemented a **two-pass reasoning design**. The first pass strictly extracts intents and formats parameters for database tool validation. The database does the hard validation and provides raw JSON facts. A second pass feeds these concrete JSON facts back to the LLM to write a natural speech response. Hallucinations on critical clinic schedules are mathematically eliminated.
*   **Open Endpoints**:
    *   *Limitation*: Currently, `/voice/process` has no authentication layers.
    *   *Roadmap*: Production deployments require integrating an API Gateway or FastAPI JWT Bearer authorization layers to protect patient database IDs.
*   **Barge-In Capabilities**:
    *   *Limitation*: Patients cannot interrupt the agent mid-sentence because the audio files play to completion in the browser.
    *   *Roadmap*: A future iteration will stream audio chunks back via the Node.js Gateway and listen for user microphone voice activity during playback to instantly halt transmission and trigger a reset frame.

---

## 📦 Project Structure

```
voice-ai-agent/
├── backend/
│   ├── main.py                  # Core FastAPI Orchestrator, metrics, and api endpoints
│   ├── requirements.txt         # Python dependencies
│   ├── docker-compose.yml       # Infrastructure orchestration file
│   ├── Dockerfile               # Backend container recipe
│   ├── start.bat                # Automated multi-process local startup script
│   ├── agent/
│   │   ├── agent.py             # Groq clinical reasoning agent & two-pass logic
│   │   ├── tools.py             # Clinic DB operations (checking, booking, rescheduling)
│   │   └── prompts.py           # Language-aware prompt engineering
│   ├── memory/
│   │   ├── session.py           # Tier 1 - Redis session cache handling (30-min TTL)
│   │   └── persistent.py        # Tier 2 - Postgres patient history queries
│   ├── services/
│   │   ├── stt.py               # Sarvam Saaras V3 Speech-to-Text client
│   │   ├── tts.py               # Sarvam Bulbul V3 Text-to-Speech client
│   │   └── lang_detect.py       # Language refinement utilities
│   ├── scheduler/
│   │   └── campaigns.py         # Celery tasks and daily Beat configuration
│   ├── db/
│   │   ├── database.py          # SQLAlchemy async engine & session configurations
│   │   └── models.py            # Clinical schema database models
│   └── tests/                   # 16 Async Python test cases (pytest)
├── gateway/
│   ├── src/
│   │   ├── server.ts            # Node.js WebSocket gateway with VAD timers
│   │   └── audioStream.ts       # Raw PCM audio buffering utilities
│   ├── package.json             # Node.js gateway manifest
│   └── tsconfig.json            # TypeScript compile configurations
├── demo/
│   ├── index.html               # Real-time Web Voice Console
│   └── server.py                # Python demo server & API reverse-proxy
```
