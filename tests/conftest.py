import os


for name in ("CONFIG_DIR", "MUSIC_ROOT", "MUSIC_DIR", "HOST", "PORT"):
    os.environ.pop(name, None)
os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")
