# استقرار مخزن جدید با بکاپ فقط روی لپ‌تاپ

این راهنما مربوط به مخزن `manouchehr94-ux/Rasti_on_server-14050824` است.
کد منبع، تست‌ها و مستندات در مخزن می‌مانند. `build_release.py` فقط فایل‌های
Production را بسته‌بندی می‌کند؛ پاکسازی دستی بعد از هر استقرار لازم نیست.

## وضعیت فعلی

این راهنما مراحل پس از قرارگرفتن کد در GitHub را توضیح می‌دهد؛ نصب روی سرور
هنوز انجام نشده است. ورودی‌های تست از
فایل‌های ارسالی شما بازیابی شدند. خطاهای تأییدشدهٔ محصول، تست و وابستگی‌ها
اصلاح شده‌اند؛ نتیجهٔ هر مجموعه و محدودیت‌های اعتبارسنجی در
`INTEGRATION_STATUS_20261115.md` ثبت می‌شود. هیچ استثنای خطا یا آسیب‌پذیری
برای سبزشدن CI پذیرفته نشده است.

## اجرای ساده روی لپ‌تاپ

پس از واردکردن و ارسال Commitهای این بسته، در ریشهٔ مخزن اجرا کنید:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy\release\Prepare-Production.ps1
```

این فرمان فقط طرح انتقال را روی سرور بررسی می‌کند. دو کلید جدا در
`C:\Users\hp\RastiSi-Server-Backup-PRIVATE\deployment-keys` می‌سازد؛ کلید خصوصی
به سرور منتقل نمی‌شود. مسیر فایل JSON متغیرهای عمومی GitHub را چاپ می‌کند.
پس از دریافت `BOOTSTRAP_PLAN_OK` و آماده‌بودن تنظیمات GitHub و CI، همان فرمان
با `-Apply` انتقال اولیه را اجرا می‌کند. خروجی ناموفق را پیش از تکرار بررسی کنید.

## انتقال اولیهٔ ساختار موجود

سرور فعلی `current/.venv` دارد؛ ابزار استقرار از `current/app` و
`current/venv` استفاده می‌کند. اسکریپت `bootstrap_existing.py` این انتقال را
به‌صورت صریح انجام می‌دهد؛ داده‌ها را جابه‌جا نمی‌کند و مسیر فیزیکی Release
قبلی را برای بازگشت نگه می‌دارد.

۱. در لپ‌تاپ از Commit اولیهٔ مخزن جدید، Manifest بسازید:

```powershell
py -3 deploy/release/make_bootstrap_manifest.py --repo . --out bootstrap-source.json
```

Commit پیش‌فرض `86742072855eb0d937a63be0c7662919d0dbef87` است. این عدد
به‌صورت حدسی به سرور نسبت داده نمی‌شود: تطبیق تک‌تک فایل‌ها الزامی است. تغییر
favicon در Commit بعدی جداگانه منتشر می‌شود.

۲. دو کلید Ed25519 جدا بسازید: کلید استقرار و کلید امضای بکاپ. کلید خصوصی
امضای بکاپ فقط در لپ‌تاپ می‌ماند. فقط کلید خصوصی استقرار به Secret محیط
Production در GitHub می‌رود. هیچ کلید خصوصی وارد مخزن یا چت نشود.

۳. ابزارهای `deploy/release`، فایل Manifest و دو کلید **عمومی** را در مسیر
موقت اختصاصی سرور منتقل کنید و از همان مسیر طرح را بخوانید:

```bash
python3 deploy/release/bootstrap_existing.py \
  --manifest bootstrap-source.json \
  --backup-public-key laptop-backup.pub \
  --deploy-public-key github-deploy.pub
