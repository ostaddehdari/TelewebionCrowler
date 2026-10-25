# TelewebionCrowler — Python + PostgreSQL (Ubuntu 26.04)

**مرحله PG-01** بر اساس `telewebion_server_transfer.tar.gz` کاربر، با حفظ منطق `episode_crawler.py` موجود. سورس جدید در `twpg/` است. `SQLite` تنها مبدأ انتقال داده است؛ پس از ایمپورت، پنل و پردازش‌گر جدید مستقیم از PostgreSQL استفاده می‌کنند.

## دارایی‌های موجود در فایل انتقال اولیه

در snapshot بررسی‌شده: `channels=82`، `programs=49,822`، `program_channels=50,728`، `media_items=184,539`، `jobs=49,822`، `events=90,422` و `program_metadata=189` است؛ `PRAGMA integrity_check = ok`. وضعیت کرولر قبلی: `done=5,231`، `partial=32,907`، `empty_review=210`، `pending=11,472`، `running=2`. این اعداد صرفاً مربوط به همان فایل است.

## معماری

- `twpg/schema.sql`: ساختار دقیق معادل جداول SQLite و ایندکس‌ها، حفظ ستون‌های `raw_json` و تاریخ‌های فارسی به‌صورت TEXT.
- `twpg/import_sqlite.py`: انتقال جداول با ترتیب وابستگی، `COPY` دسته‌ای و `ON CONFLICT DO NOTHING`، قابل تکرار، ثبت checksum فایل انتقال؛ داده PG موجود را بازنویسی نمی‌کند.
- `twpg/scraper.py`: منطق واقعی Playwright کاربر، با پیمایش صفحه/DOM و مشاهده پاسخ‌های JSON وب‌سایت تلوبیون؛ فقط فراداده، نه فایل ویدئو.
- `twpg/worker.py`: صف PostgreSQL و `FOR UPDATE SKIP LOCKED`، پردازش یک‌به‌یک، لاگ، ذخیره تدریجی و checkpoint.
- `twpg/web.py`: جستجو، شبکه‌ها، فهرست قسمت/کلیپ، داشبورد و API صفحه‌بندی‌شده FastAPI.
- `deploy/install_ubuntu26.sh`: نصب کنترل‌شده PostgreSQL + Python venv + systemd؛ بدون تغییر تنظیمات فعال Nginx/SSH.

## ۱. انتقال سورس

روی **Git Bash ویندوز** (نه شل Ubuntu)، در Downloads:

```bash
scp -P 2233 ~/Downloads/TelewebionCrowler_PG_source.tar.gz root@5.61.28.187:/tmp/
ssh -p 2233 root@5.61.28.187
```

روی **سرور**:

```bash
mkdir -p /opt/telewebion-crawler
tar --no-same-owner --no-same-permissions -xzf /tmp/TelewebionCrowler_PG_source.tar.gz -C /opt/telewebion-crawler
cd /opt/telewebion-crawler
bash deploy/install_ubuntu26.sh
```

اگر اولین اجرای نصب با خطای نصب مرورگر روبه‌رو شد، پنل/دیتابیس ممکن است نصب شده باشد ولی **worker نباید شروع شود** تا خطای Playwright رفع شود.

## ۲. انتقال آرشیو واقعی SQLite

در **Git Bash ویندوز**:

```bash
scp -P 2233 ~/Downloads/telewebion_server_transfer.tar.gz root@5.61.28.187:/tmp/telewebion_server_transfer.tar.gz
```

در **SSH سرور**:

```bash
cd /opt/telewebion-crawler
bash deploy/extract_sqlite_from_archive.sh /tmp/telewebion_server_transfer.tar.gz
# Verify SQLite before importing
python3 - <<'PY'
import sqlite3
p='/srv/telewebion/private/imports/telewebion.sqlite3'
with sqlite3.connect('file:'+p+'?mode=ro',uri=True) as db:
    print(db.execute('PRAGMA integrity_check').fetchone())
    for table in ['channels','programs','media_items','jobs']:
        print(table,db.execute('SELECT count(*) FROM '+table).fetchone()[0])
PY
```

> مسیر فوق **عمداً خارج از `/var/www` و Nginx web root** قرار گرفته است. فایل SQLite ممکن است `raw_json` دارای توکن‌های موقت یا داده‌های دیگر داشته باشد و نباید به لینک عمومی دانلود تبدیل شود. ابتدا HTTP Basic Auth و HTTPS را تنظیم کنید؛ در اولین نصب از تونل SSH استفاده کنید.

## ۳. انتقال واقعی داده‌ها به PostgreSQL

روی سرور:

```bash
cd /opt/telewebion-crawler
# Import by limited Linux service account, not postgres superuser:
runuser -u twcrawler -- env \
  DATABASE_URL="$(sed -n 's/^DATABASE_URL=//p' /etc/telewebion/twcrawler.env)" \
  .venv/bin/python -m twpg.import_sqlite \
  /srv/telewebion/private/imports/telewebion.sqlite3

bash deploy/verify.sh
```

ایمپورت، رکوردهای PG موجود را حذف نمی‌کند. اگر import نیمه‌کاره شد، اجرای دوباره امن است، هرچند ممکن است دوباره جداول قبلی را بخواند. `import_history` بعد از پایان کامل ثبت می‌شود. **SQLite اصلی را پاک نکنید.**

## ۴. مشاهده سایت از Windows بدون دامنه

در Git Bash ویندوز (یک پنجره جداگانه و باز):

```bash
ssh -N -L 18895:127.0.0.1:18895 -p 2233 root@5.61.28.187
```

