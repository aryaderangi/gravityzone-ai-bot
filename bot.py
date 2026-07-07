import asyncio
import json
import os
import tempfile
import aiohttp
import aiofiles
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    FSInputFile
)
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN       = os.getenv("BOT_TOKEN")
OPENROUTER_KEY  = os.getenv("OPENROUTER_API_KEY")
OPENAI_KEY      = os.getenv("OPENAI_API_KEY")

bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher()

USERS_FILE    = "data/users.json"
REFERRAL_CODE = "GRAVITY"
MINI_APP_URL  = "https://mini.gravityzoneshop.top"

# Format: model_id, display_name, source, tier
# tier: "free" | "premium"
MODELS = {
    # ───── Local (Ollama) ─────
    "qwen3_local": (
        "qwen3:8b",
        "🧠 Qwen 3 8B",
        "ollama",
        "free",
    ),

    "qwen_coder": (
        "qwen2.5-coder:7b",
        "💻 Qwen Coder",
        "ollama",
        "free",
    ),

    "deepseek_local": (
        "deepseek-r1:7b",
        "🧠 DeepSeek R1",
        "ollama",
        "free",
    ),

    "phi_local": (
        "phi4:latest",
        "🌌 Phi-4",
        "ollama",
        "free",
    ),

    # ───── OpenRouter ─────
    "gpt5": (
        "openai/gpt-4o-mini",
        "🚀 GPT-5",
        "openrouter",
        "paid",
    ),

    "claude": (
        "anthropic/claude-3.5-haiku",
        "🌍 Claude",
        "openrouter",
        "paid",
    ),

    "gemini": (
        "google/gemini-2.5-flash",
        "⚡ Gemini",
        "openrouter",
        "paid",
    ),

    "nemotron": (
        "nvidia/nemotron-3-super-120b-a12b:free",
        "🦙 Nemotron",
        "openrouter",
        "free",
    ),
}
DEFAULT_MODEL = "qwen3_local"
PREMIUM_STARS_COST = 10
# ─────────────────────────────────────────────
# Whisper model (lazy load, once)
# ─────────────────────────────────────────────
_whisper_model = None

def _load_whisper():
    global _whisper_model
    if _whisper_model is None:
        import whisper
        _whisper_model = whisper.load_model("small")
    return _whisper_model

async def transcribe_local(file_path: str) -> str:
    """Transcribe audio using local Whisper (runs in thread pool)."""
    loop = asyncio.get_event_loop()
    model = await loop.run_in_executor(None, _load_whisper)
    result = await loop.run_in_executor(None, model.transcribe, file_path)
    return result.get("text", "").strip()

async def transcribe_openai(file_path: str) -> str:
    """Fallback: OpenAI Whisper API."""
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=OPENAI_KEY)
    async with aiofiles.open(file_path, "rb") as f:
        data = await f.read()
    # openai expects a file-like; wrap bytes
    import io
    audio_io = io.BytesIO(data)
    audio_io.name = "voice.ogg"
    result = await client.audio.transcriptions.create(
        model="whisper-1",
        file=audio_io,
        language="fa",
    )
    return result.text.strip()

async def speech_to_text(file_path: str) -> str:
    """Try local Whisper first, fall back to OpenAI."""
    try:
        text = await transcribe_local(file_path)
        if text:
            return text
    except Exception as e:
        print(f"[STT local] {e}")
    # fallback
    if OPENAI_KEY:
        return await transcribe_openai(file_path)
    raise RuntimeError("هیچ موتور STT‌ای در دسترس نیست.")

# ─────────────────────────────────────────────
# TTS — OpenAI
# ─────────────────────────────────────────────
TTS_VOICES = {
    "nova":    "Nova  — زنانه، ملایم",
    "alloy":   "Alloy — خنثی، واضح",
    "echo":    "Echo  — مردانه، گرم",
    "shimmer": "Shimmer — زنانه، شاد",
}
DEFAULT_VOICE = "nova"

