"""High-contrast calibration-driven Windows GUI, with global emergency stop."""
from __future__ import annotations
import os
import time
import threading
from pathlib import Path
import cv2
import numpy as np
from PySide6.QtCore import Qt, QThread, Signal, QPoint, QRect, QObject, QTimer
from PySide6.QtGui import QImage,QPixmap,QPainter,QPen,QColor,QFont
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QHBoxLayout,QVBoxLayout,QLabel,QComboBox,
    QPushButton,QGroupBox,QGridLayout,QSpinBox,QDoubleSpinBox,QTextEdit,QSplitter,QScrollArea,
    QMessageBox,QSizePolicy,QFrame,QCheckBox,QFileDialog)
from .model import Settings, Rect, load_settings,save_settings,settings_path
from .vision import Vision,View,write_image,crop
from .engine import FishingEngine,Stage
from .windows import windows, Window,box,focused

CSS='''
QWidget {color:#F0F5FA; background:#101820;font-family:Segoe UI, Arial; font-size:13px;}
QMainWindow {background:#0B121B;}
QGroupBox {background:#172430; border:1px solid #2C4052; border-radius:10px;
 margin-top:13px; padding:10px 7px 8px 7px; font-weight:600;}
QGroupBox::title {subcontrol-origin:margin; left:13px; padding:0 6px; color:#DCE9F6;}
QPushButton {background:#23394B; border:1px solid #426075; padding:9px 11px; border-radius:7px; font-weight:600;}
QPushButton:hover{background:#315268;}
QPushButton:pressed{background:#1D3549;}
QPushButton:disabled{color:#8394A3;background:#1D2731;border:1px solid #2C3846;}
QPushButton#primary{background:#05AB98;color:#041A1A;border:0;}
QPushButton#primary:hover{background:#25D2BC;}
QPushButton#danger{background:#9F3543;color:white;border:0;}
QPushButton#danger:hover{background:#BC4655;}
QComboBox, QSpinBox, QDoubleSpinBox {background:#243440; border:1px solid #415969;
 border-radius:6px;padding:7px 8px;color:white;}
QComboBox QAbstractItemView {background:#1C2E3C;color:white;selection-background-color:#2F6576;}
QTextEdit{background:#0D1721;border:1px solid #294052;border-radius:8px;color:#C3D6E3;}
QLabel#muted{color:#97ACBC;} QLabel#badge{color:#8AFFD8; font-size:14px;font-weight:700;}
QLabel#headline{font-size:23px; font-weight:750; color:white;}
QLabel#step{color:#D4E4F1;font-size:13px;line-height:1.4;}
QFrame#separator{background:#304454;}
'''

class Capture(QThread):
    issue=Signal(str)
    def __init__(self,hwnd:int,fps:int):
        super().__init__()
        self.hwnd=hwnd
        self.fps=fps
        self.running=True
        self._lock=threading.Lock()
        self._latest=None
    def pop_latest(self):
        with self._lock:
            latest=self._latest
            self._latest=None
        return latest
    def run(self):
        try:
            from mss import mss
            with mss() as sct:
                while self.running:
                    t=time.monotonic()
                    r=box(self.hwnd)
                    if r is not None:
                        try:
                            shot=sct.grab({'left':r[0],'top':r[1],'width':r[2],'height':r[3]})
                            frame=np.asarray(shot)[:,:,:3].copy() # BGRA -> BGR
                            with self._lock:self._latest=frame
                        except Exception as exc:
                            self.issue.emit('Сбой захвата: '+str(exc))
                            self.msleep(500)
                    else:self.msleep(400)
                    remaining=1/max(1,self.fps)-(time.monotonic()-t)
                    if remaining>0:self.msleep(round(remaining*1000))
        except Exception as exc:
            self.issue.emit('Невозможно захватить окно: '+str(exc))
    def stop(self):
        self.running=False
        self.wait(2500)

