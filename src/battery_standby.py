"""Supervise the privileged, opt-in battery standby systemd unit.

The unprivileged voice service never writes cpufreq or Bluetooth/WLAN sysfs.
This worker runs systemctl off the GPIO/I2C event loop; a strict Polkit rule
allows only start/stop of the specific battery-standby unit.
"""
import subprocess
import threading


UNIT = 'proximus-battery-standby.service'


class Supervisor:
    def __init__(self, report=None, run=subprocess.run):
        self.report = report
        self.run = run
        self.desired = False
        self.applied = False
        self.enabled = True
        self._wakeup = threading.Event()
        self._lock = threading.Lock()
        self._closed = False
        self._thread = threading.Thread(target=self._loop, name='battery-standby',
                                        daemon=True)

    def start(self):
        self._thread.start()
        return self

    def request(self, desired):
        with self._lock:
            if self._closed:
                return
            new = bool(desired)
            if new != self.desired:
                self.desired = new
                self._wakeup.set()

    def _call(self, op):
        try:
            completed = self.run(
                ['/usr/bin/systemctl', op, UNIT], check=False, timeout=12,
                capture_output=True, text=True)
        except (OSError, subprocess.SubprocessError) as exc:
            self._emit('battery_standby_error', operation=op, message=str(exc))
            return False
        if completed.returncode:
            # No stdout/stderr: keep potentially sensitive diagnostics out of logs.
            self._emit('battery_standby_error', operation=op,
                       returncode=completed.returncode)
            return False
        self._emit('battery_standby_policy', operation=op)
        return True

    def _emit(self, name, **fields):
        if self.report:
            self.report(name, **fields)

    def _loop(self):
        while True:
            with self._lock:
                desired, applied, closed = self.desired, self.applied, self._closed
            if closed and not applied:
                return
            if desired != applied:
                op = 'start' if desired else 'stop'
                if self._call(op):
                    with self._lock:
                        self.applied = desired
                    continue
                self._wakeup.wait(5)          # retry a failed systemctl call
            else:
                self._wakeup.wait()           # nothing to do: sleep until asked
            self._wakeup.clear()

    def close(self):
        # The unit is also BindsTo=pi-ptt.service; systemd stops and restores
        # it if the process is killed before this worker can finish.
        with self._lock:
            self._closed = True
            self.desired = False
        self._wakeup.set()
        self._thread.join(timeout=2)