async def text_to_speech(text: str, voice: str = DEFAULT_VOICE) -> str:
    """Convert text to MP3 using Edge-TTS."""
    import edge_tts
    import tempfile

    voice_map = {
        "nova": "en-US-AriaNeural",
        "alloy": "en-US-JennyNeural",
        "echo": "fa-IR-FaridNeural",
        "shimmer": "en-GB-SoniaNeural",
    }

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
    tmp.close()

    communicate = edge_tts.Communicate(
        text=text,
        voice=voice_map.get(voice, "en-US-AriaNeural")
    )

    await communicate.save(tmp.name)
    return tmp.name

# ─────────────────────────────────────────────
# Data helpers
# ─────────────────────────────────────────────
def load_users():
    os.makedirs("data", exist_ok=True)
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_users(users):
    os.makedirs("data", exist_ok=True)
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2, ensure_ascii=False)

def get_user(uid: str) -> dict:
    return load_users().get(uid, {})

def update_user(uid: str, **kwargs):
    users = load_users()
    users.setdefault(uid, {}).update(kwargs)
    save_users(users)

# ─────────────────────────────────────────────
# Keyboards
# ─────────────────────────────────────────────
def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🚀 GravityZone Mini App",
                web_app=WebAppInfo(url=MINI_APP_URL)
            ),
        ],
        [
            InlineKeyboardButton(text="🧠 AI Chat",      callback_data="ai_menu"),
            InlineKeyboardButton(text="🎙 AI Voice",  callback_data="voice_tools"),
        ],
        [
            InlineKeyboardButton(text="📰 AI & Crypto News", callback_data="news"),
        ],

        [
            InlineKeyboardButton(
                text="📈 TradingView",
                web_app=WebAppInfo(url="https://www.tradingview.com")
            ),
            InlineKeyboardButton(
                text="💰 CoinMarketCap",
                web_app=WebAppInfo(url="https://coinmarketcap.com")
            ),
        ],
        [
            InlineKeyboardButton(text="🎨 Visual Art", callback_data="visual_art"),
            InlineKeyboardButton(text="🎧 Music Zone", callback_data="guide_music"),
        ],
        [
            InlineKeyboardButton(text="🌐 GravityZone", callback_data="gravityzone"),
            InlineKeyboardButton(text="🛒 JelicCray Shop", callback_data="shop"),
        ],
        [
            InlineKeyboardButton(text="👤 Profile", callback_data="profile"),
        ],    ])

def back_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Back", callback_data="main_menu")]
    ])

def ai_keyboard(current_model: str):
    buttons = []

    for key, (_, name, source, tier) in MODELS.items():
        check = "✅ " if key == current_model else ""
        buttons.append(
            InlineKeyboardButton(
                text=f"{check}{name}",
                callback_data=f"set_model:{key}"
            )
        )

    rows = []
    for i in range(0, len(buttons), 2):
        rows.append(buttons[i:i+2])

    rows.append([
        InlineKeyboardButton(
            text="⬅️ Back",
            callback_data="main_menu"
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)

def voice_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎤 ویس → متن (STT)",  callback_data="voice_stt_info")],
        [InlineKeyboardButton(text="🔊 متن → ویس (TTS)",  callback_data="voice_tts_info")],
        [InlineKeyboardButton(text="🎭 تغییر صدای TTS",   callback_data="voice_pick")],
        [InlineKeyboardButton(text="⬅️ Back",              callback_data="main_menu")],
    ])

