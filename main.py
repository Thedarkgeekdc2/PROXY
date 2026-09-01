__version__ = "1.0.3"

import os, sys, shutil, socket, threading, time, traceback

# ── Private storage ──────────────────────────────────────────
def get_private_dir():
    try:
        from android.storage import app_storage_path
        p = app_storage_path(); os.makedirs(p, exist_ok=True); return p
    except Exception: pass
    try:
        from jnius import autoclass
        p = autoclass('org.kivy.android.PythonActivity').mActivity.getFilesDir().getAbsolutePath()
        os.makedirs(p, exist_ok=True); return p
    except Exception: pass
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'userdata')
    os.makedirs(p, exist_ok=True); return p

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
PRIVATE_DIR = get_private_dir()
DB_DIR      = os.path.join(PRIVATE_DIR, 'database')
UPLOAD_DIR  = os.path.join(PRIVATE_DIR, 'uploads')
LOG_FILE    = os.path.join(PRIVATE_DIR, 'server.log')
os.makedirs(DB_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

BUNDLED_DB = os.path.join(BASE_DIR, 'database', 'db.sqlite3')
LIVE_DB    = os.path.join(DB_DIR, 'db.sqlite3')
if os.path.exists(BUNDLED_DB) and not os.path.exists(LIVE_DB):
    shutil.copy2(BUNDLED_DB, LIVE_DB)

os.environ['APP_DATA_DIR']  = PRIVATE_DIR
os.environ['DATABASE_URL']  = f'sqlite:///{LIVE_DB}'
os.environ['UPLOAD_FOLDER'] = UPLOAD_DIR
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# ── Shared state ─────────────────────────────────────────────
_state = {'step': 'Init...', 'error': '', 'ready': False}
SERVER_PORT = 5050

def _log(msg):
    _state['step'] = msg
    try:
        with open(LOG_FILE, 'a') as f:
            f.write(f'[{time.strftime("%H:%M:%S")}] {msg}\n')
    except Exception: pass

def _run_server():
    try:
        _log('1/4 socket test...')
        s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(('0.0.0.0', SERVER_PORT)); s.close()
        _log('2/4 importing Flask...')
        import flask; _log('2/4 Flask OK')
        _log('3/4 importing app.py...')
        from app import app as flask_app
        _log('4/4 starting server...')
        from werkzeug.serving import make_server
        srv = make_server('0.0.0.0', SERVER_PORT, flask_app)
        _state['ready'] = True
        _log('SERVER RUNNING')
        srv.serve_forever()
    except Exception as e:
        err = traceback.format_exc()
        _state['error'] = f'{type(e).__name__}: {e}\n\n{err}'
        _log(f'FAILED: {type(e).__name__}: {e}')
        try:
            with open(LOG_FILE, 'a') as f: f.write(err)
        except Exception: pass

threading.Thread(target=_run_server, daemon=True).start()

def server_ready():
    try:
        socket.create_connection(('127.0.0.1', SERVER_PORT), timeout=0.3).close()
        return True
    except OSError: return False

# ── Kivy UI ──────────────────────────────────────────────────
from kivy.app import App
from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.progressbar import ProgressBar
from kivy.uix.scrollview import ScrollView
from kivy.graphics import Color, Rectangle

class SplashScreen(BoxLayout):
    def __init__(self, **kw):
        super().__init__(orientation='vertical', padding=12, spacing=6, **kw)
        with self.canvas.before:
            Color(0.04, 0.07, 0.12, 1)
            self.rect = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._u, size=self._u)

        self.title = Label(text='[b]KVS Proxy System[/b]', markup=True,
                           font_size='20sp', color=(.31,.56,.97,1),
                           size_hint_y=None, height=56)
        self.step  = Label(text='Starting...', font_size='12sp',
                           color=(.8,.8,.8,1), size_hint_y=None, height=32)
        self.pb    = ProgressBar(max=100, value=0, size_hint_y=None, height=8)

        # Error box — shows only on failure
        sv = ScrollView(size_hint=(1, 1))
        self.err_lbl = Label(text='', font_size='10sp', color=(1,.4,.4,1),
                             halign='left', valign='top',
                             text_size=(None, None), size_hint_y=None)
        self.err_lbl.bind(texture_size=lambda i, v: setattr(i, 'height', v[1]))
        sv.add_widget(self.err_lbl)

        self.add_widget(Label(size_hint_y=.15))
        self.add_widget(self.title)
        self.add_widget(self.step)
        self.add_widget(Label(size_hint_y=None, height=8))
        self.add_widget(self.pb)
        self.add_widget(sv)

    def _u(self, *_):
        self.rect.pos = self.pos; self.rect.size = self.size

    def update(self, pct, msg, error=''):
        self.pb.value = pct
        self.step.text = msg
        if error:
            self.err_lbl.text = error
            self.step.color = (1, .4, .4, 1)


class SPS3App(App):
    def build(self):
        self.title = 'KVS Proxy System'
        self._shown = False; self._tick = 0
        self.root_widget = SplashScreen()
        Clock.schedule_interval(self._poll, 0.4)
        return self.root_widget

    def _poll(self, dt):
        self._tick += 1
        elapsed = self._tick * 0.4
        pct = min(88, 5 + self._tick * 2)
        step = _state['step']

        # Show error on screen
        if _state['error']:
            self.root_widget.update(0, f'ERROR — {elapsed:.0f}s', _state['error'])
            Clock.unschedule(self._poll)
            return

        self.root_widget.update(pct, f'{step}  ({elapsed:.0f}s)')

        if server_ready():
            self.root_widget.update(100, 'Ready!')
            Clock.unschedule(self._poll)
            Clock.schedule_once(lambda _: self._open_webview(), 0.4)
            return

        # 45-second hard timeout
        if self._tick > 112 and not self._shown:
            Clock.unschedule(self._poll)
            Clock.schedule_once(lambda _: self._open_webview(), 0.1)

    def _open_webview(self):
        if self._shown: return
        self._shown = True
        try:
            from android.runnable import run_on_ui_thread
            from jnius import autoclass

            @run_on_ui_thread
            def _go():
                try:
                    act  = autoclass('org.kivy.android.PythonActivity').mActivity
                    WV   = autoclass('android.webkit.WebView')
                    WVC  = autoclass('android.webkit.WebViewClient')
                    FLLP = autoclass('android.widget.FrameLayout$LayoutParams')
                    VGLP = autoclass('android.view.ViewGroup$LayoutParams')
                    wv = WV(act)
                    s  = wv.getSettings()
                    s.setJavaScriptEnabled(True)
                    s.setDomStorageEnabled(True)
                    s.setAllowFileAccess(True)
                    s.setAllowContentAccess(True)
                    s.setLoadWithOverviewMode(True)
                    s.setUseWideViewPort(True)
                    s.setMixedContentMode(0)
                    wv.setWebViewClient(WVC())
                    wv.loadUrl(f'http://127.0.0.1:{SERVER_PORT}/')
                    act.addContentView(wv, FLLP(VGLP.MATCH_PARENT, VGLP.MATCH_PARENT))
                    self._wv = wv
                except Exception as e:
                    _log(f'WebView error: {e}')
            _go()
        except Exception:
            self.root_widget.update(100, f'Desktop: http://127.0.0.1:{SERVER_PORT}')

if __name__ == '__main__':
    SPS3App().run()
