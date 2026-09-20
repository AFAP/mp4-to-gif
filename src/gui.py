"""MP4 → 透明 GIF / WebP，图形界面版。

把命令行那套经验做成双击即用的工具：拖入 mp4、选好尺寸和体积上限、点开始。

界面部分：扁平卡片式布局，统一配色与字体，日志按级别上色。
功能与命令行模式（--cli）保持不变。
"""
from __future__ import annotations

import os
import queue
import sys
import threading
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import engine
from engine import Options

APP_TITLE = 'MP4 → 透明 GIF / WebP'
APP_SUB = '把视频转成带透明背景的动图，内置 ffmpeg，不需要额外安装'

PRESETS = [
    ('微信表情 240×240', 240, 240),
    ('方形 400×400', 400, 400),
    ('竖版 2:3 · 400×600', 400, 600),
    ('竖版 2:3 · 240×360', 240, 360),
    ('竖版 3:4 · 480×640', 480, 640),
    ('横版 16:9 · 640×360', 640, 360),
    ('横版 16:9 · 960×540', 960, 540),
    ('自定义', 0, 0),
]

# ---- 配色 ----
BG      = '#F3F4F6'   # 窗体底色
CARD    = '#FFFFFF'   # 卡片
LINE    = '#E2E4E9'   # 卡片描边
TXT     = '#1F2329'   # 主文字
SUB     = '#8A9099'   # 次要文字
PRIMARY = '#2F6FED'   # 主色
PRIM_HI = '#1F5BD8'
DANGER  = '#D9534F'
LOG_BG  = '#FBFCFD'

F   = ('Microsoft YaHei UI', 9)
FB  = ('Microsoft YaHei UI', 9, 'bold')
FH  = ('Microsoft YaHei UI', 15, 'bold')
FS  = ('Microsoft YaHei UI', 8)
FM  = ('Consolas', 9)


def _font_ok(name: str) -> str:
    try:
        from tkinter import font as tkfont
        if name in tkfont.families():
            return name
    except Exception:
        pass
    return 'Microsoft YaHei'


class Card(tk.Frame):
    """白底卡片：一条细描边 + 标题。"""

    def __init__(self, master, title: str, sub: str = ''):
        super().__init__(master, bg=LINE, bd=0, highlightthickness=0)
        inner = tk.Frame(self, bg=CARD)
        inner.pack(fill='both', expand=True, padx=1, pady=1)
        head = tk.Frame(inner, bg=CARD)
        head.pack(fill='x', padx=14, pady=(11, 0))
        tk.Label(head, text=title, bg=CARD, fg=TXT, font=FB).pack(side='left')
        if sub:
            tk.Label(head, text=sub, bg=CARD, fg=SUB, font=FS).pack(side='left', padx=(8, 0))
        self.body = tk.Frame(inner, bg=CARD)
        self.body.pack(fill='both', expand=True, padx=14, pady=(8, 12))


