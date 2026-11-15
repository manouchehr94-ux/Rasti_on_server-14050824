#!/usr/bin/env python3
"""Plan/apply adoption of the existing /srv/rastisi flat layout. Administrator only.

No database changes, no nginx changes, no media copies, no backups on the server.
The exact source manifest from the NEW repository must match before adoption.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rastisi_common as C
from make_bootstrap_manifest import SOURCE_ROOTS
from make_dropin import build_dropin

BASE = Path("/srv/rastisi")
SERVICE = "rastisi-rc-20261111.service"
ENV = Path("/etc/rastisi/rc-20261111.env")
HERE = Path(__file__).resolve().parent
PROTECTED = ("rasti.service", "rasti-bale-worker.service")


def run(*args, **kwargs):
    return subprocess.check_output(list(args), text=True, **kwargs).strip()


def source_hashes(root):
    files = {}
    for name in SOURCE_ROOTS:
        p = root / name
        for item in p.rglob("*") if p.is_dir() else [p]:
            if "__pycache__" in item.parts or item.suffix in (".pyc", ".pyo"):
                continue
            if item.is_symlink():
                raise ValueError("unexpected source symlink")
            if item.is_file():
                files[item.relative_to(root).as_posix()] = C.sha256_file(item)
    return files


def verify_baseline(root, manifest):
    if manifest.get("schema") != 1 or not C.SHA_RE.fullmatch(manifest.get("sha", "")):
        raise ValueError("invalid Git baseline manifest")
    actual = source_hashes(root)
    expected = manifest.get("files", {})
    if not expected or actual != expected:
        differences = sorted(k for k in set(actual) | set(expected) if actual.get(k) != expected.get(k))
        raise ValueError("source differs from Git baseline (nothing registered): " + ", ".join(differences[:15]))


def health():
    for _ in range(2):
        code = run("curl", "--resolve", "rastisi.ir:443:127.0.0.1", "--connect-timeout", "5", "--max-time", "20", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "https://rastisi.ir/")
        if code != "200":
            raise ValueError("HTTPS health check did not return 200")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", required=True)
    p.add_argument("--backup-public-key", required=True)
    p.add_argument("--deploy-public-key", required=True)
    p.add_argument("--apply", action="store_true")
    a = p.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("run via administrator SSH")
    old = (BASE / "current").resolve()
    if old.parent != BASE / "releases" or not re.fullmatch("[0-9a-f]{40}", old.name):
        raise SystemExit("STOP: unexpected current layout; adoption may already be complete")
    manifest = json.loads(Path(a.manifest).read_text(encoding="utf-8-sig"))
    verify_baseline(old, manifest)
    app_user = run("systemctl", "show", SERVICE, "-p", "User", "--value")
    if not app_user or app_user == "root" or pwd.getpwnam(app_user).pw_uid == 0:
        raise SystemExit("STOP: service must have a dedicated non-root application user")
    unit = run("systemctl", "cat", SERVICE)
    dropin, _, _, _ = build_dropin(unit, str(BASE), str(BASE / "current"))
    env_stat = ENV.stat()
    if ENV.is_symlink() or env_stat.st_uid != 0 or env_stat.st_mode & 0o022:
        raise SystemExit("STOP: production environment must be a root-owned regular file without group/world write")
    values = C.parse_env_file(ENV)
    for name, env_key in (("media", "DJANGO_MEDIA_ROOT"), ("private_media", "DJANGO_PRIVATE_MEDIA_ROOT"), ("static", "DJANGO_STATIC_ROOT")):
        if values.get(env_key) != str(BASE / "shared" / name):
            raise SystemExit("STOP: unexpected " + env_key)
    if Path("/etc/rastisi/deploy.conf").exists() or (BASE / "state/active.json").exists():
        raise SystemExit("STOP: deployment configuration/state already exists; inspect instead of reinstalling")
    for keypath in (a.backup_public_key, a.deploy_public_key):
        key = Path(keypath).read_text().strip()
        if not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/=]+(?: [^\r\n]*)?", key):
            raise SystemExit("STOP: expected one Ed25519 public key")
    if Path(a.backup_public_key).read_text().split()[:2] == Path(a.deploy_public_key).read_text().split()[:2]:
        raise SystemExit("STOP: backup signing key must differ from deployment key")
    # Any existing RastiSi scheduled command needs an explicit transition to the current-release wrapper.
    cron_files = [Path("/etc/crontab"), *Path("/etc/cron.d").glob("*"), *Path("/var/spool/cron/crontabs").glob("*")]
    for cron in cron_files:
        if cron.is_file():
            for line in cron.read_text(errors="replace").splitlines():
                if not line.lstrip().startswith("#") and re.search("rastisi", line, re.I):
                    raise SystemExit("STOP: review existing RastiSi cron entry in " + str(cron))
    timers = run("systemctl", "list-timers", "--all", "--no-pager")
    if "rastisi" in timers.lower():
        raise SystemExit("STOP: review existing RastiSi timers before adoption")
    protected_before = {s: run("systemctl", "show", s, "-p", "ActiveEnterTimestampMonotonic", "--value") for s in PROTECTED}
    for s in (SERVICE, *PROTECTED):
        subprocess.run(["systemctl", "is-active", "--quiet", s], check=True)
    health()
    print("BASELINE_MATCH sha=%s files=%d app_user=%s" % (manifest["sha"], len(manifest["files"]), app_user))
    print("PLAN: install laptop-mode tooling; register legacy links; update ONLY RastiSi drop-in; verify HTTPS")
    print("PLAN: env file becomes root:app-group 0640 for the app helper; no secret value changes")
    print("PLAN: existing shared data, nginx, PostgreSQL, Redis and RastiService remain untouched")
    if not a.apply:
        print("BOOTSTRAP_PLAN_OK; --apply performs the plan")
        return
    try:
        pwd.getpwnam("rastisi-deploy")
    except KeyError:
        pass
    else:
        raise SystemExit("STOP: deploy account already exists; inspect its ownership and keys before adoption")
    dropin_path = Path("/etc/systemd/system") / (SERVICE + ".d") / "90-cicd-layout.conf"
    if dropin_path.exists():
        raise SystemExit("STOP: bootstrap drop-in already exists")
    subprocess.run(["bash", str(HERE / "install.sh"), "--service", SERVICE, "--app-user", app_user, "--base", str(BASE), "--env-file", str(ENV), "--port", "18082", "--backup-mode", "laptop", "--adopt-flat-layout"], check=True)
    # The app-task helper runs as the app user, not as root. Deploy remains
    # outside that group and can only request the fixed sudo helper verbs.
    os.chown(ENV, 0, pwd.getpwnam(app_user).pw_gid)
    ENV.chmod(0o640)
    conf = Path("/etc/rastisi/deploy.conf")
    text = conf.read_text().replace("smoke_host = CHANGE_ME_PLATFORM_HOST", "smoke_host = rastisi.ir").replace("jobs_follow_current = false", "jobs_follow_current = true")
    conf.write_text(text)
    signers = Path("/etc/rastisi/backup_allowed_signers")
    signers.write_text("rastisi-laptop namespaces=\"rastisi-backup\" " + Path(a.backup_public_key).read_text().strip() + "\n")
    signers.chmod(0o644)
    home = Path(pwd.getpwnam("rastisi-deploy").pw_dir)
    sshdir = home / ".ssh"
    if sshdir.exists() and (sshdir.is_symlink() or any(sshdir.iterdir())):
        raise SystemExit("STOP: deploy SSH configuration already exists; merge by inspection")
    sshdir.mkdir(mode=0o700, exist_ok=True)
    auth = sshdir / "authorized_keys"
    auth.write_text('restrict,command="/usr/bin/python3 /usr/local/lib/rastisi/rastisi_gate.py" ' + Path(a.deploy_public_key).read_text().strip() + "\n")
    auth.chmod(0o600)
    deploy = pwd.getpwnam("rastisi-deploy")
    os.chown(sshdir, deploy.pw_uid, deploy.pw_gid)
    os.chown(auth, deploy.pw_uid, deploy.pw_gid)
    # Account-wide forced command, including password/session attempts. Validate before reload.
    ssh_config = Path("/etc/ssh/sshd_config")
    original_ssh = ssh_config.read_text()
    marker = "# BEGIN RASTISI DEPLOY ACCOUNT"
    if marker in original_ssh:
        raise SystemExit("STOP: deploy SSH restriction already exists")
    snippet = (HERE / "examples/sshd_config.snippet").read_text()
    ssh_config.write_text(original_ssh.rstrip() + "\n\n" + marker + "\n" + snippet + "\n")
    try:
        subprocess.run(["/usr/sbin/sshd", "-t"], check=True)
        subprocess.run(["systemctl", "reload", "ssh"], check=True)
    except BaseException:
        ssh_config.write_text(original_ssh)
        raise
    rid = "legacy-" + manifest["sha"][:12]
    legacy = BASE / "releases" / rid
    legacy.mkdir()
    (legacy / "app").symlink_to(old)
    (legacy / "venv").symlink_to(old / ".venv")
    C.atomic_write_json(legacy / "release.json", {"id": rid, "sha": manifest["sha"], "kind": "legacy", "migration_report": {"level": "NONE", "new_migrations": []}}, mode=0o644)
    from rastisi_release import State
    cfg = C.Config()
    state = State(cfg)
    state.ensure()
    state.set_status(rid, "active", sha=manifest["sha"])
    state.set_active(current=rid, previous=None, sha=manifest["sha"], migrations_applied=[], rollback_safe_to_previous=False, via="verified-flat-bootstrap")
    # Files written by this administrative bootstrap must be readable by deploy.
    for q in (cfg.state, *cfg.state.rglob("*")):
        if not q.is_symlink():
            os.chown(q, deploy.pw_uid, pwd.getpwnam(app_user).pw_gid)
    original_link = os.readlink(BASE / "current")
    try:
        dropin_path.parent.mkdir(parents=True, exist_ok=True)
        dropin_path.write_text(dropin)
        C.atomic_symlink("releases/" + rid, BASE / "current")
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "restart", SERVICE], check=True)
        import time
        for attempt in range(30):
            try:
                health()
                break
            except (ValueError, subprocess.CalledProcessError):
                if attempt == 29:
                    raise
                time.sleep(3)
    except BaseException:
        C.atomic_symlink(original_link, BASE / "current")
        dropin_path.unlink(missing_ok=True)
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "restart", SERVICE], check=True)
        print("BOOTSTRAP_ROLLED_BACK; tooling/state remain installed: inspect before retry", file=sys.stderr)
        raise
    for s in PROTECTED:
        if run("systemctl", "show", s, "-p", "ActiveEnterTimestampMonotonic", "--value") != protected_before[s]:
            raise SystemExit("STOP: protected service start timestamp changed; investigate")
    print("BOOTSTRAP_APPLIED_HTTP_200")


if __name__ == "__main__":
    main()
