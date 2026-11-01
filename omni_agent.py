import os
import sys
import json
import time
import subprocess
import urllib.parse
import urllib.request
import re
import traceback
import importlib.util
from io import BytesIO
from pathlib import Path
import psutil
from PIL import Image
import mss
import pyautogui
from bs4 import BeautifulSoup
from google import genai
from google.genai import types

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.15

BASE_DIR = Path(__file__).resolve().parent
SKILLS_DIR = BASE_DIR / "skills"
WORKSPACE_DIR = BASE_DIR / "workspace"
MEMORY_FILE = BASE_DIR / "memory.json"

SKILLS_DIR.mkdir(exist_ok=True)
WORKSPACE_DIR.mkdir(exist_ok=True)

if not MEMORY_FILE.exists():
    MEMORY_FILE.write_text(json.dumps({
        "user_profile": [],
        "learned_solutions": [],
        "system_rules": []
    }, indent=2, ensure_ascii=False), encoding="utf-8")

# Global state for sending files or vision back
PENDING_FILES_TO_SEND = []
ATTACHED_SCREENSHOT = None

# =====================================================================
# 1. AVTONOM TIZIM VA KOGNITIV QUROLLAR (TOOLS)
# =====================================================================

def run_bash(command: str, timeout: int = 45) -> str:
    """
    Linux Mint terminalida istalgan buyruqni bajarish.
    Agar xato (nonzero exit code) chiqsa, stderr to'liq qaytariladi — xatoni tahlil qilib o'zing tuzat!
    """
    try:
        res = subprocess.run(
            command, shell=True, capture_output=True, text=True, 
            timeout=timeout, cwd=str(WORKSPACE_DIR)
        )
        stdout = res.stdout.strip()
        stderr = res.stderr.strip()
        status = "SUCCESS" if res.returncode == 0 else f"ERROR (Exit Code {res.returncode})"
        out = f"[{status}]\n"
        if stdout:
            out += f"STDOUT:\n{stdout[:3000]}\n"
        if stderr:
            out += f"STDERR:\n{stderr[:3000]}\n"
        return out.strip()
    except subprocess.TimeoutExpired:
        return "[TIMEOUT]: Buyruq 45 soniyadan oshib ketdi yoki fonda qoldi. Agar u GUI/server bo'lsa 'nohup ... &' bilan chaqir."
    except Exception as e:
        return f"[EXCEPTION]: {traceback.format_exc()}"

def run_python_code(code: str) -> str:
    """
    Murakkab hisob-kitob, ma'lumotlar tahlili yoki avtomatlashtirish uchun Python kodni darhol ishga tushirish.
    Agar kodda xato bo'lsa, Traceback qaytadi — uni o'qib kodni tuzatib qayta ishga tushir!
    """
    script_path = WORKSPACE_DIR / "_temp_exec.py"
    try:
        script_path.write_text(code, encoding="utf-8")
        python_bin = str(BASE_DIR / "venv" / "bin" / "python")
        res = subprocess.run([python_bin, str(script_path)], capture_output=True, text=True, timeout=45, cwd=str(WORKSPACE_DIR))
        if res.returncode == 0:
            return f"[PYTHON SUCCESS]:\n{res.stdout[:3500] or 'Kod xatosiz bajarildi (bo`sh chiqish).'}"
        else:
            return f"[PYTHON ERROR - ANALYZE AND FIX]:\n{res.stderr[:3500]}"
    except Exception as e:
        return f"[EXECUTION ERROR]: {e}"

