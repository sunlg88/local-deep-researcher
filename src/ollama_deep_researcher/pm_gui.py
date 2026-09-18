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
from .pm_projects import Workspace
from .pm_types import Settings

SOURCE_MODE_LABELS = {'일반 웹 조사': 'open', '신뢰 도메인 우선': 'preferred', '허용 도메인 전용': 'allowlist'}
SOURCE_MODE_NAMES = {value: label for label, value in SOURCE_MODE_LABELS.items()}

STATUS = {'PENDING': '\ub300\uae30', 'RUNNING': '\uc9c4\ud589 \uc911',
          'PAUSED': '\uc77c\uc2dc\uc815\uc9c0', 'STOPPED': '\uc911\uc9c0\ub428',
          'DONE': '\uac80\ud1a0 \ud1b5\uacfc', 'RETRY': '\uc7ac\uc870\uc0ac',
          'BLOCKED': '\uadfc\uac70 \ubd80\uc871 / \ubcf4\ub958', 'PARTIAL': '\ubd80\ubd84 \uacb0\uacfc',
          'COMPLETED_REVIEW_REQUIRED': '\uc885\ub8cc / \uc0ac\ub78c \uac80\ud1a0 \ud544\uc694',
          'BUDGET_EXHAUSTED': '\uc791\uc5c5 \ud55c\ub3c4 \ub3c4\ub2ec', 'ERROR': '\uc624\ub958 / \uc7ac\uac1c \uac00\ub2a5'}

STATUS.update({'COLLECTED':'자료 축적 / 후처리 필요', 'NO_FINDINGS':'원문 추가 확인 필요',
    'NO_NEW_WORK':'현재 범위에서 새 작업 없음', 'TIME_LIMIT_REACHED':'실행 시간 한도',
    'STORAGE_ERROR':'저장 오류 / 안전 정지'})