def _enable_dpi_awareness() -> None:
    """必须在创建 Tk() 之前调用。

    Windows 默认按 150% 之类缩放显示，tkinter 若不开 DPI 感知，
    geometry('1060x800') 会被系统放大成 1590 物理像素，窗口右边直接跑到屏幕外。
    """
    try:
        from ctypes import windll
        try:
            windll.shcore.SetProcessDpiAwareness(1)      # SYSTEM_DPI_AWARE
        except Exception:
            windll.user32.SetProcessDPIAware()
    except Exception:
        pass


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.files: list[str] = []
        self.worker: threading.Thread | None = None
        self.stop_flag = threading.Event()
        self.msgq: queue.Queue = queue.Queue()
        self.adv_open = tk.BooleanVar(value=False)
        self.last_out_dir = ''           # 最近一次实际写入的输出目录

        root.title(APP_TITLE)
        root.configure(bg=BG)
        self._setup_style()
        self._build_ui()

        # 按内容需求定尺寸，再居中；避免超出屏幕
        root.update_idletasks()
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w = min(max(root.winfo_reqwidth(), 980), sw - 140)
        h = min(max(root.winfo_reqheight(), 720), sh - 160)
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 3)
        root.geometry(f'{w}x{h}+{x}+{y}')
        root.minsize(900, 640)
        self._base_h = h

        self._drain()

    # ------------------------------------------------------------------ 主题
    def _setup_style(self):
        st = ttk.Style()
        try:
            st.theme_use('clam')
        except Exception:
            pass
        st.configure('.', background=BG, foreground=TXT, font=F)
        st.configure('TFrame', background=BG)
        st.configure('TLabel', background=BG, foreground=TXT, font=F)
        st.configure('Sub.TLabel', foreground=SUB, font=FS)
        st.configure('Card.TLabel', background=CARD, foreground=TXT, font=F)
        st.configure('CardSub.TLabel', background=CARD, foreground=SUB, font=FS)
        st.configure('CardBold.TLabel', background=CARD, foreground=TXT, font=FB)

        st.configure('TEntry', fieldbackground='#FFFFFF', bordercolor=LINE,
                     lightcolor=LINE, darkcolor=LINE, insertcolor=TXT, padding=4)
        st.configure('TCombobox', fieldbackground='#FFFFFF', background='#FFFFFF',
                     bordercolor=LINE, lightcolor=LINE, darkcolor=LINE, padding=4,
                     arrowcolor=SUB)
        st.map('TCombobox', fieldbackground=[('readonly', '#FFFFFF')])

        st.configure('TCheckbutton', background=CARD, foreground=TXT, font=F, focuscolor=CARD)
        st.map('TCheckbutton', background=[('active', CARD)])
        st.configure('TRadiobutton', background=CARD, foreground=TXT, font=F, focuscolor=CARD)
        st.map('TRadiobutton', background=[('active', CARD)])

        st.configure('TButton', padding=(12, 6), font=F, borderwidth=0,
                     background='#FFFFFF', foreground=TXT)
        st.map('TButton',
               background=[('active', '#EDEFF3'), ('disabled', '#F5F6F8')],
               foreground=[('disabled', '#B9BEC7')])

        st.configure('Primary.TButton', background=PRIMARY, foreground='#FFFFFF',
                     font=FB, padding=(18, 8))
        st.map('Primary.TButton',
               background=[('active', PRIM_HI), ('disabled', '#AFC4F4')],
               foreground=[('disabled', '#FFFFFF')])

        st.configure('Danger.TButton', background='#FFFFFF', foreground=DANGER, padding=(14, 6))
        st.map('Danger.TButton', background=[('active', '#FBEDEC'), ('disabled', '#F5F6F8')],
               foreground=[('disabled', '#D9B4B2')])

        st.configure('Link.TButton', background=CARD, foreground=PRIMARY, font=FS, padding=(4, 2))
        st.map('Link.TButton', background=[('active', CARD)], foreground=[('active', PRIM_HI)])

        st.configure('Bar.Horizontal.TProgressbar', background=PRIMARY, troughcolor='#E7E9EE',
                     borderwidth=0, lightcolor=PRIMARY, darkcolor=PRIMARY, thickness=8)

    # ------------------------------------------------------------------ 界面
    def _build_ui(self):
        root = self.root

        # 顶栏
        head = tk.Frame(root, bg=CARD)
        head.pack(fill='x')
        hb = tk.Frame(head, bg=CARD)
        hb.pack(fill='x', padx=22, pady=14)
        tk.Label(hb, text=APP_TITLE, bg=CARD, fg=TXT, font=FH).pack(anchor='w')
        tk.Label(hb, text=APP_SUB, bg=CARD, fg=SUB, font=FS).pack(anchor='w', pady=(2, 0))
        tk.Frame(root, bg=LINE, height=1).pack(fill='x')

        body = tk.Frame(root, bg=BG)
        body.pack(fill='both', expand=True, padx=18, pady=14)

        # 1 选择视频
        c1 = Card(body, '1  选择视频', '可多选，也可以整个文件夹')
        c1.pack(fill='x')
        r = tk.Frame(c1.body, bg=CARD)
        r.pack(fill='x')
        ttk.Button(r, text='选择 mp4 文件…', command=self.pick_files).pack(side='left')
        ttk.Button(r, text='选择文件夹…', command=self.pick_dir).pack(side='left', padx=6)
        ttk.Button(r, text='清空', command=self.clear_files).pack(side='left')
        self.lbl_files = ttk.Label(r, text='尚未选择', style='CardSub.TLabel')
        self.lbl_files.pack(side='left', padx=12)

        lw = tk.Frame(c1.body, bg=LINE)
        lw.pack(fill='x', pady=(9, 0))
        self.listbox = tk.Listbox(lw, height=4, activestyle='none', bd=0,
                                  highlightthickness=0, bg='#FBFCFD', fg=TXT, font=F,
                                  selectbackground='#DCE7FF', selectforeground=TXT)
        sb1 = ttk.Scrollbar(lw, command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb1.set)
        sb1.pack(side='right', fill='y')
        self.listbox.pack(side='left', fill='both', expand=True, padx=1, pady=1)

        # 2 输出设置
        c2 = Card(body, '2  输出设置')
        c2.pack(fill='x', pady=(12, 0))
        g = c2.body

        def row(parent, top=6, bottom=6):
            f = tk.Frame(parent, bg=CARD)
            f.pack(fill='x', pady=(top, bottom))
            return f

        r1 = row(g)
        ttk.Label(r1, text='尺寸预设', style='Card.TLabel').pack(side='left')
        self.var_preset = tk.StringVar(value=PRESETS[0][0])
        cb = ttk.Combobox(r1, textvariable=self.var_preset, values=[p[0] for p in PRESETS],
                          state='readonly', width=19)
        cb.pack(side='left', padx=(10, 0))
        cb.bind('<<ComboboxSelected>>', self.on_preset)
        wh = tk.Frame(r1, bg=CARD)
        wh.pack(side='right')
        ttk.Label(wh, text='宽 × 高', style='Card.TLabel').pack(side='left')
        self.var_w = tk.StringVar(value='240')
        self.var_h = tk.StringVar(value='240')
        ttk.Entry(wh, textvariable=self.var_w, width=7).pack(side='left', padx=(10, 3))
        ttk.Label(wh, text='×', style='Card.TLabel').pack(side='left')
        ttk.Entry(wh, textvariable=self.var_h, width=7).pack(side='left', padx=(3, 0))

        r2 = row(g)
        ttk.Label(r2, text='体积上限', style='Card.TLabel').pack(side='left')
        self.var_limit = tk.StringVar(value='500')
        ttk.Entry(r2, textvariable=self.var_limit, width=9).pack(side='left', padx=(10, 8))
        ttk.Label(r2, text='KB', style='Card.TLabel').pack(side='left')
        ttk.Label(r2, text='填 0 = 不限体积', style='CardSub.TLabel').pack(side='left', padx=(14, 0))

        r3 = row(g, 6, 0)
        ttk.Label(r3, text='输出格式', style='Card.TLabel').pack(side='left')
        self.var_fmt = tk.StringVar(value='gif')
        ttk.Radiobutton(r3, text='GIF', variable=self.var_fmt, value='gif').pack(side='left', padx=(10, 0))
        ttk.Radiobutton(r3, text='WebP', variable=self.var_fmt, value='webp').pack(side='left', padx=(14, 6))
        ttk.Label(r3, text='真透明 + 全彩，同体积画质更好，推荐',
                  style='CardSub.TLabel').pack(side='left')

        # 3 高级
        c3 = Card(body, '3  高级')
        c3.pack(fill='x', pady=(12, 0))
        hd = tk.Frame(c3.body, bg=CARD)
        hd.pack(fill='x')
        self.btn_adv = ttk.Button(hd, text='展开设置 ▾', style='Link.TButton', command=self.toggle_adv)
        self.btn_adv.pack(side='left')
        ttk.Label(hd, text='一般不用改', style='CardSub.TLabel').pack(side='left', padx=(8, 0))

        self.adv = tk.Frame(c3.body, bg=CARD)
        a = self.adv

        def arow(top=6, bottom=6):
            f = tk.Frame(a, bg=CARD)
            f.pack(fill='x', pady=(top, bottom))
            return f

        self.var_bg = tk.BooleanVar(value=True)
        a0 = arow(10, 4)
        # clam 主题的 ttk.Checkbutton 会画成一个带 × 的方块，很难看；
        # 改用原生 tk.Checkbutton，指示器是正常的方框 + 勾。
        tk.Checkbutton(a0, text='自动抠掉白色背景（生成透明底）', variable=self.var_bg,
                       bg=CARD, fg=TXT, activebackground=CARD, activeforeground=TXT,
                       selectcolor='#FFFFFF', font=F, bd=0, highlightthickness=0,
                       anchor='w', padx=0, cursor='hand2').pack(side='left')

        a1 = arow()
        ttk.Label(a1, text='体积不足时优先', style='Card.TLabel').pack(side='left')
        self.var_prefer = tk.StringVar(value='fps')
        ttk.Radiobutton(a1, text='保帧率', variable=self.var_prefer, value='fps').pack(side='left', padx=(10, 0))
        ttk.Radiobutton(a1, text='保色彩', variable=self.var_prefer, value='color').pack(side='left', padx=(14, 8))
        ttk.Label(a1, text='保帧率=先降色数；保色彩=先降帧率',
                  style='CardSub.TLabel').pack(side='left')

        a2 = arow()
        ttk.Label(a2, text='亮度', style='Card.TLabel').pack(side='left')
        self.var_bri = tk.StringVar(value='1.00')
        ttk.Entry(a2, textvariable=self.var_bri, width=7).pack(side='left', padx=(10, 6))
        ttk.Label(a2, text='饱和度', style='Card.TLabel').pack(side='left', padx=(16, 0))
        self.var_sat = tk.StringVar(value='1.00')
        ttk.Entry(a2, textvariable=self.var_sat, width=7).pack(side='left', padx=(10, 6))
        ttk.Label(a2, text='灰调素材（石头、白墙）提亮度更有效',
                  style='CardSub.TLabel').pack(side='left')

        a3 = arow()
        ttk.Label(a3, text='调色板饱和', style='Card.TLabel').pack(side='left')
        self.var_psat = tk.StringVar(value='1.30')
        ttk.Entry(a3, textvariable=self.var_psat, width=7).pack(side='left', padx=(10, 6))
        ttk.Label(a3, text='只改调色板、体积零增长；1.30 合适，1.5 偏橘',
                  style='CardSub.TLabel').pack(side='left')

        a4 = arow()
        ttk.Label(a4, text='抠像阈值', style='Card.TLabel').pack(side='left')
        self.var_thr = tk.StringVar(value='244')
        ttk.Entry(a4, textvariable=self.var_thr, width=7).pack(side='left', padx=(10, 6))
        ttk.Label(a4, text='白描边宽度', style='Card.TLabel').pack(side='left', padx=(16, 0))
        self.var_key = tk.StringVar(value='2')
        ttk.Entry(a4, textvariable=self.var_key, width=7).pack(side='left', padx=(10, 6))
        ttk.Label(a4, text='背景没抠净调高阈值，主体被吃掉调低',
                  style='CardSub.TLabel').pack(side='left')

        a5 = arow(6, 0)
        ttk.Label(a5, text='WebP 质量', style='Card.TLabel').pack(side='left')
        self.var_wq = tk.StringVar(value='80')
        ttk.Entry(a5, textvariable=self.var_wq, width=7).pack(side='left', padx=(10, 6))

        # 4 保存到
        c4 = Card(body, '4  保存到')
        c4.pack(fill='x', pady=(12, 0))
        r4 = tk.Frame(c4.body, bg=CARD)
        r4.pack(fill='x')
        self.var_out = tk.StringVar(value='')
        ttk.Entry(r4, textvariable=self.var_out).pack(side='left', fill='x', expand=True)
        ttk.Button(r4, text='浏览…', command=self.pick_out).pack(side='left', padx=(8, 0))
        ttk.Label(c4.body, text='留空 = 每段视频自己所在目录下的 gif\\ 或 webp\\ 子目录',
                  style='CardSub.TLabel').pack(anchor='w', pady=(6, 0))

        # 操作条
        bar = tk.Frame(body, bg=BG)
        bar.pack(fill='x', pady=(14, 0))
        self.btn_run = ttk.Button(bar, text='开始转换', style='Primary.TButton', command=self.start)
        self.btn_run.pack(side='left')
        self.btn_stop = ttk.Button(bar, text='停止', style='Danger.TButton', command=self.stop,
                                   state='disabled')
        self.btn_stop.pack(side='left', padx=8)
        self.var_check = tk.BooleanVar(value=True)
        tk.Checkbutton(bar, text='转换后校验透明通道', variable=self.var_check,
                       bg=BG, fg=TXT, activebackground=BG, activeforeground=TXT,
                       selectcolor='#FFFFFF', font=F, bd=0, highlightthickness=0,
                       cursor='hand2').pack(side='left', padx=(14, 0))
        self.btn_open = ttk.Button(bar, text='打开输出目录', command=self.open_out)
        self.btn_open.pack(side='right')

        self.pb = ttk.Progressbar(body, style='Bar.Horizontal.TProgressbar', mode='determinate')
        self.pb.pack(fill='x', pady=(12, 4))
        self.lbl_status = ttk.Label(body, text='就绪', style='Sub.TLabel')
        self.lbl_status.pack(anchor='w')

        # 日志
        cl = Card(body, '日志')
        cl.pack(fill='both', expand=True, pady=(10, 0))
        lw2 = tk.Frame(cl.body, bg=LINE)
        lw2.pack(fill='both', expand=True)
        self.log = tk.Text(lw2, height=8, wrap='word', state='disabled', bd=0,
                           highlightthickness=0, bg=LOG_BG, fg=TXT, font=F,
                           padx=10, pady=8, insertbackground=TXT)
        sb2 = ttk.Scrollbar(lw2, command=self.log.yview)
        self.log.configure(yscrollcommand=sb2.set)
        sb2.pack(side='right', fill='y')
        self.log.pack(side='left', fill='both', expand=True, padx=1, pady=1)
        self.log.tag_configure('ok', foreground='#1B8A4B')
        self.log.tag_configure('err', foreground=DANGER)
        self.log.tag_configure('warn', foreground='#C77700')
        self.log.tag_configure('dim', foreground=SUB)
        self.log.tag_configure('head', foreground=TXT, font=FB)

    def toggle_adv(self):
        if self.adv_open.get():
            self.adv.pack_forget()
            self.btn_adv.configure(text='展开设置 ▾')
            self.adv_open.set(False)
            self.root.geometry(f'{self.root.winfo_width()}x{self._base_h}')
        else:
            self.adv.pack(fill='x')
            self.btn_adv.configure(text='收起设置 ▴')
            self.adv_open.set(True)
            self.root.geometry(f'{self.root.winfo_width()}x{self._base_h + 210}')

    # ------------------------------------------------------------- 回调
    def on_preset(self, _=None):
        for name, w, h in PRESETS:
            if name == self.var_preset.get():
                if w and h:
                    self.var_w.set(str(w))
                    self.var_h.set(str(h))
                return

    def pick_files(self):
        fs = filedialog.askopenfilenames(
            title='选择 mp4',
            filetypes=[('视频', '*.mp4 *.mov *.mkv *.webm'), ('全部', '*.*')])
        if fs:
            self.files = list(fs)
            self._refresh_files()

    def pick_dir(self):
        d = filedialog.askdirectory(title='选择包含 mp4 的文件夹')
        if not d:
            return
        fs = [os.path.join(d, n) for n in sorted(os.listdir(d))
              if n.lower().endswith(('.mp4', '.mov', '.mkv', '.webm'))]
        if not fs:
            messagebox.showinfo(APP_TITLE, '这个文件夹里没有找到视频文件。')
            return
        self.files = fs
        self._refresh_files()

    def clear_files(self):
        self.files = []
        self._refresh_files()

    def pick_out(self):
        d = filedialog.askdirectory(title='选择输出目录')
        if d:
            self.var_out.set(d)

    def open_out(self):
        """在资源管理器里打开成品所在的目录。

        优先用「4 保存到」里指定的目录，其次是最近一次转换实际写入的目录，
        最后按第一个待转视频推算默认输出目录（视频同级的 gif\ 或 webp\）。
        """
        d = self.var_out.get().strip() or self.last_out_dir
        if not d and self.files:
            d = os.path.join(os.path.dirname(os.path.abspath(self.files[0])), self.var_fmt.get())
        if not d:
            messagebox.showinfo(APP_TITLE,
                                '还没有可打开的目录。\n\n先选好视频转换一次，'
                                '或者在「4 保存到」里指定一个目录。')
            return
        d = os.path.abspath(d)
        if not os.path.isdir(d):
            messagebox.showinfo(APP_TITLE, f'目录还不存在：\n{d}\n\n转换完成后才会有。')
            return
        try:
            os.startfile(d)
        except Exception as e:                       # noqa: BLE001
            messagebox.showerror(APP_TITLE, f'打开目录失败：{e}')

    def _refresh_files(self):
        self.listbox.delete(0, 'end')
        for f in self.files:
            self.listbox.insert('end', '  ' + os.path.basename(f))
        self.lbl_files.configure(
            text=f'已选 {len(self.files)} 个' if self.files else '尚未选择',
            foreground=PRIMARY if self.files else SUB)

    def _write(self, s: str):
        self.log.configure(state='normal')
        tag = ''
        st = s.strip()
        if st.startswith('✓') or '  校验[' in s or 'OK ' in s[:8]:
            tag = 'ok'
        elif st.startswith('✗') or 'FAIL' in s or '失败' in st[:6]:
            tag = 'err'
        elif st.startswith('!') or '注意' in s:
            tag = 'warn'
        elif st.startswith('[') and '/' in st[:8]:
            tag = 'head'
        self.log.insert('end', s + '\n', tag)
        self.log.see('end')
        self.log.configure(state='disabled')

    def _drain(self):
        try:
            while True:
                kind, payload = self.msgq.get_nowait()
                if kind == 'log':
                    self._write(payload)
                elif kind == 'status':
                    self.lbl_status.configure(text=payload)
                elif kind == 'progress':
                    done, total = payload
                    self.pb.configure(maximum=total, value=done)
                elif kind == 'outdir':
                    self.last_out_dir = payload
                elif kind == 'done':
                    self.btn_run.configure(state='normal')
                    self.btn_stop.configure(state='disabled')
                    self.worker = None
                    if payload:
                        messagebox.showinfo(APP_TITLE, payload)
        except queue.Empty:
            pass
        self.root.after(120, self._drain)

    # --------------------------------------------------------------- 运行
    def _options(self) -> Options:
        def num(var, dflt, cast=float):
            try:
                return cast(var.get())
            except Exception:
                return dflt
        return Options(
            out_width=int(num(self.var_w, 240, int)),
            out_height=int(num(self.var_h, 240, int)),
            limit_kb=num(self.var_limit, 500.0),
            prefer=self.var_prefer.get(),
            threshold=int(num(self.var_thr, 244, int)),
            keyline=int(num(self.var_key, 2, int)),
            palette_sat=num(self.var_psat, 1.30),
            brightness=num(self.var_bri, 1.0),
            saturation=num(self.var_sat, 1.0),
            keep_background=not self.var_bg.get(),
            webp_quality=int(num(self.var_wq, 80, int)),
        )

    def start(self):
        if self.worker:
            return
        if not self.files:
            messagebox.showwarning(APP_TITLE, '请先选择至少一个 mp4 文件。')
            return
        opt = self._options()
        if opt.out_width < 8 or opt.out_height < 8:
            messagebox.showwarning(APP_TITLE, '输出尺寸太小。')
            return
        fmt = self.var_fmt.get()
        out_root = self.var_out.get().strip()
        do_check = self.var_check.get()
        self.stop_flag.clear()
        self.btn_run.configure(state='disabled')
        self.btn_stop.configure(state='normal')
        self.pb.configure(value=0, maximum=len(self.files))
        self.worker = threading.Thread(target=self._work,
                                       args=(list(self.files), opt, fmt, out_root, do_check),
                                       daemon=True)
        self.worker.start()

    def stop(self):
        self.stop_flag.set()
        self.msgq.put(('log', '…… 已请求停止，当前这条做完就停'))

    def _work(self, files, opt, fmt, out_root, do_check):
        q = self.msgq
        try:
            ff = engine.find_ffmpeg()
            q.put(('log', f'ffmpeg  {ff}'))
        except Exception as e:                       # noqa: BLE001
            q.put(('done', f'找不到 ffmpeg：{e}'))
            return
        q.put(('log', f'输出格式 {fmt.upper()}   尺寸 {opt.out_width}×{opt.out_height}   '
                      f'上限 {"不限" if opt.limit_kb <= 0 else str(int(opt.limit_kb)) + " KB"}'))

        ok = bad = 0
        for i, f in enumerate(files, 1):
            if self.stop_flag.is_set():
                break
            name = os.path.basename(f)
            q.put(('status', f'({i}/{len(files)}) {name}'))
            q.put(('log', f'[{i}/{len(files)}] {name}'))
            out_dir = out_root or os.path.join(os.path.dirname(os.path.abspath(f)), fmt)
            q.put(('outdir', out_dir))
            try:
                r = engine.convert(f, out_dir, opt, fmt=fmt, log=lambda s: q.put(('log', s)))
            except Exception:                        # noqa: BLE001
                q.put(('log', traceback.format_exc()))
                bad += 1
                q.put(('progress', (i, len(files))))
                continue
            if r.ok:
                ok += 1
                q.put(('log', f'  ✓ {r.message}   体积 {r.kb:.0f} KB'))
                if r.kb > opt.limit_kb > 0:
                    q.put(('log', f'  ! 已到阶梯末端仍超过 {opt.limit_kb:.0f} KB'))
                q.put(('log', f'  → {r.out}'))
                if do_check and fmt == 'gif':
                    try:
                        c = engine.check_gif(r.out)
                        flag = '通过' if (c['transparent'] and c['stable']) else '注意'
                        q.put(('log', f'  校验[{flag}] 帧数 {c["frames"]}  透明索引 {c["transparency"]}'
                                      f'  背景占比 {c["min_bg"]:.0f}%–{c["max_bg"]:.0f}%'))
                    except Exception as e:           # noqa: BLE001
                        q.put(('log', f'  校验失败: {e}'))
            else:
                bad += 1
                q.put(('log', f'  ✗ {r.message}'))
            q.put(('progress', (i, len(files))))

        summary = f'完成：成功 {ok} 个' + (f'，失败 {bad} 个' if bad else '')
        q.put(('status', summary))
        q.put(('log', summary))
        q.put(('done', summary))


