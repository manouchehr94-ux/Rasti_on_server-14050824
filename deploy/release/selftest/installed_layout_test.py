#!/usr/bin/env python3
"""Run ONLY in a disposable Linux container, as root. No external SSH/DB hosts.

Real installer, Unix accounts, sudo/sshd forced command, PostgreSQL and restic.
The repository is mounted read-only at /repo. All state is under /test and the
container's /etc, /srv and /usr/local/lib. Never run this on an application host.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def run(args, *, user=None, check=True, env=None):
    cmd = (['runuser', '-u', user, '--'] if user else []) + list(map(str, args))
    p = subprocess.run(cmd, env=env, text=True, capture_output=True, timeout=180)
    if check and p.returncode:
        raise AssertionError('%s exited %s\n%s\n%s' % (cmd[:5], p.returncode, p.stdout, p.stderr))
    return p


def main():
    if not Path('/.dockerenv').exists() or os.geteuid() != 0 or not Path('/repo/deploy/release').is_dir():
        raise SystemExit('requires a disposable container with read-only /repo mount')
    os.umask(0o022)
    root = Path('/test')
    root.mkdir(exist_ok=True)
    run(['useradd', '-m', '-s', '/bin/sh', 'rastisi'])
    run(['bash', '/repo/deploy/release/install.sh', '--service', 'test-rastisi.service', '--app-user', 'rastisi', '--base', '/srv/rastisi-releases', '--env-file', '/etc/rastisi/test.env', '--port', '18082'])
    assert Path('/usr/local/lib/rastisi/make_dropin.py').is_file()
    run(['visudo', '-cf', '/etc/sudoers.d/rastisi-deploy'])
    run(['usermod', '-p', '*', 'rastisi-deploy'])
    # Local, generated test keys only; no externally configured identity.
    run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', '/test/client_key'])
    ssh_dir = Path('/home/rastisi-deploy/.ssh')
    ssh_dir.mkdir(mode=0o700)
    pub = Path('/test/client_key.pub').read_text().strip()
    (ssh_dir/'authorized_keys').write_text('restrict,command="/usr/bin/python3 /usr/local/lib/rastisi/rastisi_gate.py" ' + pub + '\n')
    os.chmod(ssh_dir/'authorized_keys', 0o600)
    run(['chown', '-R', 'rastisi-deploy:rastisi-deploy', ssh_dir])
    Path('/run/sshd').mkdir(exist_ok=True)
    os.chmod('/run/sshd', 0o755)
    os.chown('/run/sshd', 0, 0)
    run(['ssh-keygen', '-A'])
    snippet = Path('/repo/deploy/release/examples/sshd_config.snippet').read_text()
    Path('/test/sshd.conf').write_text('Port 2222\nListenAddress 127.0.0.1\nHostKey /etc/ssh/ssh_host_ed25519_key\nPidFile /test/sshd.pid\nUsePAM no\nPermitRootLogin no\n' + snippet)
    run(['/usr/sbin/sshd', '-t', '-f', '/test/sshd.conf'])
    run(['/usr/sbin/sshd', '-f', '/test/sshd.conf'])
    hostkey = Path('/etc/ssh/ssh_host_ed25519_key.pub').read_text().split()
    Path('/test/known_hosts').write_text('[127.0.0.1]:2222 ' + ' '.join(hostkey[:2]) + '\n')
    ssh = ['ssh', '-p', '2222', '-i', '/test/client_key', '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=/test/known_hosts', '-o', 'BatchMode=yes', 'rastisi-deploy@127.0.0.1']
    version = json.loads(run(ssh + ['version']).stdout)
    assert version['tooling'] == Path('/repo/deploy/release/TOOLING_VERSION').read_text().strip()
    assert run(ssh + ['id'], check=False).returncode != 0
    assert run(ssh, check=False).returncode != 0
    assert run(ssh[:-1] + ['-o', 'ExitOnForwardFailure=yes', '-R', '0:localhost:9', ssh[-1], 'version'], check=False).returncode != 0
    assert run(['sudo', '-n', '-u', 'root', '/usr/bin/id'], user='rastisi-deploy', check=False).returncode != 0
    assert run(['sudo', '-n', '-u', 'rastisi', '/usr/bin/python3', '/usr/local/lib/rastisi/rastisi_app_task.py', 'who'], user='rastisi-deploy').returncode == 0
    print('PASS installed SSH: version, forced-command rejection, no interactive shell, no port forwarding, restricted sudo', flush=True)

    run(['pg_ctlcluster', '16', 'main', 'start'])
    run(['psql', '-v', 'ON_ERROR_STOP=1', '-c', "CREATE ROLE rastisi_app LOGIN PASSWORD 'local-test-app'; CREATE ROLE rastisi_drill LOGIN PASSWORD 'local-test-drill' CREATEDB NOSUPERUSER NOCREATEROLE;"], user='postgres')
    run(['createdb', '-O', 'rastisi_app', 'rastisi_test'], user='postgres')
    pg_env = dict(os.environ, PGHOST='127.0.0.1', PGUSER='rastisi_app', PGPASSWORD='local-test-app', PGDATABASE='rastisi_test')
    run(['psql', '-v', 'ON_ERROR_STOP=1', '-c', "CREATE TABLE stores_store(id serial primary key,name text); CREATE TABLE orders_order(id serial primary key,total int); CREATE TABLE auth_user(id serial primary key,username text); INSERT INTO stores_store(name) VALUES('local-canary-shop'); INSERT INTO orders_order(total) SELECT g FROM generate_series(1,300) g; INSERT INTO auth_user(username) VALUES('local-canary-user');"], env=pg_env)
    for name in ['media', 'backup-state', 'restic']:
        (root/name).mkdir()
    (root/'media/canary.txt').write_text('local synthetic customer media')
    Path('/etc/rastisi/test.env').write_text('DATABASE_URL=postgresql://rastisi_app:local-test-app@127.0.0.1:5432/rastisi_test\n')
    run(['chown', 'rastisi:rastisi', '/etc/rastisi/test.env'])
    os.chmod('/etc/rastisi/test.env', 0o600)
    (root/'password').write_text('local-test-restic-password')
    os.chmod(root/'password', 0o600)
    run(['chown', '-R', 'rastisi:rastisi', root/'media', root/'backup-state', root/'restic', root/'password'])
    Path('/etc/rastisi/backup.conf').write_text('[backup]\nenv_file=/etc/rastisi/test.env\nstate_dir=/test/backup-state\nmedia_root=/test/media\nrestic_repository=/test/restic\nrestic_password_file=/test/password\nconfig_files=/etc/rastisi/test.env\ndrill_admin_url=postgresql://rastisi_drill:local-test-drill@127.0.0.1:5432/postgres\ncritical_tables=stores_store,orders_order,auth_user\nretention=yes\ncheck_read_data_subset=100%\n')
    run(['restic', '-r', '/test/restic', '--password-file', '/test/password', 'init'], user='rastisi')
    backup = ['python3', '/usr/local/lib/rastisi-backup/rastisi_backup.py']
    for verb in ['run', 'verify', 'restore-drill', 'status']:
        result = run(backup + [verb], user='rastisi')
        print('PASS installed backup ' + verb, flush=True)
    record = Path('/test/backup-state/last_backup.json')
    assert json.loads(run(['cat', record], user='rastisi-deploy').stdout)['verified']
    assert run(['cat', '/etc/rastisi/test.env'], user='rastisi-deploy', check=False).returncode != 0
    assert json.loads(Path('/test/backup-state/last_restore_drill.json').read_text())['ok']
    assert run(['psql', '-At', '-c', 'SELECT count(*) FROM orders_order'], env=pg_env).stdout.strip() == '300'
    assert run(['psql', '-At', '-c', "SELECT count(*) FROM pg_database WHERE datname LIKE 'rastisi_test_drill_%'"], user='postgres').stdout.strip() == '0'
    print('PASS backup visibility: deploy reads state but cannot read credentials; 300 synthetic orders unchanged; scratch DB removed', flush=True)

    if Path('/opt/test-venv/lib').is_dir():
        # Run a real non-atomic MigrationExecutor operation: SQL commits before
        # failure and no django_migrations recorder row is written.
        script = Path('/repo/deploy/release/selftest/non_atomic_probe.py')
        result = run(['/opt/test-venv/bin/python', script], env=pg_env)
        print(result.stdout, flush=True)

    # Actual non-atomic Django migration: first SQL commits, second fails; the
    # migration recorder has no row. This is why release failure must be UNKNOWN.
    run(['psql', '-v', 'ON_ERROR_STOP=1', '-c', "ALTER ROLE postgres PASSWORD 'local-test-admin'"], user='postgres')
    env = dict(os.environ, RASTISI_TEST_PG_BIN='/usr/lib/postgresql/16/bin', RASTISI_TEST_PG_ADMIN_URL='postgresql://postgres:local-test-admin@127.0.0.1:5432/postgres', RASTISI_TEST_RESTIC='/usr/bin/restic')
    result = run(['/opt/test-venv/bin/python', '-m', 'unittest', 'discover', '-s', '/repo/deploy/backup/tests', '-p', 'test_*.py', '-v'], env=env)
    print(result.stderr, flush=True)
    exercise_laptop_backup()
    exercise_ssh_release(ssh)
    print('PASS isolated installation integration suite', flush=True)


def exercise_laptop_backup():
    """Real pg_dump, pg_restore format check, streamed tar, media SHA256 and SSHSIG receipt.

    The administrator transport is local to the disposable container; forced-command
    deployment SSH is separately exercised above/below. No production service exists here.
    """
    base = Path('/test/laptop-server')
    for name in ('media', 'private_media'):
        (base/'shared'/name).mkdir(parents=True)
    (base/'shared/media/customer.bin').write_bytes(b'local synthetic public media')
    (base/'shared/private_media/private.bin').write_bytes(b'local synthetic private media')
    env = Path('/etc/rastisi/laptop-test.env')
    env.write_text('DATABASE_URL=postgresql://rastisi_app:local-test-app@127.0.0.1/rastisi_test\nDJANGO_MEDIA_ROOT=%s/shared/media\nDJANGO_PRIVATE_MEDIA_ROOT=%s/shared/private_media\n' % (base, base))
    env.chmod(0o600)
    run(['chown', 'rastisi-deploy:rastisi', base])
    (base/'state').mkdir()
    run(['chown', 'rastisi-deploy:rastisi', base/'state'])
    conf = Path('/etc/rastisi/laptop-test.conf')
    conf.write_text('[rastisi]\nbase_dir=%s\napp_user=rastisi\nservice=test-rastisi.service\nenv_file=%s\nport=18082\nbackup_mode=laptop\nbackup_database=rastisi_test\nbackup_allowed_signers=/etc/rastisi/laptop-test-signers\n' % (base, env))
    run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', '/test/laptop-signing'])
    Path('/etc/rastisi/laptop-test-signers').write_text('rastisi-laptop namespaces="rastisi-backup" ' + Path('/test/laptop-signing.pub').read_text())
    transport = Path('/test/admin_transport.py')
    transport.write_text('import shlex,subprocess,sys\nraise SystemExit(subprocess.call(shlex.split(sys.argv[-1])))\n')
    result = run(['python3', '/repo/deploy/release/laptop_backup.py', '--ssh', 'python3 /test/admin_transport.py',
                  '--destination', '/test/laptop-storage', '--from-sha', 'a'*40, '--to-sha', 'b'*40,
                  '--signing-key', '/test/laptop-signing', '--database', 'rastisi_test',
                  '--pg-restore', '/usr/lib/postgresql/16/bin/pg_restore'],
                 env=dict(os.environ, RASTISI_DEPLOY_CONF=str(conf)))
    assert 'LAPTOP_BACKUP_VERIFIED_RECEIPT_IMPORTED files=2' in result.stdout, result.stdout
    assert sorted(p.name for p in (base/'state').iterdir()) == ['laptop-backup.json']
    # Prove a full restore separately; the receipt itself only claims format readability.
    run(['createdb', 'laptop_restore_check'], user='postgres')
    run(['pg_restore', '--no-owner', '--no-privileges', '-d', 'laptop_restore_check', '/test/laptop-storage/database.dump'], user='postgres')
    assert run(['psql', '-At', '-d', 'laptop_restore_check', '-c', 'SELECT count(*) FROM orders_order'], user='postgres').stdout.strip() == '300'
    run(['dropdb', 'laptop_restore_check'], user='postgres')
    print('PASS laptop backup: real dump/restore (300 orders), 2 public/private media hashes, signed receipt; server stores metadata only', flush=True)


def exercise_ssh_release(ssh):
    """Real SSH transport; fake app/systemctl isolate the service boundary."""
    import signal
    sys.path.insert(0, '/repo/deploy/release/tests')
    from harness import make_artifact
    root = Path('/test')
    fake = root/'fake-service'
    fake.mkdir(mode=0o2775, exist_ok=True)
    stubs = root/'stubs'
    shutil.copytree('/repo/deploy/release/tests/stubs', stubs, dirs_exist_ok=True)
    os.chmod(stubs,0o755)
    for file in stubs.iterdir(): os.chmod(file,0o644)
    (fake/'flags.json').write_text(json.dumps({'base':'/srv/rastisi-releases'}))
    (fake/'calls.log').touch()
    os.chmod(fake/'calls.log', 0o664)
    run(['chown','-R','rastisi-deploy:rastisi', fake])
    control, db = root/'control.json', root/'db.json'
    control.write_text('{}')
    db.write_text(json.dumps({'rows':['order-canary'], 'applied':[]}))
    run(['chown','rastisi:rastisi',control,db])
    static = root/'static'
    static.mkdir(exist_ok=True)
    run(['chown','rastisi:rastisi',static])
    with open('/etc/rastisi/test.env','a') as env:
        env.write('DJANGO_DEBUG=False\nDJANGO_SECRET_KEY=local-test-secret-key\nDJANGO_ALLOWED_HOSTS=rastisi.test\nRASTISI_RATE_LIMIT_CACHE_URL=redis://127.0.0.1:6379/0\nDJANGO_STATIC_ROOT=/test/static\nDJANGO_MEDIA_ROOT=/test/media\nSTUB_CONTROL=/test/control.json\nSTUB_DB=/test/db.json\n')
    conf = Path('/etc/rastisi/deploy.conf')
    import configparser
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(conf)
    parser['rastisi'].update({
        'jobs_follow_current':'true', 'edge_probe_enabled':'false',
        'systemctl':'/usr/bin/python3 /test/stubs/fake_systemctl.py /test/fake-service',
        'systemctl_show':'/usr/bin/python3 /test/stubs/fake_systemctl.py /test/fake-service',
        'probe_script':'/test/stubs/stub_probe.py', 'smoke_script':'/test/stubs/stub_smoke.py',
        'proc_root':'/test/fake-service/proc', 'health_timeout':'6',
    })
    with conf.open('w') as fh: parser.write(fh)
    def stage(interrupt=False, **kwargs):
        import hashlib
        import shlex

        art, sha, digest = make_artifact(root, **kwargs)
        if interrupt:
            identity = f'{sha} {digest} {art.stat().st_size}'
            offset = json.loads(run(ssh + ['upload-offset ' + identity]).stdout)
            with art.open('rb') as source:
                chunk = source.read(offset['chunk_size'])
            chunk_hash = hashlib.sha256(chunk).hexdigest()
            aborted = subprocess.run(ssh + [f'upload-chunk {identity} 0 {chunk_hash}'],
                                     input=chunk[:len(chunk) // 2], capture_output=True, timeout=30)
            assert aborted.returncode == 4, aborted.stderr
            assert json.loads(run(ssh + ['upload-offset ' + identity]).stdout)['offset'] == 0
        result = subprocess.run([
            sys.executable, '/repo/deploy/release/remote_client.py', 'stage',
            '--sha', sha, '--sha256', digest, '--artifact', str(art),
        ], env=dict(os.environ, RASTISI_SSH=shlex.join(ssh)), capture_output=True, text=True, timeout=180)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout.strip(), sha

    def start(rid,sha):
        return run(ssh+['activate %s %s -' % (rid,sha)]).stdout.split()[1]
    def finish(run_id):
        for _ in range(100):
            response = run(ssh+['follow %s 0' % run_id]).stdout
            states = [line for line in response.splitlines() if line.startswith('STATE ')]
            if states and states[-1].startswith('STATE done'):
                code = int(states[-1].split()[2])
                if code not in (0,75): print(response,flush=True)
                return code
            time.sleep(0.2)
        raise AssertionError('detached SSH deployment did not finish')
    first,sha = stage(interrupt=True)
    assert finish(start(first,sha)) == 0
    control.write_text(json.dumps({'smoke_delay':5}))
    second,sha2 = stage()
    other,sha3 = stage()
    run_id = start(second,sha2)
    # Kill an actual SSH follower. The detached activation continues.
    follower = subprocess.Popen(ssh+['follow %s 0' % run_id],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    follower.kill()
    follower.communicate()
    competing = start(other,sha3)
    assert finish(competing) == 75
    assert finish(run_id) == 0
    control.write_text('{}')
    result = run(ssh+['rollback previous'])
    assert finish(result.stdout.split()[1]) == 0
    assert json.loads(db.read_text())['rows'] == ['order-canary']
    print('PASS real SSH truncated upload + resumable Stage + disconnect + concurrent activation rejection + rollback preserves synthetic data (service/app are stubs)',flush=True)


if __name__ == '__main__':
    main()