def voice_pick_keyboard():
    kb = []
    for key, label in TTS_VOICES.items():
        kb.append([InlineKeyboardButton(text=label, callback_data=fset_voice:{key}")])
    kb.append([InlineKeyboardButton(text="⬅️ Back", callback_data="voice_tools")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def mini_app_reply_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🚀 Open GravityZone", web_app=WebAppInfo(url=MINI_APP_URL))]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )

# ─────────────────────────────────────────────
# AI backends
# ─────────────────────────────────────────────
async def ask_ollama(model_id, text):
    payload = {"model": model_id, "messages": [{"role": "user", "content": text}], "stream": False}
    print(f"OpenRouter Request => {model_id} :: {text}", flush=True)
    async with aiohttp.ClientSession() as s:
        async with s.post("https://ol.gravityzoneshop.top/api/chat", json=payload,
                          timeout=aiohttp.ClientTimeout(total=120)) as r:
            data = await r.json()
            return data.get("message", {}).get("content", "⚠️ مدل پاسخی برنگرداند.")

async def ask_openrouter(model_id, text):
    print("### OPENROUTER BOT.PY ###", flush=True)
    payload = {"model": model_id, "messages": [{"role": "user", "content": text}]}
    print(f"OpenRouter Request => {model_id} :: {text}", flush=True)
    async with aiohttp.ClientSession() as s:
        async with s.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"},
            json=payload, timeout=aiohttp.ClientTimeout(total=60)
        ) as r:
            if r.status != 200:
                return f"❌ خطا {r.status}:\n{(await r.text())[:300]}"
            data = await r.json()
            return data.get("choices", [{}])[0].get("message", {}).get("content", "⚠️ پاسخی نگرفتیم.")

# ─────────────────────────────────────────────
# Action dispatcher (Mini App → Bot)
# ─────────────────────────────────────────────
async def set_model_by_key(message: Message, model_key: str):
    if model_key not in MODELS:
        await message.answer("❌ مدل نامعتبر!", reply_markup=main_menu())
        return
    update_user(str(message.from_user.id), model=model_key)
    _, model_name, source, tier = MODELS[model_key]
    badge = "🏠 Local" if source == "ollama" else "🌐 OpenRouter"
    await message.answer(
        f"✅ مدل تغییر کرد!\n\n🤖 {model_name}\n📡 {badge}\n\nالان می‌تونی مستقیم باهاش چت کنی 👇",
        reply_markup=main_menu(),
    )

async def dispatch_action(message: Message, action: str):
    uid = str(message.from_user.id)
    if action == "ai_menu":
        current = get_user(uid).get("model", DEFAULT_MODEL)
        await message.answer("🧠 مدل AI را انتخاب کنید:", reply_markup=ai_keyboard(current))
    elif action == "market":
        await message.answer(
            "📈 TradingView\n\n🔗 TradingView: https://www.tradingview.com\n"
            "🔗 CoinMarketCap: https://coinmarketcap.com\n🔗 Binance: https://www.binance.com",
            reply_markup=back_menu())
    elif action == "visual_art":
        kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🧠 AI Models", callback_data="guide_ai"),
            InlineKeyboardButton(text="🎙 AI Voice", callback_data="guide_voice")
        ],
        [
            InlineKeyboardButton(text="📰 Live News Center", callback_data="guide_news")
        ],
        [
            InlineKeyboardButton(text="🪂 Airdrop Center", callback_data="guide_airdrop"),
            InlineKeyboardButton(text="🌐 GravityZone", callback_data="guide_gz")
        ],
        [
            InlineKeyboardButton(text="💳 Payments", callback_data="guide_payment"),
            InlineKeyboardButton(text="🚀 Roadmap", callback_data="guide_roadmap")
        ],
        [
            InlineKeyboardButton(text="⬅️ Back", callback_data="main_menu")
        ]
    ])

        await message.answer(
            "🎨 Visual Art",
            reply_markup=kb
        )
    elif action == "shop":
        await message.answer(
            "🛒 JelicCray Shop\n\n• 🤖 AI  • 🖥️ VPS  • 📢 Ads  • 🎵 Music\n\n"
            "👨‍💼 @ArJeliicc | 📱 https://t.me/JeliccRay",
            reply_markup=back_menu())
    elif action == "gravityzone":
        await message.answer(
            "🎧 Music Zone\n\nhttps://t.me/GravityyZone/139484\nPsytrance • NFT • DJ Sets",
            reply_markup=back_menu())
    elif action == "cloud":
        await message.answer(
            "☁️ Cloud Center\n\nAWS • Azure • Hetzner • Google Cloud • DigitalOcean\n\n"
            "👨‍💼 @ArJeliicc",
            reply_markup=back_menu())
    else:
        await message.answer("🔹 منوی اصلی", reply_markup=main_menu())

# ─────────────────────────────────────────────
# /start
# ─────────────────────────────────────────────
@dp.message(Command("start"))
async def start(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) > 1 and args[1].startswith("act_"):
        await dispatch_action(message, args[1][4:])
        return
    if len(args) > 1 and args[1].startswith("mdl_"):
        await set_model_by_key(message, args[1][4:])
        return
    await message.answer(
        f"👋 سلام {message.from_user.first_name}!\n\n🤖 به GravityZone AI خوش اومدی!\n\n"
        "مستقیم پیام بده یا از منو استفاده کن:",
        reply_markup=main_menu(),
    )

# ─────────────────────────────────────────────
# /app
# ─────────────────────────────────────────────
@dp.message(Command("app"))
async def open_app(message: Message):
    await message.answer(
        "🚀 روی دکمه زیر کلیک کن تا Mini App باز بشه:",
        reply_markup=mini_app_reply_keyboard(),
    )

# ─────────────────────────────────────────────
# /tts  —  متن به صدا
# ─────────────────────────────────────────────
@dp.message(Command("tts"))
async def tts_command(message: Message):
    if not OPENAI_KEY:
        await message.answer("❌ OPENAI_API_KEY در .env تنظیم نشده.")
        return

    # متن از دستور یا reply
    text = message.text[4:].strip()
    if not text and message.reply_to_message:
        text = message.reply_to_message.text or ""
    if not text:
        await message.answer("📝 متن مورد نظر را ارسال کن")
        return

    uid = str(message.from_user.id)
    voice = get_user(uid).get("tts_voice", DEFAULT_VOICE)

    status = await message.answer(f"🔊 در حال ساختن صدا... ({voice})")
    try:
        mp3_path = await text_to_speech(text, voice)
        await message.answer_voice(FSInputFile(mp3_path))
        await status.delete()
        os.unlink(mp3_path)
    except Exception as e:
        await status.edit_text(f"❌ خطای TTS:\n{str(e)[:200]}")

# ─────────────────────────────────────────────
# Voice message handler  —  ویس به متن (STT)
# ─────────────────────────────────────────────
@dp.message(F.voice)
async def handle_voice(message: Message):
    status = await message.answer("🎤 داره پردازش میشه...")
    try:
        # دانلود فایل ویس
        voice = message.voice
        file = await bot.get_file(voice.file_id)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as tmp:
            tmp_path = tmp.name
        await bot.download_file(file.file_path, tmp_path)

        # تبدیل به متن
        text = await speech_to_text(tmp_path)
        os.unlink(tmp_path)

        if not text:
            await status.edit_text("⚠️ متنی تشخیص داده نشد.")
            return

        uid = str(message.from_user.id)
        model_key = get_user(uid).get("model", DEFAULT_MODEL)
        model_id, model_name, source, tier = MODELS.get(model_key, MODELS[DEFAULT_MODEL])

        # نشون بده چی شنیده
        await status.edit_text(
            f"📝 متن شناسایی شده:\n_{text}_\n\n⏳ {model_name} داره جواب میده...",
            parse_mode="Markdown"
        )

        print("VOICE -> ASK AI", flush=True)

        if source == "ollama":
            answer = await ask_ollama(model_id, text)
        else:
            answer = await ask_openrouter(model_id, text)

        print("VOICE -> AI DONE", flush=True)

        voice_name = get_user(uid).get("tts_voice", DEFAULT_VOICE)

        print("VOICE -> TTS START", flush=True)

        try:
            mp3_path = await text_to_speech(
                answer[:3000],
                voice_name
            )

            print("VOICE -> TTS DONE", flush=True)

            print("VOICE -> SEND TELEGRAM", flush=True)

            await message.answer_voice(
                FSInputFile(mp3_path)
            )

            print("VOICE -> SENT", flush=True)

            os.unlink(mp3_path)

        except Exception as tts_error:
            print("VOICE REPLY ERROR:", tts_error)

        await status.edit_text(
            f"🎤 *تو گفتی:*\n_{text}_\n\n🤖 *{model_name}:*\n{answer[:3500]}",
            parse_mode="Markdown"
        )

    except asyncio.TimeoutError:
        await status.edit_text("⏰ Timeout!")
    except Exception as e:
        await status.edit_text(f"❌ خطا:\n{str(e)[:300]}")

# ─────────────────────────────────────────────
# Text chat
# ─────────────────────────────────────────────
@dp.message(F.text & ~F.text.startswith("/"))
async def chat(message: Message):
    uid = str(message.from_user.id)
    user = get_user(uid)

    if user.get("mode") == "tts":
        voice = user.get("tts_voice", DEFAULT_VOICE)

        status = await message.answer(f"🔊 در حال ساختن صدا... ({voice})")

        try:
            mp3_path = await text_to_speech(message.text, voice)

            print("VOICE -> SEND TELEGRAM", flush=True)

            await message.answer_voice(
                FSInputFile(mp3_path)
            )

            print("VOICE -> SENT", flush=True)

            update_user(uid, mode="chat")

            await status.delete()
            os.unlink(mp3_path)

        except Exception as e:
            await status.edit_text(
                f"❌ خطای TTS:\n{str(e)[:200]}"
            )

        return

    model_key = user.get("model", DEFAULT_MODEL)
    model_id, model_name, source, tier = MODELS.get(model_key, MODELS[DEFAULT_MODEL])

    thinking = await message.answer(f"⏳ {model_name} داره فکر میکنه...")

    try:
        answer = await (
            ask_ollama(model_id, message.text)
            if source == "ollama"
            else ask_openrouter(model_id, message.text)
        )

        await thinking.edit_text(answer[:4000])

    except asyncio.TimeoutError:
        await thinking.edit_text("⏰ Timeout! مدل دیگه‌ای انتخاب کن.")

    except Exception as e:
        await thinking.edit_text(f"❌ خطا:\n{str(e)}")


# ─────────────────────────────────────────────
# Mini App → Bot (sendData)
# ─────────────────────────────────────────────
@dp.message(F.web_app_data)
async def handle_webapp_data(message: Message):
    try:
        data       = json.loads(message.web_app_data.data)
        action     = data.get("action", "")
        model_key  = data.get("model_key", "")
    except Exception:
        await message.answer("🔹 منوی اصلی", reply_markup=main_menu())
        return

    await message.answer("⚡", reply_markup=ReplyKeyboardRemove())

    if action == "set_model" and model_key:
        await set_model_by_key(message, model_key)
    else:
        await dispatch_action(message, action)

# ─────────────────────────────────────────────
# Callbacks
# ─────────────────────────────────────────────
@dp.callback_query(F.data == "ai_menu")
async def ai_menu(cb: CallbackQuery):
    current = get_user(str(cb.from_user.id)).get("model", DEFAULT_MODEL)
    await cb.message.edit_text("🧠 مدل AI را انتخاب کنید:", reply_markup=ai_keyboard(current))
    await cb.answer()

@dp.callback_query(F.data.startswith("set_model:"))
async def set_model_cb(cb: CallbackQuery):
    model_key = cb.data.split(":")[1]
    if model_key not in MODELS:
        await cb.answer("❌ مدل نامعتبر!", show_alert=True)
        return
    update_user(str(cb.from_user.id), model=model_key)
    _, model_name, _, _ = MODELS[model_key]
    await cb.message.edit_text(f"✅ مدل به {model_name} تغییر کرد!", reply_markup=back_menu())
    await cb.answer()

@dp.callback_query(F.data == "voice_tools")
async def voice_tools(cb: CallbackQuery):
    uid   = str(cb.from_user.id)
    voice = get_user(uid).get("tts_voice", DEFAULT_VOICE)
    await cb.message.edit_text(
        f"🎙 AI Voice\n\n"
        f"🎤 ویس بفرست → متن + جواب AI میگیری\n"
        f"🔊 Text To Speech\n\n"
        f"🎭 صدای فعلی TTS: *{TTS_VOICES[voice]}*",
        reply_markup=voice_menu(),
        parse_mode="Markdown",
    )
    await cb.answer()

@dp.callback_query(F.data == "voice_stt_info")
async def voice_stt_info(cb: CallbackQuery):
    await cb.answer(
        "🎤 فقط یه ویس برام بفرست — خودم رونویسی می‌کنم!",
        show_alert=True
    )

@dp.callback_query(F.data == "voice_tts_info")
async def voice_tts_info(cb: CallbackQuery):
    update_user(str(cb.from_user.id), mode="tts")
    print("TTS BUTTON CLICKED:", cb.from_user.id)
    await cb.answer("🔊 حالا متن خود را ارسال کنید", show_alert=True)
    await cb.answer()

@dp.callback_query(F.data.startswith(set_voice:"))
async def set_voice_cb(cb: CallbackQuery):
    voice = cb.data.split(":")[1]
    if voice not in TTS_VOICES:
        await cb.answer("❌ صدا نامعتبر!", show_alert=True)
        return
    update_user(str(cb.from_user.id), tts_voice=voice)
    await cb.message.edit_text(
        f"✅ صدا به *{TTS_VOICES[voice]}* تغییر کرد!\n\nحالا متن خود را ارسال کنید",
        reply_markup=back_menu(), parse_mode="Markdown"
    )
    await cb.answer()

@dp.callback_query(F.data == "visual_art")
async def visual_art(cb: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🌊 Fluid Simulation",
            web_app=WebAppInfo(
                url="https://paveldogreat.github.io/WebGL-Fluid-Simulation/"
            )
        )],
        [InlineKeyboardButton(
            text="🖌 TLDraw",
            web_app=WebAppInfo(
                url="https://www.tldraw.com"
            )
        )],
        [InlineKeyboardButton(
            text="⬅️ Back",
            callback_data="main_menu"
        )]
    ])

    await cb.message.edit_text(
        "🎨 Visual Art",
        reply_markup=kb
    )
    await cb.answer()

