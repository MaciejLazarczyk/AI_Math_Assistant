#!/usr/bin/env python3
"""
Math & Physics AI Assistant - math_assistant.py
v1.3 - Chat | Pause | Verification | Feature-flags
pip install openai mss Pillow keyboard pygame
"""

import os, sys, time, threading, configparser, itertools
import base64, io, json, re, datetime, argparse
from pathlib import Path
from typing import Optional

# optional imports
try:
    import mss
    from PIL import Image
except ImportError:
    sys.exit("[ERROR] pip install mss Pillow")

try:
    import keyboard
except ImportError:
    sys.exit("[ERROR] pip install keyboard")

try:
    from openai import OpenAI, APIStatusError, APIConnectionError, RateLimitError
except ImportError:
    sys.exit("[ERROR] pip install openai")

PYGAME_AVAILABLE = False
try:
    import pygame
    pygame.mixer.pre_init(44100, -16, 2, 512)
    pygame.mixer.init()
    PYGAME_AVAILABLE = True
except Exception:
    print("[WARNING] pygame not available - audio disabled.")

# terminal colors
R, G, Y, C, B, RST = "\033[91m","\033[92m","\033[93m","\033[96m","\033[1m","\033[0m"

def ts() -> str: return datetime.datetime.now().strftime("%H:%M:%S")
def log_info(m): print(f"{C}[{ts()}] {m}{RST}")
def log_ok(m):   print(f"{G}[{ts()}] {m}{RST}")
def log_warn(m): print(f"{Y}[{ts()}] {m}{RST}")

def log_error_box(step, etype, line, expl):
    print(f"\n{R}{B}{'-'*62}\n  ERROR | Step {step} | Type: {etype}\n{'-'*62}{RST}")
    if line: print(f"{R}  Line: {line}{RST}")
    print(f"{R}  Explanation: {expl}{RST}")
    print(f"{R}{'-'*62}{RST}\n")

def log_false_positive_box(step, ai_expl, reason):
    print(f"\n{G}{B}{'-'*62}\n  VERIFICATION: FALSE POSITIVE | Step {step}\n{'-'*62}{RST}")
    print(f"{G}  AI reported: {ai_expl}{RST}")
    print(f"{G}  Verified: {reason}{RST}")
    print(f"{G}{'-'*62}{RST}\n")

def log_hint_box(hint):
    words = hint.split()
    buf, lines = [], []
    for w in words:
        if sum(len(x)+1 for x in buf)+len(w) > 56: lines.append("  "+" ".join(buf)); buf=[w]
        else: buf.append(w)
    if buf: lines.append("  "+" ".join(buf))
    print(f"\n{Y}{B}{'-'*62}\n  HINT\n{'-'*62}{RST}")
    for ln in lines: print(f"{Y}{ln}{RST}")
    print(f"{Y}{'-'*62}{RST}\n")

def log_chat_response(text):
    print(f"\n{C}{B}--- AI ---{RST}")
    for para in text.split("\n"):
        if para.strip(): print(f"{C}  {para}{RST}")
        else: print()
    print()


# --- config file picker ---

def pick_config(default: str) -> str:
    script_dir = Path(sys.argv[0]).resolve().parent
    configs = sorted(script_dir.glob("*.config"))
    if len(configs) <= 1:
        return default
    print(f"\n{B}{C}Multiple .config files found - select one:{RST}")
    print(f"{C}{'-'*62}{RST}")
    for i, c in enumerate(configs, 1):
        mark = f" {Y}<- current default{RST}" if c.name == Path(default).name else ""
        print(f"  {Y}[{i}]{RST} {c.name}{mark}")
    print(f"  {Y}[Enter]{RST} use default ({Path(default).name})\n")
    while True:
        raw = input(f"{C}Select [1-{len(configs)}]: {RST}").strip()
        if not raw: return default
        if raw.isdigit() and 1 <= int(raw) <= len(configs): return str(configs[int(raw)-1])
        print(f"{Y}Invalid selection.{RST}")


# --- API key rotation ---

class KeyRotator:
    def __init__(self, keys: list):
        if not keys: raise ValueError("API key list is empty.")
        self._cycle = itertools.cycle(keys)
        self._lock  = threading.Lock()
        self.count  = len(keys)

    def next(self) -> str:
        with self._lock: return next(self._cycle)


# --- memory management ---