def cli(argv: list[str]) -> int:
    """命令行模式：无界面批量转换，便于脚本调用和自动化测试。

    用法：mp4togif.exe --cli <文件或文件夹> [选项]
      --out <目录>        输出目录（默认视频同级的 gif/ 或 webp/）
      --size 240x240     输出尺寸
      --limit 500        体积上限 KB（0=不限）
      --fmt gif|webp     输出格式
      --prefer fps|color 优先保帧率还是保色彩
      --brightness 1.1   提亮
      --saturation 1.1   提饱和度
      --palette-sat 1.3  调色板饱和度（体积零增长）
      --keep-bg          不抠底
      --log <文件>       日志文件（默认输出目录下的 _转换日志.txt）
    """
    import argparse
    ap = argparse.ArgumentParser(prog='mp4togif --cli', add_help=True)
    ap.add_argument('input')
    ap.add_argument('--out', default='')
    ap.add_argument('--size', default='240x240')
    ap.add_argument('--limit', type=float, default=500.0)
    ap.add_argument('--fmt', default='gif', choices=['gif', 'webp'])
    ap.add_argument('--prefer', default='fps', choices=['fps', 'color'])
    ap.add_argument('--brightness', type=float, default=1.0)
    ap.add_argument('--saturation', type=float, default=1.0)
    ap.add_argument('--palette-sat', type=float, default=1.30)
    ap.add_argument('--threshold', type=int, default=244)
    ap.add_argument('--keyline', type=int, default=2)
    ap.add_argument('--webp-quality', type=int, default=80)
    ap.add_argument('--keep-bg', action='store_true')
    ap.add_argument('--log', default='')
    a = ap.parse_args(argv)

    w, _, h = a.size.lower().partition('x')
    opt = Options(out_width=int(w), out_height=int(h or w), limit_kb=a.limit,
                  prefer=a.prefer, threshold=a.threshold, keyline=a.keyline,
                  brightness=a.brightness, saturation=a.saturation,
                  palette_sat=a.palette_sat, keep_background=a.keep_bg,
                  webp_quality=a.webp_quality)

    src = os.path.abspath(a.input)
    if os.path.isdir(src):
        files = [os.path.join(src, n) for n in sorted(os.listdir(src))
                 if n.lower().endswith(('.mp4', '.mov', '.mkv', '.webm'))]
    else:
        files = [src]
    if not files:
        _safe_print('没有找到视频文件')
        return 2

    out_dir = a.out or os.path.join(os.path.dirname(files[0]), a.fmt)
    os.makedirs(out_dir, exist_ok=True)
    log_path = a.log or os.path.join(out_dir, '_转换日志.txt')
    lines: list[str] = []

    def log(s: str):
        lines.append(s)
        _safe_print(s)

    log(f'ffmpeg: {engine.find_ffmpeg()}')
    log(f'输出: {out_dir}   共 {len(files)} 个文件')
    ok = bad = 0
    for i, f in enumerate(files, 1):
        log(f'[{i}/{len(files)}] {os.path.basename(f)}')
        r = engine.convert(f, out_dir, opt, fmt=a.fmt, log=log)
        if r.ok:
            ok += 1
            log(f'  OK  {r.message}  {r.kb:.0f} KB -> {os.path.basename(r.out)}')
            if a.fmt == 'gif':
                c = engine.check_gif(r.out)
                log(f'  校验 帧数={c["frames"]} 透明索引={c["transparency"]} '
                    f'背景占比={c["min_bg"]:.0f}%-{c["max_bg"]:.0f}% '
                    f'{"通过" if c["transparent"] and c["stable"] else "注意"}')
        else:
            bad += 1
            log(f'  FAIL  {r.message}')
    log(f'完成：成功 {ok}，失败 {bad}')
    try:
        with open(log_path, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(lines) + '\n')
        _safe_print('日志: ' + log_path)
    except Exception:
        pass
    return 0 if bad == 0 else 1


