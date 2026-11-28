# Development log

## Video logos (merged from mp1711) + two additions (2026-11-28)

- Merged branch `video-logo-campaigns` by mp1711:
    - Video logos (MP4/MOV/M4V/WebM), transparent or green screen (auto-detected and removed).
    - The visible part of the logo is cropped, and it can be placed and resized like image logos (new Size slider).
    - Logo videos loop without sound.
    - Campaign-wide "Show captions? On/Off".
- Added on top:
    - **Cleaner green-screen edges.** The key is now applied at the logo's own size, before scaling, plus `despill=type=green` (`media/logo.py` `key_filter` / `logo_filter`). Keying after a resize left a thin green outline.
    - **"No captions" for selected clips.** The video page's Captions dialog has Set the height / Automatic / No captions. `BulkClipEdit.captions` sets `edit.captions`; choosing a height or Automatic turns captions back on. The dialog notes when the campaign has captions off, and clip cards say "no captions".
- Not run here (the user tests): no new automated tests were run for the two additions.

## Round 5: ideas adapted from nero-harsh/clipstudio (2026-11-28)

Rebuilt in this codebase with his code as the reference; nothing was copied wholesale.
- **Quality check after every render** (`media/qa.py`). Measures loudness, true peak, frozen and black picture, and rapid flashing. Results are stored as `analysis.qa` and shown as checks (`qa_sound`, `qa_picture`, `qa_flashing`). Music edits only report silence. Silent on purpose shows "not checked". Off switch: Settings → `qa_check`.
- **Two-pass loudness** (`render.measure_mix_loudness`, `loudnorm_filter`). Falls back to one pass if measuring fails.
    - Found and fixed: `alimiter`'s default auto-level was adding make-up gain after loudnorm, so every render was about 1.5 dB hot (-12.5 LUFS). Now `level=disabled` with limit 0.84, giving -14.0 LUFS and about -2 dBTP. The song in music edits is no longer pushed up either.
- **No weak padding clip.** The minimum score is strict in highlights, action and the merge step. A video where nothing clears it finishes "ready" with 0 clips and a reason, keeps any clips it already had on re-process, and shows on Home (`todo.no_clips`). "No moments of that length at all" is still an error.
- **Post stats** (`download.post_stats`, `posting.refresh_stats`, columns on `posts`):
    - "Refresh stats" on the Post queue's Posted list (newest 25 links) and "Refresh views" in the editor.
    - A campaign shows its total views.
    - Instagram usually refuses without a login; the reason is shown.
    - The last good numbers are kept when a refresh fails; a new link clears them.
- **Captions without libass** (`media/caption_png.py`): draws the app's own ASS output (captions with highlight and emphasis, title and end text, #ad, POV, progress bar) with Pillow and overlays it via `movie=…:f=concat`.
    - Used automatically when FFmpeg lacks libass. `CLIPSTUDIO_CAPTIONS=images|libass` forces a path.
    - Pop-in animations and fades are dropped, and emoji are left out.
    - Memory is about 36 MB for a 60 s clip. Home shows a one-time note.
- Also fixed: the cold-open teaser no longer shows the end text. The bulk "skip unchanged" from round 4 now applies only to caption position, so Framing and Apply recipe re-render as before.
- Tests: `tests/test_round5.py` (10). The full suite passes (122), and the montage, polish and round-5 tests pass with `CLIPSTUDIO_CAPTIONS=images`.

## Round 4 from user feedback (2026-11-27)

- **#4 Same link added twice.** Create → Clips now lists the chosen campaign's videos *above* the upload/link box (Open, Start, Re-process). Links are matched with `download.same_video_key()`, so youtu.be, watch?v= with `si`/`t`, /shorts and /live count as one video.
    - `inspect-url` returns `existing_projects`.
    - `POST /projects/bulk` skips links already in the same campaign (`existing` in the response).
    - `POST /projects` refuses a same-campaign duplicate unless `allow_duplicate`.
    - The Configure step shows "Already in …" with Open / Re-process and a "Make a second copy anyway" tick that the Start button waits for.
    - The campaign page highlights newly added videos and scrolls to them.
- **#3 Captions vs logo.**
    - New `Brief.caption_position`, plus `campaigns/text_layout.py`: captions, title text and end text move to the nearest spot clear of the logo's pixels. A caption position set on the clip itself is kept as is.
    - `features.text_layout` reports the result. A new compliance check, "Captions clear of the logo", warns above 0% and fails above 25%.
    - UI: a "Text on the video" card on Requirements (caption position, title text moved here, end text), and a draggable captions band on the logo placer. In the editor, the caption position shows "automatic (why)" with an Automatic reset. On the video page, the selection bar has **Captions** (set a height or go back to automatic for the selected clips).
    - The bulk endpoint skips clips that wouldn't change.
- **#2 End text.**
    - `Brief.end_text` and clip `edit.end_text` (partial, merged): text with `{title}`, 1–6 s, hook styles, and look `overlay` or `card` (dimmed and blurred video, captions stop). The clip keeps its length.
    - Brief reader: "end screen / end card / end each video with / at the end of every clip" (quoted words). Summary item, compliance check `end_text`, montages locked.
