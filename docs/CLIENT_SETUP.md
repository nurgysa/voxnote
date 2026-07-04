# VoxNote - installation and first run

**Version:** v0.1.0, cloud-only
**Support:** [GitHub Issues](https://github.com/nurgysa/voxnote/issues)

---

## 1. What you need

- Windows 10 64-bit or Windows 11.
- About 2 GB of free disk space.
- Internet access. VoxNote uses cloud APIs and does not work offline.
- A transcription provider API key. AssemblyAI is the default recommended setup.
- An OpenRouter API key if you want LLM task extraction or meeting protocols.
- Optional Linear, Glide, or Trello API keys if you want to send tasks to a task tracker.

You do **not** need an NVIDIA GPU, Hugging Face account, Python, or local ML
models. Recognition runs through managed cloud providers.

---

## 2. Installation

1. Download `VoxNote-v0.1.0.zip` from
   [Releases](https://github.com/nurgysa/voxnote/releases/latest).
2. Unzip it into `C:\Apps\VoxNote\` or any other user-writable folder.
3. Do **not** unzip it into `C:\Program Files\`. VoxNote stores logs and local
   app files beside the executable, and normal Windows users cannot write there.
   Meeting files are stored under `Documents\VoxNote\meetings\` by default.
4. Double-click `VoxNote.exe`.
5. On first launch, Windows Defender may delay startup for a few seconds while it
   scans the new executable. If Defender blocks it completely, add the unpacked
   VoxNote folder to Defender exclusions.

---

## 3. First launch

On first launch, VoxNote shows a setup banner asking you to open Settings and add
required API keys.

Open the Settings screen from the banner or main window.

### 3.1 AssemblyAI, required for the default transcription setup

1. Create an account at <https://www.assemblyai.com>.
2. Copy your API key from <https://www.assemblyai.com/app/account>.
3. Paste it into Settings under the cloud transcription API key field.
4. Use the built-in check button if available; it should show a successful result.

AssemblyAI Universal is the default recommendation for Kazakh + Russian + English
code-switching meetings. Quality depends on audio clarity, background noise, and
speaker overlap. If quality is not good enough for your recordings, try another
provider in Settings.

Provider pricing changes over time. Check the official provider page before
running long recordings.

### 3.2 OpenRouter, required for protocols and task extraction

1. Create an account at <https://openrouter.ai/>.
2. Add a small balance for testing.
3. Create a key at <https://openrouter.ai/keys>.
4. Paste it into the OpenRouter field in Settings.
5. Keep the default model unless you have a reason to change it.

OpenRouter is only required for LLM-based follow-up, such as extracting tasks or
generating a meeting protocol. Plain transcription can run without it.

### 3.3 Linear, Glide, or Trello, optional

Configure these only if you want VoxNote to send extracted tasks to an external
task tracker.

- **Linear:** create a personal API key in Linear settings.
- **Glide:** use the Glide API key from the Glide API documentation/settings.
- **Trello:** create an API key and token from <https://trello.com/app-key>, then
  select the target board and list inside VoxNote.

If no tracker is configured, extracted tasks remain in local files next to the
transcript and can be copied manually.

---

## 4. First test

1. Load audio by dragging an MP3/M4A/WAV file into the window or by using the file
   picker. You can also record audio inside the app.
2. Start transcription.
   - Runtime depends on your internet upload speed and the selected provider.
   - The full audio file is uploaded to the selected cloud provider.
   - For a cheap smoke test, start with a short non-sensitive recording.
3. When transcription completes, you should see a transcript with speaker labels
   such as `Speaker A:` and `Speaker B:`.
4. Optional: run task extraction.
   - Choose the LLM model.
   - Choose a backend if you want to send tasks externally.
   - Keep meeting protocol generation enabled if you want `protocol.md`.
   - Optionally attach documents such as agendas, budgets, PDFs, DOCX, PPTX, or
     XLSX files. They are converted to text and used as LLM context.
5. After the run, the meeting folder contains artifacts such as:
   - source audio copy or source path metadata, depending on configuration;
   - `transcript.md`;
   - `description.md`;
   - `tasks.json`, if task extraction ran;
   - `protocol.md`, if meeting protocol generation ran.
6. Use the Meetings view to open previous transcripts.
7. Use Audio Cutter if you need to trim silence, pre-meeting chatter, or unrelated
   audio before transcription.

For Mini-AGI validation, a short clip only proves technical plumbing. Product
value should be evaluated on realistic 60-180 minute meeting material after cost
and sensitivity are explicitly approved.

---

## 5. Meeting storage

Each processed recording gets its own meeting folder under the configured
meetings directory. The default location is:

```text
Documents\VoxNote\meetings\
```

Folder names include the date/time and source file name.

You can change the meetings directory in Settings. Common choices are an
Obsidian vault folder, OneDrive, or Google Drive Desktop sync folder.

If the old meetings folder already contains files and you choose a new location,
VoxNote can either:

- move existing meetings to the new location; or
- switch only future meetings to the new location.

If a target folder already contains a meeting with the same name, VoxNote avoids
overwriting and uses an import suffix.

On first launch after older internal builds, VoxNote may detect the legacy
`history/` folder and offer migration choices. Fresh public installs do not need
this step.

---

## 6. Troubleshooting

| Symptom | What to try |
|---|---|
| App does not start or closes immediately | Windows Defender or another antivirus may have flagged the PyInstaller bundle. Add the VoxNote folder to exclusions. You can also start it from `cmd.exe` to see errors. |
| Write error or permission denied | Move VoxNote out of `C:\Program Files\` and into a user-writable folder such as `C:\Apps\VoxNote\`. |
| You cannot find meeting files | By default they are under `Documents\VoxNote\meetings\`. If corporate policy blocks Documents, VoxNote falls back to a folder beside the executable. |
| Setup banner does not disappear after saving a key | Close VoxNote completely and start it again. The banner check runs during startup. |
| `401 Unauthorized` during transcription | The provider key is wrong or expired. Re-check it in the provider dashboard and paste it again in Settings. |
| `Insufficient credits` from the transcription provider | Add balance or free credits on the provider dashboard. |
| `Insufficient credits` from OpenRouter | Add OpenRouter credits at <https://openrouter.ai/credits>. |
| Transcript is empty or clearly wrong for Kazakh | Try Gladia or Speechmatics, or retry with cleaner audio. |
| Protocol fields all say nothing was found | Try another LLM model or attach relevant documents as context. |
| File is larger than 2 GB | This is not a tested path. Trim the recording with Audio Cutter first. |
| Unexpected crash | Open a GitHub issue and attach a redacted log archive from the app diagnostics screen. API keys are stripped automatically. If the app cannot open at all, attach `_internal\logs\app.log`, `_internal\logs\faulthandler.log`, and `%TEMP%\voxnote-bootstrap.log` if they exist. |

---

## 7. What's inside

- Transcription: cloud STT providers, with AssemblyAI Universal as the default
  recommendation for KZ/RU/EN code switching.
- Task extraction and meeting protocol: OpenRouter-backed LLM calls.
- Audio processing: bundled ffmpeg.
- UI: Python + CustomTkinter desktop app.
- Privacy model: audio goes to the selected transcription provider; transcript
  text can go to OpenRouter only when LLM follow-up is used. Do not use VoxNote
  for content that is not allowed to be sent to those providers.
- Meeting history is stored locally under the configured meetings directory.

Alternative cloud providers can be selected in Settings if the default provider
is not appropriate for your recordings.

---

## 8. Feedback

This is an early public version. Feedback is welcome through
[GitHub Issues](https://github.com/nurgysa/voxnote/issues), especially:

- transcription quality on your real meeting types;
- Kazakh recognition quality on your audio;
- protocol structure gaps;
- task extraction false positives or missed actions;
- setup problems on Windows 10/11.