def _safe_print(s: str) -> None:
    """--windowed 打包后 sys.stdout 是 None，print 会抛异常。"""
    try:
        if sys.stdout is not None:
            print(s)
    except Exception:
        pass


def _boot_log(msg: str) -> None:
    """最早的启动诊断：--windowed 打包后没有 stdout，出问题只能靠这个文件。"""
    try:
        import time
        p = os.path.join(os.environ.get('TEMP', '.'), 'mp4togif_boot.log')
        with open(p, 'a', encoding='utf-8') as fh:
            fh.write(f'[{time.strftime("%F %T")}] {msg}\n')
    except Exception:
        pass


def main():
    _boot_log(f'start argv={sys.argv!r} frozen={getattr(sys, "frozen", False)}')
    try:
        if len(sys.argv) > 1 and sys.argv[1] == '--cli':
            rc = cli(sys.argv[2:])
            _boot_log(f'cli done rc={rc}')
            raise SystemExit(rc)
        _boot_log(f'entering GUI font={_font_ok("Microsoft YaHei UI")}')
        _enable_dpi_awareness()
        root = tk.Tk()
        App(root)
        root.mainloop()
        _boot_log('GUI closed')
    except SystemExit:
        raise
    except Exception:
        _boot_log('EXCEPTION:\n' + traceback.format_exc())
        raise


if __name__ == '__main__':
    main()