class App:
    def __init__(self, root, data_dir=None):
        self.root = root
        self.store = Workspace(data_dir or Path.cwd()/'pm_data')
        self.messages, self.worker, self.pid = queue.Queue(), None, None
        self.project_ids, self.report_stamp = [], None
        self.reference_ids = []
        self.reference_label = tk.StringVar(value='참조 프로젝트: 선택 없음 (완전 독립)')
        self.refresh_token = None
        root.title('Research PM - \uadfc\uac70 \uc911\uc2ec \uc5f0\uad6c\uc2e4')
        root.geometry('1200x880'); root.minsize(1050, 760)
        style = ttk.Style(root); style.theme_use('clam')
        style.configure('.', font=('Malgun Gothic', 10))
        style.configure('Treeview', rowheight=32)
        style.configure('Title.TLabel', font=('Malgun Gothic', 20, 'bold'))
        outer = ttk.Frame(root, padding=16); outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='Research PM  |  원문을 쌓는 리서치 연구실', style='Title.TLabel').pack(anchor='w')
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
            'output_tokens', 'max_calls', 'max_searches', 'min_tasks', 'max_tasks', 'time_limit_minutes')}
        box = ttk.LabelFrame(outer, text='1. \uc5f0\uacb0 / \uc870\uc0ac \uae30\uc900', padding=10); box.pack(fill='x')
        ttk.Label(box, text='Ollama').grid(row=0, column=0, sticky='w')
        ttk.Entry(box, textvariable=self.url, width=29).grid(row=0, column=1, padx=6)
        self.model_box = ttk.Combobox(box, textvariable=self.model, width=23)
        self.model_box.grid(row=0, column=2, padx=6)
        ttk.Button(box, text='\uc5f0\uacb0 / \ubaa8\ub378 \ucc3e\uae30', command=self.connect).grid(row=0, column=3, padx=6)
        ttk.Combobox(box, textvariable=self.search, values=('duckduckgo','searxng','tavily'), state='readonly', width=13).grid(row=0, column=4, padx=6)
        ttk.Label(box, text='출처 정책').grid(row=1, column=0, sticky='w', pady=8)
        ttk.Combobox(box, textvariable=self.source_policy, values=tuple(SOURCE_MODE_LABELS), state='readonly', width=18).grid(row=1, column=1, sticky='w', padx=6)
        ttk.Label(box, text='선호 / 허용 도메인').grid(row=1, column=2, sticky='e', padx=(12,0))
        ttk.Entry(box, textvariable=self.domains).grid(row=1, column=3, columnspan=2, sticky='ew', padx=6)
        ttk.Label(box, text='일반 웹 조사는 공란으로 바로 실행 가능. 업무 자료는 신뢰 도메인 우선, 엄격 제한이 필요할 때만 허용 도메인 전용을 선택하세요.').grid(row=2, column=0, columnspan=5, sticky='w')
        runtime = ttk.Frame(box)
        runtime.grid(row=3, column=0, columnspan=5, sticky='w', pady=(8,0))
        for label,key in [('실행 시간(분)', 'time_limit_minutes'), ('보고 간격(분)', 'report_minutes')]:
            ttk.Label(runtime,text=label).pack(side='left',padx=(0,5))
            ttk.Entry(runtime,textvariable=self.values[key],width=6).pack(side='left',padx=(0,12))
        ttk.Label(runtime,text='0 = 시간 제한 없음 / 호출·검색 한도는 유지').pack(side='left',padx=5)
        ttk.Button(runtime,text='고급 설정 열기 / 닫기',command=self.toggle_advanced).pack(side='left',padx=12)
        self.advanced=ttk.Frame(box)
        self.advanced.grid(row=4,column=0,columnspan=5,sticky='ew',pady=6)
        rows = [[('출처 보강 목표','min_sources'), ('과제당 검색 시도','max_attempts'), ('Context','context_tokens'),('Output','output_tokens')],
                [('총 호출','max_calls'), ('총 검색','max_searches'), ('초기 최소 과제','min_tasks'), ('최대 과제','max_tasks')]]
        for index,row in enumerate(rows):
            bar=ttk.Frame(self.advanced);bar.pack(anchor='w',pady=4)
            for label,key in row:
                ttk.Label(bar,text=label).pack(side='left',padx=(0,5))
                ttk.Entry(bar,textvariable=self.values[key],width=6).pack(side='left',padx=(0,14))
            if index==1:
                ttk.Checkbutton(bar,text='판단 단계 추론',variable=self.think).pack(side='left')
        self.strict_final=tk.BooleanVar(value=cfg.strict_final)
        self.draft_enabled=tk.BooleanVar(value=cfg.draft_enabled)
        flags=ttk.Frame(self.advanced);flags.pack(anchor='w')
        ttk.Checkbutton(flags,text='최종 초안에만 엄격 출처 필터 (원문은 보존)',variable=self.strict_final).pack(side='left')
        ttk.Checkbutton(flags,text='참고용 요약 초안 생성',variable=self.draft_enabled).pack(side='left',padx=16)
        self.advanced.grid_remove()
        box.columnconfigure(4, weight=1)
        topic_box = ttk.LabelFrame(outer, text='2. \uc5f0\uad6c \uc9c0\uc2dc', padding=10); topic_box.pack(fill='x', pady=10)
        self.topic = tk.StringVar(value='')
        ttk.Entry(topic_box, textvariable=self.topic, font=('Malgun Gothic',12)).pack(fill='x', pady=(0,6))
        self.instructions = ScrolledText(topic_box, height=2, font=('Malgun Gothic',10), wrap='word')
        self.instructions.pack(fill='x')
        references=ttk.Frame(topic_box);references.pack(fill='x',pady=(8,0))
        self.reference_button=ttk.Button(references,text='참조 프로젝트 선택',command=self.choose_references)
        self.reference_button.pack(side='left')
        ttk.Label(references,textvariable=self.reference_label).pack(side='left',padx=12)
        buttons = ttk.Frame(outer); buttons.pack(fill='x')
        self.start_button = ttk.Button(buttons, text='\uc0c8 \uc5f0\uad6c \uc2dc\uc791', command=self.start); self.start_button.pack(side='left', padx=(0,5))
        self.pause_button = ttk.Button(buttons, text='\uc77c\uc2dc\uc815\uc9c0', command=lambda: self.control('PAUSE')); self.pause_button.pack(side='left', padx=5)
        for text, command in [('\uc911\uc9c0', lambda: self.control('STOP')), ('\uc120\ud0dd \uc5f0\uad6c \uc7ac\uac1c', self.resume), ('\uacb0\uacfc \ud3f4\ub354', self.open_results)]:
            ttk.Button(buttons, text=text, command=command).pack(side='left', padx=5)
        self.saved = ttk.Combobox(buttons, state='readonly', width=32); self.saved.pack(side='right')
        self.saved.bind('<<ComboboxSelected>>', self.select_saved)
        self.status = tk.StringVar(value='연구 주제를 입력하고 Ollama 연결을 확인하세요. 일반 웹 조사는 도메인 입력이 필요 없습니다.')
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
                        allowed_domains=[d.strip() for d in self.domains.get().split(',') if d.strip()], think=self.think.get(),
                        strict_final=self.strict_final.get(), draft_enabled=self.draft_enabled.get(), **values)

    def toggle_advanced(self):
        if self.advanced.winfo_ismapped(): self.advanced.grid_remove()
        else: self.advanced.grid()

    def update_reference_label(self):
        names={p['id']:p['topic'] for p in self.store.projects()}
        selected=[names.get(pid,pid)[:24] for pid in self.reference_ids]
        self.reference_label.set('참조: '+(' / '.join(selected) if selected else '선택 없음 (완전 독립)'))

    def choose_references(self):
        projects=self.store.projects()
        window=tk.Toplevel(self.root);window.title('새 연구에서 사용할 참조 프로젝트')
        window.geometry('620x360');window.transient(self.root)
        ttk.Label(window,text='선택한 프로젝트만 원문 후보로 참조합니다. 기존 연구는 변경하지 않습니다.').pack(pady=12)
        choices=tk.Listbox(window,selectmode='multiple',exportselection=False,font=('Malgun Gothic',11))
        choices.pack(fill='both',expand=True,padx=14,pady=6)
        for index,project in enumerate(projects):
            choices.insert('end',project['id'][:8]+' | '+project['topic'])
            if project['id'] in self.reference_ids: choices.selection_set(index)
        def apply():
            self.reference_ids=[projects[i]['id'] for i in choices.curselection()]
            self.update_reference_label();window.destroy()
        bar=ttk.Frame(window);bar.pack(pady=10)
        ttk.Button(bar,text='선택 적용',command=apply).pack(side='left',padx=6)
        ttk.Button(bar,text='전체 해제',command=lambda:choices.selection_clear(0,'end')).pack(side='left',padx=6)

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
            try: Engine(self.store.open(pid),Ollama(cfg),Web(cfg)).run(pid)
            except Exception as exc: self.messages.put(('error',str(exc)))
        self.worker = threading.Thread(target=work,daemon=True); self.worker.start()

    def start(self):
        if self.busy(): return
        try:
            cfg = self.settings()
            from . import utils  # Check search dependencies before consuming model calls.
            self.pid = self.store.create(self.topic.get(),cfg,self.instructions.get('1.0','end').strip(),reference_projects=self.reference_ids)
            self.reload_projects(); self.launch()
        except (ValueError,ImportError) as exc:
            messagebox.showerror('\uc2dc\uc791 \uc124\uc815 \ud655\uc778',str(exc)+'\n\nINSTALL_PM_DEPENDENCIES.bat: \uc758\uc874\uc131 \uc124\uce58')

    def control(self,action):
        if self.pid:
            self.store.control(self.pid,action)
            self.status.set('\uc694\uccad \uc800\uc7a5\ub428. \ud1b5\uc2e0 / \ucd94\ub860 \ucc98\ub9ac \uacbd\uacc4\uc5d0\uc11c \ubc18\uc601\ub429\ub2c8\ub2e4.')

    def resume(self):
        if self.busy() or not self.pid: return
        s=self.store.load(self.pid)
        if s['status'] in TERMINAL:
            messagebox.showinfo('Research PM','현재 한도에서 종료된 연구입니다. 새 연구에서 이 프로젝트를 참조로 지정하면 원문을 재활용할 수 있습니다.\n일시정지 / 사용자 중지는 현재 위치에서 재개됩니다.')
            return
        s['status'],s['errors']='PENDING',0
        self.store.save(s);self.store.control(self.pid,'RUN');self.launch()

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
            defaults=Settings.from_saved(cfg)
            for k,v in self.values.items(): v.set(str(getattr(defaults,k)))
            self.strict_final.set(defaults.strict_final);self.draft_enabled.set(defaults.draft_enabled)
            self.reference_ids=list(s.get('reference_projects',[]));self.update_reference_label()
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
                s = self.store.load(self.pid)
                local=self.store.open(self.pid);counts=local.counts()
                pending_review=sum(len(set(t.get('evidence_ids',[]))-set(t.get('reviewed_ids',[]))) for t in s['tasks'])
                failed=sum(bool(row.get('error')) for row in local.sources())
                pending = '' if s['control']=='RUN' else ' | '+s['control']+' requested'
                self.status.set(f"{STATUS.get(s['status'],s['status'])} | {s['stage']} | 원문 {counts['documents']} | 추출 {counts['evidence']} | 검토 대기 {pending_review} | 접근 실패 {failed} | LLM {s['calls']} | Search {s['searches']}"+pending+' | 마지막 수집: '+s.get('last_progress_at','-'))
                self.table.delete(*self.table.get_children())
                for t in s['tasks']: self.table.insert('','end',values=(t['title'],STATUS.get(t['status'],t['status']),t['attempts'],len(t['evidence_ids'])))
                self.replace(self.log,'\n'.join(f"{e['time']} [{e['kind']}] {e['message']}" for e in self.store.events(self.pid)))
                path = self.store.project_path(self.pid)/'handoff'/'progress.md'
                if path.exists():
                    stamp = (str(path),path.stat().st_mtime_ns)
                    if stamp != self.report_stamp:
                        self.replace(self.report,path.read_text(encoding='utf-8')); self.report_stamp = stamp
        except (OSError,ValueError) as exc: self.status.set(str(exc))
        if self.refresh_token is not None:
            try: self.root.after_cancel(self.refresh_token)
            except tk.TclError: pass
        self.refresh_token=self.root.after(1000,self.refresh)

    def open_results(self):
        if not self.pid: return
        folder = self.store.export(self.pid)
        if os.name == 'nt': os.startfile(str(folder))
        else: subprocess.Popen(['open' if sys.platform=='darwin' else 'xdg-open',str(folder)])

    def close(self):
        if self.busy():
            if not messagebox.askyesno('Research PM','\uc911\uc9c0 \ud6c4 \ub2eb\uc744\uae4c\uc694? \ubbf8\uc644\ub8cc \ub2e8\uacc4\ub294 \uc7ac\uac1c \uc2dc \ub2e4\uc2dc \uc2e4\ud589\ub429\ub2c8\ub2e4.'): return
            self.store.control(self.pid,'STOP')
        if self.refresh_token is not None:
            self.root.after_cancel(self.refresh_token)
        self.root.destroy()

def main():
    os.environ['LANGSMITH_TRACING'] = 'false'; os.environ['LANGCHAIN_TRACING_V2'] = 'false'
    try:
        from dotenv import load_dotenv
        load_dotenv(override=False)
    except ImportError: pass
    root = tk.Tk(); App(root,os.environ.get('RESEARCH_PM_DATA_DIR')); root.mainloop()

if __name__ == '__main__': main()
