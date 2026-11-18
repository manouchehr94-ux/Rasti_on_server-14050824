#!/usr/bin/env python3
"""The GitHub-runner side of a deployment (standard library only). Talks to rastisi_gate.py over SSH and survives a flaky connection.

    RASTISI_SSH="ssh -i key -o StrictHostKeyChecking=yes ... deploy@host" remote_client.py <command> [...]

    status | version
    stage    --sha S --sha256 D --artifact FILE                   prints the release id
    activate --release R --sha S --from-sha F [--allow-migrations] [--destructive-ack P] [--dry-run]
    rollback [--to previous|ID] [--unsafe-ack P]
    follow   --run RUN_ID

Exit codes are the server's (0 ok, 1 rolled back safely, 2 needs a human, 3 migration failed, 4 refused by policy, 75 busy) plus
10 = could not talk to the server for too long (the deployment may still be running there: use 'follow' / 'status' later).

Connection loss is normal here: every call is retried; a run is started with a client-chosen id so a repeated request resumes the same
run instead of starting another; the log is read with an offset so nothing is lost or repeated.
"""

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
import time

EXIT_LOST = 10
MESSAGES = {
    0: "OK: the release is serving and verified.",
    1: "ROLLED BACK: the new release failed verification and the previous release is serving again (nothing for the database to undo).",
    2: "NEEDS A HUMAN: the system was left in a state that needs a person (see state/INCIDENT on the server). No further automatic action was taken.",
    3: "MIGRATION FAILED: the release was not activated; the previous release is still serving.",
    4: "REFUSED BY POLICY: nothing was changed (see the log for the rule).",
    75: "BUSY: another deployment is running; nothing was changed.",
    EXIT_LOST: "LOST CONTACT with the server for too long. The deployment may still be running; check the server status before doing anything else.",
}


def ssh_base():
    raw = os.environ.get("RASTISI_SSH", "")
    if not raw:
        raise SystemExit("RASTISI_SSH is not set")
    return shlex.split(raw)


def call(command, stdin_path=None, timeout=900, data=None):
    """One SSH call. Returns (returncode, stdout, stderr). 255 = the connection itself failed."""
    cmd = ssh_base() + [command]
    stdin = open(stdin_path, "rb") if stdin_path else (subprocess.DEVNULL if data is None else None)
    try:
        proc = subprocess.run(cmd, stdin=stdin, input=data, capture_output=True, timeout=timeout)
        return proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return 255, "", "ssh timed out"
    finally:
        if stdin_path:
            stdin.close()


def call_retry(command, stdin_path=None, attempts=6, delay=None, timeout=900, data=None):
    delay = float(os.environ.get("RASTISI_RETRY_DELAY", "10")) if delay is None else delay
    last = (255, "", "")
    for n in range(attempts):
        last = call(command, stdin_path, timeout, data=data)
        if last[0] != 255:
            return last
        sys.stderr.write("connection problem (attempt %d/%d): %s\n" % (n + 1, attempts, last[2].strip()[-200:]))
        time.sleep(delay)
    return last


def new_run_id():
    return "%s-%s" % (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()), os.urandom(4).hex())


def follow(run_id, give_up_after=1800, poll=3.0, log=None):
    if log is None:
        log = sys.stdout
    """Print the run's log until it is done. Reconnects as often as needed; gives up only after ``give_up_after`` s without contact."""
    offset = 0
    last_contact = time.time()
    while True:
        rc, out, err = call("follow %s %d" % (run_id, offset), timeout=120)
        if rc == 255:
            if time.time() - last_contact > give_up_after:
                return EXIT_LOST
            time.sleep(float(os.environ.get("RASTISI_RETRY_DELAY", "10")))
            continue
        last_contact = time.time()
        if rc != 0:
            sys.stderr.write("server refused: %s\n" % err.strip()[-300:])
            return 4
        lines = out.splitlines()
        state = next((l for l in reversed(lines) if l.startswith("STATE ")), "")
        new_offset = next((int(l.split()[1]) for l in reversed(lines) if l.startswith("OFFSET ")), offset)
        text = "\n".join(l for l in lines if not l.startswith(("OFFSET ", "STATE ")))
        if text.strip():
            print(text, file=log, flush=True)
        offset = new_offset
        if state.startswith("STATE done"):
            return int(state.split()[2])
        if state == "STATE died":
            print("the deployment process on the server died without reporting a result", file=log)
            return 2
        time.sleep(poll)


def cmd_status(args):
    rc, out, err = call_retry("status")
    print(out.strip() or err.strip())
    return rc


def cmd_version(args):
    rc, out, err = call_retry("version")
    print(out.strip() or err.strip())
    return rc


def stage_error(rc, out, err):
    print((err or out).strip()[-800:], file=sys.stderr)
    return EXIT_LOST if rc == 255 else rc


