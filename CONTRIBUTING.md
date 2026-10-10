# Contributing to pic-search

Thanks for your interest. pic-search is a personal project maintained in spare time, so reviews are best-effort and may take a while — but bug reports, ideas and pull requests are all welcome.

## Reporting a bug or suggesting an idea

Open a [GitHub issue](https://github.com/bloveobs/pic-search/issues). For a bug, please include:

- what you did, what you expected, and what happened instead
- the full error message or traceback
- your setup: OS / WSL distro, Podman version, Python version
- roughly how large your photo library is, if the problem is about indexing or search speed

**Security problems are the exception:** do not open a public issue. Follow [SECURITY.md](SECURITY.md).

## Never post private data

This project handles personal photos and login credentials. Before you open an issue or a pull request, check that it does not contain:

- `config.py`, `config.sh` or `client_secret.json`
- anything from `data/` (`index.json`, `faces.json`) or `references_faces/`
- real photos, real names, email addresses, or your public hostname
- screenshots or logs that show any of the above

These files are already in `.gitignore`; please keep it that way.

## Setting up for development

Follow the [Setup section of the README](README.md#-setup). In short: build the container image, copy the two example config files, index a folder of photos, start Ollama, install `requirements.txt` into a virtual environment and run `python3 app.py`.

A small test library (a few hundred photos and two or three reference-face folders) is enough for development and makes indexing fast.

## Making a change

1. For anything larger than a small fix, open an issue first so we can agree on the approach before you spend time on it.
2. Fork the repository and create a branch from `main`.
3. Keep each pull request to one change. Small pull requests get reviewed sooner.
4. Match the style of the code around your change.
5. Test it by hand (see below) and say in the pull request what you tested.
6. Update the README if you change setup steps, configuration, routes or behaviour.

### Where things live

| If you are changing… | Look at… | Then… |
|---|---|---|
| Scene (CLIP) indexing or search | `app/main.py` | rebuild the image: `podman build -t pic-search .` |
| Face indexing or search | `app/face_search.py` | rebuild the image |
| Container packages | `app/requirements.txt`, `Containerfile` | rebuild the image |
| Natural-language query parsing | `query_parser.py` | restart `app.py` |
| Web UI, login, routes | `app.py` | restart `app.py` |
| Web UI packages | `requirements.txt` | `pip install -r requirements.txt` |

### Testing

There is no automated test suite yet, so changes are tested by hand. Run whichever of these your change could affect:

```bash
./index_photos.sh              # scene indexing
./index_faces.sh               # face indexing
./search.sh "a dog"            # scene search
./search_face.sh "Jill"        # face search
python3 query_parser.py "Jack and Jill on a hill"   # query parsing (needs Ollama running)
python3 app.py                 # web UI: log in, search, browse, upload
```

Adding automated tests is itself a welcome contribution.

### Changes to login, sessions, file serving or uploads

`app.py` can be exposed to the public internet, so changes to the OAuth flow, the email allowlist, session handling, `/image`, `/browse` or `/upload` get a closer review. Please explain in the pull request why the change is safe — for example, how a path from the browser is still confined to the Pictures directory.

## Good places to start

The README lists known limitations that would make useful contributions:

- a vector database instead of the linear scan over `index.json`
- a long-running search service instead of one container per query
- duplicate-photo detection by content hash
- automatic reindexing after an upload
- an automated test suite

## Licence

pic-search is released under the [MIT License](LICENSE). By submitting a contribution you agree that it is released under the same licence.