# OLD AIRDROPS REMOVED (replaced by API system)

# OLD NEWS REMOVED (replaced by API system)

@dp.callback_query(F.data == "market")
async def market_menu(cb: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📈 TradingView", web_app=WebAppInfo(url="https://www.tradingview.com"))],
        [InlineKeyboardButton(text="🪙 CoinMarketCap", web_app=WebAppInfo(url="https://coinmarketcap.com"))],
        [InlineKeyboardButton(text="🏦 Binance", web_app=WebAppInfo(url="https://www.binance.com"))],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="main_menu")]
    ])
    await cb.message.edit_text("📈 Trading 📈 TradingView Tools Markets", reply_markup=kb)
    await cb.answer()



@dp.callback_query(F.data == "gravityzone")
async def gravityzone_menu(cb: CallbackQuery):
    await cb.message.edit_text(
        "🌐 GravityZone\n\n"
        "🏠 Main:\nhttps://t.me/GravityyZone/1\n\n"
        "🤖 AI Robots Zone:\nhttps://t.me/GravityyZone/139978\n\n"
        "🎧 Music Zone:\nhttps://t.me/GravityyZone/139484\n\n"
        "💼 JelicCray:\nhttps://t.me/GravityyZone/139488\n\n"
        "📈 Traders Zone:\nhttps://t.me/GravityyZone/139478\n\n"
        "🖥️ Datacenters Zone:\nhttps://t.me/GravityyZone/139468\n\n"
        "🎮 Gamers Zone:\nhttps://t.me/GravityyZone/139873",
        reply_markup=back_menu()
    )
    await cb.answer()

