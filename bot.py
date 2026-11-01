import os
import sys
import json
import asyncio
import subprocess
from io import BytesIO
from pathlib import Path
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import FSInputFile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from omni_agent import AutonomousAGIBrain, run_autonomous_evolution, MEMORY_FILE, SKILLS_DIR, BASE_DIR

os.environ["DISPLAY"] = ":0"
os.environ["XAUTHORITY"] = str(Path.home() / ".Xauthority")

token_file = Path.home() / ".telegram_bot_token"
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or (token_file.read_text().strip() if token_file.exists() else "")

STATE_FILE = BASE_DIR / "bot_state.json"

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"owner_chat_id": None, "auto_evolve": True, "interval_minutes": 60}

def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
brain = AutonomousAGIBrain()
main_loop = None
bot_state = load_state()

def register_owner(chat_id: int):
    if bot_state.get("owner_chat_id") != chat_id:
        bot_state["owner_chat_id"] = chat_id
        save_state(bot_state)

@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    register_owner(msg.chat.id)
    status = "🟢 Yoqilgan" if bot_state.get("auto_evolve") else "🔴 O'chirilgan"
    await msg.answer(
        "🧬 **Omni-Agent 4.0 (Perpetual Self-Evolving AGI) Faol!**\n\n"
        f"🔄 **Avto-Rivojlanish rejimi:** {status} (har {bot_state.get('interval_minutes', 60)} daqiqada)\n\n"
        "🚀 **Maxsus buyruqlar:**\n"
        "🔹 `/evolve` — Hoziroq mustaqil yangi skill o'ylab topish va yaratish\n"
        "🔹 `/evolve <mavzu>` — Siz aytgan yo'nalishda o'zini rivojlantirish\n"
        "🔹 `/autoevolve` — To'xtovsiz fon rivojlanishini Yoqish / O'chirish\n"
        "🔹 `/skills` — Agent o'zi yaratgan barcha qurollar va tajribalar\n"
        "🔹 `/shot` — Ekran rasmini olish\n"
        "🔹 `/clear` — Kontekstni tozalash",
        parse_mode="Markdown"
    )

@dp.message(Command("autoevolve"))
async def cmd_autoevolve(msg: types.Message):
    register_owner(msg.chat.id)
    bot_state["auto_evolve"] = not bot_state.get("auto_evolve", True)
    save_state(bot_state)
    holat = "🟢 YOQILDI! Agent endi fonda to'xtovsiz o'z ustida ishlaydi." if bot_state["auto_evolve"] else "🔴 VAQTINCHA TO'XTATILDI."
    await msg.answer(f"🧬 **Avtonom Rivojlanish:** {holat}", parse_mode="Markdown")

@dp.message(Command("evolve"))
async def cmd_evolve(msg: types.Message):
    register_owner(msg.chat.id)
    parts = msg.text.split(maxsplit=1)
    custom_topic = parts[1] if len(parts) > 1 else ""
    
    wait_msg = await msg.answer(
        "🧬 _Avtonom Evolyutsiya sikli ishga tushdi: Agent yangi qobiliyat loyihalamoqda, kod yozmoqda va sinovdan o'tkazmoqda..._",
        parse_mode="Markdown"
    )
    try:
        report = await asyncio.to_thread(run_autonomous_evolution, brain, custom_topic)
        try:
            await wait_msg.edit_text(f"🧬 **Evolyutsiya Hisoboti:**\n\n{report[:3900]}", parse_mode="Markdown")
        except Exception:
            await wait_msg.edit_text(f"🧬 Evolyutsiya Hisoboti:\n\n{report[:3900]}")
    except Exception as e:
        await wait_msg.edit_text(f"❌ Evolyutsiya xatosi: {e}")

@dp.message(Command("clear"))
async def cmd_clear(msg: types.Message):
    brain.history.clear()
    await msg.answer("🧹 Suhbat tarixi tozalandi (Doimiy xotira va barcha yaratilgan skillar saqlab qolindi).")

