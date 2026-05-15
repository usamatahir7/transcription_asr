# Audio JSON Transcript Editor

A Streamlit app for reviewing channel audio and editing word-level transcript JSON.

The app lets you upload a labeling JSON file and matching channel `.wav` files, listen to each speaker/channel, edit word text and timestamps from an interactive transcript, and download a fixed JSON.

## Features

- Upload a labeling JSON file and multiple `.wav` / `.wave` channel audio files, or load channel WAV files from a Google Drive folder link.
- Match audio files to speakers using participant email or username in the WAV filename.
- Sticky waveform/audio player with play, pause, seek, skip, and speed controls.
- Highlight the current word while audio plays.
- Click a transcript word to seek audio to that word and open an edit popup.
- Edit word text, start time, and end time.
- Add or delete words.
- Highlight suspicious timestamp overlaps in red.
- Warn before switching speakers or downloading while frontend edits are unsaved.
- Download the fixed JSON.

## Expected Inputs

### JSON

The uploaded JSON should include a `participants` object. Each participant should have transcript data under:

```text
participants.<participant_id>.annotation.updatedTranscription[*].words
```

Each word item should include fields like:

```json
{
  "text": "hello",
  "start": 1230,
  "end": 1450,
  "confidence": 0.99
}
```

Timestamps are stored in milliseconds.

### Audio

Upload one WAV file per speaker/channel, or provide a Google Drive folder containing the WAV files. The filename should contain the participant email or username, for example:

```text
paul.g1@turing.com.wav
paul.g1.wav
```

When using a Google Drive folder link, the app loads files whose names end with:

```text
@turing.com.wav
```

The Drive folder must be accessible to the deployed app, such as a public/shared folder.

## Local Setup

Create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\activate
```

Install dependencies:

```powershell
pip install -r requirements.txt
```

Run the app:

```powershell
streamlit run app.py
```

Then open the local URL printed by Streamlit, usually:

```text
http://localhost:8501
```

## How To Use

1. Upload the labeling JSON in the sidebar.
2. Choose an audio source:
   - upload the matching WAV files, or
   - paste a Google Drive folder link containing `@turing.com.wav` files.
3. Select a speaker/channel.
4. Use the waveform player to listen.
5. Click any transcript word to edit text or timing.
6. Click the green **Save edited JSON** button above the transcript to push frontend edits into the app state.
7. Click **Download fixed JSON** in the sidebar.

If you change speakers or download while edits are unsaved, the app will warn you and offer an "anyway" option.

## Deploying To Streamlit Cloud

Yes, this can be deployed on Streamlit Cloud:

1. Push this project to GitHub.
2. Make sure `app.py`, `requirements.txt`, `README.md`, `audio_component/`, and `transcript_component/` are committed.
3. Do not commit `.venv/`, WAV files, or local JSON data.
4. In Streamlit Cloud, create a new app from the GitHub repo.
5. Set the main file path to:

```text
app.py
```

Note: large WAV uploads can use significant memory because audio is kept in session memory and sent to the browser for playback.

## Repository Notes

The `.gitignore` is configured to exclude local virtual environments, uploaded data, audio files, JSON files, caches, and editor artifacts.
