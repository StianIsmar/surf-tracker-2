# Surf Tracker

Surf Tracker finds likely rides in fixed-camera beach footage using classical
computer vision. It does not use an AI model, cloud service, or paid API.

The detector looks for compact dark shapes in the wave zone and tracks their
sustained sideways movement. Results are written to JSON so they can be reviewed
before the original video is cut with FFmpeg.

## Run

```bash
source .venv/bin/activate
python surf_detector.py wholeclip.mp4 --output rides.json
```

Review `rides.json`, then export clips with:

```bash
python surf_detector.py wholeclip.mp4 --output rides.json --export-dir clips
```

Each result includes three seconds before and after the detected movement.
Exported videos retain the original 1920x1080 dimensions and include audio.

## Notes

- The current thresholds are calibrated for the supplied fixed camera angle.
- A 9:49 video takes about one minute to analyze on the current Mac.
- A two-hour video should take roughly 10-15 minutes under similar conditions.
- Detection is intentionally a review-first process because waves can resemble
  small moving surfers. Remove unwanted clips after reviewing the output.

## Test

```bash
python -m pytest -q
```

## Review app

For the normal macOS app experience, double-click `run_app.command` in Finder.
The launcher starts the local server and opens Surf Tracker in a native window.
Drop a recording from Finder or use **Choose video**. The app stores only its
path and metadata; it reads the original recording in place without copying it.

To run the browser-only interface instead:

```bash
python review_server.py
```

Open <http://127.0.0.1:8765>. Browser security prevents a normal web page from
learning a dropped file's original path, so no-copy selection is available in
the macOS app window.

Use the **Source timeline** link to review the complete recording and mark the
exact start and end of every ride. These annotations are saved in
`ground_truth.json` and will be used to measure missed rides and prepare future
training frames.

After the source review is complete, select **Generate training frames** on the
Source Timeline. Cropped ride and hard-negative JPEGs are written to
`training_data/frames`, with timestamps and crop coordinates in
`training_data/manifest.json`.

Open <http://127.0.0.1:8765/label.html> to draw a tight box around each standing
surfer in the ride frames. Use **No standing surfer** when a sampled ride frame
contains only paddlers or empty water. Labels are saved separately in
`training_annotations.json`, so regenerating frames does not remove them.

Once every ride frame is reviewed, prepare a YOLO dataset with:

```bash
python prepare_yolo_dataset.py
```

The generated `yolo_dataset` keeps complete rides together: rides 4 and 7 are
reserved for validation, while the remaining rides are used for training.

Train the same-session MVP locally with Apple Silicon acceleration when
available:

```bash
python train_yolo.py
```

The best checkpoint is written to `runs/surf_mvp/weights/best.pt`.

Run the trained model over a fixed-camera recording with:

```bash
python infer_surf_video.py 2hrsvid.mp4 --output model_rides_2hrs.json
```

Inference samples the trained wave zone at 2 FPS and requires detections across
multiple nearby frames before creating a ride interval.

Review the resulting candidates at <http://127.0.0.1:8765/model-review.html>.
Mark every candidate **Keep in final video** or **Exclude from final video**,
then use **Save final video to Mac** to choose a destination and create one
chronological MP4 with audio. Final Edit shows encoding and joining progress,
then displays the completed duration and file size. Use **Stop export** to
cancel an active export without saving a partial final video.

Final Edit keyboard shortcuts:

- `1` or `X`: exclude the current clip and advance.
- `2` or `K`: keep the current clip and advance.
- `Left Arrow` / `Right Arrow`: move between clips.
- `Space`: play or pause the current clip.
- `R`: replay the current clip from its start.

Shortcuts are disabled while typing in notes, titles, descriptions, or other controls.

## Publish unlisted to YouTube

Final Edit offers two independent destinations after every clip has a decision:

- **Save final video to Mac** keeps the combined MP4 locally.
- **Publish unlisted to YouTube** creates a temporary combined MP4, uploads it,
  and removes that temporary file after YouTube confirms success. If an upload
  fails, the temporary file is retained so retrying does not re-encode it.

YouTube requires a one-time personal OAuth setup:

1. Create a project in Google Cloud Console and enable **YouTube Data API v3**.
2. Configure the OAuth consent screen. While it is in Testing mode, add the
  YouTube account that will upload videos as a test user.
3. Create an OAuth client with application type **Desktop app** and download its
  JSON file.
4. In Final Edit, import that JSON under **Publish unlisted to YouTube**, then
  select **Connect YouTube account** and approve access in the system browser.

The OAuth configuration and refresh token are stored in macOS Keychain under
`local.surftracker.app`; they are not written into this project or browser
storage. The backend always sends `privacyStatus: unlisted`, so the publishing
UI cannot accidentally make a video public. YouTube API quota applies, and
Google may expire refresh tokens for consent screens left in Testing mode.