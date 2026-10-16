# Personal device setup


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
