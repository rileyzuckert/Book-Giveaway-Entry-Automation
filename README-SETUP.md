# Get the Files
1. Open this project's GitHub page
2. Click the green **Code** button, then **Download ZIP**
3. Unzip it and rename the folder to `giveaway-bot`
4. Move the folder to your home folder (Mac: the folder with your name, Windows: `C:\Users\YourName`, Linux: `/home/yourname`)

Want your own copy on GitHub? Click **Fork** first, then download the ZIP from your copy. Never upload your `.env` file.

# Install Python
**Mac**
1. Open Terminal (press `Cmd + Space`, type `Terminal`, press Return)
2. Type `python3 --version` and press Return
3. If you see a version number, you're done. If your Mac asks to install developer tools, click Install.

**Windows**
1. Download Python from python.org/downloads
2. Run the installer
3. Tick **Add python.exe to PATH** before clicking Install

**Linux**
1. Type `python3 --version`. Python is usually already installed.

# Open a Terminal and Go to the Folder
1. Mac: press `Cmd + Space`, type `Terminal`. Windows: click Start, type `PowerShell`. Linux: press `Ctrl + Alt + T`.
2. Go to the folder.

   Mac / Linux:
   ```
   cd ~/giveaway-bot
   ```
   Windows:
   ```
   cd $HOME\giveaway-bot
   ```
3. List the files to check you're in the right place. You should see `giveaway_bot.py`.

   Mac / Linux: `ls`    Windows: `dir`

Do this step again every time you open a new terminal window.

# Install What the Bot Needs
This takes a few minutes.

Mac / Linux:
```
python3 -m pip install playwright
python3 -m playwright install chromium
```
Windows:
```
py -m pip install playwright
py -m playwright install chromium
```
Linux only, if needed: `sudo python3 -m playwright install-deps chromium`

# Ntfy Setup
1. Install the ntfy app (App Store or Google Play)
2. Pick a long, random topic name and subscribe to it in the app. The topic name works like a password. Anyone who guesses it can read your notifications.
3. Save that same topic name in your settings file (next section)

Need a random name? Mac / Linux: `echo giveaways-$(openssl rand -hex 10)`

# Settings File Setup
Your settings go in a file called `.env` that stays on your computer only. Don't put them on GitHub.

1. Make a copy of the template.

   Mac / Linux: `cp env.example .env`    Windows: `copy env.example .env`
2. Open the new file.

   Mac: `open -e .env`    Windows: `notepad .env`    Linux: `nano .env`
3. Fill in these four lines (no quotes, no spaces around the `=`):
   ```
   SG_EMAIL=your StoryGraph email
   SG_PASSWORD=your StoryGraph password
   NTFY_TOPIC=your ntfy topic name
   MY_COUNTRY=your country, as StoryGraph writes it (for example United States)
   ```
4. Save and close the file
5. Test the notification. Your phone should buzz within a few seconds.

   Mac / Linux: `python3 giveaway_bot.py --test-notify`    Windows: `py giveaway_bot.py --test-notify`

The `.env` file starts with a dot, so your computer hides it. That's normal.

# Practice Run (nothing is entered)
1. Run it.

   Mac / Linux: `python3 giveaway_bot.py --headed --once`    Windows: `py giveaway_bot.py --headed --once`
2. A browser window opens. If you see an "I'm not a robot" checkbox, click it yourself. The bot waits up to 10 minutes.
3. Wait for it to finish. The browser closes by itself.
4. Check the results.

   Mac / Linux: `grep "to-read scraped\|WOULD ENTER" giveaway_bot.log`
   Windows: `Select-String "to-read scraped|WOULD ENTER" giveaway_bot.log`
5. The "to-read scraped" number should be close to the number of books on your shelf. A `WOULD ENTER` line is a giveaway it would enter. It's normal to see none some weeks.

# First Real Run
1. Run it.

   Mac / Linux: `python3 giveaway_bot.py --live --headed --once`    Windows: `py giveaway_bot.py --live --headed --once`
2. Watch the browser
3. You should get an "Entered: (book title)" notification on your phone
4. Check StoryGraph to see that you're entered

