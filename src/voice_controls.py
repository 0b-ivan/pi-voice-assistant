"""Nonblocking STT job, owned speech subprocess and WM8960 playback volume."""
import os
import shlex
import signal
import subprocess
import threading


class TranscriptionJob:
    def __init__(self, transcribe, path):
        self.cancelled = False
        self.done = threading.Event()
        self.result = None
        self.error = None
        def run():
            try:
                self.result = transcribe(path)
            except Exception as exc:
                self.error = str(exc)
            finally:
                self.done.set()
        self.thread = threading.Thread(target=run, name='stt', daemon=True)
        self.thread.start()

    def cancel(self):
        # Native Vosk calls cannot be interrupted safely. Keep this job's
        # capture/model exclusively owned until done, and discard its result.
        self.cancelled = True


class SpeechOutput:
    def __init__(self, command):
        self.command = shlex.split(command)
        if not self.command:
            raise ValueError('PTT_SPEAK_COMMAND must not be empty')
        self.process = None

    @property
    def active(self):
        return self.process is not None and self.process.poll() is None

    def start(self, text):
        self.stop()
        self.process = subprocess.Popen(
            [*self.command, text], stdin=subprocess.DEVNULL,
            start_new_session=True)

    def poll(self):
        if self.process is None:
            return None
        code = self.process.poll()
        if code is not None:
            self.process = None
        return code

    def stop(self):
        proc, self.process = self.process, None
        if proc is None:
            return
        # Own process group includes speak.py and its aplay child. Never kill
        # unrelated playback processes by name.
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=0.25)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=1)
        # The leader may terminate before an aplay child that ignores SIGTERM.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def change_volume(direction):
    """One 5 percentage point step of digital Playback; analog/input untouched."""
    subprocess.run([
        '/usr/bin/amixer', '-q', '-c', 'wm8960soundcard',
        'sset', 'Playback', '5%+' if direction > 0 else '5%-'],
        check=True, timeout=2, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
