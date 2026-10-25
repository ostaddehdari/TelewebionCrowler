# تلوبیون کرولر – مرحله ۳: داشبورد و ورود حرفه‌ای

این بسته **به‌روزرسانی رابط موجود** در `/opt/telewebion-crawler` است؛ پایگاه داده PostgreSQL، واردسازی SQLite، سرویس worker و داده‌های ذخیره‌شده را تغییر نمی‌دهد.

## امکانات
- رابط فارسی راست‌به‌چپ، سایدبار سمت راست، کارت‌های حرفه‌ای، واکنش‌گرا و آیکون‌های Font Awesome؛ فونت Vazirmatn (نسل جدید وزیر) و Bootstrap RTL از CDN با CSS داخلی پشتیبان برای ساختار اصلی
- فرم ورود با رمز هش‌شده `PBKDF2-SHA256`، کوکی نشست امضاشده `HttpOnly`, `Secure`, `SameSite=Strict`, انقضای ۱۲ ساعت، خروج از سامانه، محدودیت مقدماتی تلاش ناموفق
- آمار مستقل قسمت‌ها و کلیپ‌ها، بروزرسانی خودکار هر ۱۲ ثانیه در صفحات داشبورد و کانال‌ها با شمارنده انیمیشنی
- نمایش عنوان برنامه، نام شبکه(ها)، وضعیت، تعداد قسمت‌ها و کلیپ‌های هر برنامه، جست‌وجوی متنی و فیلتر شبکه/وضعیت
- صفحه هر شبکه: تعداد برنامه‌ها، قسمت‌ها و کلیپ‌های مرتبط، وضعیت استخراج، پیشرفت برنامه‌های تمام‌شده و برنامه‌های نیازمند پیگیری

**تفسیر «باقی‌مانده»:** تعداد برنامه‌هایی که وضعیت‌شان `pending`، `partial`، `running`، `failed` یا `empty_review` است. این عدد **تعداد قسمت/کلیپ کشف‌نشده نیست**؛ برای آن داده قطعی نداریم. به سبب اشتراک برنامه‌ها، جمع تعداد محتوا در شبکه‌ها می‌تواند بیش از تعداد یکتای کل باشد.

## نصب روی سرور فعلی
فایل `TelewebionCrawler-Stage03-UI.zip` را با SCP به `/tmp` بفرستید:

```bash
# Git Bash Windows, from Downloads
scp -P 2233 ~/Downloads/TelewebionCrawler-Stage03-UI.zip root@5.61.28.187:/tmp/
ssh -p 2233 root@5.61.28.187
```

سپس روی سرور Ubuntu 26.04 اجرا کنید:

```bash
mkdir -p /root/twcrawler-stage03
unzip -o /tmp/TelewebionCrawler-Stage03-UI.zip -d /root/twcrawler-stage03
cd /root/twcrawler-stage03
bash -n deploy/install_stage03.sh
bash deploy/install_stage03.sh
```

در اولین نصب رمز قوی (حداقل ۱۴ کاراکتر) برای کاربر `manager` وارد کنید. رمز در terminal echo نمی‌شود؛ فقط هش رمز در `/etc/telewebion/twcrawler-ui.env` ذخیره می‌شود. اسکریپت مراحل احراز هویت را **پیش از غیرفعال‌سازی Basic Auth پاپ‌آپی Nginx** آزمایش می‌کند. اگر پورت 443 هنوز برای این سایت تنظیم نشده، اسکریپت با پیام خطا متوقف می‌شود تا احراز هویت ناخواسته از دسترس خارج نشود.

**آدرس‌های پنل بعد از نصب موفق:**

- `/twcrawler/login` — صفحه ورود
- `/twcrawler/dashboard` — آمار و فعالیت زنده
- `/twcrawler/channels` — شبکه‌ها و کانال‌ها
- `/twcrawler/channels/{slug}` — گزارش اختصاصی شبکه
- `/twcrawler/` — کتابخانه برنامه‌ها

## تست

```bash
systemctl status twcrawler-web --no-pager
nginx -t
curl -k -I https://5.61.28.187/twcrawler/login
curl -k -I https://5.61.28.187/twcrawler/dashboard
journalctl -u twcrawler-web -n 70 --no-pager
```

نتیجه مطلوب: `200` برای ورود و `303` برای داشبورد بدون نشست. پاسخ `401` برای API بدون نشست طبیعی است.

## بازگشت

```bash
cd /root/twcrawler-stage03
bash deploy/rollback_stage03.sh
```

## هشدار HTTPS
تا وقتی گواهی IP معتبر از Let's Encrypt دریافت نشده و Nginx از گواهی خودامضاشده استفاده می‌کند، **ورود عمومی همراه با ردکردن هشدار گواهی توصیه نمی‌شود**. برای مدیریت امن از تونل SSH یا گواهی معتبر استفاده کنید. این بسته صدور گواهی یا شبکه سرور را تغییر نمی‌دهد.

## ادامه کرول
این بسته `twcrawler-worker` را **فعال یا متوقف نمی‌کند**؛ دکمه «ادامه استخراج» فقط مقدار `settings.enabled` را تغییر می‌دهد. برای شروع کرول واقعی ابتدا سرویس worker و لاگ‌های آن را بررسی کنید. از فعال‌سازی هم‌زمان چند worker بدون هماهنگی خودداری کنید.

## همگام‌سازی GitHub

برای ارسال سورس کامل به مخزن `ostaddehdari/TelewebionCrowler`، در Git Bash ویندوز از مسیر سورس بازشده `bash deploy/push_full_repo.sh` را اجرا کنید. از credential helper ویندوز برای GitHub استفاده می‌شود.