- **Videos kept when changing requirements first.** In the campaign's "Make clips from…" box, "Change requirements first" now adds the links/files to the campaign as *not started* (`POST /projects/bulk` with `start: false`, uploads without `process`) and opens Requirements. Add videos shows "N videos added, not started yet" with **Start making clips** (same confirmation box, current campaign settings). Not-started videos also show in Create → Clips under "Already in …".
- Tests: `tests/test_text_layout.py` (8, written and run once before the user asked to stop testing). A clash between the title-text and end-text variables in `build_overlay_ass` was found by a demo render and fixed; the end-to-end demo render after that fix was not run.

## Mark as posted + campaign Post queue (2026-11-26)

- **Data:** new `posts` table (clip_id, platform, url, posted_at; cascades with the clip). Helpers in `campaigns/posting.py`. A clip is *done* when posted on every platform in `brief.platforms` (both without a campaign).
- **API:** `PUT/DELETE /api/clips/{id}/posts/{platform}` (`{url?}`, http(s) only; re-marking keeps the original time). Clips return `posted` + `post_platforms`. Campaign detail returns `ready` (with captions per platform, `render_status`, `has_video` = file exists), `ready_total`, `posted_clips`, `posted_total`, `post_platforms`.
- **Rules:** done clips leave ready-to-post (campaign, Home), outdated/re-render counts, Home render to-do, and Export ready clips. Partly posted clips stay in To post even if outdated/failed (flagged). Re-processing (pipeline, AI-picks replace, montage re-make) never deletes a clip with any post mark; new clips are numbered after the highest kept rank. Home ignores archived campaigns for ready-to-post.
- **UI:** campaign **Post queue** tab (`components/posting.tsx`): To post / Posted, MP4, Copy caption/title, Mark posted, Add/Edit link, Undo; Overview card links to it. Editor Post tab has **Mark as posted** (outside the compliance block). Clip cards show posted badges; "Render approved" and "select outdated" skip done clips. Dialogs mention posted clips.
- **Polling:** `usePoll.refresh()` bumps the generation so older polls/refreshes can't overwrite a click; the campaign page polls through `reload()`.
- Tests: `tests/test_posting.py` (3). Checked by screenshots on synthetic media; clicks verified via the API only.

## Fixes from user testing round 3 and a second audit (2026-11-26)

**What the user asked for**
- **Campaigns page:** every card has Open / Archive / Delete. Multi-select gives bulk Archive / Unarchive / Delete.
- **Clip cards:** Edit is icon-only, so Post is never cut off; the action row wraps.
- **Framing or recipe for just some clips:** select clips, then use **Framing** / **Apply recipe** in the selection bar (`BulkClipEdit.ids`). The header Framing box also offers "the N selected clips".

**Audit bugs fixed**
- **Editor saves no longer freeze campaign settings onto a clip.** `campaignEdit` sends only changed fields. `HookOverlayEdit` fields are optional, and the server merges `hook_overlay` like `polish`.
- **Export zip** is rebuilt when the clip changed after the last export.
- **Brief tab:** Archive merges only the status, so unsaved edits stay. Delete handles errors and skips the leave guard.
- **Clip length:** a campaign's min/max now replaces the length preset instead of intersecting with it. The confirm box shows what is used, and its framing label falls back to the Settings layout.
- **Downloads** count only clips whose render is current.
- **Campaign style on the server:** `campaigns/styles.py` and `pipeline.with_campaign()` fill a campaign video's options the video didn't set, so "Start with default settings" and re-processing follow the campaign.
- **Ready to post:**
    - `ready_total` excludes exported clips, consistent with Home.
    - Verdicts are cached per clip and campaign version.
    - New `outdated` count, plus `POST /campaigns/{id}/rerender-outdated` with a bar on Overview.
- **Smaller fixes:**
    - Errors are shown via `attempt()` for logo, sound, library, re-render-all and source-preview actions.
    - "Reset to transcript" asks first.
    - The editor's prev/next tooltips match j/k, and moving between clips stays on the same tab.
    - `usePoll` drops responses requested before a local update (no flicker back after approve/reject).
    - The video and campaign pages poll fast only while work is running.
    - An archived campaign still shows by name in the campaign select.
    - A failed music-edit start deletes the half-made edit.
- **UX:**
    - Upload percentage on the campaign page.
    - "Change style" in the confirm box keeps the picked videos.
    - The caption preview shows only the chosen platforms.
    - "Use template" saves immediately.
    - The video page's back link and post-delete redirect go to the campaign.
    - Editor tabs are Trim · Captions · Framing · Sound · Post.
    - Naming fixes (recipe vs style, "Editing details").

**Open flow gaps, waiting for the user's choice** (posted state + post queue since built)
- Videos list search and filters, and bulk actions on the campaign's videos.
- Rendering straight from Home.
- Montage video picker filters.
- Duplicate campaign.
- Richer campaign cards.
- Auto-advance after A/R.
- "Find more clips" per video.
- Copy all captions.

## Fixes from user testing round 2 (2026-11-26)

Reported problems first, then 30 findings from a code-level UX audit.

**Bugs**
- **Campaign uploads did nothing.** The FileList was read after an `await`, by which time clearing the input had emptied it. Files are now copied first.
- **Change style didn't stick.** `mergeServer` didn't carry `layout` / `caption_preset`, so "Save changes" wrote the old values back.
- **Clips per video was ignored.** `target_count` used about one clip per 4 minutes. It now aims for the number asked for, skipping only moments below the minimum score, with a clear message saying how many of N were found.
- **Global editing defaults were ignored.** Styles sent a full `DEFAULT_POLISH`, overriding Settings. Styles now send only the switches they decide. Styles also no longer set `layout`, which was resetting framing.
- **"#ad on the video"** was disabled when there was no label. It now defaults to #ad.
- **Settings → Processing mode** was never applied, because the whole object was sent. Only changed fields are sent now.