class VideoReplay(QThread):
    finished_report=Signal(object,object,str)
    failed=Signal(str)
    def __init__(self,path:str,out:str):
        super().__init__()
        self.path=path;self.out=out;self._cancel=threading.Event()
    def run(self):
        try:
            from .replay import analyze,write_report
            report,frames=analyze(self.path,6.0,cancelled=self._cancel.is_set)
            saved=write_report(report,frames,self.out)
            self.finished_report.emit(report,frames,str(saved))
        except Exception as exc:self.failed.emit(str(exc))
    def cancel(self):self._cancel.set()

class Hotkeys(QObject):
    stop=Signal()
    toggle=Signal()
    freeze=Signal()
    def __init__(self,fast_stop=None):
        super().__init__()
        self.fast_stop=fast_stop
        self.listener=None
        self.active=False
    def start_listener(self):
        try:
            from pynput.keyboard import Key,Listener
            def on_press(key):
                # Emits Qt signal from the keyboard thread; Qt dispatches on main thread.
                if key==Key.f8:
                    if self.fast_stop:
                        try:self.fast_stop()
                        except Exception:pass
                    self.stop.emit()
                elif key==Key.f3:self.toggle.emit()
                elif key==Key.f4:self.freeze.emit()
            self.listener=Listener(on_press=on_press)
            self.listener.start()
            self.active=True
        except Exception:
            self.active=False
    def shutdown(self):
        if self.listener:
            try:self.listener.stop()
            except Exception:pass

class Preview(QLabel):
    selected=Signal(str,object)
    def __init__(self):
        super().__init__('Подключите окно игры — здесь появится изображение')
        self.setMinimumSize(480,325)
        self.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Expanding)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet('background:#091119;border:1px solid #355164;border-radius:9px;color:#9DB3C6;')
        self.frame:np.ndarray|None=None
        self.render_pix=None
        self.mode=''
        self.rects={}
        self.start=None
        self.point=None
        self.setMouseTracking(True)
    def set_frame(self,frame:np.ndarray):
        self.frame=frame
        self.refresh()
    def draw_rect(self,painter:QPainter,roi:Rect,color:QColor,name:str,scale:float,dx:float,dy:float):
        painter.setPen(QPen(color,2,Qt.DashLine))
        painter.drawRect(QRect(round(dx+roi.x*scale),round(dy+roi.y*scale),round(roi.w*scale),round(roi.h*scale)))
        painter.setPen(color)
        painter.drawText(round(dx+roi.x*scale)+3,round(dy+roi.y*scale)-5,name)
    def display_box(self):
        if self.frame is None:return None
        w=self.frame.shape[1];h=self.frame.shape[0]
        scale=min(self.width()/w,self.height()/h)
        dx=(self.width()-w*scale)/2
        dy=(self.height()-h*scale)/2
        return scale,dx,dy
    def to_image(self,p:QPoint):
        geom=self.display_box()
        if not geom or self.frame is None:return None
        scale,dx,dy=geom
        x=(p.x()-dx)/scale;y=(p.y()-dy)/scale
        if 0<=x<self.frame.shape[1] and 0<=y<self.frame.shape[0]:return (int(x),int(y))
        return None
    def refresh(self):
        if self.frame is None:return
        rgb=cv2.cvtColor(self.frame,cv2.COLOR_BGR2RGB)
        h,w=rgb.shape[:2]
        source=QImage(rgb.data,w,h,rgb.strides[0],QImage.Format_RGB888).copy()
        sc=QPixmap.fromImage(source).scaled(self.size(),Qt.KeepAspectRatio,Qt.SmoothTransformation)
        canvas=QPixmap(self.size());canvas.fill(QColor('#091119'))
        p=QPainter(canvas)
        x=(self.width()-sc.width())//2;y=(self.height()-sc.height())//2
        p.drawPixmap(x,y,sc)
        geometry=self.display_box()
        if geometry:
            scale,dx,dy=geometry
            for name,(roi,color) in self.rects.items():
                if roi is not None:self.draw_rect(p,roi,QColor(color),name,scale,dx,dy)
            if self.point is not None:
                xx=round(dx+self.point[0]*scale);yy=round(dy+self.point[1]*scale)
                p.setPen(QPen(QColor('#FFD166'),2));p.drawEllipse(QPoint(xx,yy),9,9)
                p.drawLine(xx-14,yy,xx+14,yy);p.drawLine(xx,yy-14,xx,yy+14)
            if self.start is not None and self.mode and self.mode!='cast':
                # Selection rectangle is drawn by the OS mouse move function below.
                pass
        p.end()
        self.setPixmap(canvas)
    def resizeEvent(self,e):
        super().resizeEvent(e)
        self.refresh()
    def mousePressEvent(self,e):
        if not self.mode or e.button()!=Qt.LeftButton:return
        self.start=self.to_image(e.position().toPoint())
    def mouseReleaseEvent(self,e):
        if not self.mode or e.button()!=Qt.LeftButton or self.start is None:return
        end=self.to_image(e.position().toPoint())
        start=self.start
        self.start=None
        if end is None:return
        if self.mode=='cast':
            self.selected.emit('cast',end)
        else:
            x0,y0=start;x1,y1=end
            roi=Rect(min(x0,x1),min(y0,y1),abs(x1-x0),abs(y1-y0))
            if roi.valid:self.selected.emit(self.mode,roi)
        self.mode=''