در مرورگر همان ویندوز:

- http://127.0.0.1:18895/ — سایت آرشیو
- http://127.0.0.1:18895/dashboard — گزارش صف
- http://127.0.0.1:18895/api/stats — شمارش داده‌ها

این تونل ترافیک HTTP را در SSH رمز می‌کند؛ پورت پنل مستقیماً برای اینترنت باز نشده است.

## ۵. تست واقعی کرولر

نخست دسترسی شبکه و مرورگر را بررسی کنید:

```bash
curl -IL --max-time 20 https://telewebion.net/
cd /opt/telewebion-crawler
runuser -u twcrawler -- env \
  DATABASE_URL="$(sed -n 's/^DATABASE_URL=//p' /etc/telewebion/twcrawler.env)" \
  HOME=/var/lib/twcrawler PLAYWRIGHT_BROWSERS_PATH=/var/lib/twcrawler/pw-browsers \
  .venv/bin/python -m twpg.worker --limit 2 --force
```

خروجی `done`, `partial`, `empty_review` الزاماً به معنی پوشش کامل کل سایت نیست. موفقیت واقعی هر شبکه/برنامه را با نمونه‌های واقعی مقایسه کنید. محدودیت سایت، پاسخ 403 یا 429 را دور نزنید؛ سرعت پیش‌فرض ۱ مرورگر و ۱۵۰۰ms است.

بعد از تست:

```bash
systemctl enable --now twcrawler-worker
# Enable claim of new jobs via dashboard (localhost tunnel), or via psql:
runuser -u postgres -- psql -d twcrawler -c "UPDATE settings SET value='1' WHERE key='enabled';"
systemctl status twcrawler-worker twcrawler-web --no-pager
journalctl -u twcrawler-worker -n 60 --no-pager
```

برای توقف:

```bash
systemctl stop twcrawler-worker
runuser -u postgres -- psql -d twcrawler -c "UPDATE settings SET value='0' WHERE key='enabled';"
```

نکته: `--retry-partial` به‌طور پیش‌فرض فعال نیست تا 32,907 مورد `partial` قبلی به یکباره تکرار نشود. وضعیت `running` قدیمی پس از تشخیص stale قابل بازیابی است.

## ۶. انتشار با HTTPS

بدون دامنه، **پورت 18895 را عمومی نکنید**. بعد از اتصال DNS یک دامنه/زیردامنه به `5.61.28.187`، ابتدا گواهی SSL برای آن بگیرید و سپس فایل `deploy/nginx-domain.example.conf` را با آدرس دامنه و مسیر گواهی صحیح در Nginx استفاده کنید. روی تمام پنل، API و مسیرهای کنترل باید HTTP Basic Auth و HTTPS فعال باشد.

نمونه تولید Basic Auth:

```bash
apt-get install -y apache2-utils
htpasswd -c /etc/nginx/.htpasswd_twcrawler manager
chmod 640 /etc/nginx/.htpasswd_twcrawler
chown root:www-data /etc/nginx/.htpasswd_twcrawler
nginx -t && systemctl reload nginx
```

## ۷. امنیت و حق نشر

- داده‌های اصلی و `.env` هرگز در GitHub بارگذاری نمی‌شوند (`.gitignore`).
- API سایت هیچ `raw_json` یا `details_json` یا `content_token` برنمی‌گرداند.
- بازیابی فراداده عمومی و فایل‌های عکس/ویدئو دو بحث متفاوت‌اند؛ این پروژه **ویدئو دانلود نمی‌کند**، DRM یا احراز هویت را دور نمی‌زند و فقط URL صفحه‌های عمومی/فراداده مجاز را نگه می‌دارد.
- در صورت تغییر API تلوبیون باید scraper دوباره با داده واقعی آزمایش شود.
- برای نسخه production بهتر است کاربر admin مستقل و بکاپ زمان‌بندی‌شده PostgreSQL اضافه شود.

## تست‌ها

```bash
python -m compileall -q twpg
bash -n deploy/*.sh
.venv/bin/python -m pip install pytest
.venv/bin/python -m pytest -q tests/test_snapshot_mapping.py
```

تست integration برای PostgreSQL فقط با `TW_TEST_DATABASE_URL` و یک دیتابیس **موقت مجزا** اجرا می‌شود. هرگز آن را به دیتابیس production وصل نکنید.

## ۸. کشف برنامه‌های جدید (Parent Catalog)

کرولر علاوه بر قسمت‌ها و کلیپ‌ها، از قوانین DOM نسخه V4 برای کشف برنامه‌های **جدید** شبکه‌ها استفاده می‌کند. این فرآیند عمداً جداگانه و دستی است؛ اطلاعات قدیمی را پاک نمی‌کند و برای هر برنامه جدید یک job به PostgreSQL اضافه می‌کند. ابتدا فقط یک شبکه کوچک را آزمایش کنید:

```bash
cd /opt/telewebion-crawler
runuser -u twcrawler -- env \
  DATABASE_URL="$(sed -n 's/^DATABASE_URL=//p' /etc/telewebion/twcrawler.env)" \
  HOME=/var/lib/twcrawler PLAYWRIGHT_BROWSERS_PATH=/var/lib/twcrawler/pw-browsers \
  .venv/bin/python -m twpg.discover --slug tv1 --scrolls 15 --delay-ms 1500
```

برای فیلم و سریال: `--slug tw-series`، برای کودک: `--slug tw-kids`. از شناسه و نشانی همان دامنه رسمی استفاده می‌شود و هیچ داده خام قبلی حذف نخواهد شد.