# Weekly Schedule: Mac
1. Fill in your details automatically (run these two lines):
   ```
   sed -i '' "s|REPLACE_WITH_HOME|$HOME|g" ~/giveaway-bot/com.giveawaybot.weekly.plist
   sed -i '' "s|REPLACE_WITH_PYTHON|$(which python3)|g" ~/giveaway-bot/com.giveawaybot.weekly.plist
   ```
2. Turn it on:
   ```
   mkdir -p ~/Library/LaunchAgents
   cp ~/giveaway-bot/com.giveawaybot.weekly.plist ~/Library/LaunchAgents/
   launchctl load ~/Library/LaunchAgents/com.giveawaybot.weekly.plist
   ```
3. Check it's installed (you should see one line): `launchctl list | grep giveawaybot`
4. Test it now (this is a real run): `launchctl start com.giveawaybot.weekly`
5. It now runs every Monday at 9:00 AM. To change the day or time, edit `Weekday` (1 = Monday ... 7 = Sunday), `Hour`, and `Minute` in the `.plist` file before step 2.

# Weekly Schedule: Windows
These steps haven't been tested by the author.
1. Click Start, type `Task Scheduler`, and open it
2. Click **Create Basic Task** and name it `StoryGraph Giveaway Bot`
3. Choose **Weekly**, then **Monday** and a time (for example 9:00 AM)
4. Choose **Start a program**
5. Fill in:
   - Program/script: `py`
   - Add arguments: `"C:\Users\YourName\giveaway-bot\giveaway_bot.py" --live --once`
   - Start in: `C:\Users\YourName\giveaway-bot`
6. Click Finish
7. Right-click the task and choose **Run** to test it

# Weekly Schedule: Linux
These steps haven't been tested by the author.
1. Type `crontab -e`
2. Add this line at the bottom (it means every Monday at 9:00 AM), then save:
   ```
   0 9 * * 1 cd ~/giveaway-bot && /usr/bin/python3 giveaway_bot.py --live --once
   ```

# How to Tell It's Working
1. Every week you get a "Weekly giveaway run finished" notification. If these stop, something is wrong.
2. You get an "Entered: (book title)" notification for each giveaway entered
3. A "Giveaway bot needs attention" notification means something looks broken
4. A "Check manually" notification means it clicked through but couldn't confirm the entry. Open the link and check.
5. Everything it does is written in `giveaway_bot.log`

# Updating
1. Download the new `giveaway_bot.py` and `selectors.json`
2. Put them in your `giveaway-bot` folder and replace the old ones
3. Don't touch `.env`, `browser_profile`, or `state.json`. They hold your settings, your saved login, and your list of entered giveaways.

# Turn It Off
- Mac: `launchctl unload ~/Library/LaunchAgents/com.giveawaybot.weekly.plist`, then delete that file
- Windows: open Task Scheduler, right-click the task, choose Delete
- Linux: run `crontab -e` and delete the line

# Troubleshooting
- **"command not found: playwright"**: use the commands in this guide that start with `python3 -m` (or `py -m` on Windows)
- **"scraped: 0 books"**: add `SG_USERNAME=yourusername` to `.env` and run the practice run again
- **Login fails or the checkbox keeps coming back**: repeat the practice run with the browser showing and click through the checkbox
- **No phone notification**: make sure `NTFY_TOPIC` in `.env` matches the topic in the ntfy app exactly
- **Mac asks about adding `.txt` when saving a file**: choose the option that keeps the original name
- **A giveaway wasn't entered**: search `giveaway_bot.log` for the book's title. The reason is written next to it (for example "not on TBR", "format=digital", "not open to your country", or "no enter button").

# What's in the Folder
| File | What it is |
|---|---|
| `giveaway_bot.py` | The bot |
| `selectors.json` | Where to find things on StoryGraph's pages (updated if StoryGraph changes its layout) |
| `env.example` | Template for your settings |
| `.env` | Your private settings. You make this. Don't share or upload it |
| `local-setup/com.giveawaybot.weekly.plist` | Mac weekly schedule file |
| `giveaway_bot.log` | Diary of what the bot did (created when it runs) |
| `state.json` | Giveaways already entered (created when it runs) |
| `browser_profile/` | Your saved StoryGraph login. Treat it like a password (created when it runs) |
| `debug/` | Screenshots saved when something goes wrong (created when needed) |
