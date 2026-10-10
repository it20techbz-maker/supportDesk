"""Email connector.

FakeEmail records what would be sent. The connector for the company mail
server replaces it in phase 2 and must offer the same send() method.
"""


class FakeEmail:
    def __init__(self):
        self.sent = []

    def send(self, recipient, subject, body):
        self.sent.append({"recipient": recipient, "subject": subject, "body": body})
