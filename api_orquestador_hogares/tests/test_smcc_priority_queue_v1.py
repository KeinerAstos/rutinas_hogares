import threading
import time

from app.queue.manager import (
    QueueJobTimeout,
    SmccPriorityQueueManager,
)


def test_max_concurrency():

    manager = SmccPriorityQueueManager(
        max_workers=3,
        name="test-concurrency",
    )

    lock = threading.Lock()

    active = 0
    max_active = 0

    results = []
    errors = []

    def job(value):

        nonlocal active
        nonlocal max_active

        with lock:
            active += 1
            max_active = max(
                max_active,
                active,
            )

        time.sleep(0.20)

        with lock:
            active -= 1

        return value

    def submit(value):

        try:
            result = (
                manager.submit_and_wait(
                    job_id=f"job-{value}",
                    case_id=str(value),
                    source="SMCC",
                    priority=10,
                    queue_timeout_seconds=5,
                    fn=lambda: job(value),
                )
            )

            results.append(result)

        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(
            target=submit,
            args=(index,),
        )
        for index in range(8)
    ]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join()

    assert not errors, errors
    assert len(results) == 8
    assert max_active <= 3
    assert max_active == 3


def test_priority():

    manager = SmccPriorityQueueManager(
        max_workers=1,
        name="test-priority",
    )

    gate = threading.Event()

    execution_order = []

    def blocking_job():

        execution_order.append(
            "BLOCKER"
        )

        gate.wait(
            timeout=5
        )

        return "BLOCKER"

    blocker_thread = threading.Thread(
        target=lambda:
            manager.submit_and_wait(
                job_id="blocker",
                case_id="1",
                source="TEST",
                priority=1,
                queue_timeout_seconds=5,
                fn=blocking_job,
            )
    )

    blocker_thread.start()

    deadline = time.time() + 3

    while (
        manager.status()["active"]
        != 1
    ):
        if time.time() > deadline:
            raise RuntimeError(
                "worker no inicio blocker"
            )

        time.sleep(0.02)

    results = {}

    def submit(
        name,
        priority,
    ):
        results[name] = (
            manager.submit_and_wait(
                job_id=name,
                case_id=name,
                source="TEST",
                priority=priority,
                queue_timeout_seconds=5,
                fn=lambda: (
                    execution_order.append(
                        name
                    )
                    or name
                ),
            )
        )

    low = threading.Thread(
        target=submit,
        args=(
            "LOW",
            50,
        ),
    )

    high = threading.Thread(
        target=submit,
        args=(
            "HIGH",
            10,
        ),
    )

    low.start()
    time.sleep(0.05)
    high.start()

    time.sleep(0.10)

    gate.set()

    blocker_thread.join()
    low.join()
    high.join()

    assert execution_order == [
        "BLOCKER",
        "HIGH",
        "LOW",
    ], execution_order


def test_queue_timeout():

    manager = SmccPriorityQueueManager(
        max_workers=1,
        name="test-timeout",
    )

    gate = threading.Event()

    blocker = threading.Thread(
        target=lambda:
            manager.submit_and_wait(
                job_id="blocking",
                case_id="1",
                source="TEST",
                priority=1,
                queue_timeout_seconds=10,
                fn=lambda: gate.wait(
                    timeout=5
                ),
            )
    )

    blocker.start()

    deadline = time.time() + 3

    while (
        manager.status()["active"]
        != 1
    ):
        if time.time() > deadline:
            raise RuntimeError(
                "worker no inicio"
            )

        time.sleep(0.02)

    executed = []

    try:

        manager.submit_and_wait(
            job_id="timeout-job",
            case_id="2",
            source="TEST",
            priority=10,
            queue_timeout_seconds=0.20,
            fn=lambda: executed.append(
                True
            ),
        )

        raise AssertionError(
            "esperaba QueueJobTimeout"
        )

    except QueueJobTimeout:
        pass

    gate.set()
    blocker.join()

    time.sleep(0.20)

    assert executed == []

    status = manager.status()

    assert status["timeouts"] >= 1


if __name__ == "__main__":

    test_max_concurrency()
    test_priority()
    test_queue_timeout()

    print(
        "TEST_SMCC_PRIORITY_QUEUE_V1=PASS"
    )
