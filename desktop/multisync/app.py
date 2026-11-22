"""MultiSync Hub desktop UI (customtkinter)."""
import ctypes
import os
import sys
import tkinter as tk
from tkinter import filedialog

import customtkinter as ctk

from . import calibrate
from .config import APP_NAME, APP_VERSION
from .engine import Engine, MAX_LEAD_MS, MIN_LEAD_MS
from .outputs import list_input_devices, list_output_devices
from .sources import AUDIO_EXTENSIONS, InputDeviceSource, LoopbackSource

# palette
BG = "#0E1117"
CARD = "#161B24"
CARD2 = "#1D2430"
LINE = "#273041"
TEXT = "#E8ECF3"
MUTED = "#8A94A7"
ACCENT = "#22D3B6"
ACCENT_H = "#1BB89E"
VIOLET = "#8B5CF6"
WARN = "#F5A524"
DANGER = "#F25F5C"

ICON = "Segoe MDL2 Assets"
I_PLAY, I_PAUSE, I_PREV, I_NEXT = "", "", "", ""
I_BT, I_SPK, I_PHONE, I_REFRESH = "", "", "", ""
I_ADD, I_X, I_VOL, I_MIC, I_SHIELD = "", "", "", "", ""


def resource(name):
    base = getattr(sys, "_MEIPASS", os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    return os.path.join(base, "assets", name)


def fmt_time(s):
    s = max(0, int(s))
    return f"{s // 60}:{s % 60:02d}"


class Card(ctk.CTkFrame):
    def __init__(self, master, title=None, **kw):
        super().__init__(master, fg_color=CARD, corner_radius=16, border_width=1, border_color=LINE, **kw)
        if title:
            ctk.CTkLabel(self, text=title.upper(), text_color=MUTED,
                         font=ctk.CTkFont("Segoe UI", 11, "bold")).pack(anchor="w", padx=18, pady=(14, 4))


class OutputRow(ctk.CTkFrame):
    def __init__(self, master, app, out):
        super().__init__(master, fg_color=CARD2, corner_radius=12)
        self.app = app
        self.out = out
        self.oid = out.id
        remote = out.kind == "remote"

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(10, 2))
        self.switch = ctk.CTkSwitch(top, text="", width=44, progress_color=ACCENT, command=self._toggle)
        self.switch.pack(side="left")
        if out.enabled and not out.muted:
            self.switch.select()
        icon = I_PHONE if remote else (I_BT if out.bluetooth else I_SPK)
        ctk.CTkLabel(top, text=icon, font=ctk.CTkFont(ICON, 18),
                     text_color=VIOLET if remote else ACCENT, width=26).pack(side="left", padx=(2, 8))
        names = ctk.CTkFrame(top, fg_color="transparent")
        names.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(names, text=out.name, anchor="w", text_color=TEXT,
                     font=ctk.CTkFont("Segoe UI", 14, "bold")).pack(fill="x")
        self.status = ctk.CTkLabel(names, text="", anchor="w", text_color=MUTED, font=ctk.CTkFont("Segoe UI", 11))
        self.status.pack(fill="x")
        self.meter = ctk.CTkProgressBar(top, width=90, height=6, progress_color=ACCENT, fg_color=LINE)
        self.meter.set(0)
        self.meter.pack(side="right", padx=(8, 0))

        bot = ctk.CTkFrame(self, fg_color="transparent")
        bot.pack(fill="x", padx=12, pady=(4, 10))
        ctk.CTkLabel(bot, text=I_VOL, font=ctk.CTkFont(ICON, 14), text_color=MUTED).pack(side="left")
        self.vol = ctk.CTkSlider(bot, from_=0, to=1, number_of_steps=100, height=14,
                                 progress_color=ACCENT, button_color=TEXT, button_hover_color=ACCENT,
                                 command=self._vol)
        self.vol.set(out.volume)
        self.vol.pack(side="left", fill="x", expand=True, padx=(6, 14))
        ctk.CTkLabel(bot, text="Delay", text_color=MUTED, font=ctk.CTkFont("Segoe UI", 11)).pack(side="left", padx=(0, 6))
        for txt, d in (("−10", -10), ("−1", -1)):
            self._step_btn(bot, txt, d)
        self.delay = ctk.CTkEntry(bot, width=74, height=26, justify="center", fg_color=BG, border_color=LINE,
                                  font=ctk.CTkFont("Consolas", 12))
        self.delay.pack(side="left", padx=4)
        self.delay.bind("<Return>", self._delay_entered)
        self.delay.bind("<FocusOut>", self._delay_entered)
        for txt, d in (("+1", 1), ("+10", 10)):
            self._step_btn(bot, txt, d)
        self._show_delay()

    def _step_btn(self, parent, txt, d):
        ctk.CTkButton(parent, text=txt, width=34, height=26, corner_radius=8, fg_color=LINE, hover_color="#344056",
                      font=ctk.CTkFont("Segoe UI", 11), command=lambda: self._nudge(d)).pack(side="left", padx=1)

    def _show_delay(self):
        self.delay.delete(0, "end")
        self.delay.insert(0, f"{self.out.latency_ms:.0f} ms")

    def _nudge(self, d):
        self.out.latency_ms = max(0.0, min(1500.0, self.out.latency_ms + d))
        self._show_delay()
        self.app.engine.store_output_settings(self.out)

    def _delay_entered(self, _e=None):
        txt = self.delay.get().replace("ms", "").strip()
        try:
            self.out.latency_ms = max(0.0, min(1500.0, float(txt)))
            self.app.engine.store_output_settings(self.out)
        except ValueError:
            pass
        self._show_delay()

    def _vol(self, v):
        self.out.volume = float(v)
        self.app.engine.store_output_settings(self.out)

    def _toggle(self):
        on = bool(self.switch.get())
        if self.out.kind == "local":
            self.app.engine.set_output_enabled(self.oid, on)
            if on and self.out.error:
                self.switch.deselect()
                self.app.toast(f"Couldn't open {self.out.name}: {self.out.error}", DANGER)
        else:
            self.out.muted = not on
            self.app.engine.store_output_settings(self.out)

    def refresh(self):
        o = self.out
        st = o.status() if (o.kind == "remote" or o.enabled) else ("Off" if not o.error else o.error)
        if o.kind == "local" and o.enabled and o.xruns:
            st += f" · {o.xruns} glitches"
        self.status.configure(text=st)
        self.meter.set(min(1.0, o.level))
        if self.app.focus_get() is not self.delay._entry:
            cur = f"{o.latency_ms:.0f} ms"
            if self.delay.get() != cur:
                self._show_delay()


