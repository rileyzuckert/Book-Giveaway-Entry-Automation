# Book Giveaway Entry Automation
This code scrapes StoryGraph on a weekly basis and enters the user into any relevant print giveaways matching books on their to-be read lists. It also sends the user recurring notifications (separate, free installation) regarding entries, along with bug alerts.

giveaway_bot.py is the main script, running 1x per week. It logs into the user's StoryGraph account and scans their to-read shelves. It then checks against every open giveaway across both sites and enters only the ones that meet the following criteria: 
  1. The title and author match a book on the user's to-read shelf, in any edition
  2. The giveaway prize is print (hardcover or paperback), not digital or audio
  3. The user's country is eligible

Upon entering, it sends a notification to the user's phone through the nfty app, with the book, author, format, and site. It also alerts the user if something looks broken (i.e., a failed login page, 2FA), and sends a short weekly "I ran" summary. If the user does not receive the weekly summary, there is a problem with the code.

selectors.json is a settings file that tells giveaway_bot.py where to find things on each site, such as the giveaway list and the 'Enter' button. If Storygraph redesigns a page, edit this file and the script will pick up the change on its next run.

giveaways.yml is a GitHub Actions schedule. It lets GitHub run the script every Monday (or whenever the user starts the code), avoiding Python and terminal, and keeping the user's logins and ntfy topic private in their GitHub repo.

# Ntfy Setup
1. Install ntfy app
2. Pick a long, random topic name and subscribe to it in the app. The topic name works like a password. Anyone who guesses it can read your notifications.
3. Set an environment variable to that same name in Secrets

# Secrets and Variables Setup
1. Go to the Settings tab for the GitHub repo
2. In the left sidebar, click 'Secrets and variables,' then click 'Actions'
3. Add each secret (SG_EMAIL, SG_PASSWORD, NTFY_TOPIC)
4. Click the 'Variables' tab next to 'Secrets'
5. Add MY_COUNTRY and the corresponding user country (as Storygraph writes it)
