"""WhatsApp connectors. Each offers the same send() method.

FakeWhatsApp records what would be sent.
BaileysWhatsApp sends through the Node.js bridge in whatsapp-bridge/, which
links to a WhatsApp account the way WhatsApp Web does. Baileys is unofficial.
"""
import json
import urllib.error
import urllib.request


class WhatsAppUnavailable(Exception):
    pass


class FakeWhatsApp:
    def __init__(self):
        self.sent = []

    def send(self, phone, text, template=False):
        """template=True means the 24-hour window has closed, so a pre-approved
        template message has to be used instead of free text."""
        self.sent.append({"phone": phone, "text": text, "template": template})


class BaileysWhatsApp:
    def __init__(self, bridge_url, token, timeout=15):
        self._url = bridge_url.rstrip("/") + "/send"
        self._token = token
        self._timeout = timeout

    def send(self, phone, text, template=False):
        # A linked account has no templates and no 24-hour window, so the flag is not used.
        request = urllib.request.Request(
            self._url,
            data=json.dumps({"phone": phone, "text": text}).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Bridge-Token": self._token},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout):
                pass
        except urllib.error.HTTPError as exc:
            raise WhatsAppUnavailable("bridge refused the message: %s %s" % (exc.code, exc.read().decode("utf-8", "replace")))
        except OSError as exc:
            raise WhatsAppUnavailable("bridge is not reachable: %s" % exc)
