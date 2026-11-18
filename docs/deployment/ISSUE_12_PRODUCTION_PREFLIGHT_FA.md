# پیش‌نیاز نصب و بازگشت Production راستی‌سی

این دستورکار برای پنجرهٔ نصبِ دارای مجوز است. مبنای ممیزی Issue #12 نسخهٔ
فعال `8158a21f5d20252cc041dec7f924504def51cf1a`، سرویس
`rastisi-rc-20261111.service`، مسیر `/srv/rastisi` و حالت `laptop` است.
هدف نهایی انتشار را پس از ادغام اصلاح Issue #12 با SHA کاملِ همان commit تعیین کنید.

## نقاط توقف و تأیید

| مرحله | شرط ادامه |
|---|---|
| CI | اجرای یکپارچهٔ main ممیزی‌شده و CI مربوط به **SHA نهاییِ قابل استقرار** هر دو موفق باشند. |
| تمرین | dry-run و one-shot حساب محدود، ارتقا و بازگشت ابزار در replica موفق باشند و ظرفیت واقعی ثبت شود. |
| بکاپ | بازیابی کامل PostgreSQL در مقصد مجزا و بررسی manifest محتوای Media ثبت شوند. رسید امضاشدهٔ لپ‌تاپ برای SHA فعال و SHA مقصد، با عمر حداکثر ۹۰ دقیقه در زمان Activate، معتبر باشد. |
| ابزار | مجوز جداگانه برای تغییر ابزارهای root-owned اخذ و مسیر جایگزین بازگشت بررسی شود. |
| برنامه | تأیید جداگانهٔ استقرار و بازبینی محیط GitHub `production` انجام شود. |
| Media | پس از سلامت برنامه، مجوز مستقل برای ACL، نقش دیتابیس، فایل محیط، one-shot و سپس فعال‌سازی تایمر اخذ شود. |

برای پنجرهٔ تغییر ابزار، ورودی‌های deploy را با هماهنگی مالک موقتاً متوقف کنید.
در GitHub هر اجرای Production منتظر تأیید، صف‌شده یا در حال اجرا را بررسی کنید.
در سرور، فایل‌های `state/INCIDENT` و `state/in_progress.json`، قفل
`state/deploy.lock` و runهای بدون `exit_code` را بررسی کنید. PID والد و فرزند و
فرمان واقعی هر PID را با `ps` تطبیق دهید و `staging/resumable/transfer.json`
را برای Stage باز بررسی کنید. در صورت اجرای فعال، launch مبهم یا incident
توقف کنید. SHA فعال، مقصد `current` و `previous`، وضعیت سرویس و گواهی TLS
را ثبت کنید. کلید، رمز و محتوای env را در گزارش قرار ندهید.

در replica سرد، از همان artifact استفاده کنید و تغییر فضای مصرفی و inode را
برای spool، استخراج، venv پیش از prune، static و مسیر موقت اندازه بگیرید.
در سرور برای هر filesystem مربوط از `findmnt -T PATH`، `df -PB1 PATH` و
`df -Pi PATH` استفاده کنید. مصرف‌های هم‌زمان روی یک mount را جمع کنید.
فضای لازم شامل artifact کامل، Stage بازشده، ساخت venv سرد، static جدید، نسخهٔ
فعال و نسخهٔ بازگشت، فضای موقت، ابزار قبلی و reserve تنظیم‌شده است. پس از
احتساب اندازه‌ها و inodeهای اندازه‌گیری‌شده باید reserve نیز آزاد بماند.
عدد ۱۶٫۷۴ GiB ممیزی فقط یک مشاهده است. نبود اندازه‌گیری یا کمبود ظرفیت شرط توقف است.

اندازه‌گیری آزمایشی برای artifact ساخته‌شده از `fabc3358…` در یک Stage
سرد نشان داد spool حدود ۶۷٬۲۶۸٬۶۰۸ بایت و ۲ inode، release نهایی
۵۳٬۷۳۵٬۴۲۴ بایت و ۳٬۳۲۷ inode و venv حدود ۱۸۰٬۹۰۳٬۹۳۶ بایت و ۱۴٬۲۷۰
inode مصرف کرد. این اعداد پس از Stage ثبت شدند و static تازه، نسخهٔ قبلی،
بکاپ و سقف لحظه‌ای استخراج wheelhouse را جداگانه باید حساب کرد.
برای SHA نهایی این اندازه‌گیری را تکرار کنید.

## نصب نسخه‌ای ابزار، پس از تأیید ابزار

