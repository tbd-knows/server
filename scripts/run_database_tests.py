"""Run the whole test suite against a fresh, local PostgreSQL cluster over TCP."""

import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile


def main():
    for tool in ("initdb", "pg_ctl", "createdb"):
        if not shutil.which(tool):
            raise SystemExit(f"{tool} is required; install PostgreSQL locally")
    with tempfile.TemporaryDirectory(prefix="tbd14-postgres-") as temporary:
        root = Path(temporary)
        data, log = root / "data", root / "postgres.log"
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        subprocess.run(
            ["initdb", "-D", str(data), "-A", "trust", "-U", "postgres"], check=True, stdout=subprocess.DEVNULL
        )
        subprocess.run(
            ["pg_ctl", "-D", str(data), "-l", str(log), "-o", f"-h 127.0.0.1 -p {port} -k {temporary}", "start"],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        try:
            subprocess.run(
                ["createdb", "-h", "127.0.0.1", "-p", str(port), "-U", "postgres", "tbd_user_test_acceptance"],
                check=True,
            )
            env = {
                **os.environ,
                "TEST_DATABASE_URL": f"postgresql://postgres@127.0.0.1:{port}/tbd_user_test_acceptance",
            }
            return subprocess.run([sys.executable, "-m", "pytest", *sys.argv[1:]], env=env).returncode
        finally:
            subprocess.run(["pg_ctl", "-D", str(data), "-m", "fast", "stop"], check=True, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    raise SystemExit(main())
