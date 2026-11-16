# 🌱 Plant Growth Tracker

Take photos of a plant on your phone. The app tells you what it is, how healthy it is, its growth stage, what it needs (water, sunlight, soil, fertilizer) and what to do this week. It keeps a growth log with charts, sends **real phone notifications** when it is time to water or fertilize, and has a **Fun tab** that jokes about any picture.

Languages: English, हिन्दी, తెలుగు, தமிழ், ಕನ್ನಡ.

---

## Quick start (Windows)

1. Put all the files in one folder, for example `plant_app` on your Desktop.
2. Open that folder, click the address bar at the top, type `cmd` and press Enter.
3. Install the tools (one time, needs internet):
   ```
   python -m pip install -r requirements.txt
   ```
4. Run the app:
   ```
   python -m streamlit run app.py
   ```
   Your browser opens the app. (On a Mac use `python3` instead of `python`.)

Lines like "WARNING: script ... is not on PATH" during step 3 are harmless.

Without a key the app runs in **demo mode** (sample results). To get real AI analysis, follow the next part.

---

## Get a FREE AI key (Google Gemini)

1. Go to https://aistudio.google.com and sign in with a Google account.
2. Click **Get API key**, then **Create API key**. Copy the long key. The free tier needs no payment.
3. In the app's left sidebar, keep **AI service = Gemini (free)** and paste the key in the **Gemini API key** box.

Good to know:
- A real key is a long text of letters and numbers, not a 6-digit code.
- On the free tier, Google may use what you send to improve its products. Send plant photos only.
- The free tier has daily limits. If the app says "the free service is busy", wait a minute and try again.
- The app never saves your key. Paste it again each time you restart, and never show it on a projector.
- (Optional) Choose **Claude (paid)** in the sidebar to use an Anthropic key instead. That needs `python -m pip install anthropic` first.

---

## Faster and more accurate

In the sidebar, **Speed vs accuracy**:
- **Fast** (default): small photos, a quick model, short answers.
- **More accurate (slower)**: a bigger model and larger photos.

Ways to get a better answer:
- In **Upload from gallery**, add up to **3 photos**: the whole plant, a close-up of a leaf, and the soil.
- Type the plant's name in the optional box if you know it.
- Use good daylight and keep the leaves in focus. The app shows a "Photo tip" when it needs a better picture.
- The same photos with the same settings are answered instantly from memory.

Speed tricks inside the app: photos are shrunk before sending (a 3.6 MB phone photo becomes about 250 KB), the AI is told to answer briefly, the growth log only loads what you look at, and saved results are cached.

---

## Phone notifications (not Outlook, not a calendar)

Free, no account. Open the **⏰ Reminders** tab:

1. Install the free app **ntfy** on your phone (Play Store or App Store).
2. In ntfy tap **+** and subscribe to the secret topic name shown in the app.
3. Tick **Send me notifications**, then press **Send test notification**. Your phone should buzz.

After that, the app sends a notification when watering or fertilizing is due (once per day per task, from the hour you choose, until you press "I watered it today").

Important:
- Notifications are only sent **while the app is running on your computer** (keep the terminal window open). Laptop asleep = no notifications.
- Keep the topic name private. Anyone who knows it can read your plant notifications.
- Some school Wi-Fi blocks the notification service. A phone hotspot works as a backup.
- The app already does this by itself. Do not also run `notifier.py` at the same time.
- The calendar file is still there, hidden under "Calendar file (advanced)", if you ever want it.

---

## Fun tab 😄

Upload any picture: a meme, a cartoon character, your pet, food, a landmark. The AI says what it sees, names the meme or character if it recognizes it, and tells a joke.

- It does **not** identify real people from their faces, even famous ones. For a picture of a person it describes the situation (clothes, pose, setting) and jokes about that. If you type who it is in the optional box, it uses that name in a light-hearted joke.
- Jokes are kept kind and clean. Only upload pictures of people who are okay with it.

---

## Secret screens

Type one of these words as the plant name (capitals and spaces do not matter) and the screen goes black:

