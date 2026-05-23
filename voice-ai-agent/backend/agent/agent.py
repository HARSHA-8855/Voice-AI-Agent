import os
import json
import time
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from groq import AsyncGroq

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ClinicalAgent:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("GROQ_API_KEY")
        self.client = AsyncGroq(api_key=self.api_key)
        self.model = "llama-3.1-8b-instant"
        
        # Tool Schemas for Groq
        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": "check_availability",
                    "description": "Check available appointment slots for a specific doctor type and date.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "doctor_type": {
                                "type": "string",
                                "description": "Type of doctor (e.g., General Physician, Cardiologist, Dentist)"
                            },
                            "date": {
                                "type": "string",
                                "description": "Date to check availability for (YYYY-MM-DD)"
                            }
                        },
                        "required": ["doctor_type", "date"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "book_appointment",
                    "description": "Book a new appointment for a patient.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "patient_id": {"type": "string"},
                            "doctor_type": {"type": "string"},
                            "date": {"type": "string", "description": "YYYY-MM-DD"},
                            "time": {"type": "string", "description": "HH:MM"}
                        },
                        "required": ["doctor_type", "date", "time"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "cancel_appointment",
                    "description": "Cancel an existing appointment.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "appointment_id": {"type": "integer"}
                        },
                        "required": ["appointment_id"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "reschedule_appointment",
                    "description": "Reschedule an existing appointment to a new date and time.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "appointment_id": {"type": "integer"},
                            "new_date": {"type": "string", "description": "YYYY-MM-DD"},
                            "new_time": {"type": "string", "description": "HH:MM"}
                        },
                        "required": ["appointment_id", "new_date", "new_time"]
                    }
                }
            }
        ]

    def _build_system_prompt(self, language: str, history: Dict, session: Dict) -> str:
        from agent.prompts import get_system_prompt
        today = datetime.now().strftime("%Y-%m-%d")
        base_prompt = get_system_prompt()
        patient_id = history.get("id") or ""
        
        # Map ISO language codes to full names and native script directives
        lang_map = {
            "en": "English",
            "hi": "Hindi (हिंदी)",
            "ta": "Tamil (தமிழ்)",
            "te": "Telugu (తెలుగు)"
        }
        target_lang = lang_map.get(str(language).lower(), "English")
        
        prompt = f"""{base_prompt}

=== CRITICAL LANGUAGE CONSTRAINT ===
The target language for this turn is: **{target_lang}**.
YOU MUST GENERATE YOUR FINAL TEXT RESPONSE STRICTLY IN **{target_lang}**.
- If target language is Hindi, write ONLY in Hindi (Devanagari script).
- If target language is Tamil, write ONLY in Tamil script.
- If target language is English, write ONLY in English.
DO NOT use any other language or script.
Even if the patient spoke in another language, or the conversation history has turns in another language, you MUST ignore that and respond ONLY and entirely in **{target_lang}**.
=====================================

Patient info and history: {json.dumps(history)}
Current session state: {json.dumps(session)}
Today's date: {today}
Available actions: book, cancel, reschedule, check availability.
Always confirm details before booking. Suggest alternatives on conflict.

When calling book_appointment, always pass the patient's ID '{patient_id}' to the patient_id parameter.
When cancelling or rescheduling, identify the appointment ID (appointment_id) from the patient's history or previous turns."""
        return prompt

    async def process_request(
        self, 
        user_text: str, 
        session_context: Dict, 
        patient_history: Dict, 
        detected_language: str
    ) -> Dict[str, Any]:
        """
        Processes a user request using Groq LLM with tool-calling.
        """
        start_time = time.perf_counter()
        
        system_prompt = self._build_system_prompt(detected_language, patient_history, session_context)
        
        messages = [{"role": "system", "content": system_prompt}]
        
        # Load and append conversation turns from session_context
        for msg in session_context.get("messages", []):
            if msg.get("role") in ["user", "assistant"]:
                messages.append({"role": msg["role"], "content": msg["content"]})
                
        messages.append({"role": "user", "content": user_text})

        try:
            # 1. First LLM call to see if tools are needed
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=self.tools,
                tool_choice="auto",
                temperature=0.2
            )
            
            response_message = response.choices[0].message
            tool_calls = response_message.tool_calls
            
            result_data = {
                "response_text": response_message.content or "",
                "tool_called": None,
                "tool_result": None,
                "latency_ms": 0
            }

            if tool_calls:
                # For this implementation, we handle the first tool call
                tool_call = tool_calls[0]
                function_name = tool_call.function.name
                function_args = json.loads(tool_call.function.arguments)
                
                logger.info(f"Reasoning: User requested action requiring {function_name} with args {function_args}")
                
                result_data["tool_called"] = function_name
                
                # Mock tool execution (In reality, these would call backend/agent/tools.py)
                # Since we are just building the agent here, we simulate a successful tool result
                # but in a real app, this would be passed to the tools.py functions.
                result_data["tool_result"] = {
                    "status": "success",
                    "message": f"Simulated execution of {function_name}",
                    "args": function_args
                }
                
                # Usually, we'd feed the tool result back to the LLM for a final response,
                # but the requirement asks to return the tool_called and tool_result directly.
                # If no content was generated (common with tool calls), we might need a second call
                # or just describe the intent.
                if not result_data["response_text"]:
                    result_data["response_text"] = f"I am checking the details for your {function_name.replace('_', ' ')} request."

            latency_ms = int((time.perf_counter() - start_time) * 1000)
            result_data["latency_ms"] = latency_ms
            
            logger.info(f"LLM Latency: {latency_ms}ms")
            return result_data

        except Exception as e:
            logger.error(f"Error in ClinicalAgent: {e}")
            return {
                "response_text": "I'm sorry, I'm having trouble processing that right now.",
                "tool_called": None,
                "tool_result": {"error": str(e)},
                "latency_ms": int((time.perf_counter() - start_time) * 1000)
            }

