"""Orchestrator: запускает _nightly_audit.py батчами по 200 записей, в петле.
Логирует прогресс. Останавливается когда LAST_ID не растёт (=обработали всё).
"""
import os, sys, io, subprocess, time, datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)

BATCH_SIZE = 150
MAX_ITER = 50          # запас на ~50 * 150 = 7500 записей
NEXT_ID_FILE = "_nightly_next_id.txt"
RUNLOG = "_nightly_runlog.txt"


def get_next_id() -> int:
    if not os.path.exists(NEXT_ID_FILE):
        return 0
    try:
        with open(NEXT_ID_FILE) as f:
            return int(f.read().strip() or "0")
    except:
        return 0


def main():
    print(f"=== ORCHESTRATOR start {datetime.datetime.now().isoformat()} ===")
    prev_id = -1
    stall_count = 0
    for i in range(1, MAX_ITER + 1):
        start_id = get_next_id()
        if start_id == prev_id:
            stall_count += 1
            if stall_count >= 2:
                print(f"STALL after {i} iterations (last_id={start_id}). Done.")
                break
        else:
            stall_count = 0
        prev_id = start_id

        env = os.environ.copy()
        env["NIGHTLY_BATCH"] = str(BATCH_SIZE)
        env["NIGHTLY_START_ID"] = str(start_id)
        env["NIGHTLY_DRY"] = "0"

        t0 = time.time()
        print(f"\n=== BATCH {i} (after id={start_id}) at {datetime.datetime.now().strftime('%H:%M:%S')} ===")
        with open(RUNLOG, "a", encoding="utf-8") as logf:
            logf.write(f"\n=== BATCH {i} (after id={start_id}) at {datetime.datetime.now().isoformat()} ===\n")
            proc = subprocess.run(
                ["python", "-u", "_nightly_audit.py"],
                stdout=logf, stderr=subprocess.STDOUT, env=env, timeout=1800)
        elapsed = time.time() - t0
        print(f"  batch finished rc={proc.returncode} elapsed={elapsed:.1f}s")

        # Каждые 5 батчей — деплой если parser_engine.py изменён
        if i % 5 == 0:
            try:
                diff = subprocess.run(["git", "diff", "--quiet", "parser_engine.py"],
                                       capture_output=True)
                if diff.returncode != 0:
                    print(f"  parser_engine.py changed — committing")
                    subprocess.run(["git", "add", "parser_engine.py"], check=True)
                    subprocess.run(["git", "commit", "-m",
                                     f"parser: nightly audit batch {i} updates"], check=True)
                    subprocess.run(["git", "push"], check=True)
            except Exception as e:
                print(f"  git deploy skipped: {e}")

    print(f"\n=== ORCHESTRATOR end {datetime.datetime.now().isoformat()} ===")


if __name__ == "__main__":
    main()