**Campaigns**
- **Brief summary on the campaign page.** "Create campaign from the brief" is one step. What was read (with quotes) shows as **Read from your brief** on Overview; clicking an item opens where to change it, and "Looks right" hides it. Stored as `Brief.brief_sources`, and `campaign.summary` is returned with the campaign.
- **Confirmation before videos start.** Adding videos (files or links) opens a summary of exactly what will be used and any unfinished setup. Unsaved campaign changes are saved first.
- **Campaign title text.** `HookOverlay.text`: written per clip, or the campaign's own words with `{title}`. A clip's own text still wins.
- **Smaller fixes:** errors in the style box are handled; a min ≥ max length warning; the music/effects mixed state shows as a badge; re-picking the same logo or sound file works.

**Create, Clips and Music edit**
- **Clips with a campaign** shows "Using the campaign's settings" (length, clips, style, framing, captions), with **Change for this video** to override. The campaign's existing videos are listed with Start/Open.
- **Quick clip** was removed from Create. It is the "Quick clip" tick on the Clips page.
- **Music edit:**
    - Length comes from the campaign.
    - In-app-sound campaigns render silent.
    - Leaving after adding videos asks whether to keep or delete them.

**Navigation and posting**
- **Home** is one grouped To-do list (Fix first, Review, Render, Post, Not started) naming the campaign. Recent videos was removed.
- **Post button** on approved, rendered clip cards and in the editor header.

**Editor**
- **Shortcuts:** ⌘S / ⌘↵ first commit a caption line being typed. j = next clip, k = previous.
- **Kept edits:** uploading music no longer wipes other unsaved edits. Discard resets caption inputs.
- **Labels and buttons:** Render vs Re-render; a real disabled zip button; the preview tools wrap instead of overlapping.

**Everything else**
- **Storage:** multi-select, "Delete source files" and "Delete videos entirely", largest first.
- **Video page:** an empty state with Add clip / AI picks when 0 clips were found; a tooltip on the disabled Re-process.
- **Dialogs:** only the top dialog reacts to Esc, and Esc keeps typed text.
- **Settings:** save bar, leave guard, and a note that these are defaults.
- **Framing (all clips)** starts from the video's current framing.
- **Consistency:** naming (video, Music edit, type labels), one safe-zone palette, clips slider 1–30 everywhere.
- **Not done yet:** montage-tab edits and Check & post caption edits still don't count as unsaved changes; trim handles don't work with the keyboard.

## UX revamp R1–R4 (2026-11-25)

This follows the user's feedback that there were too many settings to understand and too many places to go. It replaces the earlier UI-3, UI-4 and UI-5 plan.

### R1: campaigns
- **Creating a campaign:** `/campaigns/new` (`NewCampaign.tsx`).
    - Pick the type (Clips, Logo or Music), paste the brief, and see **Here's what we understood**.
    - `POST /api/campaigns/preview` returns the brief plus `summary_brief()` (`campaigns/summary.py`). The summary is split into what changes the videos and what is for reference, and each item quotes the brief line it came from (`parser` `_sources`).
    - `POST /api/campaigns` takes `type`; the old `preset` still works for API compatibility.
    - The preset picker is gone. `presets.base_brief(type)` and `apply_site_rules()` add Clipster's rules only when the brief mentions Clipster.
- **Parser:**
    - Reads ranges like "15–60 seconds", and "no background music / sound effects".
    - Reads **required caption text** ("paste this into every caption exactly as written" plus the quoted text).
    - The name is taken from before a dash or colon.
    - Type guessing ignores "no background music".
    - Pasting an updated brief keeps the campaign's type.
- **Required caption text:**
    - New `Brief.required_caption_text`.
    - `build_post` always appends it, even when a custom template leaves out `{required_text}`.
    - The compliance check `required_text` fails if the text is missing or changed.
- **Clip length follows the campaign:** `pipeline.campaign_length()`.
    - The campaign's min/max now override the project's length preset when finding clips.
    - A +2 s margin is added when dead-air cuts are on.
    - Before this, the length was only flagged afterwards by the checker.
- **Campaign page** (`CampaignView.tsx`, rewritten):
    - Tabs: Overview, Requirements, Posting, Brief (`?tab=`). Inline rename. A sticky save bar, plus a guard when leaving with unsaved changes.
    - Overview:
        - Add videos on the page (files or links), processed with the campaign's style.
        - A setup checklist.
        - The video list.
        - **Ready to post**: approved, rendered clips with their check verdict, from `campaign.ready`.
    - Requirements:
        - Drag-and-resize logo on the frame (`LogoPlacer`). It shows the Reels and Shorts button areas, the title-text area, and a live coverage and overlap check. Arrow keys move the logo.
        - Sound as 3 cards.
        - "What the brand allows" as Yes/No questions.
        - An Advanced section.
    - Posting: a live caption preview (`POST /api/campaigns/caption-preview`) with the paid-post label and required text highlighted, and the **Before you post** list (automatic checks and editable steps you tick).
    - Brief: pay and targets (marked as reference), notes for AI picks, the brand's rules, the original brief, and archive/delete.
- `Brief.style`, `intensity` and `max_clips` store how the campaign's videos are made.
- `Brief.layout` and `caption_preset` are optional overrides of the style's framing and caption look. They're set in the campaign's **Change style** dialog, used for videos added from the campaign page, and pre-filled on New project. Invalid campaign edits now return 422 instead of a server error.