در checkout معتبر و تمیز روی SHA تأییدشده، ابتدا تست‌های edge probe و migration
guard را اجرا کنید. از `deploy/release/tooling-files.txt` و فایل‌های
`smoke.d/*.py` یک پوشهٔ ابزار بسازید؛ manifest SHA256 را از همان checkout
بسازید و پس از انتقال با `sha256sum -c` دوباره کنترل کنید. صحت manifest را
با checkout مستقل از انتقال مقایسه کنید. اسکریپت exchange را از همان commit
دریافت و جداگانه کنترل کنید. چهار migration تأییدشده با SHA پین‌شده در
`migration_guard.py` و اصلاح خواندن PNG در `rastisi_edge_probe.py` را تأیید کنید.

در سیستم ساخت، از داخل checkout تأییدشده:

```sh
src="$PWD/deploy/release"
bundle="$(mktemp -d)"
install -d "$bundle/smoke.d"
while IFS= read -r f; do
  install -m 0644 "$src/$f" "$bundle/$f" || exit 1
done < "$src/tooling-files.txt"
install -m 0644 "$src"/smoke.d/*.py "$bundle/smoke.d/"
chmod 0755 "$bundle/run_current_job.sh"
(cd "$bundle" && find . -type f -print0 | sort -z | xargs -0 sha256sum) > "$bundle.sha256"
(cd "$bundle" && sha256sum -c "$bundle.sha256")
```

پس از انتقال، قبل از کپی به مقصد ممتاز، همین کنترل را دوباره اجرا کنید.
در مسیر root-owned نیز manifest را دوباره بررسی کنید.

فایل‌های کنترل‌شده را در sibling تازهٔ
`/usr/local/lib/rastisi-tools-<sha12>` نصب کنید. مالک همه root، حالت پوشه‌ها
0755 و فایل‌ها 0644 باشد. حالت `run_current_job.sh` برابر 0755 است.
مسیر فعال و sibling باید هر دو دایرکتوری فیزیکی روی همان filesystem باشند.
بستهٔ قبلی، از جمله اصلاح Edge Probe موجود، نگه داشته می‌شود. در این پنجره از
`install.sh` و نصب restic استفاده نمی‌شود و تنظیمات SSH، sudo و سرویس‌ها
دست‌نخورده می‌مانند.

```sh
# از محل اسکریپتِ تأییدشده و تحت مالکیت root
sudo python3 exchange_tooling.py "rastisi-tools-$SHA12" 3-laptop-flat-bootstrap 4-resumable-staging
# پس از بررسی نسخهٔ gate و سلامت سرویس، برای بازگشت در همان پنجره:
sudo python3 exchange_tooling.py "rastisi-tools-$SHA12" 4-resumable-staging 3-laptop-flat-bootstrap
```

اسکریپت نسخهٔ هر دو پوشه و مالکیت و mode را می‌سنجد و با تبادل اتمیک Linux
مسیر فعال را ثابت نگه می‌دارد. اگر پیام خطا آمد، قبل از تکرار نسخهٔ هر دو مسیر
را دوباره بخوانید. اگر exchange روی filesystem پشتیبانی نشد، توقف کنید.
پس از تعویض، فرمان مجاز `version` از حساب deploy باید نسخهٔ جدید را برگرداند
و health برنامهٔ فعال، مسیرهای عمومی و یک PNG باید سالم باشند. برای بازگشت
ابزار هم نبود run باز و همین بررسی سلامت الزامی است.

## انتشار برنامه، پس از تأیید جداگانه

از همان SHA نهاییِ موفق در CI و SHA فعلیِ مشاهده‌شده استفاده کنید. پس از
بازیابی ایزوله و تطبیق hash و manifest، شناسهٔ مدرک را به
`restore_drill_ref` بدهید و رسید امضاشده را با روش لپ‌تاپ ثبت کنید. اگر
رسید در زمان Activate منقضی شده، بکاپ تازه و مدرک بازیابی را در پنجرهٔ
تأییدشده تهیه کنید. Dry-run هم در Stage و state می‌نویسد و مشمول همان مجوز است.
بعد از انتشار شناسهٔ release، SHA سرویس‌دهنده، TLS، پاسخ صفحات عمومی و PNG،
و سلامت سرویس را تطبیق دهید. برای قطع ارتباط از `follow --run` و `status`
استفاده کنید. در حالت incident، عملیات را متوقف و وضعیت schema و سلامت را
بررسی کنید. بازگشت برنامه فقط با نسخهٔ قبلی سالم و Schema سازگار و پس از
مجوز، از طریق `remote_client.py rollback --to previous` انجام می‌شود.

## حساب و تایمر Media، پس از تأیید جداگانه

چیدمان adopted می‌تواند `/srv/rastisi` را به حساب deploy بسپارد. در نتیجه
مسیر مدیریتی جدید `/srv/rastisi-operator/media-by-name` است. والد مستقل
`root:root` و 0755 و غیرقابل‌نوشتن برای deploy است. دایرکتوری فرزند به
`rastisi-media-aliases:rastisi-media-aliases` و 0700 تعلق دارد. WinSCP
مدیر root به مسیر جدید منتقل می‌شود. در محیط replica با همین mode آزمایش
کنید. برای WinSCP غیر root، ابتدا هویت مدیر را تأیید و طرح دسترسی خواندن
محدود را جداگانه بررسی کنید.

