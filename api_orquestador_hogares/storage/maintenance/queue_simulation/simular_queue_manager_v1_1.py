import threading
import time
import json

from app.queue.manager import (
    QueueJobTimeout,
    SmccPriorityQueueManager,
)


def pretty(label, data):
    print()
    print("=" * 100)
    print(label)
    print("=" * 100)
    print(json.dumps(data, indent=2, ensure_ascii=False))


# ==============================================================================================
# PRUEBA 1
# 3 ACTIVOS + 2 EN COLA
# ==============================================================================================

manager = SmccPriorityQueueManager(
    max_workers=3,
    name="simulation-concurrency",
)

release_gate = threading.Event()
lock = threading.Lock()

active_counter = 0
max_active_seen = 0
execution_started = []
execution_finished = []
results = []
errors = []


def simulated_job(name):
    global active_counter
    global max_active_seen

    with lock:
        active_counter += 1
        max_active_seen = max(
            max_active_seen,
            active_counter,
        )
        execution_started.append(name)

    release_gate.wait(timeout=10)

    time.sleep(0.10)

    with lock:
        execution_finished.append(name)
        active_counter -= 1

    return name


def submit_job(name):
    try:
        result = manager.submit_and_wait(
            job_id=f"sim-{name}",
            case_id=f"case-{name}",
            source="SMCC",
            priority=10,
            queue_timeout_seconds=5,
            fn=lambda: simulated_job(name),
        )
        results.append(result)

    except Exception as exc:
        errors.append(
            f"{name}:{type(exc).__name__}:{exc}"
        )


threads = [
    threading.Thread(
        target=submit_job,
        args=(f"JOB_{index}",),
        daemon=True,
    )
    for index in range(1, 6)
]

for thread in threads:
    thread.start()
    time.sleep(0.03)


deadline = time.time() + 5

while time.time() < deadline:
    status = manager.status()

    if (
        status["active"] == 3
        and status["queued"] == 2
    ):
        break

    time.sleep(0.05)


status_blocked = manager.status()

pretty(
    "SIMULACION_1_ESTADO_CON_3_ACTIVOS_Y_2_EN_COLA",
    status_blocked,
)

assert status_blocked["active"] == 3, status_blocked
assert status_blocked["queued"] == 2, status_blocked
assert status_blocked["available"] == 0, status_blocked

print()
print("SIMULACION_1=PASS_3_ACTIVOS_2_EN_COLA")

release_gate.set()

for thread in threads:
    thread.join(timeout=10)

status_finished = manager.status()

pretty(
    "SIMULACION_1_ESTADO_FINAL",
    status_finished,
)

assert len(errors) == 0, errors
assert len(results) == 5, results
assert max_active_seen == 3, max_active_seen
assert status_finished["active"] == 0
assert status_finished["queued"] == 0
assert status_finished["completed"] == 5

print()
print("SIMULACION_1_FINAL=PASS")
print("MAX_ACTIVE_SEEN=", max_active_seen)
print("STARTED=", execution_started)
print("FINISHED=", execution_finished)


# ==============================================================================================
# PRUEBA 2
# PRIORIDAD ALTA ANTES QUE BAJA
# ==============================================================================================

priority_manager = SmccPriorityQueueManager(
    max_workers=1,
    name="simulation-priority",
)

priority_gate = threading.Event()
priority_order = []
priority_results = {}


def blocker_job():
    priority_order.append("BLOCKER")
    priority_gate.wait(timeout=10)
    return "BLOCKER"


blocker_thread = threading.Thread(
    target=lambda: priority_manager.submit_and_wait(
        job_id="blocker",
        case_id="blocker",
        source="TEST",
        priority=1,
        queue_timeout_seconds=5,
        fn=blocker_job,
    )
)

blocker_thread.start()


deadline = time.time() + 5

while time.time() < deadline:
    if priority_manager.status()["active"] == 1:
        break
    time.sleep(0.05)


def submit_priority(name, priority):
    priority_results[name] = (
        priority_manager.submit_and_wait(
            job_id=name,
            case_id=name,
            source="TEST",
            priority=priority,
            queue_timeout_seconds=5,
            fn=lambda: (
                priority_order.append(name)
                or name
            ),
        )
    )


low_thread = threading.Thread(
    target=submit_priority,
    args=("LOW_PRIORITY_50", 50),
)

high_thread = threading.Thread(
    target=submit_priority,
    args=("HIGH_PRIORITY_10", 10),
)

low_thread.start()
time.sleep(0.05)
high_thread.start()

time.sleep(0.20)

status_priority_waiting = priority_manager.status()

pretty(
    "SIMULACION_2_PRIORIDADES_EN_COLA",
    status_priority_waiting,
)

assert status_priority_waiting["active"] == 1
assert status_priority_waiting["queued"] == 2

priority_gate.set()

blocker_thread.join(timeout=10)
low_thread.join(timeout=10)
high_thread.join(timeout=10)

print()
print("PRIORITY_ORDER=", priority_order)

assert priority_order == [
    "BLOCKER",
    "HIGH_PRIORITY_10",
    "LOW_PRIORITY_50",
], priority_order

print("SIMULACION_2=PASS_PRIORIDAD")


# ==============================================================================================
# PRUEBA 3
# TIMEOUT MIENTRAS SIGUE QUEUED
# ==============================================================================================

timeout_manager = SmccPriorityQueueManager(
    max_workers=1,
    name="simulation-timeout",
)

timeout_gate = threading.Event()
timeout_job_executed = []


blocker_timeout_thread = threading.Thread(
    target=lambda: timeout_manager.submit_and_wait(
        job_id="timeout-blocker",
        case_id="timeout-blocker",
        source="TEST",
        priority=1,
        queue_timeout_seconds=5,
        fn=lambda: timeout_gate.wait(timeout=10),
    )
)

blocker_timeout_thread.start()


deadline = time.time() + 5

while time.time() < deadline:
    if timeout_manager.status()["active"] == 1:
        break
    time.sleep(0.05)


timeout_detected = False

try:
    timeout_manager.submit_and_wait(
        job_id="job-que-debe-expirar",
        case_id="timeout-case",
        source="SMCC",
        priority=10,
        queue_timeout_seconds=0.30,
        fn=lambda: timeout_job_executed.append(True),
    )

except QueueJobTimeout:
    timeout_detected = True


assert timeout_detected is True
assert timeout_job_executed == []

status_timeout = timeout_manager.status()

pretty(
    "SIMULACION_3_TIMEOUT",
    status_timeout,
)

assert status_timeout["timeouts"] == 1

timeout_gate.set()
blocker_timeout_thread.join(timeout=10)

time.sleep(0.20)

assert timeout_job_executed == []

print()
print("SIMULACION_3=PASS_TIMEOUT")


# ==============================================================================================
# RESULTADO GENERAL
# ==============================================================================================

print()
print("=" * 100)
print("RESULTADO_SIMULACION=PASS")
print("=" * 100)
print("CONCURRENCIA_MAXIMA=3")
print("COLA_CONFIRMADA=SI")
print("PRIORIDAD_CONFIRMADA=SI")
print("TIMEOUT_CONFIRMADO=SI")
print("TRABAJOS_REALES_SMCC=0")
print("SERVICIOS_EXTERNOS_CONSUMIDOS=0")
print("=" * 100)