### R2: Create and Style
- **Create** (`/create`): Clips from a long video, Music edit, or Quick clip (`/new?quick=1`). A campaign passed in is kept.
- **Style templates** (`components/styles.tsx`):
    - Talking/Podcast, Gaming/Stream, Hype, Clean. Each bundles the caption preset, title text, sound effects and framing.
    - **Editing intensity** (Clean / Balanced / Punchy) bundles the retention-polish switches.
- **New project:** "Campaign & clips" (length shown from the campaign when it sets one), then Style, then a collapsed **Customize everything** with all the old settings. "Finishing" is renamed Extras and "Retention polish" is renamed Editing details.
- **Music edit:** a 3-step wizard (Song → Videos → Style). Variants, drop position, audio and flash are under "More options". It uses the campaign's sound by default when there is one.

### R3: navigation, Home, editor
- **Sidebar:** **Create** plus Home, Campaigns, Videos (formerly Projects), Library, Settings. Storage opens from the disk meter or Settings.
- **Home** (`Dashboard.tsx`): a "Needs you" to-do list from `dashboard.todo`:
    - campaigns missing a logo or sound file
    - failed videos and renders
    - clips to review
    - approved clips not rendered
    - clips ready to post
    - videos never started
  It also shows "Working on it" and recent videos.
- **Clip cards:** an "applied" line (captions · logo · title text · Xs dead air cut · zooms · music…).
- **Editor:** 5 tabs instead of 9: Trim, Text, Look, Sound, Check & post. Panels are grouped as collapsible sections, and `?tab=` opens a given tab. "You confirm" steps can be ticked (saved per clip in localStorage).

### R4: help and accessibility
- **Help:** `HelpTip` "?" buttons with plain-language explanations (`components/help.tsx`) on campaign, style and montage settings. A **Getting started** card on Home (can be hidden).
- **Settings:** split into **Basics** (mode, clip defaults, storage) and **Advanced** (`?view=advanced`).
- **Accessibility:**
    - Skip link, `<nav>` landmark and `main` target.
    - `:focus-visible` outlines and `prefers-reduced-motion`.
    - Darker secondary text in light mode (4.5:1 contrast).
    - ARIA roles and labels on tabs, radios and icon-only buttons.
    - The logo placer works with the keyboard.

### Not verified yet
- These were checked only with screenshots on synthetic data: drag-and-drop, the help popovers, the leave-without-saving dialogs and the wizard steps were not clicked through.
- 97 backend tests pass.

## Fix from user testing: music montage from links (2026-11-25)

Reported problem: the Music montage page only accepted uploads. The campaign's "Add video" button led to New project (highlight clipping), so pasting a link there started the wrong flow.
- **Montage page takes links.** Paste one or more links and click "Add link". `POST /api/montages/sources` checks each link (title and length, no download) and creates an unprocessed URL project in the campaign. Links that fail are reported, and duplicates are ignored.
- **Montage job downloads them.** The new "Downloading linked videos" stage downloads a linked source in full when it has no video file and no sections. It is skipped when everything is already downloaded.
- **Music campaigns lead to the montage.** The campaign page shows "Make music montage" as the main button, and "Add video" becomes "Clip a video". New project shows a note linking to Music montage when the selected campaign is a music campaign.
- Test: `test_montage_from_links` uses a stubbed downloader with no network. 90 tests pass.

## Fix from user testing: several uploads in New project (2026-11-25)

Reported problem: the upload box took only the first file.
- **Several files at once.** You can now pick or drop several files. They upload one after another ("2 of 5 · name.mp4"), and "Cancel the rest" stops the remaining ones. A file that fails is listed while the others continue.
- **One settings page for all of them.** It shows the total length and a combined disk estimate. Starting queues a processing job for each video with the same settings and campaign, then opens the campaign page or the Projects page.
- **Quick clip** applies the same start and end to every video. Leaving them at 0 and blank uses each whole file.
- A single file works as before. Frontend only, no backend changes.

## Fix from user testing: Projects lists only started work (2026-11-25)

Reported problem: uploads appeared in Projects as "new" before anything was started.
- **Project stages.** Every project now has a `stage`, from `stage_of()` in `api/projects.py`:
    - `active`: started, or a montage.
    - `not_started`: status "new" and not used by any montage.
    - `footage`: status "new" and used as a montage source.
  Responses also carry `used_in`, the montages that use the video. `GET /api/projects?stage=` filters by stage. No database change: footage is worked out from the montages' source lists, so existing data is classified correctly.
- **Projects page** lists only active projects. A "Not started (n)" button opens the rest. Clicking one opens `/new?setup=<id>` to choose its settings, and there's a "Delete all" button that also frees the uploaded copies.
- **Leaving New project without starting** shows a dialog (`useBlocker` plus a `beforeunload` warning) with three choices: Stay, Keep for later or Delete. "Choose another video" asks the same question. A start that fails turns the guard back on.
- **Montage footage** doesn't appear under Projects or in the campaign's video list. The montage page has a "Source videos" card, with "Find clips in it" on each video. Deleting a montage can also delete its footage (`delete_footage`), but only videos that no other montage uses and that were never processed on their own.
- **Other places:**
    - The dashboard's project count and "Recent projects" skip unstarted videos.
    - The campaign video count counts only started videos.
    - Storage tags rows as "not started" or "montage source".
    - The status badge reads "not started" instead of "new".
    - A not-started project page offers "Choose settings" (primary) or "Start with default settings".