def create_or_update_skill(skill_name: str, python_code: str, test_call_code: str = "") -> str:
    """
    O'z-o'zini rivojlantirish: yangi doimiy Python qurol (skill) yaratish va uni avtomatik sinovdan o'tkazish.
    - skill_name: funksiya va fayl nomi (masalan: 'parse_pdf')
    - python_code: Funksiya kodi (docstring va type hints bo'lishi SHART)
    - test_call_code: Ixtiyoriy, funksiyani tekshirish uchun 1 qator kod
    """
    try:
        skill_path = SKILLS_DIR / f"{skill_name}.py"
        skill_path.write_text(python_code.strip() + "\n", encoding="utf-8")
        
        # Sintaksis va import xatolarini darhol tekshirish
        spec = importlib.util.spec_from_file_location(skill_name, skill_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        
        if not hasattr(mod, skill_name):
            return f"[SKILL ERROR]: Fayl ichida `{skill_name}` nomli funksiya topilmadi! Funksiya nomini to'g'rilab qayta yoz."
            
        return f"[SKILL CREATED & VERIFIED]: `{skill_name}` muvaffaqiyatli yaratildi va xotiraga ulandi!"
    except Exception as e:
        return f"[SKILL BROKEN - AUTO-FIX REQUIRED]: Kodda xato bor, tuzatib qayta chaqir:\n{traceback.format_exc()}"

def search_web(query: str) -> str:
    """Internetdan ma'lumot, xato yechimlari, dokumentatsiya yoki yangiliklarni qidirish."""
    try:
        from duckduckgo_search import DDGS
        results = DDGS().text(query, max_results=5)
        if not results:
            return "Qidiruv bo'yicha hech narsa topilmadi."
        formatted = []
        for r in results:
            formatted.append(f"Title: {r.get('title')}\nURL: {r.get('href')}\nSnippet: {r.get('body')}\n")
        return "\n---\n".join(formatted)
    except Exception as e:
        return f"Qidiruv xatosi: {e}"

def read_webpage(url: str) -> str:
    """Veb-sahifa manzilini o'qib, ichidagi asosiy matn va kodlarni sug'urib olish."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"})
        html = urllib.request.urlopen(req, timeout=12).read().decode("utf-8", errors="ignore")
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = re.sub(r'\n\s*\n', '\n\n', soup.get_text(separator="\n")).strip()
        return f"Sahifa mazmuni ({url}):\n{text[:4000]}"
    except Exception as e:
        return f"Sahifani o'qishda xato: {e}"

def look_at_screen(reason: str = "Ekran holatini tahlil qilish") -> str:
    """
    Noutbuk ekranini rasmga olib, vizual ko'rish (Vision).
    Ekranda nima ochiqligini, xato oynalarini yoki UI elementlarni ko'rish uchun chaqir.
    """
    global ATTACHED_SCREENSHOT
    try:
        with mss.MSS() as sct:
            sct_img = sct.grab(sct.monitors[1])
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
        img.thumbnail((1280, 720), Image.Resampling.LANCZOS)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=75)
        ATTACHED_SCREENSHOT = buf.getvalue()
        return f"Ekran rasmi olindi ({reason}) va navbatdagi tahlil uchun biriktirildi."
    except Exception as e:
        return f"Ekran rasmini olishda xato: {e}"

def gui_control(action: str, x: int = 0, y: int = 0, text: str = "") -> str:
    """
    Sichqoncha va klaviaturani boshqarish (0-1000 masshtabda koordinatalar).
    action: 'click', 'double_click', 'right_click', 'type', 'hotkey' (masalan 'ctrl+c'), 'press' (masalan 'enter')
    """
    try:
        sw, sh = pyautogui.size()
        if action in ["click", "double_click", "right_click"]:
            rx, ry = int((x / 1000.0) * sw), int((y / 1000.0) * sh)
            pyautogui.moveTo(rx, ry, duration=0.2)
            if action == "click": pyautogui.click(rx, ry)
            elif action == "double_click": pyautogui.doubleClick(rx, ry)
            elif action == "right_click": pyautogui.rightClick(rx, ry)
            return f"GUI {action} bajarildi: ({rx}, {ry})"
        elif action == "type":
            proc = subprocess.Popen(['xclip', '-selection', 'clipboard'], stdin=subprocess.PIPE)
            proc.communicate(input=text.encode('utf-8'))
            pyautogui.hotkey('ctrl', 'v')
            return f"Matn yozildi: {text}"
        elif action == "hotkey":
            keys = [k.strip() for k in text.split("+")]
            pyautogui.hotkey(*keys)
            return f"Hotkey bosildi: {text}"
        elif action == "press":
            pyautogui.press(text.strip())
            return f"Tugma bosildi: {text}"
        return "Noma'lum GUI harakat."
    except Exception as e:
        return f"GUI xatosi: {e}"

def manage_file(action: str, filepath: str, content: str = "") -> str:
    """Faylni o'qish ('read'), yozish ('write'), yoki Telegram orqali foydalanuvchiga yuborish ('send_to_user')."""
    global PENDING_FILES_TO_SEND
    try:
        p = Path(filepath).expanduser().resolve()
        if action == "write":
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return f"Fayl muvaffaqiyatli yozildi: {p}"
        elif action == "read":
            if not p.exists():
                return f"[FILE ERROR]: Fayl topilmadi: {p}"
            return p.read_text(encoding="utf-8", errors="ignore")[:4500]
        elif action == "send_to_user":
            if not p.exists():
                return f"[FILE ERROR]: Jo'natiladigan fayl topilmadi: {p}"
            PENDING_FILES_TO_SEND.append(str(p))
            return f"Fayl foydalanuvchiga Telegram orqali yuborish ro'yxatiga qo'shildi: {p.name}"
        return "Noma'lum action ('read', 'write', 'send_to_user')."
    except Exception as e:
        return f"Fayl xatosi: {e}"

def save_knowledge(category: str, entry: str) -> str:
    """
    Muhim tajriba, xato yechimi yoki foydalanuvchi qoidasini xotiraga muhrlash.
    category: 'user_profile', 'learned_solutions', 'system_rules'
    """
    try:
        data = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        if category not in data:
            data[category] = []
        if entry not in data[category]:
            data[category].append(entry)
            MEMORY_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return f"Xotiraga saqlandi [{category}]: {entry}"
    except Exception as e:
        return f"Xotira xatosi: {e}"

def play_youtube(query: str) -> str:
    """YouTube dan to'g'ridan-to'g'ri birinchi videoni topib Chrome brauzerida ijro etish."""
    try:
        encoded = urllib.parse.quote_plus(query)
        search_url = f"https://www.youtube.com/results?search_query={encoded}"
        curl_cmd = [
            "curl", "-s", "-L",
            "-H", "User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0",
            search_url
        ]
        res = subprocess.run(curl_cmd, capture_output=True, text=True, timeout=10)
        video_ids = re.findall(r'/watch\?v=([a-zA-Z0-9_-]{11})', res.stdout)
        target_id = next((vid for vid in video_ids if vid != "dQw4w9WgXcQ"), None)
        video_url = f"https://www.youtube.com/watch?v={target_id}" if target_id else search_url

        subprocess.Popen(["google-chrome", video_url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.0)
        subprocess.run(["wmctrl", "-a", "Google Chrome"], check=False)
        return f"Video ijro etilmoqda: {video_url}"
    except Exception as e:
        return f"YouTube xatosi: {e}"

def control_hardware(target: str, value: str) -> str:
    """Apparat boshqaruvi: target='volume' ('50%', '+10%', 'mute') yoki target='brightness' ('70%')."""
    try:
        if target == "volume":
            if value == "mute":
                subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"], check=False)
                return "Ovoz holati o'zgartirildi."
            subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", value], check=False)
            return f"Ovoz {value} ga sozlandi."
        elif target == "brightness":
            subprocess.run(["brightnessctl", "set", value], check=False)
            return f"Yorug'lik {value} ga sozlandi."
        return "Noma'lum target."
    except Exception as e:
        return f"Xato: {e}"

def check_system() -> str:
    """Tizim diagnostikasi: CPU, RAM, Disk, aktiv jarayonlar va tarmoq."""
    cpu = psutil.cpu_percent(interval=0.2)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    procs = sorted(psutil.process_iter(['name', 'memory_percent']), key=lambda x: x.info['memory_percent'] or 0, reverse=True)[:5]
    top_p = ", ".join([f"{p.info['name']} ({p.info['memory_percent']:.1f}%)" for p in procs])
    return (
        f"CPU: {cpu}% | RAM: {mem.percent}% ({mem.available // (1024*1024)}MB bo'sh) | "
        f"Disk: {disk.free // (1024**3)}GB bo'sh\nEng ko'p RAM yeyayotganlar: {top_p}"
    )

# =====================================================================
# 2. DINAMIK QUROLLAR YUKLAGICHI
# =====================================================================

def load_all_tools() -> dict:
    tools = {
        "run_bash": run_bash,
        "run_python_code": run_python_code,
        "create_or_update_skill": create_or_update_skill,
        "search_web": search_web,
        "read_webpage": read_webpage,
        "look_at_screen": look_at_screen,
        "gui_control": gui_control,
        "manage_file": manage_file,
        "save_knowledge": save_knowledge,
        "play_youtube": play_youtube,
        "control_hardware": control_hardware,
        "check_system": check_system,
    }
    for file in SKILLS_DIR.glob("*.py"):
        skill_name = file.stem
        try:
            spec = importlib.util.spec_from_file_location(skill_name, file)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, skill_name):
                tools[skill_name] = getattr(mod, skill_name)
        except Exception:
            pass
    return tools

# =====================================================================
# 3. AVTONOM REJA, O'Z-O'ZINI TUZATISH VA MULOQOT MIYASI
# =====================================================================

class AutonomousAGIBrain:
    def __init__(self):
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            try:
                for line in (Path.home() / ".bashrc").read_text().splitlines():
                    if "export GEMINI_API_KEY=" in line:
                        api_key = line.split("=")[1].strip().strip('"').strip("'")
            except Exception:
                pass
        self.client = genai.Client(api_key=api_key)
        self.history = []

    def get_system_prompt(self, tools_map: dict) -> str:
        memory_data = MEMORY_FILE.read_text(encoding="utf-8")
        custom_skills = [k for k in tools_map.keys() if k not in [
            "run_bash", "run_python_code", "create_or_update_skill", "search_web",
            "read_webpage", "look_at_screen", "gui_control", "manage_file",
            "save_knowledge", "play_youtube", "control_hardware", "check_system"
        ]]
        return (
            "Sen Aslbekning Linux Mint kompyuterida yashovchi o'ta kuchli, avtonom, o'z-o'zini tuzatuvchi (Self-Healing) va rivojlantiruvchi (Self-Evolving) AI Agentisan.\n\n"
            "SENING OLTIIN QOIDALARING (AGI PRINSIPLARI):\n"
            "1. AVTONOM XATO TUZATISH (SELF-HEALING): Agar `run_bash`, `run_python_code` yoki `create_or_update_skill` xato qaytarsa (masalan, ModuleNotFoundError, SyntaxError, Command not found), "
            "TASLIM BO'LMA VA FOYDALANUVCHIGA XATONI TASHLAB QO'YMA! Darhol xatoni o'qi, yetishmayotgan kutubxonani o'rnat (`~/omni_agent/venv/bin/pip install ...`), kodni tuzat va vazifa muvaffaqiyatli bajarilmaguncha qayta urinib ko'r!\n"
            "2. MULOQOT VA ANIQLASHTIRISH: Agar foydalanuvchining topshirig'i mavhum bo'lsa yoki muhim qaror kerak bo'lsa, taxmin qilmasdan avval foydalanuvchidan o'zbek tilida so'ra.\n"
            "3. IZLANISH (WEB RESEARCH): Agar biror API, kutubxona yoki joriy ma'lumotni bilmasang, `search_web` va `read_webpage` orqali internetdan qidirib o'rgan.\n"
            "4. VIZUAL NAZORAT: Agar ekrandagi dasturni ko'rish yoki interfeysda tugma bosish kerak bo'lsa, `look_at_screen` va `gui_control` ni ishlat.\n"
            "5. TAJRIBA YIG'ISH: Murakkab muammoni hal qilganingda yoki foydalanuvchi yangi qoida aytsa, `save_knowledge` orqali xotiraga yozib qo'y.\n"
            "6. NATIJANI YETKAZISH: Agar foydalanuvchi fayl, skript, PDF yoki hisobot tayyorlashni so'rasa, uni yaratib bo'lgach `manage_file(action='send_to_user', ...)` orqali bevosita Telegramiga jo'nat!\n\n"
            f"O'ZING YARATGAN QOBILIYATLAR (SKILLS): {custom_skills}\n"
            f"ISHCHI PAPKA: {WORKSPACE_DIR}\n"
            f"DOIMIY XOTIRA VA TAJRIBALAR:\n{memory_data}"
        )

    def process(self, user_text: str, media_bytes: bytes = None, mime_type: str = None, status_callback=None) -> tuple[str, list[str]]:
        global PENDING_FILES_TO_SEND, ATTACHED_SCREENSHOT
        PENDING_FILES_TO_SEND = []
        ATTACHED_SCREENSHOT = None

        user_parts = []
        if media_bytes and mime_type:
            user_parts.append(types.Part.from_bytes(data=media_bytes, mime_type=mime_type))
        if user_text:
            user_parts.append(types.Part.from_text(text=user_text))

        self.history.append(types.Content(role="user", parts=user_parts))
        if len(self.history) > 24:
            self.history = self.history[-24:]

        execution_logs = []

        # Maksimal 12 qadamli avtonom o'z-o'zini tuzatish sikli
        for step in range(1, 13):
            tools_map = load_all_tools()
            config = types.GenerateContentConfig(
                system_instruction=self.get_system_prompt(tools_map),
                tools=list(tools_map.values()),
                temperature=0.2,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            )

            response = self.client.models.generate_content(
                model="gemini-3.5-flash-lite",
                contents=self.history,
                config=config,
            )

            candidate = response.candidates[0].content
            self.history.append(types.Content(role="model", parts=candidate.parts))

            fn_calls = [p.function_call for p in candidate.parts if p.function_call]
            text_reply = "".join([p.text for p in candidate.parts if p.text]).strip()

            # Agar barcha amallar tugab, model yakuniy javob yoki savol qaytargan bo'lsa
            if not fn_calls:
                header = "\n".join(execution_logs) + "\n\n" if execution_logs else ""
                final_text = f"{header}{text_reply or '✅ Vazifa yakunlandi.'}"
                return final_text, list(PENDING_FILES_TO_SEND)

            tool_responses = []
            for call in fn_calls:
                fn_name = call.name
                fn_args = call.args or {}
                step_info = f"⚙️ `[Qadam {step}] {fn_name}`"
                execution_logs.append(step_info)

                if status_callback:
                    try:
                        status_callback("\n".join(execution_logs[-6:]))
                    except Exception:
                        pass

                tools_map = load_all_tools()
                if fn_name in tools_map:
                    try:
                        res = tools_map[fn_name](**fn_args)
                    except Exception as e:
                        res = f"[TOOL CRASH - FIX THIS]: {traceback.format_exc()}"
                else:
                    res = f"[ERROR]: `{fn_name}` nomli qurol mavjud emas. Kerak bo'lsa `create_or_update_skill` orqali yarat!"

                # Agar xato aniqlansa, logda o'z-o'zini tuzatish belgisini ko'rsatish
                if any(err_k in str(res) for err_k in ["ERROR", "CRASH", "BROKEN", "Traceback"]):
                    execution_logs.append("🔄 _Xato aniqlandi, agent yechim qidirib qayta urinmoqda..._")

                # 1 qadamli media/apparat buyrug'i bo'lsa va xatosiz bo'lsa darhol qaytarish
                if fn_name in ["play_youtube", "control_hardware"] and len(fn_calls) == 1 and "xato" not in str(res).lower():
                    execution_logs.append(f"✅ {res}")
                    return "\n".join(execution_logs), list(PENDING_FILES_TO_SEND)

                tool_responses.append(
                    types.Part.from_function_response(name=fn_name, response={"result": str(res)})
                )

            if ATTACHED_SCREENSHOT:
                tool_responses.append(types.Part.from_bytes(data=ATTACHED_SCREENSHOT, mime_type="image/jpeg"))
                ATTACHED_SCREENSHOT = None

            self.history.append(types.Content(role="user", parts=tool_responses))

        return "\n".join(execution_logs + ["⚠️ Vazifa juda ko'p qadam oldi, oraliq holatda to'xtatildi."]), list(PENDING_FILES_TO_SEND)

if __name__ == "__main__":
    brain = AutonomousAGIBrain()
    if len(sys.argv) > 1:
        ans, _ = brain.process(" ".join(sys.argv[1:]))
        print(ans)
    else:
        print("🧠 Omni-Agent 3.0 (Autonomous Self-Healing Mode)")
        while True:
            q = input("\nSiz: ").strip()
            if q.lower() in ["exit", "quit"]: break
            if q:
                ans, _ = brain.process(q)
                print(f"\nAgent:\n{ans}")

# =====================================================================
# 4. TO'XTOVSIZ O'Z-O'ZINI RIVOJLANTIRISH DVIGATELI (EVOLUTION ENGINE)
# =====================================================================

def run_autonomous_evolution(brain: AutonomousAGIBrain, custom_goal: str = "") -> str:
    """
    Agent foydalanuvchi aralashuvisiz o'zining mavjud skillarini tahlil qiladi,
    yangi foydali qobiliyat (skill) o'ylab topadi, kodini yozadi, test qiladi va xotiraga saqlaydi.
    """
    existing_skills = [f.stem for f in SKILLS_DIR.glob("*.py")]
    memory_data = MEMORY_FILE.read_text(encoding="utf-8")

    evolution_prompt = (
        "🧬 [AVTONOM EVOLYUTSIYA SIKLI BOSHLANDI]\n"
        f"Hozirgi mavjud dinamik qobiliyatlaring (skills): {existing_skills}\n"
        f"Xotira va foydalanuvchi ma'lumotlari: {memory_data}\n\n"
    )

    if custom_goal:
        evolution_prompt += f"Maxsus rivojlanish yo'nalishi: {custom_goal}\n"
    else:
        evolution_prompt += (
            "VAZIFANG:\n"
            "1. Foydalanuvchining Linux Mint kompyuteri, kundalik ishlari, tizim optimizatsiyasi, internet tahlili, "
            "fayllarni tartiblash yoki dasturlash jarayonini tezlashtirish uchun HALI MAVJUD BO'LMAGAN 1 ta yangi, "
            "o'ta foydali Python qurol (skill) g'oyasini o'ylab top.\n"
            "2. `create_or_update_skill` orqali ushbu yangi qurolni yoz (kod toza, xatosiz, docstring va type-hintlar bilan bo'lsin).\n"
            "3. Yaratgan yangi qurolingni DARHOL chaqirib yoki `run_python_code` orqali real ishlatib test qilib ko'r!\n"
            "4. Agar testda xato chiqsa, o'zing tuzatib qayta test qil.\n"
            "5. Muvaffaqiyatli sinovdan o'tgach, `save_knowledge` orqali nima o'rganganingni xotiraga yoz va foydalanuvchiga "
            "qanday yangi qobiliyatni o'zlashtirganing haqida qisqa, aniq hisobot ber!"
        )

    reply, _ = brain.process(evolution_prompt)
    return reply