| Type this | You get | Your picture goes in a file named |
|---|---|---|
| `Stranger Things` | flickering red text | `egg_image` |
| `swag`, `rajini`, `rajni`, `thalaivar`, `thalaiva` | glowing gold "SWAG" | `swag_image` |

- **Your own picture:** save a picture next to `app.py` with the file name above plus `.png`, `.jpg`, `.jpeg` or `.webp` (for example `swag_image.jpg`). It replaces the text. A picture with a transparent or black background looks best. The app does not include any pictures of real people or logos, so you add your own. Delete the file to go back to the text.
- **Buttons at the bottom:** **Back to the app** returns to the plant tracker (and clears the plant name). **Full screen / Exit full screen** maximizes and minimizes the screen. (iPhone Safari does not allow full screen for web pages, so that button is hidden there.)
- **Change the words:** see `SECRET_SCREENS` in `app.py`. Each screen has a list of `triggers` (letters and numbers only, no spaces or capitals) and a picture name.

---

## Show it on other people's devices (hackathon)

Judges open the app on **their own phones**. Run it in **public mode** so every visitor gets private, temporary data (deleted after 12 hours), your AI key stays on your computer (never sent to their browsers), and each visitor gets 15 free AI tries (then they can paste their own free key). Phone notifications are switched off in this mode.

In the `cmd` window (replace the key with your free Gemini key):

```
set PLANT_PUBLIC=1
set GEMINI_API_KEY=your-long-key-here
python -m streamlit run app.py
```

Then pick ONE way to share:

**A. Same Wi-Fi or your phone hotspot (easiest).** Everyone connects to the same network, then types the `Network URL` the terminal shows (like `http://192.168.1.5:8501`). Make a QR code of it at any free QR site so they can scan it. On this plain link the camera is blocked, so tell people to use **Upload from gallery**. If it will not open, allow Python through the Windows firewall when asked. Some school Wi-Fi blocks device-to-device traffic; a hotspot works.

**B. A public https link (camera works, any network).** Install the free `cloudflared` from Cloudflare, and in a second `cmd` window run:

```
cloudflared tunnel --url http://localhost:8501
```

It prints a link like `https://something.trycloudflare.com`. Share that link or its QR code. It works only while your laptop and both windows stay on.

**C. Streamlit Community Cloud (always on, free).** Put the files on GitHub, deploy at share.streamlit.io, and in the app's **Secrets** add:

```
PLANT_PUBLIC = "1"
GEMINI_API_KEY = "your-long-key"
```

Change the per-visitor limit with `set PLANT_MAX_CALLS=10`. The free Gemini plan has daily limits shared by everyone, so test before the demo and keep demo mode as a backup.

---

## Files

| File | What it does |
|---|---|
| `app.py` | The screens: scan, growth log, reminders, fun, secret screens |
| `ai.py` | Talks to the AI (Gemini or Claude), the prompts, speed and error handling |
| `notifier.py` | Sends phone notifications through ntfy |
| `reminders.py` | Works out when a task is due, safe file saving |
| `translations.py` | Every word on screen in all 5 languages, plus demo results |
| `requirements.txt` | The tools to install |
| `.streamlit/config.toml` | Upload size limit, no usage tracking |

Saved data (photos, growth log, reminders, settings) goes in a `data` folder next to `app.py`. Delete that folder to erase everything.

## Use it on your phone

Laptop and phone on the same Wi-Fi. After starting the app, the terminal shows a line like `Network URL: http://192.168.1.5:8501`. Type that into your phone's browser. If **Take a photo** does not open the camera (some phones block it on non-secure links), use **Upload from gallery**. Anyone on the same Wi-Fi can open that address, so a phone hotspot is safer than school Wi-Fi.

## If something goes wrong

- **"No module named ..."**: repeat step 3 and check all the files are in the same folder as `app.py`.
- **"model not found" or other AI errors**: send me the exact message.
- **Words look wrong in Hindi/Telugu/Tamil/Kannada**: they were machine-written. Ask a native speaker to check them, and edit `translations.py`.
- To stop the app, press Ctrl+C in the terminal.