- Test: `test_project_stages_and_footage_delete`. 91 tests pass.

## UI-1 + UI-2: safety and faster review (2026-11-24)

These follow from the UI/UX audit. The rest is planned as UI-3 (show the value), UI-4 (simplify) and UI-5 (accessibility).

### UI-1: safety and layout
- **Re-process asks first.** The dialog says how many clips will be deleted and how many are approved, hand-made or rendered. It can keep approved and hand-made clips (`keep_clips` → job payload; new candidates that overlap a kept clip by more than 50% are skipped). "Change settings first" opens `/new?reprocess=<id>`, the New project form pre-filled with that project's options.
- **AI picks "Replace"** shows how many clips it deletes and asks for confirmation. A dialog with typed text doesn't close on a backdrop click.
- **Changing a project's campaign** asks first when rendered clips exist. The toast that follows has a "Re-render all" button.
- **Clip editor:**
    - Leaving with unsaved edits is blocked: a `useBlocker` dialog offers Stay / Discard / Save & leave, plus a `beforeunload` warning when closing the tab. This required switching to a data router (`createBrowserRouter`).
    - Automatic sound-effect cues are no longer part of the draft, so a render no longer shows a false "Unsaved changes".
    - The tab bar wraps instead of overflowing.
- **Campaign page:** uploading or removing a logo or sound, or archiving, merges only the fields the server owns, so unsaved edits elsewhere survive. Pasting a brief warns about unsaved edits first. The rules and checklist editors follow outside changes.
- **Confirmations** before deleting a source video, clearing the cache, removing a logo, campaign sound, music track or SFX file, and deleting a recipe. Browser `confirm()`/`prompt()` calls are replaced with in-app dialogs (`useDialogs`).
- **Small fixes:**
    - Modals close with Esc and keep focus inside.
    - Disk and memory meters say "x free · y% used".
    - Pluralised counts.
    - New project's framing options reuse `LAYOUT_OPTIONS` (fixes the Split hint).

### UI-2: faster review
- **Project page:**
    - Select clips with a checkbox on each card, select all, or quick-select (unreviewed, score 70+, needs render). Then Approve, Reject, Render or Download as zip (`POST /projects/{id}/clips/actions`, `GET /projects/{id}/clips.zip?ids=`).
    - "Render approved (n)" and "Download approved (n)" buttons.
    - Counts on the filter buttons.
    - Approve and reject show an Undo toast.
    - Unrendered clips can be previewed from the source (`GET /clips/{id}/source-info`, media fragment `#t=start,end`).
- **Clip editor header:** Previous / Next (following the order the project page last showed), Approve, Reject, and a position counter.
- **Keyboard shortcuts** (`useHotkeys`; press ? for the list):
    - Project page: J/K or arrows to move, A, R, X, E/Enter, Shift+A, Esc, Space.
    - Editor: ⌘S save, ⌘↵ save & render, J/K, A, R, Space.
- **Elapsed time and estimated time left** (`JobTime`) for processing (project page, dashboard) and renders (editor).
- **Tests:** `tests/test_api_review.py`. The UI was checked with typecheck, build and headless-Chrome screenshots on synthetic data. Clicking through dialogs and shortcuts still needs a manual check.

## Phase 4A: retention polish (2026-11-24)

Each feature can be set in Settings (defaults), per project (New project), per clip (Clip editor → Polish) or through a recipe.

### Done and tested (synthetic media only)
- **Dead-air and filler removal** (`media/polish.py: plan_segments`): pauses between words are cut only where the audio is quiet: the 90th-percentile level must sit 16 dB below the speech level, or below −50 dBFS. Gameplay sound, music and reactions are never cut. Fillers (um, uh, erm…) are cut out. Levels: light (pauses ≥ 0.9 s), normal (≥ 0.6 s), tight (≥ 0.38 s).
    - Cuts snap to the frame grid. They are applied after reframing with `select`/`aselect` (160-sample audio frames), so crop plans and burned-text blur stay in source time.
    - Captions, sound effects, zooms and overlays are remapped to output time.
    - Safety: if the cuts would leave less than 50% of the clip or under 3 s, nothing is cut.
- **Punch-in zooms** (`plan_zooms`): every other segment after a jump cut is zoomed in, plus emphasised words. Clips without speech zoom on the loudest moments instead.
    - Subtle zooms 1.10×, punchy 1.20×. Zooms cover at most 45% of the clip.
    - Done as a split → crop+scale → overlay with `enable`, so it's a hard punch-in with no zoompan jitter. Not used on split or stream layouts.
- **Keyword emphasis** (`pick_emphasis`): at most one word per caption page, and only if its score is ≥ 2. Numbers, money, strong words, emoji words and long content words score highest.
    - The word is drawn in `emphasis_color` at `emphasis_scale`, constant for the whole page, so the line doesn't jitter.
- **Emoji** (`pick_emoji`, `render_emoji_png`): up to 4 per clip, at least 3 s apart, chosen from a word→emoji map. They are rendered with Pillow and Apple Color Emoji, then overlaid above the captions. The render is skipped if the font is missing.
- **Five caption themes:** Hormozi, Beast (bundled OFL Bangers font), Hype (one word at a time), Neon glow (`glow` → `\blur` on the outline), Boxed.
- **Progress bar:** an ASS drawing with an animated `\clip`, at the top or bottom. A cold-open teaser and the main part share one bar (`progress_span`).
- **Hook title styles:** box, dark, yellow, banner, bold, outline. Hook text templates are in the clip editor.
- **Cold open** (`pipeline.render_cold_open`): the strongest ~2.6 s, taken from `peak_t` for action clips or the loudest window in the last 70%, snapped to word boundaries. It is rendered as a teaser carrying the hook title, then the full clip follows with a white flash and a whoosh.
    - Both parts are joined with the concat demuxer (no re-encode). Music continues across the join.