class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('GamePilot Fishing • Контроль одной точки')
        self.resize(1160,750);self.setMinimumSize(920,610)
        self.setStyleSheet(CSS)
        try:self.cfg=load_settings()
        except ValueError:self.cfg=Settings()
        self.vision=Vision(self.cfg)
        self.engine=FishingEngine(self.cfg)
        self.current:np.ndarray|None=None
        self.frozen:np.ndarray|None=None
        self.worker=None
        self.connected:Window|None=None
        self.input=None
        self.halt_now=threading.Event()
        self.armed_mode=None
        self.hot=Hotkeys(self.fast_stop)
        self.hot.stop.connect(self.emergency)
        self.hot.toggle.connect(self.hot_start)
        self.hot.freeze.connect(self.toggle_freeze)
        self.hot.start_listener()
        self.last_stage=None
        self.last_fps_t=time.monotonic();self.frame_count=0
        self.video_replay=None
        self.make_ui()
        self.refresh_windows()
        self.sync_controls()
        self.refresh_labels()
        if not self.hot.active:self.log('⚠ Глобальная F8 недоступна. Автоуправление заблокировано.')
        else:self.log('F3 — запуск/пауза, F4 — заморозить кадр, F8 — аварийный стоп.')
        self.start_timer=QTimer(self);self.start_timer.timeout.connect(self.check_armed)
        self.start_timer.start(100)
        self.frame_timer=QTimer(self);self.frame_timer.timeout.connect(self.poll_frame)
        self.frame_timer.start(60)
        self._last_preview=0.0

    def button(self,label,callback,primary=False,danger=False):
        b=QPushButton(label)
        if primary:b.setObjectName('primary')
        if danger:b.setObjectName('danger')
        b.clicked.connect(callback)
        return b
    def make_ui(self):
        center=QWidget();self.setCentralWidget(center)
        main=QHBoxLayout(center);main.setContentsMargins(12,12,12,12);main.setSpacing(12)
        sidebar=QScrollArea();sidebar.setWidgetResizable(True)
        sidebar.setFrameShape(QFrame.NoFrame)
        sidebar.setFixedWidth(345)
        inside=QWidget();boxv=QVBoxLayout(inside);boxv.setSpacing(11)
        head=QLabel('GAMEPILOT   🎣');head.setObjectName('headline');boxv.addWidget(head)
        desc=QLabel('Рыбалка на одном месте • без карты и ИИ');desc.setObjectName('muted');desc.setWordWrap(True);boxv.addWidget(desc)
        g=QGroupBox('01  Подключение к игре');v=QVBoxLayout(g)
        self.windows=QComboBox();self.windows.setMinimumWidth(240)
        v.addWidget(self.windows)
        row=QHBoxLayout();row.addWidget(self.button('↻ Окна',self.refresh_windows));row.addWidget(self.button('Подключить',self.connect_window,True));v.addLayout(row)
        boxv.addWidget(g)
        g=QGroupBox('02  Калибровка на живом кадре');v=QVBoxLayout(g)
        label=QLabel('Выберите действие, затем кликните или выделите рамку на изображении игры. F4 — зафиксировать кадр мини-игры.')
        label.setWordWrap(True);label.setObjectName('step');v.addWidget(label)
        self.tools=[]
        selections=[('① Точка заброса','cast'),('② Поплавок / поклёвка','bite'),('③ Вся шкала мини-игры','bar'),('④ Зелёная зона','safe'),('⑤ Маркер шкалы','marker'),('⑥ Сообщение об улове (необяз.)','result')]
        for label,mode in selections:
            b=self.button(label,lambda checked=False,m=mode:self.select_mode(m))
            v.addWidget(b);self.tools.append((mode,b))
        self.calibration=QLabel('Отметьте точку и области на превью');self.calibration.setWordWrap(True)
        self.calibration.setObjectName('muted');v.addWidget(self.calibration)
        auto_note=QLabel('✓ По твоему видео распознаём две шкалы и награду автоматически. Для автозаброса ещё нужны точка воды и область поплавка.')
        auto_note.setObjectName('step');auto_note.setWordWrap(True);v.addWidget(auto_note)
        boxv.addWidget(g)
        g=QGroupBox('03  Работа');v=QVBoxLayout(g)
        self.mode=QComboBox()
        self.mode.addItems(['Наблюдение — без кликов','Помощь — только мини-игра','Полный цикл — заброс, поклёвка, вываживание'])
        v.addWidget(self.mode)
        self.status=QLabel('Ожидание')
        self.status.setObjectName('badge');self.status.setWordWrap(True);v.addWidget(self.status)
        r=QHBoxLayout();r.addWidget(self.button('▶ Запустить (F3)',self.on_start,True));r.addWidget(self.button('Ⅱ Пауза',self.on_pause))
        v.addLayout(r)
        v.addWidget(self.button('⛔ Аварийный STOP (F8)',self.emergency,False,True))
        v.addWidget(self.button('🚶 Новое место ловли',self.new_spot))
        self.counters=QLabel('Забросов 0 / 10 · Подтверждено 0 · Неясно 0');self.counters.setWordWrap(True)
        v.addWidget(self.counters)
        boxv.addWidget(g)
        g=QGroupBox('04  Настройки');grid=QGridLayout(g)
        self.auto_chk=QCheckBox('Автораспознавание двух шкал (по видео)')
        self.auto_chk.setChecked(self.cfg.auto_gauge)
        self.reward_chk=QCheckBox('Подтверждать улов по стандартной награде')
        self.reward_chk.setChecked(self.cfg.built_in_reward)
        grid.addWidget(self.auto_chk,0,0,1,2)
        grid.addWidget(self.reward_chk,1,0,1,2)
        self.limit=QSpinBox();self.limit.setRange(1,100);self.limit.setValue(self.cfg.max_attempts)
        self.casts=QSpinBox();self.casts.setRange(100,2500);self.casts.setSingleStep(50);self.casts.setValue(self.cfg.cast_hold_ms)
        self.sensitivity=QDoubleSpinBox();self.sensitivity.setRange(1,75);self.sensitivity.setDecimals(1);self.sensitivity.setValue(self.cfg.bite_motion_threshold)
        self.fps=QSpinBox();self.fps.setRange(2,30);self.fps.setValue(self.cfg.fps)
        grid.addWidget(QLabel('Лимит забросов'),2,0);grid.addWidget(self.limit,2,1)
        grid.addWidget(QLabel('Заброс (мс)'),3,0);grid.addWidget(self.casts,3,1)
        grid.addWidget(QLabel('Чувств. поклёвки'),4,0);grid.addWidget(self.sensitivity,4,1)
        grid.addWidget(QLabel('Кадров/с'),5,0);grid.addWidget(self.fps,5,1)
        grid.addWidget(self.button('💾 Сохранить профиль',self.save),6,0,1,2)
        grid.addWidget(self.button('⬆ Экспорт',self.export_profile),7,0)
        grid.addWidget(self.button('⬇ Импорт',self.import_profile),7,1)
        grid.addWidget(self.button('🎬 Анализ MP4 — без кликов',self.analyze_video),8,0,1,2)
        boxv.addWidget(g)
        boxv.addStretch(1)
        sidebar.setWidget(inside)
        main.addWidget(sidebar)
        right=QWidget();rv=QVBoxLayout(right);rv.setContentsMargins(0,0,0,0);rv.setSpacing(9)
        self.header=QLabel('ИГРОВОЕ ОКНО  •  LIVE PREVIEW')
        self.header.setStyleSheet('color:#9CBCD2;font-weight:700;letter-spacing:1px;');rv.addWidget(self.header)
        self.preview=Preview();self.preview.selected.connect(self.on_selected);rv.addWidget(self.preview,5)
        self.diagnostic=QLabel('Шкала: —  |  Маркер: —  |  Движение поплавка: —')
        self.diagnostic.setStyleSheet('color:#AFC5D5;font-size:12px');rv.addWidget(self.diagnostic)
        self.logbox=QTextEdit();self.logbox.setReadOnly(True);self.logbox.setMinimumHeight(132)
        self.logbox.setMaximumHeight(210)
        rv.addWidget(self.logbox,2)
        note=QLabel('ℹ  Автоцикл требует калибровки. После 10 попыток бот остановится; новое место выбираешь ты. При отсутствии подтверждения улов не засчитывается.')
        note.setObjectName('muted');note.setWordWrap(True);rv.addWidget(note)
        main.addWidget(right,1)

    def analyze_video(self):
        if self.video_replay is not None and self.video_replay.isRunning():
            return self.log('Анализ другого видео ещё выполняется')
        path,_=QFileDialog.getOpenFileName(self,'Выберите записанную рыбалку','','Видео (*.mp4 *.mov *.mkv *.avi)')
        if not path:return
        out=str(Path(path).with_suffix(''))+'_GamePilot_report'
        self.video_replay=VideoReplay(path,out)
        self.video_replay.finished_report.connect(self.on_video_report)
        self.video_replay.failed.connect(lambda msg:self.log('Ошибка видео: '+msg))
        self.video_replay.start()
        self.log('Анализирую видео в фоне. Команды в игру не отправляются.')

    def on_video_report(self,report,frames,saved):
        self.log(f"Видео: {report['confirmed_cycles']} подтверждённых циклов, {len(report['minigames'])} мини-игры")
        for x in report['minigames']:
            self.log(f"Мини-игра {x['start_s']:.1f}–{x['end_s']:.1f} сек · рыбка {x['peak_progress']*100:.0f}% · награда: {'да' if x['reward_confirmed'] else 'нет'}")
        self.log('Отчёт: '+saved)
        QMessageBox.information(self,'GamePilot: анализ видео',f"Найдено мини-игр: {len(report['minigames'])}\nУловов подтверждено: {report['confirmed_cycles']}\nОтчёт: {saved}")

    def refresh_windows(self):
        was=self.windows.currentData()
        self.windows.clear()
        entries=windows()
        for w in entries:
            self.windows.addItem(f'{w.title[:48]}   [PID {w.pid}]',w)
        if entries:
            idx=next((i for i,v in enumerate(entries) if was and v.hwnd==was.hwnd),0)
            self.windows.setCurrentIndex(idx)
        else:self.log('Открытые игровые окна не найдены (программа для Windows).')

    def connect_window(self):
        w=self.windows.currentData()
        if w is None:return self.log('Сначала выберите окно игры')
        r=box(w.hwnd)
        if not r:return self.log('Клиентская область игры недоступна')
        if self.worker:self.worker.stop()
        if self.input:self.input.release()
        self.connected=w
        if self.cfg.client_size and self.cfg.client_size!=list(r[2:]):
            self.log('⚠ Размер экрана изменился. Старые точки не подходят: выполните калибровку.')
            self.cfg.cast_xy=None;self.cfg.bite_roi=self.cfg.bar_roi=self.cfg.safe_roi=self.cfg.result_roi=None
            self.cfg.marker_template='';self.cfg.result_template='';self.vision.load_templates()
        self.cfg.client_size=list(r[2:]);self.cfg.window_title=w.title
        self.worker=Capture(w.hwnd,self.fps.value())
        self.worker.issue.connect(self.log);self.worker.start()
        self.log(f'Подключено: {w.title}; область {r[2]} × {r[3]}')
        self.refresh_labels()

    def select_mode(self,mode):
        if self.current is None:
            return self.log('Сначала подключите игру')
        if self.engine.stage not in (Stage.IDLE,Stage.PAUSE,Stage.ERROR,Stage.NEED_MOVE,Stage.ASSIST):
            return self.log('Для разметки сначала нажмите Пауза')
        if mode in ('bar','safe','marker') and self.frozen is None:
            self.log('Рекомендация: запустите мини-игру вручную и нажмите F4 для стоп-кадра.')
        self.preview.mode=mode
        self.log('Разметка: '+mode+' — '+('нажмите точку' if mode=='cast' else 'выделите область мышью'))

    def on_selected(self,mode,value):
        if self.current is None:return
        if mode=='cast':
            self.cfg.cast_xy=list(value)
            self.log(f'Точка заброса: {value}')
        else:
            if mode in ('bite','bar','safe'):
                setattr(self.cfg,{'bite':'bite_roi','bar':'bar_roi','safe':'safe_roi'}[mode],value)
            elif mode in ('marker','result'):
                frame=self.frozen if self.frozen is not None else self.current
                patch=crop(frame,value)
                if patch is None or min(patch.shape[:2])<5:return self.log('Область слишком мала')
                name='marker.png' if mode=='marker' else 'success.png'
                path=settings_path().parent/name
                if not write_image(str(path),patch):return self.log('Не удалось сохранить образец')
                if mode=='marker':self.cfg.marker_template=name
                else:
                    self.cfg.result_template=name
                    H,W=frame.shape[:2]
                    # Search only near the event, to reduce false matches in HUD.
                    value=Rect(max(0,value.x-80),max(0,value.y-65),value.w+160,value.h+130).clamp(W,H)
                    self.cfg.result_roi=value
                self.vision.load_templates()
            self.log('Сохранено: '+mode)
        self.refresh_labels();self.save(quiet=True)

    def toggle_freeze(self):
        if self.engine.stage not in (Stage.IDLE,Stage.PAUSE,Stage.ERROR,Stage.NEED_MOVE):
            return self.log('Кадр нельзя заморозить во время автоуправления')
        if self.frozen is None and self.current is not None:
            self.frozen=self.current.copy();self.preview.set_frame(self.frozen)
            self.log('Стоп-кадр включён. Выделите шкалу и маркер.')
        else:
            self.frozen=None
            self.log('Вернулись к живому изображению')
            if self.current is not None:self.preview.set_frame(self.current)

    def sync_controls(self):
        self.limit.setValue(self.cfg.max_attempts)
        self.casts.setValue(self.cfg.cast_hold_ms)
        self.sensitivity.setValue(self.cfg.bite_motion_threshold)
        self.fps.setValue(self.cfg.fps)
        self.auto_chk.setChecked(self.cfg.auto_gauge)
        self.reward_chk.setChecked(self.cfg.built_in_reward)

    def apply_values(self):
        self.cfg.max_attempts=self.limit.value()
        self.cfg.max_verified=self.limit.value()
        self.cfg.cast_hold_ms=self.casts.value()
        self.cfg.bite_motion_threshold=self.sensitivity.value()
        self.cfg.fps=self.fps.value()
        self.cfg.auto_gauge=self.auto_chk.isChecked()
        self.cfg.built_in_reward=self.reward_chk.isChecked()

    def save(self,quiet=False):
        self.apply_values()
        try:
            path=save_settings(self.cfg)
            if not quiet:self.log('Настройки сохранены: '+str(path))
        except Exception as exc:self.log('Ошибка сохранения: '+str(exc))

    def export_profile(self):
        from .profiles import export_profile
        self.save(quiet=True)
        path,_=QFileDialog.getSaveFileName(self,'Экспорт профиля','GamePilotFishing_profile.zip','ZIP profile (*.zip)')
        if not path:return
        try:export_profile(Path(path));self.log('Профиль экспортирован: '+path)
        except Exception as exc:self.log('Ошибка экспорта: '+str(exc))

    def import_profile(self):
        from .profiles import import_profile
        self.on_pause()
        path,_=QFileDialog.getOpenFileName(self,'Импорт профиля','','ZIP profile (*.zip)')
        if not path:return
        try:
            c=import_profile(Path(path))
            self.cfg=c;self.vision=Vision(c);self.engine=FishingEngine(c)
            self.sync_controls();self.refresh_labels()
            if self.connected:
                region=box(self.connected.hwnd)
                if region and c.client_size!=list(region[2:]):
                    self.log('⚠ Экран другой: повторите калибровку! Автоцикл блокируется.')
                    self.cfg.cast_xy=None
                    self.cfg.bite_roi=self.cfg.bar_roi=self.cfg.safe_roi=None
                    self.cfg.marker_template=''
            self.log('Профиль импортирован. Проверьте области на кадре.')
        except Exception as exc:self.log('Ошибка импорта: '+str(exc))

    def refresh_labels(self):
        c=self.cfg
        self.preview.rects={
            'Поплавок':(c.bite_roi,'#33D4CE'),
            'Шкала':(c.bar_roi,'#FAD25D'),
            'Зелёная':(c.safe_roi,'#3FFA83'),
            'Улов':(c.result_roi,'#EE9AFA')}
        self.preview.point=c.cast_xy
        self.calibration.setText(' · '.join([
          ('●' if c.cast_xy else '○')+' Заброс',
          ('●' if c.bite_roi else '○')+' Поплавок',
          ('●' if c.auto_gauge or c.bar_roi else '○')+' Шкала',
          ('●' if c.auto_gauge or c.safe_roi else '○')+' Зелёная зона',
          ('●' if c.auto_gauge or c.marker_template else '○')+' Маркер']))
        self.preview.refresh()

    def log(self,msg:str):
        if not hasattr(self,'logbox'):return
        t=time.strftime('%H:%M:%S')
        self.logbox.append(f'<span style="color:#83C6E8">{t}</span>  {msg}')
        # keep memory bounded
        doc=self.logbox.document()
        if doc.blockCount()>260:
            cursor=self.logbox.textCursor();cursor.movePosition(cursor.Start)
            cursor.select(cursor.BlockUnderCursor);cursor.removeSelectedText();cursor.deleteChar()

    def set_stage(self):
        self.status.setText('●  '+self.engine.stage.value)
        self.counters.setText(f'Забросов {self.engine.attempts} / {self.cfg.max_attempts}   •   '+
                              f'Подтверждено {self.engine.verified}   •   Вероятно {self.engine.likely}   •   Неясно {self.engine.uncertain}')
        if self.engine.stage==Stage.BITE and self.last_stage!=Stage.BITE:
            self.vision.bite.reset()
        self.last_stage=self.engine.stage
        if self.engine.events:
            for text in self.engine.events:self.log(text)
            self.engine.events.clear()

    def poll_frame(self):
        if self.worker is not None:
            frame=self.worker.pop_latest()
            if frame is not None:self.on_frame(frame)

    def on_frame(self,frame:np.ndarray):
        now=time.monotonic()
        self.current=frame
        if self.frozen is None and now-self._last_preview>.17:
            self.preview.set_frame(frame)
            self._last_preview=now
        mode=self.engine.stage==Stage.BITE
        view=self.vision.inspect(frame,look_for_bite=mode)
        self.diagnostic.setText(f'Шкала: {"ДА" if view.bar else "нет"}    •    '
                    f'Поплавок шкалы: {view.marker_x if view.marker_x is not None else "—"}    •    '
                    f'Рыбка: {view.fish_progress*100:.0f}% ' if view.fish_progress is not None else
                    f'Шкала: {"ДА" if view.bar else "нет"}    •    Рыбка: —')
        self.diagnostic.setText(self.diagnostic.text()+f'    •    Поклёвка: {view.bite_score:.1f}   •   Награда: {"ДА" if view.success else "нет"}')
        if view.gauge_rect is not None and self.cfg.auto_gauge:
            gx,gy,gw,gh=view.gauge_rect
            self.preview.rects['Автошкала']=(Rect(gx,gy,gw,gh),'#FFD166')
        else:self.preview.rects.pop('Автошкала',None)
        if self.input and self.input.watchdog_fired:
            self.input.watchdog_fired=False
            self.halt_now.set()
            self.engine.pause(time.monotonic(),'Контроль удержания: мышь отпущена watchdog — требуется проверка')
        if self.connected is not None:
            focused_now=focused(self.connected.hwnd,self.connected.pid)
            actions=self.engine.step(view,time.monotonic(),foreground=focused_now)
            self.perform(actions)
        self.set_stage()

    def perform(self,actions):
        if self.halt_now.is_set():
            if self.input:self.input.release()
            return
        if not actions:return
        if not self.connected:
            self.engine.pause(time.monotonic(),'Нет окна игры');return
        try:
            if self.input is None:
                from .input import MouseInput
                self.input=MouseInput()
            for a in actions:
                hold_limit=3.0 if self.engine.stage==Stage.CAST else .9
                self.input.execute(a,self.connected.hwnd,self.connected.pid,self.cfg.client_size,hold_limit)
        except Exception as exc:
            if self.input:self.input.release()
            self.engine.pause(time.monotonic(),'Управление заблокировано: '+str(exc))

    def on_start(self):
        if self.frozen is not None:self.toggle_freeze()
        if not self.connected:return self.log('Сначала подключите игровое окно')
        self.save(quiet=True)
        self.halt_now.clear()
        mode=['observe','assist','auto'][self.mode.currentIndex()]
        if mode!='observe' and not self.hot.active:
            return self.log('Глобальная F8 недоступна: автоуправление запрещено')
        try:
            # validate BEFORE arming, not after switching focus
            self.engine.start(mode,time.monotonic())
        except ValueError as exc:
            return self.log('Не готово: '+str(exc))
        if mode!='observe':
            self.engine.pause(time.monotonic(),'Готовность проверена, переключитесь в игру')
            self.armed_mode=mode
            self.log('Ожидаю переключения в игру. Перейдите в игру через Alt+Tab. Автостарт при фокусе.')
        else:
            self.log('Наблюдение включено (без кликов)')
        self.set_stage()

    def check_armed(self):
        if self.armed_mode and self.connected and focused(self.connected.hwnd,self.connected.pid):
            mode=self.armed_mode;self.armed_mode=None
            try:self.engine.start(mode,time.monotonic())
            except ValueError as exc:self.log('Ошибка: '+str(exc))
            self.set_stage()

    def hot_start(self):
        if self.armed_mode:
            self.armed_mode=None
            self.log('Отменён ожидающий запуск')
            return
        if self.engine.stage not in (Stage.IDLE,Stage.PAUSE,Stage.ERROR,Stage.NEED_MOVE):
            self.on_pause()
        else:
            self.on_start()

    def on_pause(self):
        self.armed_mode=None
        acts=self.engine.pause(time.monotonic())
        if self.input:self.input.release()
        self.set_stage()

    def fast_stop(self):
        self.halt_now.set()
        if self.input:self.input.release()

    def emergency(self):
        self.halt_now.set()
        self.armed_mode=None
        acts=self.engine.emergency(time.monotonic())
        if self.input:self.input.release()
        self.set_stage()

    def new_spot(self):
        self.armed_mode=None
        if self.input:self.input.release()
        self.engine.new_spot(time.monotonic())
        self.save(quiet=True)
        self.refresh_labels();self.set_stage()

    def closeEvent(self,event):
        self.armed_mode=None
        if self.input:self.input.release()
        if self.video_replay is not None and self.video_replay.isRunning():
            self.video_replay.cancel();self.video_replay.wait(3000)
        if self.worker:self.worker.stop()
        if self.input:self.input.close()
        self.hot.shutdown()
        self.save(quiet=True)
        super().closeEvent(event)
