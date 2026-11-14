"""Deterministic, auditable fishing cycle. No mouse IO, UI, or clocks inside."""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from .model import Settings
from .vision import View


class Stage(str, Enum):
    IDLE='Ожидание'
    CAST='Заброс'
    BITE='Ожидание поклёвки'
    HOOK='Подсечка'
    REEL_WAIT='Ожидание шкалы'
    REEL='Вываживание'
    VERIFY='Проверка улова'
    COOLDOWN='Пауза между забросами'
    ASSIST='Помощь с мини-игрой'
    NEED_MOVE='Переставьте персонажа'
    PAUSE='Пауза'
    ERROR='Нужна помощь'


@dataclass(frozen=True)
class Action:
    type: str # 'down'/'up'/'move_down' / 'log'
    point: tuple[int,int] | None = None
    message: str = ''


class FishingEngine:
    def __init__(self,cfg:Settings):
        self.cfg=cfg
        self.stage=Stage.IDLE
        self.mode='observe'
        self.since=0.0
        self.attempts=0
        self.verified=0
        self.uncertain=0
        self.likely=0
        self.best_progress=0.0
        self.button_down=False
        self.marker_prev_x: int|None=None
        self.marker_prev_t:float|None=None
        self.last_marker_t:float|None=None
        self.last_bar_t:float|None=None
        self.last_hold_start:float|None=None
        self.minigame_seen=False
        self.result_armed=False
        self.events:list[str]=[]

    def enter(self,stage:Stage,now:float,message:str=''):
        self.stage=stage
        self.since=now
        if message:self.events.append(message)

    def _down(self,point=None) -> list[Action]:
        if self.button_down:return []
        self.button_down=True
        return [Action('down',point)]

    def _up(self) -> list[Action]:
        if not self.button_down:return []
        self.button_down=False
        return [Action('up')]

    def pause(self,now:float,reason:str='Остановлено пользователем') -> list[Action]:
        a=self._up()
        self.enter(Stage.PAUSE,now,reason)
        return a

    def emergency(self,now:float)->list[Action]:
        a=self._up()
        self.enter(Stage.ERROR,now,'Аварийный стоп F8 — новые действия заблокированы')
        return a

    def start(self,mode:str,now:float)->list[Action]:
        if mode not in ('observe','assist','auto'):raise ValueError(mode)
        if mode in ('auto','assist') and not self.cfg.ready_to_reel():raise ValueError('Сначала настройте шкалу и маркер')
        if mode=='auto' and not self.cfg.ready_to_auto():raise ValueError('Укажите точку заброса и область поплавка')
        actions=self._up()
        self.mode=mode
        self.events.append('Режим: '+{'observe':'Наблюдение','assist':'Помощь','auto':'Автоцикл'}[mode])
        self.result_armed=False
        if mode=='auto':
            self.enter(Stage.COOLDOWN,now)
            self.since=now-self.cfg.between_cast_s
        elif mode=='assist':self.enter(Stage.ASSIST,now)
        else:self.enter(Stage.IDLE,now)
        return actions

    def new_spot(self,now:float)->list[Action]:
        actions=self._up()
        self.attempts=self.verified=self.uncertain=self.likely=0
        self.mode='observe'
        # New character location implies old screen cast point is not guaranteed water.
        self.cfg.cast_xy=None
        self.enter(Stage.IDLE,now,'Новое место: отметьте точку заброса заново')
        return actions

    def _reel(self,view:View,now:float)->list[Action]:
        if not view.bar:
            if self.last_bar_t is not None and now-self.last_bar_t > .65 and now-self.since>=self.cfg.min_reel_visible_s:
                a=self._up()
                self.enter(Stage.VERIFY,now,'Шкала исчезла: проверяю результат')
                return a
            if now-self.since>2.2 and self.last_bar_t is None:
                a=self._up();self.enter(Stage.ERROR,now,'Шкала не появилась: требуется калибровка');return a
            return self._up()
        self.last_bar_t=now
        if view.marker_x is None:
            if self.last_marker_t is None:self.last_marker_t=self.since
            if now-self.last_marker_t>self.cfg.max_lost_marker_s:
                a=self._up();self.enter(Stage.ERROR,now,'Маркер потерян: автоматическое удержание остановлено');return a
            return self._up()
        x=view.marker_x
        v=0.0
        if self.marker_prev_t is not None and now>self.marker_prev_t:
            v=(x-self.marker_prev_x)/(now-self.marker_prev_t) # type: ignore[operator]
            v=max(-550,min(550,v))
        self.marker_prev_x=x;self.marker_prev_t=now;self.last_marker_t=now
        if view.safe_left is not None and view.safe_right is not None:
            left=view.safe_left+self.cfg.marker_margin_px
            right=view.safe_right-self.cfg.marker_margin_px
        elif self.cfg.safe_roi:
            left=self.cfg.safe_roi.x+self.cfg.marker_margin_px
            right=self.cfg.safe_roi.x+self.cfg.safe_roi.w-self.cfg.marker_margin_px
        else:return self.pause(now,'Не определена зелёная зона')
        if right-left < 10:
            return self.pause(now,'Зелёная зона слишком узкая')
        mid=(left+right)/2
        projected=x+v*self.cfg.predictive_s
        # Safeguard: enforce maximum continuous hold regardless of vision estimate.
        if self.button_down and self.last_hold_start is not None and now-self.last_hold_start>self.cfg.max_hold_s:
            return self._up()
        if projected>=mid+self.cfg.marker_deadband_px or x>=right:
            return self._up()
        if projected<=mid-self.cfg.marker_deadband_px or x<=left:
            actions=self._down()
            if actions:self.last_hold_start=now
            return actions
        return []

    def step(self,view:View,now:float,foreground:bool=True)->list[Action]:
        if self.mode=='observe' or self.stage in (Stage.IDLE,Stage.PAUSE,Stage.ERROR,Stage.NEED_MOVE):return []
        if not foreground:return self.pause(now,'Игровое окно потеряло фокус — пауза')
        s=self.stage
        dt=now-self.since
        if s==Stage.ASSIST:
            if view.bar and view.marker_x is not None:
                self.best_progress=0.0
                self.marker_prev_x=self.marker_prev_t=self.last_marker_t=None
                self.last_bar_t=now
                self.enter(Stage.REEL,now,'Обнаружена мини-игра')
            return []
        if s==Stage.COOLDOWN:
            if self.attempts>=self.cfg.max_attempts or self.verified>=self.cfg.max_verified:
                self.enter(Stage.NEED_MOVE,now,'Лимит точки достигнут. Переставьте персонажа вручную')
                return self._up()
            if dt>=self.cfg.between_cast_s:
                self.attempts+=1
                self.result_armed=False
                self.best_progress=0.0
                self.enter(Stage.CAST,now,f'Заброс {self.attempts} / {self.cfg.max_attempts}')
                return self._down(tuple(self.cfg.cast_xy)) # type: ignore[arg-type]
            return []
        if s==Stage.CAST:
            if dt*1000>=self.cfg.cast_hold_ms:
                a=self._up()
                self.enter(Stage.BITE,now,'Жду поклёвку')
                return a
            return []
        if s==Stage.BITE:
            # Sometimes a game's indicator begins without an extra hook click.
            if view.bar and view.marker_x is not None:
                self._initialize_reel(now)
                self.enter(Stage.REEL,now,'Шкала появилась')
                return []
            if dt>self.cfg.bite_timeout_s:
                self.uncertain+=1
                self.enter(Stage.COOLDOWN,now,'Поклёвки нет: повторный заброс')
                return []
            if dt>=self.cfg.min_bite_wait_s and view.bite:
                self.enter(Stage.HOOK,now,'Обнаружено движение поплавка — подсечка')
                return self._down(tuple(self.cfg.cast_xy)) # type: ignore[arg-type]
            return []
        if s==Stage.HOOK:
            if dt*1000>=max(80,self.cfg.hook_delay_ms):
                a=self._up()
                self.enter(Stage.REEL_WAIT,now)
                return a
            return []
        if s==Stage.REEL_WAIT:
            if view.bar and view.marker_x is not None:
                self._initialize_reel(now)
                self.enter(Stage.REEL,now,'Шкала вываживания найдена')
            elif dt>2.0:
                self.enter(Stage.ERROR,now,'Подсечка без шкалы: проверьте сигнал поклёвки')
            return []
        if s==Stage.REEL:
            if not view.success:self.result_armed=True
            if view.fish_progress is not None:
                self.best_progress=max(self.best_progress,view.fish_progress)
            if dt>self.cfg.reel_timeout_s:
                a=self._up();self.enter(Stage.ERROR,now,'Превышено время вываживания');return a
            return self._reel(view,now)
        if s==Stage.VERIFY:
            if view.success and (self.cfg.result_template or self.cfg.built_in_reward) and self.result_armed:
                self.verified+=1
                self.enter(Stage.ASSIST if self.mode=='assist' else Stage.COOLDOWN,now,f'Улов подтверждён: {self.verified}')
                return []
            if dt>=self.cfg.verify_timeout_s:
                if self.best_progress >= self.cfg.min_progress_for_likely:
                    self.likely+=1
                    msg='Вероятный улов (шкала завершилась); награда не распознана'
                else:
                    self.uncertain+=1
                    msg='Шкала исчезла; результата нет — попытка неопределённая'
                self.enter(Stage.ASSIST if self.mode=='assist' else Stage.COOLDOWN,now,msg)
                return []
        return []

    def _initialize_reel(self,now:float):
        self.marker_prev_x=None;self.marker_prev_t=None;self.last_marker_t=now
        self.last_bar_t=now;self.last_hold_start=None