@dp.callback_query(F.data == "shop")
async def shop_menu(cb: CallbackQuery):
    await cb.message.edit_text(
        "🛒 JelicCray Shop\n\n"
        "🛍 Products:\nhttps://t.me/JeliccRay/1870\n\n"
        "👨‍💼 @ArJeliicc",
        reply_markup=back_menu()
    )
    await cb.answer()

@dp.callback_query(F.data == "profile")
async def profile_menu(cb: CallbackQuery):
    uid  = str(cb.from_user.id)
    user = get_user(uid)
    _, model_name, _, _tier = MODELS.get(user.get("model", DEFAULT_MODEL), MODELS[DEFAULT_MODEL])
    voice = TTS_VOICES.get(user.get("tts_voice", DEFAULT_VOICE), "—")
    await cb.message.edit_text(
        f"👤 پروفایل\n\n• ID: {cb.from_user.id}\n• نام: {cb.from_user.full_name}\n"
        f"• مدل AI: {model_name}\n• صدای TTS: {voice}\n\n"
        f"🔗 https://t.me/gravityAIZone_bbot?start={REFERRAL_CODE}",
        reply_markup=back_menu())
    await cb.answer()

@dp.callback_query(F.data == "settings")
async def settings_menu(cb: CallbackQuery):
    await cb.message.edit_text(
        "💳 Payment\n\n🟡 EVM:\n`0x87abdd11267CE3A0479A392f2d678960CB60310b`\n\n"
        "🔴 TRON:\n`TGURS5XZv7bXLjd6t2i78BnoV3wTWrTm65`\n\n👨‍💼 @ArJeliicc",
        reply_markup=back_menu(), parse_mode="Markdown")
    await cb.answer()




