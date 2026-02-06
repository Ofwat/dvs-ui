import os


def load_env(path: str | os.PathLike[str] = ".env"):
    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as fp:
        for raw_line in fp:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'\"")
            if not key:
                continue
            os.environ.setdefault(key, value)
