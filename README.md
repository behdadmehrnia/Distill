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
pip install -r requirements.txt
python -m api
# یا: python main.py
```

UI لندینگ: `http://localhost:8030/`  
UI دستیار: `http://localhost:8030/assistant`

## متغیرهای محیطی

| متغیر | پیش‌فرض | توضیح |
|--------|---------|--------|
| `LLM_ENDPOINT` | — | آدرس chat completions |
| `LLM_API_KEY` | — | کلید LLM |
| `LLM_MODEL_NAME` | — | نام مدل |
| `AUDIO_SAMPLE_RATE` | `16000` | نرخ نمونه‌برداری |
| `AUDIO_CHANNELS` | `1` | تعداد کانال (مونو) |
| `MEETING_HOST` | `0.0.0.0` | بایند سرور |
| `MEETING_PORT` | `8030` | پورت |
| `MEETING_DB` | `./data/meetings.db` | مسیر SQLite |
| `MEETING_WINDOW_MS` | `8000` | طول پنجره STT |
| `MEETING_HOP_MS` | `2000` | گام پنجره |
| `MEETING_DIARIZE_EVERY_MS` | `20000` | فاصله diarization زنده |
| `STT_ENDPOINT` | — | آدرس STT |
| `STT_API_KEY` | — | کلید STT |
| `STT_MODEL` | — | مدل STT |
| `HF_TOKEN` | — | توکن HuggingFace برای pyannote |

## تست

```bash
pip install pytest pytest-aiohttp pytest-asyncio
pytest
```

## API

| متد | مسیر | توضیح |
|-----|------|--------|
| `GET` | `/` | لندینگ Distill |
| `GET` | `/assistant` | UI دستDistill |
| `GET` | `/docs` | مستندات Swagger |
| `GET` | `/health` | سلامت سرویس |
| `POST` | `/meetings` | ایجاد جلسه |
| `WS` | `/meetings/{id}/audio` | استریم صوت |
| `POST` | `/meetings/{id}/upload` | آپلود فایل |
| `GET` | `/meetings/{id}/transcript` | timeline متن |
| `POST` | `/meetings/{id}/insights` | تولید تحلیل |


