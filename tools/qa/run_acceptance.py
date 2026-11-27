"""Run the project acceptance labels on disposable local state, with evidence."""
import argparse
import html
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from xml.etree import ElementTree as XML

ROOT = Path(__file__).resolve().parents[2]
GROUPS = {
    "browser-management-commerce": ["tools.qa.acceptance_browser"],
    "browser-excel-v33": ["apps.dashboard.tests.test_import_v33_browser"],
    "excel-v33-data-contract": ["apps.dashboard.tests.test_import_v33"],
    "browser-signup-industry-choice": ["apps.portal.tests.test_custom_industry_browser"],
    "order-push-permissions": [
        "apps.notifications.tests.test_targeted_webpush",
        "apps.notifications.tests.test_inbox_views.TestNotificationAndWorkerFileTests",
        "apps.notifications.tests.test_inbox_wiring.OrderSideTests",
        "apps.orders.tests.test_cod_confirmation",
        "apps.customers.tests.test_account_views.AccountOrderDetailViewTests",
    ],
    "diagnostic-evidence": (
        [f"deploy.release.tests.test_stage2_availability.StageTwoTests.{test}" for test in (
            "test_disabled_keeps_original_namespace",
            "test_requires_flag_and_a_protected_root_owned_marker",
            "test_django_phases_and_slow_sql_report_without_sensitive_data",
        )] if os.name == "nt" else ["deploy.release.tests.test_stage2_availability"]
    ),
}
KEEP = {"PATH", "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "SYSTEMROOT",
        "WINDIR", "COMSPEC", "LANG", "LC_ALL", "PLAYWRIGHT_BROWSERS_PATH", "PATHEXT"}


def run(output, chromium):
    output.mkdir(parents=True, exist_ok=False)
    results = []
    with tempfile.TemporaryDirectory(prefix="rastisi-e2e-") as scratch_name:
        scratch = Path(scratch_name)
        env = {k: v for k, v in os.environ.items() if k.upper() in KEEP}
        deps = str(Path(importlib.util.find_spec("playwright").origin).parent.parent)
        env.update(
            PYTHONPATH=os.pathsep.join((deps, str(ROOT))),
            TEMP=str(scratch), TMP=str(scratch), TMPDIR=str(scratch),
            DJANGO_SETTINGS_MODULE="shop_core.settings_browser_tests", DJANGO_DEBUG="True",
            DJANGO_ALLOW_ASYNC_UNSAFE="true", DJANGO_MEDIA_ROOT=str(scratch / "media"),
            DJANGO_PRIVATE_MEDIA_ROOT=str(scratch / "private"), RASTISI_QA_MODE="1",
            RASTISI_EMAIL_PROVIDER="fake", RASTISI_OWNER_SMS_BACKEND="console",
            RASTISI_OWNER_SMS_ALLOW_CONSOLE_OTP="1", RASTISI_DEV_OTP_CODE="123456",
            RASTISI_ACCEPTANCE_OUTPUT=str(output), SFB_BROWSER_TESTS="1", RASTISI_V33_BROWSER="1",
            PYTHONIOENCODING="utf-8",
        )
        if chromium:
            env["SFB_CHROMIUM_PATH"] = env["RASTISI_CHROMIUM"] = str(chromium)
        for name, labels in GROUPS.items():
            cmd = [sys.executable, "manage.py", "test", *labels, "--noinput", "--verbosity", "2"]
            log = output / f"{name}.log"
            with log.open("w", encoding="utf-8") as stream:
                result = subprocess.run(cmd, env=env, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
            contents = log.read_text(encoding="utf-8", errors="replace")
            # These named acceptance suites require their browser checks to run.
            skipped = name.startswith("browser-") and ("skipped '" in contents or "skipped=" in contents)
            result_name = "FAIL" if result.returncode or skipped else "PASS"
            results.append({"name": name, "status": result_name, "log": log.name,
                            "exit_code": result.returncode, "browser_skipped": skipped})
            print(result_name, name, flush=True)
    if os.name == "nt":
        results.append({"name": "gunicorn-posix-configuration-and-hooks", "status": "NOT_RUN",
                        "log": None, "reason": "Run unchanged on Linux in release-system CI."})
    results.append({"name": "physical-android-iphone-delivery", "status": "NOT_RUN",
                    "log": None, "reason": "Physical devices are required."})
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    (output / "summary.json").write_text(json.dumps({"sha": sha, "groups": results}, indent=2), encoding="utf-8")
    suite = XML.Element("testsuite", name="RastiSi acceptance", tests=str(len(results)),
                        failures=str(sum(item["status"] == "FAIL" for item in results)))
    for result in results:
        case = XML.SubElement(suite, "testcase", name=result["name"])
        if result["status"] == "FAIL":
            XML.SubElement(case, "failure", message=result["log"])
        elif result["status"] == "NOT_RUN":
            XML.SubElement(case, "skipped", message=result["reason"])
    XML.ElementTree(suite).write(output / "junit.xml", encoding="utf-8", xml_declaration=True)
    rows = "".join(
        f'<li><a href="{r["log"]}">{r["name"]}</a> — {r["status"]}</li>' if r["log"]
        else f'<li>{r["name"]} — {r["status"]}: {r["reason"]}</li>' for r in results
    )
    attachments = "".join(
        f'<li><a href="{html.escape(path.name, quote=True)}">{html.escape(path.name)}</a></li>'
        for path in sorted(output.iterdir()) if path.suffix in {".png", ".xlsx", ".json"}
    )
    (output / "index.html").write_text(
        f'<!doctype html><meta charset="utf-8"><title>RastiSi acceptance</title>'
        f'<h1>RastiSi acceptance</h1><p>{sha}</p><ul>{rows}</ul><h2>Evidence</h2><ul>{attachments}</ul>',
        encoding="utf-8",
    )
    return int(any(item["status"] == "FAIL" for item in results))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--chromium", type=Path, required=True)
    args = parser.parse_args()
    if importlib.util.find_spec("playwright") is None:
        parser.error("Install tools/qa/requirements-management-browser.txt in the QA runtime.")
    raise SystemExit(run(args.output.resolve(), args.chromium.resolve() if args.chromium else None))
