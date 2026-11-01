import os
import sys
import json
import socket
import asyncio
import urllib.parse
import urllib.request
import re
import traceback
import subprocess
from io import BytesIO
from pathlib import Path
import psutil
from bs4 import BeautifulSoup
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import FSInputFile
from google import genai
from google.genai import types as gtypes

BASE_DIR = Path(__file__).resolve().parent
SKILLS_DIR = BASE_DIR / "skills"
WORKSPACE_DIR = BASE_DIR / "workspace"
MEMORY_FILE = BASE_DIR / "memory.json"

SKILLS_DIR.mkdir(exist_ok=True)
WORKSPACE_DIR.mkdir(exist_ok=True)

if not MEMORY_FILE.exists():
    MEMORY_FILE.write_text(json.dumps({"user_profile": [], "learned_solutions": []}, indent=2), encoding="utf-8")

HOSTNAME = socket.gethostname()
IS_LAPTOP = os.path.exists("/home/nexzaelon")

# =====================================================================
# BULUT VA NOUTBUK UCHUN UNIVERSAL QUROLLAR
# =====================================================================

def search_web(query: str) -> str:
    """Internetdan ma'lumot, yangiliklar va dokumentatsiya qidirish."""
    try:
        from duckduckgo_search import DDGS
        results = DDGS().text(query, max_results=5)
        if not results:
            return "Hech narsa topilmadi."
        return "\n---\n".join([f"Title: {r.get('title')}\nURL: {r.get('href')}\nSnippet: {r.get('body')}" for r in results])
    except Exception as e:
        return f"Qidiruv xatosi: {e}"