@dp.callback_query(F.data == "guide")
async def guide_menu(cb: CallbackQuery):

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🧠 AI Models", callback_data="guide_ai"),
            InlineKeyboardButton(text="🎙 AI Voice", callback_data="guide_voice")
        ],
        [
            InlineKeyboardButton(text="📰 Live News Center", callback_data="guide_news")
        ],
        [
            InlineKeyboardButton(text="🪂 Airdrop Center", callback_data="guide_airdrop"),
            InlineKeyboardButton(text="🌐 GravityZone", callback_data="guide_gz")
        ],
        [
            InlineKeyboardButton(text="💳 Payments", callback_data="guide_payment"),
            InlineKeyboardButton(text="🚀 Roadmap", callback_data="guide_roadmap")
        ],
        [
            InlineKeyboardButton(text="⬅️ Back", callback_data="main_menu")
        ]
    ])

    await cb.message.edit_text(
        "📚 GravityZone Guide\n\nChoose a section:",
        reply_markup=kb
    )
    await cb.answer()
    return

@dp.callback_query(F.data == "main_menu")
async def back_to_main(cb: CallbackQuery):
    await cb.message.edit_text("🔹 منوی اصلی", reply_markup=main_menu())
    await cb.answer()

# ─────────────────────────────────────────────

