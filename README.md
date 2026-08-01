# Distill — Distill | Distill

**Distill** جلسات چندنفره را با یک میکروفون مشترک ضبط می‌کند، گفتگو را زنده یا از فایل پیاده‌سازی می‌کند، گوینده و هم‌پوشانی را تشخیص می‌دهد و خلاصه / نکات کلیدی / تصمیمات را استخراج می‌کند.

## ساختار پروژه

```
api/                 # اپلیکیشن + UI
  web/               # لندینگ (/) و دستیار (/assistant)
  routes/
  meeting/
  providers/
tests/
data/
.env
```

## اجرا

```bash
cp .env.example .env
# کلیدها و endpointها را در .env ویرایش کنید
pip install -r requirements.txt
python -m api
# یا: python main.py
# یا مستقیم: uvicorn api.app:app --host 0.0.0.0 --port 8000
```

مسیر اصلی STT دیگر به **ffmpeg / pydub** نیاز ندارد؛ صوت به‌صورت WAV مستقیم به endpoint سازگار با OpenAI ارسال می‌شود.

UI لندینگ: `http://localhost:8000/`  
UI دستیار: `http://localhost:8000/assistant`  
باز کردن جلسه قبلی: `http://localhost:8000/assistant/{meeting_id}`

## کیفیت Whisper (Review Agent)

بعد از هر تکه STT، یک **دروازه کیفیت سریع** hallucinationهای معروف Whisper را حذف می‌کند (مثلاً حلقهٔ «خیلی خیلی خیلی…»).  
اگر endpoint از `verbose_json` پشتیبانی کند، زمان‌بندی کلمه/سگمنت هم گرفته می‌شود تا متن روی مرز گوینده‌ها شکسته شود.  
در پایان جلسه / آپلود، در حالت پیش‌فرض `finalize`، همان LLM (مثلاً Gemma) متن‌های پذیرفته‌شده را polish می‌کند.

از پنل تنظیمات (کلیک روی وضعیت) یا `/tuning`:

| کلید | پیش‌فرض | معنی |
|------|---------|------|
| `window_ms` | `8000` | طول پنجره STT (جلسه بعد) |
| `hop_ms` | `6000` | گام پنجره ≈۲ثانیه هم‌پوشانی (جلسه بعد) |
| `stt_workers` | `2` | تعداد worker موازی STT |
| `stt_retry_count` | `3` | تلاش مجدد با backoff |
| `stt_review_mode` | `finalize` | `off` / `heuristic` / `finalize` / `live` |
| `stt_min_quality` | `0.35` | حداقل نمره برای قبول متن خام |

## Diarization

- **بهتر:** `pip install -r requirements.optional.txt` و تنظیم `HF_TOKEN` برای pyannote.
- **Fallback:** بدون pyannote هم pipeline کار می‌کند، ولی برچسب گوینده روی میک تکی ضعیف است. سرور در لاگ و رویداد `warning` این را اعلام می‌کند.
- هر جلسه diarizer جدا (`fork`) دارد تا state گوینده بین جلسات قاطی نشود.

در UI روی نام گوینده کلیک کنید تا به نام نمایشی (مثلاً «علی») نگاشت شود (`PATCH /meetings/{id}/speakers`).

## Docker

کل API (لندینگ، دستیار، REST، WebSocket، `/docs`) داخل یک کانتینر اجرا می‌شود. ایمیج **CPU-only** است و torch/pyannote را هم شامل می‌شود. دادهٔ ماندگار روی volume به `/app/data` مپ می‌شود:

- `meetings.db` — دیتابیس جلسات (WAL mode)
- `uploads/` — فایل‌های آپلودی
- `audio/` — صوت ضبط زنده
- `stt_cache/` — کش رونویسی
- `hf_cache/` / `torch_cache/` — کش مدل‌های diarization (تا بعد از rebuild دوباره دانلود نشوند)

`HF_TOKEN` را در `.env` بگذارید و شرایط مدل‌های gated pyannote را در Hugging Face بپذیرید.

```bash
cp .env.example .env
docker compose up -d --build
```

سرویس روی `http://localhost:8000` در دسترس است.

فقط با Docker (بدون compose):

```bash
docker build -t distill .
docker run --rm -p 8000:8000 --env-file .env \
  -v distill-data:/app/data \
  distill
```

برای اجرای محلی بدون Docker، وابستگی پایه سبک است؛ برای diarization باکیفیت:

```bash
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.optional.txt
# سپس HF_TOKEN را در .env بگذارید
```

## متغیرهای محیطی

مسیرهای ماندگار در کد ثابت‌اند و از env خوانده نمی‌شوند:

- `data/meetings.db`
- `data/uploads/`
- `data/audio/`
- `data/stt_cache/`
- `data/hf_cache/` (در Docker)
- `api/web/`

در Docker همین‌ها زیر `/app/...` هستند؛ volume روی `/app/data` (اعلام‌شده در Dockerfile و bind در compose) کافی است.

| متغیر | پیش‌فرض | توضیح |
|--------|---------|--------|
| `LLM_ENDPOINT` | — | آدرس chat completions |
| `LLM_API_KEY` | — | کلید LLM |
| `LLM_MODEL_NAME` | — | نام مدل |
| `AUDIO_SAMPLE_RATE` | `16000` | نرخ نمونه‌برداری |
| `AUDIO_CHANNELS` | `1` | تعداد کانال (مونو) |
| `MEETING_HOST` | `0.0.0.0` | بایند سرور |
| `MEETING_PORT` | `8000` | پورت |
| `MEETING_WINDOW_MS` | `8000` | طول پنجره STT |
| `MEETING_HOP_MS` | `6000` | گام پنجره |
| `MEETING_DIARIZE_EVERY_MS` | `20000` | فاصله diarization زنده |
| `STT_ENDPOINT` | — | آدرس STT |
| `STT_API_KEY` | — | کلید STT |
| `STT_MODEL` | — | مدل STT |
| `HF_TOKEN` | — | توکن HuggingFace برای pyannote |

## تست

```bash
pip install -r requirements.txt
pytest
```

شامل unit تست chunker، pipeline با mock STT، و harness سبک WER/DER در `api/meeting/eval_metrics.py`.

## API

| متد | مسیر | توضیح |
|-----|------|--------|
| `GET` | `/` | لندینگ Distill |
| `GET` | `/assistant` | UI دستDistill |
| `GET` | `/assistant/{meeting_id}` | UI دستیار با تاریخچه همان جلسه |
| `GET` | `/docs` | مستندات Swagger |
| `GET` | `/health` | سلامت + backend diarization |
| `GET` / `PUT` | `/tuning` | خواندن / اعمال تنظیمات زنده |
| `POST` | `/meetings` | ایجاد جلسه |
| `WS` | `/meetings/{id}/audio` | استریم صوت + رویدادها |
| `POST` | `/meetings/{id}/upload` | آپلود فایل |
| `GET` | `/meetings/{id}/transcript` | timeline متن |
| `GET` | `/meetings/{id}/debug` | شمارنده‌های STT / diarization |
| `PATCH` | `/meetings/{id}/speakers` | نام‌گذاری گوینده‌ها |
| `POST` | `/meetings/{id}/insights` | تولید تحلیل |