class MemoryManager:
    def __init__(self, folder: str):
        self.folder     = Path(folder); self.folder.mkdir(parents=True, exist_ok=True)
        self.task_file  = self.folder/"current_task.txt"
        self.steps_file = self.folder/"steps.txt"
        self.meta_file  = self.folder/"meta.json"

    def save_task(self, desc: str):
        self.task_file.write_text(f"[TASK - {ts()}]\n{desc}\n", encoding="utf-8")
        self._write_meta({"step_count": 0, "started": datetime.datetime.now().isoformat()})

    def add_step(self, step: int, summary: str, has_error: bool, detail: str=""):
        status = "[ERROR]" if has_error else "[OK]"
        with open(self.steps_file,"a",encoding="utf-8") as f:
            f.write(f"\n[STEP {step} - {ts()} | {status}]\n{summary}\n")
            if has_error and detail: f.write(f"[ERROR DETAILS]: {detail}\n")
        m=self._read_meta(); m["step_count"]=step; self._write_meta(m)

    def add_chat_exchange(self, user_msg: str, ai_response: str, had_screenshot: bool):
        shot = " [+screenshot]" if had_screenshot else ""
        with open(self.steps_file,"a",encoding="utf-8") as f:
            f.write(f"\n[CHAT {ts()}{shot}]\nStudent: {user_msg}\nAI: {ai_response}\n")

    def get_context(self) -> str:
        parts=[]
        if self.task_file.exists():  parts.append(self.task_file.read_text(encoding="utf-8"))
        if self.steps_file.exists(): parts.append(self.steps_file.read_text(encoding="utf-8"))
        return "\n".join(parts).strip()

    def step_count(self) -> int: return self._read_meta().get("step_count", 0)
    def has_task(self)   -> bool: return self.task_file.exists()
    def status(self)     -> str:
        return "No active task" if not self.has_task() else f"Active | {self.step_count()} steps"

    def reset(self):
        for f in [self.task_file, self.steps_file, self.meta_file]:
            if f.exists(): f.unlink()
        log_info("Memory cleared.")

    def _read_meta(self) -> dict:
        if self.meta_file.exists():
            try: return json.loads(self.meta_file.read_text(encoding="utf-8"))
            except: pass
        return {}

    def _write_meta(self, data: dict):
        self.meta_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# --- screenshots ---

def take_screenshot() -> bytes:
    with mss.mss() as sct:
        raw = sct.grab(sct.monitors[0])
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        buf = io.BytesIO(); img.save(buf, format="JPEG", quality=85, optimize=True)
        return buf.getvalue()

def img_to_b64(data: bytes) -> str: return base64.b64encode(data).decode("utf-8")

def save_screenshot(data: bytes, folder: str, step: int):
    Path(folder).mkdir(parents=True, exist_ok=True)
    (Path(folder)/f"step_{step:04d}_{datetime.datetime.now().strftime('%H%M%S')}.jpg").write_bytes(data)


# --- AI client ---

_RETRYABLE = (RateLimitError, APIConnectionError, APIStatusError)