@dp.message(Command("skills"))
async def cmd_skills(msg: types.Message):
    register_owner(msg.chat.id)
    skills = [f.stem for f in SKILLS_DIR.glob("*.py")]
    mem = MEMORY_FILE.read_text(encoding="utf-8")
    await msg.answer(
        f"🛠 **Agent O'zi Yaratgan Qurollar ({len(skills)} ta):**\n`{skills or 'Hozircha yo`q'}`\n\n"
        f"🧠 **To'plangan Tajriba va Bilimlar:**\n```json\n{mem[:2000]}\n```",
        parse_mode="Markdown"
    )

@dp.message(Command("shot"))
async def cmd_screenshot(msg: types.Message):
    register_owner(msg.chat.id)
    shot_path = "/tmp/telegram_screen.jpg"
    subprocess.run(["scrot", "-q", "80", shot_path], check=False)
    if os.path.exists(shot_path):
        await msg.answer_photo(FSInputFile(shot_path), caption="📸 Noutbuk ekrani")
        os.remove(shot_path)
    else:
        await msg.answer("❌ Skrinshot olishda xato.")

async def execute_with_live_feedback(msg: types.Message, prompt_text: str, media_bytes: bytes = None, mime_type: str = None):
    register_owner(msg.chat.id)
    wait_msg = await msg.answer("🧠 _Tahlil qilinmoqda..._", parse_mode="Markdown")

    def update_status(log_text: str):
        if main_loop:
            asyncio.run_coroutine_threadsafe(
                wait_msg.edit_text(f"⚡ **Jarayon ketmoqda:**\n{log_text}", parse_mode="Markdown"),
                main_loop
            )

    try:
        reply, files_to_send = await asyncio.to_thread(
            brain.process, prompt_text, media_bytes, mime_type, update_status
        )
        try:
            await wait_msg.edit_text(reply[:4000], parse_mode="Markdown")
        except Exception:
            await wait_msg.edit_text(reply[:4000])

        for fpath in files_to_send:
            if os.path.exists(fpath):
                await msg.answer_document(FSInputFile(fpath), caption=f"📎 `{Path(fpath).name}`", parse_mode="Markdown")
    except Exception as e:
        await wait_msg.edit_text(f"❌ Tizim xatosi: {e}")

@dp.message(F.voice)
async def handle_voice(msg: types.Message):
    file_info = await bot.get_file(msg.voice.file_id)
    buf = BytesIO()
    await bot.download_file(file_info.file_path, destination=buf)
    prompt = "Foydalanuvchi ushbu ovozli xabarni yubordi. Uni diqqat bilan eshitib, buyrug'ini bajar yoki savoliga javob ber."
    await execute_with_live_feedback(msg, prompt, buf.getvalue(), "audio/ogg")

@dp.message(F.photo)
async def handle_photo(msg: types.Message):
    photo = msg.photo[-1]
    file_info = await bot.get_file(photo.file_id)
    buf = BytesIO()
    await bot.download_file(file_info.file_path, destination=buf)
    caption = msg.caption or "Ushbu rasmni tahlil qil va nima qilish kerakligini ayt."
    await execute_with_live_feedback(msg, caption, buf.getvalue(), "image/jpeg")

@dp.message()
async def handle_text(msg: types.Message):
    if not msg.text:
        return
    await execute_with_live_feedback(msg, msg.text)

async def background_evolution_daemon():
    """Orqa fonda to'xtovsiz o'z-o'zini rivojlantiruvchi sikl."""
    while True:
        interval = bot_state.get("interval_minutes", 60) * 60
        await asyncio.sleep(interval)
        
        if bot_state.get("auto_evolve") and bot_state.get("owner_chat_id"):
            chat_id = bot_state["owner_chat_id"]
            try:
                report = await asyncio.to_thread(run_autonomous_evolution, brain, "")
                await bot.send_message(
                    chat_id,
                    f"🧬 **[Avtonom Rivojlanish Hisoboti]**\nMen bo'sh vaqtda o'zimni yangi qobiliyat bilan boyitdim:\n\n{report[:3800]}"
                )
            except Exception as e:
                print(f"Background evolution xatosi: {e}")

async def main():
    global main_loop
    main_loop = asyncio.get_running_loop()
    asyncio.create_task(background_evolution_daemon())
    print("🧬 Omni-Agent 4.0 (Perpetual Self-Evolution) ishga tushdi...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