- **Profanity:** strong swear words are masked in captions (f***) and either muted or beeped (a 1 kHz `sine` gated with `enable`).
- **Speech denoise:** `afftdn`, light or strong.
- **Recipes:** `recipes` table, `/api/recipes` CRUD, `/clips/{id}/apply-recipe`, `/clips/{id}/save-recipe`, and `recipe_id` on `/projects/{id}/clips/bulk`. New project can start from a recipe. A campaign still decides the format and the music/SFX rules.
- **Compliance:** the duration check uses the rendered length (`analysis.output_duration`) when the render is current, because jump cuts and cold opens change the length.
- **Tests:** `tests/test_polish.py` (planning, ASS output, real FFmpeg renders with every feature, cold-open concat) and `tests/test_api_recipes.py`.

### Not yet verified with real footage
- Quiet-gap thresholds on real podcasts and streams (background music beds, room noise).
- Whether the speech model transcribes fillers at all. Parakeet often drops them, in which case only pauses are cut.
- Small A/V drift on clips with many cuts, at most a few ms per cut.
- How emphasis picks read on real speech, and zoom framing on tracked faces (crop centre at 40% height).
- Cold-open peak choice on talk clips, and AAC joins at the concat point.

## Phase 2: gameplay and action detection (2026-11-24)

- [x] **Action curves** on a 0.5 s grid (`analysis/action.py`), no ML models:
  - Audio: loudness, transients (spectral flux: gunfire, explosions, impacts) and voice-band bursts (shouting, laughing).
  - Video: motion from a 96x54 grey frame stream (VideoToolbox decode with software fallback), scene cuts, HUD/kill-feed flashes in the top-right corner, and where the motion is horizontally.
  - Words: reaction phrases ("let's go", "no way", "oh my god"…).
- [x] **Excitement curve:** each signal is compared with its local baseline, then combined with adjustable weights (**Settings → Gameplay & action detection**). YouTube "Most replayed" data is blended in when available.
- [x] **Action highlight finder:**
  - Builds clips around peaks, with build-up before and payoff after.
  - Tries several lengths and lead-ins.
  - Boundaries snap so words aren't cut, and to nearby scene cuts.
  - Each clip gets an explainable score with its own criteria (opening intensity, reaction, action, audio impact, build-up, replay heat) and reasons/weaknesses.
- [x] **Content type** (Auto / Talk / Gameplay, per project and as a default):
  - Talk keeps speech highlights and gives a small boost to clips containing a big moment.
  - Gameplay merges speech and action candidates.
  - Auto picks gameplay below ~110 spoken words per minute.
  - Silent gameplay works.
- [x] **Smart downloads + gameplay:** a ~240p video-only preview (`proxy.mp4` in the project folder) is downloaded for motion analysis only.
- [x] **Action-following crop:** Fill crops, and the gameplay half of the stream layout, drift towards concentrated motion (wide dead-zone, slow smoothing, ignores camera pans). Motion is measured in the same frame pass as face and burned-text detection.
- [x] Action clips get titles and hooks from the streamer's reaction where possible, plus gaming hashtags.
- [x] Tests: `test_action.py` (synthetic burst of motion and bangs found in curves, highlight, crop and the end-to-end API).

## Phase 3: music montage builder (2026-11-24)

- [x] **Song analysis** (`analysis/beats.py`):
  - Band-normalised onset flux.
  - Tempo by autocorrelation with an octave check.
  - Dynamic-programming beat tracker.
  - Bars from bass onsets.
  - Drop = largest 4-bar energy jump.
  - On a synthetic 128 BPM track: 128.1 BPM, beats within ~10 ms, drop within ~15 ms.
