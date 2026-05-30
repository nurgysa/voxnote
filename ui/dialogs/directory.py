"""«Справочники» — people + projects directory editor (Phase A UI).

Two-tab CRUD over directory.store.DirectoryStore. «Люди»: ФИО, role, project
membership (checkboxes). «Проекты»: name + description. Mutations persist
immediately via DirectoryStore (atomic JSON at ~/.audio-transcriber/directory.json).
Mirrors ui/dialogs/terms.py for the list/row/button style.
"""
from __future__ import annotations

from tkinter import messagebox

import customtkinter as ctk

from directory.schema import Person, Project
from directory.store import DirectoryError, DirectoryStore
from theme import (
    BG,
    BLUE,
    BLUE_DIM,
    BORDER,
    FONT,
    INPUT_BG,
    RED,
    SURFACE,
    SURFACE_BRIGHT,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)


class DirectoryDialog(ctk.CTkToplevel):
    """CRUD editor for the people/projects directory («Справочники»)."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Справочники")
        self.geometry("680x640")
        self.configure(fg_color=BG)
        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._close)

        self._store = DirectoryStore()
        try:
            self._store.load()
        except DirectoryError as exc:
            # Corrupt file: warn before starting empty. The next successful save
            # atomically overwrites the bad file, so surface the loss now rather
            # than letting it vanish silently.
            messagebox.showwarning(
                "Справочники",
                f"Файл справочников повреждён — начинаем с пустого списка.\n\n{exc}",
                parent=self,
            )

        self._editing_person_id: str | None = None
        self._editing_project_id: str | None = None
        self._project_check_vars: dict[str, ctk.BooleanVar] = {}

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._tabview = ctk.CTkTabview(
            self,
            fg_color=SURFACE,
            segmented_button_selected_color=BLUE,
            segmented_button_selected_hover_color=BLUE_DIM,
            text_color=TEXT_PRIMARY,
        )
        self._tabview.grid(row=0, column=0, padx=16, pady=16, sticky="nsew")
        self._build_people_tab(self._tabview.add("Люди"))
        self._build_projects_tab(self._tabview.add("Проекты"))

        self._render_people()
        self._render_projects()
        self._clear_person_form()
        self._clear_project_form()

    # ───────────────────────── People tab ─────────────────────────
    def _build_people_tab(self, parent) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)

        self._people_list = ctk.CTkScrollableFrame(
            parent, fg_color=SURFACE, corner_radius=10,
        )
        self._people_list.grid(row=0, column=0, padx=4, pady=(4, 8), sticky="nsew")
        self._people_list.grid_columnconfigure(0, weight=1)

        form = ctk.CTkFrame(parent, fg_color=SURFACE_BRIGHT, corner_radius=10)
        form.grid(row=1, column=0, padx=4, pady=4, sticky="ew")
        form.grid_columnconfigure(0, weight=1)

        self._person_name_var = ctk.StringVar()
        self._person_role_var = ctk.StringVar()

        ctk.CTkEntry(
            form, textvariable=self._person_name_var, height=34,
            placeholder_text="ФИО", fg_color=INPUT_BG, border_color=BORDER,
            border_width=1, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT, size=13),
        ).grid(row=0, column=0, padx=10, pady=(10, 4), sticky="ew")

        ctk.CTkEntry(
            form, textvariable=self._person_role_var, height=34,
            placeholder_text="Должностные обязанности", fg_color=INPUT_BG,
            border_color=BORDER, border_width=1, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT, size=13),
        ).grid(row=1, column=0, padx=10, pady=4, sticky="ew")

        ctk.CTkLabel(
            form, text="Проекты:", anchor="w",
            font=ctk.CTkFont(family=FONT, size=12), text_color=TEXT_SECONDARY,
        ).grid(row=2, column=0, padx=10, pady=(6, 0), sticky="w")

        self._person_projects_frame = ctk.CTkFrame(form, fg_color="transparent")
        self._person_projects_frame.grid(row=3, column=0, padx=10, pady=4, sticky="ew")

        btns = ctk.CTkFrame(form, fg_color="transparent")
        btns.grid(row=4, column=0, padx=10, pady=(4, 10), sticky="w")

        ctk.CTkButton(
            btns, text="Новый", width=90, height=32, corner_radius=16,
            fg_color="transparent", hover_color=BORDER, text_color=TEXT_SECONDARY,
            font=ctk.CTkFont(family=FONT, size=13), command=self._clear_person_form,
        ).grid(row=0, column=0, padx=(0, 6))
        ctk.CTkButton(
            btns, text="Сохранить", width=120, height=32, corner_radius=16,
            fg_color=BLUE, hover_color=BLUE_DIM, text_color="#FFFFFF",
            font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
            command=self._save_person,
        ).grid(row=0, column=1, padx=6)
        self._person_delete_btn = ctk.CTkButton(
            btns, text="Удалить", width=100, height=32, corner_radius=16,
            fg_color="transparent", hover_color=BORDER, text_color=RED,
            font=ctk.CTkFont(family=FONT, size=13), command=self._delete_person,
        )
        self._person_delete_btn.grid(row=0, column=2, padx=6)

    def _render_people(self) -> None:
        for w in self._people_list.winfo_children():
            w.destroy()
        people = self._store.people()
        if not people:
            ctk.CTkLabel(
                self._people_list, text="Нет людей",
                font=ctk.CTkFont(family=FONT, size=13), text_color=TEXT_SECONDARY,
            ).grid(row=0, column=0, pady=16)
            return
        for i, p in enumerate(people):
            row = ctk.CTkFrame(self._people_list, fg_color=SURFACE_BRIGHT, corner_radius=8)
            row.grid(row=i, column=0, padx=4, pady=3, sticky="ew")
            row.grid_columnconfigure(0, weight=1)
            label = p.full_name + (f" — {p.role}" if p.role else "")
            ctk.CTkButton(
                row, text=label, anchor="w", height=34, fg_color="transparent",
                hover_color=BORDER, text_color=TEXT_PRIMARY,
                font=ctk.CTkFont(family=FONT, size=13),
                command=lambda pid=p.id: self._load_person(pid),
            ).grid(row=0, column=0, padx=(8, 4), pady=4, sticky="ew")
            ctk.CTkButton(
                row, text="✕", width=32, height=32, corner_radius=16,
                fg_color="transparent", hover_color=BORDER, text_color=RED,
                font=ctk.CTkFont(family=FONT, size=14),
                command=lambda pid=p.id: self._delete_person(pid),
            ).grid(row=0, column=1, padx=(0, 6))

    def _rebuild_person_projects(self, selected_ids: set[str]) -> None:
        for w in self._person_projects_frame.winfo_children():
            w.destroy()
        self._project_check_vars = {}
        projects = self._store.projects()
        if not projects:
            ctk.CTkLabel(
                self._person_projects_frame, text="(сначала добавьте проекты)",
                font=ctk.CTkFont(family=FONT, size=12), text_color=TEXT_SECONDARY,
            ).grid(row=0, column=0, sticky="w")
            return
        for i, pr in enumerate(projects):
            var = ctk.BooleanVar(value=pr.id in selected_ids)
            self._project_check_vars[pr.id] = var
            ctk.CTkCheckBox(
                self._person_projects_frame, text=pr.name, variable=var,
                fg_color=BLUE, hover_color=BLUE_DIM, text_color=TEXT_PRIMARY,
                font=ctk.CTkFont(family=FONT, size=12),
            ).grid(row=i // 2, column=i % 2, padx=4, pady=2, sticky="w")

    def _clear_person_form(self) -> None:
        self._editing_person_id = None
        self._person_name_var.set("")
        self._person_role_var.set("")
        self._rebuild_person_projects(set())
        self._person_delete_btn.configure(state="disabled")

    def _load_person(self, person_id: str) -> None:
        p = self._store.get_person(person_id)
        if p is None:
            return
        self._editing_person_id = p.id
        self._person_name_var.set(p.full_name)
        self._person_role_var.set(p.role)
        self._rebuild_person_projects(set(p.project_ids))
        self._person_delete_btn.configure(state="normal")

    def _save_person(self) -> None:
        name = self._person_name_var.get().strip()
        if not name:
            return
        project_ids = [pid for pid, var in self._project_check_vars.items() if var.get()]
        role = self._person_role_var.get().strip()
        if self._editing_person_id:
            person = self._store.get_person(self._editing_person_id) or Person(full_name=name)
            person.full_name = name
            person.role = role
            person.project_ids = project_ids
        else:
            person = Person(full_name=name, role=role, project_ids=project_ids)
        try:
            self._store.upsert_person(person)
        except DirectoryError as exc:
            messagebox.showerror(
                "Справочники", f"Не удалось сохранить человека:\n\n{exc}",
                parent=self,
            )
            return
        self._render_people()
        self._clear_person_form()

    def _delete_person(self, person_id: str | None = None) -> None:
        pid = person_id or self._editing_person_id
        if not pid:
            return
        try:
            self._store.delete_person(pid)
        except DirectoryError as exc:
            messagebox.showerror(
                "Справочники", f"Не удалось удалить человека:\n\n{exc}",
                parent=self,
            )
            return
        self._render_people()
        self._clear_person_form()

    # ───────────────────────── Projects tab ─────────────────────────
    def _build_projects_tab(self, parent) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)

        self._projects_list = ctk.CTkScrollableFrame(
            parent, fg_color=SURFACE, corner_radius=10,
        )
        self._projects_list.grid(row=0, column=0, padx=4, pady=(4, 8), sticky="nsew")
        self._projects_list.grid_columnconfigure(0, weight=1)

        form = ctk.CTkFrame(parent, fg_color=SURFACE_BRIGHT, corner_radius=10)
        form.grid(row=1, column=0, padx=4, pady=4, sticky="ew")
        form.grid_columnconfigure(0, weight=1)

        self._project_name_var = ctk.StringVar()
        ctk.CTkEntry(
            form, textvariable=self._project_name_var, height=34,
            placeholder_text="Название проекта", fg_color=INPUT_BG,
            border_color=BORDER, border_width=1, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT, size=13),
        ).grid(row=0, column=0, padx=10, pady=(10, 4), sticky="ew")

        self._project_desc_box = ctk.CTkTextbox(
            form, height=80, fg_color=INPUT_BG, border_color=BORDER, border_width=1,
            text_color=TEXT_PRIMARY, font=ctk.CTkFont(family=FONT, size=13),
        )
        self._project_desc_box.grid(row=1, column=0, padx=10, pady=4, sticky="ew")

        btns = ctk.CTkFrame(form, fg_color="transparent")
        btns.grid(row=2, column=0, padx=10, pady=(4, 10), sticky="w")
        ctk.CTkButton(
            btns, text="Новый", width=90, height=32, corner_radius=16,
            fg_color="transparent", hover_color=BORDER, text_color=TEXT_SECONDARY,
            font=ctk.CTkFont(family=FONT, size=13), command=self._clear_project_form,
        ).grid(row=0, column=0, padx=(0, 6))
        ctk.CTkButton(
            btns, text="Сохранить", width=120, height=32, corner_radius=16,
            fg_color=BLUE, hover_color=BLUE_DIM, text_color="#FFFFFF",
            font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
            command=self._save_project,
        ).grid(row=0, column=1, padx=6)
        self._project_delete_btn = ctk.CTkButton(
            btns, text="Удалить", width=100, height=32, corner_radius=16,
            fg_color="transparent", hover_color=BORDER, text_color=RED,
            font=ctk.CTkFont(family=FONT, size=13), command=self._delete_project,
        )
        self._project_delete_btn.grid(row=0, column=2, padx=6)

    def _render_projects(self) -> None:
        for w in self._projects_list.winfo_children():
            w.destroy()
        projects = self._store.projects()
        if not projects:
            ctk.CTkLabel(
                self._projects_list, text="Нет проектов",
                font=ctk.CTkFont(family=FONT, size=13), text_color=TEXT_SECONDARY,
            ).grid(row=0, column=0, pady=16)
            return
        for i, pr in enumerate(projects):
            row = ctk.CTkFrame(self._projects_list, fg_color=SURFACE_BRIGHT, corner_radius=8)
            row.grid(row=i, column=0, padx=4, pady=3, sticky="ew")
            row.grid_columnconfigure(0, weight=1)
            ctk.CTkButton(
                row, text=pr.name, anchor="w", height=34, fg_color="transparent",
                hover_color=BORDER, text_color=TEXT_PRIMARY,
                font=ctk.CTkFont(family=FONT, size=13),
                command=lambda pid=pr.id: self._load_project(pid),
            ).grid(row=0, column=0, padx=(8, 4), pady=4, sticky="ew")
            ctk.CTkButton(
                row, text="✕", width=32, height=32, corner_radius=16,
                fg_color="transparent", hover_color=BORDER, text_color=RED,
                font=ctk.CTkFont(family=FONT, size=14),
                command=lambda pid=pr.id: self._delete_project(pid),
            ).grid(row=0, column=1, padx=(0, 6))

    def _clear_project_form(self) -> None:
        self._editing_project_id = None
        self._project_name_var.set("")
        self._project_desc_box.delete("1.0", "end")
        self._project_delete_btn.configure(state="disabled")

    def _load_project(self, project_id: str) -> None:
        pr = self._store.get_project(project_id)
        if pr is None:
            return
        self._editing_project_id = pr.id
        self._project_name_var.set(pr.name)
        self._project_desc_box.delete("1.0", "end")
        self._project_desc_box.insert("1.0", pr.description)
        self._project_delete_btn.configure(state="normal")

    def _save_project(self) -> None:
        name = self._project_name_var.get().strip()
        if not name:
            return
        description = self._project_desc_box.get("1.0", "end").strip()
        if self._editing_project_id:
            pr = self._store.get_project(self._editing_project_id) or Project(name=name)
            pr.name = name
            pr.description = description
        else:
            pr = Project(name=name, description=description)
        try:
            self._store.upsert_project(pr)
        except DirectoryError as exc:
            messagebox.showerror(
                "Справочники", f"Не удалось сохранить проект:\n\n{exc}",
                parent=self,
            )
            return
        self._render_projects()
        self._clear_project_form()

    def _delete_project(self, project_id: str | None = None) -> None:
        pid = project_id or self._editing_project_id
        if not pid:
            return
        try:
            self._store.delete_project(pid)
        except DirectoryError as exc:
            messagebox.showerror(
                "Справочники", f"Не удалось удалить проект:\n\n{exc}",
                parent=self,
            )
            return
        self._render_projects()
        self._clear_project_form()
        # delete_project ref-cascades the id out of every person; rebuild the
        # People form's checkboxes from the live selection so the just-removed
        # project can't linger as a stale ticked box and get re-saved.
        selected = {cid for cid, var in self._project_check_vars.items() if var.get()}
        self._rebuild_person_projects(selected)

    def _close(self) -> None:
        self.grab_release()
        self.destroy()
