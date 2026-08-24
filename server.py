import os
import io
import re
import json
import asyncio
from datetime import datetime
import httpx
import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from groq import Groq
import edge_tts

app = FastAPI(title="Veva Cloud Brain")

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "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")

http_client = httpx.Client(verify=False, timeout=30.0)
client = Groq(api_key=GROQ_API_KEY, http_client=http_client)

SELECTED_MODEL = "openai/gpt-oss-120b"
VOICE_MODEL = "en-IN-NeerjaNeural"

SYSTEM_PROMPT_TEMPLATE = """
You are Veva, an autonomous AI desktop secretary for Boss with full OS, Web, and Hardware access.
- Address the user as 'Boss'.
- Language: Short, crisp, natural conversational Hinglish (Latin alphabet only).
- NEVER use emojis in text output.
- Real-time System Clock: {live_datetime}

ACTION DISPATCH RULES (Return pure JSON when executing):
1. YouTube Playback:
   - Direct: {{"type": "youtube_play", "query": "<song name>"}}
   - Song change request without name: Ask "Boss, kaun sa gaana chala du?" (No JSON).
   - Follow-up song name: {{"type": "youtube_change_song", "query": "<song name>"}}

2. Web Page Reading / Extraction:
   {{"type": "web_read", "url": "<url>", "query": "<info to extract>"}}

3. WhatsApp Messaging:
   {{"type": "whatsapp_batch", "tasks": [{{"contact": "<Name>", "message": "<Message>"}}]}}

4. System & Hardware Controls:
   - Safe controls: {{"type": "hardware", "command": "brightness_up|brightness_down|volume_up|volume_down|mute|lock_pc|screenshot"}}
   - Shell Execution: {{"type": "shell", "command": "<powershell command>"}}
   - Wallpaper Change: {{"type": "change_wallpaper", "target": "<theme or path>"}}
   - Launch Application: {{"type": "open_app", "app": "<app_name>"}}
"""

def get_current_system_prompt() -> str:
    now = datetime.now()
    return SYSTEM_PROMPT_TEMPLATE.format(live_datetime=now.strftime("%A, %d %B %Y, %I:%M:%S %p"))

def clean_text_for_speech(text: str) -> str:
    text = re.sub(r'\{.*?\}', '', text, flags=re.DOTALL)
    text = re.sub(r'ACTION:\s*', '', text)
    emoji_pattern = re.compile(r'[\U00010000-\U0010ffff]', flags=re.UNICODE)
    return emoji_pattern.sub('', text).strip()

def extract_json_payload(text: str):
    match = re.search(r'\{.*\}', text, flags=re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            return None
    return None

def scrape_webpage(url: str, user_query: str) -> str:
    try:
        url = url.strip()
        if not url.startswith("http"):
            url = "https://www.sarkariresult.com" if "sarkariresult" in url.lower() else "https://" + url

        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        response = requests.get(url, headers=headers, timeout=12, verify=False)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            tag.decompose()

        clean_text = " ".join(soup.stripped_strings)[:6000]
        if not clean_text or len(clean_text) < 40:
            return "Website se data load nahi ho paya."

        prompt = f"Scraped Text:\n{clean_text}\n\nUser Query: '{user_query}'\nGive a short 2-sentence answer in Hinglish for Boss."
        res = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model=SELECTED_MODEL,
            temperature=0.2,
            max_tokens=200
        )
        return res.choices[0].message.content.strip()
    except Exception as e:
        return f"Web read error: {str(e)}"

@app.get("/")
def root():
    return {"status": "Veva Cloud Brain Active", "engine": "FastAPI + Groq"}

@app.websocket("/ws/live")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    conversation_history = []
    
    try:
        while True:
            raw_data = await websocket.receive_text()
            data = json.loads(raw_data)
            user_text = data.get("text", "").strip()
            
            if not user_text:
                continue

            live_system_prompt = get_current_system_prompt()
            if not conversation_history:
                conversation_history.append({"role": "system", "content": live_system_prompt})
            else:
                conversation_history[0] = {"role": "system", "content": live_system_prompt}

            conversation_history.append({"role": "user", "content": user_text})
            if len(conversation_history) > 10:
                conversation_history = [conversation_history[0]] + conversation_history[-8:]

            chat_completion = client.chat.completions.create(
                messages=conversation_history,
                model=SELECTED_MODEL,
                temperature=0.2,
                max_tokens=350
            )
            raw_reply = chat_completion.choices[0].message.content.strip()
            action_data = extract_json_payload(raw_reply)

            if action_data and action_data.get("type") == "web_read":
                scraped_answer = scrape_webpage(action_data.get("url", ""), action_data.get("query", ""))
                reply_text = scraped_answer
                action_payload = None
            else:
                reply_text = raw_reply
                action_payload = action_data

            conversation_history.append({"role": "assistant", "content": reply_text})

            clean_speech = clean_text_for_speech(reply_text) or "Done Boss."
            communicate = edge_tts.Communicate(clean_speech, VOICE_MODEL, rate="+6%", pitch="+1Hz")
            audio_bytes = bytearray()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_bytes.extend(chunk["data"])

            await websocket.send_text(json.dumps({
                "text": reply_text,
                "action": action_payload,
                "audio_bytes": list(audio_bytes)
            }))

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[!] Server Error: {e}")