async def run_agent(
    user_text: str,
    session_context: Dict[str, Any],
    patient_history: Dict[str, Any],
    detected_language: str = "English",
    db: Optional[Any] = None
) -> Dict[str, Any]:
    """
    Run the ClinicalAgent for a single turn, executing any requested tools 
    using the provided db session, and returning the agent's final text and tool actions.
    """
    agent = ClinicalAgent()
    agent_res = await agent.process_request(
        user_text=user_text,
        session_context=session_context,
        patient_history=patient_history,
        detected_language=detected_language
    )
    
    tool_called = agent_res.get("tool_called")
    tool_args = agent_res.get("tool_result", {}).get("args", {}) if agent_res.get("tool_result") else {}
    tool_exec_result = None
    response_text = agent_res.get("response_text", "")
    
    if tool_called and db is not None:
        from agent.tools import (
            check_availability, 
            book_appointment, 
            cancel_appointment, 
            reschedule_appointment, 
            ClinicalClinicError, 
            AppointmentConflictError
        )
        try:
            if tool_called == "check_availability":
                tool_exec_result = await check_availability(
                    db,
                    doctor_type=tool_args.get("doctor_type"),
                    date_str=tool_args.get("date")
                )
            elif tool_called == "book_appointment":
                patient_id = int(patient_history.get("id") or session_context.get("appointment_id") or 0)
                tool_exec_result = await book_appointment(
                    db,
                    patient_id=patient_id,
                    doctor_type=tool_args.get("doctor_type"),
                    date_str=tool_args.get("date"),
                    time_str=tool_args.get("time")
                )
            elif tool_called == "cancel_appointment":
                appt_id = int(tool_args.get("appointment_id"))
                patient_id = int(patient_history.get("id") or session_context.get("appointment_id") or 0)
                tool_exec_result = await cancel_appointment(
                    db,
                    appointment_id=appt_id,
                    patient_id=patient_id
                )
            elif tool_called == "reschedule_appointment":
                appt_id = int(tool_args.get("appointment_id"))
                patient_id = int(patient_history.get("id") or session_context.get("appointment_id") or 0)
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

        # Secondary reasoning pass
        if tool_exec_result:
            try:
                messages = [
                    {"role": "system", "content": agent._build_system_prompt(detected_language, patient_history, session_context)},
                    {"role": "user", "content": user_text},
                    {"role": "assistant", "content": None, "tool_calls": [
                        {"id": "call_1", "type": "function", "function": {"name": tool_called, "arguments": json.dumps(tool_args)}}
                    ]},
                    {"role": "tool", "tool_call_id": "call_1", "name": tool_called, "content": json.dumps(tool_exec_result)}
                ]
                
                second_res = await agent.client.chat.completions.create(
                    model=agent.model,
                    messages=messages,
                    temperature=0.2
                )
                response_text = second_res.choices[0].message.content or ""
            except Exception as e:
                logger.error(f"Secondary LLM generation failed: {e}")
                if tool_exec_result.get("status") == "success":
                    response_text = tool_exec_result.get("confirmation_message") or "Done."
                else:
                    response_text = tool_exec_result.get("message") or "Sorry, an error occurred."

    return {
        "response_text": response_text,
        "tool_called": tool_called,
        "tool_result": tool_exec_result or agent_res.get("tool_result"),
        "latency_ms": agent_res.get("latency_ms", 0)
    }

# Simple test function
if __name__ == "__main__":
    import asyncio
    
    async def test():
        agent = ClinicalAgent()
        history = {"last_visit": "2024-01-10", "condition": "Dental checkup"}
        session = {"current_step": "greeting"}
        
        print("Testing check availability...")
        res = await agent.process_request(
            "I want to book an appointment with a dentist tomorrow",
            session,
            history,
            "English"
        )
        print(json.dumps(res, indent=2))
        
        print("\nTesting Hindi response...")
        res_hi = await agent.process_request(
            "कल के लिए डेंटिस्ट के साथ अपॉइंटमेंट बुक करें",
            session,
            history,
            "Hindi"
        )
        print(json.dumps(res_hi, indent=2))

    asyncio.run(test())
