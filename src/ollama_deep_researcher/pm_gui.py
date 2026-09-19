"""Small Windows-friendly desktop front end for the checkpointed research PM."""
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from urllib.request import urlopen
import json
import webbrowser

from .pm_engine import Engine
from .pm_engine_v05 import EngineV05
from .pm_engine_v06 import EngineV06
from .pm_io_v06 import WebV06
from . import pm_research_metrics as metrics
from .pm_budget import preflight_research_start
from .pm_io import Ollama, Web
from .pm_store import worker_lock
from .pm_projects import Workspace
from .pm_types import Settings, validate_research_input, MAX_TOPIC_BYTES, MAX_INSTRUCTION_BYTES


class App:
    def __init__(self, root, data_dir=None):
        self.root = root
        self.store = Workspace(data_dir or os.environ.get('RESEARCH_PM_DATA_DIR', 'pm_data'))
        self.thread = None
        self.messages = queue.Queue()
        self.current = None
        self.selection_signature = None
        self.closing = False
        self.view_signature = None
        root.title('Research PM 0.6.2 - isolated sources and evidence handoff')
        root.geometry('1180x890')
        root.protocol('WM_DELETE_WINDOW', self.close)
        outer = ttk.Frame(root, padding=12)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='Research PM  |  로컬 근거 중심 조사', font=('Malgun Gothic', 16, 'bold')).pack(anchor='w')
        self.defaults = Settings()
        self.variables = {name: tk.StringVar(value=str(getattr(self.defaults, name)))
                          for name in ('model','search_api','source_mode','min_sources','context_tokens','output_tokens','max_calls',
                                       'max_searches','min_tasks','max_tasks','max_attempts','report_minutes',
                                       'time_limit_minutes','request_timeout','source_chars','source_limit','optimization_mode','semantic_rerank')}
        self.domains = tk.StringVar(value='')
        self.think = tk.BooleanVar(value=True)
        self.strict = tk.BooleanVar(value=False)
        self.draft = tk.BooleanVar(value=True)
        ttk.Label(outer, text='연구 주제 (원문 그대로 모든 역할에 전달)').pack(anchor='w', pady=(10, 0))
        self.topic = tk.Text(outer, height=2, wrap='word', font=('Malgun Gothic', 10))
        self.topic.pack(fill='x')
        ttk.Label(outer, text='추가 지시 / 조사 범위 (선택, 프로젝트별 저장)').pack(anchor='w', pady=(5, 0))
        self.instructions = tk.Text(outer, height=2, wrap='word', font=('Malgun Gothic', 9))
        self.instructions.pack(fill='x')
        self.input_usage = tk.StringVar()
        ttk.Label(outer, textvariable=self.input_usage).pack(anchor='w')
        self.topic.bind('<KeyRelease>', self.update_input_usage)
        self.instructions.bind('<KeyRelease>', self.update_input_usage)
        self.update_input_usage()
        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill='x', pady=8)
        basic, advanced = ttk.Frame(self.notebook, padding=8), ttk.Frame(self.notebook, padding=8)
        self.notebook.add(basic, text='기본 설정')
        self.notebook.add(advanced, text='예산 / 고급 설정')
        ttk.Label(basic, text='Ollama 모델').grid(row=0, column=0, sticky='w')
        self.models = ttk.Combobox(basic, textvariable=self.variables['model'], width=24)
        self.models.grid(row=0, column=1, sticky='w', padx=5)
        ttk.Button(basic, text='모델 확인', command=self.check_model).grid(row=0, column=2, padx=4)
        ttk.Label(basic, text='검색').grid(row=0, column=3)
        ttk.Combobox(basic, textvariable=self.variables['search_api'], values=['duckduckgo','searxng','tavily'],
                     state='readonly', width=12).grid(row=0, column=4, padx=5)
        ttk.Label(basic, text='출처 정책').grid(row=1, column=0, sticky='w', pady=5)
        ttk.Combobox(basic, textvariable=self.variables['source_mode'], values=['open','preferred','allowlist'],
                     state='readonly', width=13).grid(row=1, column=1, sticky='w', padx=5)
        ttk.Label(basic, text='도메인 (preferred/allowlist)').grid(row=2, column=0, sticky='w', pady=5)
        ttk.Entry(basic, textvariable=self.domains, width=78).grid(row=2, column=1, columnspan=4, sticky='ew', padx=5)
        ttk.Label(basic, text='최적화 모드').grid(row=3,column=0,sticky='w')
        ttk.Combobox(basic,textvariable=self.variables['optimization_mode'],values=['efficient','balanced','quality'],
                     state='readonly',width=13).grid(row=3,column=1,sticky='w',padx=5)
        ttk.Label(basic,text='의미 재정렬 (기본 off)').grid(row=3,column=2,sticky='w')
        ttk.Combobox(basic,textvariable=self.variables['semantic_rerank'],values=['off','auto','on'],
                     state='readonly',width=8).grid(row=3,column=3,sticky='w',padx=5)
        ttk.Label(basic, text='SearXNG: SEARXNG_URL   /   Tavily: TAVILY_API_KEY 환경변수', foreground='#666').grid(
            row=4, column=0, columnspan=5, sticky='w')
        options = [('context_tokens','Context'),('output_tokens','출력 토큰'),('max_calls','모델 호출 한도'),
                   ('max_searches','검색 한도'),('min_tasks','최소 Task'),('max_tasks','최대 Task'),
                   ('max_attempts','Task별 시도'),('report_minutes','중간보고(분)'),
                   ('time_limit_minutes','시간(분, 0=무제한)'),('request_timeout','요청 timeout(초)'),
                   ('source_chars','원문 구간(자)'),('source_limit','검색당 출처 수'),('min_sources','최소 출처 수')]
        for i, (key, label) in enumerate(options):
            row, col = divmod(i, 4)
            ttk.Label(advanced, text=label).grid(row=row, column=col*2, sticky='w', padx=3, pady=3)
            ttk.Entry(advanced, textvariable=self.variables[key], width=8).grid(row=row, column=col*2+1, padx=3)
        ttk.Checkbutton(advanced, text='Planner/Critic/Writer Thinking', variable=self.think).grid(row=4, column=0, columnspan=3, sticky='w')
        ttk.Checkbutton(advanced, text='엄격 보고서 필터', variable=self.strict).grid(row=4, column=3, columnspan=2, sticky='w')
        ttk.Checkbutton(advanced, text='참고용 초안 작성', variable=self.draft).grid(row=4, column=5, columnspan=3, sticky='w')
        ttk.Label(advanced, text='모델 토큰 추정치로 원문/근거를 분할하며, 한도에서 잘린 모델 응답은 채택하지 않습니다.', foreground='#666').grid(
            row=5, column=0, columnspan=8, sticky='w')
        refs = ttk.LabelFrame(outer, text='명시적으로 참조할 기존 프로젝트 (선택하지 않으면 완전 독립)', padding=4)
        refs.pack(fill='x')
        self.references = tk.Listbox(refs, selectmode='multiple', exportselection=False, height=2)
        self.references.pack(fill='x')
        self.reference_ids = []
        actions = ttk.Frame(outer)
        actions.pack(fill='x', pady=5)
        self.start_button = ttk.Button(actions, text='새 연구 시작', command=self.start)
        self.start_button.pack(side='left', padx=3)
        self.resume_button = ttk.Button(actions, text='선택 연구 재개', command=self.resume)
        self.resume_button.pack(side='left', padx=3)
        self.continue_button = ttk.Button(actions, text='0.6 방식으로 이어서 조사', command=self.continue_v06)
        self.continue_button.pack(side='left', padx=3)
        self.pause_button = ttk.Button(actions, text='일시정지', command=lambda: self.control('PAUSE'))
        self.pause_button.pack(side='left', padx=3)
        self.stop_button = ttk.Button(actions, text='중지·저장', command=lambda: self.control('STOP'))
        self.stop_button.pack(side='left', padx=3)
        self.open_button = ttk.Button(actions, text='결과 폴더 열기', command=self.open_results)
        self.open_button.pack(side='left', padx=3)
        self.status = tk.StringVar(value='API 구독 불필요 · 자동 종료/절전 방지는 운영체제에서 별도 설정')
        ttk.Label(outer, textvariable=self.status).pack(anchor='w', pady=3)
        self.efficiency=tk.StringVar(value='v0.6.2 / actual research efficiency requires live validation')
        ttk.Label(outer,textvariable=self.efficiency,wraplength=1080).pack(anchor='w',pady=2)
        self.projects = ttk.Combobox(outer, state='readonly', width=110)
        self.projects.pack(fill='x')
        self.projects.bind('<<ComboboxSelected>>', self.select)
        self.project_ids = []
        self.tasks = ttk.Treeview(outer, columns=('state','searches','fetched','relevant','evidence','zero'), show='tree headings', height=5)
        self.tasks.heading('#0', text='Task / 조사 항목')
        self.tasks.column('#0', width=470)
        for key, label, width in [('state','상태',130),('searches','검색',65),('fetched','원문',65),
                                  ('relevant','관련',65),('evidence','근거',65),('zero','무성과',70)]:
            self.tasks.heading(key, text=label)
            self.tasks.column(key, width=width)
        self.tasks.pack(fill='both', pady=7)
        self.log = tk.Text(outer, height=9, wrap='word', font=('Consolas', 9), state='disabled')
        self.log.pack(fill='both', expand=True)
        ttk.Label(outer, text='PASS/초안은 동일 모델의 판정입니다. 최종 인계본의 원문·단일출처·충돌 후보를 사람이 검토해야 합니다.',
                  foreground='#8b3a14').pack(anchor='w', pady=4)
        self.refresh_projects()
        self.set_running(False)
        self.refresh()

    def update_input_usage(self, event=None):
        topic = self.topic.get('1.0', 'end').strip()
        instructions = self.instructions.get('1.0', 'end').strip()
        self.input_usage.set(f'주제 {len(topic.encode("utf-8")):,}/{MAX_TOPIC_BYTES:,} · '
                             f'추가 지시 {len(instructions.encode("utf-8")):,}/{MAX_INSTRUCTION_BYTES:,} UTF-8 bytes')

    def read_settings(self):
        data = self.defaults.to_dict()
        for key, var in self.variables.items():
            data[key] = var.get().strip() if key in ('model','search_api','source_mode','optimization_mode','semantic_rerank') else int(var.get())
        data.update(allowed_domains=[d.strip() for d in self.domains.get().replace(';', ',').split(',') if d.strip()],
                    think=self.think.get(), strict_final=self.strict.get(), draft_enabled=self.draft.get())
        return Settings(**data)

    def display_settings(self, cfg):
        for key, var in self.variables.items():
            var.set(str(getattr(cfg, key)))
        self.domains.set(', '.join(cfg.allowed_domains))
        self.think.set(cfg.think)
        self.strict.set(cfg.strict_final)
        self.draft.set(cfg.draft_enabled)

    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def set_running(self, running):
        self.start_button.configure(state='disabled' if running else 'normal')
        self.resume_button.configure(state='disabled' if running else 'normal')
        self.continue_button.configure(state='disabled' if running else 'normal')
        self.pause_button.configure(state='normal' if running else 'disabled')
        self.stop_button.configure(state='normal' if running else 'disabled')
        self.projects.configure(state='disabled' if running else 'readonly')
        self.references.configure(state='disabled' if running else 'normal')
        # Settings are frozen for a running job: disable those fields, not every
        # ttk widget (doing so also disables action buttons and creates stale UI).
        for tab in self.notebook.tabs():
            frame = self.root.nametowidget(tab)
            for widget in frame.winfo_children():
                if isinstance(widget, (ttk.Entry, ttk.Combobox, ttk.Checkbutton)):
                    if running:
                        widget.configure(state='disabled')
                    else:
                        readonly = isinstance(widget, ttk.Combobox) and widget is not self.models
                        widget.configure(state='readonly' if readonly else 'normal')
        self.topic.configure(state='disabled' if running else 'normal')
        self.instructions.configure(state='disabled' if running else 'normal')

    def refresh_projects(self):
        projects = self.store.projects()
        self.project_ids = [p['id'] for p in projects]
        self.projects['values'] = [f"{p['id']} | {p['status']} | {p['topic'][:75]}" for p in projects]
        selected = {self.reference_ids[i] for i in self.references.curselection() if i < len(self.reference_ids)}
        self.reference_ids = [p['id'] for p in projects]
        self.references.delete(0, 'end')
        for i, p in enumerate(projects):
            self.references.insert('end', f"{p['id']} | {p['topic'][:85]}")
            if p['id'] in selected:
                self.references.selection_set(i)
        if self.current in self.project_ids:
            self.projects.current(self.project_ids.index(self.current))

    def select(self, _event=None):
        index = self.projects.current()
        if index < 0 or self.busy():
            return
        self.current = self.project_ids[index]
        s = self.store.load(self.current)
        self.display_settings(Settings.from_saved(s['settings']))
        self.topic.delete('1.0', 'end')
        self.topic.insert('1.0', s['topic'])
        self.instructions.delete('1.0', 'end')
        self.instructions.insert('1.0', s.get('instructions', ''))
        self.update_input_usage()
        self.references.selection_clear(0, 'end')
        for i, pid in enumerate(self.reference_ids):
            if pid in s.get('reference_projects', []):
                self.references.selection_set(i)
        self.selection_signature = (s['topic'], s.get('instructions', ''), self.read_settings().to_dict(),
                                    tuple(s.get('reference_projects', [])))
        self.view_signature = None

    def start(self):
        if self.busy():
            return
        try:
            topic = self.topic.get('1.0', 'end').strip()
            instructions = self.instructions.get('1.0', 'end').strip()
            validate_research_input(topic, instructions)
            cfg = self.read_settings()
            preflight_research_start(cfg,topic,instructions,version=6)
            references = [self.reference_ids[i] for i in self.references.curselection()]
            pid = self.store.create_v06(topic, cfg, instructions, reference_projects=references)
            self.current = pid
            self.selection_signature = (topic, instructions, cfg.to_dict(), tuple(references))
            self.view_signature = None
            self.refresh_projects()
            self.run_worker(pid)
        except (ValueError, TypeError, KeyError) as exc:
            # Input/setting failures are not dependency installation failures.
            messagebox.showerror('입력 또는 설정 오류', str(exc))
        except Exception as exc:
            messagebox.showerror('연구 시작 실패', str(exc))

    def continue_v05(self):
        if self.busy() or not self.current:
            return
        try:
            original = self.store.load(self.current)
            if original.get('engine_version',4) >= 5:
                messagebox.showinfo('이미 v0.5 프로젝트', '선택 연구 재개를 사용하세요.')
                return
            cfg = self.read_settings()
            pid = self.store.continue_as_v05(self.current,cfg)
            self.current = pid
            self.selection_signature = None
            self.view_signature = None
            self.refresh_projects()
            self.select()
            self.run_worker(pid)
        except Exception as exc:
            messagebox.showerror('v0.5 이어서 조사 실패', str(exc))

    def continue_v06(self):
        if self.busy() or not self.current:return
        try:
            original=self.store.load(self.current)
            if original.get('engine_version',4)>=6:
                messagebox.showinfo('Already v0.6','Use resume for this project.');return
            pid=self.store.continue_as_v06(self.current,self.read_settings())
            self.current=pid;self.selection_signature=None;self.view_signature=None
            self.refresh_projects();self.select();self.run_worker(pid)
        except Exception as exc:messagebox.showerror('v0.6 continuation failed',str(exc))

    def resume(self):
        if self.busy() or not self.current:
            return
        try:
            s = self.store.load(self.current)
            if s.get('engine_version',4) < 5:
                messagebox.showinfo('Legacy project', 'Use 0.6 continuation to preserve the original project.')
                return
            if s['stage'] == 'done':
                messagebox.showinfo('이미 종료된 연구', '추가 조사는 새 연구로 시작하세요. 기존 근거와 보고서는 그대로 보존됩니다.')
                return
            edited = (self.topic.get('1.0', 'end').strip(), self.instructions.get('1.0', 'end').strip(),
                      self.read_settings().to_dict(),
                      tuple(self.reference_ids[i] for i in self.references.curselection()))
            stored = (s['topic'], s.get('instructions', ''), Settings.from_saved(s['settings']).to_dict(),
                      tuple(s.get('reference_projects', [])))
            if edited != stored:
                messagebox.showwarning('설정 변경 감지', '재개는 저장된 주제·추가 지시·참조 선택·설정을 그대로 사용합니다.\n변경 내용을 사용하려면 새 연구를 시작하세요.')
                return
            # Resume uses the immutable recorded question and source policy.  A
            # changed topic/policy requires a new project rather than mixing data.
            # Acquire the same worker lock before resetting elapsed/terminal
            # status: another GUI process must not mutate a live job.
            with worker_lock(self.store.project_path(self.current)):
                cfg = Settings.from_saved(s['settings'])
                if s['calls'] >= cfg.max_calls or s['searches'] >= cfg.max_searches:
                    messagebox.showinfo('작업 한도 소진', '호출/검색 한도에 도달했습니다. 한도를 늘린 새 연구로 시작하세요.')
                    return
                if s['status'] == 'TIME_LIMIT':
                    s['elapsed_seconds'] = 0
                s['status'] = 'PENDING'
                self.store.save(s)
                self.store.control(self.current, 'RUN')
            self.view_signature = None
            self.run_worker(self.current)
        except Exception as exc:
            messagebox.showerror('재개 실패', str(exc))

    def run_worker(self, pid):
        self.set_running(True)
        def work():
            try:
                cfg = Settings.from_saved(self.store.load(pid)['settings'])
                model = Ollama(cfg)
                project_store = self.store.open(pid)
                version=self.store.load(pid).get('engine_version',4)
                engine_class=EngineV06 if version>=6 else EngineV05 if version>=5 else Engine
                web=WebV06(cfg) if version>=6 else Web(cfg)
                engine_class(project_store, model, web).run(pid)
                self.messages.put(('done', pid))
            except Exception as exc:
                self.messages.put(('error', str(exc)))
        self.thread = threading.Thread(target=work, daemon=False)
        self.thread.start()

    def control(self, action):
        if self.current and self.busy():
            self.store.control(self.current, action)
            self.status.set('중지 요청 저장됨 — 진행 중 요청을 취소하고 체크포인트를 저장합니다.')

    def check_model(self):
        def check():
            try:
                with urlopen('http://localhost:11434/api/tags', timeout=5) as response:
                    names = [m['name'] for m in json.load(response).get('models', [])]
                self.messages.put(('models', names))
            except Exception as exc:
                self.messages.put(('error', 'Ollama 실행/모델 설치를 확인하세요: ' + str(exc)))
        threading.Thread(target=check, daemon=True).start()

    def open_results(self):
        if not self.current:
            return
        try:
            folder = self.store.project_path(self.current).resolve()
            # Never publish a final handoff while the worker is mutating its DB.
            # During execution this action only opens existing snapshots.
            if not self.busy():
                with worker_lock(folder):
                    folder = self.store.export(self.current).resolve()
            if hasattr(os, 'startfile'):
                os.startfile(str(folder))
            else:
                webbrowser.open(folder.as_uri())
        except Exception as exc:
            messagebox.showerror('결과 폴더', str(exc))

    def render(self, s):
        events = self.store.events(s['id'], 80)
        signature = (s['id'], s['status'], s['calls'], s['searches'], s.get('last_error'),
                     events[-1]['time'] if events else '', events[-1]['message'] if events else '',
                     tuple((t['id'], t['status'], t['attempts'], len(t['evidence_ids'])) for t in s['tasks']))
        if signature == self.view_signature:
            return
        self.view_signature = signature
        version=s.get('engine_version',4)
        st=self.store.open(s['id']) if version>=5 else None
        overall=metrics.project_metrics(st) if st else {}
        self.tasks.delete(*self.tasks.get_children())
        for t in s['tasks']:
            tm=metrics.task_metrics(st,t['id']) if st else {'searches':t['attempts'],'fetched':len(t.get('document_ids',[])),'relevant':0,'zero_yield':0}
            self.tasks.insert('', 'end', text=t['title'], values=(t['status'], tm['searches'],tm['fetched'],tm['relevant'],len(t['evidence_ids']),tm['zero_yield']))
        ratio=overall.get('productive_extraction_ratio')
        ratio_text='-' if ratio is None else f'{ratio:.1%}'
        self.status.set(f"{s['status']} | 모델 {s['calls']} · 검색 {s['searches']} · 오류 {s['errors']} | "
                        f"후보 {overall.get('hits_screened','-')} / 제외 {overall.get('prefilter_rejected','-')} / "
                        f"중복절약 {overall.get('duplicate_fetch_avoided','-')} / 무관추출 {overall.get('irrelevant_extractions','-')} / "
                        f"생산추출 {ratio_text} / 접근실패 {overall.get('fetch_failures','-')} | {s.get('last_error','')[:100]}")
        if version>=6:
            from . import pm_v06_store as audit
            rm=audit.summary(st)
            self.efficiency.set('v0.6.2 / '+s['settings'].get('optimization_mode','balanced')+
                f" | deferred pages {rm['deferred_documents']} | reused bodies {rm['duplicate_bodies_avoided']} | "
                f"unread chars {rm['unread_characters']} | decisions {rm['retrieval_decisions']} | "
                'observed token totals and claims/token: efficiency_metrics.json (not accuracy)')
        else:self.efficiency.set('Legacy engine preserved; create a v0.6 continuation to compare retrieval.')
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.insert('end', '\n'.join(f"{e['time']} [{e['kind']}] {e['message']}" for e in events))
        self.log.see('end')
        self.log.configure(state='disabled')

    def refresh(self):
        while True:
            try:
                kind, data = self.messages.get_nowait()
            except queue.Empty:
                break
            if kind == 'error':
                messagebox.showerror('Research PM', data)
            elif kind == 'models':
                self.models['values'] = data
                self.status.set('설치 모델: ' + ', '.join(data))
        running = self.busy()
        self.set_running(running)
        if not running:
            self.refresh_projects()
        if self.current:
            self.render(self.store.load(self.current))
        if self.closing and not running:
            self.root.destroy()
            return
        self.root.after(1000, self.refresh)

    def close(self):
        if self.busy():
            if messagebox.askyesno('종료', '현재 요청을 취소하고 저장한 뒤 종료할까요?'):
                self.closing = True
                self.control('STOP')
        else:
            self.root.destroy()


def main():
    from dotenv import load_dotenv
    load_dotenv()
    os.environ['LANGSMITH_TRACING'] = 'false'
    os.environ['LANGCHAIN_TRACING_V2'] = 'false'
    root = tk.Tk()
    ttk.Style(root).theme_use('clam')
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
