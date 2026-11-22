"""Keep audio running smoothly while other apps are in the foreground (Windows only, best effort)."""
import ctypes
import sys
import threading

_local = threading.local()


def boost_process():
    """Called once at startup.

    Without this, Windows treats MultiSync as a background app as soon as another window is focused:
    its threads get less CPU, its timers get coarser (time.sleep(0.004) becomes ~16 ms) and power
    throttling (EcoQoS) can kick in, so the audio callbacks miss their deadlines and the music stutters.
    """
    # Python hands the GIL to another thread only every 5 ms by default; audio callbacks need it sooner.
    sys.setswitchinterval(0.001)
    if sys.platform != "win32":
        return
    k32 = ctypes.WinDLL("kernel32")  # own instance: argtypes below must not leak into other libraries
    # Without these the 64-bit pseudo-handle (-1) is truncated to 32 bits and every call fails.
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    k32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    k32.SetProcessInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
    try:
        k32.SetPriorityClass(k32.GetCurrentProcess(), 0x00000080)  # HIGH_PRIORITY_CLASS
    except Exception:
        pass
    try:
        ctypes.windll.winmm.timeBeginPeriod(1)
    except Exception:
        pass
    try:
        # PROCESS_POWER_THROTTLING_STATE: opt out of EcoQoS and of timer-resolution throttling.
        class State(ctypes.Structure):
            _fields_ = [("Version", ctypes.c_ulong), ("ControlMask", ctypes.c_ulong),
                        ("StateMask", ctypes.c_ulong)]

        st = State(1, 0x1 | 0x4, 0)  # EXECUTION_SPEED | IGNORE_TIMER_RESOLUTION, both off
        k32.SetProcessInformation(k32.GetCurrentProcess(), 4,  # ProcessPowerThrottling
                                  ctypes.byref(st), ctypes.sizeof(st))
    except Exception:
        pass


def boost_audio_thread():
    """Register the calling thread with MMCSS ("Pro Audio"), like other Windows audio apps do."""
    if sys.platform != "win32" or getattr(_local, "done", False):
        return
    _local.done = True
    try:
        task = ctypes.c_ulong(0)
        ctypes.windll.avrt.AvSetMmThreadCharacteristicsW("Pro Audio", ctypes.byref(task))
    except Exception:
        pass