- [x] **Planner** (`montage/planner.py`):
  - Shots come from source videos (scene structure + motion peaks).
  - The song window puts the drop at a chosen % (or keeps a campaign sound's cut).
  - Cut grid by style (chill / normal / hype, including half-beat stutters after the drop), with a cut exactly on the drop.
  - The strongest shot goes on the drop; calmer shots build up before it.
  - Sources alternate.
  - Variants avoid each other's shots.
- [x] **Renderer** (`montage/render.py`):
  - Frame-exact cuts (frame counts from beat times at 30 fps).
  - 9:16 crops centred on each shot's action.
  - White flash on the drop.
  - POV text for the whole clip.
  - Campaign logo/disclosure; song audio or silent.
- [x] **Montage projects:**
  - New **Music montage** page: song upload or campaign sound, pick or upload videos, length, drop position, style, POV, variants, audio.
  - Variants are clips, so compliance, captions and export work as usual.
  - Per-variant re-cut (style, drop, POV, shuffle shots); "New variant" button.
  - For in-app-sound campaigns the checklist says where to start the official sound so the cuts match.
- [x] Tests: `test_montage.py` (song analysis, cut grid, drop slot, variant uniqueness, end-to-end API montage with a music campaign). 70 tests passing in total.

### Not yet verified with real footage (Phases 2 & 3)
- Action detection thresholds on real streams (GTA, CS2), especially how gunfire vs music in the stream is treated. Tune with the weights in Settings.
- Beat/drop detection on real songs with soft intros, tempo changes, or no clear drop (it then cuts on the beat only and says so).
- Shot choice quality for sports/movie edits. Shots are chosen by motion; faces and emotion aren't considered yet.

## Phase 1.5: framing for streams (2026-11-24)

Fixes blurred bars on vertical clips from streams and gameplay, which you found while testing.

### Done and tested (synthetic media)
- [x] **Auto no longer blurs by default.** It follows a face only if it's big (median height ≥ 12% of the frame). With a webcam overlay it uses the stream layout; otherwise it **fills** the 9:16 frame by cropping. To get the old behaviour, set **Settings → Framing (Auto) → Whole frame + blurred bars**.
- [x] **Stream layout** (`layout = "stream"`):
  - Webcam on top or bottom (20–60% of the height), gameplay filling the rest. Both are cropped to fill, so there are no bars.
  - Gameplay focus can be set left, centre or right.
  - The hook title sits on the seam between webcam and gameplay.
- [x] **Webcam detection:** a small face that stays in the same off-centre spot is treated as a webcam overlay. It runs per clip in Auto, or on demand with **Detect webcam** (3 short samples). You can also mark the box by hand on a still frame.
- [x] **Framing for all clips** (project page → **Framing**): choose a layout, mark or detect the webcam, see a live 9:16 mock-up, then apply and re-render all, approved-only or non-rejected clips.
- [x] Clip editor and New project have the new options. The preview frame supports the stream layout, using the saved or last-detected webcam box.
- [x] Tests: 63 passing (new `test_framing.py`).

### Not yet verified with real footage
- Webcam detection on real streams (webcam size and position vary; YuNet must see the face). If it misses, mark the box once with Framing.
- Fill crop on gameplay loses the sides of the frame (HUD, minimap). Following the action is Phase 2.

## Phase 1: campaign foundation (2026-11-24)

Turns ClipStudio into a **platform-neutral campaign clipping tool**: a campaign is a brief from any site (Clipster is one preset), and every clip is finished, checked and packaged against it. Built and tested with synthetic media only; the user tests on real footage.

### Done and tested (automated tests on synthetic media)
- [x] **A0 Smarter downloads:**
  - yt-dlp uses Node/Deno/Bun for YouTube's JavaScript challenges; `yt-dlp-ejs` is bundled.
  - *Smart* strategy: audio first (~1 MB/min) → transcribe → pick clips → download only each clip's section (±20 s). The true section start is found by correlating its audio with the full track. Falls back to a full download automatically.
  - YouTube "Most replayed" heatmap adds up to +10 / −4 points to the score.
  - Clips that cross a chapter boundary get a −6 penalty.
  - Optional creator subtitles replace transcription (word times are interpolated).
- [x] **A1 Campaign object:** `campaigns` table, brief schema (`campaigns/models.py`), and projects linked to a campaign. File paths can only be set by upload endpoints.
- [x] **A2 Brief form + paste-to-fill:** rule-based parser picks up pay, max payout, minimum views, engagement, min/max length, platforms, sound volume, official sound, song names, logo template and zone, UI and no-go limits, disclosure, English-only, keep-live days, hashtags, mentions, do/don't rules, post actions and banned items. Presets: Generic (clipping, logo, music) and Clipster (logo, music, clipping, UGC).
- [x] **A3 Logo overlay:**
  - Template mode: full-frame PNG/APNG/GIF, never moved or resized, loops for the whole clip, forces 1080x1920.
  - Free mode: placed logo with an allowed zone.
  - Editor preview shows Reels/Shorts zones.
- [x] **A4 Campaign sounds:** several cuts (start offset per cut). *In-app* mode puts the sound on the checklist; *burn* mode mixes the file in at a set volume (≥ the rule) with the original audio under it.
- [x] **A5 On-screen disclosure** (corner, inside the safe area) and disclosure in the caption.
- [x] **A6 Hook title overlay:** Anton, black on white, first N seconds, pop-in animation.
- [x] **A7 Post caption builder:** template tokens, per platform. Instagram is capped at 5 hashtags; YouTube titles at 100 characters. Captions can be edited per clip.
- [x] **A8 Compliance checker:** render freshness, length, 9:16 and 1080x1920 for templates, logo present, logo zone, logo UI cover and no-go overlap per platform, on-screen disclosure, sound rule, music/SFX forbidden, English-only (script and stopwords), banned topics, required tags, duplicate footage across campaigns, burned-in captions left unblurred, and manual checklist items.
- [x] **A9 Export package:** folder with `clip.mp4` (hard link, no extra disk), `caption_reels.txt`, `caption_shorts.txt`, `manifest.json`, `checklist.md` and the thumbnail. Also available as a .zip download. "Export approved" works for a whole campaign. Duplicate-content memory is kept in the `exports` table.
- [x] **A10 Reels/Shorts safe zones:** UI and no-go rectangles, an overlay in the editor and campaign page, and used by the checker.
- [x] **A11 Quick clip mode:** your own in/out range (or the whole file) at project start, "Add clip" on any processed project, and AI picks. AI picks: copy a prompt (rubric + timestamped transcript) to any chat, paste the JSON answer back; picks are validated, snapped to words and scored.
- [x] **From ClipForge review:**
  - Burned-in caption blur: stroke-transition text detector, blurs only while text is visible, shares the face-detection decode pass.
  - Audio: music ducked ~10 dB under speech (sidechain), voice high-pass + compression, master loudness −14 LUFS.
  - Built-in synthesized SFX (pop, whoosh, hit, riser, ding), auto-placed (≤ 4, ≥ 2.5 s apart) or chosen by hand. User overrides go in Library.
  - Music library by mood, picked per clip with a varied start point.
  - Bundled OFL fonts (Montserrat ExtraBold, Anton) via `fontsdir`.
  - Bulk links, queued one after another.
  - `manifest.json` in every export.
- [x] **Fix:** FFmpeg stderr now goes to a temp file. An unread stderr pipe could deadlock long or chatty renders (seen with looping GIF overlays).
- [x] UI: Campaigns, campaign brief editor, Library, New project (campaign, bulk, quick, finishing), project view (campaign, Add clip, AI picks), editor tabs (Overlays, Audio & effects, Check & export), Settings (Downloads, Finishing defaults), Storage.
- [x] Tests: 56 passing. New: `test_campaigns.py`, `test_phase1_media.py`, `test_api_campaigns.py` (including a simulated smart download).

### Not yet verified with real footage (the user will test)
- Smart downloads against real YouTube (section cutting, alignment confidence on real audio). The fallback to a full download is automatic.
- Burned-in caption detection on real subtitled podcasts (tuned on synthetic frames; may need threshold tuning).
- Logo templates from Clipster (only synthetic 1080x1920 GIF/APNG/PNG were tested). Safe-zone rectangles are approximations of current app layouts.
- How the ducked music and SFX levels sound. They were checked only by measurement, not by listening.
- Paste-to-fill accuracy on many real briefs. It is conservative; check the filled fields.

### Known limitations (Phase 1)
- Official in-app sounds can't be attached by code; the checklist covers it.
- The English check is heuristic (script + common-word ratio), not real language detection.
- Duplicate detection covers footage exported from this app only.
- The preview frame doesn't include face tracking or burned-text blur detection (it uses the last render's detected band).

