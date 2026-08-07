Bought a Kindle Paperwhite recently, and immediately tried to use it for Anki reviews on the go. Turns out the browser on Kindle doesn't really do AnkiWeb - can't even log in.

So I built a small web app called **AnkiPaper** that talks to AnkiWeb and renders cards optimized for e-ink.

#### Limitations
- Black/white/gray only (most e-ink readers can't show color anyway).  
- No animations, no audio/video, only images + fonts; almost no JavaScript - literally just one fetch request to indicate sync progress.  
- No edit (it would be a pain on a Kindle touchscreen) - only review, mark, flag, rebuild filtered decks. That's it.   
- No review analytics, no card browser. Just review.  
- No tracking, no analytics scripts. Static page, that's all.  

#### Two ways to use it:
- **Hosted:** https://ankipaper.study - sign in with your existing AnkiWeb account, that's it.  
- **Self-hosted:** repo is open source, single `docker compose up --build` and you're good. For a public instance, nginx/letsencrypt/cloudflared/etc knowledge is strongly recommended.  

#### Repo
- **Link**: https://github.com/casualshammy/ankipaper  

#### A note on the hosted version (since nobody reads ToS and Privacy Policy):
- It's **free and provided as-is**. No premium tier, no "Pro" plan, no upsell - at least not today. I built this because I wanted it for myself.
- **No ads.** Not now, not later, not ever. A Kindle browser is a terrible place for ads, and I don't want them in a project like this anyway.
- **The trust thing.** Using the hosted version means your AnkiWeb credentials and the contents of your decks pass through my server. That's unavoidable - the whole point of the service is to talk to AnkiWeb on your behalf, so it has to. I treat that data with care (credentials stay encrypted at rest, no logging of card contents), but if your decks hold anything personal - medical notes, private journals, work-confidential stuff - just self-host.
- **The AnkiWeb throttling risk.** Hosted AnkiPaper talks to AnkiWeb's sync protocol, so in theory AnkiWeb could start throttling or banning requests from it. I've done some load testing and didn't hit any limits, but real usage usually differs from tests. If throttling does happen - and if it's bad enough - the hosted version would have to shut down. Sad but true.
- **Media size limits.** Hosted accounts are capped at 1 MB per file and 200 MB of media per account in total. Audio, video, and other heavy formats are skipped during sync anyway (only images + fonts go through), so this only really bites if you have a deck full of large card images.

[![AI Level 4](https://ai-level.dev/badge/standard/4.svg)](https://ai-level.dev/level-4)