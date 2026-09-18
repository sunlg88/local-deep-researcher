"""Korean desktop control panel; model work never runs on the Tk event thread."""
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from .pm_engine import Engine, TERMINAL
from .pm_io import Ollama, Web
from .pm_store import Store
from .pm_types import Settings

STATUS = {'PENDING': '\ub300\uae30', 'RUNNING': '\uc9c4\ud589 \uc911',
          'PAUSED': '\uc77c\uc2dc\uc815\uc9c0', 'STOPPED': '\uc911\uc9c0\ub428',
          'DONE': '\uac80\ud1a0 \ud1b5\uacfc', 'RETRY': '\uc7ac\uc870\uc0ac',
          'BLOCKED': '\uadfc\uac70 \ubd80\uc871 / \ubcf4\ub958', 'PARTIAL': '\ubd80\ubd84 \uacb0\uacfc',
          'COMPLETED_REVIEW_REQUIRED': '\uc885\ub8cc / \uc0ac\ub78c \uac80\ud1a0 \ud544\uc694',
          'BUDGET_EXHAUSTED': '\uc791\uc5c5 \ud55c\ub3c4 \ub3c4\ub2ec', 'ERROR': '\uc624\ub958 / \uc7ac\uac1c \uac00\ub2a5'}

class App:
    def __init__(self, root, data_dir=None):
        self.root = root
        self.store = Store(data_dir or Path.cwd()/'pm_data')
        self.messages, self.worker, self.pid = queue.Queue(), None, None
        self.project_ids, self.report_stamp = [], None
        root.title('Research PM - \uadfc\uac70 \uc911\uc2ec \uc5f0\uad6c\uc2e4')
        root.geometry('1200x880'); root.minsize(1050, 760)
        style = ttk.Style(root); style.theme_use('clam')
        style.configure('.', font=('Malgun Gothic', 10))
        style.configure('Treeview', rowheight=32)
        style.configure('Title.TLabel', font=('Malgun Gothic', 20, 'bold'))
        outer = ttk.Frame(root, padding=16); outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='Research PM  |  \uadfc\uac70\uac00 \uc788\uc5b4\uc57c \ud1b5\uacfc', style='Title.TLabel').pack(anchor='w')
        ttk.Label(outer, text='\uacc4\ud68d > \uc870\uc0ac > \ucd94\ucd9c > \ube44\ud310 \uac80\ud1a0 > \uc885\ud569  |  \ub3d9\uc77c \ubaa8\ub378 \uc21c\ucc28 \uc2e4\ud589  |  \ucd5c\uc885 \uc0ac\ub78c \uac80\ud1a0 \ud544\uc218').pack(anchor='w', pady=(5,12))
        cfg = Settings()
        self.url = tk.StringVar(value=cfg.ollama_url)
        self.model = tk.StringVar(value=cfg.model)
        self.search = tk.StringVar(value=cfg.search_api)
        self.source_policy = tk.StringVar(value=SOURCE_MODE_NAMES[cfg.source_mode])
        self.domains = tk.StringVar(value=', '.join(cfg.allowed_domains))
        self.think = tk.BooleanVar(value=True)
        self.values = {k: tk.StringVar(value=str(getattr(cfg, k))) for k in (
            'min_sources', 'max_attempts', 'report_minutes', 'context_tokens',
            'output_tokens', 'max_calls', 'max_searches', 'min_tasks', 'max_tasks')}
        box = ttk.LabelFrame(outer, text='1. \uc5f0\uacb0 / \uc870\uc0ac \uae30\uc900', padding=10); box.pack(fill='x')
        ttk.Label(box, text='Ollama').grid(row=0, column=0, sticky='w')
        ttk.Entry(box, textvariable=self.url, width=29).grid(row=0, column=1, padx=6)
        self.model_box = ttk.Combobox(box, textvariable=self.model, width=23)
        self.model_box.grid(row=0, column=2, padx=6)
        ttk.Button(box, text='\uc5f0\uacb0 / \ubaa8\ub378 \ucc3e\uae30', command=self.connect).grid(row=0, column=3, padx=6)
        ttk.Combobox(box, textvariable=self.search, values=('duckduckgo','searxng','tavily'), state='readonly', width=13).grid(row=0, column=4, padx=6)
        ttk.Label(box, text='\ud5c8\uc6a9 \ub3c4\uba54\uc778').grid(row=1, column=0, sticky='w', pady=8)
        ttk.Entry(box, textvariable=self.domains).grid(row=1, column=1, columnspan=4, sticky='ew', padx=6)
        ttk.Label(box, text='\ud68c\uc0ac \uc870\uc0ac\ub294 \ud574\ub2f9 \uacf5\uc2dd \ub3c4\uba54\uc778\ub3c4 \ucd94\uac00\ud558\uc138\uc694. URL \uc804\uccb4 \ub300\uc2e0 example.org \ud615\uc2dd, \uc27c\ud45c\ub85c \uad6c\ubd84.').grid(row=2, column=0, columnspan=5, sticky='w')
        rows = [(
            ('\ucd5c\uc18c \ucd9c\ucc98', 'min_sources'), ('\ucd5c\ub300 \uc2dc\ub3c4', 'max_attempts'),
            ('\ubcf4\uace0 \uac04\uaca9(\ubd84)', 'report_minutes'), ('Context', 'context_tokens'), ('Output', 'output_tokens')),
            (('\ucd1d \ud638\ucd9c', 'max_calls'), ('\ucd1d \uac80\uc0c9', 'max_searches'),
             ('\ucd5c\uc18c \uacfc\uc81c', 'min_tasks'), ('\ucd5c\ub300 \uacfc\uc81c', 'max_tasks'))]
        for index, row in enumerate(rows, 3):
            bar = ttk.Frame(box); bar.grid(row=index, column=0, columnspan=5, sticky='w', pady=(8,0))
            for label, key in row:
                ttk.Label(bar, text=label).pack(side='left', padx=(0,5))
                ttk.Entry(bar, textvariable=self.values[key], width=6).pack(side='left', padx=(0,14))
            if index == 4:
                ttk.Checkbutton(bar, text='\ud310\ub2e8 \ub2e8\uacc4 \ucd94\ub860', variable=self.think).pack(side='left')
        box.columnconfigure(4, weight=1)
        topic_box = ttk.LabelFrame(outer, text='2. \uc5f0\uad6c \uc9c0\uc2dc', padding=10); topic_box.pack(fill='x', pady=10)
        self.topic = tk.StringVar(value='')
        ttk.Entry(topic_box, textvariable=self.topic, font=('Malgun Gothic',12)).pack(fill='x', pady=(0,6))
        self.instructions = ScrolledText(topic_box, height=2, font=('Malgun Gothic',10), wrap='word')
        self.instructions.insert('1.0', '\uc124\ube44 \ub2a5\ub825\uacfc \uc0dd\uc0b0 \uc2e4\uc801\uc744 \uad6c\ubd84. \ubbf8\ud655\uc778 \uc218\uce58\ub294 \ucd94\uc815\ud558\uc9c0 \ub9d0 \uac83.')
        self.instructions.pack(fill='x')
        buttons = ttk.Frame(outer); buttons.pack(fill='x')
        self.start_button = ttk.Button(buttons, text='\uc0c8 \uc5f0\uad6c \uc2dc\uc791', command=self.start); self.start_button.pack(side='left', padx=(0,5))
        self.pause_button = ttk.Button(buttons, text='\uc77c\uc2dc\uc815\uc9c0', command=lambda: self.control('PAUSE')); self.pause_button.pack(side='left', padx=5)
        for text, command in [('\uc911\uc9c0', lambda: self.control('STOP')), ('\uc120\ud0dd \uc5f0\uad6c \uc7ac\uac1c', self.resume), ('\uacb0\uacfc \ud3f4\ub354', self.open_results)]:
            ttk.Button(buttons, text=text, command=command).pack(side='left', padx=5)
        self.saved = ttk.Combobox(buttons, state='readonly', width=32); self.saved.pack(side='right')
        self.saved.bind('<<ComboboxSelected>>', self.select_saved)
        self.status = tk.StringVar(value='\uc5f0\uad6c \uc8fc\uc81c\uc640 \ud5c8\uc6a9 \ucd9c\ucc98\ub97c \uc124\uc815\ud558\uace0 Ollama \uc5f0\uacb0\uc744 \ud655\uc778\ud558\uc138\uc694.')
        ttk.Label(outer, textvariable=self.status, wraplength=1120).pack(anchor='w', pady=10)
        tabs = ttk.Notebook(outer); tabs.pack(fill='both', expand=True)
        task_tab, report_tab, log_tab = [ttk.Frame(tabs) for _ in range(3)]
        for frame, label in [(task_tab,'\uacfc\uc81c \ud604\ud669'), (report_tab,'\uacb0\uacfc / \uadfc\uac70'), (log_tab,'\uc2e4\ud589 \uae30\ub85d')]: tabs.add(frame, text=label)
        self.table = ttk.Treeview(task_tab, columns=('task','state','attempts','evidence'), show='headings')
        for key, label, width in [('task','\uc5f0\uad6c \uacfc\uc81c',620),('state','\uc0c1\ud0dc',210),('attempts','\uc2dc\ub3c4',65),('evidence','\uadfc\uac70 \uc218',70)]:
            self.table.heading(key,text=label); self.table.column(key,width=width,stretch=key in ('task','state'))
        self.table.pack(fill='both', expand=True)
        self.report = ScrolledText(report_tab, wrap='word', font=('Malgun Gothic',11)); self.report.pack(fill='both',expand=True)
        self.log = ScrolledText(log_tab, wrap='word', font=('Consolas',10)); self.log.pack(fill='both',expand=True)
        self.reload_projects(); self.refresh()
        root.protocol('WM_DELETE_WINDOW', self.close)

    def settings(self):
        values = {k: int(v.get()) for k,v in self.values.items()}
        return Settings(model=self.model.get().strip(), ollama_url=self.url.get().strip(), search_api=self.search.get(),
                        source_mode=SOURCE_MODE_LABELS[self.source_policy.get()],
                        allowed_domains=[d.strip() for d in self.domains.get().split(',') if d.strip()], think=self.think.get(), **values)

    def busy(self): return self.worker is not None and self.worker.is_alive()

    def connect(self):
        try: cfg = self.settings()
        except ValueError as exc:
            messagebox.showerror('Settings',str(exc)); return
        def lookup():
            try: self.messages.put(('models',Ollama(cfg).models()))
            except Exception as exc: self.messages.put(('error',str(exc)))
        threading.Thread(target=lookup,daemon=True).start()

    def launch(self):
        pid = self.pid; cfg = Settings.from_saved(self.store.load(pid)['settings'])
        def work():
            try: Engine(self.store,Ollama(cfg),Web(cfg)).run(pid)
            except Exception as exc: self.messages.put(('error',str(exc)))
        self.worker = threading.Thread(target=work,daemon=True); self.worker.start()

    def start(self):
        if self.busy(): return
        try:
            cfg = self.settings()
            from . import utils  # Check search dependencies before consuming model calls.
            self.pid = self.store.create(self.topic.get(),cfg,self.instructions.get('1.0','end').strip())
            self.reload_projects(); self.launch()
        except (ValueError,ImportError) as exc:
            messagebox.showerror('\uc2dc\uc791 \uc124\uc815 \ud655\uc778',str(exc)+'\n\nINSTALL_PM_DEPENDENCIES.bat: \uc758\uc874\uc131 \uc124\uce58')

    def control(self,action):
        if self.pid:
            self.store.control(self.pid,action)
            self.status.set('\uc694\uccad \uc800\uc7a5\ub428. \ud1b5\uc2e0 / \ucd94\ub860 \ucc98\ub9ac \uacbd\uacc4\uc5d0\uc11c \ubc18\uc601\ub429\ub2c8\ub2e4.')

    def resume(self):
        if self.busy() or not self.pid: return
        s = self.store.load(self.pid)
        if s['status'] in TERMINAL-{'ERROR'}:
            messagebox.showinfo('Research PM','\uc885\ub8cc\ub41c \uc5f0\uad6c\uc785\ub2c8\ub2e4. \uc0c8 \uc5f0\uad6c\uc5d0\uc11c \uae30\uc874 DB\ub97c \uc7ac\ud65c\uc6a9\ud569\ub2c8\ub2e4.'); return
        s['status'],s['errors'] = 'PENDING',0
        self.store.save(s); self.store.control(self.pid,'RUN'); self.launch()

    def reload_projects(self):
        projects = self.store.projects(); self.project_ids = [p['id'] for p in projects]
        self.saved['values'] = [p['id'][:6]+' | '+p['topic'][:24] for p in projects]
        if self.pid in self.project_ids: self.saved.current(self.project_ids.index(self.pid))
        elif projects: self.saved.current(0); self.pid = self.project_ids[0]

    def select_saved(self,event=None):
        if self.busy(): self.reload_projects(); return
        i = self.saved.current()
        if i >= 0:
            self.pid = self.project_ids[i]; self.report_stamp = None
            s = self.store.load(self.pid); self.topic.set(s['topic'])
            cfg = s['settings']; self.url.set(cfg['ollama_url']); self.model.set(cfg['model'])
            self.search.set(cfg['search_api']); self.source_policy.set(SOURCE_MODE_NAMES[cfg.get('source_mode', 'allowlist')])
            self.domains.set(', '.join(cfg['allowed_domains'])); self.think.set(cfg['think'])
            for k,v in self.values.items(): v.set(str(cfg[k]))
            self.replace(self.instructions,s['instructions'])

    @staticmethod
    def replace(widget,text): widget.delete('1.0','end'); widget.insert('1.0',text)

    def refresh(self):
        try:
            while not self.messages.empty():
                kind,value = self.messages.get_nowait()
                if kind == 'models':
                    self.model_box['values'] = value
                    self.status.set('Ollama: '+', '.join(value))
                else: messagebox.showerror('Research PM',value)
            self.start_button.configure(state='disabled' if self.busy() else 'normal')
            if self.pid:
                s = self.store.load(self.pid); done = sum(t['status']=='DONE' for t in s['tasks'])
                pending = '' if s['control']=='RUN' else ' | '+s['control']+' requested'
                self.status.set(f"{STATUS.get(s['status'],s['status'])} | {s['stage']} | {done}/{len(s['tasks'])} | LLM {s['calls']} | Search {s['searches']}"+pending)
                self.table.delete(*self.table.get_children())
                for t in s['tasks']: self.table.insert('','end',values=(t['title'],STATUS.get(t['status'],t['status']),t['attempts'],len(t['evidence_ids'])))
                self.replace(self.log,'\n'.join(f"{e['time']} [{e['kind']}] {e['message']}" for e in self.store.events(self.pid)))
                path = self.store.root/'projects'/self.pid/'progress.md'
                if path.exists():
                    stamp = (str(path),path.stat().st_mtime_ns)
                    if stamp != self.report_stamp:
                        self.replace(self.report,path.read_text(encoding='utf-8')); self.report_stamp = stamp
        except (OSError,ValueError) as exc: self.status.set(str(exc))
        self.root.after(1000,self.refresh)

    def open_results(self):
        if not self.pid: return
        folder = self.store.export(self.pid)
        if os.name == 'nt': os.startfile(str(folder))
        else: subprocess.Popen(['open' if sys.platform=='darwin' else 'xdg-open',str(folder)])

    def close(self):
        if self.busy():
            if not messagebox.askyesno('Research PM','\uc911\uc9c0 \ud6c4 \ub2eb\uc744\uae4c\uc694? \ubbf8\uc644\ub8cc \ub2e8\uacc4\ub294 \uc7ac\uac1c \uc2dc \ub2e4\uc2dc \uc2e4\ud589\ub429\ub2c8\ub2e4.'): return
            self.store.control(self.pid,'STOP')
        self.root.destroy()

def main():
    os.environ['LANGSMITH_TRACING'] = 'false'; os.environ['LANGCHAIN_TRACING_V2'] = 'false'
    try:
        from dotenv import load_dotenv
        load_dotenv(override=False)
    except ImportError: pass
    root = tk.Tk(); App(root,os.environ.get('RESEARCH_PM_DATA_DIR')); root.mainloop()

if __name__ == '__main__': main()
