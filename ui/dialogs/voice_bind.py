"""Voice-ID bind/enroll panel for «Встречи».

A finished Speechmatics meeting can carry a ``<voxnote_id>.voiceid.json``
sidecar with pending anonymous speakers. This dialog lets the operator bind each
pending voice to an existing directory person or create a new person, enrolls the
Speechmatics identifier, re-renders transcript.md in place, and drains the
sidecar so the «🆕 новые голоса» badge disappears.
"""
from __future__ import annotations

import os
from tkinter import messagebox

import customtkinter as ctk

from audio_io import load_mono_float32
from directory.schema import Person, Voiceprint
from directory.store import DirectoryError
from processing import vault_note
from processing.store import read_voxnote_id
from processing.voiceid import playback_window, rerender_named_note
from theme import (
    BG,
    BLUE,
    BLUE_DIM,
    BLUE_SURFACE,
    BORDER,
    FONT,
    INPUT_BG,
    SURFACE,
    SURFACE_BRIGHT,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from utils import delete_voiceid_sidecar, load_segments_sidecar, load_voiceid_sidecar

try:  # audio preview is optional; binding/re-render must still work headlessly.
    import sounddevice as sd
except (ImportError, OSError):  # pragma: no cover - platform-dependent import
    sd = None

_SELECT_PERSON = "— выберите —"
_CREATE_NEW = "➕ Создать нового"


class VoiceBindDialog(ctk.CTkToplevel):
    """Bind pending Speechmatics Voice-ID entries to directory people."""

    def __init__(self, parent, item, store, on_applied):
        super().__init__(parent)
        self.title("Новые голоса")
        self.geometry("760x560")
        self.configure(fg_color=BG)
        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._close)

        self._item = item
        self._store = store
        self._on_applied = on_applied
        self._meeting_folder = item.meeting_folder or ""
        self._voxnote_id = read_voxnote_id(self._meeting_folder) or ""
        self._sidecar = load_voiceid_sidecar(self._voxnote_id) if self._voxnote_id else None
        self._pending = list((self._sidecar or {}).get("pending") or [])
        self._row_vars: dict[str, tuple[ctk.StringVar, ctk.StringVar]] = {}

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build()

    def _people_names(self) -> list[str]:
        people = sorted(self._store.people(), key=lambda p: p.full_name)
        return [p.full_name for p in people if p.full_name]

    def _build(self) -> None:
        header = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, height=48)
        header.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(
            header,
            text="Новые голоса",
            font=ctk.CTkFont(family=FONT, size=16, weight="bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=0, column=0, padx=16, pady=12, sticky="w")

        body = ctk.CTkScrollableFrame(self, fg_color=SURFACE, corner_radius=12)
        body.grid(row=1, column=0, padx=16, pady=12, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)

        if not self._pending:
            ctk.CTkLabel(
                body,
                text="Нет новых голосов для привязки",
                font=ctk.CTkFont(family=FONT, size=13),
                text_color=TEXT_SECONDARY,
            ).grid(row=0, column=0, padx=12, pady=24)
        else:
            values = [_SELECT_PERSON] + self._people_names() + [_CREATE_NEW]
            for row_idx, entry in enumerate(self._pending):
                self._build_pending_row(body, row_idx, entry, values)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, padx=16, pady=(0, 14), sticky="ew")
        footer.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(
            footer,
            text="Применить",
            width=130,
            height=36,
            corner_radius=18,
            font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
            fg_color=BLUE,
            hover_color=BLUE_DIM,
            text_color="#FFFFFF",
            command=self._apply,
        ).grid(row=0, column=1, padx=(8, 0))
        ctk.CTkButton(
            footer,
            text="Отмена",
            width=110,
            height=36,
            corner_radius=18,
            font=ctk.CTkFont(family=FONT, size=13),
            fg_color=BLUE_SURFACE,
            hover_color=SURFACE_BRIGHT,
            text_color="#8AB4F8",
            command=self._close,
        ).grid(row=0, column=2, padx=(8, 0))

    def _build_pending_row(self, parent, row_idx: int, entry: dict, values: list[str]) -> None:
        label = entry.get("label") or f"SPEAKER_{row_idx + 1}"
        frame = ctk.CTkFrame(parent, fg_color=SURFACE_BRIGHT, corner_radius=10)
        frame.grid(row=row_idx, column=0, padx=6, pady=6, sticky="ew")
        frame.grid_columnconfigure(1, weight=1)

        title = f"{label} · {entry.get('sample_text') or 'без фрагмента'}"
        ctk.CTkLabel(
            frame,
            text=title,
            anchor="w",
            wraplength=480,
            font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=0, column=0, columnspan=3, padx=10, pady=(10, 4), sticky="ew")

        selected_var = ctk.StringVar(value=_SELECT_PERSON)
        new_name_var = ctk.StringVar()
        self._row_vars[label] = (selected_var, new_name_var)

        ctk.CTkOptionMenu(
            frame,
            values=values or [_CREATE_NEW],
            variable=selected_var,
            fg_color=INPUT_BG,
            button_color=BLUE_SURFACE,
            button_hover_color=BLUE_DIM,
            text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT, size=12),
        ).grid(row=1, column=0, padx=10, pady=(0, 10), sticky="ew")
        ctk.CTkEntry(
            frame,
            textvariable=new_name_var,
            placeholder_text="ФИО для нового человека",
            fg_color=INPUT_BG,
            border_color=BORDER,
            border_width=1,
            text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT, size=12),
        ).grid(row=1, column=1, padx=(0, 10), pady=(0, 10), sticky="ew")
        ctk.CTkButton(
            frame,
            text="▶ Прослушать",
            width=120,
            height=32,
            corner_radius=16,
            font=ctk.CTkFont(family=FONT, size=12),
            fg_color=BLUE_SURFACE,
            hover_color=SURFACE_BRIGHT,
            text_color="#8AB4F8",
            command=lambda ent=entry: self._play_preview(ent),
        ).grid(row=1, column=2, padx=(0, 10), pady=(0, 10))

    def _person_by_name(self, full_name: str):
        for person in self._store.people():
            if person.full_name == full_name:
                return person
        return None

    def _resolve_person(
        self,
        selected: str,
        new_name: str,
        new_people_by_name: dict[str, Person] | None = None,
    ):
        if selected == _SELECT_PERSON:
            return None
        name = new_name.strip() if selected == _CREATE_NEW else selected.strip()
        if not name:
            return None
        existing = self._person_by_name(name)
        if existing is not None:
            return existing
        if new_people_by_name is not None:
            if name not in new_people_by_name:
                new_people_by_name[name] = Person(full_name=name)
            return new_people_by_name[name]
        return Person(full_name=name)

    def _play_preview(self, entry: dict) -> None:
        if sd is None:
            messagebox.showwarning(
                "Новые голоса",
                "Аудио-предпрослушивание недоступно",
                parent=self,
            )
            return
        source_path = (self._sidecar or {}).get("note_meta", {}).get("source_path")
        source_path = (
            source_path
            or getattr(self._item, "source_path", None)
            or getattr(self._item, "audio_path", None)
        )
        if not source_path or not os.path.isfile(source_path):
            messagebox.showwarning("Новые голоса", "Исходный аудиофайл не найден", parent=self)
            return
        try:
            data, sr = load_mono_float32(source_path)
            start, end = playback_window(len(data), sr, float(entry.get("first_start", 0.0)))
            if start == end:
                raise ValueError("empty preview window")
            sd.stop()
            sd.play(data[start:end], sr)
        except (OSError, ValueError, RuntimeError) as exc:
            messagebox.showerror(
                "Новые голоса",
                f"Не удалось воспроизвести фрагмент:\n\n{exc}",
                parent=self,
            )

    def _apply(self) -> None:
        if not self._voxnote_id or not self._sidecar:
            messagebox.showerror("Новые голоса", "Voice-ID sidecar не найден", parent=self)
            return
        segments = load_segments_sidecar(self._voxnote_id)
        if not segments:
            messagebox.showerror("Новые голоса", "Segments sidecar не найден", parent=self)
            return

        names_by_label: dict[str, str] = {}
        assignments = []
        new_people_by_name: dict[str, Person] = {}
        try:
            for row_idx, entry in enumerate(self._pending):
                label = entry.get("label") or f"SPEAKER_{row_idx + 1}"
                identifier = entry.get("identifier") or ""
                selected_var, new_name_var = self._row_vars[label]
                person = self._resolve_person(
                    selected_var.get(),
                    new_name_var.get(),
                    new_people_by_name,
                )
                if person is None or not identifier:
                    messagebox.showwarning(
                        "Новые голоса",
                        "Укажи человека для каждого нового голоса",
                        parent=self,
                    )
                    return
                names_by_label[label] = person.full_name
                assignments.append((person, identifier))

            content = rerender_named_note(
                segments,
                names_by_label,
                self._sidecar.get("note_meta") or {},
            )
            vault_note.overwrite_transcript_note(self._meeting_folder, content)
            for person, identifier in assignments:
                if self._store.get_person(person.id) is None:
                    self._store.upsert_person(person)
                self._store.add_voiceprint(
                    person.id,
                    Voiceprint(
                        identifier=identifier,
                        model=self._sidecar.get("model", ""),
                        source_meeting=self._voxnote_id,
                    ),
                )
            delete_voiceid_sidecar(self._voxnote_id)
        except (DirectoryError, OSError, ValueError) as exc:
            messagebox.showerror(
                "Новые голоса",
                f"Не удалось применить привязку:\n\n{exc}",
                parent=self,
            )
            return

        self._on_applied()
        self._close()

    def _close(self):
        if sd is not None:
            sd.stop()
        self.grab_release()
        self.destroy()