def read_webpage(url: str) -> str:
    """Veb-sahifani o'qib, matnini olish."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        html = urllib.request.urlopen(req, timeout=12).read().decode("utf-8", errors="ignore")
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        return soup.get_text(separator="\n").strip()[:4000]
    except Exception as e:
        return f"Xato: {e}"

def run_python_code(code: str) -> str:
    """Python kodni ishga tushirish va natijasini olish."""
    script_path = WORKSPACE_DIR / "_temp.py"
    try:
        script_path.write_text(code, encoding="utf-8")
        res = subprocess.run([sys.executable, str(script_path)], capture_output=True, text=True, timeout=35, cwd=str(WORKSPACE_DIR))
        return f"[SUCCESS]:\n{res.stdout[:3000]}" if res.returncode == 0 else f"[ERROR]:\n{res.stderr[:3000]}"
    except Exception as e:
        return f"Xato: {e}"

def run_bash(command: str) -> str:
    """Linux terminalida buyruq bajarish."""
    try:
        res = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=35, cwd=str(WORKSPACE_DIR))
        return f"Exit {res.returncode}:\n{(res.stdout + res.stderr).strip()[:3000]}"
    except Exception as e:
        return f"Bash xatosi: {e}"

def save_knowledge(category: str, entry: str) -> str:
    """Foydalanuvchi qoidasi yoki yangi bilimni xotiraga saqlash."""
    try:
        data = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        data.setdefault(category, []).append(entry)
        MEMORY_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return f"Saqlandi: {entry}"
    except Exception as e:
        return f"Xato: {e}"

def check_system() -> str:
    """Server yoki Noutbuk resurslarini tekshirish."""
    cpu = psutil.cpu_percent(interval=0.2)
    mem = psutil.virtual_memory()
    mode = "💻 Noutbuk (Linux Mint)" if IS_LAPTOP else "☁️ Bulutli Server (24/7 Cloud)"
    return f"Rejim: {mode} | Host: {HOSTNAME} | CPU: {cpu}% | RAM: {mem.percent}%"

# =====================================================================
# AGI MIYASI
# =====================================================================

class CloudHybridBrain:
    def __init__(self):
        api_key = os.environ.get("GEMINI_API_KEY", "")
        self.client = genai.Client(api_key=api_key)
        self.history = []

    def chat(self, text: str) -> str:
        self.history.append(gtypes.Content(role="user", parts=[gtypes.Part.from_text(text=text)]))
        if len(self.history) > 20:
            self.history = self.history[-20:]

        tools_map = {
            "search_web": search_web,
            "read_webpage": read_webpage,
            "run_python_code": run_python_code,
            "run_bash": run_bash,
            "save_knowledge": save_knowledge,
            "check_system": check_system,
        }

        mode_desc = "Noutbukda (mahalliy)" if IS_LAPTOP else "24/7 Bulutli Serverda (Noutbuk o'chiq holatda ham)"
        sys_prompt = (
            f"Sen Aslbekning shaxsiy avtonom AI Agentisan. Hozir {mode_desc} ishlayapsan.\n"
            "Xato chiqsa o'zing tuzatib qayta urin. Tushunmagan narsangni foydalanuvchidan o'zbek tilida so'ra.\n"
            f"Xotira: {MEMORY_FILE.read_text(encoding='utf-8')}"
        )

        logs = []
        for step in range(1, 9):
            config = gtypes.GenerateContentConfig(
                system_instruction=sys_prompt,
                tools=list(tools_map.values()),
                temperature=0.2,
                automatic_function_calling=gtypes.AutomaticFunctionCallingConfig(disable=True),
            )
            resp = self.client.models.generate_content(
                model="gemini-2.5-flash",
                contents=self.history,
                config=config,
            )
            parts = resp.candidates[0].content.parts
            self.history.append(gtypes.Content(role="model", parts=parts))

            fn_calls = [p.function_call for p in parts if p.function_call]
            reply_text = "".join([p.text for p in parts if p.text]).strip()

            if not fn_calls:
                header = "\n".join(logs) + "\n\n" if logs else ""
                return f"{header}{reply_text or '✅ Bajarildi.'}"

            tool_parts = []
            for call in fn_calls:
                fn = call.name
                args = call.args or {}
                logs.append(f"⚙️ `[{step}] {fn}`")
                res = tools_map[fn](**args) if fn in tools_map else f"Noma'lum: {fn}"
                tool_parts.append(gtypes.Part.from_function_response(name=fn, response={"result": str(res)}))

            self.history.append(gtypes.Content(role="user", parts=tool_parts))

        return "\n".join(logs + ["⚠️ Qadamlar yakunlandi."])

# =====================================================================
# TELEGRAM BOT VA KEEP-ALIVE WEB SERVER (RENDER UXLAB QOLMASLIGI UCHUN)
# =====================================================================

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
brain = CloudHybridBrain()

@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    await msg.answer(
        f"☁️ **Omni-Agent 24/7 Cloud Brain Faol!**\n\n"
        f"📍 **Server:** `{HOSTNAME}`\n"
        f"Noutbukingiz o'chiq bo'lganda ham men 24/7 suhbatlashaman, kod yozaman va internetdan qidiraman!\n\n"
        f"Buyruqlar: `/status` | `/clear`",
        parse_mode="Markdown"
    )

@dp.message(Command("status"))
async def cmd_status(msg: types.Message):
    await msg.answer(f"📊 `{check_system()}`", parse_mode="Markdown")

@dp.message(Command("clear"))
async def cmd_clear(msg: types.Message):
    brain.history.clear()
    await msg.answer("🧹 Suhbat tarixi tozalandi.")

@dp.message()
async def handle_msg(msg: types.Message):
    if not msg.text:
        return
    wait = await msg.answer("☁️ _Bulutli Miya ishlamoqda..._", parse_mode="Markdown")
    try:
        ans = await asyncio.to_thread(brain.chat, msg.text)
        await wait.edit_text(ans[:4000])
    except Exception as e:
        await wait.edit_text(f"❌ Xato: {e}")

async def health_check(request):
    return web.Response(text="Omni-Agent 24/7 Cloud Brain is ALIVE!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 Keep-Alive Web Server {port}-portda ishga tushdi.")

async def main():
    await start_web_server()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