def cmd_stage(args):
    with open(args.artifact, "rb") as artifact:
        digest = hashlib.file_digest(artifact, "sha256").hexdigest()
        if args.sha256 and digest != args.sha256:
            print("local artifact checksum does not match the build", file=sys.stderr)
            return 4
        size = os.fstat(artifact.fileno()).st_size
        identity = "%s %s %d" % (args.sha, digest, size)
        rc, response, err = call_retry("upload-offset " + identity, timeout=30)
        if rc:
            return stage_error(rc, response, err)
        checkpoint = json.loads(response)
        offset, chunk_size = checkpoint["offset"], checkpoint["chunk_size"]
        if not 0 <= offset <= size or not 0 < chunk_size <= 2 * 1024 * 1024:
            print("invalid server checkpoint", file=sys.stderr)
            return 4
        print("artifact checkpoint: %d/%d bytes" % (offset, size), file=sys.stderr)
        while offset < size:
            artifact.seek(offset)
            data = artifact.read(min(chunk_size, size - offset))
            command = "upload-chunk %s %d %s" % (identity, offset, hashlib.sha256(data).hexdigest())
            rc, response, err = call_retry(command, timeout=90, data=data)
            if rc:
                return stage_error(rc, response, err)
            committed = json.loads(response)["offset"]
            if not offset + len(data) <= committed <= size or committed <= offset:
                print("invalid committed offset", file=sys.stderr)
                return 4
            offset = committed
            print("uploaded %d/%d bytes" % (offset, size), file=sys.stderr)
    rc, response, err = call_retry("stage-upload " + identity, timeout=120)
    if rc:
        return stage_error(rc, response, err)
    run_id = response.strip().removeprefix("RUN ")
    print("staging run %s; use follow to inspect it after disconnect" % run_id, file=sys.stderr)
    result = follow(run_id, log=sys.stderr)
    if result:
        return result
    rc, response, err = call_retry("result " + run_id, timeout=30)
    if rc:
        return stage_error(rc, response, err)
    print(json.loads(response)["release"])
    return 0


def start_and_follow(command, run_id=None):
    run_id = run_id or new_run_id()
    print("requesting run %s; keep this id to reconnect" % run_id, flush=True)
    rc, out, err = call_retry("%s run=%s" % (command, run_id))
    if rc == 255:
        return EXIT_LOST
    if rc != 0:
        sys.stderr.write("could not start: %s\n" % err.strip()[-500:])
        return 4
    started = next((l.split()[1] for l in out.splitlines() if l.startswith("RUN ")), "")
    if started != run_id:
        sys.stderr.write("unexpected run id from the server: %r\n" % started)
        return 2
    print("run %s started on the server; following it (safe to lose the connection)" % run_id)
    code = follow(run_id)
    print("\nRESULT: " + MESSAGES.get(code, "exit code %s" % code))
    return code


def cmd_activate(args):
    parts = ["activate", args.release, args.sha, args.from_sha or "-"]
    if args.allow_migrations:
        parts.append("allow-migrations")
    if args.destructive_ack:
        parts.append("destructive-ack=" + args.destructive_ack)
    if args.dry_run:
        parts.append("dry-run")
    return start_and_follow(" ".join(parts), args.run)


def cmd_rollback(args):
    parts = ["rollback", args.to]
    if args.unsafe_ack:
        parts.append("unsafe-ack=" + args.unsafe_ack)
    return start_and_follow(" ".join(parts))


def cmd_follow(args):
    code = follow(args.run)
    print("\nRESULT: " + MESSAGES.get(code, "exit code %s" % code))
    return code


def cmd_backup_receipt(args):
    rc, out, err = call_retry("backup-receipt", stdin_path=args.receipt, timeout=60)
    print((out or err).strip())
    return rc


def main(argv=None):
    ap = argparse.ArgumentParser(prog="remote_client")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("backup-receipt")
    p.add_argument("--receipt", required=True)
    sub.add_parser("status")
    sub.add_parser("version")
    p = sub.add_parser("stage")
    p.add_argument("--sha", required=True)
    p.add_argument("--sha256", default="")
    p.add_argument("--artifact", required=True)
    p = sub.add_parser("activate")
    p.add_argument("--release", required=True)
    p.add_argument("--sha", required=True)
    p.add_argument("--from-sha", default="")
    p.add_argument("--allow-migrations", action="store_true")
    p.add_argument("--destructive-ack", default="")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--run", default="")
    p = sub.add_parser("rollback")
    p.add_argument("--to", default="previous")
    p.add_argument("--unsafe-ack", default="")
    p = sub.add_parser("follow")
    p.add_argument("--run", required=True)
    args = ap.parse_args(argv)
    return {"backup-receipt": cmd_backup_receipt, "status": cmd_status, "version": cmd_version, "stage": cmd_stage, "activate": cmd_activate, "rollback": cmd_rollback, "follow": cmd_follow}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
