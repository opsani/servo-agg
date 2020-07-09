import sys
import os
import subprocess
import select
import json
import threading

import typing

class _a(object): pass

# config - FIXME
g_args = _a()
g_args.verbose = False

# global state flags: set when cancel or terminate is requested
g_state = {
    "terminate": False,
    "cancel": False
}
g_children = {} # subprocess.Popen objects, keyed by id(proc)
# critical section for changes in g_state and g_children, used to ensure new sub-processes are
# placed in g_children before waiting on them to exit
g_lock = threading.RLock()

DRIVER_EXIT_TIMEOUT = 3 # max time to wait for driver to exit
# max time to wait for driver to send output; this should be more than the max time between progress updates (30sec)
DRIVER_IO_TIMEOUT = os.environ.get("OPTUNE_IO_TIMEOUT", None)
if DRIVER_IO_TIMEOUT:
    DRIVER_IO_TIMEOUT = max(int(DRIVER_IO_TIMEOUT)-1, 0) # one less, to time out before 'servo' does
if not DRIVER_IO_TIMEOUT:
    DRIVER_IO_TIMEOUT = None # treat undefined, empty or "0" value as 'infinite'

#def run_and_track(driver, app, req = None, describe = False, progress_cb: typing.Callable[..., None] = None):
def run_and_track(path, *args, data = None, progress_cb: typing.Callable[..., None] = None):
    '''
    Execute an external program and read its output line by line, expecting that each line is a valid json object.
    All decoded lines are sent to progress_cb as they arrive. In addition, the object on the last line is returned.
    Parameters:
        cmd    : command to run (passed to subprocess.Popen)
        data   : input data, if not None, sent json-encoded to the program's stdin
        progress_cb: callback function to report progress; if it raises exception, try to abort driver's operation
                Callback is called with the entire output line (decoded as json)
    '''
    # global args

    cmd = [path]
    cmd.extend(args)
    if g_args.verbose:
        print('DRIVER REQUEST:', cmd)

    # test only FIXME@@@
    if progress_cb:
        progress_cb( dict(progress = 0, message = 'starting driver') )

    # prepare stdin in-memory file if a request is provided
    if data is not None:
        stdin = json.dumps(data).encode("UTF-8")   # input descriptor -> json -> bytes
    else:
        stdin = b''         # no stdin

    # execute driver, providing request and capturing stdout/stderr
    with g_lock:
        # don't start anything if canceling or terminating
        if g_state["cancel"]:
            return {"status":"canceled"}
        if g_state["terminate"]:
            return {"status":"terminated"}
        proc = subprocess.Popen(cmd, bufsize=0, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.PIPE)
        #TBD FIXME: if Popen can be interrupted by a signal, this can be re-entered despite the lock (if
        # called from the main thread, because the lock is re-entrant and the signal comes on the main thread)
        # in that case, we have to check the cancel/terminate flags again after proc create and terminate it
        # right away.
        g_children[id(proc)] = proc

    stderr = [] # collect all stderr here
    rsp = {"status": "nodata"} # in case driver outputs nothing at all
    ri = [proc.stdout, proc.stderr]
    wi = [proc.stdin]
    ei = [proc.stdin, proc.stdout,proc.stderr]
    eof_stdout = False
    eof_stderr = False #
    while True:
        if eof_stdout and eof_stderr:
            if proc.poll() is not None: # process exited and no more data
                break
            try:
                proc.wait(DRIVER_EXIT_TIMEOUT) # don't wait forever
            except subprocess.TimeoutExpired:
                print("WARNING: killed stuck child process ({})".format(repr(cmd)), file=sys.stderr)
                proc.kill()
            break
        r,w,e = select.select(ri, wi, ei, DRIVER_IO_TIMEOUT)
        if not r and not w and not e: # timed out
            proc.terminate()
            print("timed out waiting for child process ({}) output".format(repr(cmd)), file=sys.stderr)
            continue # continue waiting until it exits, to handle process termination as any other error
        for h in r:
            if h is proc.stderr:
                l = h.read(4096)
                if not l:
                    eof_stderr = True
                    ri.remove(proc.stderr) # remove from select list (once in EOF, it will always stay 'readable')
                    continue
                stderr.append(l)
            else: # h is proc.stdout
                l = h.readline()
                if not l:
                    eof_stdout = True
                    ri.remove(proc.stdout)
                    continue
                stdout_line = l.strip().decode("UTF-8") # there will always be a complete line, driver writes one line at a time
                if g_args.verbose:
                    print('DRIVER STDOUT:', stdout_line)
                if not stdout_line:
                    continue # ignore blank lines (shouldn't be output, though)
                try:
                    stdout = json.loads(stdout_line)
                except Exception as x:
                    proc.terminate()
                    # TODO: handle exception in json.loads?
                    with g_lock:
                        del g_children[id(proc)]
                    raise
                if stdout:
                    progress_cb(stdout)
                    rsp = stdout
        if w:
            l = min(getattr(select,'PIPE_BUF',512), len(stdin)) # write with select.PIPE_BUF bytes or less should not block
            if not l: # done sending stdin
                proc.stdin.close()
                wi = []
                ei = [proc.stdout,proc.stderr]
            else:
                proc.stdin.write(stdin[:l])
                stdin = stdin[l:]
        # if e:

    with g_lock:
        del g_children[id(proc)]

    rc = proc.returncode
    if g_args.verbose or rc != 0:
        print('\n---driver stderr-----------', file=sys.stderr)
        print( (b"\n".join(stderr)).decode("UTF-8"), file=sys.stderr )  # use accumulated stderr
        print('----------------------\n', file=sys.stderr)

    if g_args.verbose:
        print('DRIVER RESPONSE:', rsp, file=sys.stderr)

    if rc != 0: # error, add verbose info to returned data
        if not rsp.get("status"): # NOTE if driver didn't return any json, status will be "nodata". Preferably, it should print structured data even on failures, so errors can be reported neatly.
            rsp["status"] = "failed"
        if not rsp.get("reason"):
            rsp["reason"] = "unknown"
        m = rsp.get("message", "")
        # if config[report_stderr]:
        rs = os.environ.get("OPTUNE_VERBOSE_STDERR", "all") # FIXME: default setting?
        if rs == "all":
            rsp["message"] = m + "\nstderr:\n" + (b"\n".join(stderr)).decode("UTF-8")
        elif rs == "minimal": # 1st two lines only
            rsp["message"] = m + "\nstderr:\n" + (b"\n".join(stderr[0:2])).decode("UTF-8")
        # else don't send any bit of stderr

    return rsp

def run_and_track_terminate():
    """terminate all processes started by run_and_track()"""

    with g_lock:
        g_state["terminate"] = True
        # take reference of all running procs under lock
        lst = g_children.values()

    # not in lock, g_children can't grow any more once terminate or cancel is set
    for p in lst:
        p.terminate()

def run_and_track_cancel():
    """send SIGUSR1 to all processes started by run_and_track(). Any process that doesn't exit with its own exit message will get {"status":"canceled"} """

    with g_lock:
        g_state["cancel"] = True
        # take reference of all running procs under lock
        lst = g_children.values()

    # not in lock, g_children can't grow any more once terminate or cancel is set
    for p in lst:
        p.send_signal(signal.SIGUSR1)

