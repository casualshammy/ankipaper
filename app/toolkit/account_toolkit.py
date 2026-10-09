from pathlib import Path

DATA_ROOT = Path("/data")
ACCOUNTS_DIR = DATA_ROOT / "accounts"

def sanitize_account_id_or_throw(username: str) -> str:
  """Returns a safe account directory name from an AnkiWeb username.

  The username is lowercased so that ``Alice`` and ``alice`` map to the
  same account — AnkiWeb treats usernames case-insensitively, and a
  case-different login would otherwise create a duplicate account and
  silently overwrite the hostKey.

  Raises:
      ValueError: if the username is empty, reserved, or contains
          invalid characters (``/``, ``\\``, NUL).
  """

  username = username.strip()
  if not username:
    raise ValueError("Empty username")
  
  cleaned = username.lower()
  if cleaned in (".", ".."):
    raise ValueError("Reserved account id")
  if any(c in cleaned for c in ("/", "\\", "\0")):
    raise ValueError("Invalid characters in username")
  if len(cleaned) > 64:
    raise ValueError("Username too long")
  
  return cleaned
