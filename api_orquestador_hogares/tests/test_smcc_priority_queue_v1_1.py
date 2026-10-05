import threading, time
from app.queue.manager import QueueJobTimeout, SmccPriorityQueueManager

def test_max_concurrency():
    m = SmccPriorityQueueManager(max_workers=3,name="test-concurrency")
    lock=threading.Lock(); active=0; maximum=0; results=[]; errors=[]
    def job(v):
        nonlocal active, maximum
        with lock: active+=1; maximum=max(maximum,active)
        time.sleep(0.20)
        with lock: active-=1
        return v
    def submit(v):
        try: results.append(m.submit_and_wait(job_id=f"job-{v}",case_id=str(v),source="SMCC",priority=10,queue_timeout_seconds=5,fn=lambda:job(v)))
        except Exception as e: errors.append(e)
    ts=[threading.Thread(target=submit,args=(i,)) for i in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert not errors, errors
    assert len(results)==8 and maximum==3

def test_priority():
    m=SmccPriorityQueueManager(max_workers=1,name="test-priority"); gate=threading.Event(); order=[]
    blocker=threading.Thread(target=lambda:m.submit_and_wait(job_id="blocker",case_id="1",source="TEST",priority=1,queue_timeout_seconds=5,fn=lambda:(order.append("BLOCKER"),gate.wait(timeout=5),"BLOCKER")[-1]))
    blocker.start(); deadline=time.time()+3
    while m.status()["active"]!=1:
        if time.time()>deadline: raise RuntimeError("worker no inicio blocker")
        time.sleep(.02)
    results={}
    def submit(name,p): results[name]=m.submit_and_wait(job_id=name,case_id=name,source="TEST",priority=p,queue_timeout_seconds=5,fn=lambda:(order.append(name) or name))
    low=threading.Thread(target=submit,args=("LOW",50)); high=threading.Thread(target=submit,args=("HIGH",10))
    low.start(); time.sleep(.05); high.start(); time.sleep(.10); gate.set(); blocker.join(); low.join(); high.join()
    assert order==["BLOCKER","HIGH","LOW"], order

def test_timeout():
    m=SmccPriorityQueueManager(max_workers=1,name="test-timeout"); gate=threading.Event(); executed=[]
    blocker=threading.Thread(target=lambda:m.submit_and_wait(job_id="blocking",case_id="1",source="TEST",priority=1,queue_timeout_seconds=10,fn=lambda:gate.wait(timeout=5)))
    blocker.start(); deadline=time.time()+3
    while m.status()["active"]!=1:
        if time.time()>deadline: raise RuntimeError("worker no inicio")
        time.sleep(.02)
    try:
        m.submit_and_wait(job_id="timeout-job",case_id="2",source="TEST",priority=10,queue_timeout_seconds=.20,fn=lambda:executed.append(True))
        raise AssertionError("esperaba QueueJobTimeout")
    except QueueJobTimeout: pass
    gate.set(); blocker.join(); time.sleep(.20)
    assert executed==[] and m.status()["timeouts"]>=1

if __name__=="__main__":
    test_max_concurrency(); test_priority(); test_timeout(); print("TEST_SMCC_PRIORITY_QUEUE_V1_1=PASS")
