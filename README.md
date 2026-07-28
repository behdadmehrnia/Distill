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
# تمام تنظیمات را در .env ویرایش کنید
# ffmpeg را روی سیستم نصب کنید (برای pydub)
pip install -r requirements.txt
python -m api
# یا: python main.py
# یا مستقیم: uvicorn api.app:app --host 0.0.0.0 --port 8000
```

UI لندینگ: `http://localhost:8000/`  
UI دستیار: `http://localhost:8000/assistant`  
باز کردن جلسه قبلی: `http://localhost:8000/assistant/{meeting_id}`

## کیفیت Whisper (Review Agent)

بعد از هر تکه STT، یک **دروازه کیفیت سریع** hallucinationهای معروف Whisper را حذف می‌کند (مثلاً حلقهٔ «خیلی خیلی خیلی…»).  
در پایان جلسه / آپلود، در حالت پیش‌فرض `finalize`، همان LLM (مثلاً Gemma) متن‌های پذیرفته‌شده را مثل pipeline مرجع Whisper→Gemma polish می‌کند.

از پنل تنظیمات (کلیک روی وضعیت) یا `/tuning`:

| کلید | پیش‌فرض | معنی |
|------|---------|------|
| `stt_review_mode` | `finalize` | `off` / `heuristic` / `finalize` / `live` |
| `stt_min_quality` | `0.35` | حداقل نمره برای قبول متن خام |

## Docker

کل API (لندینگ، دستیار، REST، WebSocket، `/docs`) داخل یک کانتینر اجرا می‌شود. دادهٔ ماندگار روی volume به `/app/data` مپ می‌شود:

- `meetings.db` — دیتابیس جلسات
- `uploads/` — فایل‌های آپلودی
- `audio/` — صوت ضبط زنده
- `stt_cache/` — کش رونویسی

```bash
cp .env.example .env
# کلیدها و endpointها را در .env تنظیم کنید

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

وابستگی‌ها **CPU-only و سبک** هستند (بدون CUDA/torch). diarization با fallback داخلی کار می‌کند. برای کیفیت بالاتر اختیاری:

```bash
pip install -r requirements.optional.txt  # torch CPU + pyannote
```

روی host هم برای `pydub` به `ffmpeg` نیاز است.

## متغیرهای محیطی

مسیرهای ماندگار در کد ثابت‌اند و از env خوانده نمی‌شوند:

- `data/meetings.db`
- `data/uploads/`
- `data/audio/`
- `data/stt_cache/`
- `api/web/`

در Docker همین‌ها زیر `/app/...` هستند؛ volume فقط روی `/app/data` کافی است.

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
| `MEETING_HOP_MS` | `2000` | گام پنجره |
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

## API

| متد | مسیر | توضیح |
|-----|------|--------|
| `GET` | `/` | لندینگ Distill |
| `GET` | `/assistant` | UI دستDistill |
| `GET` | `/assistant/{meeting_id}` | UI دستیار با تاریخچه همان جلسه |
| `GET` | `/docs` | مستندات Swagger |
| `GET` | `/health` | سلامت سرویس |
| `POST` | `/meetings` | ایجاد جلسه |
| `WS` | `/meetings/{id}/audio` | استریم صوت |
| `POST` | `/meetings/{id}/upload` | آپلود فایل |
| `GET` | `/meetings/{id}/transcript` | timeline متن |
| `POST` | `/meetings/{id}/insights` | تولید تحلیل |


