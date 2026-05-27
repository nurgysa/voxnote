"""GUI package — App class, dialogs, and shared widget factories.

Splits the monolithic ``app.py`` into focused modules:

  ui.widgets         — CTk factories shared by app + dialogs
  ui.dialogs.terms   — TermsDialog (hotword editor)
  ui.dialogs.history — HistoryDialog + HistoryViewerDialog
  ui.app             — App class + main()

The root ``app.py`` remains a thin entry point so that faulthandler is
installed BEFORE any heavy C-extension imports. The voices dialog
(speaker enrollment) was removed in the 2026-05-28 cloud-only rip-out.
"""
