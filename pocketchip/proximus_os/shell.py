"""Native 480x272 Home shell. Run: PYTHONPATH=pocketchip python3 -m proximus_os.shell."""
import argparse
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import tkinter as tk
from tkinter import messagebox

from .core import Archive, local_command, system_status, wifi_status
from .transport import ClientConfig, TextClient

BG, PANEL, FG, DIM, ACCENT, ERROR = '#121713', '#232a25', '#e7dfc7', '#adb49e', '#e0b563', '#e79a83'
MODULES = [('Vox', 'Textkanal · CT107'), ('Homelab', 'Verbindung prüfen'),
           ('Archive', 'Notizen · lokal'), ('Befehle', 'Uhr · Rechner'),
           ('Einstellungen', 'Netz · Zugang'), ('Werkzeuge', 'Akku · System')]


class Shell:
    def __init__(self, root, data, config, fullscreen=False):
        self.root, self.config_path = root, config
        self.archive = Archive(data)
        self.page, self.busy = 'Home', False
        self.conversation = 'TEXTMODUS / kein Audio\nNachrichten gehen einzeln an CT107. Kein Gesprächsgedächtnis.\n'
        self.events = queue.Queue(maxsize=2)
        root.title('Proximus OS')
        root.geometry('480x272')
        root.resizable(False, False)
        root.configure(bg=BG)
        if fullscreen:
            root.attributes('-fullscreen', True)
        root.option_add('*Font', ('DejaVu Sans', 10))
        root.option_add('*Background', PANEL)
        root.option_add('*Foreground', FG)
        root.option_add('*insertBackground', ACCENT)
        root.option_add('*selectBackground', '#65502b')
        self.header = tk.Frame(root, bg=BG, height=38)
        self.header.pack(fill='x')
        self.header.pack_propagate(False)
        self.home = self.button(self.header, '⌂ HOME', self.show_home, width=9)
        self.home.pack(side='left', fill='y')
        self.heading = tk.Label(self.header, text='PROXIMUS / OS', bg=BG, fg=ACCENT,
                                font=('DejaVu Sans', 12, 'bold'))
        self.heading.pack(side='left', padx=8)
        self.body = tk.Frame(root, bg=BG)
        self.body.pack(fill='both', expand=True, padx=8, pady=4)
        self.footer = tk.Label(root, text='LOKAL BEREIT  ·  1–6 Module  ·  Esc Home',
                               anchor='w', bg=BG, fg=DIM, font=('DejaVu Sans', 9))
        self.footer.pack(fill='x', padx=8, pady=(0, 3))
        root.bind('<Escape>', lambda event: self.show_home())
        root.bind('<Key>', self.key)
        root.protocol('WM_DELETE_WINDOW', root.destroy)
        self.show_home()
        root.after(100, self.poll)

    def button(self, parent, text, command, **kwargs):
        return tk.Button(parent, text=text, command=command, bg=PANEL, fg=FG,
                         activebackground='#4b4430', activeforeground=FG,
                         highlightcolor=ACCENT, highlightbackground=PANEL,
                         highlightthickness=1, relief='flat', borderwidth=0,
                         takefocus=True, **kwargs)

    def key(self, event):
        if self.page == 'Home' and event.char in '123456' and event.char:
            self.open(MODULES[int(event.char) - 1][0])

    def clear(self, title):
        self.page = title
        self.heading.configure(text='PROXIMUS / ' + title.upper())
        for child in self.body.winfo_children():
            child.destroy()

    def show_home(self):
        self.clear('Home')
        for i, (title, subtitle) in enumerate(MODULES):
            button = self.button(self.body, f'{i + 1:02}  {title}\n{subtitle}',
                                 lambda name=title: self.open(name), anchor='w', padx=10,
                                 font=('DejaVu Sans', 10))
            button.grid(row=i // 3, column=i % 3, sticky='nsew', padx=3, pady=3)
        for i in range(3):
            self.body.columnconfigure(i, weight=1, uniform='module')
        for i in range(2):
            self.body.rowconfigure(i, weight=1, uniform='module')
        self.body.winfo_children()[0].focus_set()

    def text_area(self, text):
        frame = tk.Frame(self.body, bg=BG)
        frame.pack(fill='both', expand=True)
        scroll = tk.Scrollbar(frame)
        scroll.pack(side='right', fill='y')
        area = tk.Text(frame, wrap='word', height=3, bg=PANEL, fg=FG, relief='flat',
                       padx=7, pady=4, yscrollcommand=scroll.set,
                       font=('DejaVu Sans', 10), takefocus=True)
        area.pack(fill='both', expand=True)
        scroll.configure(command=area.yview)
        area.insert('end', text)
        area.configure(state='disabled')
        return area

    def input_row(self, callback, label):
        frame = tk.Frame(self.body, bg=BG, height=42)
        frame.pack(side='bottom', fill='x', pady=(4, 0))
        frame.pack_propagate(False)
        entry = tk.Entry(frame, relief='flat', bg=PANEL)
        entry.pack(side='left', fill='both', expand=True, padx=(0, 4))
        button = self.button(frame, label, lambda: callback(entry), width=10)
        button.pack(side='right', fill='y')
        entry.bind('<Return>', lambda event: callback(entry))
        entry.focus_set()
        return entry, button

    def open(self, name):
        self.clear(name)
        if name == 'Vox':
            self.input_row(self.send, 'Senden')
            self.chat_area = self.text_area(self.conversation)
            self.chat_area.see('end')
        elif name == 'Homelab':
            bar = tk.Frame(self.body, bg=BG, height=42)
            bar.pack(side='bottom', fill='x', pady=(4, 0))
            bar.pack_propagate(False)
            self.button(bar, 'CT107 prüfen', self.check_ct).pack(fill='both')
            self.status_area = self.text_area('CT107 / Textkanal\nNoch nicht geprüft.\n'
                'Kein Zugriff auf Proxmox-API oder andere Homelab-Dienste im MVP.')
        elif name == 'Archive':
            self.input_row(self.save_note, 'Speichern')
            try:
                text = '\n\n'.join(n['time'] + '\n' + n['text'] for n in self.archive.notes())
                self.text_area(text or 'Lokales Archiv leer. Neue Notiz unten eingeben.')
            except (ValueError, OSError) as exc:
                self.text_area(str(exc))
        elif name == 'Befehle':
            self.input_row(self.execute, 'Ausführen')
            self.command_area = self.text_area(local_command('hilfe'))
        elif name == 'Einstellungen':
            bar = tk.Frame(self.body, bg=BG, height=42)
            bar.pack(side='bottom', fill='x', pady=(4, 0))
            bar.pack_propagate(False)
            self.button(bar, 'WLAN einrichten', self.network_setup).pack(fill='both')
            self.settings_area = self.text_area('CT-Zugang: private client.json (0600).\n'
                'HTTPS oder lokaler SSH-Tunnel. Anleitung: pocketchip/README.md\n'
                'Updates: manuell, kein automatisches OTA.\nNetzstatus wird gelesen …')
            self.run_job('wifi', wifi_status)
        elif name == 'Werkzeuge':
            self.text_area(system_status() + '\n\nKeine automatischen Power-Aktionen.\n'
                           'Uhr und Rechner: Modul „Befehle“.')

    @staticmethod
    def replace(area, text):
        if area.winfo_exists():
            area.configure(state='normal')
            area.delete('1.0', 'end')
            area.insert('end', text)
            area.configure(state='disabled')

    def execute(self, entry):
        self.replace(self.command_area, local_command(entry.get()))

    def save_note(self, entry):
        try:
            self.archive.add(entry.get())
            self.open('Archive')
            self.footer.configure(text='NOTIZ GESICHERT  ·  lokal', fg=ACCENT)
        except (ValueError, OSError) as exc:
            self.footer.configure(text=str(exc)[:70], fg=ERROR)

    def client(self):
        return TextClient(ClientConfig.load(self.config_path))

    def send(self, entry):
        text = entry.get().strip()
        if self.busy or not text:
            return
        if len(text) > 2000:
            self.footer.configure(text='Maximal 2000 Zeichen pro Nachricht.', fg=ERROR)
            return
        self.conversation = (self.conversation + '\nDU > ' + text + '\n')[-16000:]
        self.replace(self.chat_area, self.conversation)
        self.chat_area.see('end')
        entry.delete(0, 'end')
        self.run_job('reply', lambda: self.client().ask(text))

    def check_ct(self):
        self.run_job('status', lambda: self.client().status())

    def run_job(self, kind, function):
        if self.busy:
            self.footer.configure(text='KANAL BELEGT  ·  laufenden Auftrag abwarten', fg=ACCENT)
            return
        self.busy = True
        self.footer.configure(text='VERBINDUNG WIRD GEPRÜFT …' if kind != 'reply' else 'CT107 DENKT …', fg=ACCENT)

        def worker():
            try:
                result, failed = function(), False
            except FileNotFoundError:
                result, failed = 'CT-Zugang fehlt. client.json gemäß Anleitung einrichten.', True
            except Exception:
                result, failed = ('Kanal nicht verfügbar. Zugang, TLS und CT107 prüfen. '
                                  'Nachricht wird nicht automatisch erneut gesendet.'), True
            self.events.put((kind, result, failed))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            kind, result, failed = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            self.footer.configure(text=('OFFLINE / ZUGANG PRÜFEN' if failed else 'BEREIT') +
                                  '  ·  Esc Home', fg=ERROR if failed else DIM)
            if kind == 'reply':
                self.conversation = (self.conversation + '\nPROXIMUS > ' + result + '\n')[-16000:]
                if self.page == 'Vox':
                    self.replace(self.chat_area, self.conversation)
                    self.chat_area.see('end')
            elif kind == 'status' and self.page == 'Homelab':
                self.replace(self.status_area, result)
            elif kind == 'wifi' and self.page == 'Einstellungen':
                self.replace(self.settings_area, 'WLAN / NetworkManager\n' + result +
                             '\n\nCT-Zugang: client.json (0600).\nUpdates manuell; OTA gesperrt.')
        self.root.after(100 if self.busy else 500, self.poll)

    def network_setup(self):
        if messagebox.askyesno('WLAN einrichten', 'Lokales Netzwerkmenü öffnen? Änderungen können die Verbindung trennen.'):
            try:
                subprocess.Popen(['xterm', '-fa', 'DejaVu Sans Mono', '-fs', '9', '-e', 'nmtui'],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
            except OSError:
                self.footer.configure(text='nmtui/xterm fehlt. Netzwerk lokal einrichten.', fg=ERROR)


def smoke(shell, root):
    """Real Tk lifecycle/input check under a 480x272 X server; no network needed."""
    shell.show_home()
    root.update()
    assert (root.winfo_width(), root.winfo_height()) == (480, 272)
    for button in shell.body.winfo_children():
        assert button.winfo_height() >= 40
        assert button.winfo_width() >= 120
    for name, _ in MODULES:
        shell.open(name)
        root.update()
        assert shell.body.winfo_height() >= 180
        for child in shell.body.winfo_children():
            assert child.winfo_width() <= 464
        root.event_generate('<Escape>')
        root.update()
        assert shell.page == 'Home'
    shell.archive.add('Bootprobe: lokale Daten sind schreibbar.')
    assert shell.archive.notes()[-1]['text'].startswith('Bootprobe:')
    assert local_command('rechne (12 + 3) / 2') == '7.5'
    print('PROXIMUS_GUI_OK 480x272 modules=6 text-only offline archive calculator', flush=True)
    root.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fullscreen', action='store_true')
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--data', default=str(Path.home() / '.local/share/proximus'))
    parser.add_argument('--config', default=str(Path.home() / '.config/proximus/client.json'))
    args = parser.parse_args()
    root = tk.Tk()
    # Smoke data never touches a user's actual notes.
    if args.smoke:
        with tempfile.TemporaryDirectory() as directory:
            shell = Shell(root, directory, args.config)
            root.after(300, lambda: smoke(shell, root))
            root.mainloop()
    else:
        Shell(root, args.data, args.config, args.fullscreen)
        root.mainloop()


if __name__ == '__main__':
    main()
