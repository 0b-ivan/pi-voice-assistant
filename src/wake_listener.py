"""Listens for the wake word while the assistant is idle.

Owns its own arecord process, so it must give the microphone back before a
recording starts: VoiceController calls stop() first. On a detection the
listener stops itself and reports it once through take_detection().
"""
import subprocess
import threading
import time

CHUNK = 2560  # 80 ms of 16 kHz mono int16


class WakeListener:
    def __init__(self, device, detector_factory, popen=subprocess.Popen):
        self.device = device
        self.detector_factory = detector_factory
        self.popen = popen
        self.detector = None
        self.process = None
        self.thread = None
        self.error = None
        self._detected = False
        self._lock = threading.Lock()

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self):
        if self.running:
            return
        self.error = None
        self.thread = threading.Thread(target=self._run, name='wake-listener', daemon=True)
        self.thread.start()

    def _run(self):
        try:
            if self.detector is None:
                self.detector = self.detector_factory()  # loads the ONNX models once
            process = self.popen(
                ['/usr/bin/arecord', '-q', '-D', self.device, '-t', 'raw',
                 '-f', 'S16_LE', '-r', '16000', '-c', '1'],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            with self._lock:
                self.process = process
            while True:
                chunk = process.stdout.read(CHUNK)
                if not chunk:
                    return
                if self.detector.feed(chunk, time.monotonic()):
                    self._detected = True
                    return
        except Exception as exc:  # reported to the controller, never fatal
            self.error = str(exc)
        finally:
            self._kill()

    def _kill(self):
        with self._lock:
            process, self.process = self.process, None
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
            if process.stdout is not None:
                process.stdout.close()

    def stop(self):
        """Free the microphone; returns once arecord is gone."""
        self._kill()
        if self.thread is not None:
            self.thread.join(timeout=2)
            if not self.thread.is_alive():
                self.thread = None

    def take_detection(self):
        detected, self._detected = self._detected, False
        return detected
