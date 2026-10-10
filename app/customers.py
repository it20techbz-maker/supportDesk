"""Look up who a phone number belongs to, so the reply uses the right voice."""
import json


class Customers:
    def __init__(self, path):
        with open(path, encoding="utf-8") as f:
            self._by_phone = {_norm(k): v for k, v in json.load(f).items()}

    def get(self, phone):
        """Return {"company", "type": "bank" | "stock", "contact"} or None."""
        return self._by_phone.get(_norm(phone))


def _norm(phone):
    return "".join(ch for ch in str(phone) if ch.isdigit())
