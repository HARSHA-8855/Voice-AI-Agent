import WebSocket from 'ws';
import express from 'express';
import url from 'url';
import { AudioStreamHandler } from './audioStream';

const app = express();
const FASTAPI_URL = process.env.FASTAPI_URL || 'http://127.0.0.1:8000';
const PORT = process.env.PORT || 3000;

const server = app.listen(PORT, () => {
    console.log(`Gateway listening on port ${PORT}`);
    console.log(`FastAPI Backend URL configured to: ${FASTAPI_URL}`);
});

interface SessionState {
    sessionId: string;
    patientPhone: string;
    handler: AudioStreamHandler;
    silenceTimer: NodeJS.Timeout | null;
    isProcessing: boolean;
}

interface HeartbeatSocket extends WebSocket {
    isAlive?: boolean;
}

const wss = new WebSocket.Server({ server });
const sessions = new Map<WebSocket, SessionState>();

// Ping-Pong keepalive check every 30 seconds
const keepaliveInterval = setInterval(() => {
    wss.clients.forEach((ws: HeartbeatSocket) => {
        if (ws.isAlive === false) {
            console.log('[Gateway] Client did not respond to ping. Terminating connection.');
            return ws.terminate();
        }
        ws.isAlive = false;
        ws.ping();
    });
}, 30000);

wss.on('connection', (ws: HeartbeatSocket, req) => {
    ws.isAlive = true;

    // Parse session configuration from request query string
    const parsedUrl = url.parse(req.url || '', true);
    const querySessionId = parsedUrl.query.session_id as string;
    const queryPatientPhone = parsedUrl.query.patient_phone as string;

    const sessionId = querySessionId || `session_${Date.now()}`;
    const patientPhone = queryPatientPhone || '+919999999999';

    console.log(`[Gateway] New client connected. SessionID: ${sessionId}, Phone: ${patientPhone}`);

    // Create session state
    const session: SessionState = {
        sessionId,
        patientPhone,
        handler: new AudioStreamHandler(),
        silenceTimer: null,
        isProcessing: false
    };
    sessions.set(ws, session);

    ws.on('pong', () => {
        ws.isAlive = true;
    });

    ws.on('message', (message: WebSocket.RawData, isBinary: boolean) => {
        const currentSession = sessions.get(ws);
        if (!currentSession) return;

        if (isBinary) {
            // Message is raw binary audio chunk from MediaRecorder
            let buffer: Buffer;
            if (Buffer.isBuffer(message)) {
                buffer = message;
            } else if (message instanceof ArrayBuffer) {
                buffer = Buffer.from(message);
            } else {
                buffer = Buffer.concat(message);
            }
            currentSession.handler.append(buffer);

            // Reset silence VAD inactivity timer
            resetSilenceTimer(ws, currentSession);
        } else {
            // Message is text JSON control payload
            try {
                const textMessage = message.toString();
                const payload = JSON.parse(textMessage);

                if (payload.type === 'config') {
                    if (payload.session_id) currentSession.sessionId = payload.session_id;
                    if (payload.patient_phone) currentSession.patientPhone = payload.patient_phone;
                    console.log(`[Gateway] Session configured via config frame: ID=${currentSession.sessionId}, Phone=${currentSession.patientPhone}`);
                } else if (payload.type === 'end_of_speech') {
                    console.log(`[Gateway] Client explicitly triggered end_of_speech for session ${currentSession.sessionId}`);
                    triggerProcessing(ws, currentSession);
                }
            } catch (err) {
                console.error('[Gateway] Failed to parse JSON text control frame:', err);
            }
        }
    });

    const cleanup = () => {
        const currentSession = sessions.get(ws);
        if (currentSession) {
            if (currentSession.silenceTimer) {
                clearTimeout(currentSession.silenceTimer);
            }
            currentSession.handler.clear();
            sessions.delete(ws);
            console.log(`[Gateway] Connection closed. Session state cleaned up for ${currentSession.sessionId}`);
        }
    };

    ws.on('close', cleanup);
    ws.on('error', (err) => {
        console.error(`[Gateway] WebSocket error for session ${sessionId}:`, err);
        cleanup();
    });
});

wss.on('close', () => {
    clearInterval(keepaliveInterval);
});

// Reset VAD silence inactivity timer (silence >0.8s triggers backend call)
function resetSilenceTimer(ws: WebSocket, session: SessionState) {
    if (session.silenceTimer) {
        clearTimeout(session.silenceTimer);
    }

    session.silenceTimer = setTimeout(() => {
        console.log(`[Gateway] Silence detected (>0.8s) for session ${session.sessionId}. Initiating processing.`);
        triggerProcessing(ws, session);
    }, 800);
}

// Trigger backend pipeline process
async function triggerProcessing(ws: WebSocket, session: SessionState) {
    if (session.isProcessing) return;

    if (session.silenceTimer) {
        clearTimeout(session.silenceTimer);
        session.silenceTimer = null;
    }

    const audioSize = session.handler.size;
    if (audioSize === 0) {
        console.log(`[Gateway] Audio buffer is empty for session ${session.sessionId}. Skipping execution.`);
        return;
    }

    session.isProcessing = true;

    // Get aggregated buffer and clear handler to receive next speaking turn
    const audioBuffer = session.handler.getBuffer();
    session.handler.clear();

    console.log(`[Gateway] Forwarding audio to backend. Session: ${session.sessionId}, Size: ${audioSize} bytes`);

    const payload = {
        audio_base64: audioBuffer.toString('base64'),
        session_id: session.sessionId,
        patient_phone: session.patientPhone
    };

    const maxRetries = 3;
    let attempt = 0;
    let responseData: any = null;
    let success = false;

    while (attempt < maxRetries && !success) {
        attempt++;
        try {
            console.log(`[Gateway] POST to FastAPI /voice/process (Attempt ${attempt}/${maxRetries})...`);
            const response = await globalThis.fetch(`${FASTAPI_URL}/voice/process`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(payload)
            });

            if (response.ok) {
                responseData = await response.json();
                success = true;
            } else {
                const errorText = await response.text();
                console.error(`[Gateway] FastAPI backend failed with status ${response.status}: ${errorText}`);
                if (attempt < maxRetries) {
                    await new Promise(resolve => setTimeout(resolve, attempt * 1000)); // Exponential backoff delay
                }
            }
        } catch (err: any) {
            console.error(`[Gateway] FastAPI backend connection failed (Attempt ${attempt}): ${err.message}`);
            if (attempt < maxRetries) {
                await new Promise(resolve => setTimeout(resolve, attempt * 1000)); // Exponential backoff delay
            }
        }
    }

    session.isProcessing = false;

    if (success && responseData) {
        console.log(`[Gateway] Received backend response for session ${session.sessionId}. Dispatching back to client.`);
        ws.send(JSON.stringify({
            type: 'voice_response',
            audio_base64: responseData.audio_base64,
            response_text: responseData.response_text,
            detected_language: responseData.detected_language || 'en', // Fallback
            latency: responseData.latency_breakdown || {},
            trace: responseData.trace || {}
        }));
    } else {
        console.error(`[Gateway] Pipeline execution failed for session ${session.sessionId} after all ${maxRetries} attempts.`);
        ws.send(JSON.stringify({
            type: 'error',
            message: 'Internal communication failure with Voice AI backend service.'
        }));
    }
}