@dp.callback_query(F.data == "guide_ai")
async def guide_ai(cb: CallbackQuery):
    await cb.message.edit_text(
        "🧠 AI Models\n\n"
        "🇮🇷 فارسی\n\n"
        "🤖 Gemma 3\nگفتگو و تولید محتوا\n\n"
        "👽 Qwen Coder\nبرنامه نویسی و توسعه ربات\n\n"
        "🧠 DeepSeek R1\nتحلیل و استدلال پیشرفته\n\n"
        "🌌 Phi-4\nسبک و سریع برای کارهای روزمره\n\n"
        "🪐 GPT-4o Mini\nمدل ابری سریع\n\n"
        "🦙 Nemotron\nمدل قدرتمند ابری برای تحلیل\n\n"
        "🇬🇧 English\n\n"
        "Chat • Coding • Reasoning • Content Creation",
        reply_markup=back_menu()
    )
    await cb.answer()

@dp.callback_query(F.data == "guide_voice")
async def guide_voice(cb: CallbackQuery):
    await cb.message.edit_text(
        "🎙 AI Voice\n\n"
        "🎤 STT\n"
        "🔊 TTS\n"
        "🎭 Voice Selection",
        reply_markup=back_menu()
    )
    await cb.answer()

@dp.callback_query(F.data == "guide_music")
async def guide_music(cb: CallbackQuery):

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🤖 Open Music Bot",
                url="https://t.me/GravitySoundZone_bot"
            )
        ],
        [
            InlineKeyboardButton(
                text="❤️ Favorites",
                callback_data="music_favorites"
            ),
            InlineKeyboardButton(
                text="🕘 History",
                callback_data="music_history"
            )
        ],
        [
            InlineKeyboardButton(
                text="⬅️ Back",
                callback_data="guide"
            )
        ]
    ])

    await cb.message.edit_text(
        "🎧 Music Zone\n\n"
        "🇮🇷 جستجو و شناسایی موزیک\n\n"
        "🎵 Search Songs\n"
        "🎤 Voice Recognition\n"
        "🎬 Video Recognition\n\n"
        "@GravitySoundZone_bot",
        reply_markup=kb
    )

    await cb.answer()

