import base64
import json
import math
import os
import re
import tempfile
import wave
from io import BytesIO
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components


APP_DIR = Path(__file__).parent
AUDIO_EXTENSIONS = {".wav", ".wave"}


st.set_page_config(
    page_title="Audio JSON Editor",
    page_icon=":memo:",
    layout="wide",
)

TRANSCRIPT_COMPONENT = components.declare_component(
    "transcript_editor",
    path=str(APP_DIR / "transcript_component"),
)
AUDIO_COMPONENT = components.declare_component(
    "sticky_audio_player",
    path=str(APP_DIR / "audio_component"),
)


def parse_uploaded_json(uploaded_file) -> dict:
    data = json.loads(uploaded_file.getvalue().decode("utf-8"))
    if not isinstance(data, dict) or "participants" not in data:
        raise ValueError("Uploaded JSON must be an object with a participants key.")
    return data


def update_progress(progress, value: int, text: str) -> None:
    if progress is not None:
        progress.progress(value, text=text)


def parse_uploaded_audio_files(uploaded_files, progress=None) -> dict[str, bytes]:
    audio_files = {}
    valid_files = [
        uploaded_file
        for uploaded_file in uploaded_files
        if Path(uploaded_file.name).suffix.lower() in AUDIO_EXTENSIONS
    ]
    total_files = max(1, len(valid_files))
    update_progress(progress, 5, "Preparing uploaded audio files...")
    for index, uploaded_file in enumerate(valid_files, start=1):
        filename = Path(uploaded_file.name).name
        audio_files[filename] = bytes(uploaded_file.getbuffer())
        percent = 5 + int(index / total_files * 90)
        update_progress(progress, percent, f"Loaded {index}/{len(valid_files)} WAV files...")
    update_progress(progress, 100, "Audio files ready.")
    return audio_files


def is_drive_folder_link(link: str) -> bool:
    return "/folders/" in link or "folders/" in link


