<div align="center">

# 🎓 KVS Proxy Management System

### *Fair, transparent, and automatic teacher-arrangement (proxy) management — built for schools, run from your pocket.*

[![Made with Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-3.x-000000?style=for-the-badge&logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![SQLite](https://img.shields.io/badge/SQLite-3-07405E?style=for-the-badge&logo=sqlite&logoColor=white)](https://www.sqlite.org/)
[![Runs on Termux](https://img.shields.io/badge/Runs%20on-Termux-1BB91F?style=for-the-badge&logo=android&logoColor=white)](https://termux.dev/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](#-license)
[![Status](https://img.shields.io/badge/Status-Active-brightgreen?style=for-the-badge)]()

<img src="https://img.shields.io/badge/KVS-No.%202%20Kharagpur-4d5bd9?style=flat-square" />
<img src="https://img.shields.io/badge/Built%20by-THEDARKGEEKDC-1f9e83?style=flat-square" />

</div>

---

## 🧭 What Is This?

Every school day, some teachers are absent — and someone has to decide, fairly and fast, who covers their classes. Doing that on paper (a manual proxy register) is slow, error-prone, and hard to keep fair over weeks and months.

**KVS Proxy Management System** replaces that paper register with a smart Flask + SQLite web app that:

- 🧮 **Suggests the fairest teacher** for every vacant period — automatically
- ⚖️ Keeps arrangement load **balanced across the whole staff**, week after week
- 🔁 Understands **class merges** and never double-counts a teacher's own freed period as "extra duty"
- 🧑‍🏫 Keeps **Secondary section (Class 6+)** completely separate — this system handles Primary only
- 🛟 Always has **two backup teachers** ready for the toughest periods (6, 7, 8)
- 📊 Produces clean **Daily / Weekly / Monthly / Custom** reports — Excel-exportable
- 📱 Runs entirely **offline, on-device**, in Termux or as a packaged Android app

No cloud, no internet dependency, no per-seat license. Just your phone (or laptop) and Python.

---

## ✨ Feature Highlights

<table>
<tr>
<td width="50%" valign="top">

### 🤖 Smart Arrangement Engine
- Auto & Manual proxy modes
- Fairness-based scoring (free periods, recent load, recency, class familiarity)
- Daily & weekly caps respected for every teacher
- Class-merge aware — merge-freed periods tracked separately via the `[M]` tag
- Secondary (Class 6/7/8) periods auto-skipped
- Two dedicated backup teachers (`BV1`/`BV2`) for Periods 6–8

</td>
<td width="50%" valign="top">

### 📊 Reporting & Transparency
- Live Today / Week / Month / Total load per teacher
- Weekly, Monthly & Custom-range reports
- Excel export for record-keeping
- One-click **month-wipe** (proxy + merge + absence) for a clean reset
- Teacher-wise fairness leaderboard
- No hidden numbers — every report is self-consistent

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 🗂️ Data Management
- Excel upload for timetable & student data
- Class-day merge manager
- Absence marking (single & bulk)
- Full master timetable viewer

</td>
<td width="50%" valign="top">

### 📱 Runs Anywhere
- Plain Flask app — runs in Termux, Linux, Windows, macOS
- Also packagable as a native Android APK (Kivy + Buildozer included)
- SQLite — zero external database setup
- All data stays on-device

</td>
</tr>
</table>

---

## 🧱 Tech Stack

| Layer | Technology |
|---|---|
| 🐍 Backend | Python 3 + Flask |
| 🗄️ Database | SQLite (via Flask-SQLAlchemy) |
| 📈 Excel I/O | openpyxl |
| 🎨 Frontend | Jinja2 templates + vanilla JS |
| 📱 Android packaging | Kivy + Buildozer *(optional, for APK builds)* |

---

## 📲 Setup on Termux (Android)

> Run your whole school's proxy system straight from your phone — no laptop needed.

### 1️⃣ Install Termux
Get **Termux** from [F-Droid](https://f-droid.org/packages/com.termux/) *(recommended — the Play Store build is outdated)*.

### 2️⃣ Set up the environment

```bash
# Update Termux packages
pkg update -y && pkg upgrade -y

# Install Python & Git
pkg install python git -y

# (Recommended) grant storage access for Excel uploads/exports
termux-setup-storage
```

### 3️⃣ Clone this repository

```bash
git clone https://github.com/<your-username>/kvs-proxy-system.git
cd kvs-proxy-system
```

### 4️⃣ Install dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 5️⃣ Run the server

```bash
python app.py
```

You should see Flask start on:

```
🌐  http://127.0.0.1:5050
```

### 6️⃣ Open it

Open **Chrome** (or any browser) on the same phone and go to:

```
http://127.0.0.1:5050
```

That's it — your school's proxy system is now running locally on your device. 🎉

<details>
<summary>💡 <b>Want it running in the background so you can close Termux?</b></summary>

<br>

Use Termux's `tmux` or `nohup` so the server survives after you exit:

```bash
pkg install tmux -y
tmux new -s proxy
python app.py
# Press Ctrl+B then D to detach — the server keeps running
```

Reconnect anytime with:
```bash
tmux attach -t proxy
```
</details>

<details>
<summary>🔐 <b>Default login / first-time setup</b></summary>

<br>

On first run, the app creates a fresh SQLite database automatically at:
```
database/db.sqlite3
```
Head to the login page and follow the on-screen setup to create your Timetable-In-Charge (TT IC) account, then upload your school's timetable via **Upload → Timetable Excel**.
</details>

---

## 🖥️ Running on a Laptop / PC

Works exactly the same way, minus the `pkg install` step:

```bash
git clone https://github.com/<your-username>/kvs-proxy-system.git
cd kvs-proxy-system
python -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Then open **http://127.0.0.1:5050** in your browser.

---

## 📦 Building the Android APK *(optional, advanced)*

This repo also ships with `main.py` + `buildozer.spec`, wrapping the same Flask app in a native Kivy splash-screen APK — for distributing a proper installable app instead of running through Termux.

```bash
pip install buildozer
buildozer -v android debug
```

The signed/unsigned APK will appear under `bin/`.

---

## 🗂️ Project Structure

```
kvs-proxy-system/
├── app.py                  # Flask entry point (Termux / desktop)
├── main.py                 # Kivy/Android entry point (APK wrapper)
├── config.py                # App configuration
├── buildozer.spec           # Android build spec
├── requirements.txt
│
├── database/
│   ├── models.py             # SQLAlchemy models
│   └── db.sqlite3            # SQLite database (auto-created)
│
├── services/
│   ├── proxy_engine.py       # Core suggestion & confirm logic
│   ├── scoring_engine.py     # Fairness scoring
│   ├── availability_service.py
│   ├── report_service.py
│   └── excel_service.py
│
├── routes/
│   ├── auth_routes.py
│   ├── dashboard_routes.py
│   ├── proxy_routes.py
│   ├── merge_routes.py
│   ├── upload_routes.py
│   └── tt_view.py
│
├── templates/                # Jinja2 HTML views
├── static/                   # CSS / JS assets
└── uploads/                  # Uploaded Excel files
```

---

## ⚖️ Fairness, Guaranteed

This system was built on one non-negotiable rule: **no favoritism, ever.**

| Rule | What it means |
|---|---|
| 🎯 Fair selection | The same scoring checks run for every teacher, every day — never a personal pick |
| 📏 Limits respected | No one is ever given more than their daily/weekly cap |
| 🔁 Merge-freed periods | A period freed by a class merge is *never* counted as "extra duty" |
| 🚫 Secondary skip | Class 6/7/8 absences are never routed here — handled by Secondary staff separately |
| 🛟 Backup transparency | BV1/BV2 duty is counted honestly, shown in every report |

📄 A full, teacher-friendly breakdown of every fairness rule ships alongside this project as `proxy-fairness-rules.html` — open it in any browser.

---

## 🛣️ Roadmap

- [ ] Push notifications for newly-assigned proxies
- [ ] Multi-school / multi-branch support
- [ ] PDF export for reports
- [ ] Dark/Light theme toggle

---

## 🤝 Contributing

Issues and pull requests are welcome! If you spot a bug in the scoring logic or have an idea to make arrangement fairer, open an issue.

---

## 📜 License

Licensed under the **MIT License** — free to use, modify, and deploy in your own school.

---

<div align="center">

### 👨‍💻 Built & Maintained by

**THEDARKGEEKDC · Mr DK CHAUDHARY**

(https://thedarkgeekdc2.github.io)

⭐ **If this saved your school some paper and a lot of headaches, consider starring the repo!** ⭐

</div>