class AIClient:
    def __init__(self, rotator: KeyRotator, base_url: str, retry_delay: float=1.0, backoff: float=2.0):
        self.rotator     = rotator; self.base_url = base_url
        self.retry_delay = retry_delay; self.backoff = backoff

    def _call_with_retry(self, fn):
        last_exc = None; delay = self.retry_delay
        for attempt in range(1, self.rotator.count+1):
            client = OpenAI(api_key=self.rotator.next(), base_url=self.base_url)
            try: return fn(client)
            except _RETRYABLE as e:
                last_exc = e
                if attempt < self.rotator.count:
                    log_warn(f"[API] Key {attempt}/{self.rotator.count} failed ({type(e).__name__}). Retrying in {round(delay,1)}s...")
                    time.sleep(delay); delay *= self.backoff
                else:
                    log_warn(f"[API] Key {attempt}/{self.rotator.count} failed ({type(e).__name__}). All keys exhausted.")
            except Exception as e: raise e
        raise last_exc

    def extract_task(self, b64: str, model: str, system: str, prompt: str) -> str:
        def _c(client):
            content = client.chat.completions.create(model=model, messages=[
                {"role":"system","content":system},
                {"role":"user","content":[{"type":"text","text":prompt},
                    {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}"}}]}
            ], max_tokens=1024).choices[0].message.content
            return content.strip() if content else ""
        return self._call_with_retry(_c)

    def analyze_step(self, b64: str, context: str, step: int, model: str, system: str, prompt: str) -> dict:
        user_msg = f"""{prompt}

=== STEP HISTORY ===
{context}

=== STEP {step} ===
Respond ONLY as valid JSON:
{{
  "has_error": true/false,
  "error_line": "line with error or null",
  "error_type": "arithmetic | methodological | assumption | null",
  "explanation": "explanation or 'No errors'",
  "step_summary": "what the student did (1 sentence)"
}}"""
        def _c(client):
            raw = client.chat.completions.create(model=model, messages=[
                {"role":"system","content":system},
                {"role":"user","content":[{"type":"text","text":user_msg},
                    {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}"}}]}
            ], max_tokens=512, response_format={"type":"json_object"}).choices[0].message.content
            try: return json.loads(raw)
            except:
                m = re.search(r"\{.*\}", raw, re.DOTALL)
                if m:
                    try: return json.loads(m.group())
                    except: pass
                return {"has_error":False,"error_line":None,"error_type":None,
                        "explanation":f"JSON parse error: {raw[:200]}","step_summary":"?"}
        return self._call_with_retry(_c)

    def verify_error(self, error_text: str, model: str, prompt: str) -> dict:
        """Checks whether a reported error is a false positive (mathematically equivalent expressions)."""
        def _c(client):
            raw = client.chat.completions.create(model=model, messages=[
                {"role":"system","content":prompt},
                {"role":"user","content":f"Error report to verify:\n\n{error_text}"}
            ], max_tokens=256, response_format={"type":"json_object"}).choices[0].message.content
            try: return json.loads(raw)
            except: return {"is_false_positive":False,"reason":"Verification parse error"}
        return self._call_with_retry(_c)

    def get_hint(self, b64: str, context: str, model: str, system: str, hint_prompt: str) -> str:
        user_msg = f"""{hint_prompt}

=== CONTEXT ===
{context if context else "None - first look at the problem."}

Give ONE concise hint (2-4 sentences). Do not solve the problem."""
        def _c(client):
            content = client.chat.completions.create(model=model, messages=[
                {"role":"system","content":system},
                {"role":"user","content":[{"type":"text","text":user_msg},
                    {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}"}}]}
            ], max_tokens=300).choices[0].message.content
            return content.strip() if content else ""
        return self._call_with_retry(_c)

    def chat(self, messages: list, b64: Optional[str], model: str, system: str) -> str:
        """Free-form chat with an optional screenshot attached to the last message."""
        def _c(client):
            msgs = [{"role":"system","content":system}] + messages
            if b64:
                last = msgs[-1]
                msgs[-1] = {"role":"user","content":[
                    {"type":"text","text":last["content"]},
                    {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}"}}
                ]}
            content = client.chat.completions.create(
                model=model, messages=msgs, max_tokens=1024
            ).choices[0].message.content
            return content.strip() if content else ""
        return self._call_with_retry(_c)

    def generate_tts(self, text: str, model: str, voice: str, speed: float, out: str):
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        def _c(client): client.audio.speech.create(model=model,voice=voice,input=text,speed=speed).stream_to_file(out)
        self._call_with_retry(_c)


# --- audio ---

def play_audio(path: str):
    if not PYGAME_AVAILABLE: return
    try:
        pygame.mixer.music.load(path); pygame.mixer.music.play()
        while pygame.mixer.music.get_busy(): time.sleep(0.05)
    except Exception as e: log_warn(f"[AUDIO] {e}")


# --- main class ---

class MathAssistant:
    def __init__(self, config_path: str="config.config"):
        cfg = configparser.ConfigParser(interpolation=None)
        if not os.path.exists(config_path): sys.exit(f"[ERROR] Config file not found: {config_path}")
        cfg.read(config_path, encoding="utf-8")

        # API
        keys = [k.strip() for k in cfg.get("API","keys").split(",") if k.strip()]
        self.rotator = KeyRotator(keys)
        self.ai = AIClient(self.rotator,
            cfg.get("API","base_url",fallback="https://api.naga.ac/v1"),
            cfg.getfloat("API","retry_delay",fallback=1.0),
            cfg.getfloat("API","backoff_factor",fallback=2.0))

        # models
        self.vision_model       = cfg.get("MODELS","vision_model",       fallback="google/gemini-2.5-flash")
        self.tts_model          = cfg.get("MODELS","tts_model",           fallback="tts-1")
        self.tts_voice          = cfg.get("MODELS","tts_voice",           fallback="nova")
        self.tts_speed          = cfg.getfloat("MODELS","tts_speed",      fallback=1.1)
        self.verification_model = cfg.get("MODELS","verification_model",  fallback="gpt-4o-mini")

        # behavior
        self.mode           = cfg.get("BEHAVIOR","mode",             fallback="both").lower()
        self.timer_interval = cfg.getint("BEHAVIOR","timer_interval", fallback=40)
        self.initial_delay  = cfg.getfloat("BEHAVIOR","initial_delay", fallback=3.0)
        self.shot_key       = cfg.get("BEHAVIOR","screenshot_key",   fallback="f9")
        self.hint_key       = cfg.get("BEHAVIOR","hint_key",         fallback="f8")
        self.chat_key       = cfg.get("BEHAVIOR","chat_key",         fallback="f7")
        self.pause_key      = cfg.get("BEHAVIOR","pause_key",        fallback="f12")
        self.next_key       = cfg.get("BEHAVIOR","next_task_key",    fallback="f10")
        self.quit_key       = cfg.get("BEHAVIOR","quit_key",         fallback="f11")
        self.save_shots     = cfg.getboolean("BEHAVIOR","save_screenshots",fallback=True)

        # paths
        self.mem_dir  = cfg.get("PATHS","memory_folder",     fallback="memory")
        self.tts_dir  = cfg.get("PATHS","tts_folder",        fallback="tts")
        self.shot_dir = cfg.get("PATHS","screenshots_folder", fallback="screenshots")

        # feature flags
        def feat(key, default=True): return cfg.getboolean("FEATURES", key, fallback=default)
        self.feat_chat         = feat("chat")
        self.feat_pause        = feat("pause")
        self.feat_hint         = feat("hint")
        self.feat_verification = feat("error_verification")
        self.feat_tts          = cfg.getboolean("TTS","enabled",fallback=False)

        # prompts
        self.sys_prompt          = cfg.get("PROMPTS","system_prompt")
        self.first_prompt        = cfg.get("PROMPTS","first_screenshot_prompt")
        self.analysis_prompt     = cfg.get("PROMPTS","analysis_prompt")
        self.tts_template        = cfg.get("PROMPTS","tts_error_template",
            fallback="Error in step {step}. Type: {error_type}. {explanation}.")
        self.hint_prompt         = cfg.get("PROMPTS","hint_prompt",
            fallback="You are a tutor. Give a concise hint without revealing the solution.")
        self.verification_prompt = cfg.get("PROMPTS","verification_prompt",
            fallback=("You are a mathematical correctness verifier. You will receive an error report from an AI assistant. "
                      "Check whether the expressions described as incorrect are actually mathematically equivalent "
                      "(e.g. '1-(x-2)' equals '3-x'). If so, it's a false positive. "
                      "Respond ONLY as JSON: {\"is_false_positive\": true/false, \"reason\": \"brief explanation\"}"))
        self.chat_system_prompt  = cfg.get("PROMPTS","chat_system_prompt",
            fallback="You are a helpful math and physics tutor. Be concise and specific. Do not solve problems for the student.")

        # internal state
        self.memory                      = MemoryManager(self.mem_dir)
        self._running                    = False
        self._busy                       = False
        self._paused                     = False
        self._chat_mode                  = False
        self._lock                       = threading.Lock()
        self._step                       = 0
        self._chat_history: list         = []
        self._chat_screenshot_pending    = False
        self._chat_screenshot_data: Optional[bytes] = None
        self._chat_thread: Optional[threading.Thread] = None

    # --- step analysis ---

    def _process(self):
        with self._lock:
            if self._busy:   log_warn("Previous analysis still running, skipping."); return
            if self._paused: log_info("Paused - skipping screenshot."); return
            self._busy = True
        try:
            data = take_screenshot(); b64 = img_to_b64(data)
            if self.save_shots: save_screenshot(data, self.shot_dir, self._step)

            if not self.memory.has_task():
                log_info("First screenshot - identifying task...")
                desc = self.ai.extract_task(b64, self.vision_model, self.sys_prompt, self.first_prompt)
                self.memory.save_task(desc); self._step = 1
                log_ok(f"Task saved:\n{desc[:250]}{'...' if len(desc)>250 else ''}")
                return

            self._step += 1
            log_info(f"Analyzing step {self._step}...")
            ctx    = self.memory.get_context()
            result = self.ai.analyze_step(b64, ctx, self._step, self.vision_model,
                                          self.sys_prompt, self.analysis_prompt)

            has_error   = result.get("has_error",   False)
            error_line  = result.get("error_line",  "") or ""
            error_type  = result.get("error_type",  "") or ""
            explanation = result.get("explanation", "")
            summary     = result.get("step_summary","")

            if has_error:
                if self.feat_verification and explanation:
                    log_info("Verifying error...")
                    error_text = (f"Type: {error_type}\n"
                                  f"Line: {error_line}\n"
                                  f"Explanation: {explanation}")
                    try:
                        vr = self.ai.verify_error(error_text, self.verification_model,
                                                  self.verification_prompt)
                        if vr.get("is_false_positive"):
                            log_false_positive_box(self._step, explanation, vr.get("reason",""))
                            self.memory.add_step(self._step, summary, False,
                                                 f"[FALSE POSITIVE] {vr.get('reason','')}")
                            return
                    except Exception as ve:
                        log_warn(f"[VERIFY] {ve}")
                log_error_box(self._step, error_type, error_line, explanation)
                self.memory.add_step(self._step, summary, True,
                                     f"{error_type}: {error_line} - {explanation}")
                if self.feat_tts: self._speak(self._step, error_type, error_line, explanation)
            else:
                log_ok(f"Step {self._step}: {summary or 'No errors'}")
                self.memory.add_step(self._step, summary, False)

        except Exception as e: log_warn(f"[SCRIPT ERROR] {type(e).__name__}: {e}")
        finally:
            with self._lock: self._busy = False

    # --- F9 handler (normal mode / chat mode router) ---

    def _f9_handler(self):
        if self._chat_mode: self._chat_take_screenshot()
        else:               self._process()

    # --- chat mode (F7) ---

    def _toggle_chat(self):
        if self._chat_mode:
            self._exit_chat()
        else:
            if self._chat_thread and self._chat_thread.is_alive():
                log_warn("Chat is still closing - press Enter, then F7 again.")
                return
            self._enter_chat()

    def _enter_chat(self):
        self._chat_mode               = True
        self._chat_history            = []
        self._chat_screenshot_pending = False
        self._chat_screenshot_data    = None
        self._chat_thread = threading.Thread(target=self._chat_loop, daemon=True)
        self._chat_thread.start()

    def _exit_chat(self):
        self._chat_mode = False
        print(f"\r{C}[{ts()}] Leaving chat - press {B}Enter{RST}{C} once to confirm.{RST} ")

    def _chat_loop(self):
        log_info(f"Chat mode. Type a message and press Enter. "
                 f"{self.shot_key.upper()} = attach screenshot. {self.chat_key.upper()} = exit.")
        while self._chat_mode:
            try:
                text = input(f"{C}> {RST}").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not self._chat_mode:
                break
            if not text:
                continue
            shot = self._chat_screenshot_data if self._chat_screenshot_pending else None
            self._chat_screenshot_pending = False
            self._chat_screenshot_data    = None
            threading.Thread(target=self._send_chat_message, args=(text, shot), daemon=True).start()
        log_info("Chat mode closed.")

    def _chat_take_screenshot(self):
        """F9 in chat mode - captures a screenshot to attach to the next message."""
        with self._lock:
            self._chat_screenshot_data    = take_screenshot()
            self._chat_screenshot_pending = True
        print(f"\n{C}[{ts()}] Screenshot ready - type your question and press Enter{RST}")

    def _send_chat_message(self, text: str, screenshot_data: Optional[bytes]):
        with self._lock:
            if self._busy: log_warn("[CHAT] Previous request still in progress, try again."); return
            self._busy = True
        try:
            b64 = img_to_b64(screenshot_data) if screenshot_data else None
            self._chat_history.append({"role":"user","content":text})
            log_info(f"Sending{' +screenshot' if b64 else ''}...")
            ctx     = self.memory.get_context()
            sys_ctx = self.chat_system_prompt + (f"\n\n=== TASK AND STEP CONTEXT ===\n{ctx}" if ctx else "")
            response = self.ai.chat(self._chat_history.copy(), b64, self.vision_model, sys_ctx)
            self._chat_history.append({"role":"assistant","content":response})
            log_chat_response(response)
            self.memory.add_chat_exchange(text, response, screenshot_data is not None)
        except Exception as e:
            log_warn(f"[CHAT] {type(e).__name__}: {e}")
        finally:
            with self._lock: self._busy = False

    # --- pause (F12) ---

    def _toggle_pause(self):
        self._paused = not self._paused
        if self._paused: log_warn("Automatic screenshots paused. F12 to resume.")
        else:            log_ok("Automatic screenshots resumed.")

    # --- hint (F8) ---

    def _request_hint(self):
        with self._lock:
            if self._busy: log_warn("Analysis in progress - please wait."); return
            self._busy = True
        try:
            log_info("Fetching hint...")
            b64  = img_to_b64(take_screenshot())
            ctx  = self.memory.get_context()
            hint = self.ai.get_hint(b64, ctx, self.vision_model, self.sys_prompt, self.hint_prompt)
            log_hint_box(hint)
        except Exception as e: log_warn(f"[HINT] {e}")
        finally:
            with self._lock: self._busy = False

    # --- TTS ---

    def _speak(self, step, etype, line, expl):
        text = self.tts_template.format(step=step, error_type=etype, error_line=line, explanation=expl)
        try:
            fname = Path(self.tts_dir)/f"error_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.mp3"
            log_info(f"TTS -> {fname.name}")
            self.ai.generate_tts(str(text), self.tts_model, self.tts_voice, self.tts_speed, str(fname))
            threading.Thread(target=play_audio, args=(str(fname),), daemon=True).start()
        except Exception as e: log_warn(f"[TTS] {e}")

    # --- timer ---

    def _timer_worker(self):
        log_info(f"Starting in {self.initial_delay}s...")
        time.sleep(self.initial_delay)
        self._process()
        while self._running:
            time.sleep(self.timer_interval)
            if self._running and not self._chat_mode and not self._paused:
                self._process()

    # --- reset ---

    def _reset(self):
        with self._lock:
            if self._busy: log_warn("Waiting for current analysis to finish before resetting."); return
        self.memory.reset(); self._step = 0
        self._chat_history = []
        log_ok("Ready for new task. Next screenshot will capture the task description.")

    # --- startup ---

    def start(self):
        enabled = lambda name, flag: f"{G}+ {name}{RST}" if flag else f"{R}- {name}{RST}"
        print(f"\n{B}{C}Math & Physics AI Assistant  |  naga.ac v1.3{RST}")
        print(f"{C}Model: {self.vision_model}{RST}")
        print(f"{C}{'-'*62}{RST}")
        kmap = [(self.shot_key, "Screenshot + analyze", True)]
        if self.feat_chat:  kmap.append((self.chat_key,  "Chat mode",    True))
        if self.feat_hint:  kmap.append((self.hint_key,  "Hint",         True))
        if self.feat_pause: kmap.append((self.pause_key, "Pause/resume", True))
        kmap.append((self.next_key, "New task", True))
        kmap.append((self.quit_key, "Quit",     True))
        for k, label, _ in kmap: print(f"  {Y}{k.upper():<6}{RST} {label}")
        print(f"{C}{'-'*62}{RST}")
        feats = [
            enabled("Chat",         self.feat_chat),
            enabled("Hints",        self.feat_hint),
            enabled("Pause",        self.feat_pause),
            enabled("Verification", self.feat_verification),
            enabled("TTS",          self.feat_tts),
        ]
        print("  Features: " + " | ".join(feats))
        print(f"  API keys: {self.rotator.count} | Timer: {self.timer_interval}s | Mode: {self.mode.upper()}")
        print(f"{C}{'='*62}{RST}\n")
        log_info(f"Memory: {self.memory.status()}")

        self._running = True

        # hotkeys
        keyboard.add_hotkey(self.shot_key, self._f9_handler)
        keyboard.add_hotkey(self.next_key, self._reset)
        keyboard.add_hotkey(self.quit_key, self.stop)
        if self.feat_chat:  keyboard.add_hotkey(self.chat_key,  self._toggle_chat)
        if self.feat_hint:  keyboard.add_hotkey(self.hint_key,  self._request_hint)
        if self.feat_pause: keyboard.add_hotkey(self.pause_key, self._toggle_pause)

        if self.mode in ("timer","both"):
            log_info(f"Timer: every {self.timer_interval}s")
            threading.Thread(target=self._timer_worker, daemon=True).start()

        print()
        try: keyboard.wait()
        except KeyboardInterrupt: self.stop()

    def stop(self):
        log_info("Shutting down...")
        self._running = False; keyboard.unhook_all(); sys.exit(0)


# entry point

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Math & Physics AI Assistant v1.3")
    parser.add_argument("--config", default="config.config",
                        help="Path to config file (default: config.config)")
    args   = parser.parse_args()
    chosen = pick_config(args.config)
    if chosen != args.config: print(f"{C}Using: {chosen}{RST}")
    MathAssistant(config_path=chosen).start()