@dp.callback_query(F.data == "guide_roadmap")
async def guide_roadmap(cb: CallbackQuery):
    await cb.message.edit_text(
        "🚀 Roadmap\n\n"
        "📰 Live News Live\n"
        "🪂 Live Airdrops Live\n"
        "🌐 VPN Shop\n"
        "👑 Admin Panel\n"
        "📱 Mini App V2",
        reply_markup=back_menu()
    )
    await cb.answer()


@dp.callback_query(F.data == "guide_news")
async def guide_news(cb: CallbackQuery):
    await cb.message.edit_text(
        "📰 Live News Center\n\n"
        "🇮🇷 اخبار AI، کریپتو و تکنولوژی\n\n"
        "🇬🇧 AI, Crypto & Tech News",
        reply_markup=back_menu()
    )
    await cb.answer()

@dp.callback_query(F.data == "guide_airdrop")
async def guide_airdrop(cb: CallbackQuery):
    await cb.message.edit_text(
        "🪂 Airdrop Center\n\n"
        "🇮🇷 ایردراپ‌ها و تست‌نت‌ها\n\n"
        "🇬🇧 Airdrops & Testnets",
        reply_markup=back_menu()
    )
    await cb.answer()

@dp.callback_query(F.data == "guide_gz")
async def guide_gz(cb: CallbackQuery):
    await cb.message.edit_text(
        "🌐 GravityZone\n\n"
        "🤖 AI\n"
        "🎧 Music Zone\n"
        "🌐 VPN\n"
        "📈 Trading\n"
        "☁️ Cloud",
        reply_markup=back_menu()
    )
    await cb.answer()

@dp.callback_query(F.data == "guide_payment")
async def guide_payment(cb: CallbackQuery):
    await cb.message.edit_text(
        "💳 Payments\n\n"
        "TRON • EVM\n\n"
        "@ArJeliicc",
        reply_markup=back_menu()
    )
    await cb.answer()


@dp.callback_query(F.data == "music_favorites")
async def music_favorites(cb: CallbackQuery):
    await cb.message.edit_text(
        "❤️ Favorites\n\nComing Soon...",
        reply_markup=back_menu()
    )
    await cb.answer()

@dp.callback_query(F.data == "music_history")
async def music_history(cb: CallbackQuery):
    await cb.message.edit_text(
        "🕘 History\n\nComing Soon...",
        reply_markup=back_menu()
    )
    await cb.answer()


# ─────────────────────────────────────────────
async def main():
    print("GravityZone AI Bot Online ✅")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())


