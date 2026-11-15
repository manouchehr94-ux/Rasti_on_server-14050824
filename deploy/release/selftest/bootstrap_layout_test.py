#!/usr/bin/env python3
"""Disposable-container test of flat-layout adoption, including failed-start rollback.

Real filesystem/accounts/installer/sudoers/sshd validation; systemctl and HTTPS
are controlled executables. Never run on a real application host.
"""
import hashlib
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
from unittest.mock import patch


def run(*args):
    subprocess.run(args, check=True, capture_output=True)


def main():
    if os.geteuid() != 0 or not Path("/.dockerenv").exists() or not Path("/repo/deploy/release").is_dir():
        raise SystemExit("disposable root container with /repo only")
    sys.path.insert(0, "/repo/deploy/release")
    import bootstrap_existing as boot
    root = Path("/test")
    root.mkdir()
    bin_dir = root / "bin"
    bin_dir.mkdir()
    manager = bin_dir / "systemctl"
    manager.write_text('''#!/usr/bin/python3
import pathlib,sys
a=sys.argv[1:]
with open('/test/systemctl-calls','a') as f: f.write(' '.join(a)+'\\n')
if a[:1]==['cat']:
 print('[Service]\\nUser=rastisi\\nGroup=rastisi\\nWorkingDirectory=/srv/rastisi/current\\nExecStart=/srv/rastisi/current/.venv/bin/python -m gunicorn shop_core.wsgi:application --bind 127.0.0.1:18082 --workers 1 --threads 2 --timeout 40')
elif a[:1]==['show']: print('rastisi' if 'User' in a else '9001')
elif a[:1]==['list-timers']: print('No timers listed')
''')
    manager.chmod(0o755)
    curl = bin_dir / "curl"
    curl.write_text('''#!/usr/bin/python3
import os,pathlib
link=os.readlink('/srv/rastisi/current')
print('503' if os.getenv('RASTISI_TEST_BOOTSTRAP_FAIL')=='1' and 'legacy-' in link else '200',end='')
''')
    curl.chmod(0o755)
    os.environ["PATH"] = str(bin_dir) + ":" + os.environ["PATH"]
    run("useradd", "--system", "--no-create-home", "--shell", "/bin/false", "rastisi")
    app = pwd.getpwnam("rastisi")
    base = Path("/srv/rastisi")
    old = base / "releases" / ("5" * 40)
    (old / ".venv/bin").mkdir(parents=True)
    (old / "manage.py").write_text("# synthetic bootstrap app\n")
    (old / ".venv/bin/python").symlink_to("/usr/bin/python3")
    (base / "current").symlink_to("releases/" + old.name)
    for name in ("media", "private_media", "static"):
        p = base / "shared" / name
        p.mkdir(parents=True)
        os.chown(p, app.pw_uid, app.pw_gid)
        p.chmod(0o750)
        (p / "canary").write_text("preserve " + name)
    env = Path("/etc/rastisi/rc-20261111.env")
    env.parent.mkdir(parents=True)
    env.write_text("".join("DJANGO_%s_ROOT=%s/shared/%s\n" % (key, base, name)
                           for key, name in (("MEDIA", "media"), ("PRIVATE_MEDIA", "private_media"), ("STATIC", "static"))))
    os.chown(env, 0, app.pw_gid)
    env.chmod(0o640)
    manifest = root / "baseline.json"
    manifest.write_text(json.dumps({"schema": 1, "sha": "a" * 40,
        "files": {"manage.py": hashlib.sha256((old / "manage.py").read_bytes()).hexdigest()}}))
    for name in ("backup", "deploy"):
        run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(root / name))
    Path("/run/sshd").mkdir(exist_ok=True)
    run("ssh-keygen", "-A")
    protected = {p: (p.stat().st_uid, p.stat().st_gid, p.stat().st_mode, p.read_bytes() if p.is_file() else None)
                 for p in (base / "shared").rglob("*")}
    argv = ["bootstrap_existing.py", "--manifest", str(manifest),
            "--backup-public-key", str(root / "backup.pub"), "--deploy-public-key", str(root / "deploy.pub")]
    with patch.object(sys, "argv", argv):
        boot.main()
    assert not Path("/etc/rastisi/deploy.conf").exists(), "plan wrote deployment configuration"
    assert os.readlink(base / "current") == "releases/" + old.name
    failed = os.environ.get("RASTISI_TEST_BOOTSTRAP_FAIL") == "1"
    try:
        with patch.object(sys, "argv", argv + ["--apply"]), patch("time.sleep", return_value=None):
            boot.main()
    except ValueError:
        if not failed:
            raise
    else:
        assert not failed, "bad health was not rejected"
    dropin = Path("/etc/systemd/system/rastisi-rc-20261111.service.d/90-cicd-layout.conf")
    if failed:
        assert os.readlink(base / "current") == "releases/" + old.name
        assert not dropin.exists()
        print("PASS_BOOTSTRAP_FAILED_HEALTH_RESTORES_FLAT_LINK_AND_DROPIN")
    else:
        assert (base / "current/app").resolve() == old
        assert "python -m gunicorn" in dropin.read_text()
        assert "WorkingDirectory=/srv/rastisi/current/app" in dropin.read_text()
        for name in ("state", "venvs", "staging", "releases", "state/static-build"):
            assert (base / name).stat().st_gid == app.pw_gid
        run("runuser", "-u", "rastisi", "--", "test", "-w", str(base / "state/static-build"))
        assert not Path("/usr/local/lib/rastisi-backup").exists()
        print("PASS_BOOTSTRAP_FLAT_ADOPTION_GROUPS_AND_SSH_CONFIG")
    for p, before in protected.items():
        assert before == (p.stat().st_uid, p.stat().st_gid, p.stat().st_mode, p.read_bytes() if p.is_file() else None), p
    calls = (root / "systemctl-calls").read_text().splitlines()
    assert all(line == "restart rastisi-rc-20261111.service" for line in calls if line.startswith("restart "))
    print("PASS_SHARED_MEDIA_AND_PROTECTED_SERVICES_UNCHANGED")


if __name__ == "__main__":
    main()
