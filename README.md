# Book Giveaway Entry Automation
This code scrapes StoryGraph on a weekly basis and enters the user into any relevant print giveaways matching books on their to-be read lists. It also sends the user recurring notifications (separate, free installation) regarding entries, along with bug alerts.

giveaway_bot.py is the main script, running 1x per week. It logs into the user's StoryGraph account and scans their to-read shelves. It then checks against every open giveaway across the site and enters only the ones that meet the following criteria: 
  1. The title and author match a book on the user's to-read shelf, in any edition
  2. The giveaway prize is print (hardcover or paperback), not digital or audio
  3. The user's country is eligible

Upon entering, it sends a notification to the user's phone through the nfty app, with the book, author, format, and site. It also alerts the user if something looks broken (i.e., a failed login page, 2FA), and sends a short weekly "I ran" summary. If the user does not receive the weekly summary, there is a problem with the code.

See README-SETUP.md for full setup instructions.

# License
BSD 3-Clause License, © 2026 [rileyzuckert].

You're free to download, use, fork, and modify this project. If you share it or a modified version, you must keep the copyright notice and the license text with it. You may not use the author's name to promote a version you made without permission. The full text is in the LICENSE file.

This project works together with other software that you install separately, such as Playwright and the Chromium browser. They keep their own licenses.

# Privacy Policy
This tool runs on your own computer. The author runs no server, has no accounts, and collects no usage data. The author never receives your StoryGraph login or your TBR shelf.

# Disclaimers
-- Unofficial. This project is not made by, affiliated with, endorsed by, or sponsored by The StoryGraph. "StoryGraph" and any related names or logos belong to their owners and are used here only to describe what this tool works with.
-- Automation and terms of service. The tool performs actions on your own StoryGraph account for you. Automated use may go against StoryGraph's terms of service, and StoryGraph may limit or suspend accounts that use it. You are responsible for reading their current terms and for how you use this tool.
-- No warranty. This project is provided "as is," with no promise that it works, keeps working, or enters any giveaway. StoryGraph can change its site at any time and break it. The author is not responsible for any problems that result from using it.
-- It doesn't get around security checks. If StoryGraph asks you to prove you're human, you do that yourself. The tool does not solve or bypass those checks.
No guarantee of winning. Entering a giveaway does not guarantee a prize. Each giveaway is run by its host under its own rules. You're responsible for the accuracy of anything in your StoryGraph account that a giveaway uses.
-- Takedown requests. If StoryGraph or its owners ask for this project to be changed or removed, the author may do so.
-- Be considerate. Please don't run it more often than about once a week, and don't change it to send requests faster. Heavy automated traffic is the quickest way to get this kind of tool blocked.
