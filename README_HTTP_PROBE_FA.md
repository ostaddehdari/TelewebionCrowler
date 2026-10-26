# ارزیابی استخراج بدون Chromium

این ابزار فعلاً «کرولر جایگزین» نیست، بلکه آزمایش پوشش واقعی HTTP/HTML/API روی دیتابیس موجود است. بدون استفاده از Playwright یا اجرای مرورگر، یک صفحه از هر نوع برنامه و کانال، صفحه آرشیو، و نخستین صفحه API کودکان را بررسی می‌کند.

## اجرا روی Ubuntu (بدون تغییر PostgreSQL)

```bash
systemctl stop twcrawler-worker
cd /opt/telewebion-crawler
cp -a twpg /root/twpg-before-http-probe-$(date +%Y%m%d-%H%M%S)
curl -fL -o /tmp/twcrawler-http-probe.py https://raw.githubusercontent.com/ostaddehdari/TelewebionCrowler/main/twpg/http_probe.py
python3 -m py_compile /tmp/twcrawler-http-probe.py
install -m 644 /tmp/twcrawler-http-probe.py /opt/telewebion-crawler/twpg/http_probe.py
set -a
source /etc/telewebion/twcrawler.env
set +a
runuser -u twcrawler -- env DATABASE_URL="$DATABASE_URL" .venv/bin/python -m twpg.http_probe --per-kind 1
unset DATABASE_URL
```

تفسیر: `parent_links` و `episode_links` تعداد لینک‌های موجود در HTML اولیه هستند؛ `json_*_shapes` تعداد اشکال شناسایی‌شده در JSON توکار است. صفر بودن آن‌ها به معنی خالی بودن آرشیو نیست و باید APIهای JSON شبکه‌های مختلف جداگانه شناسایی شوند. `KIDS API` فقط یک فصل/صفحه آزمایشی را بررسی می‌کند. ابزار خواندنی است، متادیتا را دانلود نمی‌کند و هیچ جدول یا تنظیمی را تغییر نمی‌دهد.

**مهم:** اجرای `systemctl stop twcrawler-worker` توقف سرویس فعلی است تا خطای تکراری Chromium و استفاده بیهوده از منابع قطع شود؛ تغییر ماندگار در داده‌ها نیست. پس از تصمیم درباره مهاجرت به HTTP می‌توان سرویس جدیدی ساخت.

هدف نهایی: HTTP-first با صفحه‌بندی صحیح، حفظ رکوردهای قبلی، علامت `partial` برای منابع نامطمئن، backoff برای 429/5xx، عدم دانلود ویدیو، یکپارچگی با PostgreSQL و بدون اجرای مرورگر.