def extract_drive_id(link: str) -> str | None:
    patterns = [
        r"/d/([a-zA-Z0-9_-]+)",
        r"id=([a-zA-Z0-9_-]+)",
        r"/folders/([a-zA-Z0-9_-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, link)
        if match:
            return match.group(1)
    if re.fullmatch(r"[a-zA-Z0-9_-]{20,}", link.strip()):
        return link.strip()
    return None


def is_turing_wav(path: Path) -> bool:
    return path.is_file() and path.name.lower().endswith("@turing.com.wav")


def fetch_turing_wavs_from_drive(drive_link: str, _progress=None) -> dict[str, bytes]:
    drive_link = drive_link.strip()
    if not drive_link:
        return {}

    try:
        import gdown
    except ImportError as exc:
        raise RuntimeError(
            "Google Drive folder loading requires the `gdown` package. "
            "Install dependencies with `pip install -r requirements.txt`."
        ) from exc

    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            update_progress(_progress, 5, "Connecting to Google Drive...")
            if is_drive_folder_link(drive_link):
                update_progress(_progress, 15, "Downloading Google Drive folder...")
                gdown.download_folder(
                    drive_link,
                    output=temp_dir,
                    quiet=True,
                    use_cookies=False,
                )
            else:
                file_id = extract_drive_id(drive_link)
                if not file_id:
                    raise ValueError("Invalid Google Drive link.")
                update_progress(_progress, 15, "Downloading Google Drive file...")
                gdown.download(
                    id=file_id,
                    output=temp_dir,
                    quiet=True,
                    fuzzy=True,
                )
        except Exception as exc:
            raise RuntimeError(
                "Could not download from Google Drive. This is often caused "
                "by a private/non-shared folder, Google Drive quota/confirmation "
                "limits, or a network/DNS issue reaching googleusercontent.com. "
                "Try making the folder public/shared, retrying later, or using the "
                "manual WAV upload option."
            ) from exc

        update_progress(_progress, 75, "Scanning downloaded files...")
        matched_paths = []
        for root, _dirs, files in os.walk(temp_dir):
            for filename in files:
                path = Path(root) / filename
                if is_turing_wav(path):
                    matched_paths.append(path)

        audio_files = {}
        total_files = max(1, len(matched_paths))
        for index, path in enumerate(matched_paths, start=1):
            audio_files[path.name] = path.read_bytes()
            percent = 75 + int(index / total_files * 25)
            update_progress(
                _progress,
                percent,
                f"Loaded {index}/{len(matched_paths)} matching WAV files...",
            )
        update_progress(_progress, 100, "Google Drive audio files ready.")
        return audio_files


def json_download_bytes(data: dict) -> bytes:
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def uploaded_file_signature(uploaded_file) -> tuple[str, int] | None:
    if uploaded_file is None:
        return None
    size = getattr(uploaded_file, "size", None)
    if size is None:
        size = len(uploaded_file.getbuffer())
    return uploaded_file.name, int(size)


def uploaded_files_signature(uploaded_files) -> tuple[tuple[str, int], ...]:
    return tuple(
        uploaded_file_signature(uploaded_file)
        for uploaded_file in uploaded_files or []
        if uploaded_file is not None
    )


def normalize_for_match(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def match_audio_file(email: str, audio_files: dict[str, bytes]) -> tuple[str, bytes] | None:
    normalized_email = normalize_for_match(email)
    for filename, audio_bytes in audio_files.items():
        if normalized_email in normalize_for_match(Path(filename).stem):
            return filename, audio_bytes

    username = normalize_for_match(email.split("@", 1)[0])
    for filename, audio_bytes in audio_files.items():
        if username and username in normalize_for_match(Path(filename).stem):
            return filename, audio_bytes
    return None


def get_segments(participant: dict) -> list[dict]:
    return participant.get("annotation", {}).setdefault("updatedTranscription", [])


def rebuild_segment_text(segment: dict) -> None:
    words = segment.get("words", [])
    segment["text"] = " ".join(word.get("text", "") for word in words).strip()
    if words:
        segment["start"] = min(int(word.get("start", 0)) for word in words)
        segment["end"] = max(int(word.get("end", 0)) for word in words)


def rebuild_all_segment_text(segments: list[dict]) -> None:
    for segment in segments:
        rebuild_segment_text(segment)


@st.cache_data(show_spinner=False)
def audio_peaks_from_bytes(audio_bytes: bytes, peak_count: int = 900) -> tuple[list[float], float]:
    with wave.open(BytesIO(audio_bytes), "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        frame_rate = wav_file.getframerate()
        frame_count = wav_file.getnframes()
        duration = frame_count / frame_rate if frame_rate else 0
        raw = wav_file.readframes(frame_count)

    if not raw or sample_width not in {1, 2, 4}:
        return [], duration

    if sample_width == 1:
        samples = [byte - 128 for byte in raw]
        max_value = 128
    elif sample_width == 2:
        samples = [
            int.from_bytes(raw[i : i + 2], "little", signed=True)
            for i in range(0, len(raw), 2)
        ]
        max_value = 32768
    else:
        samples = [
            int.from_bytes(raw[i : i + 4], "little", signed=True)
            for i in range(0, len(raw), 4)
        ]
        max_value = 2147483648

    if channels > 1:
        samples = samples[::channels]

    if not samples:
        return [], duration

    bucket_size = max(1, math.ceil(len(samples) / peak_count))
    peaks = []
    for start in range(0, len(samples), bucket_size):
        bucket = samples[start : start + bucket_size]
        peaks.append(min(1.0, max(abs(sample) for sample in bucket) / max_value))
    return peaks, duration


@st.cache_data(show_spinner=False)
def encoded_audio_bytes(audio_bytes: bytes) -> str:
    return base64.b64encode(audio_bytes).decode("ascii")


def initialize_state() -> None:
    if "data" not in st.session_state:
        st.session_state.data = None
        st.session_state.json_name = None
        st.session_state.audio_files = {}
        st.session_state.active_participant_id = None
        st.session_state.data_version = 0
        st.session_state.edited_json_bytes = None
        st.session_state.has_unsaved_frontend_edits = False
        st.session_state.pending_speaker_change = None
        st.session_state.show_download_unsaved_warning = False


def participant_options(data: dict) -> list[tuple[str, str]]:
    options = []
    for participant_id, participant in data.get("participants", {}).items():
        email = participant.get("email", "unknown")
        role = participant.get("role", "unknown role")
        options.append((participant_id, f"{email} ({role}, id {participant_id})"))
    return options


def transcript_component_segments(segments: list[dict]) -> list[dict]:
    return [
        {
            "words": [
                {
                    "text": word.get("text", ""),
                    "start": int(word.get("start", 0)),
                    "end": int(word.get("end", 0)),
                    "confidence": float(word.get("confidence", 1.0)),
                    "isUserAdded": bool(word.get("isUserAdded", False)),
                }
                for word in segment.get("words", [])
            ]
        }
        for segment in segments
    ]


def process_transcript_component_event(event: dict | None, segments: list[dict]) -> None:
    if not event:
        return

    event_id = event.get("eventId")
    if not event_id or st.session_state.get("last_component_event_id") == event_id:
        return
    st.session_state.last_component_event_id = event_id

    action = event.get("action")
    if action == "unsaved_state":
        st.session_state.has_unsaved_frontend_edits = bool(event.get("dirty"))
        return

    if action == "save_all":
        incoming_segments = event.get("segments", [])
        if len(incoming_segments) != len(segments):
            st.session_state.last_action_message = "Could not save: segment count changed unexpectedly."
            st.rerun()

        for segment, incoming_segment in zip(segments, incoming_segments):
            segment["words"] = [
                {
                    "text": str(word.get("text", "")),
                    "start": int(word.get("start", 0)),
                    "end": int(word.get("end", 0)),
                    "confidence": float(word.get("confidence", 1.0)),
                    **({"isUserAdded": True} if word.get("isUserAdded") else {}),
                }
                for word in incoming_segment.get("words", [])
            ]

        rebuild_all_segment_text(segments)
        st.session_state.edited_json_bytes = json_download_bytes(st.session_state.data)
        st.session_state.data_version += 1
        st.session_state.has_unsaved_frontend_edits = False
        st.session_state.last_action_message = "Prepared edited JSON for download."
        st.rerun()


def render_transcript_editor(
    participant_id: str,
    segments: list[dict],
    channel_name: str,
) -> None:
    event = TRANSCRIPT_COMPONENT(
        participant_id=participant_id,
        segments=transcript_component_segments(segments),
        channelName=channel_name,
        key=f"transcript_editor_{participant_id}_{st.session_state.data_version}",
        default=None,
    )
    process_transcript_component_event(event, segments)


def render_sticky_audio_player(
    audio_name: str,
    audio_bytes: bytes,
    email: str,
    channel_name: str,
) -> None:
    try:
        peaks, _duration = audio_peaks_from_bytes(audio_bytes)
        audio_base64 = encoded_audio_bytes(audio_bytes)
    except Exception as exc:
        st.error(f"Could not load audio file: {exc}")
        return

    AUDIO_COMPONENT(
        audioBase64=audio_base64,
        audioTitle=f"{email} - {audio_name}",
        peaks=peaks,
        channelName=channel_name,
        key=f"sticky_audio_{audio_name}",
        default=None,
    )


def main() -> None:
    initialize_state()

    with st.sidebar:
        st.header("Upload files")
        uploaded_json = st.file_uploader(
            "Labeling JSON",
            type=["json"],
            accept_multiple_files=False,
        )
        audio_source = st.radio(
            "Audio source",
            ["Upload WAV files", "Google Drive folder"],
        )
        uploaded_audio_files = []
        drive_folder_link = ""
        if audio_source == "Upload WAV files":
            uploaded_audio_files = st.file_uploader(
                "Channel WAV files",
                type=["wav", "wave"],
                accept_multiple_files=True,
            )
        else:
            drive_folder_link = st.text_input(
                "Google Drive folder link",
                placeholder="https://drive.google.com/drive/folders/...",
            )
            st.caption("Loads WAV files whose names end with `@turing.com.wav`.")
        st.caption(
            "Note: Streamlit shows browser upload progress while files upload. "
            "The progress below starts once the app begins processing files."
        )

        upload_signature = (
            uploaded_file_signature(uploaded_json),
            audio_source,
            uploaded_files_signature(uploaded_audio_files)
            if audio_source == "Upload WAV files"
            else drive_folder_link.strip(),
        )
        if (
            uploaded_json is not None
            and audio_source == "Upload WAV files"
            and upload_signature != st.session_state.get("loaded_upload_signature")
        ):
            try:
                st.session_state.data = parse_uploaded_json(uploaded_json)
                st.session_state.json_name = uploaded_json.name
                if audio_source == "Upload WAV files":
                    progress = st.progress(0, text="Processing uploaded WAV files...")
                    st.session_state.audio_files = parse_uploaded_audio_files(
                        uploaded_audio_files or [],
                        progress=progress,
                    )
                else:
                    with st.spinner("Fetching WAV files from Google Drive..."):
                        progress = st.progress(0, text="Starting Google Drive fetch...")
                        st.session_state.audio_files = fetch_turing_wavs_from_drive(
                            drive_folder_link,
                            _progress=progress,
                        )
                st.session_state.active_participant_id = None
                st.session_state.edited_json_bytes = None
                st.session_state.last_component_event_id = None
                st.session_state.last_action_message = None
                st.session_state.has_unsaved_frontend_edits = False
                st.session_state.pending_speaker_change = None
                st.session_state.show_download_unsaved_warning = False
                st.session_state.loaded_upload_signature = upload_signature
                st.session_state.data_version += 1
                st.rerun()
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError, RuntimeError) as exc:
                st.error(f"Could not load files: {exc}")

        if st.button("Load uploaded files", type="primary"):
            if uploaded_json is None:
                st.error("Please upload a JSON file.")
            elif audio_source == "Google Drive folder" and not drive_folder_link.strip():
                st.error("Please enter a Google Drive folder link.")
            else:
                try:
                    st.session_state.data = parse_uploaded_json(uploaded_json)
                    st.session_state.json_name = uploaded_json.name
                    if audio_source == "Upload WAV files":
                        progress = st.progress(0, text="Processing uploaded WAV files...")
                        st.session_state.audio_files = parse_uploaded_audio_files(
                            uploaded_audio_files or [],
                            progress=progress,
                        )
                    else:
                        with st.spinner("Fetching WAV files from Google Drive..."):
                            progress = st.progress(0, text="Starting Google Drive fetch...")
                            st.session_state.audio_files = fetch_turing_wavs_from_drive(
                                drive_folder_link,
                                _progress=progress,
                            )
                    st.session_state.active_participant_id = None
                    st.session_state.edited_json_bytes = None
                    st.session_state.last_component_event_id = None
                    st.session_state.last_action_message = None
                    st.session_state.has_unsaved_frontend_edits = False
                    st.session_state.pending_speaker_change = None
                    st.session_state.show_download_unsaved_warning = False
                    st.session_state.loaded_upload_signature = upload_signature
                    st.session_state.data_version += 1
                    st.success("Uploaded files loaded.")
                    st.rerun()
                except (json.JSONDecodeError, UnicodeDecodeError, ValueError, RuntimeError) as exc:
                    st.error(f"Could not load files: {exc}")

        data = st.session_state.data
        if data is None:
            st.info("Upload a JSON file and WAV files to begin.")
            return

        st.divider()
        st.write(f"JSON: `{st.session_state.json_name}`")
        if st.session_state.audio_files:
            st.write("Audio files:")
            for audio_name in sorted(st.session_state.audio_files):
                st.write(f"- `{audio_name}`")
        else:
            st.warning("No WAV files loaded.")

        download_name = f"{Path(st.session_state.json_name or 'labeling').stem}.fixed.json"
        download_data = st.session_state.edited_json_bytes or json_download_bytes(data)
        if st.session_state.edited_json_bytes:
            st.success("Fixed JSON is ready to download.")
        else:
            st.caption(
                "Click the green Save edited JSON button above the transcript first "
                "to include unsaved frontend word edits."
            )
        if st.session_state.has_unsaved_frontend_edits:
            if st.button("Download fixed JSON", use_container_width=True):
                st.session_state.show_download_unsaved_warning = True
            if st.session_state.show_download_unsaved_warning:
                st.warning(
                    "You have unsaved edits. Click the green Save edited JSON button "
                    "first to include them, or download anyway to get the current "
                    "unedited backend version."
                )
                st.download_button(
                    "Download anyway",
                    data=download_data,
                    file_name=download_name,
                    mime="application/json",
                    use_container_width=True,
                )
        else:
            st.session_state.show_download_unsaved_warning = False
            st.download_button(
                "Download fixed JSON",
                data=download_data,
                file_name=download_name,
                mime="application/json",
                use_container_width=True,
            )

        participants = participant_options(data)
        if not participants:
            st.error("No participants found in the uploaded JSON.")
            return

        participant_ids = [participant_id for participant_id, _ in participants]
        if st.session_state.active_participant_id not in participant_ids:
            st.session_state.active_participant_id = participant_ids[0]

        st.divider()
        pending_participant_id = st.selectbox(
            "Speaker / channel",
            participant_ids,
            format_func=dict(participants).get,
            index=participant_ids.index(st.session_state.active_participant_id),
        )
        if st.button("Change speaker", use_container_width=True):
            if (
                pending_participant_id != st.session_state.active_participant_id
                and st.session_state.has_unsaved_frontend_edits
            ):
                st.session_state.pending_speaker_change = pending_participant_id
                st.warning(
                    "You have unsaved edits. Please click Save edited JSON before "
                    "changing speakers, or change anyway to discard unsaved frontend edits."
                )
            else:
                st.session_state.active_participant_id = pending_participant_id
                st.session_state.has_unsaved_frontend_edits = False
                st.session_state.last_component_event_id = None
                st.session_state.pending_speaker_change = None
                st.session_state.data_version += 1
                st.rerun()
        if st.session_state.pending_speaker_change:
            st.warning(
                "Changing speakers now will discard unsaved frontend edits for the "
                "current speaker."
            )
            if st.button("Change speaker anyway", use_container_width=True):
                st.session_state.active_participant_id = st.session_state.pending_speaker_change
                st.session_state.has_unsaved_frontend_edits = False
                st.session_state.last_component_event_id = None
                st.session_state.pending_speaker_change = None
                st.session_state.data_version += 1
                st.rerun()
        selected_participant_id = st.session_state.active_participant_id

    participant = data["participants"][selected_participant_id]
    email = participant.get("email", "unknown")
    segments = get_segments(participant)
    if not segments:
        st.warning("This participant has no updatedTranscription segments.")
        return

    audio_match = match_audio_file(email, st.session_state.audio_files)
    if not audio_match:
        st.warning(
            f"No matching .wav file found for `{email}`. Add a .wav file whose name "
            "contains this email address or username."
        )
        channel_name = f"audio-sync-{selected_participant_id}"
    else:
        audio_name, audio_bytes = audio_match
        channel_name = f"audio-sync-{selected_participant_id}"
        render_sticky_audio_player(audio_name, audio_bytes, email, channel_name)

    if st.session_state.get("last_action_message"):
        st.success(st.session_state.last_action_message)
        st.session_state.last_action_message = None
    render_transcript_editor(selected_participant_id, segments, channel_name)

    st.subheader("Save Status")
    st.write(
        "Word edits stay in the browser until you click the green "
        "**Save edited JSON** button above the transcript. Then use "
        "**Download edited JSON** in the sidebar."
    )


if __name__ == "__main__":
    main()