1. یک حساب سیستمی بدون ورود و بدون عضویت در گروه‌های `rastisi` یا
   `www-data` ایجاد کنید. دو دایرکتوری بالا را با مالکیت و mode گفته‌شده
   بسازید. از مسیر قدیمی فقط چهار لینک `demo-arch02`، `demo-v5`، `thermco`
   و `a6a69ggnv` را پس از بررسی مقصد canonical UUID با `cp -P` منتقل
   کنید. مقصد هر لینک کپی‌شده را با `readlink` تطبیق دهید. مسیر قبلی برای
   بازگشت قابل استفاده می‌ماند. از تغییر مالکیت بازگشتی Media خودداری کنید.
2. در حساب مدیریت PostgreSQL، login تازهٔ اختصاصی با رمز جدا ایجاد کنید.
   فقط `CONNECT` روی دیتابیس موردنظر، `USAGE` روی schema و `SELECT` روی
   `stores_store` و `stores_storedomain` اعطا کنید. تنظیم
   `ALTER ROLE rastisi_alias_reader IN DATABASE <db> SET default_transaction_read_only = on`
   را اضافه کنید و امتیازهای به‌ارث‌رسیده از PUBLIC را بررسی کنید. دسترسی
   خواندن جدول بی‌ربط و نوشتن را با بررسی privileges و آزمایش در replica رد کنید.
3. فایل `/etc/rastisi/media-aliases.env` با مالک root و 0600 فقط شامل
   `DATABASE_URL` حساب اختصاصی، `DJANGO_DEBUG=False`،
   `DJANGO_ALLOWED_HOSTS`، `DJANGO_SECRET_KEY` مستقل، مقادیر صحیح
   `DJANGO_MEDIA_ROOT`، `DJANGO_PRIVATE_MEDIA_ROOT` و
   `RASTISI_PLATFORM_PRIMARY_HOST` است. اعتبارسنجی تنظیمات Django یک URL
   Redis می‌خواهد. برای این فرمان صرفاً ORM، مقدار غیرمحرمانهٔ
   `RASTISI_RATE_LIMIT_CACHE_URL=redis://127.0.0.1:1/0` کفایت می‌کند و نیاز
   به دسترسی Redis برنامه ندارد.
4. با `setfacl -m u:rastisi-media-aliases:--x PATH` فقط روی والدهایی که
   واقعاً لازم است حق عبور بدهید. از `namei -l` برای یافتن مسیر **واقعی**
   app و venv و والدهای آن‌ها و Media استفاده کنید. روی درخت app و venv
   واقعی از `setfacl -Rm u:rastisi-media-aliases:r-X PATH` استفاده کنید.
   ACL مربوط به Media فقط عبور از والد محدود است، بدون خواندن فایل یا
   دسترسی به private media. قبل از هر انتشار بعدی، تایمر را موقتاً متوقف
   کنید و حق عبور/خواندن release و venv جدید را پیش از راه‌اندازی دوباره
   کنترل کنید. ACL مسیرهای دست‌خورده را پیشاپیش با `getfacl` ثبت کنید.
5. unit و timer را نصب و `daemon-reload` کنید. برای پیش‌نمایش، یک drop-in
   موقت زیر `/run/systemd/system/rastisi-media-aliases.service.d/` بسازید
   و `ExecStart=` را خالی و سپس عین فرمان unit را بدون `--apply` ثبت کنید.
   پس از `daemon-reload` و `systemctl start rastisi-media-aliases.service`
   لاگ را بررسی کنید. drop-in موقت را بردارید و دوباره reload و start کنید
   تا one-shot واقعی با همان sandbox اجرا شود. سپس چهار لینک دستی، نبود
   دسترسی برای وب و deploy و لینک یک فروشگاه آزمایشیِ مجاز را پس از اولین
   آپلود عمومی کنترل کنید. در نهایت
   `systemctl enable --now rastisi-media-aliases.timer` را اجرا کنید.

**بازگشت Media.** تایمر را disable و stop کنید و پایان one-shot فعال را
تأیید کنید. تا فرمان Django در نسخهٔ جاری موجود است، با همان حساب و env و
گزینهٔ `--remove-managed` پیش‌نمایش بگیرید و با `--apply` فقط لینک‌های
ثبت‌شده را بردارید. WinSCP را به مسیر قبلی برگردانید. واحدها و فایل env را
از چرخه خارج و login دیتابیس را غیرفعال کنید. ACLهای اضافه‌شده را از همان
مسیرهای ثبت‌شده بردارید. فایل‌های UUID و چهار لینک دستی در مسیر قبلی
باقی می‌مانند.