## Base app status (2026-11-20)

### Done and tested
- [x] Backend core: config and modes, SQLite, structured JSON logging, storage checks and estimates
- [x] Ingest: upload (streamed), local file in place, URL via yt-dlp (validation, dedup, progress, friendly errors)
- [x] Transcription: parakeet-mlx in a child process, chunked, word timestamps, cache, JSON and SRT. Includes a fix for numbers merged into the preceding word ("was22").
- [x] Highlight engine: sentence normalisation, TextTiling, candidate windows, 7-criteria explainable scoring, NMS and duplicate removal, no forced padding
- [x] Extractive hooks, titles, descriptions, hashtags and categories. Optional Ollama tier with grounding validation.
- [x] Rendering: 9:16, 1:1, 16:9 at 720p or 1080p. Layouts: tracked crop, centre, blurred fill, split. Loudness normalisation, optional music.
- [x] Face tracking: YuNet, scene cuts, a switch must be confirmed before the camera moves, dead-zone, two-pass smoothing
- [x] Captions: 6 ASS presets, per-word highlight, karaoke, pop, fade, balanced line wrapping, overrides, exact frame preview
- [x] Job system: sequential worker, per-stage progress, cancellation, restart recovery, atomic outputs
- [x] API: projects, clips, jobs, settings, models, storage and cleanup, with localhost and Host-header protection
- [x] UI: Dashboard, New project, Projects, Processing and gallery, Clip editor, Settings, Storage. Dark and light themes.
- [x] Tests: 33 passing (unit, integration through the API with real FFmpeg, and one real-model transcription test)
- [x] Docs: README, ARCHITECTURE

### Verified manually during development
- Parakeet loads offline from the cache. Peak RSS on short audio was about 550 MB.
- Renders were checked visually: caption fonts (Arial Black, Impact), word highlight, outline, 720×1280 output.
- URL inspection worked against a public-domain Wikimedia Commons video.

### Not yet verified with real footage (the user will test)
- Face tracking and split-screen layout on real multi-person video. The tracking logic is covered by unit tests on synthetic tracks only.
- Score quality and ranking on long real-world videos (30–90 minutes). Thresholds may need tuning.
- Peak RAM and time per hour of video on the M2 8 GB.
- The optional Ollama tier (Ollama isn't installed on this machine).

## Known limitations
1. **English only** by default (Parakeet v2). Parakeet v3 (multilingual, 2.4 GB) can be selected once downloaded. Language isn't detected automatically.
2. **No speaker diarisation.** Framing follows the largest face, keeping continuity. There is no audio-based active-speaker detection yet.
3. **Rule-based scoring** works from phrase lexicons. It favours talking-head content (podcasts, interviews, commentary). Gameplay without speech gets few candidates, because the engine is speech-driven.
4. The **Visual quality** criterion is based only on source resolution.
5. **The caption preview is a single still frame** (exact styling). Animation shows only in the rendered video.
6. **Split screen** needs two clearly separated faces of similar size in at least 20% of sampled frames. Otherwise it falls back to tracking or blurred fill.
7. **Re-processing a project replaces its clips**, including edits. Rendered MP4s of the old clips are deleted.

## Next steps (suggested)
- Speechless highlight seeding from audio peaks and scene motion (useful for gameplay and GTA footage)
- Active-speaker detection (mouth-region motion × audio energy) for interviews
- Calibrate `CALIBRATION()` from real engagement data. Export and import per-clip performance.
- Optional Parakeet v3, with a model download flow in the UI that shows the size before downloading