```

بدون `--apply`، فقط بررسی و طرح انجام می‌شود. ابتدا باید `BOOTSTRAP_PLAN_OK`
دریافت شود. طرح، کاربر واقعی سرویس، مسیرهای Shared، نبود cron/timer وابسته،
سلامت HTTPS و مطابقت کد را کنترل می‌کند. وجود تنظیمات استقرار قبلی باعث توقف
می‌شود؛ آن‌ها خودکار بازنویسی نمی‌شوند.

پس از آماده‌بودن CI و بررسی طرح، `--apply` همان عملیات را اجرا می‌کند: نصب
ابزار root-owned، حساب محدود استقرار، ثبت Legacy، drop-in مختص RastiSi، و
Restart فقط RastiSi با Retry سلامت. فایل env برای helper برنامه به
`root:app-group 0640` تغییر مجوز می‌دهد؛ مقدار Secretها تغییر نمی‌کند.
تنظیم حساب SSH با `sshd -t` کنترل می‌شود. RastiService، Nginx، دیتابیس، Redis
و Media تغییر نمی‌کنند. بازگشت bootstrap در خطای سلامت، لینک و drop-in قبلی
را برمی‌گرداند؛ فایل‌های ابزار/تنظیمات نصب‌شده باقی می‌مانند و قبل از تلاش
دوباره باید بررسی شوند.

## تنظیم GitHub

خصوصی‌بودن مخزن را در Settings بررسی کنید. Workflowهای Release و Rollback
دستی هستند. Secret `DEPLOY_SSH_KEY` فقط در محیط `production` قرار گیرد.
متغیرها: `DEPLOY_HOST`، `DEPLOY_USER=rastisi-deploy`، `DEPLOY_PORT=22`،
`DEPLOY_KNOWN_HOSTS`، `PRODUCTION_URL=https://rastisi.ir` و
`RASTISI_PYTHON_VERSION=3.12`.

Host key را از اتصال SSH مدیریتیِ از قبل معتبر بردارید؛ host-key checking
غیرفعال نشود. محیط production به شاخهٔ main محدود و حفاظت/Reviewer موردنظر
مالک واقعاً در تنظیمات اعمال و بررسی شود؛ متن Workflow آن را ایجاد نمی‌کند.
فعال‌بودن و امکانات Required reviewers برای پلن و مخزن خصوصی بررسی شود.

## بکاپ قبل از Migration

برای تغییر بدون migration، لپ‌تاپ لازم نیست. برای Migration افزایشی:

```powershell
py -3 deploy/release/laptop_backup.py `
  --destination 'C:\Users\hp\RastiSi-Server-Backup-PRIVATE\deploy-UNIQUE-ID' `
  --from-sha FULL_CURRENT_COMMIT `
  --to-sha FULL_TARGET_COMMIT `
  --signing-key 'C:\path-outside-repository\laptop-backup-signing' `
  --pg-restore-image postgres:16
```

از شناسه‌های واقعی ۴۰کاراکتری و پوشهٔ جدید استفاده شود. نسخهٔ PostgreSQL
اعتبارسنج باید هم‌نسخه یا جدیدتر از dump باشد. اگر pg_restore بومی نصب است،
به‌جای گزینهٔ Docker از `--pg-restore PATH` استفاده کنید. این فرمان با SSH
مدیریتی root انجام می‌شود؛ کلید root هرگز به GitHub داده نمی‌شود.

DB و Media مستقیماً روی لپ‌تاپ ذخیره می‌شوند. Manifest فایل‌های عمومی و خصوصی
پیش و پس از انتقال با آرشیو SHA256 مقایسه می‌شود. تغییر هم‌زمان Media باعث
توقف و صادرنشدن رسید می‌شود. dump با pg_restore خوانده می‌شود، ولی این کار
بازیابی کامل Production را اثبات نمی‌کند. سازگاری تراکنشی هم‌زمان DB و Media
نیز ادعا نمی‌شود. رسید امضاشده فقط متادیتا دارد و به Commit مبدأ/مقصد، نام DB
و عمر بکاپ وابسته است؛ روی سرور دادهٔ بکاپ ذخیره نمی‌شود. پیش‌فرض اعتبار ۹۰
دقیقه است. پوشهٔ ناقص حذف نمی‌شود و رسید معتبر برای آن صادر نمی‌گردد.

## انتشار و بازگشت

CI ابتدا کامل‌بودن ورودی‌های تست، ابزارها، Secretها، آسیب‌پذیری‌ها و تست‌ها را
بررسی می‌کند. Release یک artifact با وابستگی‌های دارای hash می‌سازد و همان
مجموعه را تست می‌کند. سرور checksum و Manifest را دوباره بررسی می‌کند، venv
را روی همان سرور می‌سازد، preflight را اجرا می‌کند، Static را منتشر و لینک
current را اتمیک عوض می‌کند. شکست smoke در تغییر سازگار با schema باعث
Rollback کد می‌شود. Rollback دیتابیس را عقب نمی‌برد. Migration نامطمئن یا
غیرقابل‌برگشت، برنامهٔ نگهداری مستقل می‌خواهد و online مسدود است.

تنظیم cronهای مالی/اشتراک و اعلان‌ها وظیفهٔ جداگانه‌ای است؛ وجود فایل نمونه
`deploy/cron/rastisi.crontab` به معنی نصب آن نیست. مقادیر CHANGE_ME و مسیرهای
نمونه نباید مستقیم روی Production نصب شوند.