class App(ctk.CTk):
    def __init__(self, engine: Engine):
        super().__init__(fg_color=BG)
        self.engine = engine
        self.title(f"{APP_NAME} Hub")
        self.geometry("1120x680")
        self.minsize(960, 600)
        try:
            self.iconbitmap(resource("icon.ico"))
        except Exception:
            pass
        self.rows = {}
        self.calibrator = None
        self.seeking = False
        self._toast_job = None
        self.mode = "Music"
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._sync_seg()
        self.after(150, self._tick)
        if engine.server.error:
            self.after(500, lambda: self.toast(engine.server.error + " — is MultiSync already running?", DANGER, 12000))

    # ---------------- layout ----------------
    def _build(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=24, pady=(18, 10))
        logo = ctk.CTkFrame(header, width=44, height=44, corner_radius=12, fg_color=ACCENT)
        logo.pack(side="left")
        logo.pack_propagate(False)
        ctk.CTkLabel(logo, text="", font=ctk.CTkFont(ICON, 22), text_color=BG).pack(expand=True)
        tt = ctk.CTkFrame(header, fg_color="transparent")
        tt.pack(side="left", padx=12)
        ctk.CTkLabel(tt, text="MultiSync", text_color=TEXT, font=ctk.CTkFont("Segoe UI", 22, "bold")).pack(anchor="w")
        ctk.CTkLabel(tt, text="One source · every speaker · in perfect sync", text_color=MUTED,
                     font=ctk.CTkFont("Segoe UI", 12)).pack(anchor="w")
        right = ctk.CTkFrame(header, fg_color="transparent")
        right.pack(side="right")
        self.pill = ctk.CTkLabel(right, text="", corner_radius=14, fg_color=CARD2, text_color=TEXT,
                                 font=ctk.CTkFont("Segoe UI", 12, "bold"), padx=14, height=30)
        self.pill.pack(side="left", padx=6)
        ctk.CTkButton(right, text=I_SHIELD + "  Allow phones", width=130, height=30, corner_radius=14,
                      fg_color=CARD2, hover_color=LINE, font=ctk.CTkFont("Segoe UI", 12),
                      command=self._firewall).pack(side="left", padx=6)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=(0, 8))
        body.grid_columnconfigure(0, weight=0, minsize=400)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(body, fg_color="transparent", width=400)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        self._build_source(left)
        self._build_master(left)

        rightcol = Card(body)
        rightcol.grid(row=0, column=1, sticky="nsew")
        self._build_outputs(rightcol)

        self.footer = ctk.CTkLabel(self, text="", text_color=MUTED, anchor="w", font=ctk.CTkFont("Segoe UI", 12))
        self.footer.pack(fill="x", padx=28, pady=(0, 12))

    def _build_source(self, parent):
        card = Card(parent, "Source")
        card.pack(fill="both", expand=True)
        self.seg = ctk.CTkSegmentedButton(card, values=["Music", "System audio", "Input", "Sync test"],
                                          selected_color=ACCENT, selected_hover_color=ACCENT_H,
                                          unselected_color=CARD2, unselected_hover_color=LINE,
                                          text_color=TEXT, font=ctk.CTkFont("Segoe UI", 12, "bold"),
                                          command=self._mode_changed, height=34)
        self.seg.set("Music")
        self.seg.pack(fill="x", padx=16, pady=(4, 10))
        self.src_host = ctk.CTkFrame(card, fg_color="transparent")
        self.src_host.pack(fill="both", expand=True, padx=16, pady=(0, 14))
        self.src_frames = {
            "Music": self._music_frame(self.src_host),
            "System audio": self._loopback_frame(self.src_host),
            "Input": self._input_frame(self.src_host),
            "Sync test": self._test_frame(self.src_host),
        }
        self.src_frames["Music"].pack(fill="both", expand=True)

    def _music_frame(self, host):
        f = ctk.CTkFrame(host, fg_color="transparent")
        np_card = ctk.CTkFrame(f, fg_color=CARD2, corner_radius=14)
        np_card.pack(fill="x")
        self.track = ctk.CTkLabel(np_card, text="Nothing playing", text_color=TEXT, anchor="w",
                                  font=ctk.CTkFont("Segoe UI", 16, "bold"))
        self.track.pack(fill="x", padx=14, pady=(12, 0))
        self.track_sub = ctk.CTkLabel(np_card, text="Add songs (mp3, flac, wav, ogg, opus)", text_color=MUTED,
                                      anchor="w", font=ctk.CTkFont("Segoe UI", 11))
        self.track_sub.pack(fill="x", padx=14)
        self.seek = ctk.CTkSlider(np_card, from_=0, to=1, height=14, progress_color=ACCENT, button_color=TEXT,
                                  button_hover_color=ACCENT, command=lambda v: None)
        self.seek.set(0)
        self.seek.pack(fill="x", padx=10, pady=(8, 0))
        self.seek.bind("<ButtonPress-1>", lambda e: setattr(self, "seeking", True))
        self.seek.bind("<ButtonRelease-1>", self._seek_release)
        times = ctk.CTkFrame(np_card, fg_color="transparent")
        times.pack(fill="x", padx=14)
        self.t_pos = ctk.CTkLabel(times, text="0:00", text_color=MUTED, font=ctk.CTkFont("Segoe UI", 10))
        self.t_pos.pack(side="left")
        self.t_dur = ctk.CTkLabel(times, text="0:00", text_color=MUTED, font=ctk.CTkFont("Segoe UI", 10))
        self.t_dur.pack(side="right")
        tr = ctk.CTkFrame(np_card, fg_color="transparent")
        tr.pack(pady=(2, 12))
        p = self.engine.player
        self._round_btn(tr, I_PREV, p.prev, 40).pack(side="left", padx=6)
        self.play_btn = self._round_btn(tr, I_PLAY, self._toggle_play, 54, primary=True)
        self.play_btn.pack(side="left", padx=6)
        self._round_btn(tr, I_NEXT, p.next, 40).pack(side="left", padx=6)

        bar = ctk.CTkFrame(f, fg_color="transparent")
        bar.pack(fill="x", pady=(12, 6))
        ctk.CTkLabel(bar, text="Playlist", text_color=TEXT, font=ctk.CTkFont("Segoe UI", 13, "bold")).pack(side="left")
        ctk.CTkButton(bar, text="Clear", width=60, height=28, corner_radius=8, fg_color=CARD2, hover_color=LINE,
                      command=lambda: (p.clear(), self._render_playlist())).pack(side="right")
        ctk.CTkButton(bar, text=I_ADD + "  Add music", width=110, height=28, corner_radius=8, fg_color=ACCENT,
                      hover_color=ACCENT_H, text_color=BG, font=ctk.CTkFont("Segoe UI", 12, "bold"),
                      command=self._add_music).pack(side="right", padx=6)
        self.playlist_box = ctk.CTkScrollableFrame(f, fg_color=CARD2, corner_radius=12)
        self.playlist_box.pack(fill="both", expand=True)
        self._playlist_sig = None
        self._render_playlist()
        return f

    def _round_btn(self, parent, glyph, cmd, size, primary=False):
        return ctk.CTkButton(parent, text=glyph, width=size, height=size, corner_radius=size // 2,
                             fg_color=ACCENT if primary else LINE, hover_color=ACCENT_H if primary else "#344056",
                             text_color=BG if primary else TEXT, font=ctk.CTkFont(ICON, 18 if primary else 14),
                             command=cmd)

    def _loopback_frame(self, host):
        f = ctk.CTkFrame(host, fg_color="transparent")
        ctk.CTkLabel(f, text="Play everything Windows is playing (Spotify, YouTube, games…) on all speakers.",
                     wraplength=350, justify="left", text_color=TEXT, anchor="w",
                     font=ctk.CTkFont("Segoe UI", 12)).pack(fill="x", pady=(4, 10))
        ctk.CTkLabel(f, text="Capture from", text_color=MUTED, anchor="w").pack(fill="x")
        names = [n for _, n, _ in list_output_devices()]
        self.loop_dev = ctk.CTkOptionMenu(f, values=names or ["(no devices)"], fg_color=CARD2, button_color=LINE,
                                          button_hover_color="#344056", dynamic_resizing=False)
        cable = [n for n in names if n.startswith("CABLE Input")]
        if cable:
            self.loop_dev.set(cable[0])
        self.loop_dev.pack(fill="x", pady=(2, 10))
        ctk.CTkButton(f, text="Start capturing", height=36, corner_radius=10, fg_color=ACCENT, hover_color=ACCENT_H,
                      text_color=BG, font=ctk.CTkFont("Segoe UI", 13, "bold"),
                      command=self._start_loopback).pack(fill="x")
        tip = ("Best setup — no echo: set Windows' sound output to \"CABLE Input (VB-Audio)\" and capture it here. "
               "Don't capture a speaker that is also switched on in the list, or it will hear itself.")
        ctk.CTkLabel(f, text=tip, wraplength=350, justify="left", text_color=WARN, anchor="w",
                     font=ctk.CTkFont("Segoe UI", 11)).pack(fill="x", pady=(12, 0))
        return f

    def _input_frame(self, host):
        f = ctk.CTkFrame(host, fg_color="transparent")
        ctk.CTkLabel(f, text="Use a recording device as the source — e.g. \"CABLE Output\", line-in or a mixer.",
                     wraplength=350, justify="left", text_color=TEXT, anchor="w",
                     font=ctk.CTkFont("Segoe UI", 12)).pack(fill="x", pady=(4, 10))
        self.inputs = list_input_devices()
        names = [n for _, n, _ in self.inputs]
        self.in_dev = ctk.CTkOptionMenu(f, values=names or ["(no devices)"], fg_color=CARD2, button_color=LINE,
                                        button_hover_color="#344056", dynamic_resizing=False)
        cable = [n for n in names if n.startswith("CABLE Output")]
        if cable:
            self.in_dev.set(cable[0])
        self.in_dev.pack(fill="x", pady=(2, 10))
        ctk.CTkButton(f, text="Start capturing", height=36, corner_radius=10, fg_color=ACCENT, hover_color=ACCENT_H,
                      text_color=BG, font=ctk.CTkFont("Segoe UI", 13, "bold"),
                      command=self._start_input).pack(fill="x")
        return f

    def _test_frame(self, host):
        f = ctk.CTkFrame(host, fg_color="transparent")
        txt = ("Plays a click every second on every speaker and phone. If one speaker clicks late, raise its "
               "Delay compensation until all clicks merge into one — or let Auto-calibrate do it for you.")
        ctk.CTkLabel(f, text=txt, wraplength=350, justify="left", text_color=TEXT, anchor="w",
                     font=ctk.CTkFont("Segoe UI", 12)).pack(fill="x", pady=(4, 12))
        ctk.CTkButton(f, text="Start sync test", height=36, corner_radius=10, fg_color=ACCENT, hover_color=ACCENT_H,
                      text_color=BG, font=ctk.CTkFont("Segoe UI", 13, "bold"),
                      command=self.engine.start_sync_test).pack(fill="x")
        return f

    def _build_master(self, parent):
        card = Card(parent, "Output")
        card.pack(fill="x", pady=(16, 0))
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(0, 6))
        ctk.CTkLabel(row, text="Master volume", text_color=TEXT, width=110, anchor="w").pack(side="left")
        self.master_vol = ctk.CTkSlider(row, from_=0, to=1, number_of_steps=100, progress_color=ACCENT,
                                    button_color=TEXT, button_hover_color=ACCENT,
                                    command=lambda v: setattr(self.engine, "master_volume", float(v)))
        self.master_vol.set(self.engine.master_volume)
        self.master_vol.pack(side="left", fill="x", expand=True)
        row2 = ctk.CTkFrame(card, fg_color="transparent")
        row2.pack(fill="x", padx=16, pady=(0, 2))
        ctk.CTkLabel(row2, text="Buffer", text_color=TEXT, width=110, anchor="w").pack(side="left")
        self.lead = ctk.CTkSlider(row2, from_=MIN_LEAD_MS, to=MAX_LEAD_MS, number_of_steps=(MAX_LEAD_MS - MIN_LEAD_MS) // 10,
                                  progress_color=VIOLET, button_color=TEXT, button_hover_color=VIOLET,
                                  command=self._lead_changed)
        self.lead.set(self.engine.lead_ms)
        self.lead.pack(side="left", fill="x", expand=True)
        self.lead_lbl = ctk.CTkLabel(row2, text="", width=60, text_color=MUTED)
        self.lead_lbl.pack(side="left")
        ctk.CTkLabel(card, text="Bigger buffer = rock-solid over weak Wi-Fi; smaller = less delay.",
                     text_color=MUTED, anchor="w", font=ctk.CTkFont("Segoe UI", 11)).pack(fill="x", padx=16, pady=(0, 14))

    def _build_outputs(self, card):
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=18, pady=(14, 6))
        ctk.CTkLabel(head, text="SPEAKERS", text_color=MUTED, font=ctk.CTkFont("Segoe UI", 11, "bold")).pack(side="left")
        ctk.CTkButton(head, text=I_REFRESH, width=32, height=30, corner_radius=8, fg_color=CARD2, hover_color=LINE,
                      font=ctk.CTkFont(ICON, 13), command=self._refresh_devices).pack(side="right")
        self.cal_btn = ctk.CTkButton(head, text=I_MIC + "  Auto-calibrate", width=150, height=30, corner_radius=8,
                                     fg_color=VIOLET, hover_color="#7748E8", font=ctk.CTkFont("Segoe UI", 12, "bold"),
                                     command=self._calibrate)
        self.cal_btn.pack(side="right", padx=6)
        mics = [n for _, n, _ in list_input_devices() if "CABLE" not in n and "Stereo Mix" not in n]
        self.mic = ctk.CTkOptionMenu(head, values=mics or ["(no microphone)"], width=190, height=30,
                                     fg_color=CARD2, button_color=LINE, button_hover_color="#344056",
                                     dynamic_resizing=False)
        self.mic.pack(side="right")

        self.cal_bar = ctk.CTkFrame(card, fg_color=CARD2, corner_radius=10)
        self.cal_lbl = ctk.CTkLabel(self.cal_bar, text="", text_color=TEXT, anchor="w")
        self.cal_lbl.pack(side="left", padx=12, pady=8)
        ctk.CTkButton(self.cal_bar, text="Cancel", width=70, height=26, fg_color=LINE, hover_color="#344056",
                      command=lambda: self.calibrator and self.calibrator.cancel()).pack(side="right", padx=8)
        self.cal_prog = ctk.CTkProgressBar(self.cal_bar, progress_color=VIOLET, fg_color=LINE)
        self.cal_prog.pack(side="right", fill="x", expand=True, padx=8)

        self.out_box = ctk.CTkScrollableFrame(card, fg_color="transparent")
        self.out_box.pack(fill="both", expand=True, padx=8, pady=(0, 10))
        self._out_sig = None

    # ---------------- actions ----------------
    def toast(self, msg, color=MUTED, ms=6000):
        self.footer.configure(text=msg, text_color=color)
        if self._toast_job:
            self.after_cancel(self._toast_job)
        self._toast_job = self.after(ms, lambda: self.footer.configure(text=""))

    def _mode_changed(self, mode):
        for f in self.src_frames.values():
            f.pack_forget()
        self.src_frames[mode].pack(fill="both", expand=True)
        self.mode = mode
        if mode == "Music":
            self.engine.set_source(self.engine.player)

    def _toggle_play(self):
        self.engine.set_source(self.engine.player)
        self.engine.player.toggle()

    def _add_music(self):
        exts = " ".join("*" + e for e in AUDIO_EXTENSIONS)
        paths = filedialog.askopenfilenames(title="Add music", filetypes=[("Audio", exts), ("All files", "*.*")])
        if paths:
            self.engine.player.add_files(list(paths))
            self._render_playlist()
            self.engine.save()

    def _render_playlist(self):
        p = self.engine.player
        sig = (tuple(p.playlist), p.current)
        if sig == self._playlist_sig:
            return
        self._playlist_sig = sig
        for w in self.playlist_box.winfo_children():
            w.destroy()
        if not p.playlist:
            ctk.CTkLabel(self.playlist_box, text="Your playlist is empty.\nClick “Add music” to pick songs.",
                         text_color=MUTED, justify="center").pack(pady=30)
            return
        for i, path in enumerate(p.playlist):
            cur = i == p.current
            row = ctk.CTkFrame(self.playlist_box, fg_color=LINE if cur else "transparent", corner_radius=8)
            row.pack(fill="x", pady=1)
            name = os.path.splitext(os.path.basename(path))[0]
            lbl = ctk.CTkLabel(row, text=("▶  " if cur else f"{i + 1:>2}.  ") + name, anchor="w",
                               text_color=ACCENT if cur else TEXT, font=ctk.CTkFont("Segoe UI", 12))
            lbl.pack(side="left", fill="x", expand=True, padx=8, pady=4)
            lbl.bind("<Double-Button-1>", lambda e, k=i: self._play_index(k))
            lbl.bind("<Button-1>", lambda e, k=i: self._play_index(k))
            ctk.CTkButton(row, text=I_X, width=26, height=24, fg_color="transparent", hover_color="#344056",
                          font=ctk.CTkFont(ICON, 10), text_color=MUTED,
                          command=lambda k=i: (p.remove(k), self._render_playlist())).pack(side="right", padx=4)

    def _play_index(self, i):
        self.engine.set_source(self.engine.player)
        self.engine.player.play_index(i)

    def _seek_release(self, _e):
        p = self.engine.player
        if p.duration_s > 0:
            p.seek(self.seek.get() * p.duration_s)
        self.seeking = False

    def _start_loopback(self):
        name = self.loop_dev.get()
        on = [o for o in self.engine.outputs.values() if o.enabled and o.name == name]
        if on:
            self.toast(f"“{name}” is switched on as a speaker — capturing it would echo forever. "
                       "Switch it off or pick another device.", WARN, 9000)
            return
        self.engine.set_source(LoopbackSource(self.engine, name))

    def _start_input(self):
        name = self.in_dev.get()
        for idx, n, _ in self.inputs:
            if n == name:
                self.engine.set_source(InputDeviceSource(self.engine, idx, n))
                return

    def _lead_changed(self, v):
        self.engine.set_lead(float(v))

    def _refresh_devices(self):
        if self.calibrator:
            return
        self.engine.refresh_devices()
        self._out_sig = None
        names = [n for _, n, _ in list_output_devices()]
        self.loop_dev.configure(values=names or ["(no devices)"])
        self.inputs = list_input_devices()
        self.in_dev.configure(values=[n for _, n, _ in self.inputs] or ["(no devices)"])
        self.toast("Audio devices refreshed", ACCENT)

    def _firewall(self):
        port = self.engine.port
        cmd = (f'/c netsh advfirewall firewall delete rule name="MultiSync Hub" >nul & '
               f'netsh advfirewall firewall add rule name="MultiSync Hub" dir=in action=allow protocol=TCP localport={port} profile=any & '
               f'netsh advfirewall firewall add rule name="MultiSync Hub" dir=in action=allow protocol=UDP localport={port} profile=any')
        try:
            r = ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", cmd, None, 0)
            if r > 32:
                self.toast(f"Firewall rule added for port {port} — phones can now connect.", ACCENT)
            else:
                self.toast("Firewall change was cancelled.", WARN)
        except Exception as e:
            self.toast(f"Could not change firewall: {e}", DANGER)

    def _calibrate(self):
        if self.calibrator:
            return
        ids = [o.id for o in self.engine.all_outputs() if (o.kind == "local" and o.enabled) or (o.kind == "remote" and not o.muted)]
        if len(ids) < 2:
            self.toast("Switch on at least two speakers/phones first.", WARN)
            return
        mic_name = self.mic.get()
        mic_idx = next((i for i, n, _ in list_input_devices() if n == mic_name), None)
        self.calibrator = calibrate.Calibrator(self.engine, mic_idx, progress=self._cal_progress)
        self.cal_bar.pack(fill="x", padx=18, pady=(0, 8), before=self.out_box)
        self.cal_btn.configure(state="disabled")
        self._cal_progress("Starting — keep the room quiet and speakers near the PC…", 0)
        calibrate.run_async(self.calibrator, ids, lambda r, e: self.after(0, self._cal_done, r, e))

    def _cal_progress(self, msg, frac):
        self.after(0, lambda: (self.cal_lbl.configure(text=msg), self.cal_prog.set(frac)))

    def _cal_done(self, res, err):
        self.calibrator = None
        self.cal_bar.pack_forget()
        self.cal_btn.configure(state="normal")
        if err:
            self.toast("Calibration failed: " + err, DANGER, 12000)
        elif res is None:
            self.toast("Calibration cancelled.", WARN)
        else:
            parts = [f"{v['name']}: {v['latency_ms']:.0f} ms ({v['drift_ms_s']:+.2f} ms/s)"
                     for v in res.values() if v.get("latency_ms") is not None]
            self.toast("Calibrated ✓  " + "  ·  ".join(parts), ACCENT, 15000)
        self.mode = None
        self._sync_seg()

    def _sync_seg(self):
        src = self.engine.source
        name = {"Music player": "Music", "System audio": "System audio", "Input device": "Input",
                "Sync test": "Sync test"}.get(src.name, "Music")
        if self.seg.get() != name:
            self.seg.set(name)
            self._mode_changed(name)

    def _close(self):
        try:
            self.engine.shutdown()
        finally:
            self.destroy()

    # ---------------- periodic refresh ----------------
    def _tick(self):
        try:
            self._update()
        except Exception as e:  # keep the UI alive no matter what
            self.toast(f"UI error: {e}", DANGER)
        self.after(120, self._tick)

    def _update(self):
        eng = self.engine
        ip, port = eng.hub_address()
        n = len(eng.server.clients)
        if eng.server.running:
            self.pill.configure(text=f"●  Hub live  {ip}:{port}   ·   {n} phone{'s' if n != 1 else ''}",
                                text_color=ACCENT)
        else:
            self.pill.configure(text="●  Hub offline", text_color=DANGER)
        self.lead_lbl.configure(text=f"{eng.lead_ms:.0f} ms")

        p = eng.player
        self.track.configure(text=p.track_name or "Nothing playing")
        src = eng.source
        sub = src.status() if src is not p else (p.error or (f"Track {p.current + 1} of {len(p.playlist)}"
                                                            if p.playlist else "Add songs (mp3, flac, wav, ogg, opus)"))
        self.track_sub.configure(text=sub, text_color=DANGER if src.error else MUTED)
        playing = src is p and not p.paused
        self.play_btn.configure(text=I_PAUSE if playing else I_PLAY)
        pos = max(0.0, p.position_s - eng.lead_ms / 1000.0) if playing else p.position_s
        if p.duration_s > 0 and not self.seeking:
            self.seek.set(min(1.0, pos / p.duration_s))
        self.t_pos.configure(text=fmt_time(pos))
        self.t_dur.configure(text=fmt_time(p.duration_s))
        self._render_playlist()

        if src.error and not self.calibrator and self.footer.cget("text") != src.error:
            self.toast(src.error, DANGER, 10000)

        outs = eng.all_outputs()
        sig = tuple(o.id for o in outs)
        if sig != self._out_sig:
            self._out_sig = sig
            for w in self.out_box.winfo_children():
                w.destroy()
            self.rows = {}
            local = [o for o in outs if o.kind == "local"]
            remote = [o for o in outs if o.kind == "remote"]
            self._section("This PC", f"{sum(o.enabled for o in local)} of {len(local)} on")
            for o in sorted(local, key=lambda o: (not o.bluetooth, o.name)):
                r = OutputRow(self.out_box, self, o)
                r.pack(fill="x", padx=8, pady=4)
                self.rows[o.id] = r
            self._section("Phones on Wi-Fi", f"{len(remote)} connected")
            if not remote:
                ctk.CTkLabel(self.out_box, text=f"Open the MultiSync app on a phone (same Wi-Fi) and tap Connect.\n"
                                                f"It finds this PC automatically, or enter {ip}:{port}",
                             text_color=MUTED, justify="center").pack(pady=18)
            for o in remote:
                r = OutputRow(self.out_box, self, o)
                r.pack(fill="x", padx=8, pady=4)
                self.rows[o.id] = r
        for r in self.rows.values():
            r.refresh()

    def _section(self, title, sub):
        f = ctk.CTkFrame(self.out_box, fg_color="transparent")
        f.pack(fill="x", padx=10, pady=(10, 2))
        ctk.CTkLabel(f, text=title, text_color=TEXT, font=ctk.CTkFont("Segoe UI", 14, "bold")).pack(side="left")
        ctk.CTkLabel(f, text=sub, text_color=MUTED, font=ctk.CTkFont("Segoe UI", 11)).pack(side="left", padx=8)


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MultiSync.Hub")
    except Exception:
        pass
    ctk.set_appearance_mode("dark")
    engine = Engine()
    engine.start()
    app = App(engine)
    app.mainloop()
